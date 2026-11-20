"""The projection of this machine's sessions of one repository, for the outgoing gate.

``build`` fills ``seal.Projection`` field by field from the record and from git, and from nothing
else: each session of the repository with a working copy here (hooked or plain git), live or ended
within ``team_stale_days``, under the opaque id its device gives it; its agent kind, state, times
and turn, branch and base; the branch's name and tip on the team remote, read from git's own
push and upstream settings and the remote-tracking ref (no network); the ticket in its branch's
name or the one its author gave, the task line its author confirmed, and its live claims. Cloud
and teammates' sessions are never shared again. Unpushed work comes with the live map (2.2).
"""
import re

from orchestraty import gitio, sessionkinds
from orchestraty.coordinator.record import repo_key
from orchestraty.team import recordfile, seal

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
        confirmed = rec.confirmed(s["id"]) or {}
        branch = s["branch"] or ""
        out.append(seal.SessionFacts(
            id=device.session_id(s["id"]), agent=s["agent"], state="live" if live else "ended",
            started=s["started"] // NS, last_seen=last_seen, ended=0 if live else last_seen,
            turn=s["turn"] or "", branch=branch, base=s["base_commit"] or "",
            pushed=pushed(common_dir, branch, remote_name),
            ticket=confirmed.get("ticket") or ticket_of(branch, effective), line=confirmed.get("line") or "",
            claims=tuple(held.get(s["id"], ())) if live else ()))
    return seal.Projection(tool, policy_blob, tuple(out))


def pushed(common_dir, branch, remote_name):
    """recordfile.Pushed(ref, tip) for a branch the team remote has (git's push settings first,
    then its upstream), or None when it has not been pushed there."""
    if not branch:
        return None
    try:
        out = gitio.run(["for-each-ref", "--format=%(push:remotename)%00%(push:remoteref)%00%(upstream:remotename)"
                         "%00%(upstream:remoteref)", "refs/heads/" + branch], common_dir)
    except gitio.GitError:
        return None
    parts = out.strip("\n").split("\0")
    if len(parts) != 4:
        return None
    name, ref = (parts[0], parts[1]) if parts[0] and parts[1] else (parts[2], parts[3])   # push:remoteref is
    # empty under push.default simple, where the upstream says where the branch goes
    if name != remote_name or not ref.startswith("refs/heads/"):
        return None
    try:
        tip = gitio.run(["rev-parse", "--verify", "--quiet", f"refs/remotes/{remote_name}/{ref[len('refs/heads/'):]}"],
                        common_dir).strip()
    except gitio.GitError:
        return None
    return recordfile.Pushed(ref, tip) if tip else None


def ticket_of(branch, effective):
    """The ticket a branch's name holds, of the policy's keys (or GH-123 when issues are on), or ""."""
    pattern = effective.ticket_pattern()
    m = pattern.search(branch) if pattern else None
    if m:
        return m.group(0)
    m = _ISSUE.search(branch) if effective.ticket_issues else None
    return m.group(0) if m else ""
