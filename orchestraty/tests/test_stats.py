"""orch stats: usage week by week from the record, the acted-on proxy, and the anonymous summary."""
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pytest

from helpers import run_hook, spool_event
from orchestraty import cli, config, stats
from orchestraty.coordinator import main as cmain
from orchestraty.coordinator.record import Record
from orchestraty.paths import State
from orchestraty.rules.overlap import Finding

NS = 1_000_000_000
MINUTE = 60 * NS
BROKEN = "def charge(amount):\n    return amount\n"
USES = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


# --------------------------------------------------------------------------------------- #
# A two-session scenario, end to end
# --------------------------------------------------------------------------------------- #
def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def edit(state, c, sid, top, rel, text):
    (top / rel).write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(top / rel))
    settle(c)


def test_stats_of_a_two_session_scenario(state, repo, git, tmp_path, hook_script, capsys):
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    spool_event(state.root, "A", "SessionStart", wa)
    spool_event(state.root, "B", "SessionStart", wb)
    c = cmain.Coordinator(state)
    try:
        c.step()
        assert c.livemap.indexes.wait(120)
        edit(state, c, "A", wa, "a.py", BROKEN)
        edit(state, c, "B", wb, "c.py", USES)
        payload = json.dumps({"session_id": "B", "hook_event_name": "PreToolUse", "cwd": str(wb), "tool_name": "Edit",
                              "tool_input": {"file_path": str(wb / "c.py")}, "tool_use_id": "toolu_1"}).encode()
        assert b"changed the contract of charge" in run_hook(hook_script, ["claude", "PreToolUse"], payload).stdout
        c.step()                                                     # B took its note
        edit(state, c, "B", wb, "c.py", USES.replace("charge(2, 'USD')", "charge(2)"))     # ... and acted on it
    finally:
        c.close()
    data = stats.collect(state)
    assert data["schema"] == 1 and data["days"] == 7 and data["acted_minutes"] == 30
    [week] = data["weeks"]
    assert week["sessions"] == {"live": 2, "agents": 2, "plain_git": 0, "cloud": 0} and week["pairs"] == 1
    assert week["findings"]["by_rule"]["O3"] >= 1 and week["findings"]["by_strength"]["strong"] >= 1
    notes = week["notes"]
    assert notes["by_kind"]["edit"] >= 1 and notes["delivered"] >= 1
    assert notes["awareness_ms"]["measured"] >= 1 and notes["awareness_ms"]["median"] >= 0
    assert notes["awareness_ms"]["p90"] >= notes["awareness_ms"]["median"]
    assert notes["acted_on"] >= 1 and notes["findings_acted_on"] >= 1
    assert notes["tokens_estimate"] > 50
    assert cli.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "Week 1" in out and "proxy" in out and "2 live (2 agent, 0 plain git); 1 pair working at the same time" in out
    assert cli.main(["stats", "--json", "--days", "14"]) == 0
    assert [w["week"] for w in json.loads(capsys.readouterr().out)["weeks"]] == [1, 2]


# --------------------------------------------------------------------------------------- #
# The acted-on proxy
# --------------------------------------------------------------------------------------- #
T = 1_792_000_000 * NS


def note(sid="B", delivered=T):
    return {"session_id": sid, "delivered": delivered, "finding_key": "k"}


FINDING = {"key": "k", "rule": "O3", "file": "c.py", "symbol": "a.py::charge", "resolved": None,
           "detail": {"uses": [{"symbol": "c.py::use", "lines": [5]}]}}
LIVE = {"started": T - 60 * MINUTE, "state": "live", "last_seen": T + 90 * MINUTE}


def test_an_edit_of_a_cited_file_within_the_window_counts():
    assert stats.acted_on(note(), FINDING, LIVE, {"B": [(T + 10 * MINUTE, "c.py")]}, 30 * MINUTE)
    assert stats.acted_on(note(), FINDING, LIVE, {"B": [(T + 10 * MINUTE, "a.py")]}, 30 * MINUTE)   # the changed function
    assert not stats.acted_on(note(), FINDING, LIVE, {"B": [(T + 10 * MINUTE, "d.py")]}, 30 * MINUTE)
    assert not stats.acted_on(note(), FINDING, LIVE, {"A": [(T + 10 * MINUTE, "c.py")]}, 30 * MINUTE)  # another session
    assert not stats.acted_on(note(delivered=None), FINDING, LIVE, {"B": [(T, "c.py")]}, 30 * MINUTE)


def test_the_same_edit_after_the_window_does_not():
    late = {"B": [(T + 31 * MINUTE, "c.py"), (T - MINUTE, "c.py")]}            # after the window, and before delivery
    assert not stats.acted_on(note(), FINDING, LIVE, late, 30 * MINUTE)
    assert stats.acted_on(note(), FINDING, LIVE, late, 45 * MINUTE)


def test_a_finding_that_stops_holding_while_the_session_is_live_counts():
    resolved = dict(FINDING, resolved=T + 5 * MINUTE)
    assert stats.acted_on(note(), resolved, LIVE, {}, 30 * MINUTE)
    ended = {"started": T - 60 * MINUTE, "state": "ended", "last_seen": T + 2 * MINUTE}
    assert not stats.acted_on(note(), resolved, ended, {}, 30 * MINUTE)          # it resolved because B had gone
    assert not stats.acted_on(note(), dict(FINDING, resolved=T + 40 * MINUTE), LIVE, {}, 30 * MINUTE)


def test_the_files_a_note_cites():
    o6 = {"file": "a.py", "symbol": "a.py::charge", "detail": {"left": [{"symbol": "b.py::pay", "file": "b.py"}]}}
    assert stats.cited_files(o6) == {"a.py", "b.py"}
    assert stats.cited_files({"file": "", "symbol": "", "detail": {"worktree": "/w/repo"}}) == set()   # W1 cites none


def put_note(rec, key, sid, created, delivered, text="Orchestraty: a note"):
    rec.add_note(key, sid, "next", f"notes/{sid}/next.json", text, created, created - MINUTE)
    rec.db.execute("UPDATE notes SET state = 'delivered', written = ?, delivered = ? WHERE finding_key = ? AND "
                   "session_id = ?", (created + NS, delivered, key, sid))


def test_acted_on_from_the_record_with_its_window_from_the_config(state, tmp_path):
    now = time.time()
    t = int(now * NS) - 120 * MINUTE
    trees = {sid: tmp_path / ("wt-" + sid.lower()) for sid in "BCDE"}
    rec = Record(state.record)
    with rec.transaction():
        rid = rec.repo_id(str(tmp_path / "repo" / ".git"))
        for sid, top in trees.items():
            ended = sid == "E"
            rec.put_session({"id": sid, "agent": "claude", "repo_id": rid, "cwd": str(top), "worktree": str(top),
                             "branch": "b", "base_commit": "c0", "base_guessed": 0, "started": t - 60 * MINUTE,
                             "last_seen": t + MINUTE if ended else t + 100 * MINUTE,
                             "state": "ended" if ended else "live", "turn": "", "note": ""})
            f = Finding(f"O3|a.py::charge|A|{sid}|c.py", "O3", "strong", ("A", sid), changer="A", receiver=sid,
                        symbol="a.py::charge", file="c.py", detail={"uses": [{"symbol": "c.py::use", "lines": [5]}]})
            rec.put_finding(rid, f, t - MINUTE)
            put_note(rec, f.key, sid, t - MINUTE, t)
        for sid, after in (("B", 10), ("C", 31)):                     # B edits c.py 10 minutes after, C 31
            rec.add_event({"ts": t + after * MINUTE, "agent": "claude", "event": "PostToolUse", "session_id": sid,
                           "cwd": str(trees[sid]), "file_path": str(trees[sid] / "c.py")})
        rec.resolve_finding("O3|a.py::charge|A|D|c.py", t + 5 * MINUTE)  # D's finding stops holding while D is live
        rec.resolve_finding("O3|a.py::charge|A|E|c.py", t + 5 * MINUTE)  # E's, after E ended
    rec.close()
    week = stats.collect(state, now=now)["weeks"][0]["notes"]
    assert (week["delivered"], week["acted_on"], week["findings_acted_on"]) == (4, 2, 2)
    cfg = config.load(state)
    cfg["acted_minutes"] = 45
    config.save(state, cfg)
    assert stats.collect(state, now=now)["weeks"][0]["notes"]["acted_on"] == 3


# --------------------------------------------------------------------------------------- #
# The anonymous summary
# --------------------------------------------------------------------------------------- #
SA, SB = "zelda-session-0001", "link-session-0002"
PLANTED = ("zelda", "link-session", "session-0001", "hyrule", "triforce", "master-sword", "epona", "ocarina",
           "ganon", "lift_curse", "kakariko", "sheikah", "navi", "courage", "/home", "/w/")


def plant(state, now):
    """A record with an identifier planted in every text it keeps."""
    t = int(now * NS) - 60 * MINUTE
    rec = Record(state.record)
    with rec.transaction():
        rid = rec.repo_id("/home/zelda/hyrule-repo/.git")
        for sid, top, branch in ((SA, "/home/zelda/wt-triforce", "feat/master-sword"),
                                 (SB, "/home/zelda/wt-ocarina", "feat/epona")):
            rec.put_session({"id": sid, "agent": "claude", "repo_id": rid, "cwd": top + "/kakariko", "worktree": top,
                             "branch": branch, "base_commit": "deadbeef", "base_guessed": 0, "started": t,
                             "last_seen": t + 30 * MINUTE, "state": "live", "turn": "working", "note": "ganon"})
        f = Finding(f"O3|ganon.py::lift_curse|{SA}|{SB}|kakariko.py", "O3", "strong", (SA, SB), changer=SA,
                    receiver=SB, symbol="ganon.py::lift_curse", file="kakariko.py",
                    detail={"uses": [{"symbol": "kakariko.py::sheikah", "lines": [3]}], "reasons": ["parameter navi removed"]})
        rec.put_finding(rid, f, t + MINUTE)
        put_note(rec, f.key, SB, t + MINUTE, t + 2 * MINUTE, text="Orchestraty: ganon.py lift_curse, navi, sheikah")
        rec.add_event({"ts": t + 3 * MINUTE, "agent": "claude", "event": "PostToolUse", "session_id": SB,
                       "cwd": "/home/zelda/wt-ocarina", "file_path": "/home/zelda/wt-ocarina/kakariko.py"})
        rec.add_label(f.key, "real", "only zelda knows why", t + 4 * MINUTE)
        rec.add_missed(t + 5 * MINUTE, SB, "the note about the triforce of courage")
    rec.close()


def test_the_summary_holds_no_identifiers(state, capsys):
    now = time.time()
    plant(state, now)
    full = json.dumps(stats.collect(state, now=now))
    assert '"acted_on": 1' in full and '"real": 1' in full and '"missed": 1' in full    # the record was read
    capsys.readouterr()
    assert cli.main(["stats", "--summary"]) == 0
    printed = capsys.readouterr().out
    summary = json.loads(printed)
    assert summary["weeks"][0]["notes"]["acted_on"] == 1 and summary["weeks"][0]["labels"]["by_verdict"]["real"] == 1
    assert summary["weeks"][0]["missed"] == 1 and summary["weeks"][0]["findings"]["by_rule"]["O3"] == 1
    lower = printed.lower()
    assert not [word for word in PLANTED if word in lower]
    assert not re.search(r"\d{4}-\d{2}-\d{2}", printed)                    # no dates either
    strings = re.findall(r'"([^"]*)"', printed)
    allowed = (set(stats.RULES) | set(stats.STRENGTHS) | set(stats.KINDS) | set(stats.STATES) | {"real", "harmless", "false"}
               | {"schema", "kind", "orchestraty usage summary", "version", "days", "acted_minutes", "weeks", "week",
                  "sessions", "live", "agents", "plain_git", "cloud", "pairs", "findings", "total", "by_rule",
                  "by_strength",
                  "latent", "notes", "made", "by_kind", "by_state", "delivered", "awareness_ms", "measured", "median",
                  "p90", "acted_on", "findings_acted_on", "tokens_estimate", "labels", "by_verdict", "missed",
                  "per_session_day", "session_days", "mean", "max", "held_back"} | set(stats.HELD_BACK)
               | {summary["version"]})
    assert set(strings) <= allowed


def test_the_summary_is_shown_before_it_is_saved(state, capsys, tmp_path):
    plant(state, time.time())
    out = tmp_path / "summary.json"
    capsys.readouterr()
    assert cli.main(["stats", "--summary", "--out", str(out)]) == 1         # no terminal to ask in, no --yes
    printed = capsys.readouterr().out
    assert printed.startswith("{") and "Not saved." in printed and not out.exists()
    assert cli.main(["stats", "--summary", "--out", str(out), "--yes"]) == 0
    printed = capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(printed[:printed.rindex("}") + 1])
    assert cli.main(["stats", "--out", str(out)]) == 2                      # --out goes with --summary


def test_stats_open_the_record_read_only(state, capsys, monkeypatch):
    plant(state, time.time())
    opened = []
    real = Record.__init__

    def spy(self, path, readonly=False):
        opened.append(readonly)
        real(self, path, readonly)
    monkeypatch.setattr(Record, "__init__", spy)
    before = hashlib.sha256(Path(state.record).read_bytes()).hexdigest()
    for argv in (["stats"], ["stats", "--json", "--days", "21"], ["stats", "--summary"]):
        assert cli.main(argv) == 0
    assert len(opened) == 3 and all(opened)
    assert hashlib.sha256(Path(state.record).read_bytes()).hexdigest() == before


def test_stats_without_a_record(state, capsys):
    assert cli.main(["stats", "--days", "21"]) == 0
    assert capsys.readouterr().out.count("no delivered note yet") == 3
    assert not Path(state.record).exists()


# --------------------------------------------------------------------------------------- #
# Noise: notes per session per day, and what was held back
# --------------------------------------------------------------------------------------- #
def test_notes_per_session_per_day_and_what_was_held_back(state, tmp_path, capsys):
    now = time.time()
    t = int(now * NS)
    day = 24 * 60 * MINUTE
    rec = Record(state.record)
    with rec.transaction():
        rid = rec.repo_id(str(tmp_path / "repo" / ".git"))
        for sid, agent, started in (("B", "claude", t - 2 * day - 12 * 60 * MINUTE), ("C", "codex", t - 120 * MINUTE),
                                    ("G", "git", t - 3 * day)):
            rec.put_session({"id": sid, "agent": agent, "repo_id": rid, "cwd": "/w", "worktree": "/w", "branch": "b",
                             "base_commit": "c0", "base_guessed": 0, "started": started, "last_seen": t - MINUTE,
                             "state": "live", "turn": "", "note": ""})

        def finding(key, rule, strength, receiver, **detail):
            rec.put_finding(rid, Finding(key, rule, strength, ("A", receiver), changer="A", receiver=receiver,
                                         symbol="a.py::f", file="c.py", detail=detail), t - 3 * day)
        for key, sid, delivered in (("S1", "B", t - 30 * MINUTE), ("S2", "B", t - 20 * MINUTE),
                                    ("S3", "B", t - 2 * day - MINUTE), ("S4", "C", t - 10 * MINUTE)):
            finding(key, "O3", "strong", sid)
            put_note(rec, key, sid, delivered - MINUTE, delivered)
        finding("O1|c.py|A|B", "O1", "weak", "B")                                   # status only
        finding("L1", "O3", "strong", "B", latent=True)                              # nobody changes the using file
        rec.add_note("S1", "B", "edit", "notes/B/edit.json", "Orchestraty: the same", t - 40 * MINUTE)
        [heard] = rec.notes(finding_key="S1", session_id="B", states=("pending",))
        rec.set_note_state(heard["id"], "delivered", t - 30 * MINUTE)               # B took the next note instead
        rec.add_note("S2", "B", "stop", "notes/B/stop.json", "Orchestraty: gone", t - 40 * MINUTE)
        [gone] = rec.notes(finding_key="S2", session_id="B", states=("pending",))
        rec.set_note_state(gone["id"], "withdrawn", t - 35 * MINUTE)
    rec.close()
    notes = stats.collect(state, now=now)["weeks"][0]["notes"]
    # B: live on 3 days (2 notes today, 1 two days ago), C on 1 (1 note); plain git does not count
    assert notes["per_session_day"] == {"session_days": 4, "mean": 1.0, "max": 2}
    assert notes["held_back"] == {"weak_findings": 1, "latent_findings": 1, "already_heard": 1, "withdrawn": 1}
    assert cli.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "  per day     1.0 notes per session per day, at most 2 (over 4 session-days)" in out
    assert "  held back   1 weak finding (status only), 1 latent finding, 1 note already heard, 1 withdrawn" in out
    week = stats.summary(stats.collect(state, now=now))["weeks"][0]["notes"]
    assert week["per_session_day"]["max"] == 2 and week["held_back"]["already_heard"] == 1


# --------------------------------------------------------------------------------------- #
# orch feedback: the summary and the user's comment, shown first, sent nowhere
# --------------------------------------------------------------------------------------- #
class Terminal:
    def isatty(self):
        return True


@pytest.fixture
def no_network(monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("orch feedback opened a connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_feedback_is_the_summary_and_the_comment_typed_at_the_prompt(state, capsys, monkeypatch, no_network):
    now = time.time()
    plant(state, now)
    typed = iter(["Two notes came just in time.", "  One was about a file I had left.  ", ""])
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    assert cli.main(["feedback"]) == 0
    captured = capsys.readouterr()
    assert "An empty line ends it." in captured.err
    printed = captured.out
    shown = json.loads(printed[:printed.rindex("}") + 1])
    assert shown["kind"] == "orchestraty feedback" and shown["schema"] == 1
    assert shown["comment"] == "Two notes came just in time.\n  One was about a file I had left."
    assert shown["summary"] == stats.summary(stats.collect(state, days=14, now=now)) and shown["summary"]["days"] == 14
    assert shown["summary"]["weeks"][0]["notes"]["acted_on"] == 1
    lower = printed.lower()
    assert not [word for word in PLANTED if word in lower]                # counts only, besides the comment
    assert "Not saved and not sent" in printed and "sends nothing anywhere" in printed


def test_feedback_is_saved_only_where_the_user_says_once_they_say_yes(state, capsys, tmp_path, no_network):
    plant(state, time.time())
    out = tmp_path / "feedback.json"
    assert cli.main(["feedback", "--comment", "fine", "--out", str(out)]) == 1      # no terminal to ask in
    captured = capsys.readouterr()
    assert "--comment" not in captured.err and "Not saved." in captured.out and not out.exists()
    assert cli.main(["feedback", "--comment", "fine", "--out", str(out), "--yes"]) == 0
    printed = capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(printed[:printed.rindex("}") + 1])
    assert json.loads(out.read_text(encoding="utf-8"))["comment"] == "fine"
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["feedback.json"]


def test_feedback_without_a_terminal_or_a_record(state, capsys, no_network):
    assert cli.main(["feedback", "--days", "7"]) == 0
    captured = capsys.readouterr()
    assert "No terminal to type a comment in" in captured.err
    shown = json.loads(captured.out[:captured.out.rindex("}") + 1])
    assert shown["comment"] == "" and len(shown["summary"]["weeks"]) == 1
    assert not Path(state.record).exists()
