"""When a teammate's branch counts as merged into the default branch: the first check that holds.

1. ``ancestor``: the branch's tip is an ancestor of the default branch (a merge commit, a fast
   forward, a rebase then a fast forward).
2. ``merge-tree``: merging the branch into the default branch changes nothing, which is what a
   squash, a rebase or a cherry-pick leaves. It needs ``git merge-tree --write-tree`` (git 2.38);
   with an older git this check is left out, and doctor says so.
3. ``patch-id``: the branch's whole change, from its base, is the change of a commit on the
   default branch since that base (at most its newest 500): a squash merge, even after later
   edits to the same code.

Otherwise it is not merged (""). A squash whose conflicts were resolved otherwise than the branch
is not caught: its session ends when the branch is gone, no record names it, or it goes stale.
All of it runs in the shadow, which reads the user's objects; nothing here uses the network.
"""
import functools
import re
import subprocess

from orchestraty import gitio

MAX_COMMITS = 500


@functools.lru_cache(maxsize=1)
def merge_tree_works():
    """Whether this git has merge-tree --write-tree (2.38 or later)."""
    try:
        out = gitio.run(["--version"], ".")
    except (gitio.GitError, OSError, subprocess.SubprocessError):
        return False
    m = re.search(r"(\d+)\.(\d+)", out)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (2, 38)


def merged(shadow, tip, default, base, patch_ids):
    """How the commit `tip` is in the default branch's commit `default`: "ancestor", "merge-tree",
    "patch-id", or "" when it is not. `base` is the branch's merge base with the default branch;
    `patch_ids` keeps the default branch's patch ids between calls (a PatchIds)."""
    rc, _ = shadow.git(["merge-base", "--is-ancestor", tip, default])
    if rc == 0:
        return "ancestor"
    if merge_tree_works():
        rc, out = shadow.git(["merge-tree", "--write-tree", "--no-messages", default, tip])
        rc_tree, tree = shadow.git(["rev-parse", default + "^{tree}"])
        if rc == 0 and rc_tree == 0 and out.split() and out.split()[0] == tree.strip():
            return "merge-tree"
    if base and _patch_merged(shadow, tip, default, base, patch_ids):
        return "patch-id"
    return ""


def _patch_merged(shadow, tip, default, base, patch_ids):
    rc, out = shadow.git(["rev-list", f"--max-count={MAX_COMMITS}", f"{base}..{default}"])
    since = out.split() if rc == 0 else []
    if not since:
        return False
    rc, diff = shadow.git(["diff", "--no-ext-diff", "--no-textconv", base, tip], raw=True)
    if rc != 0 or not diff:
        return False
    rc, out = shadow.git(["patch-id", "--stable"], data=diff)
    mine = out.split()[0] if rc == 0 and out.split() else ""
    known = patch_ids.of(shadow, default)
    return bool(mine) and any(known.get(c) == mine for c in since)


class PatchIds:
    """The patch ids of the default branch's newest commits, by commit, kept per shadow and
    worked out again only for the commits a new default tip adds."""

    def __init__(self):
        self._kept = {}                 # shadow path -> (default tip, {commit: patch id})

    def of(self, shadow, default):
        """{commit: patch id} for the newest 500 commits of `default` that change something."""
        tip, ids = self._kept.get(shadow.path, ("", {}))
        if tip == default:
            return ids
        older = bool(tip) and shadow.git(["merge-base", "--is-ancestor", tip, default])[0] == 0
        span = [f"{tip}..{default}"] if older else [f"--max-count={MAX_COMMITS}", default]
        rc, log = shadow.git(["log", "-p", "--no-ext-diff", "--no-textconv", "--no-color", "--format=commit %H"]
                             + span, raw=True)
        new = {}
        if rc == 0 and log:
            rc, out = shadow.git(["patch-id", "--stable"], data=log)
            for line in out.splitlines() if rc == 0 else []:
                parts = line.split()
                if len(parts) == 2:
                    new[parts[1]] = parts[0]
        ids = dict(ids, **new) if older else new
        if older and len(ids) > MAX_COMMITS:
            rc, out = shadow.git(["rev-list", f"--max-count={MAX_COMMITS}", default])
            newest = set(out.split()) if rc == 0 else set(ids)
            ids = {c: p for c, p in ids.items() if c in newest}
        self._kept[shadow.path] = (default, ids)
        return ids
