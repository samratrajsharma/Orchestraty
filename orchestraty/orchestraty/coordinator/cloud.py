"""Cloud agents' branches for the coordinator (see ``orchestraty.remote``).

The coordinator fetches each repository's cloud branches every ``remote_fetch_minutes`` (5 by
default) on a thread of its own, so a slow network never holds up a note, and waits longer after
each failure. On its level-0 timer it reads the shadow's refs, which needs no network: each
branch matching the patterns is a session of agent ``cloud``, named by the branch. What a
branch needs from git (its merge base with the default branch, whether that branch contains it,
its last commit's time) is read once per tip.
"""
import hashlib
import threading

from orchestraty import remote
from orchestraty.coordinator.record import repo_key

NS = 1_000_000_000


def session_id(common_dir, remote_name, branch):
    """A cloud branch's session id: stable for the repository, remote and branch."""
    text = "\0".join((repo_key(common_dir), remote_name, branch))
    return "cloud-" + hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()[:12]


class CloudBranches:
    def __init__(self, state, log):
        self.state = state
        self.log = log
        self._threads = {}              # repository key -> its fetch thread
        self._facts = {}                # (shadow, remote, branch) -> ((tip, default tip), facts)
        self.fetched = set()            # repository keys whose fetch finished since the last look

    def fetch_if_due(self, common_dir, chosen, interval_seconds, now):
        """Start a fetch on a thread when one is due and none is running. Returns True if started."""
        key = repo_key(common_dir)
        running = self._threads.get(key)
        if running is not None and running.is_alive():
            return False
        if not remote.Shadow(self.state, common_dir).due(now):
            return False
        thread = threading.Thread(target=self._fetch, args=(common_dir, dict(chosen), interval_seconds, now),
                                  name="cloud-fetch", daemon=True)
        self._threads[key] = thread
        thread.start()
        return True

    def _fetch(self, common_dir, chosen, interval_seconds, now):
        try:
            result = remote.fetch_now(self.state, common_dir, chosen, interval_seconds, now=now)
        except Exception:                                   # never let a fetch take the coordinator down
            self.log.exception("fetching the cloud branches of %s failed", common_dir)
            return
        if result is None:
            return                                          # another fetch held the shadow
        if result["ok"]:
            self.log.info("fetched %s for %s: %d branches match", chosen["remote"], common_dir, result["branches"])
        else:
            self.log.warning("fetching %s for %s failed (%d in a row): %s", chosen["remote"], common_dir,
                             result["failures"], result["error"])
        self.fetched.add(repo_key(common_dir))

    def wait(self, timeout=None):
        """Wait for the running fetches (tests)."""
        for thread in list(self._threads.values()):
            thread.join(timeout)

    def branches(self, common_dir, chosen):
        """{branch: {"tip", "base", "merged", "time"}} for the matching branches as last fetched,
        or None while no fetch has brought the default branch. Raises remote.ShadowBroken."""
        shadow = remote.Shadow(self.state, common_dir)
        tips, default = shadow.refs(chosen["remote"])
        out = {}
        if not default:
            return None
        for name, tip in sorted(tips.items()):
            if not remote.matches(name, chosen["patterns"]):
                continue
            key = (shadow.path, chosen["remote"], name)
            known = self._facts.get(key)
            if known is None or known[0] != (tip, default):
                known = ((tip, default), shadow.branch(tip, default))
                self._facts[key] = known
            out[name] = dict(known[1], tip=tip)
        return out

    def forget(self, common_dir):
        """Drop what was read from a repository's shadow (it is being fetched afresh)."""
        path = remote.Shadow(self.state, common_dir).path
        self._facts = {k: v for k, v in self._facts.items() if k[0] != path}
