"""Reads Claude Code session logs for the overlap history: for each session, where it ran, when
it was active, and which files it edited. Nothing else.

A session log (one JSON object per line, under ``~/.claude/projects/<folder>/``) also holds the
prompts, the agent's replies and the contents of files it read and wrote. None of that is
decoded or kept. Each line is walked token by token, and only the values at the places listed
in ``WANTED`` are decoded; every other string is stepped over as raw bytes. A line this reader
cannot follow is dropped.

Kept per session: its id, the folder it started in, the times of its events, and for each
Edit, Write, MultiEdit and NotebookEdit whose result was not an error: the file's path, the
time, and the line ranges its patch covers in the file after the edit. Logs of subagents
(``<session>/subagents/...``) count for the session that started them.
"""
import json
import os
import re
import time
from datetime import datetime

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
IDLE = 30 * 60                 # a gap longer than this between two events ends an active stretch

# The only places whose values are decoded; "*" stands for any position in a list.
WANTED = frozenset([
    ("type",), ("sessionId",), ("cwd",), ("timestamp",),
    ("message", "content", "*", "type"),
    ("message", "content", "*", "id"),
    ("message", "content", "*", "name"),
    ("message", "content", "*", "input", "file_path"),
    ("message", "content", "*", "input", "notebook_path"),
    ("message", "content", "*", "tool_use_id"),
    ("message", "content", "*", "is_error"),
    ("toolUseResult", "filePath"),
    ("toolUseResult", "structuredPatch", "*", "newStart"),
    ("toolUseResult", "structuredPatch", "*", "newLines"),
])

_TOKEN = re.compile(
    rb'[ \t\r\n]*(?:'
    rb'"([^"\\]*(?:\\.[^"\\]*)*)"'           # 1: a string's body, still escaped
    rb'|([{}\[\],:])'                         # 2: structure
    rb'|(-?[0-9][0-9.eE+-]*)'                # 3: a number
    rb'|(true|false|null)'                   # 4: a literal
    rb')', re.S)
_LITERALS = {b"true": True, b"false": False, b"null": None}


class Unreadable(ValueError):
    """A line that is not JSON this reader can follow."""


def _text(body):
    """A string token's value, unescaped (only ever called for keys and wanted values)."""
    if b"\\" not in body:
        return body.decode("utf-8", "replace")
    return json.loads(b'"' + body + b'"')


def select(line, wanted=WANTED):
    """[(path, value)] for the scalar values in one JSON line whose path is in `wanted`. A path
    is a tuple of keys and list positions; in `wanted`, "*" stands for any position. Strings
    anywhere else are never decoded."""
    out = []
    path = []                     # keys and list positions down to the current place
    kinds = []                    # b"{" or b"[" for each open container
    want_key = False
    pos, end = 0, len(line)
    while pos < end:
        m = _TOKEN.match(line, pos)
        if m is None:
            if line[pos:].strip():
                raise Unreadable("unexpected byte at %d" % pos)
            break
        pos = m.end()
        body, punct, number, literal = m.groups()
        if punct is not None:
            if punct == b"{":
                kinds.append(punct)
                path.append(None)
                want_key = True
            elif punct == b"[":
                kinds.append(punct)
                path.append(0)
                want_key = False
            elif punct in b"}]":
                if not kinds:
                    raise Unreadable("unbalanced")
                kinds.pop()
                path.pop()
                want_key = False                 # a value just ended; a key follows only a comma
            elif punct == b",":
                if kinds and kinds[-1] == b"{":
                    want_key = True
                elif kinds:
                    path[-1] += 1
            continue
        if want_key:
            if body is None:
                raise Unreadable("a key that is not a string")
            path[-1] = _text(body)
            want_key = False
            continue
        if tuple("*" if isinstance(p, int) else p for p in path) in wanted:
            if body is not None:
                value = _text(body)
            elif number is not None:
                value = float(number) if any(c in number for c in b".eE") else int(number)
            else:
                value = _LITERALS[literal]
            out.append((tuple(path), value))
    if kinds:
        raise Unreadable("truncated")
    return out


def parse_time(text):
    """Seconds since the epoch for a log timestamp such as 2026-10-20T09:08:42.123Z, or None."""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


class Edit:
    __slots__ = ("time", "path", "tool", "lines")

    def __init__(self, time_, path, tool, lines):
        self.time, self.path, self.tool, self.lines = time_, path, tool, lines

    def __repr__(self):
        return "Edit(%r, %r, %r, %r)" % (self.time, self.path, self.tool, self.lines)


class Session:
    """One session: `stretches` are its active periods [(start, end)], split where it was idle
    for more than IDLE seconds; `edits` are its successful file edits in time order."""

    def __init__(self, sid):
        self.id = sid
        self.cwd = ""
        self.times = []
        self.edits = []
        self.logs = []

    @property
    def start(self):
        return self.times[0] if self.times else None

    @property
    def end(self):
        return self.times[-1] if self.times else None

    @property
    def stretches(self):
        out = []
        for t in self.times:
            if out and t - out[-1][1] <= IDLE:
                out[-1][1] = t
            else:
                out.append([t, t])
        return [tuple(s) for s in out]

    def __repr__(self):
        return "Session(%r, %r, %d events, %d edits)" % (self.id, self.cwd, len(self.times), len(self.edits))


def _session_id(projects, path):
    """A log's session: its own name, or for a subagent's log, the session folder above it."""
    rel = os.path.relpath(path, projects).replace(os.sep, "/").split("/")
    if len(rel) >= 3:
        return rel[1]
    return os.path.splitext(rel[-1])[0]


def read_log(path, session, skipped):
    """Adds one log file's events and edits to `session`. Unreadable lines are counted in
    skipped["lines"]."""
    calls = {}                    # tool_use id -> (time, tool, path)
    results = {}                  # tool_use id -> (is_error, [(start, count)])
    with open(path, "rb") as fh:
        for raw in fh:
            if b'"timestamp"' not in raw and b'"cwd"' not in raw:
                continue          # metadata lines (titles, modes, last prompt): nothing we keep
            try:
                found = select(raw)
            except (Unreadable, ValueError):
                skipped["lines"] += 1
                continue
            top = {p[0]: v for p, v in found if len(p) == 1}
            when = parse_time(top.get("timestamp")) if isinstance(top.get("timestamp"), str) else None
            if when is not None:
                session.times.append(when)
            if not session.cwd and isinstance(top.get("cwd"), str):
                session.cwd = top["cwd"]
            blocks, hunks = {}, {}
            for p, v in found:
                if len(p) >= 4 and p[0] == "message" and p[1] == "content":
                    blocks.setdefault(p[2], {})[p[3] if len(p) == 4 else p[4]] = v
                elif p[0] == "toolUseResult" and len(p) == 4 and p[1] == "structuredPatch":
                    hunks.setdefault(p[2], {})[p[3]] = v
            ranges = tuple((h["newStart"], h["newLines"]) for _, h in sorted(hunks.items())
                           if isinstance(h.get("newStart"), int) and isinstance(h.get("newLines"), int))
            answered = [b for b in blocks.values() if b.get("type") == "tool_result"]
            for b in blocks.values():
                if b.get("type") == "tool_use" and b.get("name") in EDIT_TOOLS and isinstance(b.get("id"), str):
                    target = b.get("file_path") or b.get("notebook_path")
                    if isinstance(target, str) and target:
                        calls[b["id"]] = (when, b["name"], target)
                elif b.get("type") == "tool_result" and isinstance(b.get("tool_use_id"), str):
                    # the patch belongs to the line's one result; with several, its owner is unclear
                    results[b["tool_use_id"]] = (b.get("is_error") is True, ranges if len(answered) == 1 else ())
    for use_id, (when, tool, target) in calls.items():
        result = results.get(use_id)
        if result is None or result[0] or when is None:
            continue              # no result (cut off), or it failed: the file was not changed
        session.edits.append(Edit(when, target, tool, result[1]))
    session.logs.append(path)


def find_logs(projects, since):
    """The session logs under `projects` written to at or after `since` (epoch seconds)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(projects):
        dirnames[:] = sorted(d for d in dirnames if d != "memory")
        for name in sorted(filenames):
            if name.endswith(".jsonl"):
                path = os.path.join(dirpath, name)
                try:
                    if os.stat(path).st_mtime >= since:
                        out.append(path)
                except OSError:
                    continue
    return out


def read_sessions(projects=None, days=30, now=None):
    """({session id: Session}, {"lines": unreadable lines, "logs": logs read}) for the sessions
    active in the last `days` days. Sessions with no events in that window are left out."""
    projects = projects or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    now = time.time() if now is None else now
    since = now - days * 86400
    sessions, skipped = {}, {"lines": 0, "logs": 0}
    for path in find_logs(projects, since):
        sid = _session_id(projects, path)
        s = sessions.get(sid)
        if s is None:
            s = sessions[sid] = Session(sid)
        try:
            read_log(path, s, skipped)
        except OSError:
            skipped["logs"] += 1
    out = {}
    for sid, s in sessions.items():
        s.times = sorted(t for t in s.times if since <= t <= now)
        s.edits = sorted((e for e in s.edits if since <= e.time <= now), key=lambda e: e.time)
        if s.times:
            out[sid] = s
    return out, skipped
