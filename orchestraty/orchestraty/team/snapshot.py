"""The projection of this machine's sessions of one repository, for the outgoing gate.

``build`` fills ``seal.Projection`` field by field from the record and from git, and from nothing
else: each session of the repository with a working copy here (hooked or plain git), live or ended
within ``team_stale_days``, under the opaque id its device gives it; its agent kind, state, times
and turn, branch and base; the branch's name and tip on the team remote, read from git's own
push and upstream settings and the remote-tracking ref (no network); the ticket in its branch's
name or the one its author gave, the task line its author confirmed, and its live claims; and,
only when the policy shares unpushed work and the branch is not on the team remote, its committed
work (base..HEAD), read from the commits, never from the working copy. Cloud and teammates'
sessions are never shared again, nor a session on a detached HEAD, which no teammate can follow.
"""
import re

from orchestraty import gitio, sessionkinds
from orchestraty.coordinator.record import repo_key
from orchestraty.team import grammar, recordfile, seal

NS = 1_000_000_000
DAY = 86400
_ISSUE = re.compile(r"(?<![A-Za-z0-9])GH-[0-9]{1,7}(?![0-9])")


def build(rec, common_dir, remote_name, device, effective, now, tool, policy_blob, stale_days=14):
    """seal.Projection for one repository's sessions: `rec` is the record (read only), `device`
    the identity.Device, `now` in seconds."""
    repo = next((r for r in rec.repos() if r["key"] == repo_key(common_dir)), None)
    if repo is None:
        return seal.Projection(tool, policy_blob, ())
    held = {}
    for c in rec.claims(active_at=int(now * NS), repo_id=repo["id"]):
        held.setdefault(c["session_id"], []).append(
            recordfile.Claim(c["kind"], c["target"], c["file"], bool(c["hard"]), c["ts"] // NS, c["expires"] // NS))
    out = []
    for s in rec.sessions(repo_id=repo["id"]):
        if s["agent"] in sessionkinds.REMOTE:
            continue                                     # cloud and teammates' sessions: never shared again
        live = s["state"] == "live"
        last_seen = s["last_seen"] // NS
        if not live and last_seen < now - stale_days * DAY:
            continue
        branch = s["branch"] or ""
        if not branch:
            continue                                     # a detached HEAD: nothing a teammate can follow
        confirmed = rec.confirmed(s["id"]) or {}
        on_remote = pushed(common_dir, branch, remote_name)
        out.append(seal.SessionFacts(
            id=device.session_id(s["id"]), agent=s["agent"], state="live" if live else "ended",
            started=s["started"] // NS, last_seen=last_seen, ended=0 if live else last_seen,
            turn=s["turn"] or "", branch=branch, base=s["base_commit"] or "",
            pushed=on_remote,
            ticket=confirmed.get("ticket") or ticket_of(branch, effective), line=confirmed.get("line") or "",
            claims=tuple(held.get(s["id"], ())) if live else (),
            work=work(common_dir, repo["key"], s) if on_remote is None and effective.unpushed else None))
    return seal.Projection(tool, policy_blob, tuple(out))


def pushed(common_dir, branch, remote_name):
    """recordfile.Pushed(ref, tip) for a branch the team remote has, or None when it has not been
    pushed there. The tip is the remote-tracking ref's: what the remote had at the last push or
    fetch (no network)."""
    if not branch:
        return None
    try:
        out = gitio.run(["for-each-ref", "--format=%(push)%00%(push:remotename)%00%(push:remoteref)",
                         "refs/heads/" + branch], common_dir)
    except gitio.GitError:
        return None
    parts = out.strip("\n").split("\0")
    if len(parts) != 3:
        return None
    name = name_on(branch, remote_name, *parts)
    if not name:
        return None
    try:
        tip = gitio.run(["rev-parse", "--verify", "--quiet", f"refs/remotes/{remote_name}/{name}"], common_dir).strip()
    except gitio.GitError:
        return None
    return recordfile.Pushed("refs/heads/" + name, tip) if tip else None


def name_on(branch, remote_name, push_tracking, push_remote, push_ref):
    """The branch's name on `remote_name`, or "" when its pushes go elsewhere. git's own answer
    comes first: a push refspec, else @{push}, the remote-tracking ref of where git push sends the
    branch. An upstream of another name is none: a branch made from origin/main tracks main, and
    push.default simple never pushes it there. git gives no @{push} for a branch pushed to another
    remote than its upstream: it goes there under its own name."""
    if push_remote == remote_name and push_ref.startswith("refs/heads/"):
        return push_ref[len("refs/heads/"):]
    prefix = f"refs/remotes/{remote_name}/"
    if push_tracking:
        return push_tracking[len(prefix):] if push_tracking.startswith(prefix) else ""
    return branch if push_remote == remote_name else ""


def work(common_dir, key, session):
    """recordfile.Work for a session's committed but unpushed work: what changed from its base to
    the commit its worktree has checked out, read from the commits as a teammate's branch is
    (Python's functions only). Never its uncommitted changes. None when there is none, or it
    cannot be read."""
    from orchestraty.livemap.map import branch_view
    base = session["base_commit"] or ""
    if not grammar.sha(base) or not session["worktree"]:
        return None
    try:
        tip = gitio.run(["rev-parse", "-q", "--verify", "HEAD^{commit}"], session["worktree"]).strip()
    except gitio.GitError:
        return None
    if not grammar.sha(tip) or tip == base:
        return None
    view = branch_view(common_dir, key, session["id"], base, tip)
    if view.error:
        return None
    symbols = [_symbol(c) for _, c in sorted(view.symbols.items()) if c.language == "python"]
    return recordfile.Work(tip, tuple(sorted(view.files.items())), tuple(symbols))


def _symbol(change):
    breaks = tuple(sorted((reason, tuple(_condition(c) for c in conditions))
                          for reason, conditions in (change.breaks or {}).items()))
    return recordfile.Symbol(change.symbol, change.kind, change.change, change.contract, tuple(change.reasons),
                             breaks, change.file, tuple(change.lines), change.moved_from or "")


def _condition(c):
    return tuple(tuple(x) if isinstance(x, (list, tuple)) else x for x in c)


def ticket_of(branch, effective):
    """The ticket a branch's name holds, of the policy's keys (or GH-123 when issues are on), or ""."""
    pattern = effective.ticket_pattern()
    m = pattern.search(branch) if pattern else None
    if m:
        return m.group(0)
    m = _ISSUE.search(branch) if effective.ticket_issues else None
    return m.group(0) if m else ""
