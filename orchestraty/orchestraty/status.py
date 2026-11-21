"""What ``orch status`` reports: the coordinator, the watched repositories and their sessions.

``collect`` reads the record without writing to it, so it works while the coordinator runs
and when it does not; ``render`` turns the result into text. The JSON form (``--json``) is
``collect``'s result as it is, with a schema number for other tools to check. Cloud agents'
branches (``orch remote``) are a group of their own in each repository, with the last fetch's
result, which the shadow repository keeps beside it. Each session says which kinds of note its
agent can be handed (``adapters``), and the text says what an agent there cannot be told. A
live session's claims (``orch claim``) that hold are listed with it, and each repository has
its merge-order advice (``mergeorder``) and its together check: the settings, and the latest
result for each pair of its sessions (``together``); and its TypeScript contracts: the settings,
and how each session's exports were last read (``livemap.typescript``). While the coordinator
runs, ``unwatched`` names the repositories, not watched, that agent sessions work in now, and
how many, so the user can watch them (its unwatched.json; nothing about them is in the record).
"""
import calendar
import json
import os
import time

from orchestraty import adapters, claims as claiming, config, mergeorder, remote, together as checking
from orchestraty.coordinator.lock import is_fresh
from orchestraty.coordinator.record import Record, repo_key, repo_name
from orchestraty.delivery.notes import printable

STATUS_SCHEMA = 1
LEVEL0_AGENT = "git"
STALE_SECONDS = 10.0
NS = 1_000_000_000


def _iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _coordinator(state, now):
    running = is_fresh(state.alive, STALE_SECONDS, now)
    out = {"running": running}
    if running:
        try:
            with open(state.info, "r", encoding="utf-8") as fh:
                info = json.load(fh)
            out.update(pid=info.get("pid"), since=_iso(info["started"]), version=info.get("version"))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out


def _hears(agent):
    """The kinds of note a session of this agent can be handed: none for plain git and cloud branches."""
    return [] if agent in (LEVEL0_AGENT, remote.CLOUD_AGENT) else list(adapters.hears(agent))


def _claim(c):
    return {"id": c["id"], "kind": c["kind"], "target": c["target"], "file": c["file"], "hard": bool(c["hard"]),
            "since": _iso(c["ts"] / NS), "until": _iso(c["expires"] / NS)}


def _session(s, now, changes, declared=(), claims=()):
    files, unattributed, symbols, unread = changes
    return {"id": s["id"], "agent": s["agent"], "hears": _hears(s["agent"]), "worktree": s["worktree"],
            "branch": s["branch"],
            "base_commit": s["base_commit"], "base_guessed": bool(s["base_guessed"]),
            "state": s["state"], "turn": s["turn"], "note": s["note"],
            "started": _iso(s["started"] / NS), "last_event": _iso(s["last_seen"] / NS),
            "seconds_since_last_event": max(0, int(now - s["last_seen"] / NS)),
            "changes": {"files": files, "unattributed": unattributed, "symbols": symbols, "not_compared": unread},
            "declared": [{"paths": d["paths"], "symbols": d["symbols"], "task": d["task"], "since": _iso(d["ts"] / NS),
                          "until": _iso(d["expires"] / NS)} for d in declared],
            "claims": [_claim(c) for c in claims]}


def collect(state, include_ended=False, now=None):
    now = time.time() if now is None else now
    cfg = config.load(state)
    data = {"schema": STATUS_SCHEMA, "coordinator": _coordinator(state, now), "repositories": []}
    sessions_by_repo, repo_rows, changes, findings, declared, claimed, every = {}, {}, {}, {}, {}, {}, {}
    if os.path.exists(state.record):
        record = Record(state.record, readonly=True)
        try:
            repo_rows = {r["key"]: r for r in record.repos()}
            states = None if include_ended else ("live",)
            for s in record.sessions(states=states):
                sessions_by_repo.setdefault(s["repo_id"], []).append(s)
                changes[s["id"]] = (*record.changes(s["id"]), record.not_compared(s["id"]))
            for d in record.declarations(active_at=int(now * NS)):
                declared.setdefault(d["session_id"], []).append(d)
            for c in record.claims(active_at=int(now * NS)):
                claimed.setdefault(c["session_id"], []).append(c)
            for f in record.findings():
                every.setdefault(f["repo_id"], []).append(f)
                if include_ended or not f["detail"].get("latent"):
                    findings.setdefault(f["repo_id"], []).append(f)
        finally:
            record.close()
    checked = {}
    for r in checking.results(state):                     # oldest first: the latest for a pair wins
        if isinstance(r.get("repo"), str) and isinstance(r.get("sessions"), list):
            checked.setdefault(repo_key(r["repo"]), {})[tuple(r["sessions"])] = r
    for common_dir in cfg["repos"]:
        row = repo_rows.get(repo_key(common_dir))
        sessions = sessions_by_repo.get(row["id"], []) if row else []
        local = [s for s in sessions if s["agent"] != remote.CLOUD_AGENT]
        cloud = [s for s in sessions if s["agent"] == remote.CLOUD_AGENT]
        data["repositories"].append({
            "name": repo_name(common_dir), "common_dir": common_dir,
            "sessions": [_session(s, now, changes.get(s["id"], ({}, [], [], {})),
                                  declared.get(s["id"], ()) if s["state"] == "live" else (),
                                  claimed.get(s["id"], ()) if s["state"] == "live" else ())
                         for s in sorted(local, key=lambda s: (s["started"], s["id"]))],
            "overlaps": [_finding(f) for f in sorted(findings.get(row["id"], []) if row else [], key=_finding_order)],
            "merge_order": mergeorder.advise(every.get(row["id"], []) if row else [], {s["id"]: s for s in sessions}),
            "together": _together(cfg, common_dir, checked.get(repo_key(common_dir), {}), {s["id"] for s in local}),
            "typescript": _typescript(state, cfg, common_dir, {s["id"] for s in local}),
            "cloud": _cloud(state, cfg, common_dir, now, cloud, changes)})
    data["unwatched"] = unwatched(state, cfg, now)
    return data


def unwatched(state, cfg=None, now=None):
    """The repositories, not watched, that agent sessions work in now, as the running
    coordinator counts them in unwatched.json: [{name, common_dir, folder, sessions}]. Empty
    while no coordinator runs."""
    now = time.time() if now is None else now
    if not is_fresh(state.alive, STALE_SECONDS, now):
        return []
    cfg = config.load(state) if cfg is None else cfg
    try:
        with open(state.unwatched, "r", encoding="utf-8") as fh:
            kept = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for r in kept.get("repositories", []) if isinstance(kept, dict) else []:
        if (isinstance(r, dict) and isinstance(r.get("common_dir"), str) and isinstance(r.get("folder"), str)
                and isinstance(r.get("sessions"), int) and r["sessions"] > 0
                and not config.is_watched(cfg, r["common_dir"])):
            out.append({"name": repo_name(r["common_dir"]), "common_dir": r["common_dir"], "folder": r["folder"],
                        "sessions": r["sessions"]})
    return out


def _cloud(state, cfg, common_dir, now, sessions, changes):
    """The repository's cloud branches: the settings, the last fetch, and one session each."""
    chosen = remote.settings(cfg, common_dir)
    kept = remote.Shadow(state, common_dir).status()
    fetch = None
    if isinstance(kept.get("at"), (int, float)):
        fetch = {"at": _iso(kept["at"]), "ok": bool(kept.get("ok")), "error": str(kept.get("error") or ""),
                 "failures": int(kept.get("failures") or 0),
                 "next": _iso(kept["next"]) if isinstance(kept.get("next"), (int, float)) else None,
                 "last_ok": _iso(kept["last_ok"]) if isinstance(kept.get("last_ok"), (int, float)) else None,
                 "branches": int(kept.get("branches") or 0)}
    return {"on": chosen["on"], "remote": chosen["remote"], "patterns": chosen["patterns"], "agents_told": False,
            "fetch": fetch,
            "branches": [_session(s, now, changes.get(s["id"], ({}, [], [], {})))
                         for s in sorted(sessions, key=lambda s: (s["branch"], s["id"]))]}


def _together(cfg, common_dir, checked, shown):
    """The together check of one repository: its settings, and the latest result for each pair of
    the sessions shown, the latest first."""
    entry = checking.settings(cfg, common_dir)
    latest = [r for pair, r in checked.items() if set(pair) <= shown]
    latest.sort(key=lambda r: r.get("at", ""), reverse=True)
    results = [{"sessions": r["sessions"], "findings": r.get("findings", []), "at": r.get("at", ""),
                "command": r.get("command", ""), "result": r.get("result", ""), "conflicts": r.get("conflicts", []),
                "alone": {sid: _ran(x) for sid, x in (r.get("alone") or {}).items()},
                "together": _ran(r["together"]) if isinstance(r.get("together"), dict) else None,
                "first_failing_line": r.get("first_failing_line", ""), "error": r.get("error", "")} for r in latest]
    return {"on": entry["on"], "command": entry["command"], "timeout": entry["timeout"], "results": results}


def _typescript(state, cfg, common_dir, shown):
    """TypeScript contracts for one repository: the settings, and each shown session's latest run."""
    from orchestraty.livemap import typescript
    entry = typescript.settings(cfg, common_dir)
    key = repo_key(common_dir)
    found = []
    for sid, run in sorted(typescript.runs(state).items()):
        if run.get("repo") != key or sid not in shown:
            continue
        found.append({"session": sid, "at": run.get("at", ""), "source": run.get("source", ""), "why": run.get("why", ""),
                      "head": _compiled(run.get("head")), "base": _compiled(run.get("base")),
                      "not_compared": [str(p) for p in run.get("not_compared") or []]})
    return {"on": entry["on"], "timeout": entry["timeout"], "runs": found}


def _compiled(run):
    if not isinstance(run, dict):
        return None
    return {"exit_code": run.get("exit_code"), "timed_out": bool(run.get("timed_out")), "seconds": run.get("seconds", 0),
            "error": run.get("error", "")}


def _ts_line(run):
    """One line for one session's latest TypeScript run."""
    from orchestraty.livemap import typescript
    return f"{run['session'][:8]}: {typescript.describe(run)}"


def _ran(run):
    return {"exit_code": run.get("exit_code"), "timed_out": bool(run.get("timed_out"))}


def _together_line(r):
    """One line for one together check's result."""
    pair = " and ".join(sid[:8] for sid in r["sessions"])
    if r["result"] == "fails together":
        return f"{pair}: each passes alone; together it fails" + (f": {r['first_failing_line']}"
                                                                   if r["first_failing_line"] else "")
    if r["result"] == "passes together":
        return f"{pair}: pass together"
    if r["result"] == "fails alone":
        failing = " and ".join(sid[:8] for sid, x in r["alone"].items() if x["exit_code"] != 0)
        return f"{pair}: already fails alone ({failing})"
    if r["result"] == "conflict":
        return f"{pair}: their changes conflict in {', '.join(r['conflicts'][:4])}"
    if r["result"] == "timed out":
        return f"{pair}: together, did not finish in time"
    return f"{pair}: could not be checked: {r['error']}"


_STRENGTH = {"strong": 0, "medium": 1, "weak": 2}


def _finding_order(f):
    return (_STRENGTH.get(f["strength"], 9), f["rule"], f["key"])


def _finding(f):
    return {"key": f["key"], "rule": f["rule"], "strength": f["strength"], "symbol": f["symbol"],
            "file": f["file"], "changer": f["changer"], "receiver": f["receiver"], "sessions": f["sessions"],
            "detail": f["detail"]}


def _overlap_line(f, names=None):
    """One line for one finding, from its structured fields only. `names`: how to call some
    sessions (a cloud branch by its name) instead of by the start of their id."""
    def _short(sid):
        return (names or {}).get(sid) or sid[:8]

    name = f["symbol"].split("::")[-1] if f["symbol"] else f["file"]
    d = f["detail"]
    if f["rule"] == "O1":
        what = f"{f['file']} changed in both " + " and ".join(_short(s) for s in f["sessions"])
    elif f["rule"] == "O2":
        what = f"{f['symbol']} changed in both " + " and ".join(_short(s) for s in f["sessions"])
    elif f["rule"] == "O5":
        did = d.get("changes") or {}
        what = (f"{d.get('method', '?')} {d.get('path', '?')} ({f['file']}): "
                + ", ".join(f"{_short(s)} {_route_done(did.get(s) or {})}" for s in f["sessions"]))
    elif f["rule"] == "W1":
        folder = os.path.basename(d.get("worktree", "")) or "?"
        what = f"{' and '.join(_short(s) for s in f['sessions'])} work in the same folder, {folder}"
    elif f["rule"] == "O7":
        how = d.get("how") or {}
        what = (f"{d.get('what') or f['symbol'] or f['file']}: "
                + ", ".join(f"{how.get(s, 'declared')} by {_short(s)}" for s in f["sessions"]))
    elif f["rule"] in ("O3", "O4"):
        how = "removed" if f["rule"] == "O4" else "breaking change"
        what = (f"{name} ({f['symbol'].split('::')[0]}): {how} in {_short(f['changer'])}, "
                f"used in {f['file']} by {_short(f['receiver'])}")
    else:
        left = d.get("left", [])
        what = (f"{name} ({f['symbol'].split('::')[0]}): {len(left)} caller{'s' if len(left) != 1 else ''} "
                f"left behind by {_short(f['changer'])}")
    return f"{f['strength']:<6}  {f['rule']}  {what}"


def _route_done(change):
    """What one session did to a route, for an O5 line."""
    if change.get("to"):
        return "changed it to " + change["to"]
    if change.get("from"):
        return f"changed {change['from']} into it"
    return change.get("kind") or "changed"


def _summary(changes, limit=4):
    """One line: the files a session changed and the functions whose contract it changed."""
    files, symbols = changes["files"], changes["symbols"]
    if not files and not changes["unattributed"]:
        return ""
    names = sorted(files)
    parts = ["files: " + ", ".join(names[:limit]) + (f" (+{len(names) - limit} more)" if len(names) > limit else "")]
    if changes["unattributed"]:
        parts.append(f"{len(changes['unattributed'])} not attributed")
    unread = changes.get("not_compared") or {}
    if unread:
        listed = [f"{p} ({why})" for p, why in sorted(unread.items())]
        parts.append("not compared: " + ", ".join(listed[:limit]) + (f" (+{len(listed) - limit} more)" if len(listed) > limit else ""))
    marked = [c for c in symbols if c["contract"] in ("breaking", "review")]
    modified = [c for c in symbols if c["contract"] not in ("breaking", "review") and c["kind"] != "added"]
    if marked:
        shown = [f"{c['symbol'].split('::')[-1]} [{c['contract']}: {'; '.join(c['reasons'][:2])}]"
                 for c in sorted(marked, key=lambda c: (c["contract"] != "breaking", c["symbol"]))[:limit]]
        parts.append("contracts: " + ", ".join(shown) + (f" (+{len(marked) - limit} more)" if len(marked) > limit else ""))
    if modified:
        parts.append(f"{len(modified)} other functions changed")
    return "; ".join(parts)


def _declared_line(d, limit=4):
    """One line for one declaration: what it names, until when, and the agent's task line."""
    paths = ", ".join(d["paths"][:limit]) + (f" (+{len(d['paths']) - limit} more)" if len(d["paths"]) > limit else "")
    names = f" ({', '.join(x.split('::')[-1] for x in d['symbols'][:limit])})" if d["symbols"] else ""
    until = time.strftime("%H:%M", time.localtime(calendar.timegm(time.strptime(d["until"], "%Y-%m-%dT%H:%M:%SZ"))))
    return f"declared: {paths}{names} until {until}" + (f': "{d["task"]}"' if d.get("task") else "")


def _claim_line(c):
    """One line for one claim that holds: its number, what it reserves, until when, and whether it is hard."""
    return f"claimed #{c['id']}: {claiming.describe(c)} until {_local(c['until'])}" + (", hard" if c["hard"] else "")


def _ago(seconds):
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def render(data, wake=False):
    """The text of orch status; `wake`: whether wake is on (it says then who cannot be woken)."""
    c = data["coordinator"]
    if c["running"]:
        lines = [f"Coordinator: running (pid {c.get('pid', '?')}, since {c.get('since', '?')})"]
    else:
        lines = ["Coordinator: not running (a hook starts it with the next event)"]
    if not data["repositories"]:
        lines += ["", "No repository is watched. Run orch init (or orch watch) inside one."]
        lines += _unwatched_lines(data.get("unwatched", []))
        return "\n".join(printable(line) for line in lines) + "\n"
    for repo in data["repositories"]:
        lines += ["", f"{repo['name']}  ({repo['common_dir']})"]
        cloud = repo.get("cloud") or {}
        names = {s["id"]: s["branch"] for s in cloud.get("branches", [])}
        if repo["sessions"]:
            lines += _session_lines(repo["sessions"])
            lines += _cannot_lines(repo["sessions"], wake)
        else:
            lines.append("  no live sessions")
        if cloud.get("on") or cloud.get("branches"):
            lines += _cloud_lines(cloud)
        if repo["overlaps"]:
            lines.append("  overlaps:")
            lines += ["    " + _overlap_line(f, names) for f in repo["overlaps"]]
        for m in repo.get("merge_order", ()):
            lines.append("  merge order: " + m["line"])
        check = repo.get("together") or {}
        if check.get("on") or check.get("results"):
            command = check.get("command") or "no command"
            lines.append(f"  together check: {'on' if check.get('on') else 'off'}, `{command}`")
            lines += ["    " + _together_line(r) for r in check.get("results", [])]
        ts = repo.get("typescript") or {}
        if ts.get("on") or ts.get("runs"):
            lines.append(f"  TypeScript contracts: {'on' if ts.get('on') else 'off'}")
            lines += ["    " + _ts_line(r) for r in ts.get("runs", [])]
    lines += _unwatched_lines(data.get("unwatched", []))
    if any(s["base_guessed"] for r in data["repositories"] for s in r["sessions"]):
        lines += ["", "base?: the session was first seen after it started, so its base is the commit at that time."]
    return "\n".join(printable(line) for line in lines) + "\n"


def _unwatched_lines(unwatched):
    """Where agent sessions work that Orchestraty does not compare, and how to change that."""
    if not unwatched:
        return []
    lines = ["", "Not watched, so these sessions are not compared:"]
    for r in unwatched:
        count = "1 session" if r["sessions"] == 1 else f"{r['sessions']} sessions"
        lines.append(f"  {r['name']}: {count}. To watch it: orch watch \"{r['folder']}\"")
    return lines


def _local(iso, form="%H:%M"):
    return time.strftime(form, time.localtime(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))))


def _cloud_lines(cloud):
    """The cloud branches: from where, the last fetch, and each branch with what it changed."""
    lines = [f"  cloud branches from {cloud['remote']} ({', '.join(cloud['patterns']) or 'no patterns'}): "
             "level 0, their agents are not told"]
    f = cloud.get("fetch")
    if not f:
        lines.append("    not fetched yet" + ("" if cloud.get("on") else " (off)"))
    else:
        when = _local(f["at"], "%Y-%m-%d %H:%M")
        nxt = f" next at {_local(f['next'])}" if f.get("next") and cloud.get("on") else ""
        if f["ok"]:
            lines.append(f"    last fetch {when}: ok, {f['branches']} branch(es) match;{nxt}".rstrip(";"))
        else:
            lines.append(f"    last fetch {when}: failed ({f['failures']} in a row): {f['error']};"
                         + (nxt.replace("next at", "next try at") if nxt else "").rstrip(";"))
    for s in cloud.get("branches", []):
        state = "" if s["state"] == "live" else f"  ({s['note'] or s['state']})"
        lines.append(f"    {s['branch']}  last commit {_ago(s['seconds_since_last_event'])}{state}")
        summary = _summary(s["changes"])
        if summary:
            lines.append("      " + summary)
    return lines


def _cannot_lines(sessions, wake):
    """One line for each agent among the sessions that cannot be handed a kind of note, and,
    where another session's hard claim holds, for each that cannot be made to ask its user."""
    lines = []
    hard = {s["id"] for s in sessions if any(c["hard"] for c in s.get("claims", ()))}
    asked = {s["agent"] for s in sessions if s["hears"] and hard - {s["id"]}}
    for agent in sorted({s["agent"] for s in sessions if s["hears"]}):
        cannot = adapters.cannot(agent)
        if "edit" in cannot:
            lines.append(f"  {agent}: no note before an edit; {cannot['edit']}.")
        if wake and "wake" in cannot:
            lines.append(f"  {agent}: not woken by a note; {cannot['wake']}.")
        if agent in asked and not adapters.asks(agent):
            lines.append(f"  {agent}: " + cannot.get("ask", "a hard claim only informs it: this agent is not known "
                                                            "to ask its user before an edit") + ".")
    return lines


def _session_lines(sessions):
    """The table of a repository's sessions, each with what it changed and declared."""
    rows = [("session", "agent", "worktree", "branch", "base", "last event", "state")]
    for s in sessions:
        state = s["turn"] if s["state"] == "live" else (s["note"] or s["state"])
        base = (s["base_commit"][:9] or "-") + ("?" if s["base_guessed"] else "")
        rows.append((s["id"][:8], s["agent"], os.path.basename(s["worktree"]) or s["worktree"],
                     s["branch"] or "(detached)", base, _ago(s["seconds_since_last_event"]), state or "-"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  " + "  ".join(v.ljust(w) for v, w in zip(rows[0], widths)).rstrip()]
    for row, s in zip(rows[1:], sessions):
        lines.append("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip())
        summary = _summary(s["changes"])
        if summary:
            lines.append("    " + summary)
        for d in s["declared"]:
            lines.append("    " + _declared_line(d))
        for c in s["claims"]:
            lines.append("    " + _claim_line(c))
    return lines
