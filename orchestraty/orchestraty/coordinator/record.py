"""The local record: one SQLite file per user and environment, written only by the coordinator.

Write-ahead logging, so ``orch status`` and other readers never wait for the writer. The
``events`` table is append-only: every spool line the coordinator accepted, in the shape of
the versioned event schema. ``sessions`` and ``repos`` are projections that can be rebuilt
from the events and git. ``spool_files`` keeps where reading stopped in each spool file, saved
in the same transaction as the events read, so a restart neither loses nor repeats a line.
``labels`` and ``missed`` keep the user's word on the findings (``orch label``, ``orch missed``),
``declarations`` what agents say they are about to change (the MCP tool ``declare``), ``claims``
what a session has reserved (``orch claim``, the MCP tool ``claim``). ``session_not_compared``
(schema 5) the changed Python files whose functions the live map could not compare this time
(a syntax error, too large, not readable), next to ``session_files``. ``changes`` is append-only
too: the record's own write methods log each session started or ended, finding opened or
resolved, note written, delivered or withdrawn, declaration made and claim made or ended, in the
order they happen (``orch events`` reads it).

A record of an older schema is brought up to date when the coordinator opens it, one step at a
time (``_MIGRATIONS``); readers never change it, so they cope with tables that are not there yet.
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = 5

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
CREATE TABLE IF NOT EXISTS session_files (
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    attributed INTEGER NOT NULL,
    PRIMARY KEY (session_id, path));
CREATE TABLE IF NOT EXISTS findings (
    key TEXT PRIMARY KEY,
    repo_id INTEGER NOT NULL,
    rule TEXT NOT NULL,
    strength TEXT NOT NULL,
    changer TEXT NOT NULL,
    receiver TEXT NOT NULL,
    sessions TEXT NOT NULL,
    symbol TEXT NOT NULL,
    file TEXT NOT NULL,
    detail TEXT NOT NULL,
    state TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    resolved INTEGER);
CREATE INDEX IF NOT EXISTS findings_by_repo ON findings (repo_id, state);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    finding_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    slot TEXT NOT NULL,
    text TEXT NOT NULL,
    state TEXT NOT NULL,
    created INTEGER NOT NULL,
    cause_ts INTEGER,
    written INTEGER,
    delivered INTEGER,
    UNIQUE (finding_key, session_id, kind));
CREATE INDEX IF NOT EXISTS notes_by_slot ON notes (slot, state);
CREATE TABLE IF NOT EXISTS session_symbols (
    session_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    change TEXT NOT NULL,
    contract TEXT NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (session_id, symbol));
"""

# What each schema adds to the one before it. _TABLES is schema 1; a new record gets every step.
_MIGRATIONS = {
    2: ("""CREATE TABLE IF NOT EXISTS labels (
               id INTEGER PRIMARY KEY,
               finding_key TEXT NOT NULL,
               verdict TEXT NOT NULL,
               why TEXT NOT NULL DEFAULT '',
               ts INTEGER NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS labels_by_finding ON labels (finding_key, ts)",
        """CREATE TABLE IF NOT EXISTS missed (
               id INTEGER PRIMARY KEY,
               ts INTEGER NOT NULL,
               session_id TEXT NOT NULL DEFAULT '',
               text TEXT NOT NULL)"""),
    3: ("""CREATE TABLE IF NOT EXISTS declarations (
               id INTEGER PRIMARY KEY,
               session_id TEXT NOT NULL,
               paths TEXT NOT NULL,
               symbols TEXT NOT NULL,
               task TEXT NOT NULL DEFAULT '',
               ts INTEGER NOT NULL,
               expires INTEGER NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS declarations_by_session ON declarations (session_id, expires)",
        """CREATE TABLE IF NOT EXISTS changes (
               id INTEGER PRIMARY KEY,
               ts INTEGER NOT NULL,
               kind TEXT NOT NULL,
               session_id TEXT NOT NULL DEFAULT '',
               finding_key TEXT NOT NULL DEFAULT '',
               ref INTEGER)""",
        # What the record already holds, in time order (a withdrawal has no time of its own: none)
        """INSERT INTO changes (ts, kind, session_id, finding_key, ref)
           SELECT ts, kind, session_id, finding_key, ref FROM (
               SELECT started AS ts, 'session_started' AS kind, id AS session_id, '' AS finding_key, NULL AS ref
                 FROM sessions
               UNION ALL SELECT last_seen, 'session_ended', id, '', NULL FROM sessions WHERE state = 'ended'
               UNION ALL SELECT first_seen, 'finding_opened', '', key, NULL FROM findings
               UNION ALL SELECT resolved, 'finding_resolved', '', key, NULL FROM findings WHERE resolved IS NOT NULL
               UNION ALL SELECT written, 'note_written', session_id, finding_key, id FROM notes WHERE written IS NOT NULL
               UNION ALL SELECT delivered, 'note_delivered', session_id, finding_key, id FROM notes
                 WHERE delivered IS NOT NULL)
           ORDER BY ts"""),
    4: ("""CREATE TABLE IF NOT EXISTS claims (
               id INTEGER PRIMARY KEY,
               repo_id INTEGER NOT NULL,
               session_id TEXT NOT NULL,
               kind TEXT NOT NULL,
               target TEXT NOT NULL,
               file TEXT NOT NULL DEFAULT '',
               hard INTEGER NOT NULL DEFAULT 0,
               ts INTEGER NOT NULL,
               expires INTEGER NOT NULL,
               ended INTEGER,
               why TEXT NOT NULL DEFAULT '')""",
        "CREATE INDEX IF NOT EXISTS claims_by_repo ON claims (repo_id, expires)"),
    5: ("""CREATE TABLE IF NOT EXISTS session_not_compared (
               session_id TEXT NOT NULL,
               path TEXT NOT NULL,
               why TEXT NOT NULL,
               PRIMARY KEY (session_id, path))""",),
}

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


def _finding_row(row):
    d = dict(row)
    d["sessions"] = json.loads(d["sessions"])
    d["detail"] = json.loads(d["detail"])
    return d


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
        try:
            self._open(path)
        except BaseException:
            self.db.close()                     # not held open: on Windows the file could not be moved aside
            raise

    def _open(self, path):
        """The writer's journal, and the tables brought to this schema."""
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.executescript(_TABLES)
        found = self.meta_get("schema")
        if found is None:
            self.meta_set("schema", 1)                  # _TABLES is schema 1; the steps below follow
        version = int(found or 1)
        if version > SCHEMA:
            raise SchemaError(f"the record at {path} was written by a newer Orchestraty (schema {found}; this version "
                              f"reads {SCHEMA}): upgrade Orchestraty, or move that file aside to start a new record")
        for step in range(version + 1, SCHEMA + 1):
            with self.transaction():
                for sql in _MIGRATIONS[step]:
                    self.db.execute(sql)
                self.meta_set("schema", step)

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

    @contextmanager
    def savepoint(self):
        """Inside a transaction: what the block writes is undone alone when it raises."""
        self.db.execute("SAVEPOINT one")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK TO one")
            self.db.execute("RELEASE one")
            raise
        self.db.execute("RELEASE one")

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

    def edits(self, since=0):
        """The files the agents edited from `since` (ns) on: every PostToolUse that names a file."""
        return [dict(r) for r in self.db.execute(
            "SELECT ts, session_id, cwd, file_path FROM events WHERE event = 'PostToolUse' AND file_path IS NOT NULL "
            "AND ts >= ? ORDER BY id", (since,))]

    def recent_events(self, session_id, limit=200):
        """A session's last `limit` events, latest first."""
        rows = self.db.execute("SELECT * FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                               (session_id, int(limit)))
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
        row = self.db.execute("SELECT state FROM sessions WHERE id = ?", (s["id"],)).fetchone()
        before, after = (row["state"] if row else None), s.get("state")
        if after == "live" and before != "live":
            self._log(s["started"] if row is None else s["last_seen"], "session_started", session_id=s["id"])
        elif before == "live" and after != "live":
            self._log(s["last_seen"], "session_ended", session_id=s["id"])
            self._end_claims_of(s["id"], s["last_seen"])                # a session's claims end with it
        self.db.execute("INSERT OR REPLACE INTO sessions (%s) VALUES (%s)" % (
            ", ".join(SESSION_COLUMNS), ", ".join("?" * len(SESSION_COLUMNS))),
            [s.get(c, "" if c not in ("repo_id", "base_guessed", "started", "last_seen") else None)
             for c in SESSION_COLUMNS])

    # what each session changed (a projection of the live map, replaced on every change)
    def put_changes(self, sid, files, unattributed, symbols, not_compared=None):
        """files: {path: status}; unattributed: [path]; symbols: [SymbolChange.as_dict()];
        not_compared: {path: why}."""
        self.db.execute("DELETE FROM session_files WHERE session_id = ?", (sid,))
        self.db.execute("DELETE FROM session_symbols WHERE session_id = ?", (sid,))
        self.db.execute("DELETE FROM session_not_compared WHERE session_id = ?", (sid,))
        self.db.executemany("INSERT INTO session_not_compared VALUES (?, ?, ?)",
                            [(sid, p, why) for p, why in sorted((not_compared or {}).items())])
        self.db.executemany("INSERT INTO session_files VALUES (?, ?, ?, ?)",
                            [(sid, p, s, 1) for p, s in files.items()] + [(sid, p, "?", 0) for p in unattributed])
        self.db.executemany("INSERT INTO session_symbols VALUES (?, ?, ?, ?, ?, ?)",
                            [(sid, s["symbol"], s["kind"], s["change"], s["contract"],
                              json.dumps({"reasons": s["reasons"], "file": s["file"], "lines": s["lines"],
                                          "moved_from": s["moved_from"], "language": s.get("language", "python"),
                                          "heuristic": bool(s.get("heuristic"))}, sort_keys=True)) for s in symbols])

    def changes(self, sid):
        """({path: status}, [unattributed paths], [symbol dicts]) for one session."""
        files, unattributed = {}, []
        for r in self.db.execute("SELECT * FROM session_files WHERE session_id = ? ORDER BY path", (sid,)):
            if r["attributed"]:
                files[r["path"]] = r["status"]
            else:
                unattributed.append(r["path"])
        symbols = []
        for r in self.db.execute("SELECT * FROM session_symbols WHERE session_id = ? ORDER BY symbol", (sid,)):
            d = json.loads(r["detail"])
            d.setdefault("language", "python")
            d.setdefault("heuristic", False)
            d.update(symbol=r["symbol"], kind=r["kind"], change=r["change"], contract=r["contract"])
            symbols.append(d)
        return files, unattributed, symbols

    def not_compared(self, sid):
        """{path: why} for one session's changed Python files that were not compared; {} before schema 5."""
        if not self.has_table("session_not_compared"):
            return {}
        return {r["path"]: r["why"] for r in self.db.execute(
            "SELECT path, why FROM session_not_compared WHERE session_id = ? ORDER BY path", (sid,))}

    # findings of the overlap rules
    def put_finding(self, repo_id, f, now):
        """Open a finding (or reopen it), or refresh one that is open. Returns True when it
        was not open before."""
        row = self.db.execute("SELECT state FROM findings WHERE key = ?", (f.key,)).fetchone()
        detail = json.dumps(f.detail, sort_keys=True)
        if row is None:
            self.db.execute("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, NULL)",
                            (f.key, repo_id, f.rule, f.strength, f.changer, f.receiver, json.dumps(list(f.sessions)),
                             f.symbol, f.file, detail, now, now))
            self._log(now, "finding_opened", finding_key=f.key)
            return True
        self.db.execute("UPDATE findings SET detail = ?, last_seen = ?, state = 'open', resolved = NULL WHERE key = ?",
                        (detail, now, f.key))
        if row["state"] != "open":
            self._log(now, "finding_opened", finding_key=f.key)
        return row["state"] != "open"

    def resolve_finding(self, key, now):
        cur = self.db.execute("UPDATE findings SET state = 'resolved', resolved = ? WHERE key = ? AND state = 'open'",
                              (now, key))
        if cur.rowcount:
            self._log(now, "finding_resolved", finding_key=key)

    def findings(self, repo_id=None, states=("open",)):
        sql, args, where = "SELECT * FROM findings", [], []
        if repo_id is not None:
            where.append("repo_id = ?")
            args.append(repo_id)
        if states:
            where.append("state IN (%s)" % ", ".join("?" * len(states)))
            args.extend(states)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [_finding_row(r) for r in self.db.execute(sql + " ORDER BY key", args)]

    def finding(self, key):
        """One finding by its key, in whatever state, or None."""
        row = self.db.execute("SELECT * FROM findings WHERE key = ?", (key,)).fetchone()
        return _finding_row(row) if row else None

    # notes: pending (made), written (in a slot), delivered (taken by a hook) or withdrawn
    def add_note(self, finding_key, session_id, kind, slot, text, now, cause_ts=None):
        """Make a note unless this finding already has one of this kind for this session that
        is waiting or was delivered. A note withdrawn before its agent saw it is made again.
        Returns True when a note was made."""
        cur = self.db.execute(
            "INSERT INTO notes (finding_key, session_id, kind, slot, text, state, created, cause_ts) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?) "
            "ON CONFLICT (finding_key, session_id, kind) DO UPDATE SET state = 'pending', slot = excluded.slot, "
            "text = excluded.text, created = excluded.created, cause_ts = excluded.cause_ts, written = NULL, "
            "delivered = NULL WHERE notes.state = 'withdrawn'",
            (finding_key, session_id, kind, slot, text, now, cause_ts))
        return cur.rowcount == 1

    def note(self, note_id):
        row = self.db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return dict(row) if row else None

    def notes(self, finding_key=None, slot=None, session_id=None, states=None):
        sql, args, where = "SELECT * FROM notes", [], []
        for column, value in (("finding_key", finding_key), ("slot", slot), ("session_id", session_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if states:
            where.append("state IN (%s)" % ", ".join("?" * len(states)))
            args.extend(states)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id", args)]

    def slots_with(self, states):
        return [r[0] for r in self.db.execute(
            "SELECT DISTINCT slot FROM notes WHERE state IN (%s) ORDER BY slot" % ", ".join("?" * len(states)),
            list(states))]

    def set_note_state(self, note_id, state, ts=None):
        """Delivered or withdrawn at `ts` (now if not given), as the change log records it."""
        row = self.db.execute("SELECT state FROM notes WHERE id = ?", (note_id,)).fetchone()
        self.db.execute("UPDATE notes SET state = ? WHERE id = ?", (state, note_id))
        if row is not None and row["state"] != state and state in ("delivered", "withdrawn"):
            self._log_note(time.time_ns() if ts is None else ts, "note_" + state, note_id)

    def mark_written(self, note_ids, now):
        for note_id in note_ids:
            cur = self.db.execute("UPDATE notes SET state = 'written', written = ? WHERE id = ? AND state = 'pending'",
                                  (now, note_id))
            if cur.rowcount:
                self._log_note(now, "note_written", note_id)

    def mark_delivered(self, slot, now):
        taken = [r[0] for r in self.db.execute("SELECT id FROM notes WHERE slot = ? AND state = 'written' ORDER BY id",
                                               (slot,))]
        self.db.execute("UPDATE notes SET state = 'delivered', delivered = ? WHERE slot = ? AND state = 'written'",
                        (now, slot))
        for note_id in taken:
            self._log_note(now, "note_delivered", note_id)

    def unwrite_slot(self, slot):
        """A withdrawn slot: its other written notes wait for the next write."""
        self.db.execute("UPDATE notes SET state = 'pending', written = NULL WHERE slot = ? AND state = 'written'",
                        (slot,))

    def set_note_text(self, note_id, text):
        """A note not yet written, with its finding's text as it is now."""
        self.db.execute("UPDATE notes SET text = ? WHERE id = ? AND state = 'pending'", (text, note_id))

    # the user's word on the findings (schema 2): kept here only, never put into a note
    def has_table(self, name):
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None

    def add_label(self, finding_key, verdict, why, ts):
        self.db.execute("INSERT INTO labels (finding_key, verdict, why, ts) VALUES (?, ?, ?, ?)",
                        (finding_key, verdict, why, ts))

    def labels(self):
        """Every label, oldest first; a finding's label is its latest. None before schema 2."""
        if not self.has_table("labels"):
            return []
        return [dict(r) for r in self.db.execute("SELECT * FROM labels ORDER BY ts, id")]

    def add_missed(self, ts, session_id, text):
        self.db.execute("INSERT INTO missed (ts, session_id, text) VALUES (?, ?, ?)", (ts, session_id, text))

    def missed(self):
        """Every report of a note that should have come, oldest first. None before schema 2."""
        if not self.has_table("missed"):
            return []
        return [dict(r) for r in self.db.execute("SELECT * FROM missed ORDER BY ts, id")]

    # what agents say they are about to change (schema 3)
    def add_declaration(self, session_id, paths, symbols, task, ts, expires):
        cur = self.db.execute("INSERT INTO declarations (session_id, paths, symbols, task, ts, expires) "
                              "VALUES (?, ?, ?, ?, ?, ?)",
                              (session_id, json.dumps(list(paths)), json.dumps(list(symbols)), task, ts, expires))
        self._log(ts, "declared", session_id=session_id, ref=cur.lastrowid)

    def declarations(self, active_at=None):
        """Declarations, oldest first; with active_at (ns), only those that hold then. None
        before schema 3."""
        if not self.has_table("declarations"):
            return []
        sql, args = "SELECT * FROM declarations", []
        if active_at is not None:
            sql, args = sql + " WHERE ts <= ? AND expires > ?", [active_at, active_at]
        out = []
        for r in self.db.execute(sql + " ORDER BY ts, id", args):
            d = dict(r)
            d["paths"], d["symbols"] = json.loads(d["paths"]), json.loads(d["symbols"])
            out.append(d)
        return out

    def declarations_ended(self, after, until):
        """The declarations that stopped holding after `after` and by `until` (ns)."""
        if not self.has_table("declarations"):
            return []
        return [dict(r) for r in self.db.execute("SELECT id, session_id, expires FROM declarations "
                                                 "WHERE expires > ? AND expires <= ? ORDER BY expires, id",
                                                 (after, until))]

    def leave_project(self, session_id, ts):
        """Expire project-relative declarations and end claims when a session switches repositories."""
        self.db.execute("UPDATE declarations SET expires = MAX(ts, ?) WHERE session_id = ? AND expires > ?",
                        (ts, session_id, ts))
        self._end_claims_of(session_id, ts, why="session changed project")

    # what sessions have reserved (schema 4)
    def add_claim(self, repo_id, session_id, kind, target, file, hard, ts, expires):
        """Store a claim; returns its id."""
        cur = self.db.execute("INSERT INTO claims (repo_id, session_id, kind, target, file, hard, ts, expires) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                              (repo_id, session_id, kind, target, file, int(bool(hard)), ts, expires))
        self._log(ts, "claimed", session_id=session_id, ref=cur.lastrowid)
        return cur.lastrowid

    def end_claim(self, claim_id, ts, why):
        """End a claim that still holds at `ts` (a release). True when it did hold."""
        row = self.db.execute("SELECT session_id FROM claims WHERE id = ? AND ended IS NULL AND expires > ?",
                              (claim_id, ts)).fetchone()
        if row is None:
            return False
        self.db.execute("UPDATE claims SET ended = ?, why = ? WHERE id = ?", (ts, why, claim_id))
        self._log(ts, "claim_ended", session_id=row["session_id"], ref=claim_id)
        return True

    def _end_claims_of(self, session_id, ts, why="session ended"):
        """End the claims of a session that ended at `ts`: those that had not expired by then,
        each at `ts` or, for one made after the session's last event, when it was made."""
        if not self.has_table("claims"):
            return
        rows = self.db.execute("SELECT id, ts FROM claims WHERE session_id = ? AND ended IS NULL AND expires > ? "
                               "ORDER BY id", (session_id, ts)).fetchall()
        for row in rows:
            when = max(ts, row["ts"])
            self.db.execute("UPDATE claims SET ended = ?, why = ? WHERE id = ?", (when, why, row["id"]))
            self._log(when, "claim_ended", session_id=session_id, ref=row["id"])

    def expire_claims(self, ts):
        """End the claims whose time ran out by `ts`, each at the time it ran out. Returns them."""
        if not self.has_table("claims"):
            return []
        rows = [dict(r) for r in self.db.execute("SELECT * FROM claims WHERE ended IS NULL AND expires <= ? "
                                                 "ORDER BY expires, id", (ts,))]
        for c in rows:
            c["ended"], c["why"] = c["expires"], "expired"
            self.db.execute("UPDATE claims SET ended = ?, why = ? WHERE id = ?", (c["ended"], c["why"], c["id"]))
            self._log(c["ended"], "claim_ended", session_id=c["session_id"], ref=c["id"])
        return rows

    def claim(self, claim_id):
        if not self.has_table("claims"):
            return None
        row = self.db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        return dict(row) if row else None

    def claims(self, active_at=None, repo_id=None):
        """Claims, oldest first; with active_at (ns), only those that hold then: made by then,
        not ended, not expired. None before schema 4."""
        if not self.has_table("claims"):
            return []
        sql, args, where = "SELECT * FROM claims", [], []
        if active_at is not None:
            where.append("ts <= ? AND expires > ? AND (ended IS NULL OR ended > ?)")
            args += [active_at, active_at, active_at]
        if repo_id is not None:
            where.append("repo_id = ?")
            args.append(repo_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY ts, id", args)]

    # the change log (schema 3)
    def _log(self, ts, kind, session_id="", finding_key="", ref=None):
        self.db.execute("INSERT INTO changes (ts, kind, session_id, finding_key, ref) VALUES (?, ?, ?, ?, ?)",
                        (ts, kind, session_id or "", finding_key or "", ref))

    def _log_note(self, ts, kind, note_id):
        row = self.db.execute("SELECT session_id, finding_key FROM notes WHERE id = ?", (note_id,)).fetchone()
        self._log(ts, kind, session_id=row["session_id"], finding_key=row["finding_key"], ref=note_id)

    def change_log(self, after_id=0, since=None, limit=None):
        """The change log after the change `after_id`, from `since` (ns) on, in the order the
        changes happened. None before schema 3."""
        if not self.has_table("changes"):
            return []
        sql, args = "SELECT * FROM changes WHERE id > ?", [after_id]
        if since is not None:
            sql, args = sql + " AND ts >= ?", args + [since]
        sql += " ORDER BY id"
        if limit is not None:
            sql, args = sql + " LIMIT ?", args + [limit]
        return [dict(r) for r in self.db.execute(sql, args)]

    def last_change(self):
        """The id of the latest change in the log, 0 for none."""
        if not self.has_table("changes"):
            return 0
        return self.db.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0]

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
        return [dict(r) for r in self.db.execute(sql + " ORDER BY started, id", args)]
