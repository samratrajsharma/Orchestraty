"""git for the coordinator: read-only commands, with the engine's safety options and an
environment that cannot point git at another repository.

Commands go through the engine's ``git_command``: no network, no program a repository's own
config could start, and git's ownership check kept (a refusal raises the engine's
``UntrustedRepositoryError``). On top, the variables an outer git sets for its hooks
(``GIT_DIR``, ``GIT_INDEX_FILE`` and the rest) are removed, so a coordinator started from a
hook still reads the folder each command names.

``remove_tree`` deletes a folder git wrote, whose object files git leaves read-only.
"""
import os
import shutil
import stat
import subprocess
import sys

from orch_engine.ingest import UntrustedRepositoryError, check_ownership, git_command

INHERITED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX")
TIMEOUT = 30

__all__ = ["GitError", "UntrustedRepositoryError", "remove_tree", "run", "worktree_info"]


class GitError(Exception):
    """git failed, could not be started, or took too long."""

    def __init__(self, message, *, stderr=""):
        super().__init__(message)
        self.stderr = stderr


def _env(env):
    for name in INHERITED:
        env.pop(name, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"             # never refresh the index: the agent may be using it
    env["LC_ALL"] = "C"                         # classify Git's diagnostics independent of the user's locale
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
        raise GitError(f"git {args[0]} exited {proc.returncode}: {stderr.strip()[:300]}", stderr=stderr)
    return proc.stdout.decode("utf-8", "replace") if text else proc.stdout


def missing():
    """True when no git is on this computer's PATH (GitHub Desktop, for one, keeps its own off it):
    a checkout is then followed as a folder without git, and orch doctor says git is missing."""
    return shutil.which("git") is None


def _has_git_metadata(cwd):
    """Find checkout markers in ancestors, including a linked worktree's .git file."""
    folder = os.path.realpath(cwd)
    while True:
        try:
            os.lstat(os.path.join(folder, ".git"))
        except FileNotFoundError:
            pass
        else:
            return True
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent


def worktree_info(cwd):
    """{"top", "common_dir", "branch", "head"} for the worktree holding `cwd`, or None when
    `cwd` is not inside one. Paths are absolute and in this system's form; branch is "" when
    HEAD is detached, head is "" on a branch with no commit yet. A failed Git probe is
    distinct from a directory outside Git. Plain folders also work without Git installed."""
    if not cwd:
        return None
    try:
        if not stat.S_ISDIR(os.stat(cwd).st_mode):
            return None
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise GitError(f"Could not access {cwd}: {exc}") from exc
    try:
        out = run(["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"], cwd)
    except GitError as exc:
        outside = any(line.startswith("fatal: not a git repository (")
                      for line in exc.stderr.splitlines())
        if outside or isinstance(exc.__cause__, FileNotFoundError):
            try:
                if not _has_git_metadata(cwd):
                    return None
            except OSError:
                pass                          # cannot establish that the directory is outside Git
        raise GitError(f"Could not inspect Git worktree at {cwd}: {exc}", stderr=exc.stderr) from exc
    lines = out.splitlines()
    if len(lines) != 2 or not all(os.path.isabs(p) for p in lines):
        raise GitError(f"Could not inspect Git worktree at {cwd}: unexpected git rev-parse output")
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


def remove_tree(path):
    """Delete a folder git wrote. Git leaves its object files read-only, and on Windows a
    read-only file cannot be deleted: such a file is made writable first."""
    def writable(func, name, exc):
        if func not in (os.unlink, os.remove, os.rmdir):
            raise exc
        os.chmod(name, stat.S_IWRITE)
        func(name)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:
        shutil.rmtree(path, onerror=lambda func, name, info: writable(func, name, info[1]))
