"""Where this device's records go on the team remote, and the team fetch's reading of them.

**The outbox.** This device's chain for one repository lives in the shadow, under
``refs/orch-out/<remote>/record``. Each record is one commit whose tree holds only
``record.json``, made with git's plumbing in the shadow, so it goes into the shadow's own objects
and the user's repository is never written. The commit is named by the person's handle and git
email, dated in UTC and never signed. A record whose content is what the outbox already holds
makes no commit. Before a write, a chain of ``team_compact_commits`` commits, or one whose root
is 30 days old, starts again from a root commit.

**The push** goes to ``refs/orchestraty/<person>/<device>`` on the team remote, as a connection of
its own that never prompts, with a lease on the value the last team fetch read there: it can
only replace what this device last saw. There is no push before a team fetch has read the remote.
A remote value this device never wrote means another machine writes as this device (a copied
state folder): the outbox then stops until ``orch team device --new`` gives one of them its own.

**The records branch** is the fallback for a host that refuses custom refs (it hides them, or a
hook says no): ``refs/heads/orchestraty/records``, holding ``records/<person>/<device>.json`` for
each writer, in the same JSON. A writer switches to it after its first refusal and keeps that
choice beside the shadow; readers always read both stores, the higher seq winning. Each commit
says ``orchestraty records: <person>/<device> <seq> [skip ci]``, which the usual CI systems
honour, and the branch holds no project file for one that does not. A writer that finds more
than 500 commits there starts it again from an orphan commit, leaving out records older than
four times ``team_stale_days``.

Callers hold the shadow's lock (``locked``), which the cloud fetch shares.
"""
import os
import time
from collections import namedtuple
from contextlib import contextmanager

from orchestraty import gitio, remote
from orchestraty.coordinator.lock import Lock
from orchestraty.team import grammar, recordfile, seal, teamfile

OUTBOX = "refs/orch-out/{}/record"
RECORDS = "refs/orch-team/{}/records"
STORE = "refs/orch-team/{}/store"
HEADS = "refs/orch-team/{}/heads"
MAX_NAMED = 100                   # teammates' branches one fetch brings
BRANCH = "refs/heads/orchestraty/records"
BRANCH_COMPACT_COMMITS = 500      # past this, a writer starts the records branch again from an orphan commit
BRANCH_TRIES = 3
LOCK_WAIT = 30
COMPACT_COMMITS = 100
COMPACT_DAYS = 30
DAY = 86400

Sent = namedtuple("Sent", "kind detail")      # kind: pushed, unchanged, stale, refused, failed or held


class Busy(Exception):
    """Another fetch or push holds the shadow's lock."""


class Clash(Exception):
    """Another machine writes records as this device."""


@contextmanager
def locked(shadow, wait=LOCK_WAIT):
    """Hold the shadow's lock, waiting at most `wait` seconds for it. Raises Busy."""
    os.makedirs(shadow.folder, exist_ok=True)
    lock = Lock(shadow.lock_path)
    deadline = time.monotonic() + wait
    while not lock.acquire():
        if time.monotonic() >= deadline:
            raise Busy("another fetch or push holds the shadow")
        time.sleep(0.2)
    try:
        yield
    finally:
        lock.release()


def clash_message(device):
    return f"another machine writes as device {device}: run orch team device --new on one of them"


def refspecs(remote_name, names=()):
    """The team fetch's refspecs: the default branch, every writer's record ref, the records
    branch and the branches records name (at most 100). A glob never fails for a ref that is
    not there, so each named branch is one: `feat/x*` brings feat/x (and siblings, never used)."""
    out = [f"+HEAD:refs/orch/{remote_name}/HEAD", f"+refs/orchestraty/*:{RECORDS.format(remote_name)}/*",
           f"+refs/heads/orchestraty/records*:{STORE.format(remote_name)}*"]
    return out + [f"+refs/heads/{n}*:{HEADS.format(remote_name)}/{n}*" for n in sorted(names)[:MAX_NAMED]]


def fetch_records(shadow, url, remote_name, now, names=(), ssh_configured=False):
    """Fetch the remote's default branch, every writer's record ref and the branches `names`
    into the shadow, in one connection, and keep how it went in the team file. True when it
    worked. --prune touches only these refspecs' destinations: never the cloud fetch's refs."""
    refspecs_now = refspecs(remote_name, names)
    try:
        try:
            shadow.fetch_refspecs(url, refspecs_now, ssh_configured=ssh_configured)
        except remote.ShadowBroken:
            shadow.mark_broken(now)
            shadow.fetch_refspecs(url, refspecs_now, ssh_configured=ssh_configured)
    except (remote.RemoteError, remote.ShadowBroken, gitio.GitError, OSError) as exc:
        kept = teamfile.read(shadow).get("fetch") or {}
        teamfile.update(shadow, fetch=dict(kept, at=int(now), ok=False, error=remote._clean_error(str(exc))[:300],
                                           failures=int(kept.get("failures") or 0) + 1))
        return False
    kept = teamfile.read(shadow).get("fetch") or {}
    teamfile.update(shadow, fetch=dict(kept, at=int(now), ok=True, error="", failures=0, last_ok=int(now)))
    return True


def read_writers(shadow, remote_name):
    """[(person, device, ref, commit)] of the writers the last fetch brought, sorted. A ref there
    of any other depth, or whose person or device no writer could have, is not read at all."""
    if not shadow.exists():
        return []
    prefix = RECORDS.format(remote_name) + "/"
    rc, out = shadow.git(["for-each-ref", "--format=%(refname) %(objectname)", prefix])
    found = []
    for line in out.splitlines() if rc == 0 else []:
        ref, _, commit = line.partition(" ")
        parts = ref[len(prefix):].split("/")
        if len(parts) == 2 and grammar.person(parts[0]) and grammar.device(parts[1]):
            found.append((parts[0], parts[1], ref, commit))
    return sorted(found)


def drop_unnamed(shadow, remote_name, names):
    """Delete the shadow's copies of teammates' branches that no record names any more (and the
    siblings a glob brought)."""
    prefix = HEADS.format(remote_name) + "/"
    rc, out = shadow.git(["for-each-ref", "--format=%(refname)", prefix]) if shadow.exists() else (1, "")
    for ref in out.split() if rc == 0 else []:
        if ref[len(prefix):] not in names:
            shadow.git(["update-ref", "-d", ref])


def read_branch_writers(shadow, remote_name):
    """[(person, device, admit.Tip)] of the records in the records branch as last fetched: each
    file records/<person>/<device>.json, under the commit that last changed it. A file of any
    other place or name is not read, nor one over 256 KiB."""
    from orchestraty.team.admit import Tip
    tip = _rev(shadow, STORE.format(remote_name))
    if not tip:
        return []
    rc, out = shadow.git(["ls-tree", "-r", "-l", "-z", tip, "--", "records/"])
    found = []
    for entry in out.split("\0") if rc == 0 else []:
        meta, tab, path = entry.partition("\t")
        fields, parts = meta.split(), path.split("/")
        if not tab or len(fields) != 4 or fields[:2] != ["100644", "blob"] or len(parts) != 3:
            continue
        person, device = parts[1], parts[2][:-len(".json")] if parts[2].endswith(".json") else ""
        if not (grammar.person(person) and grammar.device(device)):
            continue
        rc, log = shadow.git(["log", "-1", "--format=%H %ae", tip, "--", path])
        commit, _, author = log.strip().partition(" ")
        if int(fields[3]) > recordfile.MAX_BYTES:
            found.append((person, device, Tip(commit, author, None, "too-large")))
            continue
        rc, data = shadow.git(["cat-file", "blob", fields[2]], raw=True)
        found.append((person, device, Tip(commit, author, data if rc == 0 else None, "" if rc == 0 else "not-a-record")))
    return sorted(found, key=lambda x: (x[0], x[1]))


def _rev(shadow, ref):
    if not shadow.exists():
        return ""
    rc, out = shadow.git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    return out.strip() if rc == 0 else ""


class BranchStore:
    """This device's record in the records branch, for a host that refuses custom refs."""

    def __init__(self, shadow, remote_name, person, device, email):
        self.shadow, self.remote_name = shadow, remote_name
        self.person, self.device, self.email = person, device, email
        self.seen = STORE.format(remote_name)          # the branch's tip at the last fetch
        self.path = f"records/{person}/{device}.json"

    def push(self, url, data, record, now, stale_days, ssh_configured=False):
        """Write the record's bytes as this writer's file on the branch and push it with a lease
        on the tip it was built on: a writer that pushed meanwhile makes it fetch and build again,
        at most three times. Returns a Sent (pushed, unchanged, stale, refused or failed)."""
        result = None
        for _ in range(BRANCH_TRIES):
            if not self._fetch_branch(url, ssh_configured):
                return Sent("failed", "the records branch could not be fetched")
            tip = _rev(self.shadow, self.seen)
            commit = self._commit(tip, data, record, now, stale_days)
            result = self.shadow.push(url, commit, BRANCH, tip, ssh_configured=ssh_configured)
            if result.kind in ("new", "fast-forward", "forced", "up to date"):
                self.shadow.git(["update-ref", self.seen, commit])
                return Sent("pushed", "")
            if result.kind != "stale":
                return Sent("refused" if result.kind == "refused" else "failed", result.detail)
        return Sent("stale", result.detail if result else "")

    def _fetch_branch(self, url, ssh_configured):
        try:
            self.shadow.fetch_refspecs(url, [f"+{BRANCH}*:{self.seen}*"], ssh_configured=ssh_configured)
        except (remote.RemoteError, remote.ShadowBroken, gitio.GitError, OSError):
            return False
        return True

    def _commit(self, tip, data, record, now, stale_days):
        """A commit of the branch with this writer's file, on `tip` (or an orphan one holding the
        files still fresh, when the branch has grown past BRANCH_COMPACT_COMMITS)."""
        blob = self._out(["hash-object", "-w", "--stdin"], data=data)
        index = os.path.join(self.shadow.path, "orchestraty-records.index")
        env = {"GIT_INDEX_FILE": index}
        try:
            os.remove(index)
        except FileNotFoundError:
            pass
        compact = bool(tip) and int(self._out(["rev-list", "--count", tip]) or 0) > BRANCH_COMPACT_COMMITS
        if tip and not compact:
            self._out(["read-tree", tip], env=env)
        else:
            self._out(["read-tree", "--empty"], env=env)
            for path, kept in self._fresh(tip, now - 4 * stale_days * DAY) if compact else ():
                self._out(["update-index", "--add", "--cacheinfo", f"100644,{kept},{path}"], env=env)
        self._out(["update-index", "--add", "--cacheinfo", f"100644,{blob},{self.path}"], env=env)
        tree = self._out(["write-tree"], env=env)
        os.remove(index)
        stamp = f"{record.written} +0000"
        names = {"GIT_AUTHOR_NAME": self.person, "GIT_COMMITTER_NAME": self.person, "GIT_AUTHOR_EMAIL": self.email,
                 "GIT_COMMITTER_EMAIL": self.email, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        message = f"orchestraty records: {self.person}/{self.device} {record.seq} [skip ci]"
        return self._out(["commit-tree", "--no-gpg-sign", tree] + (["-p", tip] if tip and not compact else [])
                         + ["-m", message], env=names)

    def _fresh(self, tip, since):
        """(path, blob) of the branch's records written since `since`."""
        rc, out = self.shadow.git(["ls-tree", "-r", "-z", tip, "--", "records/"])
        for entry in out.split("\0") if rc == 0 else []:
            meta, tab, path = entry.partition("\t")
            fields = meta.split()
            if not tab or len(fields) != 3 or path == self.path:
                continue
            rc, data = self.shadow.git(["cat-file", "blob", fields[2]], raw=True)
            try:
                written = recordfile.parse_structure(data).record.written if rc == 0 else 0
            except recordfile.Refused:
                written = 0
            if written >= since:
                yield path, fields[2]

    def _out(self, args, env=None, data=None):
        rc, out = self.shadow.git(args, env=env, data=data)
        if rc != 0:
            raise gitio.GitError(f"git {args[0]} failed in the shadow")
        return out.strip()


class Outbox:
    """This device's record chain for one repository and remote, in the shadow."""

    def __init__(self, shadow, remote_name, person, device, email):
        self.shadow, self.remote_name = shadow, remote_name
        self.person, self.device, self.email = person, device, email
        self.ref = OUTBOX.format(remote_name)
        self.dst = f"refs/orchestraty/{person}/{device}"
        self.seen = f"{RECORDS.format(remote_name)}/{person}/{device}"   # the remote's value at the last fetch

    # reading
    def tip(self):
        """This device's last record commit, "" before the first."""
        return self._commit(self.ref)

    def remote_tip(self):
        """The commit the remote had under this device's ref at the last fetch or push, "" for none."""
        return self._commit(self.seen)

    def parent(self):
        """The commit the next record goes on: this device's tip, when the remote has it or an
        older commit of its chain (or nothing); the remote's, when this device's chain was lost;
        none at first. Raises Clash, and keeps it, when the remote holds a record this device
        never wrote."""
        tip, there = self.tip(), self.remote_tip()
        if not there:
            return tip
        if not tip:
            return there
        if there == tip or there == teamfile.read(self.shadow).get("last_pushed") or self._ancestor(there, tip):
            return tip
        teamfile.update(self.shadow, clash={"device": self.device, "at": int(time.time())})
        raise Clash(clash_message(self.device))

    def next_seq(self):
        """The seq the next record has: one more than its parent's, 1 at first."""
        try:
            parent = self.parent()
        except Clash:
            parent = self.tip()
        return self._seq(parent) + 1 if parent else 1

    def clashed(self):
        kept = teamfile.read(self.shadow).get("clash") or {}
        return kept.get("device") == self.device

    # writing
    def write(self, sealed, now, compact_commits=COMPACT_COMMITS):
        """Write a sealed record as the outbox's next commit. None when it holds nothing the
        last one did not, but `written` and `seq`. Its bytes are checked again first, so only
        what seal() made is ever pushed. Raises seal.SealError, Clash, or gitio.GitError."""
        if sealed.data is None:
            raise seal.SealError(f"the record is held back: {sealed.held}")
        seal.verify(sealed.data)
        record = recordfile.parse_structure(sealed.data).record
        if (record.person, record.device) != (self.person, self.device):
            raise seal.SealError("the record names another writer")
        self.shadow.ensure()
        parent = self.parent()
        before = self._record(parent) if parent else None
        if before is not None and recordfile.content_key(before) == recordfile.content_key(record):
            return None
        expected_seq = before.seq + 1 if before is not None else 1
        if record.seq != expected_seq:
            raise seal.SealError(f"the record's seq is {record.seq}, not the next one ({expected_seq})")
        compact = bool(parent) and self._compaction_due(parent, now, compact_commits)
        blob = self._out(["hash-object", "-w", "--stdin"], data=sealed.data)
        tree = self._out(["mktree"], data=f"100644 blob {blob}\trecord.json\n".encode("ascii"))
        stamp = f"{record.written} +0000"
        env = {"GIT_AUTHOR_NAME": self.person, "GIT_COMMITTER_NAME": self.person, "GIT_AUTHOR_EMAIL": self.email,
               "GIT_COMMITTER_EMAIL": self.email, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        message = f"orchestraty record {record.seq}" + (" (compacted)" if compact else "")
        commit = self._out(["commit-tree", "--no-gpg-sign", tree] + (["-p", parent] if parent and not compact else [])
                           + ["-m", message], env=env)
        self._out(["update-ref", self.ref, commit])
        teamfile.update(self.shadow, seq=record.seq)
        return commit

    def push(self, url, now=None, ssh_configured=False):
        """Push the outbox's tip to the remote with a lease. Returns a Sent: pushed, unchanged
        (the remote had it), stale (the remote's ref was not what the last fetch read: tried once
        more after reading it again), refused (the remote said no), failed (no answer about the
        ref) or held (no push now: no team fetch yet, or another machine writes as this device)."""
        kept = teamfile.read(self.shadow)
        if not (kept.get("fetch") or {}).get("last_ok"):
            return Sent("held", "no team fetch has read the remote yet")
        if self.clashed():
            return Sent("held", clash_message(self.device))
        tip = self.tip()
        if not tip:
            return Sent("unchanged", "nothing written yet")
        for attempt in (1, 2):
            result = self.shadow.push(url, self.ref, self.dst, self.remote_tip(), ssh_configured=ssh_configured)
            if result.kind in ("new", "fast-forward", "forced", "up to date"):
                self._pushed(tip)
                return Sent("pushed" if result.kind != "up to date" else "unchanged", "")
            if result.kind != "stale":
                return Sent("refused" if result.kind == "refused" else "failed", result.detail)
            if not self._fetch_own(url, ssh_configured):
                return Sent("stale", result.detail)
            try:
                self.parent()
            except Clash as exc:
                return Sent("stale", str(exc))
        return Sent("stale", result.detail)

    # plumbing
    def _pushed(self, tip):
        """The remote has `tip` now: the lease of the next push is on it."""
        self._out(["update-ref", self.seen, tip])
        teamfile.update(self.shadow, last_pushed=tip)

    def _fetch_own(self, url, ssh_configured):
        try:
            self.shadow.fetch_refspecs(url, [f"+{self.dst}*:{self.seen}*"], ssh_configured=ssh_configured)
        except (remote.RemoteError, remote.ShadowBroken, gitio.GitError, OSError):
            return False
        return True

    def _compaction_due(self, parent, now, compact_commits):
        """Whether the chain ending at `parent` is long enough, or its root old enough, to start
        again: one git call, the commits' times newest first (the root's is the last)."""
        times = self._out(["log", "--format=%ct", f"--max-count={compact_commits}", parent]).split()
        if len(times) >= compact_commits:
            return True
        return bool(times) and int(times[-1]) < now - COMPACT_DAYS * DAY

    def _commit(self, ref):
        if not self.shadow.exists():
            return ""
        rc, out = self.shadow.git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
        return out.strip() if rc == 0 else ""

    def _ancestor(self, older, newer):
        rc, _ = self.shadow.git(["merge-base", "--is-ancestor", older, newer])
        return rc == 0

    def _record(self, commit):
        rc, data = self.shadow.git(["cat-file", "blob", f"{commit}:record.json"], raw=True)
        if rc != 0:
            return None
        try:
            return recordfile.parse_structure(data).record
        except recordfile.Refused:
            return None

    def _seq(self, commit):
        record = self._record(commit) if commit else None
        return record.seq if record is not None else 0

    def _out(self, args, env=None, data=None):
        rc, out = self.shadow.git(args, env=env, data=data)
        if rc != 0:
            raise gitio.GitError(f"git {args[0]} failed in the shadow")
        return out.strip()
