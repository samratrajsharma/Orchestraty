"""Teammates' branches for the coordinator, as sessions of agent ``team`` (see ``team/sync.py``).

Each branch of the team remote that an admitted record names (its session's ``pushed`` ref) is
one session, whoever named it: two people on one branch are one session with both in
``people``, and a person's several devices fold into it. Entries of this machine's own device
never come here; the person's other devices do, as ``own``. The branch is read from the commits
the team fetch brought into the shadow, as a cloud agent's branch is: its tip is the shadow's
ref (so a force push is taken as it is), its base our own merge base with the default branch,
and what a record says of its base or its files is never used for it. What git says of a tip is
worked out once per (tip, default tip). At most ``team_max_sessions`` branches, the newest first.
"""
import hashlib
from collections import namedtuple

from orchestraty import remote, sessionkinds
from orchestraty.coordinator.record import repo_key
from orchestraty.team import merged as merged_ladder
from orchestraty.team import store

TEAM_AGENT = sessionkinds.TEAM

TeamFacts = namedtuple("TeamFacts", "sid branch tip base merged tip_time as_of people own writers agents task claims "
                                    "pending")


def session_id(common_dir, remote_name, branch):
    """A teammate's branch's session id: stable for the repository, remote and branch."""
    text = "\0".join((repo_key(common_dir), remote_name, "b", branch))
    return "team-" + hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()[:12]


class TeamWork:
    def __init__(self, state):
        self.state = state
        self._facts = {}                # (shadow, remote, branch) -> ((tip, default tip), (base, merged, tip time))
        self.patch_ids = merged_ladder.PatchIds()
        self._ancestors = {}            # (shadow, older, newer) -> whether older is an ancestor of newer
        self._apart = {}                # common_dir -> {frozenset((team sid, local sid))}

    def relate(self, common_dir, team, heads):
        """{team sid: (contained_in, builds_on)}: the local sessions whose copy holds a teammate's
        tip (they go on from it), and those whose commits a teammate's branch builds on, which
        also covers a local copy behind on the same branch. Neither pair is compared: each copy
        holds the other's commits, and a naive base..tip would show one person's change as the
        other's. `team` is {sid: (tip, base)} and `heads` {local sid: its worktree's commit}.
        Asked in the shadow, which reads the user's objects, once per pair of commits."""
        shadow = remote.Shadow(self.state, common_dir)
        out, pairs = {}, set()
        for tsid, (tip, base) in sorted(team.items()):
            inside, on = [], []
            for lsid, head in sorted(heads.items()):
                if not tip or not head:
                    continue
                if self._ancestor(shadow, tip, head):
                    inside.append(lsid)
                elif self._ancestor(shadow, head, tip) and not (base and self._ancestor(shadow, head, base)):
                    on.append(lsid)
            out[tsid] = (inside, on)
            pairs.update(frozenset((tsid, lsid)) for lsid in inside + on)
        self._apart[common_dir] = pairs
        return out

    def apart(self, a, b):
        """Whether the rules leave a pair alone (one's copy holds the other's commits)."""
        pair = frozenset((a, b))
        return any(pair in pairs for pairs in self._apart.values())

    @staticmethod
    def evidence(sid):
        """What a teammate's branch was read from: its commits, for every pushed branch."""
        return "commits"

    def _ancestor(self, shadow, older, newer):
        key = (shadow.path, older, newer)
        if key not in self._ancestors:
            rc, _ = shadow.git(["merge-base", "--is-ancestor", older, newer]) if shadow.exists() else (1, "")
            self._ancestors[key] = rc == 0
        return self._ancestors[key]

    def settle(self, common_dir, remote_name, admitted, me, fetched, now, max_sessions=50):
        """{session id: TeamFacts} of the branches the admitted records name. `admitted` is
        TeamSync.records' list, `me` this machine's person, `fetched` the branch names the last
        team fetch brought (None before one did), `now` in seconds. A branch the shadow does not
        have is gone once a fetch asked for it, and pending until then."""
        shadow = remote.Shadow(self.state, common_dir)
        default = _rev(shadow, f"refs/orch/{remote_name}/HEAD")
        if not default:
            return {}
        named = {}
        for person, device, _, verdict in admitted:
            record = verdict.record
            for e in record.sessions if record is not None else ():
                if e.pushed is not None and e.pushed.ref.startswith("refs/heads/"):
                    named.setdefault(e.pushed.ref[len("refs/heads/"):], []).append((person, device, e, record.written))
        out = {}
        for branch, entries in named.items():
            sid = session_id(common_dir, remote_name, branch)
            tip = _rev(shadow, f"{store.HEADS.format(remote_name)}/{branch}")
            base, how, tip_time = self._commit_facts(shadow, remote_name, branch, tip, default) if tip else ("", "", 0)
            ticket = next((e.ticket for _, _, e, _ in entries if e.ticket), "")
            line = next((e.line for _, _, e, _ in entries if e.line), "")
            out[sid] = TeamFacts(
                sid=sid, branch=branch, tip=tip, base=base, merged=how, tip_time=min(tip_time, int(now)),
                as_of=min(max(w for _, _, _, w in entries), int(now)),
                people=tuple(sorted({p for p, _, _, _ in entries if p != me})),
                own=any(p == me for p, _, _, _ in entries),
                writers=tuple({"person": p, "device": d, "session": e.id, "agent": e.agent, "state": e.state,
                               "turn": e.turn, "last_seen": min(e.last_seen, int(now))} for p, d, e, _ in entries),
                agents=tuple(sorted({e.agent for _, _, e, _ in entries})),
                task={k: v for k, v in (("ticket", ticket), ("line", line)) if v},
                claims=tuple((e.id, c) for _, _, e, _ in entries for c in e.claims),
                pending=not tip and (fetched is None or branch not in fetched))
        newest = sorted(out.values(), key=lambda f: (-max(f.as_of, f.tip_time), f.branch))[:max_sessions]
        return {f.sid: f for f in newest}

    def _commit_facts(self, shadow, remote_name, branch, tip, default):
        """(base, how merged, tip's commit time) for one tip, once per (tip, default tip)."""
        key = (shadow.path, remote_name, branch)
        known = self._facts.get(key)
        if known is None or known[0] != (tip, default):
            rc, out = shadow.git(["merge-base", tip, default])
            base = out.strip() if rc == 0 else ""
            how = merged_ladder.merged(shadow, tip, default, base, self.patch_ids)
            rc, out = shadow.git(["log", "-1", "--format=%ct", tip])
            known = ((tip, default), (base, how, int(out.strip()) if rc == 0 and out.strip().isdigit() else 0))
            self._facts[key] = known
        return known[1]


def _rev(shadow, ref):
    if not shadow.exists():
        return ""
    rc, out = shadow.git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    return out.strip() if rc == 0 else ""
