"""The user's word on the findings, so that daily use can be measured.

``orch findings`` lists the findings, each with a short number that holds for 24 hours (kept in
``<state>/finding-numbers.json``, never in the record). ``orch label`` gives a finding a verdict,
real, harmless or false, with the user's reason if they give one; ``orch missed`` reports a note
that should have come. Neither writes the record: each appends one line to orch's own spool,
``<state>/spool/orch/<day>.jsonl``, in the hooks' line format, and the coordinator, the record's
only writer, stores it (``parse_line``). The user's words stay in the record on this machine and
are never put into a note.
"""
import calendar
import json
import os
import time

from orchestraty import status
from orchestraty.capture.hook import append
from orchestraty.coordinator.lock import is_fresh
from orchestraty.coordinator.record import Record
from orchestraty.delivery.notes import printable

SPOOL_AGENT = "orch"                 # the spool folder these lines go to, beside the agents' own
VERDICTS = ("real", "harmless", "false")
FINDINGS_SCHEMA = 1
NUMBERS_SECONDS = 24 * 3600
MAX_KEY = 2000
MAX_WHY = 500
MAX_TEXT = 2000
MAX_SESSION = 200
NS = 1_000_000_000
DAY = 86400


class Refused(Exception):
    """What the user asked for cannot be done; the message says why."""


def _iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _local(iso):
    return time.strftime("%m-%d %H:%M", time.localtime(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))))


def coordinator_running(state, now=None):
    return is_fresh(state.alive, status.STALE_SECONDS, time.time() if now is None else now)


# ------------------------------------------------------------------------------- the spool
def append_line(state, event, now, **fields):
    """One line in orch's spool, as the hooks write theirs: one JSON object, ASCII, appended whole."""
    line = {"ts": int(now * NS), "pid": os.getpid(), "agent": SPOOL_AGENT, "event": event}
    line.update(fields)
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    append(os.path.join(state.spool, SPOOL_AGENT, day + ".jsonl"),
           json.dumps(line, separators=(",", ":")).encode("ascii") + b"\n")


def label(state, key, verdict, why="", now=None):
    """Append the user's verdict on one finding, for the coordinator to store."""
    if verdict not in VERDICTS:
        raise Refused(f"the verdict is one of {', '.join(VERDICTS)}")
    append_line(state, "label", time.time() if now is None else now, finding=key, verdict=verdict,
            why=(why or "").strip()[:MAX_WHY])


def missed(state, text, session_id="", now=None):
    """Append a report of a note that should have come, for the coordinator to store."""
    text = (text or "").strip()
    if not text:
        raise Refused("say what should have been noted")
    append_line(state, "missed", time.time() if now is None else now, text=text[:MAX_TEXT],
            session_id=(session_id or "")[:MAX_SESSION])


def parse_line(line):
    """The label or missed report one line of orch's spool carries, checked and cut to size, or
    None for a line of any other shape, whoever wrote it."""
    event = line.get("event")
    if event == "label":
        key, verdict, why = line.get("finding"), line.get("verdict"), line.get("why", "")
        if not (isinstance(key, str) and 0 < len(key) <= MAX_KEY) or verdict not in VERDICTS or not isinstance(why, str):
            return None
        return {"event": "label", "finding": key, "verdict": verdict, "why": why[:MAX_WHY]}
    if event == "missed":
        text, sid = line.get("text"), line.get("session_id", "")
        if not (isinstance(text, str) and text.strip()) or not isinstance(sid, str):
            return None
        return {"event": "missed", "text": text.strip()[:MAX_TEXT], "session_id": sid[:MAX_SESSION]}
    return None


# ------------------------------------------------------------------------------- short numbers
def _numbers(state, now):
    """The numbers given in the last 24 hours: [{"n", "key", "at"}]."""
    try:
        with open(state.numbers, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    entries = data.get("numbers") if isinstance(data, dict) else None
    return [e for e in entries or [] if isinstance(e, dict) and isinstance(e.get("n"), int)
            and isinstance(e.get("key"), str) and isinstance(e.get("at"), (int, float))
            and now - NUMBERS_SECONDS < e["at"] <= now + 60]


def number(state, keys, now=None):
    """{key: short number} for these findings. A number given in the last 24 hours stays the
    finding's; a new one comes after the highest still held."""
    now = time.time() if now is None else now
    entries = _numbers(state, now)
    held = {e["key"]: e["n"] for e in entries}
    top = max(held.values(), default=0)
    out, new = {}, False
    for key in keys:
        if key not in held:
            top += 1
            held[key] = top
            entries.append({"n": top, "key": key, "at": now})
            new = True
        out[key] = held[key]
    if new:
        os.makedirs(state.root, exist_ok=True)
        tmp = state.numbers + ".orch-tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"numbers": entries}, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, state.numbers)
    return out


def target(state, ref, now=None):
    """The finding `ref` names: a number orch findings gave in the last 24 hours, or a key.
    Reads the record without writing to it."""
    now = time.time() if now is None else now
    ref = ref.strip()
    if ref.isdigit():
        key = next((e["key"] for e in _numbers(state, now) if e["n"] == int(ref)), None)
        if key is None:
            raise Refused(f"no finding has the number {ref} from the last 24 hours: run orch findings, then "
                          "give its number or its key")
    else:
        key = ref
    if not 0 < len(key) <= MAX_KEY or not os.path.exists(state.record):
        raise Refused(f"no finding has the key {key}")
    record = Record(state.record, readonly=True)
    try:
        found = record.finding(key)
    finally:
        record.close()
    if found is None:
        raise Refused(f"no finding has the key {key}")
    return found


def session_ref(state, ref):
    """The full id of the session `ref` names: its id, or the first characters of it as
    orch status shows them. Reads the record without writing to it."""
    ref = ref.strip()
    ids = []
    if ref and os.path.exists(state.record):
        record = Record(state.record, readonly=True)
        try:
            ids = [s["id"] for s in record.sessions()]
        finally:
            record.close()
    if ref in ids:
        return ref
    matches = [i for i in ids if ref and i.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise Refused(f"{ref} is the start of {len(matches)} sessions' ids: give more of it")
    raise Refused(f"no session {ref} in the record (orch status --all lists them)")


# ------------------------------------------------------------------------------- orch findings
def what(f):
    """A finding in a few words, from its structured fields only."""
    d = f.get("detail") or {}
    if f["rule"] == "O1":
        return f["file"]
    if f["rule"] == "O2":
        return f["symbol"]
    if f["rule"] in ("O3", "O4"):
        return f"{f['symbol']} -> {f['file']}" + (" (removed)" if f["rule"] == "O4" else "")
    if f["rule"] == "O5":
        return f"{d.get('method', '?')} {d.get('path', '?')} ({f['file']})"
    if f["rule"] == "O6":
        left = len(d.get("left", []))
        return f"{f['symbol']}: {left} caller{'s' if left != 1 else ''} left behind"
    if f["rule"] == "W1":
        return "same folder " + (os.path.basename(d.get("worktree", "")) or "?")
    return f["symbol"] or f["file"]


def _session(s, sid):
    s = s or {}
    return {"id": sid, "agent": s.get("agent", ""), "worktree": s.get("worktree", ""), "branch": s.get("branch", "")}


def collect(state, include_all=False, days=7, now=None):
    """The findings seen in the last `days` days, the latest first: the open ones that are not
    latent, or with include_all every one. Each gets its short number, its label (the latest)
    and the sessions involved. Reads the record without writing to it."""
    now = time.time() if now is None else now
    since = int((now - days * DAY) * NS)
    findings, sessions, labels = [], {}, {}
    if os.path.exists(state.record):
        record = Record(state.record, readonly=True)
        try:
            findings = [f for f in record.findings(states=None) if f["last_seen"] >= since]
            sessions = {s["id"]: s for s in record.sessions()}
            labels = {r["finding_key"]: r for r in record.labels()}          # oldest first: the latest wins
        finally:
            record.close()
    if not include_all:
        findings = [f for f in findings if f["state"] == "open" and not f["detail"].get("latent")]
    findings.sort(key=lambda f: (-f["last_seen"], f["key"]))
    numbers = number(state, [f["key"] for f in findings], now)
    rows = []
    for f in findings:
        lab = labels.get(f["key"])
        rows.append({
            "number": numbers[f["key"]], "key": f["key"], "rule": f["rule"], "strength": f["strength"],
            "symbol": f["symbol"], "file": f["file"], "what": what(f), "state": f["state"],
            "latent": bool(f["detail"].get("latent")),
            "sessions": [_session(sessions.get(sid), sid) for sid in f["sessions"]],
            "first_seen": _iso(f["first_seen"] / NS), "last_seen": _iso(f["last_seen"] / NS),
            "resolved": _iso(f["resolved"] / NS) if f["resolved"] else None,
            "label": {"verdict": lab["verdict"], "why": lab["why"], "at": _iso(lab["ts"] / NS)} if lab else None})
    return {"schema": FINDINGS_SCHEMA, "days": days, "all": include_all, "findings": rows}


def render(data):
    days = f"the last {data['days']} day{'s' if data['days'] != 1 else ''}"
    if not data["findings"]:
        if data["all"]:
            return f"No findings in {days}.\n"
        return f"No open findings in {days}. orch findings --all lists the resolved and latent ones too.\n"
    rows = [("#", "rule", "strength", "what", "sessions", "seen", "state", "label")]
    for r in data["findings"]:
        first, last = _local(r["first_seen"]), _local(r["last_seen"])
        seen = first if first == last else first + " to " + (last[6:] if last[:5] == first[:5] else last)
        rows.append((str(r["number"]), r["rule"], r["strength"], r["what"], ", ".join(s["id"][:8] for s in r["sessions"]),
                     seen, r["state"] + (", latent" if r["latent"] else ""), r["label"]["verdict"] if r["label"] else "-"))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip() for row in rows]
    lines += ["", 'Label one: orch label <#> real|harmless|false [--why "..."]. The numbers hold for 24 hours.']
    return "\n".join(printable(line) for line in lines) + "\n"
