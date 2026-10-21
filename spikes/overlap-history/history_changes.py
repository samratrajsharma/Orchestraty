"""What each session changed in a repository: its starting commit, the commits it made, and
the function and class changes between them, rated by the product's contract classifier.

* Starting commit: the HEAD of the session's working tree when the session started, read from
  that working tree's HEAD reflog (each worktree keeps its own); before the reflog's first entry,
  the last commit on HEAD dated before the start.
* Its commits: the reflog entries of that working tree made by a commit, between the session's
  start and GRACE seconds after its end. The last one is the session's end commit.
* Its changes: the engine's diff of the Python files the session edited, between the starting
  commit and the end commit, classified as the live map does (``orchestraty.livemap.map.analyse``).
  A session that committed nothing has its files but no function changes ("uncommitted").
"""
from orch_engine.ingest import blob_text, read_blobs
from orch_engine.parsing import parse_file
from orchestraty.livemap.map import analyse

from history_repos import git

GRACE = 10 * 60
PYTHON = (".py",)
ARG_BUDGET = 16000          # characters of paths per git call: Windows caps a command line at 32,767


def reflog(top):
    """[(time, sha, subject)] for the working tree's HEAD, oldest first."""
    out = git(top, "log", "-g", "--date=unix", "--format=%gd%x09%H%x09%gs", "HEAD", check=False) or ""
    entries = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2 or "@{" not in parts[0]:
            continue
        try:
            when = float(parts[0].rsplit("@{", 1)[1].rstrip("}"))
        except ValueError:
            continue
        entries.append((when, parts[1], parts[2] if len(parts) > 2 else ""))
    return sorted(entries, key=lambda e: e[0])


def start_commit(top, when, log=None):
    """The commit HEAD pointed at in this working tree at `when`, or None."""
    log = reflog(top) if log is None else log
    before = [sha for t, sha, _ in log if t <= when]
    if before:
        return before[-1]
    out = git(top, "rev-list", "-1", "--before=%d" % int(when), "HEAD", check=False)
    return (out or "").strip() or None


def own_commits(top, start, end, log=None):
    """The shas this working tree's HEAD moved to by a commit between start and end + GRACE."""
    log = reflog(top) if log is None else log
    return [sha for t, sha, subject in log
            if start <= t <= end + GRACE and subject.startswith("commit")]


def _batches(paths):
    """The paths in lists that each fit on one command line."""
    batch, size = [], 0
    for p in paths:
        if batch and size + len(p) + 3 > ARG_BUDGET:
            yield batch
            batch, size = [], 0
        batch.append(p)
        size += len(p) + 3
    if batch:
        yield batch


def files_at(top, commit, paths):
    """{path: ParsedFile or None} for the given posix paths at `commit`."""
    blobs = {}
    for batch in _batches(paths):
        out = git(top, "ls-tree", "-z", commit, "--", *batch, check=False) or ""
        for record in out.split("\0"):
            meta, _, path = record.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                blobs[path] = parts[2]
    data = read_blobs(top, list(blobs.values())) if blobs else {}
    return {p: (parse_file(p, None, text=blob_text(data[blobs[p]])) if p in blobs else None) for p in paths}


class Change:
    """One session's change in one repository."""

    def __init__(self, work):
        self.work = work
        self.start = None           # starting commit
        self.end = None             # its last commit in the window, or None
        self.commits = []
        self.symbols = {}           # symbol id -> SymbolChange (empty when nothing was committed)

    @property
    def committed(self):
        return self.end is not None


def change_of(work):
    """The Change for one session's Work."""
    c = Change(work)
    top, s = work.top, work.session
    log = reflog(top)
    c.start = start_commit(top, s.start, log)
    c.commits = own_commits(top, s.start, s.end, log)
    c.end = c.commits[-1] if c.commits else None
    py = [p for p in work.files if p.endswith(PYTHON)]
    if c.start and c.end and py:
        c.symbols = analyse(files_at(top, c.start, py), files_at(top, c.end, py))
    return c
