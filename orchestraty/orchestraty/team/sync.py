"""Team sync's part of the coordinator: the record goes with the user's own push, and teammates'
records are read from the remote every few minutes.

On the level-0 timer, and every step for two minutes after git's pre-push hook ran, the
coordinator looks for the user's push in git's reflog (``pushwatch``). A push sets this device's
record push 3 seconds later, so a ``git push --all`` can finish; each further push moves it on.
The push then runs on a thread of its own, under the shadow's lock (waiting at most 30 seconds):
the record is sealed, checked again, written to the outbox and pushed with a lease. Nothing
new, and a remote that has it already, sends nothing. What each push came to goes back to the
coordinator, which keeps it in the record (``team_sent``, and ``team_withheld`` for what was
withheld). At most one record commit goes per user push; the record is never written on a
change of the live map.

The team fetch runs on a thread too, at the coordinator's start, every ``team_fetch_minutes``
(a fifth more or less, so teammates' coordinators do not fetch in step), right after this
device's own push, and only while the coordinator runs. It brings every writer's record ref and
the branches records name; when it finds names it did not bring, it fetches those at once. A
failed fetch keeps what was read and waits twice as long each time, up to an hour. Reading,
which needs no network, takes each record through the incoming gate (only when its tip
changed) and keeps every writer in ``team_writers``; this device's own record is skipped.
"""
import json
import queue
import random
import threading
from collections import namedtuple

from orchestraty import config, remote, team
from orchestraty.team import admit, commands, identity, people, pushwatch, store, teamfile, view

NS = 1_000_000_000
PUSH_SETTLE_SECONDS = 3             # after a push, before the record goes: git push --all may go on
ARM_SECONDS = 120                   # after the pre-push hook's line, the reflog is read every step this long
BUSY_RETRY_SECONDS = 30             # the shadow's lock was held: the record goes this much later
JITTER = 0.2                        # each fetch interval is a fifth longer or shorter, at random
MAX_BACKOFF_SECONDS = 3600

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
        ssh = remote._ssh_configured(common_dir)
        branch = store.BranchStore(shadow, chosen["remote"], sealed.record.person, sealed.record.device, email)
        if (teamfile.read(shadow).get("store") or "ref") == "ref":
            sent = outbox.push(url, now, ssh)
            if sent.kind == "refused":                   # the host refuses custom refs: the records branch
                teamfile.update(shadow, store="branch")
                sent = _to_branch(branch, outbox, shadow, url, now, cfg, ssh)
        else:
            sent = _to_branch(branch, outbox, shadow, url, now, cfg, ssh)
        kept = teamfile.read(shadow).get("push") or {}
        ok = sent.kind in ("pushed", "unchanged")
        teamfile.update(shadow, push={"at": int(now), "ok": ok, "error": "" if ok else sent.detail[:300],
                                      "failures": 0 if ok else int(kept.get("failures") or 0) + 1})
        result = sent.kind if ok or not sent.detail else f"{sent.kind}: {sent.detail}"
        return outcome(result, sealed, outbox.tip())


def _to_branch(branch, outbox, shadow, url, now, cfg, ssh):
    """Push the outbox's last record to the records branch; the outbox remembers it as pushed."""
    tip = outbox.tip()
    rc, data = shadow.git(["cat-file", "blob", f"{tip}:record.json"], raw=True)
    if rc != 0:
        return store.Sent("failed", "the outbox's last record cannot be read")
    from orchestraty.team import recordfile
    sent = branch.push(url, data, recordfile.parse_structure(data).record, now, cfg["team_stale_days"], ssh)
    if sent.kind in ("pushed", "unchanged"):
        teamfile.update(shadow, last_pushed=tip)
    return sent


def _seq(tip):
    """The seq a tip's record says, 0 for one that cannot be read."""
    from orchestraty.team import recordfile
    try:
        return recordfile.parse_structure(tip.blob).record.seq if tip.blob is not None else 0
    except recordfile.Refused:
        return 0


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
        self._started = set()           # repositories whose first team fetch this coordinator made
        self._fetch_now = set()         # repositories whose team fetch is due at once
        self._names = {}                # common_dir -> the branches the last admitted records name
        self._gates = {}                # common_dir -> (key, people, effective, me, device)
        self._read = {}                 # common_dir -> (what was read, [(person, device, Tip, Admitted)])
        self._stores = {}               # common_dir -> {(person, device): "ref" or "branch"}
        self.fetched = set()            # repositories whose fetch finished since the coordinator looked

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
            if outcome.result == "pushed":
                self._fetch_now.add(common_dir)              # read again at once: the lease rests on it

    # the team fetch
    def fetch_if_due(self, common_dir, remote_name, interval_seconds, now):
        """Start the team fetch on its thread when one is due and none is running: at this
        coordinator's start, after this device's push, or when the last one said. True if started."""
        if self._running("fetch", common_dir):
            return False
        if common_dir in self._started and common_dir not in self._fetch_now:
            due = (teamfile.read(remote.Shadow(self.state, common_dir)).get("fetch") or {}).get("next")
            if isinstance(due, (int, float)) and now / NS < due:
                return False
        self._started.add(common_dir)
        self._fetch_now.discard(common_dir)
        thread = threading.Thread(target=self._fetch, args=(common_dir, remote_name, interval_seconds, now),
                                  name="team-fetch", daemon=True)
        self._threads[("fetch", common_dir)] = thread
        thread.start()
        return True

    def fetch_now(self, common_dir, remote_name, interval_seconds, now):
        """The team fetch, here and now (orch team fetch), as the thread would make it."""
        self._fetch(common_dir, remote_name, interval_seconds, now)

    def _fetch(self, common_dir, remote_name, interval_seconds, now):
        shadow = remote.Shadow(self.state, common_dir)
        try:
            with store.locked(shadow):
                ok, writers = self._fetch_once(shadow, common_dir, remote_name, now)
                kept = teamfile.read(shadow).get("fetch") or {}
                failures = int(kept.get("failures") or 0)
                wait = (interval_seconds * random.uniform(1 - JITTER, 1 + JITTER) if ok
                        else min(interval_seconds * 2 ** failures, MAX_BACKOFF_SECONDS))
                teamfile.update(shadow, fetch=dict(kept, next=now / NS + wait,
                                                   writers=writers if ok else kept.get("writers", 0)))
        except store.Busy:
            self._fetch_now.add(common_dir)
        except Exception:                                    # never let a fetch take the coordinator down
            if self.log is None:
                raise
            self.log.exception("the team fetch of %s failed", common_dir)
        finally:
            self.fetched.add(common_dir)

    def _fetch_once(self, shadow, common_dir, remote_name, now):
        """One fetch, and a second one for branches it found named and did not bring. (ok, writers)."""
        try:
            url = remote.remote_url(common_dir, remote_name)
        except remote.RemoteError as exc:
            kept = teamfile.read(shadow).get("fetch") or {}
            teamfile.update(shadow, fetch=dict(kept, at=int(now / NS), ok=False, error=str(exc)[:300],
                                               failures=int(kept.get("failures") or 0) + 1))
            return False, 0
        ssh = remote._ssh_configured(common_dir)
        names = set(self._names.get(common_dir, ()))
        if not store.fetch_records(shadow, url, remote_name, now / NS, names, ssh):
            return False, 0
        found = self._named(shadow, common_dir, remote_name, now)
        if found - names and not store.fetch_records(shadow, url, remote_name, now / NS, found, ssh):
            return False, 0
        store.drop_unnamed(shadow, remote_name, found)
        self._names[common_dir] = found
        if self.log is not None:
            self.log.info("team fetch of %s: %d writers, %d branches named", common_dir,
                          len(store.read_writers(shadow, remote_name)), len(found))
        return True, len(store.read_writers(shadow, remote_name))

    def _named(self, shadow, common_dir, remote_name, now):
        """The branches the records just fetched name, as the incoming gate admits them."""
        found = set()
        for _, _, _, verdict in self._admitted(shadow, common_dir, remote_name, now, {}):
            for e in verdict.record.sessions if verdict.record is not None else ():
                if e.pushed is not None and e.pushed.ref.startswith("refs/heads/"):
                    found.add(e.pushed.ref[len("refs/heads/"):])
        return set(sorted(found)[:store.MAX_NAMED])

    # reading
    def records(self, record, common_dir, remote_name, now):
        """[(person, device, Tip, Admitted)] of the writers last fetched, this device left out,
        through the incoming gate. Records are read again only when a tip, the policy or the
        people file changed; each writer's row in team_writers is kept then. No network."""
        shadow = remote.Shadow(self.state, common_dir)
        gate = self._gate(shadow, common_dir, remote_name)
        if gate is None:
            return []
        refs = store.read_writers(shadow, remote_name)
        seen = (gate[0], tuple(refs), store._rev(shadow, store.STORE.format(remote_name)))
        kept = self._read.get(common_dir)
        if kept is not None and kept[0] == seen:
            return kept[1]
        repo_id = record.repo_id(common_dir)
        tips = {(p, d): c for p, d, _, c in refs}
        last_seqs = {(w["person"], w["device"]): w["seq"] for w in record.team_writers(repo_id)
                     if w["commit_id"] != tips.get((w["person"], w["device"]))}   # a tip read before is no rollback
        out = self._admitted(shadow, common_dir, remote_name, now, last_seqs, refs)
        stores = self._stores.get(common_dir, {})
        with record.transaction():
            for person, device, tip, verdict in out:
                record.put_team_writer(admit.writer_row(repo_id, remote_name, person, device, tip, verdict, now / NS,
                                                        stores.get((person, device), "ref")))
        self._read[common_dir] = (seen, out)
        return out

    def me(self, common_dir):
        """This machine's person for a repository, as its records were last read ("" before)."""
        gate = self._gates.get(common_dir)
        return gate[3].person if gate is not None else ""

    def sync_on(self, common_dir):
        """Whether the effective policy lets team sync run in a repository, as last read."""
        gate = self._gates.get(common_dir)
        return gate is not None and not gate[2].off()

    def fetched_names(self, common_dir):
        """The branches the last team fetch of this coordinator asked for, None before one worked."""
        return self._names.get(common_dir)

    def _admitted(self, shadow, common_dir, remote_name, now, last_seqs, refs=None):
        gate = self._gate(shadow, common_dir, remote_name)
        if gate is None:
            return []
        _, folks, eff, me, device = gate
        refs = store.read_writers(shadow, remote_name) if refs is None else refs
        mine = [(p, d, r) for p, d, r, _ in refs if (p, d) != (me.person, device.id)]
        tips, stores = {}, {}
        for person, device_id, tip in admit.read_tips(shadow, mine):
            tips[(person, device_id)], stores[(person, device_id)] = tip, "ref"
        for person, device_id, tip in store.read_branch_writers(shadow, remote_name):
            if (person, device_id) == (me.person, device.id):
                continue
            held = tips.get((person, device_id))
            if held is None or _seq(tip) > _seq(held):          # a writer in both stores: the higher seq wins
                tips[(person, device_id)], stores[(person, device_id)] = tip, "branch"
        self._stores[common_dir] = stores
        return admit.admit_all([(p, d, t) for (p, d), t in sorted(tips.items())], folks, eff, last_seqs, now / NS,
                               admit.exists_in(shadow, f"refs/orch/{remote_name}/HEAD"))

    def _gate(self, shadow, common_dir, remote_name):
        """(key, people, effective policy, me, device) for reading a repository's records: worked
        out again when the default branch as fetched or the repository's team settings change."""
        rc, head = shadow.git(["rev-parse", "--verify", "--quiet", f"refs/orch/{remote_name}/HEAD"]) \
            if shadow.exists() else (1, "")
        cfg = config.load(self.state)
        key = (head.strip() if rc == 0 else "", json.dumps(team.settings(cfg, common_dir), sort_keys=True))
        kept = self._gates.get(common_dir)
        if kept is not None and kept[0] == key:
            return kept
        try:
            _, _, _, _, eff = commands.effective_for(self.state, common_dir, cfg)
            folks = view.people_in_force(shadow, remote_name) or people.People.empty()
            me = identity.who_am_i(folks, identity.my_email(common_dir))
            device = identity.Device.load(self.state)
        except (identity.NoEmail, identity.DeviceError, people.Error, commands.Refused):
            return None
        if device is None:
            return None
        gate = (key, folks, eff, me, device)
        self._gates[common_dir] = gate
        return gate

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
