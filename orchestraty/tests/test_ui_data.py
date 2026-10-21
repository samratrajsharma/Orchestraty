"""The dashboard's payloads: read from a record filled through its own write methods, and from
a two-session scenario driven as test_delivery drives one."""
import json
import os
from pathlib import Path

import pytest

from helpers import run_hook, spool_event
from orchestraty import config
from orchestraty.coordinator import main as cmain
from orchestraty.coordinator.record import Record
from orchestraty.delivery import slots
from orchestraty.paths import State
from orchestraty.rules.overlap import Finding
from orchestraty.ui import data

S = 1_000_000_000
BROKEN = "def charge(amount):\n    return amount\n"
USES = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


# --------------------------------------------------------------------------------------- #
# A record written through Record's own methods
# --------------------------------------------------------------------------------------- #
def session_row(sid, repo_id, worktree, state="live", turn="working", started=100, last_seen=200):
    return {"id": sid, "agent": "claude", "repo_id": repo_id, "cwd": worktree, "worktree": worktree,
            "branch": "feat-" + sid.lower(), "base_commit": "c0ffee" * 6 + "abcd", "base_guessed": 0,
            "started": started * S, "last_seen": last_seen * S, "state": state, "turn": turn, "note": ""}


CHARGE = {"symbol": "a.py::charge", "kind": "modified", "change": "signature", "contract": "breaking",
          "reasons": ["parameter currency removed"], "file": "a.py", "lines": [1, 2], "moved_from": "", "breaks": None}
O3 = Finding("O3|a.py::charge|A|B|c.py", "O3", "strong", ("A", "B"), changer="A", receiver="B",
             symbol="a.py::charge", file="c.py",
             detail={"symbol": "a.py::charge", "kind": "modified", "reasons": ["parameter currency removed"],
                     "moved_to": "", "uses": [{"symbol": "c.py::use", "lines": [5]}], "latent": False})
O6 = Finding("O6|a.py::charge|A", "O6", "strong", ("A",), changer="A", symbol="a.py::charge", file="a.py",
             detail={"symbol": "a.py::charge", "kind": "modified", "reasons": ["parameter currency removed"],
                     "left": [{"symbol": "b.py::pay", "file": "b.py", "lines": [5]}]})
O1 = Finding("O1|b.py|A|B", "O1", "weak", ("A", "B"), file="b.py", detail={"file": "b.py"})


@pytest.fixture
def filled(state, tmp_path):
    """Two live sessions and an ended one, A's breaking change, three findings (one resolved)
    and notes in every state, written as the coordinator writes them."""
    common = str(tmp_path / "repo" / ".git")
    wa, wb, wc = (str(tmp_path / name) for name in ("wt-a", "wt-b", "wt-c"))
    config.watch(state, common)
    rec = Record(state.record)
    with rec.transaction():
        rid = rec.repo_id(common)
        rec.put_session(session_row("A", rid, wa, started=100))
        rec.put_session(session_row("B", rid, wb, turn="idle", started=101))
        rec.put_session(dict(session_row("C", rid, wc, state="ended", turn="", started=99), note="ended: other"))
        rec.put_changes("A", {"a.py": "M", "b.py": "M"}, [], [CHARGE])
        rec.put_changes("B", {"c.py": "A", "b.py": "M"}, ["shared.py"], [])
        for f in (O3, O6, O1):
            rec.put_finding(rid, f, 300 * S)
        rec.resolve_finding(O1.key, 400 * S)
        for i in range(3):
            rec.add_event({"ts": (200 + i) * S, "agent": "claude", "event": "PostToolUse", "session_id": "A",
                           "tool_name": "Edit", "file_path": os.path.join(wa, "a.py")}, {"len": 9, "took": "next"})
        edit = "notes/B/edit/c.json"
        rec.add_note(O3.key, "B", "edit", edit, "the edit note", 300 * S, cause_ts=200 * S)
        rec.add_note(O3.key, "B", "next", "notes/B/next.json", "the next note", 300 * S, cause_ts=200 * S)
        rec.add_note(O3.key, "A", "next", "notes/A/next.json", "who uses it", 300 * S, cause_ts=200 * S)
        rec.add_note(O6.key, "A", "stop", "notes/A/stop.json", "callers left behind", 300 * S, cause_ts=210 * S)
        ids = {(n["session_id"], n["kind"]): n["id"] for n in rec.notes()}
        rec.mark_written([ids[("B", "edit")], ids[("B", "next")]], 200 * S + 15_500_000)       # 15.5 ms
        rec.mark_written([ids[("A", "stop")]], 210 * S + 40_000_000)                             # 40 ms
        rec.mark_delivered(edit, 305 * S)
        rec.set_note_state(ids[("B", "next")], "delivered")       # heard through the edit note
        rec.mark_delivered("notes/A/stop.json", 306 * S)
    rec.close()
    push = Path(state.root) / "push"
    push.mkdir()
    (push / (slots.file_key(wa) + ".txt")).write_text("orchestraty: 1 caller(s) of charge still use its old form "
                                                      "(pay). Run: orch status\n", encoding="utf-8")
    (push / "unknown~0000000000000000.txt").write_text("orchestraty: someone else\n", encoding="utf-8")
    (push / (slots.file_key(wa) + ".txt.tmp")).write_text("half written", encoding="utf-8")
    return {"A": wa, "B": wb, "C": wc, "ids": ids, "common": common}


def test_the_status_payload_is_orch_status_with_the_ui_part(state, filled):
    out = data.status_payload(state, demo=True)
    assert out["schema"] == 1 and out["ui"]["demo"] is True and out["ui"]["version"]
    [repo] = out["repositories"]
    assert repo["name"] == "repo" and [s["id"] for s in repo["sessions"]] == ["A", "B"]
    a = repo["sessions"][0]
    assert a["changes"]["files"] == {"a.py": "M", "b.py": "M"} and a["changes"]["symbols"][0]["contract"] == "breaking"
    assert [o["key"] for o in repo["overlaps"]] == [O3.key, O6.key]                     # strongest first; O1 resolved


def test_ended_sessions_only_when_asked(state, filled):
    assert "C" not in [s["id"] for s in data.status_payload(state)["repositories"][0]["sessions"]]
    every = data.status_payload(state, include_ended=True)["repositories"][0]["sessions"]
    assert [s["id"] for s in every] == ["C", "A", "B"]
    assert every[0]["state"] == "ended" and every[0]["note"] == "ended: other"


def test_a_session_with_its_changes_notes_events_and_push_line(state, filled):
    out = data.session_payload(state, "A", now=210)
    s = out["session"]
    assert s["worktree"] == filled["A"] and s["branch"] == "feat-a" and s["turn"] == "working"
    assert s["seconds_since_last_event"] == 10 and s["last_event"] == "1970-01-01T00:03:20.000Z"
    assert s["repository"]["name"] == "repo"
    assert out["changes"]["files"] == {"a.py": "M", "b.py": "M"}
    [charge] = out["changes"]["symbols"]
    assert (charge["symbol"], charge["kind"], charge["change"], charge["contract"]) == \
        ("a.py::charge", "modified", "signature", "breaking")
    assert charge["reasons"] == ["parameter currency removed"]
    assert [(n["kind"], n["state"]) for n in out["notes_received"]] == [("stop", "delivered"), ("next", "pending")]
    assert [(n["session_id"], n["kind"]) for n in out["notes_caused"]] == [("B", "next"), ("B", "edit")]
    assert [e["ts"] for e in out["events"]] == ["1970-01-01T00:03:22.000Z", "1970-01-01T00:03:21.000Z",
                                                "1970-01-01T00:03:20.000Z"]
    assert out["events"][0]["took"] == "next" and out["events"][0]["file_path"].endswith("a.py")
    assert out["push"]["line"].startswith("orchestraty: 1 caller(s) of charge") and out["push"]["session"] == "A"
    b = data.session_payload(state, "B", now=210)
    assert b["changes"]["unattributed"] == ["shared.py"] and b["push"] is None and b["notes_caused"] == []
    assert data.session_payload(state, "nope") is None


def test_the_events_are_cut_to_the_latest(state, filled, monkeypatch):
    monkeypatch.setattr(data, "EVENT_LIMIT", 2)
    assert [e["ts"][-7:] for e in data.session_payload(state, "A")["events"]] == ["22.000Z", "21.000Z"]


def test_findings_with_their_detail_and_notes(state, filled):
    out = data.findings_payload(state, "open")
    assert [f["key"] for f in out["findings"]] == [O3.key, O6.key]
    o3 = out["findings"][0]
    assert o3["detail"]["uses"] == [{"symbol": "c.py::use", "lines": [5]}] and o3["latent"] is False
    assert o3["changer"] == "A" and o3["receiver"] == "B" and o3["first_seen"] == "1970-01-01T00:05:00.000Z"
    assert {(n["session_id"], n["kind"]) for n in o3["notes"]} == {("B", "edit"), ("B", "next"), ("A", "next")}
    assert out["findings"][1]["detail"]["left"][0]["symbol"] == "b.py::pay"
    assert [f["key"] for f in data.findings_payload(state, "resolved")["findings"]] == [O1.key]
    assert data.findings_payload(state, "resolved")["findings"][0]["resolved"] == "1970-01-01T00:06:40.000Z"
    assert len(data.findings_payload(state, "all")["findings"]) == 3
    one = data.finding_payload(state, O3.key)
    assert one["finding"]["key"] == O3.key and set(one["sessions"]) == {"A", "B"}
    assert data.finding_payload(state, "O3|nope") is None


def test_notes_with_time_to_awareness_and_the_median(state, filled):
    out = data.notes_payload(state)
    assert out["total"] == 4 and [n["kind"] for n in out["notes"]] == ["stop", "next", "next", "edit"]
    by = {(n["session_id"], n["kind"]): n for n in out["notes"]}
    assert by[("B", "edit")]["time_to_awareness_ms"] == 15.5 and by[("A", "stop")]["time_to_awareness_ms"] == 40.0
    assert by[("A", "next")]["time_to_awareness_ms"] is None and by[("A", "next")]["written"] is None
    assert by[("B", "next")]["state"] == "delivered" and by[("B", "next")]["heard_with"] == "edit"
    assert by[("B", "edit")]["delivered"] == "1970-01-01T00:05:05.000Z" and by[("B", "edit")]["rule"] == "O3"
    assert out["summary"] == {"delivered": 3, "measured": 3, "median_ms": 15.5}
    b = data.notes_payload(state, session="B")
    assert {n["session_id"] for n in b["notes"]} == {"B"} and b["summary"]["median_ms"] == 15.5
    assert [n["kind"] for n in data.notes_payload(state, kind="stop")["notes"]] == ["stop"]
    assert [n["session_id"] for n in data.notes_payload(state, note_state="pending")["notes"]] == ["A"]
    assert data.notes_payload(state, session="nobody")["summary"]["median_ms"] is None


def test_push_lines_with_their_worktree_and_session(state, filled):
    lines = {p["name"]: p for p in data.push_payload(state)["push"]}
    assert len(lines) == 2                                              # the .tmp file is not a push line
    mine = lines[slots.file_key(filled["A"]) + ".txt"]
    assert mine["worktree"] == filled["A"] and mine["session"] == "A" and mine["line"].endswith("Run: orch status")
    other = lines["unknown~0000000000000000.txt"]
    assert other["worktree"] is None and other["session"] is None and other["line"] == "orchestraty: someone else"


def test_health_and_config(state, filled):
    health = data.health_payload(state)
    assert health["coordinator"] == {"running": False} and health["record"]["exists"] is True
    assert health["state"] == state.root
    cfg = data.config_payload(state)
    assert cfg["steer"] is False and cfg["wake"] is False and cfg["state"] == state.root
    [repo] = cfg["repos"]
    assert repo["name"] == "repo" and repo["common_dir"] == os.path.normpath(filled["common"])
    assert repo["watch"].startswith("orch watch ") and repo["unwatch"].startswith("orch unwatch ")
    assert cfg["commands"] == {"init": "orch init", "restore": "orch restore"}


def test_a_folder_with_spaces_is_quoted_in_the_commands(state, tmp_path):
    folder = tmp_path / "my repo's" / "x"
    (folder / ".git").mkdir(parents=True)
    config.watch(state, str(folder / ".git"))
    [repo] = data.config_payload(state)["repos"]
    assert repo["folder"] == str(folder)
    quoted = repo["watch"][len("orch watch "):]
    assert quoted.startswith("'") and quoted.endswith("'") and "my repo" in quoted


def test_nothing_is_created_when_there_is_no_record(state):
    assert data.status_payload(state)["repositories"] == []
    assert data.session_payload(state, "A") is None and data.finding_payload(state, "k") is None
    assert data.findings_payload(state)["findings"] == [] and data.notes_payload(state)["notes"] == []
    assert data.push_payload(state)["push"] == [] and data.health_payload(state)["record"]["exists"] is False
    assert not os.path.exists(state.record)


def test_times_and_awareness():
    assert data.iso(None) is None and data.iso(1_500_000_000) == "1970-01-01T00:00:01.500Z"
    assert data.awareness_ms({"written": 3_250_000, "cause_ts": 1_000_000}) == 2.2
    assert data.awareness_ms({"written": None, "cause_ts": 1}) is None


# --------------------------------------------------------------------------------------- #
# The same payloads after a scenario run by the coordinator
# --------------------------------------------------------------------------------------- #
@pytest.fixture
def scenario(state, repo, git, tmp_path):
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    for sid, top in (("A", wa), ("B", wb)):
        spool_event(state.root, sid, "SessionStart", top)
        spool_event(state.root, sid, "UserPromptSubmit", top)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    yield c, wa, wb
    c.close()


def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
    c.step()


def edit(state, c, sid, top, rel, text):
    path = top / rel
    path.write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(path))
    settle(c)


def test_a_scenario_reads_back_as_the_coordinator_wrote_it(state, scenario, hook_script):
    c, wa, wb = scenario
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    [repo] = data.status_payload(state)["repositories"]
    assert {s["id"] for s in repo["sessions"]} == {"A", "B"}
    keys = [o["key"] for o in repo["overlaps"]]
    assert "O3|a.py::charge|A|B|c.py" in keys and "O6|a.py::charge|A" in keys
    assert all(not o["detail"].get("latent") for o in repo["overlaps"])
    latent = [f for f in data.findings_payload(state)["findings"] if f["latent"]]
    assert [f["key"] for f in latent] == ["O3|a.py::charge|A|B|b.py"]                   # B's own b.py uses it
    a = data.session_payload(state, "A")
    assert a["changes"]["files"] == {"a.py": "M"} and a["changes"]["symbols"][0]["contract"] == "breaking"
    assert {n["kind"] for n in a["notes_received"]} == {"next", "stop"}
    assert {(n["session_id"], n["kind"]) for n in a["notes_caused"]} >= {("B", "edit"), ("B", "next")}
    assert a["push"]["line"].startswith("orchestraty: 1 caller(s) of charge still use its old form")
    written = [n for n in data.notes_payload(state, session="B", kind="edit")["notes"]
               if n["finding_key"] == "O3|a.py::charge|A|B|c.py"]
    assert written[0]["state"] == "written" and 0 <= written[0]["time_to_awareness_ms"] < 30_000
    post = json.dumps({"session_id": "B", "hook_event_name": "PostToolUse", "tool_name": "Read"}).encode()
    out = json.loads(run_hook(hook_script, ["claude", "PostToolUse"], post).stdout)
    assert "changed the contract of charge" in out["hookSpecificOutput"]["additionalContext"]
    c.step()
    b = {(n["finding_key"], n["kind"]): n for n in data.session_payload(state, "B")["notes_received"]}
    assert b[("O3|a.py::charge|A|B|c.py", "next")]["delivered"] is not None
    assert b[("O3|a.py::charge|A|B|c.py", "edit")]["heard_with"] == "next"
    summary = data.notes_payload(state)["summary"]
    assert summary["delivered"] >= 2 and summary["median_ms"] is not None
    [push] = data.push_payload(state)["push"]
    assert push["session"] == "A" and os.path.normcase(push["worktree"]) == os.path.normcase(str(wa))
