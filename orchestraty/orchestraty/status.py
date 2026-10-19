"""What ``orch status`` reports: the coordinator, the watched repositories and their sessions.

``collect`` reads the record without writing to it, so it works while the coordinator runs
and when it does not; ``render`` turns the result into text. The JSON form (``--json``) is
``collect``'s result as it is, with a schema number for other tools to check.
"""
import json
import os
import time

from orchestraty import config
from orchestraty.coordinator.lock import is_fresh
from orchestraty.coordinator.record import Record, repo_key, repo_name

STATUS_SCHEMA = 1
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


def _session(s, now, changes):
    files, unattributed, symbols = changes
    return {"id": s["id"], "agent": s["agent"], "worktree": s["worktree"], "branch": s["branch"],
            "base_commit": s["base_commit"], "base_guessed": bool(s["base_guessed"]),
            "state": s["state"], "turn": s["turn"], "note": s["note"],
            "started": _iso(s["started"] / NS), "last_event": _iso(s["last_seen"] / NS),
            "seconds_since_last_event": max(0, int(now - s["last_seen"] / NS)),
            "changes": {"files": files, "unattributed": unattributed, "symbols": symbols}}


def collect(state, include_ended=False, now=None):
    now = time.time() if now is None else now
    cfg = config.load(state)
    data = {"schema": STATUS_SCHEMA, "coordinator": _coordinator(state, now), "repositories": []}
    sessions_by_repo, repo_rows, changes = {}, {}, {}
    if os.path.exists(state.record):
        record = Record(state.record, readonly=True)
        try:
            repo_rows = {r["key"]: r for r in record.repos()}
            states = None if include_ended else ("live",)
            for s in record.sessions(states=states):
                sessions_by_repo.setdefault(s["repo_id"], []).append(s)
                changes[s["id"]] = record.changes(s["id"])
        finally:
            record.close()
    for common_dir in cfg["repos"]:
        row = repo_rows.get(repo_key(common_dir))
        sessions = sessions_by_repo.get(row["id"], []) if row else []
        data["repositories"].append({
            "name": repo_name(common_dir), "common_dir": common_dir,
            "sessions": [_session(s, now, changes.get(s["id"], ({}, [], [])))
                         for s in sorted(sessions, key=lambda s: (s["started"], s["id"]))]})
    return data


def _summary(changes, limit=4):
    """One line: the files a session changed and the functions whose contract it changed."""
    files, symbols = changes["files"], changes["symbols"]
    if not files and not changes["unattributed"]:
        return ""
    names = sorted(files)
    parts = ["files: " + ", ".join(names[:limit]) + (f" (+{len(names) - limit} more)" if len(names) > limit else "")]
    if changes["unattributed"]:
        parts.append(f"{len(changes['unattributed'])} not attributed")
    marked = [c for c in symbols if c["contract"] in ("breaking", "review")]
    modified = [c for c in symbols if c["contract"] not in ("breaking", "review") and c["kind"] != "added"]
    if marked:
        shown = [f"{c['symbol'].split('::')[-1]} [{c['contract']}: {'; '.join(c['reasons'][:2])}]"
                 for c in sorted(marked, key=lambda c: (c["contract"] != "breaking", c["symbol"]))[:limit]]
        parts.append("contracts: " + ", ".join(shown) + (f" (+{len(marked) - limit} more)" if len(marked) > limit else ""))
    if modified:
        parts.append(f"{len(modified)} other functions changed")
    return "; ".join(parts)


def _ago(seconds):
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def render(data):
    c = data["coordinator"]
    if c["running"]:
        lines = [f"Coordinator: running (pid {c.get('pid', '?')}, since {c.get('since', '?')})"]
    else:
        lines = ["Coordinator: not running (a hook starts it with the next event)"]
    if not data["repositories"]:
        lines += ["", "No repository is watched. Run orch init (or orch watch) inside one."]
        return "\n".join(lines) + "\n"
    for repo in data["repositories"]:
        lines += ["", f"{repo['name']}  ({repo['common_dir']})"]
        if not repo["sessions"]:
            lines.append("  no live sessions")
            continue
        rows = [("session", "agent", "worktree", "branch", "base", "last event", "state")]
        for s in repo["sessions"]:
            state = s["turn"] if s["state"] == "live" else (s["note"] or s["state"])
            base = (s["base_commit"][:9] or "-") + ("?" if s["base_guessed"] else "")
            rows.append((s["id"][:8], s["agent"], os.path.basename(s["worktree"]) or s["worktree"],
                         s["branch"] or "(detached)", base, _ago(s["seconds_since_last_event"]), state or "-"))
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        lines.append("  " + "  ".join(v.ljust(w) for v, w in zip(rows[0], widths)).rstrip())
        for row, s in zip(rows[1:], repo["sessions"]):
            lines.append("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip())
            summary = _summary(s["changes"])
            if summary:
                lines.append("    " + summary)
    if any(s["base_guessed"] for r in data["repositories"] for s in r["sessions"]):
        lines += ["", "base?: the session was first seen after it started, so its base is the commit at that time."]
    return "\n".join(lines) + "\n"
