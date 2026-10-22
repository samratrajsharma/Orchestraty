"""orch findings, orch label and orch missed: the user's word on the findings reaches the record
through the coordinator, and the commands only ever read the record."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from orchestraty import cli, feedback
from orchestraty.coordinator import main as cmain
from orchestraty.coordinator.record import Record
from orchestraty.paths import State
from orchestraty.rules.overlap import Finding

NS = 1_000_000_000
A, B = "A1b2c3d4-aaaa", "B5e6f7a8-bbbb"
O3 = f"O3|a.py::charge|{A}|{B}|c.py"
O6 = f"O6|a.py::charge|{A}"
O1 = f"O1|c.py|{A}|{B}"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def fill(state):
    """A record as the coordinator leaves it: two ended sessions, two open findings and a
    resolved one."""
    t = time.time_ns()
    rec = Record(state.record)
    with rec.transaction():
        rid = rec.repo_id("/w/repo/.git")
        for sid, top, branch in ((A, "/w/wt-a", "feat-a"), (B, "/w/wt-b", "feat-b")):
            rec.put_session({"id": sid, "agent": "claude", "repo_id": rid, "cwd": top, "worktree": top, "branch": branch,
                             "base_commit": "c0ffee", "base_guessed": 0, "started": t - 3600 * NS, "last_seen": t,
                             "state": "ended", "turn": "", "note": "ended"})
        rec.put_finding(rid, Finding(O1, "O1", "weak", (A, B), file="c.py", detail={"file": "c.py"}), t - 900 * NS)
        rec.put_finding(rid, Finding(O3, "O3", "strong", (A, B), changer=A, receiver=B, symbol="a.py::charge",
                                     file="c.py", detail={"uses": [{"symbol": "c.py::use", "lines": [5]}],
                                                          "latent": False}), t - 600 * NS)
        rec.put_finding(rid, Finding(O6, "O6", "strong", (A,), changer=A, symbol="a.py::charge", file="a.py",
                                     detail={"left": [{"symbol": "b.py::pay", "file": "b.py", "lines": [5]}]}), t - 300 * NS)
        rec.resolve_finding(O1, t - 100 * NS)
    rec.close()


def listed(capsys, *args):
    capsys.readouterr()
    assert cli.main(["findings", "--json", *args]) == 0
    return json.loads(capsys.readouterr().out)


def stored(state):
    rec = Record(state.record, readonly=True)
    try:
        return rec.labels(), rec.missed()
    finally:
        rec.close()


def run_coordinator(state):
    c = cmain.Coordinator(state)
    try:
        c.step()
    finally:
        c.close()


def test_findings_lists_the_open_ones_numbered_for_a_day(state, capsys):
    fill(state)
    data = listed(capsys)
    assert data["schema"] == 1 and [f["key"] for f in data["findings"]] == [O6, O3]       # the latest first
    o6, o3 = data["findings"]
    assert (o3["rule"], o3["strength"], o3["what"], o3["state"], o3["label"]) == ("O3", "strong", "a.py::charge -> c.py",
                                                                                  "open", None)
    assert [s["id"] for s in o3["sessions"]] == [A, B] and o3["sessions"][0]["worktree"] == "/w/wt-a"
    assert o6["what"] == "a.py::charge: 1 caller left behind"
    numbers = {f["key"]: f["number"] for f in data["findings"]}
    assert sorted(numbers.values()) == [1, 2]
    everything = listed(capsys, "--all")                                  # the resolved one comes in after
    assert {f["key"]: f["number"] for f in everything["findings"]} == dict(numbers, **{O1: 3})
    assert listed(capsys)["findings"][1]["number"] == numbers[O3]        # the same numbers again
    tomorrow = time.time() + 25 * 3600
    assert feedback.target(state, str(numbers[O3]))["key"] == O3
    with pytest.raises(feedback.Refused):
        feedback.target(state, str(numbers[O3]), now=tomorrow)             # a number holds for 24 hours
    capsys.readouterr()
    assert cli.main(["findings"]) == 0
    out = capsys.readouterr().out
    assert "O3" in out and "a.py::charge -> c.py" in out and "O1" not in out and "orch label <#>" in out


def test_a_label_reaches_the_record_through_the_coordinator(state, capsys):
    fill(state)
    numbers = {f["key"]: f["number"] for f in listed(capsys)["findings"]}
    assert cli.main(["label", str(numbers[O3]), "real", "--why", "it broke my build"]) == 0
    assert "O3 a.py::charge -> c.py: labelled real" in capsys.readouterr().out
    assert cli.main(["label", O6, "harmless"]) == 0                        # by its key
    [day] = list((Path(state.spool) / "orch").iterdir())
    lines = [json.loads(line) for line in day.read_bytes().splitlines()]
    assert [(x["agent"], x["event"], x["finding"], x["verdict"]) for x in lines] == [
        ("orch", "label", O3, "real"), ("orch", "label", O6, "harmless")]
    assert stored(state) == ([], [])                                      # the command did not write the record
    run_coordinator(state)
    labels, _ = stored(state)
    assert [(r["finding_key"], r["verdict"], r["why"]) for r in labels] == [(O3, "real", "it broke my build"),
                                                                         (O6, "harmless", "")]
    assert cli.main(["label", str(numbers[O3]), "false"]) == 0             # changed its mind: the latest counts
    run_coordinator(state)
    assert {f["key"]: f["label"]["verdict"] for f in listed(capsys)["findings"]} == {O3: "false", O6: "harmless"}


def test_a_missed_note_reaches_the_record_through_the_coordinator(state, capsys):
    fill(state)
    assert cli.main(["missed", "B was not told that refund() changed", "--session", "B5e6"]) == 0
    assert "for session B5e6f7a8" in capsys.readouterr().out
    assert cli.main(["missed", "nobody said that two sessions shared a folder"]) == 0
    assert stored(state) == ([], [])
    run_coordinator(state)
    _, missed = stored(state)
    assert [(r["session_id"], r["text"]) for r in missed] == [(B, "B was not told that refund() changed"),
                                                              ("", "nobody said that two sessions shared a folder")]


def test_what_cannot_be_labelled_or_reported_is_refused(state, capsys):
    fill(state)
    assert cli.main(["label", "7", "real"]) == 2                           # no number given yet
    assert cli.main(["label", "O9|nothing", "real"]) == 2
    assert cli.main(["missed", "   "]) == 2
    assert cli.main(["missed", "a note", "--session", "C0"]) == 2
    err = capsys.readouterr().err
    assert "no finding has the number 7" in err and "no finding has the key O9|nothing" in err
    assert "no session C0 in the record" in err
    with pytest.raises(SystemExit):
        cli.main(["label", O3, "maybe"])
    assert not (Path(state.spool) / "orch").exists()


def test_the_commands_open_the_record_read_only(state, capsys, monkeypatch):
    fill(state)
    opened = []
    real = Record.__init__

    def spy(self, path, readonly=False):
        opened.append(readonly)
        real(self, path, readonly)
    monkeypatch.setattr(Record, "__init__", spy)
    before = hashlib.sha256(Path(state.record).read_bytes()).hexdigest()
    for argv in (["findings"], ["findings", "--all", "--days", "3", "--json"], ["label", "1", "false"],
                 ["label", O6, "real", "--why", "x"], ["missed", "a note", "--session", A[:6]]):
        assert cli.main(argv) == 0
    assert len(opened) >= 5 and all(opened)
    assert hashlib.sha256(Path(state.record).read_bytes()).hexdigest() == before


def test_lines_of_another_shape_in_orchs_spool_are_dropped(state):
    fill(state)
    folder = Path(state.spool) / "orch"
    folder.mkdir(parents=True)
    junk = [{"event": "label", "finding": "O9|unknown", "verdict": "real"},
            {"event": "label", "finding": O3, "verdict": "maybe"},
            {"event": "label", "finding": O3, "verdict": "real", "why": 5},
            {"event": "missed", "text": "  "},
            {"event": "SessionStart", "session_id": "S", "cwd": "/w/wt-a"},       # a hook's line, in orch's folder
            {"event": "missed", "text": 3}]
    (folder / "x.jsonl").write_bytes(b"".join(json.dumps(j).encode() + b"\n" for j in junk) + b"not json\n")
    run_coordinator(state)
    assert stored(state) == ([], [])
    rec = Record(state.record, readonly=True)
    try:
        assert rec.session("S") is None
    finally:
        rec.close()


def test_orchs_day_files_go_once_read_and_a_day_old(state):
    fill(state)
    feedback.missed(state, "an old report", now=time.time() - 3 * 86400)
    feedback.missed(state, "a report of today")
    old, new = sorted((Path(state.spool) / "orch").iterdir())
    os.utime(old, (time.time() - 2 * 86400, time.time() - 2 * 86400))
    c = cmain.Coordinator(state)
    try:
        c.step()
        c.clean_spool(time.time_ns())
    finally:
        c.close()
    assert not old.exists() and new.exists()
    assert [r["text"] for r in stored(state)[1]] == ["an old report", "a report of today"]
