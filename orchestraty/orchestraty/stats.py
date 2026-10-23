"""What ``orch stats`` reports: how Orchestraty was used, week by week, from the record alone.

The weeks are the last 7 days, the 7 before them, and so on back over ``--days`` (default 7).
Each week counts:

* sessions live at some time in it, agent sessions (with hooks), plain-git ones and cloud
  agents' branches, and pairs: two agent sessions on one repository working at the same time;
* findings first seen in it, by rule and by strength (latent ones apart);
* notes made in it, by kind and by state, how many were delivered, and the median and 90th
  percentile of their time to awareness: from the event that caused a note to the note written,
  ready for its agent;
* notes acted on (a proxy, below), and the findings they were about;
* notes per session per day: the notes handed to agents, over the days agent sessions were live
  (the days are the 24 hours counted back from now), on average and at most;
* what was held back, which the record keeps: weak findings, which no session hears (status
  only), latent ones (nobody is changing the file that uses the change), notes a session did
  not get because it had heard of the finding already, and notes withdrawn because their
  finding stopped holding before the agent took them. A finding that comes back, or a change
  found again under another session, is not told twice either; the record does not count those;
* labels given in it by verdict (a finding's latest), and reports of missed notes;
* an estimate of the tokens the notes handed to agents cost: their characters / 4.

Acted on is a proxy, worked out from the record only: a note handed to its agent counts as
acted on when, within ``acted_minutes`` of its delivery (30 by default, in config.json), the
receiving session edited a file the note cites (the file its finding names, or one holding a
function it names), or the finding stopped holding while that session was live. It says the
agent did something the note may have caused, not that the note caused it.

The hook's own time is not in here: the hook would have to read the clock and write the time
down on every event, which is work it must not do, and most of its time, the interpreter
starting, passes before any of its code runs. The tests measure it from outside instead
(``tests/test_agent_hooks.py``).

``summary`` keeps counts only: no path, name, symbol, session id, date or text of the user's,
so it can be read in full before it is shared. Orchestraty never sends it anywhere.
"""
import calendar
import math
import os
import statistics
import time

from orchestraty import __version__, config
from orchestraty.coordinator.record import Record
from orchestraty.feedback import VERDICTS

STATS_SCHEMA = 1
SUMMARY_SCHEMA = 1
NS = 1_000_000_000
DAY = 86400
WEEK = 7
LEVEL0_AGENT = "git"
CLOUD_AGENT = "cloud"
RULES = ("O1", "O2", "O3", "O4", "O5", "O6", "O7", "W1")
STRENGTHS = ("strong", "medium", "weak")
KINDS = ("edit", "next", "stop", "wake")
STATES = ("pending", "written", "delivered", "withdrawn")
HELD_BACK = ("weak_findings", "latent_findings", "already_heard", "withdrawn")


def _iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _day(iso):
    return time.strftime("%b %d", time.localtime(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))))


def _norm(path):
    return os.path.normcase(path).replace(os.sep, "/")


def _file_of(symbol):
    return str(symbol or "").split("::", 1)[0]


def cited_files(finding):
    """The files a finding's notes cite: the file it names and the files holding the functions
    it names (the one changed, where it moved, its uses, the callers left behind)."""
    d = finding.get("detail") or {}
    paths = {finding.get("file") or "", _file_of(finding.get("symbol")), _file_of(d.get("moved_to"))}
    paths.update(_file_of(u.get("symbol")) for u in d.get("uses", []) if isinstance(u, dict))
    paths.update(c.get("file") or _file_of(c.get("symbol")) for c in d.get("left", []) if isinstance(c, dict))
    return {_norm(p) for p in paths if p}


def _live_at(session, t):
    return session["started"] <= t and (session["state"] == "live" or t <= session["last_seen"])


def acted_on(note, finding, session, edits, window_ns):
    """Whether a note handed to its agent counts as acted on (the proxy): within the window after
    its delivery, the receiving session edited a file the note cites, or the finding stopped
    holding while that session was live. `edits`: {session id: [(ts, posix path in its worktree)]}."""
    t0 = note.get("delivered")
    if t0 is None or finding is None:
        return False
    t1 = t0 + window_ns
    resolved = finding.get("resolved")
    if resolved is not None and t0 <= resolved <= t1 and session is not None and _live_at(session, resolved):
        return True
    cited = cited_files(finding)
    return any(t0 <= ts <= t1 and path in cited for ts, path in edits.get(note["session_id"], ()))


def _edits(rows, sessions):
    """{session id: [(ts, path)]}: each edit as a posix path inside the session's worktree."""
    out = {}
    for r in rows:
        s = sessions.get(r["session_id"])
        if s is None or not s["worktree"] or not r["file_path"]:
            continue
        path = r["file_path"]
        full = os.path.normpath(path if os.path.isabs(path) else os.path.join(r["cwd"] or s["worktree"], path))
        try:
            rel = os.path.relpath(full, s["worktree"])
        except ValueError:                            # another drive
            continue
        if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            continue
        out.setdefault(r["session_id"], []).append((r["ts"], _norm(rel)))
    return out


def _percentile(sorted_values, p):
    """The nearest-rank percentile of a sorted list."""
    return sorted_values[max(0, math.ceil(p / 100 * len(sorted_values)) - 1)]


def _counts(values, keys):
    out = dict.fromkeys(keys, 0)
    for v in values:
        out[v if v in out else "other"] = out.get(v if v in out else "other", 0) + 1
    return out


def _weeks(now, days):
    out, end, oldest = [], now, now - days * DAY
    while end > oldest:
        start = max(oldest, end - WEEK * DAY)
        out.append((start, end))
        end = start
    return out


def per_session_day(lo, hi, agents, notes):
    """Notes handed to agent sessions per session and day, between `lo` and `hi` (ns): each day
    is 24 hours counted back from `hi`, and a session counts on each day it was live in."""
    day = DAY * NS
    counts = {}
    for d in range(max(1, round((hi - lo) / day))):
        top = hi - d * day
        for s in agents:
            if s["started"] < top and s["last_seen"] >= max(lo, top - day):
                counts[(s["id"], d)] = 0
    for x in notes:
        if x["delivered"] is not None and lo <= x["delivered"] < hi:
            key = (x["session_id"], (hi - 1 - x["delivered"]) // day)
            counts[key] = counts.get(key, 0) + 1
    return {"session_days": len(counts),
            "mean": round(sum(counts.values()) / len(counts), 2) if counts else None,
            "max": max(counts.values(), default=0)}


def _week(n, start, end, rec, window_ns):
    lo, hi = int(start * NS), int(end * NS)
    live = [s for s in rec["sessions"].values() if s["started"] < hi and s["last_seen"] >= lo]
    agents = [s for s in live if s["agent"] not in (LEVEL0_AGENT, CLOUD_AGENT)]
    pairs = sum(1 for i, a in enumerate(agents) for b in agents[i + 1:]
                if a["repo_id"] == b["repo_id"]
                and max(a["started"], b["started"], lo) <= min(a["last_seen"], b["last_seen"], hi))
    found = [f for f in rec["findings"].values() if lo <= f["first_seen"] < hi]
    shown = [f for f in found if not f["detail"].get("latent")]
    made = [x for x in rec["notes"] if lo <= x["created"] < hi]
    delivered = [x for x in made if x["state"] == "delivered"]
    handed = [x for x in delivered if x["delivered"] is not None]          # taken by a hook, text and all
    tta = sorted((x["written"] - x["cause_ts"]) / 1e6 for x in delivered
                 if x["written"] is not None and x["cause_ts"] is not None and x["written"] >= x["cause_ts"])
    acted = [x for x in handed if acted_on(x, rec["findings"].get(x["finding_key"]), rec["sessions"].get(x["session_id"]),
                                           rec["edits"], window_ns)]
    noted = rec["noted"]
    held = {"weak_findings": sum(1 for f in shown if f["strength"] == "weak" and f["key"] not in noted),
            "latent_findings": sum(1 for f in found if f["detail"].get("latent") and f["key"] not in noted),
            "already_heard": len(delivered) - len(handed),
            "withdrawn": sum(1 for x in made if x["state"] == "withdrawn")}
    latest = {}
    for row in rec["labels"]:                                              # oldest first: the latest wins
        if lo <= row["ts"] < hi:
            latest[row["finding_key"]] = row["verdict"]
    return {
        "week": n, "start": _iso(start), "end": _iso(end),
        "sessions": {"live": len(live), "agents": len(agents),
                     "plain_git": sum(1 for s in live if s["agent"] == LEVEL0_AGENT),
                     "cloud": sum(1 for s in live if s["agent"] == CLOUD_AGENT)},
        "pairs": pairs,
        "findings": {"total": len(shown), "by_rule": _counts((f["rule"] for f in shown), RULES),
                     "by_strength": _counts((f["strength"] for f in shown), STRENGTHS),
                     "latent": len(found) - len(shown)},
        "notes": {"made": len(made), "by_kind": _counts((x["kind"] for x in made), KINDS),
                  "by_state": _counts((x["state"] for x in made), STATES), "delivered": len(delivered),
                  "awareness_ms": {"measured": len(tta),
                                   "median": round(statistics.median(tta), 1) if tta else None,
                                   "p90": round(_percentile(tta, 90), 1) if tta else None},
                  "acted_on": len(acted), "findings_acted_on": len({x["finding_key"] for x in acted}),
                  "tokens_estimate": round(sum(len(x["text"]) for x in handed) / 4),
                  "per_session_day": per_session_day(lo, hi, agents, rec["notes"]), "held_back": held},
        "labels": {"total": len(latest), "by_verdict": _counts(latest.values(), VERDICTS)},
        "missed": sum(1 for row in rec["missed"] if lo <= row["ts"] < hi),
    }


def collect(state, days=7, now=None):
    """The stats of the last `days` days, the latest week first. Reads the record without
    writing to it."""
    now = time.time() if now is None else now
    window = max(1, config.load(state)["acted_minutes"])
    rec = {"sessions": {}, "findings": {}, "notes": [], "labels": [], "missed": [], "edits": {}, "noted": set()}
    if os.path.exists(state.record):
        record = Record(state.record, readonly=True)
        try:
            rec["sessions"] = {s["id"]: s for s in record.sessions()}
            rec["findings"] = {f["key"]: f for f in record.findings(states=None)}
            rec["notes"] = record.notes()
            rec["noted"] = {x["finding_key"] for x in rec["notes"]}
            rec["labels"] = record.labels()
            rec["missed"] = record.missed()
            rec["edits"] = _edits(record.edits(int((now - days * DAY) * NS)), rec["sessions"])
        finally:
            record.close()
    return {"schema": STATS_SCHEMA, "version": __version__, "days": days, "acted_minutes": window,
            "weeks": [_week(i + 1, start, end, rec, window * 60 * NS) for i, (start, end) in enumerate(_weeks(now, days))]}


def summary(data):
    """The stats as counts only, built field by field: nothing from the record but numbers and
    the rule, kind, state and verdict names, which are Orchestraty's own."""
    def known(counts, keys):
        return {k: int(counts.get(k, 0)) for k in keys}

    weeks = []
    for w in data["weeks"]:
        notes, aware, per_day = w["notes"], w["notes"]["awareness_ms"], w["notes"]["per_session_day"]
        weeks.append({
            "week": int(w["week"]),
            "sessions": {k: int(w["sessions"][k]) for k in ("live", "agents", "plain_git", "cloud")},
            "pairs": int(w["pairs"]),
            "findings": {"total": int(w["findings"]["total"]), "by_rule": known(w["findings"]["by_rule"], RULES),
                         "by_strength": known(w["findings"]["by_strength"], STRENGTHS),
                         "latent": int(w["findings"]["latent"])},
            "notes": {"made": int(notes["made"]), "by_kind": known(notes["by_kind"], KINDS),
                      "by_state": known(notes["by_state"], STATES), "delivered": int(notes["delivered"]),
                      "awareness_ms": {"measured": int(aware["measured"]),
                                       "median": None if aware["median"] is None else float(aware["median"]),
                                       "p90": None if aware["p90"] is None else float(aware["p90"])},
                      "acted_on": int(notes["acted_on"]), "findings_acted_on": int(notes["findings_acted_on"]),
                      "tokens_estimate": int(notes["tokens_estimate"]),
                      "per_session_day": {"session_days": int(per_day["session_days"]),
                                          "mean": None if per_day["mean"] is None else float(per_day["mean"]),
                                          "max": int(per_day["max"])},
                      "held_back": {k: int(notes["held_back"][k]) for k in HELD_BACK}},
            "labels": {"total": int(w["labels"]["total"]), "by_verdict": known(w["labels"]["by_verdict"], VERDICTS)},
            "missed": int(w["missed"])})
    return {"schema": SUMMARY_SCHEMA, "kind": "orchestraty usage summary", "version": str(__version__),
            "days": int(data["days"]), "acted_minutes": int(data["acted_minutes"]), "weeks": weeks}


def _some(counts):
    shown = [f"{k} {v}" for k, v in counts.items() if v]
    return ", ".join(shown) or "none"


def _plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def render(data):
    lines = [f"orch stats: the last {_plural(data['days'], 'day')}, the latest week first. Acted on is a proxy: the "
             f"session edited a file the note cites, or its finding stopped holding, within {data['acted_minutes']} "
             "minutes of delivery (acted_minutes in config.json)."]
    for w in data["weeks"]:
        s, f, notes = w["sessions"], w["findings"], w["notes"]
        aware, per_day, held = notes["awareness_ms"], notes["per_session_day"], notes["held_back"]
        lines += ["", f"Week {w['week']}  {_day(w['start'])} to {_day(w['end'])}",
                  f"  sessions    {s['live']} live ({s['agents']} agent, {s['plain_git']} plain git"
                  + (f", {s['cloud']} cloud branch" + ("es" if s["cloud"] != 1 else "") if s["cloud"] else "") + "); "
                  f"{_plural(w['pairs'], 'pair')} working at the same time",
                  f"  findings    {f['total']}" + (f" ({_some(f['by_strength'])}): {_some(f['by_rule'])}" if f["total"] else "")
                  + (f"; {f['latent']} latent" if f["latent"] else ""),
                  f"  notes       {notes['made']} made" + (f" ({_some(notes['by_kind'])}): {_some(notes['by_state'])}"
                                                           if notes["made"] else ""),
                  "  awareness   " + (f"median {aware['median']} ms, 90th percentile {aware['p90']} ms, over "
                                      f"{_plural(aware['measured'], 'delivered note')}" if aware["measured"]
                                      else "no delivered note yet"),
                  "  per day     " + (f"{per_day['mean']} notes per session per day, at most {per_day['max']} (over "
                                      f"{_plural(per_day['session_days'], 'session-day')})" if per_day["session_days"]
                                      else "no agent session"),
                  f"  held back   {_plural(held['weak_findings'], 'weak finding')} (status only), "
                  f"{_plural(held['latent_findings'], 'latent finding')}, {_plural(held['already_heard'], 'note')} "
                  f"already heard, {held['withdrawn']} withdrawn",
                  f"  acted on    {_plural(notes['acted_on'], 'note')}, {_plural(notes['findings_acted_on'], 'finding')} "
                  "(proxy)",
                  f"  labels      {_some(w['labels']['by_verdict'])}",
                  f"  missed      {_plural(w['missed'], 'report')}",
                  f"  tokens      ~{notes['tokens_estimate']} for the notes handed to agents (an estimate: "
                  "characters / 4)"]
    return "\n".join(lines) + "\n"
