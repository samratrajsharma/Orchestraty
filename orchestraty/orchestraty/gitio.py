"""git for the coordinator: read-only commands, with the engine's safety options and an
environment that cannot point git at another repository.

Commands go through the engine's ``git_command``: no network, no program a repository's own
config could start, and git's ownership check kept (a refusal raises the engine's
``UntrustedRepositoryError``). On top, the variables an outer git sets for its hooks
(``GIT_DIR``, ``GIT_INDEX_FILE`` and the rest) are removed, so a coordinator started from a
hook still reads the folder each command names.
"""
import os
import subprocess

from orch_engine.ingest import UntrustedRepositoryError, check_ownership, git_command

INHERITED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX")
TIMEOUT = 30

__all__ = ["GitError", "UntrustedRepositoryError", "run", "worktree_info"]


class GitError(Exception):
    """git failed, could not be started, or took too long."""


def _env(env):
    for name in INHERITED:
        env.pop(name, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"             # never refresh the index: the agent may be using it
    return env


def run(args, cwd, timeout=TIMEOUT, text=True):
    """stdout of one git command in `cwd`. Raises GitError on a non-zero exit."""
    argv, env = git_command(list(args), cwd)
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout, env=_env(env),
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {args[0]}: {type(exc).__name__}") from exc
    stderr = proc.stderr.decode("utf-8", "replace")
    check_ownership(proc.returncode, stderr, cwd)
    if proc.returncode != 0:
        raise GitError(f"git {args[0]} exited {proc.returncode}: {stderr.strip()[:300]}")
    return proc.stdout.decode("utf-8", "replace") if text else proc.stdout


def worktree_info(cwd):
    """{"top", "common_dir", "branch", "head"} for the worktree holding `cwd`, or None when
    `cwd` is not inside one. Paths are absolute and in this system's form; branch is "" when
    HEAD is detached, head is "" on a branch with no commit yet."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        out = run(["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"], cwd)
    except GitError:
        return None
    lines = out.splitlines()
    if len(lines) < 2 or not lines[0]:
        return None
    try:
        branch = run(["symbolic-ref", "-q", "--short", "HEAD"], cwd).strip()
    except GitError:
        branch = ""
    try:
        head = run(["rev-parse", "-q", "--verify", "HEAD^{commit}"], cwd).strip()
    except GitError:
        head = ""
    return {"top": os.path.normpath(lines[0]), "common_dir": os.path.normpath(lines[1]),
            "branch": branch, "head": head}
