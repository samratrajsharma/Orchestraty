"""The git hook chain: a pre-push hook that prints Orchestraty's push line, in front of the
hooks a repository already has.

git runs the hooks of one folder: ``core.hooksPath`` if set (husky sets it), else
``.git/hooks`` (where lefthook and pre-commit put theirs). ``install`` points
``core.hooksPath`` at ``<git common folder>/orch-hooks``, which holds one small sh script per
hook:

* pre-push runs the hook script (``hook.py git pre-push``), which prints the push line if
  there is one and never fails, then runs the pre-push hook that was there before with the
  same arguments and input, and exits with its exit code, so a failing hook still stops the
  push;
* every other hook the earlier folder had only runs that earlier hook.

The earlier ``core.hooksPath`` value is kept in the chain folder and ``uninstall`` puts it back.
A hook manager that reinstalls itself later (npm install running husky, lefthook install)
replaces the chain; running ``orch init`` again puts it back in front.
"""
import os
import shutil
import stat

from orchestraty import gitio

CHAIN_DIR = "orch-hooks"
ORIGINAL_FILE = "original-hooks-path"          # the earlier core.hooksPath value ("" if unset)
HOOKS = ("pre-push",)


def _sh_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _posix(path):
    return path.replace("\\", "/")


def dispatcher(hook, original, python=None, script=None):
    """The sh script for one hook. Without python and script it only passes through."""
    lines = ["#!/bin/sh",
             "# Orchestraty: runs the hook that was here before; for pre-push it first prints a one-line",
             "# reminder of changes another session depends on. It never blocks: the earlier hook decides.",
             "original=" + _sh_quote(_posix(original))]
    if python is not None:
        lines.append(" ".join([_sh_quote(_posix(python)), "-S", "-I", _sh_quote(_posix(script)), "git", hook,
                               '"$@"', "</dev/null"]))
    lines += ['if [ -x "$original" ]; then exec "$original" "$@"; fi', "exit 0"]
    return "\n".join(lines) + "\n"


def _hook_names(folder):
    try:
        return sorted(e.name for e in os.scandir(folder)
                      if e.is_file() and not e.name.endswith(".sample") and "." not in e.name)
    except OSError:
        return []


def _paths(worktree):
    top = gitio.run(["rev-parse", "--path-format=absolute", "--show-toplevel"], worktree).strip()
    common = gitio.run(["rev-parse", "--path-format=absolute", "--git-common-dir"], worktree).strip()
    return os.path.normpath(top), os.path.normpath(common)


def _current_hooks_path(top):
    try:
        return gitio.run(["config", "--get", "core.hooksPath"], top).strip()
    except gitio.GitError:
        return ""


def chain_folder(worktree):
    return os.path.join(_paths(worktree)[1], CHAIN_DIR)


def installed(worktree):
    top, common = _paths(worktree)
    chain = os.path.join(common, CHAIN_DIR)
    current = _current_hooks_path(top)
    return bool(current) and os.path.normcase(os.path.abspath(os.path.join(top, current))) == os.path.normcase(chain)


def install(worktree, python, script):
    """Put the chain in front of the repository's hooks. Returns the chain folder."""
    top, common = _paths(worktree)
    chain = os.path.join(common, CHAIN_DIR)
    current = _current_hooks_path(top)
    if current and os.path.normcase(os.path.abspath(os.path.join(top, current))) == os.path.normcase(chain):
        with open(os.path.join(chain, ORIGINAL_FILE), "r", encoding="utf-8") as fh:
            current = fh.read().strip()             # installed before: keep the first original
    original_dir = os.path.join(top, current) if current else os.path.join(common, "hooks")
    os.makedirs(chain, exist_ok=True)
    with open(os.path.join(chain, ORIGINAL_FILE), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(current + "\n")
    for hook in sorted(set(_hook_names(original_dir)) | set(HOOKS)):
        original = os.path.join(original_dir, hook)
        text = dispatcher(hook, original, python, script) if hook in HOOKS else dispatcher(hook, original)
        path = os.path.join(chain, hook)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _set_hooks_path(top, _posix(chain))
    return chain


def _set_hooks_path(top, value):
    """Set core.hooksPath, or unset it for an empty value."""
    args = ["config", "core.hooksPath", value] if value else ["config", "--unset", "core.hooksPath"]
    try:
        gitio.run(args, top)
    except gitio.GitError:
        if value:                                  # unsetting what is not set is fine
            raise


def uninstall(worktree):
    """Take the chain out and put the earlier setting back. False when it was not there."""
    top, common = _paths(worktree)
    chain = os.path.join(common, CHAIN_DIR)
    try:
        with open(os.path.join(chain, ORIGINAL_FILE), "r", encoding="utf-8") as fh:
            original = fh.read().strip()
    except OSError:
        return False
    if installed(worktree):
        _set_hooks_path(top, original)
    shutil.rmtree(chain, ignore_errors=True)
    return True
