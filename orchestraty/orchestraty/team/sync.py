"""Team sync's part of the coordinator: the record goes with the user's own push.

On the level-0 timer, and every step for two minutes after git's pre-push hook ran, the
coordinator looks for the user's push in git's reflog (``pushwatch``). A push sets this device's
record push 3 seconds later, so a ``git push --all`` can finish; each further push moves it on.
The push then runs on a thread of its own, under the shadow's lock (waiting at most 30 seconds):
the record is sealed, checked again, written to the outbox and pushed with a lease. Nothing
new, and a remote that has it already, sends nothing. What each push came to goes back to the
coordinator, which keeps it in the record (``team_sent``, and ``team_withheld`` for what was
withheld). At most one record commit goes per user push; the record is never written on a
change of the live map.
"""
import queue
import threading
from collections import namedtuple

from orchestraty import config, remote, team
from orchestraty.team import commands, identity, pushwatch, store, teamfile

NS = 1_000_000_000
PUSH_SETTLE_SECONDS = 3             # after a push, before the record goes: git push --all may go on
ARM_SECONDS = 120                   # after the pre-push hook's line, the reflog is read every step this long
BUSY_RETRY_SECONDS = 30             # the shadow's lock was held: the record goes this much later

Outcome = namedtuple("Outcome", "common_dir remote store ts seq commit size sessions result withheld")


def send(state, common_dir, now, now_ns):
    """Seal, write and push this device's record of a repository now, under the shadow's lock.
    An Outcome, or None when there was nothing to send (team sync off here, or nothing new
    and the remote has it). Raises store.Busy when another fetch or push holds the shadow."""
    cfg = config.load(state)
    chosen = team.settings(cfg, common_dir)
    if not chosen["on"]:
        return None
    shadow = remote.Shadow(state, common_dir)

    def outcome(result, sealed=None, commit=""):
        rec = sealed.record if sealed is not None else None
        return Outcome(common_dir, chosen["remote"], teamfile.read(shadow).get("store") or "ref", now_ns,
                       rec.seq if rec else 0, commit, len(sealed.data) if sealed is not None and sealed.data else 0,
                       len(rec.sessions) if rec else 0, result, tuple(sealed.withheld) if sealed is not None else ())

    with store.locked(shadow):
        try:
            sealed = commands.sealed_now(state, common_dir, now)
            email = identity.my_email(common_dir)
        except (commands.Refused, identity.NoEmail) as exc:
            return outcome(f"held: {exc}")
        if sealed.data is None:
            return outcome(f"held: {sealed.held}", sealed)
        outbox = store.Outbox(shadow, chosen["remote"], sealed.record.person, sealed.record.device, email)
        try:
            commit = outbox.write(sealed, now, cfg["team_compact_commits"])
        except store.Clash as exc:
            return outcome(f"held: {exc}", sealed)
        if commit is None and outbox.tip() == teamfile.read(shadow).get("last_pushed"):
            return None                                  # nothing new, and the remote has it
        try:
            url = remote.push_url(common_dir, chosen["remote"])
        except remote.RemoteError as exc:
            return outcome(f"failed: {exc}", sealed, outbox.tip())
        sent = outbox.push(url, now, remote._ssh_configured(common_dir))
        kept = teamfile.read(shadow).get("push") or {}
        ok = sent.kind in ("pushed", "unchanged")
        teamfile.update(shadow, push={"at": int(now), "ok": ok, "error": "" if ok else sent.detail[:300],
                                      "failures": 0 if ok else int(kept.get("failures") or 0) + 1})
        result = sent.kind if ok or not sent.detail else f"{sent.kind}: {sent.detail}"
        return outcome(result, sealed, outbox.tip())


class TeamSync:
    """Team sync's work for the coordinator: each repository's push watch, and this device's
    record push on a thread once the user pushed. Made only once a repository has team sync on,
    so a solo user's coordinator never has one."""

    def __init__(self, state, log, now_ns):
        self.state, self.log, self.now_ns = state, log, now_ns
        self._watches = {}              # common_dir -> PushWatch
        self._due = {}                  # common_dir -> when (ns) this device's record goes
        self._armed = {}                # common_dir -> until when (ns) the reflog is read every step
        self._threads = {}              # (what, common_dir) -> its thread
        self._outcomes = queue.Queue()

    def arm(self, common_dir, now):
        """git's pre-push hook ran: read the reflog every step for a while."""
        self._armed[common_dir] = now + ARM_SECONDS * NS

    def look(self, common_dir, remote_name, now):
        """Look for the user's push to the team remote. One found sets the record push 3 s on."""
        watch = self._watches.get(common_dir)
        if watch is None or watch.remote != remote_name:
            watch = pushwatch.PushWatch(remote.Shadow(self.state, common_dir), common_dir, remote_name)
            self._watches[common_dir] = watch
        try:
            pushed = watch.check()
        except OSError as exc:
            self.log.warning("the reflog of %s could not be read: %s", common_dir, exc)
            return False
        if pushed:
            self._due[common_dir] = now + PUSH_SETTLE_SECONDS * NS
            self.log.info("a push to %s from %s: its record goes in %d s", remote_name, common_dir,
                          PUSH_SETTLE_SECONDS)
        return pushed

    def tick(self, repos, now):
        """Every step: read the reflog of the repositories armed, and push where due. `repos`
        is {common_dir: remote name} of those with team sync on."""
        for common_dir, until in list(self._armed.items()):
            if until < now or common_dir not in repos:
                del self._armed[common_dir]
            else:
                self.look(common_dir, repos[common_dir], now)
        for common_dir in list(self._due):
            if common_dir not in repos:
                del self._due[common_dir]
            else:
                self.push_if_due(common_dir, now)

    def push_if_due(self, common_dir, now):
        """Start the record push on its thread when it is due and none is running."""
        due = self._due.get(common_dir)
        if due is None or now < due or self._running("push", common_dir):
            return False
        del self._due[common_dir]
        thread = threading.Thread(target=self._push, args=(common_dir, now), name="team-push", daemon=True)
        self._threads[("push", common_dir)] = thread
        thread.start()
        return True

    def _push(self, common_dir, now):
        try:
            outcome = send(self.state, common_dir, now / NS, now)
        except store.Busy:
            self._due[common_dir] = self.now_ns() + BUSY_RETRY_SECONDS * NS
            return
        except Exception:                                    # never let a push take the coordinator down
            self.log.exception("sending the team record of %s failed", common_dir)
            return
        if outcome is not None:
            self.log.info("team record of %s: %s (seq %d)", common_dir, outcome.result, outcome.seq)
            self._outcomes.put(outcome)

    def outcomes(self):
        """What the push threads came to since the last call, for the coordinator to keep."""
        out = []
        while True:
            try:
                out.append(self._outcomes.get_nowait())
            except queue.Empty:
                return out

    def pending(self):
        """Whether a record push is due or running."""
        return bool(self._due) or any(t.is_alive() for t in self._threads.values())

    def _running(self, what, common_dir):
        thread = self._threads.get((what, common_dir))
        return thread is not None and thread.is_alive()

    def wait(self, timeout=None):
        """Wait for the running threads (tests)."""
        for thread in list(self._threads.values()):
            thread.join(timeout)
