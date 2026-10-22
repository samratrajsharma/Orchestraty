"""Overlaps by rule for each parallel pair, with the product's own rule code, and what followed.

For a pair (A, B) the two sessions become live-map views: the files each edited (from its log)
and the function changes it committed (``history_changes``). ``orchestraty.rules.overlap``
then finds O1 (same file), O2 (same function), O3 (a breaking change used in a file the other
changed), O4 (a removed function still used there) and O6 (callers neither session updated).
The code graph of a session's copy is the engine's index of its starting commit with the files
it committed put in. Latent findings (a use in a file the other did not change) do not count.

What followed, within FOLLOW_DAYS after the later session ended:
* conflict: both sessions committed, a branch holds both, and merging them conflicts;
* revert: a commit whose message says it reverts one of their commits;
* fix: another commit changes a function the overlap involves.
"""
import tempfile

from orch_engine.config import Config
from orch_engine.pipeline import build_index_at
from orchestraty.livemap.index import view
from orchestraty.livemap.map import SessionView, analyse
from orchestraty.rules.overlap import BREAKING, evaluate

from history_changes import files_at
from history_repos import git, git_code

STRONG = ("O3", "O4", "O6")
FOLLOW_DAYS = 14
PYTHON = (".py",)


class Indexes:
    """The engine's index per (working tree, commit), built once; nothing is cached on disk."""

    def __init__(self):
        self._data = tempfile.mkdtemp(prefix="overlap-history-")
        self._config = Config(data_dir=self._data, use_cache=False)
        self._built = {}
        self.failed = 0

    def get(self, top, commit):
        key = (top, commit)
        if key not in self._built:
            try:
                self._built[key] = build_index_at(top, commit, self._config, use_cache=False)
            except Exception:                     # a commit gone, an unreadable repository
                self._built[key] = None
                self.failed += 1
        return self._built[key]


def _view(change):
    w = change.work
    # The rules tell two sessions in one working tree by their views' folder: a pull request
    # read from a shared clone names its own place there, as same_worktree() tells them apart.
    v = SessionView(w.session.id, w.repo, getattr(w, "place", w.top), change.start or "")
    v.files = {p: "M" for p in w.files}
    v.symbols = dict(change.symbols)
    return v


def _graph(change, indexes, with_own=True):
    if not change.start:
        return None
    idx = indexes.get(change.work.top, change.start)
    if idx is None:
        return None
    if not (with_own and change.committed):
        return idx.graph
    py = [p for p in change.work.files if p.endswith(PYTHON)]
    return view(idx, files_at(change.work.top, change.end, py)) if py else idx.graph


def same_worktree(a, b):
    """True for two sessions in one working tree. Work that names a place of its own (a pull
    request read from a shared clone) is told apart by that place instead."""
    return getattr(a.work, "place", a.work.top) == getattr(b.work, "place", b.work.top)


def findings_for(a, b, indexes):
    """The findings for one pair of Changes, latent ones left out. Two sessions in one working
    tree share its commits, so what each changed in a function cannot be told apart: for them
    only the files each edited count (O1), as the product marks such changes not attributed.
    A code graph is built only where a breaking change needs it: the other side's copy (O3,
    O4) and the changer's base commit (O6)."""
    changes = {a.work.session.id: a, b.work.session.id: b}
    views = {sid: _view(c) for sid, c in changes.items()}
    if same_worktree(a, b):
        for v in views.values():
            v.symbols = {}
    broke = {sid for sid, v in views.items() if any(s.contract == BREAKING for s in v.symbols.values())}
    graphs = {sid: _graph(c, indexes) if broke - {sid} else None for sid, c in changes.items()}
    bases = {sid: _graph(c, indexes, with_own=False) if sid in broke else None for sid, c in changes.items()}
    out = evaluate(views, lambda sid: graphs.get(sid), lambda sid: bases.get(sid))
    return [f for f in out if not f.detail.get("latent")]


def involved(f):
    """The symbols a finding is about: its target and the uses or callers it names."""
    names = {f.symbol} if f.symbol else set()
    names.update(u["symbol"] for u in f.detail.get("uses", []))
    names.update(c["symbol"] for c in f.detail.get("left", []))
    return names


def _contains_both(top, x, y):
    out = git(top, "for-each-ref", "--format=%(objectname)", "--contains", x, check=False) or ""
    tips = set(out.split())
    if not tips:
        return False
    out = git(top, "for-each-ref", "--format=%(objectname)", "--contains", y, check=False) or ""
    return bool(tips & set(out.split()))


def _conflicts(top, x, y):
    """True when merging x and y conflicts, False when it is clean, None when git cannot say
    (merge-tree --write-tree needs git 2.38 or later)."""
    code, _ = git_code(top, "merge-tree", "--write-tree", "--no-messages", x, y)
    return {0: False, 1: True}.get(code)


def followed(a, b, findings, now):
    """{"conflict", "revert", "fix", "window_days"}: conflict is None when git cannot tell;
    window_days is how much of the follow-up window has passed."""
    top = a.work.top
    since = max(a.work.session.end, b.work.session.end)
    until = min(since + FOLLOW_DAYS * 86400, now)
    out = {"conflict": False, "revert": False, "fix": False, "window_days": round((until - since) / 86400, 1)}
    if a.committed and b.committed and _contains_both(top, a.end, b.end):
        out["conflict"] = _conflicts(top, a.end, b.end)
    wanted = set().union(*(involved(f) for f in findings)) if findings else set()
    out["revert"], out["fix"] = later(top, set(a.commits) | set(b.commits), since, until, wanted)
    return out


def later(top, own, since, until, wanted, refs=("--all",), read=files_at):
    """(revert, fix) among the commits on `refs` dated from `since` to `until`, those in `own`
    left out: a commit whose message says it reverts one of `own`, and one that changes a
    function in `wanted`. `read` gives files at a commit, as ``files_at`` does."""
    revert = fix = False
    log = git(top, "log", *refs, "--since=%d" % int(since), "--until=%d" % int(until),
              "--format=%H%x1f%B%x1e", check=False) or ""
    shas = []
    for record in log.split("\x1e"):
        sha, _, body = record.strip().partition("\x1f")
        if not sha or sha in own:
            continue
        shas.append(sha)
        if any(("reverts commit " + c) in body for c in own):
            revert = True
    for sha in shas:
        if fix or not wanted:
            break
        files = (git(top, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha, check=False) or "").split()
        hit = sorted({s.split("::", 1)[0] for s in wanted} & set(files))
        hit = [p for p in hit if p.endswith(PYTHON)]
        if not hit:
            continue
        parent = (git(top, "rev-parse", "--verify", "--quiet", sha + "^", check=False) or "").strip()
        before = read(top, parent, hit) if parent else {p: None for p in hit}
        changed = analyse(before, read(top, sha, hit))
        if any((c.moved_from or k) in wanted and c.kind != "added" for k, c in changed.items()):
            fix = True
    return revert, fix
