"""Sessions to repositories, and the parallel pairs among them.

A session belongs to every repository it edited a file in. A file is placed by its own folder:
``git rev-parse`` there gives the working tree's top and the repository's common git folder, so
all worktrees of one repository count as one repository. A file whose folder is gone (a removed
worktree, a deleted folder) cannot be placed and is counted as unmapped.

Two sessions are a parallel pair when they edited the same repository and their time ranges
(first event to last event) overlap, as the protocol says. Whether their active stretches also
overlap is kept beside it.
"""
import os
import subprocess

GIT = ["git", "-c", "maintenance.auto=false", "-c", "core.fsmonitor=false", "-c", "log.showSignature=false"]


def git_code(cwd, *args):
    """(exit status, standard output as text) of one git command."""
    proc = subprocess.run(GIT + list(args), cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def git(cwd, *args, check=True):
    """git's standard output as text, or None when it fails and check is False."""
    proc = subprocess.run(GIT + list(args), cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True)
    if proc.returncode:
        if check:
            raise RuntimeError("git %s failed in %s: %s" % (args[0], cwd, proc.stderr.decode("utf-8", "replace")[:200]))
        return None
    return proc.stdout.decode("utf-8", "replace")


def _key(path):
    return os.path.normcase(os.path.realpath(path))


class Places:
    """Folder -> (working tree top, repository key) through git, asked once per folder."""

    def __init__(self):
        self._dirs = {}

    def of_folder(self, folder):
        if folder in self._dirs:
            return self._dirs[folder]
        found = None
        if os.path.isdir(folder):
            out = git(folder, "rev-parse", "--show-toplevel", "--git-common-dir", check=False)
            lines = (out or "").splitlines()
            if len(lines) == 2:
                top = os.path.normpath(lines[0])
                common = lines[1] if os.path.isabs(lines[1]) else os.path.join(folder, lines[1])
                found = (top, _key(common))
        self._dirs[folder] = found
        return found

    def of_file(self, path):
        """(top, repository key, posix path inside the working tree) or None."""
        place = self.of_folder(os.path.dirname(os.path.normpath(path)))
        if place is None:
            return None
        top, repo = place
        rel = os.path.relpath(os.path.normpath(path), top)
        if rel.startswith(".."):
            return None
        return top, repo, rel.replace(os.sep, "/")


class Work:
    """One session's work in one repository."""

    def __init__(self, session, repo):
        self.session = session
        self.repo = repo
        self.tops = {}              # working tree top -> number of edits there
        self.edits = []             # (time, posix path, line ranges, tool)

    @property
    def top(self):
        """The working tree it edited most in."""
        return max(sorted(self.tops), key=self.tops.get)

    @property
    def files(self):
        return sorted({p for _, p, _, _ in self.edits})


def place_sessions(sessions, places=None):
    """({repository key: {session id: Work}}, unmapped edit count)."""
    places = places or Places()
    repos, unmapped = {}, 0
    for sid, s in sessions.items():
        for e in s.edits:
            where = places.of_file(e.path)
            if where is None:
                unmapped += 1
                continue
            top, repo, rel = where
            w = repos.setdefault(repo, {}).get(sid)
            if w is None:
                w = repos[repo][sid] = Work(s, repo)
            w.tops[top] = w.tops.get(top, 0) + 1
            w.edits.append((e.time, rel, e.lines, e.tool))
    return repos, unmapped


def _overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def parallel_pairs(repos):
    """[(repository key, Work a, Work b, active)] for every pair of sessions in one repository
    whose time ranges overlap; `active` says whether their active stretches overlap too."""
    out = []
    for repo, works in sorted(repos.items()):
        ids = sorted(works)
        for i, x in enumerate(ids):
            for y in ids[i + 1:]:
                a, b = works[x].session, works[y].session
                if _overlap((a.start, a.end), (b.start, b.end)):
                    active = any(_overlap(p, q) for p in a.stretches for q in b.stretches)
                    out.append((repo, works[x], works[y], active))
    return out
