"""``orch events``: what changed in the record, as a stream, read-only.

The record's own write methods log each change as it happens (the ``changes`` table, record
schema 3): sessions started and ended, findings opened and resolved, notes written, delivered
and withdrawn, and declarations made. ``orch events`` reads that log in order, with each change's
session, finding or note as the record has them when it is read. ``--follow`` polls the record
about once a second for new changes; there is no server and no socket. Without ``--follow`` (or
with ``--once``) it prints the changes there are and ends.

``--json`` prints one JSON object per line, with ``schema``: fields are only added within a
schema number. The text of a note and a declaration's task line are never part of an event.
"""
import calendar
import json
import os
import re
import time

from orchestraty.coordinator.record import Record

EVENTS_SCHEMA = 1
NS = 1_000_000_000
KINDS = ("session_started", "session_ended", "finding_opened", "finding_resolved", "note_written",
         "note_delivered", "note_withdrawn", "declared")
_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text, now=None):
    """--since: an ISO 8601 time (UTC with Z, else local), seconds since the epoch, or a time
    ago (30s, 10m, 2h, 1d). Returns seconds since the epoch; ValueError otherwise."""
    now = time.time() if now is None else now
    text = text.strip()
    m = _DURATION.match(text)
    if m:
        return now - int(m.group(1)) * _UNITS[m.group(2)]
    try:
        return float(text)
    except ValueError:
        pass
    utc = text.endswith("Z")
    body = text[:-1] if utc else text
    for form in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = time.strptime(body, form)
        except ValueError:
            continue
        return float(calendar.timegm(parsed)) if utc else time.mktime(parsed)
    raise ValueError(f"not a time: {text}")


def _iso(ns):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ns / NS))


def event(record, change, declared=None):
    """One change of the log as an event: the change, and what it is about. `declared`: the
    declarations by id, when the caller has read them already."""
    out = {"schema": EVENTS_SCHEMA, "id": change["id"], "time": _iso(change["ts"]), "ts": change["ts"],
           "event": change["kind"], "session": change["session_id"] or None}
    kind = change["kind"]
    if kind.startswith("session_"):
        s = record.session(change["session_id"])
        if s:
            out.update(agent=s["agent"], worktree=s["worktree"], branch=s["branch"])
            if kind == "session_ended" and s["state"] != "live":
                out["ended"] = s["note"]                 # why it ended, while it has not come back
    elif kind.startswith("finding_"):
        f = record.finding(change["finding_key"])
        out["finding"] = change["finding_key"]
        if f:
            out.update(rule=f["rule"], strength=f["strength"], symbol=f["symbol"], file=f["file"],
                       sessions=f["sessions"], changer=f["changer"], receiver=f["receiver"],
                       latent=bool(f["detail"].get("latent")))
    elif kind.startswith("note_"):
        n = record.note(change["ref"]) if change["ref"] is not None else None
        out.update(finding=change["finding_key"], note=change["ref"])
        if n:
            out["note_kind"] = n["kind"]
        f = record.finding(change["finding_key"])
        if f:
            out["rule"] = f["rule"]
    elif kind == "declared":
        d = (declared if declared is not None else {d["id"]: d for d in record.declarations()}).get(change["ref"])
        if d:
            out.update(paths=d["paths"], symbols=d["symbols"], until=_iso(d["expires"]))
    return out


def read(record, after_id=0, since=None):
    """The events after the change `after_id`, from `since` (seconds) on, in order."""
    changes = record.change_log(after_id=after_id, since=None if since is None else int(since * NS))
    declared = {d["id"]: d for d in record.declarations()} if any(c["kind"] == "declared" for c in changes) else {}
    return [event(record, c, declared) for c in changes]


def _local(iso, form):
    return time.strftime(form, time.localtime(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))))


def render(e):
    """One line for one event."""
    kind, sid = e["event"].replace("_", " "), (e["session"] or "")[:8]
    if e["event"].startswith("session_"):
        what = sid + (f" {e['agent']} in {os.path.basename(e['worktree']) or '?'}" if "agent" in e else "")
        what += (f" ({e['branch']})" if e.get("branch") else "") + (f": {e['ended']}" if e.get("ended") else "")
    elif e["event"].startswith("finding_"):
        if "rule" not in e:
            what = e["finding"]
        else:
            name = "same folder" if e["rule"] == "W1" else (e["symbol"] or e["file"] or "?")
            where = f", in {e['file']}" if e["symbol"] and e["file"] and e["symbol"].split("::")[0] != e["file"] else ""
            who = (f"{e['changer'][:8]} -> {e['receiver'][:8]}" if e["changer"] and e["receiver"]
                   else " and ".join(x[:8] for x in e["sessions"]))
            what = f"{e['strength']} {e['rule']} {name}{where} ({who})"
    elif e["event"].startswith("note_"):
        what = f"#{e['note']} {e.get('note_kind', '?')} note to {sid} on {e['finding']}"
    else:
        names = f" ({', '.join(x.split('::')[-1] for x in e['symbols'])})" if e.get("symbols") else ""
        until = f" until {_local(e['until'], '%H:%M')}" if e.get("until") else ""
        what = f"{sid}: {', '.join(e.get('paths', []))}{names}{until}"
    return f"{_local(e['time'], '%Y-%m-%d %H:%M:%S')}  {kind:<16}  {what}"


def _line(e, as_json):
    return json.dumps(e, separators=(",", ":"), sort_keys=True) if as_json else render(e)


def run(state, out, as_json=False, follow=False, since=None, interval=1.0, keep_going=None, sleep=time.sleep):
    """Print the events (from `since`, in seconds) to `out`; with `follow`, then poll for new
    ones until `keep_going()` says no (by default never: until interrupted). Following without
    `since` starts with the changes the record learns from now on."""
    keep_going = keep_going or (lambda: True)
    record, last, skip = None, 0, follow and since is None
    try:
        while True:
            if record is None and os.path.exists(state.record):
                record = Record(state.record, readonly=True)
                if skip:
                    last = record.last_change()
            skip = False                                 # a record that appears later is new from its start
            if record is not None:
                for e in read(record, after_id=last, since=since):
                    out.write(_line(e, as_json) + "\n")
                    last = e["id"]
                out.flush()
            if not follow or not keep_going():
                return 0
            sleep(interval)
    finally:
        if record is not None:
            record.close()
