"""The local record: one SQLite file per user and environment, written only by the coordinator.

Write-ahead logging, so ``orch status`` and other readers never wait for the writer. The
``events`` table is append-only: every spool line the coordinator accepted, in the shape of
the versioned event schema. ``sessions`` and ``repos`` are projections that can be rebuilt
from the events and git. ``spool_files`` keeps where reading stopped in each spool file, saved
in the same transaction as the events read, so a restart neither loses nor repeats a line.
"""
import json
import os
import sqlite3
from contextlib import contextmanager

SCHEMA = 1

_TABLES = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spool_files (
    path TEXT PRIMARY KEY, file_id TEXT NOT NULL, offset INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    agent TEXT NOT NULL,
    event TEXT NOT NULL,
    session_id TEXT,
    tool_name TEXT,
    tool_use_id TEXT,
    cwd TEXT,
    file_path TEXT,
    detail TEXT);
CREATE INDEX IF NOT EXISTS events_by_session ON events (session_id, ts);
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    common_dir TEXT NOT NULL,
    name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    repo_id INTEGER,
    cwd TEXT NOT NULL DEFAULT '',
    worktree TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    base_commit TEXT NOT NULL DEFAULT '',
    base_guessed INTEGER NOT NULL DEFAULT 0,
    started INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    state TEXT NOT NULL,
    turn TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '');
"""

EVENT_COLUMNS = ("ts", "agent", "event", "session_id", "tool_name", "tool_use_id", "cwd", "file_path")
SESSION_COLUMNS = ("id", "agent", "repo_id", "cwd", "worktree", "branch", "base_commit", "base_guessed",
                   "started", "last_seen", "state", "turn", "note")


class SchemaError(Exception):
    """The record was written by a newer version of Orchestraty."""


def repo_key(common_dir):
    return os.path.normcase(os.path.normpath(common_dir))


def repo_name(common_dir):
    """A short name for a repository: its main worktree's folder name."""
    path = os.path.normpath(common_dir)
    base = os.path.basename(path)
    if base.lower() == ".git":
        return os.path.basename(os.path.dirname(path)) or base
    return base[:-4] if base.lower().endswith(".git") else base


class Record:
    def __init__(self, path, readonly=False):
        self.path = path
        self.readonly = readonly
        if not readonly:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout = 5000")
        if readonly:
            self.db.execute("PRAGMA query_only = ON")
            return
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.executescript(_TABLES)
        found = self.meta_get("schema")
        if found is None:
            self.meta_set("schema", str(SCHEMA))
        elif int(found) > SCHEMA:
            self.db.close()
            raise SchemaError(f"the record at {path} has schema {found}; this version reads {SCHEMA}")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # meta
    def meta_get(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))

    # spool positions
    def positions(self):
        return {r["path"]: (r["file_id"], r["offset"]) for r in self.db.execute("SELECT * FROM spool_files")}

    def save_positions(self, changes):
        self.db.executemany("INSERT OR REPLACE INTO spool_files (path, file_id, offset) VALUES (?, ?, ?)",
                            [(p, fid, off) for p, (fid, off) in changes.items()])

    def forget_position(self, path):
        self.db.execute("DELETE FROM spool_files WHERE path = ?", (path,))

    # events
    def add_event(self, line, detail=None):
        values = [line.get(c) for c in EVENT_COLUMNS]
        values.append(json.dumps(detail, sort_keys=True) if detail else None)
        cur = self.db.execute("INSERT INTO events (%s, detail) VALUES (%s)" % (
            ", ".join(EVENT_COLUMNS), ", ".join("?" * (len(EVENT_COLUMNS) + 1))), values)
        return cur.lastrowid

    def events(self, session_id=None):
        if session_id is None:
            rows = self.db.execute("SELECT * FROM events ORDER BY id")
        else:
            rows = self.db.execute("SELECT * FROM events WHERE session_id = ? ORDER BY id", (session_id,))
        return [dict(r) for r in rows]

    # repositories
    def repo_id(self, common_dir):
        key = repo_key(common_dir)
        row = self.db.execute("SELECT id FROM repos WHERE key = ?", (key,)).fetchone()
        if row:
            return row[0]
        cur = self.db.execute("INSERT INTO repos (key, common_dir, name) VALUES (?, ?, ?)",
                              (key, os.path.normpath(common_dir), repo_name(common_dir)))
        return cur.lastrowid

    def repos(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM repos ORDER BY id")]

    # sessions
    def session(self, sid):
        row = self.db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return dict(row) if row else None

    def put_session(self, s):
        self.db.execute("INSERT OR REPLACE INTO sessions (%s) VALUES (%s)" % (
            ", ".join(SESSION_COLUMNS), ", ".join("?" * len(SESSION_COLUMNS))),
            [s.get(c, "" if c not in ("repo_id", "base_guessed", "started", "last_seen") else None)
             for c in SESSION_COLUMNS])

    def sessions(self, states=None, repo_id=None):
        sql, args = "SELECT * FROM sessions", []
        where = []
        if states:
            where.append("state IN (%s)" % ", ".join("?" * len(states)))
            args.extend(states)
        if repo_id is not None:
            where.append("repo_id = ?")
            args.append(repo_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY started", args)]
