"""Claims: orch claim, orch claims and orch release through the coordinator, which stores them in
the record; their targets resolved and kept in canonical form; their end when released, when
they expire and when their session ends; and a claim that survives a coordinator restart."""
import io
import json
import os
import time

import pytest

from helpers import spool_event
from orchestraty import claims, cli, config, events, status
from orchestraty.coordinator import main as cmain
from orchestraty.paths import State

NS = 1_000_000_000
BILLING = "class Invoice:\n    def total(self):\n        return 1\n\n\ndef refund(amount):\n    return amount\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


class Clock:
    """The coordinator's clock, which a test can move forward."""

    def __init__(self):
        self.offset = 0

    def __call__(self):
        return time.time_ns() + self.offset


@pytest.fixture
def two(state, repo, git, tmp_path):
    """Sessions A and B (Claude Code) in worktrees of their own, and a coordinator whose clock
    the test can move."""
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    for top in (wa, wb):
        (top / "pkg").mkdir()
        (top / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (top / "pkg" / "billing.py").write_text(BILLING, encoding="utf-8")
    spool_event(state.root, "A", "SessionStart", wa)
    spool_event(state.root, "B", "SessionStart", wb)
    clock = Clock()
    c = cmain.Coordinator(state, now_ns=clock)
    c.step()
    yield c, clock, wa, wb
    c.close()


def held(c, at=None):
    return c.record.claims(active_at=at if at is not None else time.time_ns())


def test_targets_resolve_to_their_canonical_form(repo, tmp_path):
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "billing.py").write_text(BILLING, encoding="utf-8")
    top, sub = str(repo), str(repo / "pkg")
    assert claims.resolve(top, top, "a.py") == ("file", "a.py", "")
    assert claims.resolve(top, sub, "billing.py") == ("file", "pkg/billing.py", "")       # from the folder it is run in
    assert claims.resolve(top, top, "pkg/") == ("area", "pkg", "")
    assert claims.resolve(top, sub, str(repo / "pkg")) == ("area", "pkg", "")
    assert claims.resolve(top, top, "a.py::charge") == ("symbol", "a.py::charge", "a.py")
    assert claims.resolve(top, sub, "billing.py::Invoice.total") == (
        "symbol", "pkg/billing.py::Invoice.total", "pkg/billing.py")
    assert claims.resolve(top, top, "pkg.billing.Invoice.total") == (
        "symbol", "pkg/billing.py::Invoice.total", "pkg/billing.py")
    assert claims.resolve(top, top, "pkg.billing.refund") == ("symbol", "pkg/billing.py::refund", "pkg/billing.py")
    assert claims.resolve(top, top, "a.charge") == ("symbol", "a.py::charge", "a.py")
    assert claims.resolve(top, sub, "a.py") == ("file", "a.py", "")                     # not in pkg: from the top
    with pytest.raises(claims.Refused, match="did you mean Invoice.total"):
        claims.resolve(top, sub, "billing.py::total")
    for bad in ("", "missing.py", "a.py::nothing", "pkg.billing.nothing", ".", top, ".git", str(tmp_path),
                "b.py::charge"):                                    # b.py imports charge; it does not define it
        with pytest.raises(claims.Refused):
            claims.resolve(top, top, bad)


def test_times_and_spool_lines_are_checked():
    assert [claims.parse_minutes(t) for t in ("90", "90m", "2h", "1h30m", "24h")] == [90, 90, 120, 90, 1440]
    for bad in ("", "0", "0m", "25h", "1d", "h", "-5", "1.5h"):
        with pytest.raises(ValueError):
            claims.parse_minutes(bad)
    assert claims.default_minutes({"claim_minutes": 0}) == 1 and claims.default_minutes({"claim_minutes": 9999}) == 1440
    good = {"event": "claim", "repo": os.path.abspath(os.path.join("r", ".git")), "session_id": "A",
            "kind": "symbol", "target": "a.py::charge", "file": "a.py", "hard": False, "minutes": 30}
    assert claims.parse_line(good)["target"] == "a.py::charge"
    for change in ({"kind": "everything"}, {"minutes": 0}, {"minutes": 1441}, {"minutes": True}, {"hard": 1},
                   {"file": ""}, {"kind": "file"}, {"repo": "relative/.git"}, {"session_id": ""}, {"target": ""},
                   {"target": "x" * 301}):
        assert claims.parse_line(dict(good, **change)) is None, change
    release = {"event": "release", "repo": good["repo"], "session_id": ""}
    assert claims.parse_line(dict(release, id=3)) == {"event": "release", "repo": good["repo"], "id": 3, "session_id": ""}
    assert claims.parse_line(dict(release, target="a.py"))["target"] == "a.py"
    for change in ({"id": 0}, {"id": True}, {"id": "3"}, {}):
        assert claims.parse_line(dict(release, **change)) is None, change


def test_a_claim_right_after_a_session_starts_waits_for_its_first_event(state, two, git, repo, tmp_path, capsys,
                                                                          monkeypatch):
    c, clock, wa, wb = two
    wc = tmp_path / "wt-c"
    git(repo, "worktree", "add", "-q", "-b", "feat-c", str(wc))
    (wc / "pkg").mkdir()
    (wc / "pkg" / "billing.py").write_text(BILLING, encoding="utf-8")
    spool_event(state.root, "C", "SessionStart", wc)              # in the spool, not yet in the record
    waits = []
    monkeypatch.setattr(cli.time, "sleep", lambda s: (waits.append(s), c.step()))   # the coordinator catches up
    monkeypatch.chdir(wc / "pkg")
    assert cli.main(["claim", "billing.py"]) == 0
    assert "Claimed pkg/billing.py for session C (claude)" in capsys.readouterr().out
    assert waits == [0.25]


def test_claim_release_and_expiry_through_the_coordinator(state, two, capsys, monkeypatch):
    c, clock, wa, wb = two
    monkeypatch.chdir(wa / "pkg")
    assert cli.main(["claim", "billing.py"]) == 0
    out = capsys.readouterr().out
    assert "Claimed pkg/billing.py for session A (claude) until" in out and "told before it edits there" in out
    assert cli.main(["claim", "billing.py::Invoice.total", "--hard", "--for", "1h"]) == 0
    assert "Claimed Invoice.total in pkg/billing.py for session A (claude)" in capsys.readouterr().out
    assert cli.main(["claim", "..", "--for", "1m"]) == 2                              # the whole repository
    assert cli.main(["claim", "."]) == 0                                              # the folder pkg
    monkeypatch.chdir(wb)
    assert cli.main(["claim", "a.charge", "--for", "1m"]) == 0
    c.step()

    got = {(x["session_id"], x["kind"], x["target"], x["file"], x["hard"]) for x in held(c)}
    assert got == {("A", "file", "pkg/billing.py", "", 0), ("A", "symbol", "pkg/billing.py::Invoice.total",
                                                             "pkg/billing.py", 1),
                   ("A", "area", "pkg", "", 0), ("B", "symbol", "a.py::charge", "a.py", 0)}
    ids = {x["target"]: x["id"] for x in held(c)}
    hours = {x["target"]: (x["expires"] - x["ts"]) / NS / 3600 for x in held(c)}
    assert hours["pkg/billing.py"] == 2 and hours["pkg/billing.py::Invoice.total"] == 1     # claim_minutes: 120

    # orch status, orch status --json and orch claims show them, with their sessions
    data = status.collect(state)
    a, b = data["repositories"][0]["sessions"]
    assert [x["target"] for x in a["claims"]] == ["pkg/billing.py", "pkg/billing.py::Invoice.total", "pkg"]
    assert b["claims"][0] == {"id": ids["a.py::charge"], "kind": "symbol", "target": "a.py::charge", "file": "a.py",
                              "hard": False, "since": b["claims"][0]["since"], "until": b["claims"][0]["until"]}
    text = status.render(data)
    assert f"claimed #{ids['pkg/billing.py::Invoice.total']}: Invoice.total in pkg/billing.py until " in text
    assert text.count(", hard") == 1 and "the folder pkg/ until" in text
    assert cli.main(["claims"]) == 0
    listed = capsys.readouterr().out
    assert f"#{ids['a.py::charge']}" in listed and "charge in a.py" in listed and "B" in listed
    assert c.write_status(force=True)
    with open(state.status, "r", encoding="utf-8") as fh:
        assert json.load(fh)["repositories"][0]["sessions"][0]["claims"] == a["claims"]   # what the mod reads

    # released by what it claims (from another folder of the repository), then by number
    monkeypatch.chdir(wa)
    assert cli.main(["release", "pkg/billing.py"]) == 0
    assert f"Released #{ids['pkg/billing.py']}: pkg/billing.py, claimed by A (claude)." in capsys.readouterr().out
    assert cli.main(["release", f"#{ids['pkg']}"]) == 0
    assert cli.main(["release", "a.py"]) == 2                                          # no claim on it
    assert cli.main(["release", "999"]) == 2
    c.step()
    assert {x["target"] for x in held(c)} == {"pkg/billing.py::Invoice.total", "a.py::charge"}
    assert c.record.claim(ids["pkg/billing.py"])["why"] == "released"

    # the one-minute claim expires on the coordinator's clock
    clock.offset = 90 * NS
    c.step()
    gone = c.record.claim(ids["a.py::charge"])
    assert (gone["why"], gone["ended"]) == ("expired", gone["expires"])
    assert {x["target"] for x in held(c, clock())} == {"pkg/billing.py::Invoice.total"}

    # each change is in orch events
    out = io.StringIO()
    events.run(state, out, as_json=True)
    got = [json.loads(line) for line in out.getvalue().splitlines() if '"claim' in line]
    assert [(e["event"], e["claim"]) for e in got if e["event"] == "claimed"] == sorted(
        (("claimed", i) for i in ids.values()), key=lambda x: x[1])
    ended = {e["claim"]: e["ended"] for e in got if e["event"] == "claim_ended"}
    assert ended == {ids["pkg/billing.py"]: "released", ids["pkg"]: "released", ids["a.py::charge"]: "expired"}
    first = next(e for e in got if e["claim"] == ids["pkg/billing.py::Invoice.total"])
    assert (first["session"], first["claim_kind"], first["target"], first["file"], first["hard"]) == (
        "A", "symbol", "pkg/billing.py::Invoice.total", "pkg/billing.py", True)
    lines = io.StringIO()
    events.run(state, lines)
    assert "claim ended" in lines.getvalue() and "charge in a.py (expired)" in lines.getvalue()


def test_a_claim_ends_with_its_session_and_is_kept_only_for_live_sessions(state, two, capsys, monkeypatch):
    c, clock, wa, wb = two
    monkeypatch.chdir(wb)
    assert cli.main(["claim", "b.py"]) == 0
    c.step()
    [mine] = held(c)
    spool_event(state.root, "B", "SessionEnd", wb, reason="other")
    c.step()
    assert c.record.claim(mine["id"])["why"] == "session ended" and held(c) == []
    monkeypatch.chdir(wb.parent / "repo")                                              # clean: no session works here
    assert cli.main(["claim", "b.py"]) == 2
    assert "no live session works in" in capsys.readouterr().err
    # A line for a session that is not live, or for a repository not watched, is dropped.
    claims.append_claim(state, "B", str(wb.parent / "repo" / ".git"), "file", "b.py", "", False, 30)
    claims.append_claim(state, "A", str(wa / "elsewhere" / ".git"), "file", "a.py", "", False, 30)
    c.step()
    assert held(c) == []


def test_a_claim_survives_a_coordinator_restart(state, two, monkeypatch, capsys):
    c, clock, wa, wb = two
    monkeypatch.chdir(wa)
    assert cli.main(["claim", "a.py", "--hard"]) == 0
    c.step()
    [before] = held(c)
    c.close()
    again = cmain.Coordinator(state)
    try:
        again.step()
        assert again.record.claims(active_at=time.time_ns()) == [before]
        assert status.collect(state)["repositories"][0]["sessions"][0]["claims"][0]["id"] == before["id"]
        assert cli.main(["release", "a.py"]) == 0
        again.step()
        assert again.record.claim(before["id"])["why"] == "released"
    finally:
        again.close()


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_several_sessions_in_one_worktree_ask_which(state, two, monkeypatch, capsys):
    c, clock, wa, wb = two
    spool_event(state.root, "A2", "SessionStart", wa, agent="codex")
    c.step()
    monkeypatch.chdir(wa)
    assert cli.main(["claim", "a.py"]) == 2                                            # no terminal to ask in
    err = capsys.readouterr().err
    assert "2 live sessions work in" in err and "--session" in err and "A2" in err
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda question: "2")
    assert cli.main(["claim", "a.py"]) == 0
    out = capsys.readouterr().out
    assert "1. A (claude)" in out and "2. A2 (codex)" in out and "for session A2 (codex)" in out
    monkeypatch.setattr("builtins.input", lambda question: "")
    assert cli.main(["claim", "b.py"]) == 2                                            # none picked
    assert cli.main(["claim", "b.py", "--session", "B"]) == 0                          # from another worktree
    assert cli.main(["claim", "b.py", "--session", "Z"]) == 2
    c.step()
    assert {(x["session_id"], x["target"]) for x in held(c)} == {("A2", "a.py"), ("B", "b.py")}
