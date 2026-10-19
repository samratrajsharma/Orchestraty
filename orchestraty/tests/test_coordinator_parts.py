"""The coordinator's parts: the lock, the heartbeat, the spool reader and the record."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orchestraty.coordinator import spool as spool_mod
from orchestraty.coordinator.lock import Heartbeat, Lock, is_fresh, touch
from orchestraty.coordinator.record import Record, SchemaError, repo_key, repo_name
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
def test_the_record_is_created_with_its_schema_and_reopens(tmp_path):
    path = str(tmp_path / "r" / "record.sqlite")
    rec = Record(path)
    assert rec.meta_get("schema") == "1"
    assert rec.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    rec.close()
    Record(path).close()


def test_a_newer_record_is_refused(tmp_path):
    path = str(tmp_path / "record.sqlite")
    rec = Record(path)
    rec.meta_set("schema", "99")
    rec.close()
    with pytest.raises(SchemaError):
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
