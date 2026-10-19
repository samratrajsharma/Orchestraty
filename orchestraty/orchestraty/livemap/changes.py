"""What git says about a worktree: the files changed since a commit, the worktrees of a
repository, and the blobs of files at a commit. Read-only: no index refresh is written."""
import os

from orchestraty import gitio

DIFF_OPTIONS = ["--no-ext-diff", "--no-textconv", "--no-renames", "--name-status", "-z"]


def _paths(raw):
    return [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]


def changed_files(worktree, base):
    """{posix path: "A" | "M" | "D"} for the files the worktree differs in from commit
    `base`: changes committed since, staged or not, and untracked files git does not ignore.
    With no base (a branch with no commit yet) every file is new."""
    out = {}
    if base:
        parts = _paths(gitio.run(["diff"] + DIFF_OPTIONS + [base, "--"], worktree, text=False))
        for status, path in zip(parts[0::2], parts[1::2]):
            out[path] = status[:1] if status[:1] in ("A", "D") else "M"
    else:
        for path in _paths(gitio.run(["ls-files", "-z"], worktree, text=False)):
            out[path] = "A"
    for path in _paths(gitio.run(["ls-files", "--others", "--exclude-standard", "-z"], worktree, text=False)):
        out[path] = "A"
    return out


def worktrees(worktree):
    """[{"path", "head", "branch"}] for every worktree of the repository holding `worktree`
    (bare and prunable entries left out)."""
    raw = gitio.run(["worktree", "list", "--porcelain", "-z"], worktree, text=False)
    out, entry = [], {}
    for field in raw.split(b"\0") + [b""]:
        text = field.decode("utf-8", "surrogateescape")
        if not text:
            if entry.get("path") and not entry.get("bare") and not entry.get("prunable"):
                out.append({"path": os.path.normpath(entry["path"]), "head": entry.get("HEAD", ""),
                            "branch": entry.get("branch", "").replace("refs/heads/", "", 1)})
            entry = {}
            continue
        key, _, value = text.partition(" ")
        entry["path" if key == "worktree" else key] = value or True
    return out


def blob_ids(worktree, commit, paths):
    """{posix path: blob id} for those of `paths` that exist as files at `commit`."""
    if not paths or not commit:
        return {}
    out, ordered = {}, sorted(paths)
    for start in range(0, len(ordered), 200):            # keep each command line short
        raw = gitio.run(["ls-tree", "-z", "--full-tree", commit, "--"] + ordered[start:start + 200], worktree,
                        text=False)
        for record in raw.split(b"\0"):
            meta, _, path = record.partition(b"\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == b"blob":
                out[path.decode("utf-8", "surrogateescape")] = parts[2].decode("ascii")
    return out


def merge_base(worktree, a, b):
    try:
        return gitio.run(["merge-base", a, b], worktree).strip()
    except gitio.GitError:
        return ""
