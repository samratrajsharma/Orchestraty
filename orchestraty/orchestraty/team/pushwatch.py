"""Whether the user pushed to the team remote, from git's own reflog: no hook is needed.

git writes "update by push" in a remote-tracking ref's reflog on every push that updates it, and
never for a push that failed or was refused, nor for a fetch (whose lines say "fetch: ...").
``PushWatch`` reads the new bytes of ``<common_dir>/logs/refs/remotes/<remote>/**`` the way the
spool reader reads the spool, keeping its offsets in ``shadows/<key>.team.json``:

* a file that shrank or was replaced is read again from its start;
* only lines newer than the last push handled count, and none older than a day, so pushes made
  while no coordinator ran are caught up at its next start;
* its first look starts at the files' ends: a push made before team sync was on sends nothing.

Where git keeps no such reflog (``core.logAllRefUpdates=false``, or a reftable repository), a
change of the remote-tracking refs while every FETCH_HEAD stayed as it was means a push.
"""
import hashlib
import os
import re
import time

from orchestraty import gitio
from orchestraty.team import teamfile

DAY = 86400
MAX_READ = 1 << 20                  # bytes of one reflog read at a time
_PUSH = re.compile(rb"^\S+ \S+ .*> (\d+) [+-]\d{4}\tupdate by push$")


class PushWatch:
    """The user's pushes to one remote of one repository."""

    def __init__(self, shadow, common_dir, remote_name):
        self.shadow, self.common_dir, self.remote = shadow, common_dir, remote_name
        self.folder = os.path.join(common_dir, "logs", "refs", "remotes", remote_name)
        self._reflogs = None

    def check(self, now=None):
        """True when the user pushed to the remote since the last push handled. `now` is the
        wall clock, which git's reflog uses too."""
        now = time.time() if now is None else now
        if self._reflogs is None:
            self._reflogs = _reflogs_on(self.common_dir)
        return self._read_reflogs(now) if self._reflogs else self._compare_refs()

    def _read_reflogs(self, now):
        kept = teamfile.read(self.shadow).get("reflog") or {}
        offsets, last = kept.get("offsets") or {}, kept.get("last_handled")
        first = not isinstance(last, int)
        found, newest, seen = False, (0 if first else last), {}
        for path in _files(self.folder):
            name = os.path.relpath(path, self.folder).replace(os.sep, "/")
            try:
                st = os.stat(path)
            except OSError:
                continue
            at = offsets.get(name) if isinstance(offsets.get(name), dict) else {}
            offset = at.get("offset") if at.get("ino") == st.st_ino and isinstance(at.get("offset"), int) else 0
            if first or offset > st.st_size:
                offset = st.st_size if first else 0
            if offset < st.st_size:
                with open(path, "rb") as fh:
                    fh.seek(offset)
                    data = fh.read(MAX_READ)
                end = data.rfind(b"\n") + 1                   # a line still being written waits
                for line in data[:end].splitlines():
                    m = _PUSH.match(line)
                    when = int(m.group(1)) if m else 0
                    if m and when >= last and when > now - DAY:     # the same second as the last: a new line
                        found, newest = True, max(newest, when)
                offset += end
            seen[name] = {"offset": offset, "ino": st.st_ino}
        after = {"offsets": seen, "last_handled": newest if not first else int(now)}
        if after != kept:
            teamfile.update(self.shadow, reflog=after)
        return found

    def _compare_refs(self):
        try:
            listing = gitio.run(["for-each-ref", "--format=%(refname) %(objectname)", f"refs/remotes/{self.remote}/"],
                                self.common_dir)
        except gitio.GitError:
            return False
        seen = {"refs": hashlib.sha1(listing.encode("utf-8", "surrogateescape")).hexdigest(),
                "fetch_head": _fetch_heads(self.common_dir)}
        kept = teamfile.read(self.shadow).get("refs_seen")
        if seen != kept:
            teamfile.update(self.shadow, refs_seen=seen)
        return (isinstance(kept, dict) and kept.get("refs") != seen["refs"]
                and kept.get("fetch_head") == seen["fetch_head"])


def _reflogs_on(common_dir):
    """Whether git writes reflogs for remote-tracking refs here."""
    if os.path.isdir(os.path.join(common_dir, "reftable")):
        return False
    try:
        value = gitio.run(["config", "--bool", "--get", "core.logAllRefUpdates"], common_dir).strip()
    except gitio.GitError:
        value = ""
    return value != "false"


def _files(folder):
    for top, _, names in os.walk(folder):
        for name in sorted(names):
            yield os.path.join(top, name)


def _fetch_heads(common_dir):
    """The newest FETCH_HEAD time of the repository and its worktrees (each worktree has its own)."""
    paths = [os.path.join(common_dir, "FETCH_HEAD")]
    try:
        paths += [os.path.join(common_dir, "worktrees", n, "FETCH_HEAD")
                  for n in os.listdir(os.path.join(common_dir, "worktrees"))]
    except OSError:
        pass
    times = []
    for path in paths:
        try:
            times.append(os.stat(path).st_mtime_ns)
        except OSError:
            pass
    return max(times, default=0)
