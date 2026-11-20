"""The team block of ``orch status``: what team sync does for one repository, for people and tools.

A repository has the block only while team sync is on for it, or teammates' records of it are
known; a solo user's status is as it was. It says whether team sync is on and where to, the policy
in force (its blob, whether it lets sync run and why not, what it shares, whether it allows more
than the person agreed to), who this machine shares as, the last record sent, what was withheld
in the last 24 hours (by rule and field, never a value), and each teammate's record as last read.

A teammate's confirmed task line is shown to people only: in the text on a terminal, and in
``orch status --json --with-lines``. Never in status.json, a note or an MCP answer. Everything is
read locally, from config.json, the record and the shadow: no network.
"""
import time

from orchestraty import remote, team
from orchestraty.team import identity, people, policy, probe, teamfile

PEOPLE_FILE = ".orchestraty/people.yml"
NS = 1_000_000_000
DAY = 86400
FIELDS = {"sessions[].branch": "a branch name", "sessions[].pushed.ref": "a branch name",
          "sessions[].task.ticket": "a ticket", "sessions[].task.line": "a task line",
          "sessions[].claims[].target": "a claim", "sessions[].claims[].file": "a claim",
          "sessions[].work.files[]": "a file path", "sessions[].work.symbols[].symbol": "a function name",
          "sessions[].work.symbols[].reasons[]": "a reason", "sessions[].work.symbols[].moved_from": "a file path",
          "writer.label": "the device label"}
DROPPED = {"writer-mismatch": "written by an email not listed for {person}", "unlisted": "{person} is not in people.yml",
           "rollback": "older than a record already read", "too-large": "larger than 256 KiB",
           "newer-format": "a newer format: upgrade Orchestraty to read it", "not-a-record": "not a record",
           "bad-json": "not a record of this format", "duplicate-key": "not a record of this format",
           "bad-structure": "not a record of this format"}


def _iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def people_in_force(shadow, name):
    """The people file on the default branch as last fetched, People.empty() when it has none,
    or None when the default branch was never fetched. Raises people.Error."""
    ref = f"{remote.REF_ROOT}/{name}/HEAD"
    if not shadow.exists() or shadow.git(["rev-parse", "--verify", "--quiet", ref])[0] != 0:
        return None
    try:
        data = shadow.read_file(ref, PEOPLE_FILE, people.MAX_BYTES)
    except remote.TooLarge:
        raise people.Error(f"the file is larger than {people.MAX_BYTES // 1024} KiB") from None
    return people.People.empty() if data is None else people.People.parse(data)


def block(state, cfg, common_dir, writers, withheld, sent, now, with_lines=False, sessions=()):
    """The team block of one repository, or None for a repository without team sync. `writers`,
    `withheld` and `sent` are the record's rows (team_writers, team_withheld from 24 hours ago,
    team_sent's latest); `sessions` the teammates' branches followed, as status shows sessions."""
    chosen = team.settings(cfg, common_dir)
    if not chosen["on"] and not writers and not sessions:
        return None
    shadow = remote.Shadow(state, common_dir)
    try:
        force = policy.in_force(shadow, chosen["remote"])
    except Exception:                                     # a broken shadow: no policy known until it is read again
        force = None
    agreed = policy.from_normalised(chosen["consented"]) if chosen["consented"] else None
    answer, _ = probe.last(shadow)
    eff = policy.effective(force, agreed, chosen["narrow"], chosen["never_branches"], anonymous_read=answer)
    off = eff.off()
    kept = teamfile.read(shadow)
    return {
        "on": chosen["on"], "remote": chosen["remote"], "store": kept.get("store") or "ref", "agents_told": False,
        "policy": {"blob": force.blob[:7] if force is not None else "", "sync": off is None, "visibility": eff.visibility,
                   "share": {"claims": eff.claims, "task": eff.task, "unpushed": eff.unpushed, "activity": eff.activity},
                   "widened": bool(force is not None and agreed is not None and policy.widened(force, agreed)),
                   "why": off[0] if off else "", "fix": off[1] if off else ""},
        "me": _me(state, common_dir, shadow, chosen["remote"]),
        "sent": _sent(sent),
        "withheld_24h": _withheld(withheld),
        "fetch": _fetch(kept.get("fetch")),
        "writers": [_writer(w, with_lines) for w in writers],
        "sessions": list(sessions),
    }


def _fetch(kept):
    """The last team fetch: when, whether it worked and why not, the failures in a row, the next
    one, the last that worked and the writers it found. None before the first."""
    if not isinstance(kept, dict) or not isinstance(kept.get("at"), (int, float)):
        return None
    when = {k: _iso(kept[k]) if isinstance(kept.get(k), (int, float)) else None for k in ("at", "next", "last_ok")}
    return {"at": when["at"], "ok": kept.get("ok") is True, "error": str(kept.get("error") or ""),
            "failures": int(kept.get("failures") or 0), "next": when["next"], "last_ok": when["last_ok"],
            "writers": int(kept.get("writers") or 0)}


def team_claim(item):
    """A teammate's claim as status shows it: its hard flag shown, and never honoured here."""
    return {"kind": item["kind"], "target": item["target"], "file": item["file"], "hard": bool(item["hard"]),
            "since": _iso(item["since"] // NS), "until": _iso(item["until"] // NS)}


def session_team(row, with_lines=False, claims=()):
    """What a teammate's branch adds to a session in status: who works on it, from what, how it
    stands, and the claims their records share that hold now (team_planned rows). The task
    line only `with_lines`."""
    row = row or {"people": [], "own": 0, "evidence": "commits", "tip": "", "tip_time": 0, "as_of": 0, "agents": [],
                  "task": "", "merged": "", "contained_in": [], "builds_on": []}
    task = row["task"] if isinstance(row["task"], dict) else {}
    out = {"people": list(row["people"]), "own": bool(row["own"]), "evidence": row["evidence"], "tip": row["tip"],
           "last_commit": _iso(row["tip_time"]) if row["tip_time"] else "",
           "as_of": _iso(row["as_of"]) if row["as_of"] else "", "agents": list(row["agents"]),
           "ticket": str(task.get("ticket") or ""), "merged": row["merged"], "contained_in": list(row["contained_in"]),
           "builds_on": list(row["builds_on"]), "claims": [team_claim(c) for c in claims]}
    if with_lines:
        out["line"] = str(task.get("line") or "")
    return out


def _me(state, common_dir, shadow, remote_name):
    try:
        email = identity.my_email(common_dir)
    except identity.NoEmail:
        return None
    try:
        folks = people_in_force(shadow, remote_name) or people.People.empty()
    except people.Error:
        folks = people.People.empty()
    me = identity.who_am_i(folks, email)
    try:
        device = identity.Device.load(state)
    except identity.DeviceError:
        device = None
    return {"person": me.person, "listed": me.listed, "device": device.id if device else "",
            "label": device.label if device else ""}


def _sent(row):
    if not row:
        return None
    ok = row["result"] == "pushed"
    return {"at": _iso(row["ts"] / NS), "ok": ok, "error": "" if ok else row["result"], "seq": row["seq"],
            "sessions": row["sessions"]}


def _withheld(rows):
    counts = {}
    for r in rows:
        key = (r["rule"], r["field"])
        counts[key] = counts.get(key, 0) + 1
    return [{"rule": rule, "field": field, "count": n}
            for (rule, field), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _writer(w, with_lines):
    entries = []
    for e in w["entries"] if isinstance(w["entries"], list) else []:
        if not isinstance(e, dict):
            continue
        entry = {"agent": str(e.get("agent", "")), "state": str(e.get("state", "")), "branch": str(e.get("branch", "")),
                 "pushed": e.get("pushed") is True, "ticket": str(e.get("ticket") or "")}
        if with_lines:
            entry["line"] = str(e.get("line") or "")
        entries.append(entry)
    return {"person": w["person"], "device": w["device"], "label": "", "written": _iso(w["written"]),
            "verdict": w["verdict"], "why": w["why"], "entries": entries}


# ------------------------------------------------------------------------------- the text

def lines(t, tty=False, local=None):
    """The block's lines in orch status (indented two spaces). Teammates' task lines only on a
    terminal (`tty`). `local(iso)` gives a time as HH:MM."""
    local = local or (lambda iso: iso[11:16])
    pol, me = t["policy"], t["me"]
    where = f"policy on {t['remote']}'s default branch" + (f", {pol['blob']}" if pol["blob"] else ", not read yet")
    who = ""
    if me:
        device = me["label"] or (f"device {me['device'][:6]}" if me["device"] else "no device yet")
        who = f"; you are {me['person']} on {device}" + (f" ({me['device'][:6]})" if me["label"] else "")
    head = f"  team: {'on' if t['on'] else 'off'} ({where}" + (f", {pol['visibility']}" if pol["visibility"] else "") + f"){who}"
    out = [head]
    if pol["why"]:
        out.append(f"    nothing is shared: {pol['why']}; fix: {pol['fix']}")
    else:
        share = pol["share"]
        yes, no = ["sessions", "branches"], []
        (yes if share["claims"] else no).append("claims")
        (yes if share["task"] != "none" else no).append("tickets")
        (yes if share["task"] == "confirmed" else no).append("task lines")
        (yes if share["unpushed"] else no).append("unpushed work")
        (yes if share["activity"] else no).append("activity")
        out.append(f"    sharing: {', '.join(yes)}" + (f" (not: {', '.join(no)})" if no else ""))
    if pol["widened"]:
        out.append("    the policy in force allows more than you agreed to: orch team on to review it")
    s = t["sent"]
    if s is None:
        out.append(f"    your record: not sent yet; it goes with your next push to {t['remote']}")
    elif s["ok"]:
        out.append(f"    your record: sent {local(s['at'])} (seq {s['seq']}, {s['sessions']} sessions); "
                   "next with your next push")
    else:
        out.append(f"    your record: not sent at {local(s['at'])}: {s['error']}")
    held = t["withheld_24h"]
    if held:
        n = sum(w["count"] for w in held)
        words = "; ".join(f"{w['rule']} in {FIELDS.get(w['field'], w['field'])}" for w in held)
        out.append(f"    withheld in the last 24 h: {n} ({words})")
    f = t.get("fetch")
    if t["writers"] or t.get("sessions") or f:
        out.append(f"  teammates' branches from {t['remote']} (shared through git; their sessions are told on their own "
                   "machines)")
    if f:
        nxt = f"; next at {local(f['next'])}" if f["next"] and t["on"] else ""
        if f["ok"]:
            out.append(f"    last read {local(f['at'])}: ok, {f['writers']} writers, {len(t.get('sessions') or [])} "
                       f"branches{nxt}")
        else:
            out.append(f"    last read {local(f['at'])}: failed ({f['failures']} in a row): {f['error']}{nxt}")
    for w in t["writers"]:
        name = f"{w['person']} ({w['device'][:6]})"
        if w["verdict"] == "ignored":
            out.append(f"    ignored: refs/orchestraty/{w['person']}/{w['device']} (past one read's limits: 64 writers, "
                       "4 devices a person, 4 MiB)")
            continue
        if w["verdict"] != "admitted":
            why = DROPPED.get(w["why"].split(":", 1)[0], "a field that breaks its grammar")
            out.append(f"    dropped: refs/orchestraty/{w['person']}/{w['device']} ({why.format(person=w['person'])})")
            continue
        if not w["entries"]:
            out.append(f"    {name}  no sessions shared, as of {local(w['written'])}")
        for e in w["entries"]:
            pushed = "" if e["pushed"] else "  nothing pushed"
            ticket = f"  [{e['ticket']}]" if e["ticket"] else ""
            line = f"  {e['line']}" if tty and e.get("line") else ""
            ended = "" if e["state"] == "live" else f"  ({e['state']})"
            out.append(f"    {name}  {e['branch']}{pushed}{ticket}{line}{ended}")
    return out
