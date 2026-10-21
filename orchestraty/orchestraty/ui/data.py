"""What the dashboard shows, read from the state folder without changing it.

Each function takes a ``State`` and returns a dict ready for JSON, with a ``schema`` number.
The record is opened read-only (``Record(path, readonly=True)``) for each call, and only when
it exists, since opening a missing one would create it: the coordinator stays its only
writer. The record keeps times in nanoseconds since the epoch; they are given here in ISO
8601, in UTC, to the millisecond. A note's time to awareness is ``written - cause_ts``: from
the event that caused it to the note being ready in its slot for the agent.
"""
import json
import os
import statistics
import time
from contextlib import contextmanager

from orchestraty import __version__, config, status
from orchestraty.coordinator.record import Record, repo_name
from orchestraty.delivery import slots

SCHEMA = 1
NS = 1_000_000_000
EVENT_LIMIT = 200
NOTE_LIMIT = 1000                  # notes listed at most, newest first; the summary counts them all
NOTE_KINDS = ("edit", "next", "stop", "wake")
NOTE_STATES = ("pending", "written", "delivered", "withdrawn")
FINDING_STATES = {"open": ("open",), "resolved": ("resolved",), "all": None}
MAX_PUSH_LINE = 2000               # bytes, as much as the hook script prints at push
_STRENGTH = {"strong": 0, "medium": 1, "weak": 2}


@contextmanager
def _record(state):
    """The record, read-only, or None while there is none."""
    if not os.path.isfile(state.record):
        yield None
        return
    record = Record(state.record, readonly=True)
    try:
        yield record
    finally:
        record.close()


def iso(ns):
    """A time in nanoseconds as ISO 8601 in UTC, to the millisecond; None when not known."""
    if ns is None:
        return None
    seconds, rest = divmod(int(ns), NS)
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + ".%03dZ" % (rest // 1_000_000)
    except (OSError, OverflowError, ValueError):
        return None


def awareness_ms(note):
    """A note's time to awareness in milliseconds, or None when it was never written."""
    if note.get("written") is None or note.get("cause_ts") is None:
        return None
    return round((note["written"] - note["cause_ts"]) / 1e6, 1)


def _shell(path):
    """A path as it is typed in this system's usual shell: as it is when it is plain, else
    in single quotes, which PowerShell and POSIX shells both take literally."""
    safe = "_-./:\\" if os.name == "nt" else "_-./:"
    if path and all((c.isascii() and c.isalnum()) or c in safe for c in path):
        return path
    if os.name == "nt":
        return "'" + path.replace("'", "''") + "'"
    return "'" + path.replace("'", "'\\''") + "'"


def _order(f):
    return (_STRENGTH.get(f["strength"], 9), f["rule"], f["key"])


def _who(s):
    """How the page names a session, and where it stands."""
    return {"id": s["id"], "agent": s["agent"], "worktree": s["worktree"], "branch": s["branch"],
            "state": s["state"], "turn": s["turn"], "note": s["note"]}


def _facts(s, repo, now):
    """A session's row, with the fields orch status --json gives a session."""
    return {"id": s["id"], "agent": s["agent"], "worktree": s["worktree"], "cwd": s["cwd"], "branch": s["branch"],
            "base_commit": s["base_commit"], "base_guessed": bool(s["base_guessed"]), "state": s["state"],
            "turn": s["turn"], "note": s["note"], "started": iso(s["started"]), "last_event": iso(s["last_seen"]),
            "seconds_since_last_event": max(0, int(now - s["last_seen"] / NS)),
            "repository": {"name": (repo or {}).get("name", ""), "common_dir": (repo or {}).get("common_dir", "")}}


def _heard_with(notes):
    """{(finding key, session id): kind} of the note through which a session heard a finding:
    the one a hook took, which has a delivery time. The finding's other notes for that session
    then count as delivered too, with no time of their own."""
    out = {}
    for n in notes:
        if n["state"] == "delivered" and n["delivered"] is not None:
            out.setdefault((n["finding_key"], n["session_id"]), n["kind"])
    return out


def _note(n, findings, heard):
    f = findings.get(n["finding_key"]) or {}
    out = {"id": n["id"], "finding_key": n["finding_key"], "rule": f.get("rule") or n["finding_key"].split("|")[0],
           "strength": f.get("strength", ""), "symbol": f.get("symbol", ""), "file": f.get("file", ""),
           "session_id": n["session_id"], "kind": n["kind"], "state": n["state"], "slot": n["slot"],
           "text": n["text"], "created": iso(n["created"]), "cause": iso(n["cause_ts"]), "written": iso(n["written"]),
           "delivered": iso(n["delivered"]), "time_to_awareness_ms": awareness_ms(n), "heard_with": None}
    if n["state"] == "delivered" and n["delivered"] is None:
        out["heard_with"] = heard.get((n["finding_key"], n["session_id"]))
    return out


def _finding(f, repos, notes, findings, heard):
    repo = repos.get(f["repo_id"]) or {}
    return {"key": f["key"], "rule": f["rule"], "strength": f["strength"], "symbol": f["symbol"], "file": f["file"],
            "changer": f["changer"], "receiver": f["receiver"], "sessions": f["sessions"], "detail": f["detail"],
            "latent": bool(f["detail"].get("latent")), "state": f["state"], "first_seen": iso(f["first_seen"]),
            "last_seen": iso(f["last_seen"]), "resolved": iso(f["resolved"]),
            "repository": {"name": repo.get("name", ""), "common_dir": repo.get("common_dir", "")},
            "notes": [_note(n, findings, heard) for n in notes]}


def _caused_by(f, session_id):
    """Whether the notes of finding `f` come from that session's changes."""
    if f is None:
        return False
    if f["rule"] in ("O3", "O4", "O6"):
        return f["changer"] == session_id
    return session_id in f["sessions"]                 # O1, O2: the changes of both sessions


def _event(e):
    try:
        detail = json.loads(e["detail"]) if e["detail"] else {}
    except ValueError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    return {"id": e["id"], "ts": iso(e["ts"]), "event": e["event"], "tool_name": e["tool_name"],
            "file_path": e["file_path"], "took": detail.get("took"), "source": detail.get("source"),
            "reason": detail.get("reason")}


def _main_worktree(common_dir):
    path = os.path.normpath(common_dir)
    return os.path.dirname(path) if os.path.basename(path).lower() == ".git" else path


# --------------------------------------------------------------------------------------- #
# The payloads
# --------------------------------------------------------------------------------------- #
def status_payload(state, include_ended=False, demo=False):
    """orch status --json as it is, plus what the page needs to know about the server."""
    data = status.collect(state, include_ended=include_ended)
    data["ui"] = {"version": __version__, "demo": bool(demo)}
    return data


def session_payload(state, session_id, now=None):
    """One session: its row and changes, the notes it received, the notes its changes caused
    for other sessions, its last events (latest first) and its worktree's push line. None when
    the record has no such session."""
    now = time.time() if now is None else now
    with _record(state) as record:
        s = record.session(session_id) if record else None
        if s is None:
            return None
        files, unattributed, symbols = record.changes(session_id)
        notes = record.notes()
        findings = {f["key"]: f for f in record.findings(states=None)}
        events = record.recent_events(session_id, EVENT_LIMIT)
        repos = {r["id"]: r for r in record.repos()}
        everyone = record.sessions()
    heard = _heard_with(notes)
    received = [n for n in notes if n["session_id"] == session_id]
    caused = [n for n in notes if n["session_id"] != session_id and _caused_by(findings.get(n["finding_key"]), session_id)]
    changers = {f["changer"] for f in findings.values() if f["rule"] == "O6" and f["state"] == "open"}
    key = slots.file_key(s["worktree"]) + ".txt" if s["worktree"] else None
    push = [p for p in push_lines(state, everyone, changers) if p["name"] == key]
    return {"schema": SCHEMA, "session": _facts(s, repos.get(s["repo_id"]), now),
            "changes": {"files": files, "unattributed": unattributed, "symbols": symbols},
            "notes_received": [_note(n, findings, heard) for n in reversed(received)],
            "notes_caused": [_note(n, findings, heard) for n in reversed(caused)],
            "events": [_event(e) for e in events], "push": push[0] if push else None,
            "sessions": {x["id"]: _who(x) for x in everyone if x["repo_id"] == s["repo_id"]}}


def findings_payload(state, which="open"):
    """The findings in one state ("open", "resolved" or "all"), strongest first, each with its
    detail and the notes made for it."""
    with _record(state) as record:
        if record is None:
            return {"schema": SCHEMA, "state": which, "findings": [], "sessions": {}}
        found = record.findings(states=FINDING_STATES[which])
        notes = record.notes()
        repos = {r["id"]: r for r in record.repos()}
        sessions = record.sessions()
    heard = _heard_with(notes)
    by_key = {}
    for n in notes:
        by_key.setdefault(n["finding_key"], []).append(n)
    known = {f["key"]: f for f in found}
    return {"schema": SCHEMA, "state": which,
            "findings": [_finding(f, repos, by_key.get(f["key"], []), known, heard) for f in sorted(found, key=_order)],
            "sessions": {s["id"]: _who(s) for s in sessions}}


def finding_payload(state, key):
    """One finding with its notes and the sessions it names, or None."""
    with _record(state) as record:
        f = record.finding(key) if record else None
        if f is None:
            return None
        notes = record.notes(finding_key=key)
        repos = {r["id"]: r for r in record.repos()}
        named = set(f["sessions"]) | {f["changer"], f["receiver"]} | {n["session_id"] for n in notes}
        sessions = {s["id"]: _who(s) for s in record.sessions() if s["id"] in named}
    return {"schema": SCHEMA, "finding": _finding(f, repos, notes, {key: f}, _heard_with(notes)),
            "sessions": sessions}


def notes_payload(state, session=None, kind=None, note_state=None, limit=NOTE_LIMIT):
    """Every note, newest first, filtered by receiving session, kind and state, with the median
    time to awareness of the delivered ones."""
    notes, findings, sessions = [], {}, []
    with _record(state) as record:
        if record is not None:
            notes = record.notes()
            findings = {f["key"]: f for f in record.findings(states=None)}
            sessions = record.sessions()
    heard = _heard_with(notes)
    picked = [n for n in notes if (session is None or n["session_id"] == session)
              and (kind is None or n["kind"] == kind) and (note_state is None or n["state"] == note_state)]
    delivered = [n for n in picked if n["state"] == "delivered"]
    measured = [ms for ms in (awareness_ms(n) for n in delivered) if ms is not None]
    picked.reverse()
    return {"schema": SCHEMA, "filters": {"session": session, "kind": kind, "state": note_state},
            "notes": [_note(n, findings, heard) for n in picked[:limit]], "total": len(picked),
            "summary": {"delivered": len(delivered), "measured": len(measured),
                        "median_ms": round(statistics.median(measured), 1) if measured else None},
            "sessions": {s["id"]: _who(s) for s in sessions}, "kinds": list(NOTE_KINDS), "states": list(NOTE_STATES)}


def push_lines(state, sessions, changers=()):
    """[{name, line, worktree, session, sessions, modified}] for each <state>/push/<worktree
    key>.txt. A line belongs to the worktree whose key names its file; its session is the live
    one there whose change it is, else the latest one there."""
    folder = os.path.join(state.root, "push")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".txt"))
    except OSError:
        return []
    owners = {}
    for s in sessions:
        if s["worktree"]:
            owners.setdefault(slots.file_key(s["worktree"]) + ".txt", []).append(s)
    out = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            with open(path, "rb") as fh:
                line = fh.read(MAX_PUSH_LINE).decode("utf-8", "replace").strip()
            modified = os.stat(path).st_mtime_ns
        except OSError:
            continue                                   # removed meanwhile
        here = sorted(owners.get(name, []),
                      key=lambda s: (s["state"] != "live", s["id"] not in changers, -s["last_seen"], s["id"]))
        out.append({"name": name, "line": line, "worktree": here[0]["worktree"] if here else None,
                    "session": here[0]["id"] if here else None, "sessions": [s["id"] for s in here],
                    "modified": iso(modified)})
    return out


def push_payload(state):
    """The push lines waiting in <state>/push/, with the worktree and session of each."""
    sessions, changers = [], set()
    with _record(state) as record:
        if record is not None:
            sessions = record.sessions()
            changers = {f["changer"] for f in record.findings() if f["rule"] == "O6"}
    return {"schema": SCHEMA, "push": push_lines(state, sessions, changers)}


def health_payload(state, demo=False):
    """Whether the coordinator is running (with its pid, version and start, as orch status
    reads them), whether the record exists, and where the state folder is."""
    return {"schema": SCHEMA, "coordinator": status.collect(state)["coordinator"],
            "record": {"exists": os.path.isfile(state.record), "path": state.record}, "state": state.root,
            "ui": {"version": __version__, "demo": bool(demo)}}


def config_payload(state):
    """The steer and wake switches, the watched repositories with the commands that watch and
    unwatch each, and the state folder."""
    cfg = config.load(state)
    repos = []
    for common_dir in cfg["repos"]:
        folder = _main_worktree(common_dir)
        if not os.path.isdir(folder):
            folder = common_dir
        repos.append({"name": repo_name(common_dir), "common_dir": common_dir, "folder": folder,
                      "watch": "orch watch " + _shell(folder), "unwatch": "orch unwatch " + _shell(folder)})
    return {"schema": SCHEMA, "steer": cfg["steer"], "wake": cfg["wake"], "repos": repos, "state": state.root,
            "settings": cfg["settings"], "commands": {"init": "orch init", "restore": "orch restore"}}
