"""Record schema 6: the tables that will hold teammates' work, added when a record is opened.

Schema 6 only adds tables, as steps 2 to 5 did, so a record of 0.1.1 keeps every row; readers
cope with a record that has not been brought up yet. A value withheld from a teammate is kept
only as a fingerprint: the table has nowhere to put the value itself."""
import sqlite3

import pytest

from orchestraty import events
from orchestraty.coordinator import record as record_module
from orchestraty.coordinator.record import SCHEMA, Record, SchemaError

TEAM_TABLES = ("team_sessions", "team_writers", "team_sent", "team_withheld", "team_confirmed", "team_planned")
NS = 1_000_000_000


def schema_5_record(path):
    """A record as 0.1.1 left it (schema 5), with a session, a finding, a note, a claim, a
    declaration and a label."""
    db = sqlite3.connect(str(path))
    db.executescript(record_module._TABLES)
    for step in (2, 3, 4, 5):
        for sql in record_module._MIGRATIONS[step]:
            db.execute(sql)
    db.execute("INSERT INTO meta VALUES ('schema', '5')")
    db.execute("INSERT INTO repos (key, common_dir, name) VALUES ('/r/.git', '/r/.git', 'r')")
    db.execute("INSERT INTO sessions VALUES ('A', 'claude', 1, '/r', '/r', 'main', 'c0', 0, 1, 2, 'live', '', '')")
    db.execute("INSERT INTO findings VALUES ('O6|a.py::charge|A', 1, 'O6', 'strong', 'A', '', '[\"A\"]', "
               "'a.py::charge', 'a.py', '{}', 'open', 5, 6, NULL)")
    db.execute("INSERT INTO notes (finding_key, session_id, kind, slot, text, state, created) "
               "VALUES ('O6|a.py::charge|A', 'A', 'stop', 'notes/A/stop.json', 'a note', 'pending', 5)")
    db.execute("INSERT INTO claims (repo_id, session_id, kind, target, file, hard, ts, expires) "
               "VALUES (1, 'A', 'file', 'a.py', '', 0, 3, 99)")
    db.execute("INSERT INTO declarations (session_id, paths, symbols, task, ts, expires) "
               "VALUES ('A', '[\"a.py\"]', '[]', 'change a.py', 3, 99)")
    db.execute("INSERT INTO labels (finding_key, verdict, why, ts) VALUES ('O6|a.py::charge|A', 'real', 'it was', 7)")
    db.commit()
    db.close()


def rows(path, table):
    db = sqlite3.connect(str(path))
    try:
        return db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    finally:
        db.close()


def tables(rec):
    return {r[0] for r in rec.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_a_schema_5_record_is_brought_to_6(tmp_path):
    path = tmp_path / "record.sqlite"
    schema_5_record(path)
    kept = ("repos", "sessions", "findings", "notes", "claims", "declarations", "labels", "changes", "events")
    before = {t: rows(path, t) for t in kept}
    rec = Record(str(path))
    try:
        assert rec.meta_get("schema") == "6" == str(SCHEMA)
        assert set(TEAM_TABLES) <= tables(rec)
        assert all(rec.has_table(t) for t in TEAM_TABLES)
    finally:
        rec.close()
    assert {t: rows(path, t) for t in kept} == before                      # every row as it was
    again = Record(str(path))                                              # opening it again changes nothing
    try:
        assert again.meta_get("schema") == "6"
    finally:
        again.close()
    assert {t: rows(path, t) for t in kept} == before


def test_new_record_has_every_step(tmp_path):
    rec = Record(str(tmp_path / "r" / "record.sqlite"))
    try:
        assert rec.meta_get("schema") == "6"
        assert tables(rec) == {
            "meta", "spool_files", "events", "repos", "sessions", "session_files", "findings", "notes",
            "session_symbols", "labels", "missed", "declarations", "changes", "claims", "session_not_compared",
            *TEAM_TABLES}
    finally:
        rec.close()


def test_schema_7_raises_SchemaError(tmp_path):
    path = str(tmp_path / "record.sqlite")
    Record(path).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value = '7' WHERE key = 'schema'")
    db.commit()
    db.close()
    with pytest.raises(SchemaError, match=r"newer Orchestraty \(schema 7; this version reads 6\)"):
        Record(path)


def test_readonly_reader_on_schema_5_returns_empty(tmp_path):
    path = tmp_path / "record.sqlite"
    schema_5_record(path)
    reader = Record(str(path), readonly=True)
    try:
        assert not any(reader.has_table(t) for t in TEAM_TABLES)
        assert reader.team_sessions() == [] and reader.team_sessions(repo_id=1) == []
        assert reader.team_session("team-0123456789ab") is None
        assert reader.team_writers(1) == [] and reader.team_sent(1, 10) == [] and reader.withheld(1, 0) == []
        assert reader.confirmed("A") is None
        assert reader.team_planned() == [] and reader.team_planned(active_at=5, session_id="A") == []
    finally:
        reader.close()
    assert rows(path, "meta") == [("schema", "5")]                          # a reader never brings it up


def test_put_team_planned_replaces_a_sessions_items(tmp_path):
    rec = Record(str(tmp_path / "record.sqlite"))
    try:
        claim = {"kind": "file", "target": "a.py", "file": "", "hard": True, "since": 10, "until": 20,
                 "note_key": "team-claim|1"}
        with rec.transaction():
            rec.put_team_planned("team-a", [claim, dict(claim, target="b.py", since=15, until=30, note_key="team-claim|2")])
            rec.put_team_planned("team-b", [dict(claim, target="c.py", note_key="team-claim|3")])
        assert [p["target"] for p in rec.team_planned(session_id="team-a")] == ["a.py", "b.py"]
        with rec.transaction():
            rec.put_team_planned("team-a", [dict(claim, target="d.py", hard=False, note_key="team-claim|4")])
        got = rec.team_planned(session_id="team-a")
        assert [(p["target"], p["hard"], p["since"], p["until"], p["note_key"]) for p in got] == [
            ("d.py", 0, 10, 20, "team-claim|4")]
        assert [p["target"] for p in rec.team_planned(session_id="team-b")] == ["c.py"]   # another session's stay
        assert [p["target"] for p in rec.team_planned(active_at=12)] == ["c.py", "d.py"]      # in the order put
        assert rec.team_planned(active_at=20) == []                         # until is when it stops holding
        with rec.transaction():
            rec.put_team_planned("team-b", [])
        assert rec.team_planned(session_id="team-b") == []
    finally:
        rec.close()


def test_withheld_table_has_no_value_column(tmp_path):
    rec = Record(str(tmp_path / "record.sqlite"))
    try:
        columns = [r["name"] for r in rec.db.execute("PRAGMA table_info(team_withheld)")]
        assert columns == ["id", "repo_id", "ts", "field", "rule", "fingerprint"]
        with rec.transaction():
            wid = rec.add_withheld(1, 5, "task.line", "aws-key", "1a2b3c4d")
        assert rec.withheld(1, 0) == [{"id": wid, "repo_id": 1, "ts": 5, "field": "task.line", "rule": "aws-key",
                                       "fingerprint": "1a2b3c4d"}]
        assert rec.withheld(1, 6) == [] and rec.withheld(2, 0) == []
    finally:
        rec.close()


def test_team_rows_read_back_as_they_were_put(tmp_path):
    rec = Record(str(tmp_path / "record.sqlite"))
    try:
        session = {"session_id": "team-0123456789ab", "repo_id": 1, "remote": "origin", "branch": "feat/refunds",
                   "people": ["alice", "bob"], "own": 0,
                   "writers": [{"person": "alice", "device": "7f3a9c2e41b0", "session": "s1", "agent": "claude",
                                "state": "live", "turn": "working", "last_seen": 9}],
                   "evidence": "commits", "tip": "a" * 40, "tip_time": 8, "as_of": 9, "agents": ["claude"],
                   "task": {"ticket": "PAY-12", "line": "refunds"}, "merged": "", "contained_in": [], "builds_on": ["B"]}
        writer = {"repo_id": 1, "remote": "origin", "person": "alice", "device": "7f3a9c2e41b0", "store": "ref",
                  "commit_id": "b" * 40, "seq": 3, "written": 9, "read": 10, "verdict": "admitted", "why": "",
                  "entries": [{"id": "s1", "agent": "claude", "state": "live", "branch": "feat/refunds", "pushed": True}]}
        with rec.transaction():
            rec.put_team_session(session)
            rec.put_team_session(dict(session, session_id="team-ffffffffffff", repo_id=2, task="", people=[], own=1))
            rec.put_team_writer(writer)
            sent = [rec.add_team_sent(1, "origin", "ref", ts, ts, "c" * 40, 900, 2, "pushed") for ts in (11, 12)]
            rec.put_confirmed("A", "PAY-12", "refunds for annual plans", 13)
        assert rec.team_session("team-0123456789ab") == session
        assert rec.team_session("team-ffffffffffff")["task"] == ""
        assert [s["session_id"] for s in rec.team_sessions(repo_id=1)] == ["team-0123456789ab"]
        assert len(rec.team_sessions()) == 2
        assert rec.team_writers(1) == [writer] and rec.team_writers(2) == []
        assert [s["id"] for s in rec.team_sent(1, 10)] == sent[::-1]            # the latest first
        assert rec.team_sent(1, 1)[0]["withheld"] == 0
        assert rec.confirmed("A") == {"session_id": "A", "ticket": "PAY-12", "line": "refunds for annual plans", "ts": 13}
        with rec.transaction():
            rec.put_confirmed("A", "PAY-12", "", 14)                        # the author's latest word wins
        assert rec.confirmed("A")["line"] == "" and rec.confirmed("B") is None
    finally:
        rec.close()


def test_team_changes_are_logged_and_read_as_events(tmp_path):
    rec = Record(str(tmp_path / "record.sqlite"))
    try:
        writer = {"repo_id": 1, "remote": "origin", "person": "mallory", "device": "000000000000", "store": "ref",
                  "commit_id": "d" * 40, "seq": 1, "written": 9, "read": 10, "verdict": "dropped",
                  "why": "not-a-record", "entries": []}
        with rec.transaction():
            sent = rec.add_team_sent(1, "origin", "ref", 11, 4, "c" * 40, 900, 2, "pushed", withheld=1)
            wid = rec.add_withheld(1, 12, "task.line", "aws-key", "1a2b3c4d")
            rec.put_team_writer(writer)
            rec.put_team_writer(dict(writer, read=20))                     # the same drop read again: logged once
        got = [e for e in events.read(rec) if e["event"].startswith("team_")]
        assert [e["event"] for e in got] == ["team_sent", "team_withheld", "team_dropped"]
        assert set(events.TEAM_KINDS) == {e["event"] for e in got}
        sent_e, withheld_e, dropped_e = got
        assert (sent_e["team_sent"], sent_e["remote"], sent_e["store"], sent_e["seq"], sent_e["result"],
                sent_e["sessions"], sent_e["withheld"]) == (sent, "origin", "ref", 4, "pushed", 2, 1)
        assert (withheld_e["withheld"], withheld_e["field"], withheld_e["rule"]) == (wid, "task.line", "aws-key")
        assert "fingerprint" not in withheld_e and "value" not in withheld_e
        assert (dropped_e["person"], dropped_e["device"], dropped_e["why"]) == ("mallory", "000000000000", "not-a-record")
        with rec.transaction():
            rec.put_team_writer(dict(writer, verdict="admitted", why="", read=30))
        [dropped_e] = [e for e in events.read(rec) if e["event"] == "team_dropped"]
        assert dropped_e["person"] == "mallory" and "why" not in dropped_e     # it was admitted since
        assert all(e["session"] is None for e in got)
        assert all(events.render(e) for e in got)
    finally:
        rec.close()
