"""The coordinator's parts: the lock, the heartbeat, the spool reader and the record."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orchestraty.coordinator import spool as spool_mod
from orchestraty.coordinator.lock import Heartbeat, Lock, is_fresh, touch
from orchestraty.coordinator import record as record_module
from orchestraty.coordinator.record import SCHEMA, Record, SchemaError, repo_key, repo_name
from orchestraty.coordinator.spool import SpoolReader

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

TRY_LOCK = """
import sys
sys.path.insert(0, %r)
from orchestraty.coordinator.lock import Lock
lock = Lock(sys.argv[1])
print("yes" if lock.acquire() else "no")
"""


def other_process_gets(path):
    out = subprocess.run([sys.executable, "-B", "-c", TRY_LOCK % str(PACKAGE_ROOT), str(path)],
                         capture_output=True, text=True, timeout=60)
    return out.stdout.strip() == "yes"


def test_the_lock_is_exclusive_across_processes(tmp_path):
    path = tmp_path / "coordinator.lock"
    lock = Lock(str(path))
    assert lock.acquire() and lock.held
    assert lock.acquire()                         # again in the same process: still held
    assert not other_process_gets(path)
    lock.release()
    assert not lock.held
    assert other_process_gets(path)


def test_the_heartbeat_keeps_a_file_fresh_and_removes_it(tmp_path):
    path = tmp_path / "alive"
    beat = Heartbeat(str(path), interval=0.05)
    beat.start()
    os.utime(path, (time.time() - 100, time.time() - 100))
    time.sleep(0.3)
    assert is_fresh(str(path), 10)
    beat.stop()
    assert not path.exists() and not is_fresh(str(path), 10)


def test_is_fresh(tmp_path):
    path = tmp_path / "f"
    touch(str(path))
    assert is_fresh(str(path), 10)
    os.utime(path, (time.time() - 60, time.time() - 60))
    assert not is_fresh(str(path), 10)


# --------------------------------------------------------------------------------------- #
# The spool reader
# --------------------------------------------------------------------------------------- #
def lines_of(polled):
    return [line for _agent, _path, line in polled]


def test_complete_lines_are_read_once_and_a_partial_line_waits(tmp_path):
    f = tmp_path / "claude" / "s1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_bytes(b'{"a":1}\n{"a":2}\n{"a":')
    reader = SpoolReader(str(tmp_path))
    polled = reader.poll()
    assert lines_of(polled) == [b'{"a":1}', b'{"a":2}'] and polled[0][0] == "claude"
    assert reader.poll() == []
    with open(f, "ab") as fh:
        fh.write(b'3}\n')
    assert lines_of(reader.poll()) == [b'{"a":3}']


def test_positions_survive_a_restart(tmp_path):
    f = tmp_path / "claude" / "s1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_bytes(b'{"a":1}\n')
    first = SpoolReader(str(tmp_path))
    first.poll()
    saved = first.take_changes()
    assert first.take_changes() == {}
    again = SpoolReader(str(tmp_path), saved)
    assert again.poll() == []
    with open(f, "ab") as fh:
        fh.write(b'{"a":2}\n')
    assert lines_of(again.poll()) == [b'{"a":2}']


def test_a_replaced_or_truncated_file_is_read_from_the_start(tmp_path):
    f = tmp_path / "claude" / "s1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_bytes(b'{"a":1}\n{"a":2}\n')
    reader = SpoolReader(str(tmp_path))
    reader.poll()
    f.write_bytes(b'{"b":1}\n')                   # truncated and rewritten, shorter
    assert lines_of(reader.poll()) == [b'{"b":1}']
    os.remove(f)
    f.write_bytes(b'{"c":1}\n{"c":2}\n{"c":3}\n')  # a new file, longer
    assert lines_of(reader.poll()) == [b'{"c":1}', b'{"c":2}', b'{"c":3}']


def test_a_file_with_the_same_inode_but_other_bytes_is_read_from_the_start(tmp_path):
    # Linux hands a deleted file's inode to the next new file at once; the bytes before the
    # offset tell the two apart.
    f = tmp_path / "claude" / "s1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_bytes(b'{"a":1}\n{"a":2}\n')
    reader = SpoolReader(str(tmp_path))
    reader.poll()
    fid, offset = reader.positions[str(f)]
    dev, ino, tail = fid.split(":")
    assert bytes.fromhex(tail) == b'{"a":1}\n{"a":2}\n'[-16:] and offset == 16
    f.write_bytes(b'{"x":1}\n{"x":2}\n{"x":3}\n')        # same file object, other content, longer
    assert lines_of(reader.poll()) == [b'{"x":1}', b'{"x":2}', b'{"x":3}']
    reader.positions[str(f)] = ("%s:%s:zz" % (dev, ino), 8)    # a damaged saved position
    assert len(lines_of(reader.poll())) == 3


def test_an_overlong_line_without_a_newline_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(spool_mod, "MAX_LINE", 16)
    f = tmp_path / "claude" / "s1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 40)
    reader = SpoolReader(str(tmp_path))
    assert reader.poll() == [] and reader.skipped == 1
    with open(f, "ab") as fh:
        fh.write(b'\n{"a":1}\n')
    assert lines_of(reader.poll()) == [b'{"a":1}']


def test_only_jsonl_files_in_agent_folders_are_read(tmp_path):
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / "s1.jsonl").write_bytes(b'{"a":1}\n')
    (tmp_path / "claude" / "notes.txt").write_bytes(b'{"a":2}\n')
    (tmp_path / "top.jsonl").write_bytes(b'{"a":3}\n')
    assert lines_of(SpoolReader(str(tmp_path)).poll()) == [b'{"a":1}']
    assert SpoolReader(str(tmp_path / "missing")).poll() == []


# --------------------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------------------- #
def test_the_record_keeps_which_files_were_not_compared(tmp_path):
    path = str(tmp_path / "r" / "record.sqlite")
    rec = Record(path)
    with rec.transaction():
        rec.put_changes("A", {"a.py": "M"}, [], [], {"a.py": "does not parse"})
    assert rec.not_compared("A") == {"a.py": "does not parse"}
    with rec.transaction():
        rec.put_changes("A", {"a.py": "M"}, [], [])
    assert rec.not_compared("A") == {}
    rec.db.execute("DROP TABLE session_not_compared")
    rec.meta_set("schema", 4)
    rec.close()
    reader = Record(path, readonly=True)
    assert reader.not_compared("A") == {}                                # a reader copes with no such table
    reader.close()


def test_the_record_is_created_with_its_schema_and_reopens(tmp_path):
    path = str(tmp_path / "r" / "record.sqlite")
    rec = Record(path)
    assert rec.meta_get("schema") == str(SCHEMA) == "6"
    assert rec.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert all(rec.has_table(t) for t in ("labels", "missed", "declarations", "changes", "claims",
                                         "session_not_compared"))
    rec.close()
    Record(path).close()


def test_a_record_from_before_labels_is_brought_up_to_date(tmp_path):
    # A schema-1 record, as Orchestraty wrote it before labels: its tables, a finding, a note.
    path = tmp_path / "record.sqlite"
    db = sqlite3.connect(str(path))
    db.executescript(record_module._TABLES)
    db.execute("INSERT INTO meta VALUES ('schema', '1')")
    db.execute("INSERT INTO findings VALUES ('O6|a.py::charge|A', 1, 'O6', 'strong', 'A', '', '[\"A\"]', "
               "'a.py::charge', 'a.py', '{}', 'open', 5, 6, NULL)")
    db.execute("INSERT INTO notes (finding_key, session_id, kind, slot, text, state, created) "
               "VALUES ('O6|a.py::charge|A', 'A', 'stop', 'notes/A/stop.json', 'a note', 'pending', 5)")
    db.commit()
    db.close()
    reader = Record(str(path), readonly=True)            # a reader copes with the tables not there yet
    assert reader.labels() == [] and reader.missed() == []
    reader.close()
    rec = Record(str(path))                               # the coordinator opens it: brought up to date
    assert rec.meta_get("schema") == str(SCHEMA)
    assert [f["key"] for f in rec.findings()] == ["O6|a.py::charge|A"]
    assert [n["text"] for n in rec.notes()] == ["a note"]
    with rec.transaction():
        rec.add_label("O6|a.py::charge|A", "real", "it was", 7)
        rec.add_missed(8, "B", "a note about refund")
    assert [(r["finding_key"], r["verdict"], r["why"], r["ts"]) for r in rec.labels()] == [
        ("O6|a.py::charge|A", "real", "it was", 7)]
    assert [(r["ts"], r["session_id"], r["text"]) for r in rec.missed()] == [(8, "B", "a note about refund")]
    rec.close()
    again = Record(str(path))                             # reopening changes nothing more
    assert again.meta_get("schema") == str(SCHEMA) and len(again.labels()) == 1
    again.close()


def schema_2_record(path):
    """A record as schema 2 left it: its tables, and a session, a finding and three notes."""
    db = sqlite3.connect(str(path))
    db.executescript(record_module._TABLES)
    for sql in record_module._MIGRATIONS[2]:
        db.execute(sql)
    db.execute("INSERT INTO meta VALUES ('schema', '2')")
    db.execute("INSERT INTO sessions (id, agent, repo_id, started, last_seen, state) VALUES ('A', 'claude', 1, 10, 60, 'ended')")
    db.execute("INSERT INTO findings VALUES ('O6|a.py::charge|A', 1, 'O6', 'strong', 'A', '', '[\"A\"]', 'a.py::charge', "
               "'a.py', '{}', 'resolved', 20, 45, 50)")
    for kind, state, written, delivered in (("stop", "delivered", 30, 40), ("next", "withdrawn", 35, None),
                                            ("edit", "pending", None, None)):
        db.execute("INSERT INTO notes (finding_key, session_id, kind, slot, text, state, created, written, delivered) "
                   "VALUES ('O6|a.py::charge|A', 'A', ?, 's', 't', ?, 25, ?, ?)", (kind, state, written, delivered))
    db.commit()
    db.close()


def test_a_record_from_before_declarations_gets_its_history_logged(tmp_path):
    path = tmp_path / "record.sqlite"
    schema_2_record(path)
    reader = Record(str(path), readonly=True)                       # before the coordinator: nothing to show
    assert reader.change_log() == [] and reader.declarations() == []
    reader.close()
    rec = Record(str(path))
    assert rec.meta_get("schema") == str(SCHEMA)
    assert [(c["ts"], c["kind"]) for c in rec.change_log()] == [
        (10, "session_started"), (20, "finding_opened"), (30, "note_written"), (35, "note_written"),
        (40, "note_delivered"), (50, "finding_resolved"), (60, "session_ended")]      # a withdrawal has no time
    with rec.transaction():
        rec.add_declaration("A", ["c.py"], ["c.py::refund"], "add a refund", 100, 200)
    [d] = rec.declarations(active_at=150)
    assert (d["session_id"], d["paths"], d["symbols"], d["task"]) == ("A", ["c.py"], ["c.py::refund"], "add a refund")
    assert rec.declarations(active_at=200) == [] and rec.declarations(active_at=99) == []
    assert rec.change_log(after_id=7)[0]["kind"] == "declared"
    rec.close()


def test_a_record_from_before_claims_keeps_what_it_has_and_takes_claims(tmp_path):
    path = tmp_path / "record.sqlite"
    schema_2_record(path)
    rec = Record(str(path))
    with rec.transaction():
        rec.add_declaration("A", ["c.py"], [], "", 100, 200)
    rec.db.execute("DROP TABLE claims")                         # as schema 3 left it
    rec.meta_set("schema", 3)
    rec.close()
    reader = Record(str(path), readonly=True)
    assert reader.claims() == [] and reader.claim(1) is None    # a reader copes with no claims table
    reader.close()
    rec = Record(str(path))
    assert rec.meta_get("schema") == "6" and len(rec.declarations()) == 1 and len(rec.change_log()) == 8
    with rec.transaction():
        first = rec.add_claim(1, "A", "symbol", "a.py::charge", "a.py", True, 300, 400)
        second = rec.add_claim(1, "B", "area", "billing", "", False, 310, 900)
    assert [c["id"] for c in rec.claims(active_at=350)] == [first, second]
    assert [c["id"] for c in rec.claims(active_at=500)] == [second]              # the first expired at 400
    assert rec.claims(active_at=299) == [] and rec.claims(repo_id=2) == []
    with rec.transaction():
        assert rec.end_claim(second, 600, "released") and not rec.end_claim(second, 610, "released")
        assert not rec.end_claim(first, 620, "released")                         # expired: nothing to end
    assert rec.claims(active_at=650) == [] and rec.claim(second)["why"] == "released"
    assert rec.claim(first)["hard"] == 1 and rec.claim(first)["file"] == "a.py"
    assert [(c["kind"], c["session_id"], c["ref"]) for c in rec.change_log(after_id=8)] == [
        ("claimed", "A", first), ("claimed", "B", second), ("claim_ended", "B", second)]
    rec.close()


def test_the_write_methods_log_each_change_in_order(tmp_path):
    from orchestraty.rules.overlap import Finding
    rec = Record(str(tmp_path / "record.sqlite"))
    session = {"id": "A", "agent": "claude", "repo_id": 1, "cwd": "/w", "worktree": "/w", "branch": "b",
               "base_commit": "c", "base_guessed": 0, "started": 5, "last_seen": 5, "state": "live", "turn": "", "note": ""}
    f = Finding("O6|a.py::charge|A", "O6", "strong", ("A",), changer="A", symbol="a.py::charge", file="a.py")
    with rec.transaction():
        rec.put_session(session)
        rec.put_session(dict(session, last_seen=6, turn="working"))           # no change of state: nothing logged
        assert rec.put_finding(1, f, 10) and not rec.put_finding(1, f, 11)
        rec.add_note(f.key, "A", "stop", "notes/A/stop.json", "a note", 12)
        rec.add_note(f.key, "A", "next", "notes/A/next.json", "a note", 12)
        rec.mark_written([1, 2], 13)
        rec.mark_written([1], 14)                                             # already written: nothing new
        rec.mark_delivered("notes/A/stop.json", 15)
        rec.set_note_state(2, "withdrawn", 16)
        rec.resolve_finding(f.key, 17)
        rec.resolve_finding(f.key, 18)                                        # already resolved
        rec.put_session(dict(session, last_seen=19, state="ended"))
    assert [(c["ts"], c["kind"], c["session_id"], c["finding_key"], c["ref"]) for c in rec.change_log()] == [
        (5, "session_started", "A", "", None), (10, "finding_opened", "", f.key, None),
        (13, "note_written", "A", f.key, 1), (13, "note_written", "A", f.key, 2), (15, "note_delivered", "A", f.key, 1),
        (16, "note_withdrawn", "A", f.key, 2), (17, "finding_resolved", "", f.key, None), (19, "session_ended", "A", "", None)]
    assert [c["kind"] for c in rec.change_log(since=16)] == ["note_withdrawn", "finding_resolved", "session_ended"]
    rec.close()


def test_a_newer_record_is_refused(tmp_path):
    path = str(tmp_path / "record.sqlite")
    rec = Record(path)
    rec.meta_set("schema", "99")
    rec.close()
    with pytest.raises(SchemaError, match="written by a newer Orchestraty .*upgrade Orchestraty, or move that file aside"):
        Record(path)


def test_events_repos_and_sessions(tmp_path):
    rec = Record(str(tmp_path / "record.sqlite"))
    with rec.transaction():
        rec.add_event({"ts": 5, "agent": "claude", "event": "PostToolUse", "session_id": "s1",
                       "file_path": "/w/a.py"}, {"len": 10})
        rid = rec.repo_id(os.path.join(str(tmp_path), "Repo", ".git"))
        assert rec.repo_id(os.path.join(str(tmp_path), "Repo", ".git") + os.sep) == rid
        rec.put_session({"id": "s1", "agent": "claude", "repo_id": rid, "started": 1, "last_seen": 2,
                         "state": "live", "base_guessed": 0})
    [event] = rec.events("s1")
    assert event["file_path"] == "/w/a.py" and json.loads(event["detail"]) == {"len": 10}
    assert rec.session("s1")["worktree"] == "" and rec.session("nope") is None
    assert [s["id"] for s in rec.sessions(states=("live",), repo_id=rid)] == ["s1"]
    assert rec.sessions(states=("ended",)) == []
    assert rec.repos()[0]["name"] == "Repo"


def test_a_sessions_recent_events_come_latest_first(tmp_path):
    path = str(tmp_path / "record.sqlite")
    rec = Record(path)
    with rec.transaction():
        for i in range(5):
            rec.add_event({"ts": i, "agent": "claude", "event": f"E{i}", "session_id": "s1"})
        rec.add_event({"ts": 9, "agent": "claude", "event": "Other", "session_id": "s2"})
    rec.close()
    reader = Record(path, readonly=True)
    try:
        assert [e["event"] for e in reader.recent_events("s1", limit=3)] == ["E4", "E3", "E2"]
        assert len(reader.recent_events("s1")) == 5 and reader.recent_events("nope") == []
    finally:
        reader.close()


def test_one_finding_by_its_key_in_any_state(tmp_path):
    from orchestraty.rules.overlap import Finding
    path = str(tmp_path / "record.sqlite")
    rec = Record(path)
    f = Finding("O6|a.py::charge|A", "O6", "strong", ("A",), changer="A", symbol="a.py::charge", file="a.py",
                detail={"left": [{"symbol": "b.py::pay", "file": "b.py", "lines": [5]}]})
    with rec.transaction():
        rec.put_finding(1, f, 10)
        rec.resolve_finding(f.key, 20)
    rec.close()
    reader = Record(path, readonly=True)
    try:
        got = reader.finding(f.key)
        assert got["state"] == "resolved" and got["resolved"] == 20 and got["sessions"] == ["A"]
        assert got["detail"]["left"][0]["lines"] == [5]
        assert got == reader.findings(states=None)[0]
        assert reader.finding("O6|nope|A") is None
    finally:
        reader.close()


def test_a_failed_transaction_leaves_nothing(tmp_path):
    rec = Record(str(tmp_path / "record.sqlite"))
    with pytest.raises(RuntimeError):
        with rec.transaction():
            rec.add_event({"ts": 1, "agent": "claude", "event": "Stop"})
            raise RuntimeError("boom")
    assert rec.events() == []


def test_a_reader_cannot_write(tmp_path):
    path = str(tmp_path / "record.sqlite")
    Record(path).close()
    reader = Record(path, readonly=True)
    with pytest.raises(Exception):
        reader.add_event({"ts": 1, "agent": "claude", "event": "Stop"})


def test_repo_names_and_keys(tmp_path):
    assert repo_name(os.path.join("x", "Orchestraty", ".git")) == "Orchestraty"
    assert repo_name(os.path.join("x", "bare.git")) == "bare"
    assert repo_key(os.path.join("x", "A", ".git")) == repo_key(os.path.join("x", "A", ".", ".git"))


def test_a_rewound_reader_reads_the_lines_again(tmp_path):
    f = tmp_path / "claude" / "s1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_bytes(b'{"a":1}\n')
    reader = SpoolReader(str(tmp_path))
    saved = dict(reader.positions)
    assert lines_of(reader.poll()) == [b'{"a":1}']
    reader.rewind(saved)                                   # what was read was not saved
    assert reader.take_changes() == {} and lines_of(reader.poll()) == [b'{"a":1}']


def test_a_savepoint_undoes_only_its_own_writes(tmp_path):
    record = Record(str(tmp_path / "record.sqlite"))
    try:
        with record.transaction():
            record.add_event({"ts": 1, "agent": "claude", "event": "before"})
            with pytest.raises(ValueError):
                with record.savepoint():
                    record.add_event({"ts": 2, "agent": "claude", "event": "undone"})
                    raise ValueError("this line only")
            record.add_event({"ts": 3, "agent": "claude", "event": "after"})
        assert [e["event"] for e in record.events()] == ["before", "after"]
    finally:
        record.close()


def test_a_record_that_cannot_be_read_is_not_held_open(tmp_path):
    path = tmp_path / "record.sqlite"
    path.write_bytes(b"GARBAGE!" * 512)
    with pytest.raises(sqlite3.DatabaseError):
        Record(str(path))
    os.replace(path, tmp_path / "aside.sqlite")            # Windows: an open file could not be moved
