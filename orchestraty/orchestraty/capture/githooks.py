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
* every other hook only runs the earlier folder's hook of that name, when there is one then.
  Each hook git looks for by name has one (``PASS_THROUGH``), so a hook added to the earlier
  folder after ``orch init`` runs too; but those whose presence alone changes what git does
  (push-to-checkout, proc-receive) and those git runs at every ref update or index write
  (reference-transaction, post-index-change: a shell start each time) have one only when the
  earlier folder has them. ``unchained`` names any such hook added since; ``orch init`` again
  adds it, and ``orch doctor`` says so.

The earlier folder is the one git itself works out (``git rev-parse --git-path hooks``: a ``~``
expanded, a relative path taken from the worktree's top). The earlier ``core.hooksPath``
value, and the config it was set in, are kept in the chain folder; ``uninstall`` puts the value
back in this repository's own config only when it was set there, and otherwise only takes ours
out, so a value from the user's global config is never copied into the repository's.
A hook manager that reinstalls itself later (npm install running husky, lefthook install)
replaces the chain; running ``orch init`` again puts it back in front.
"""
import os
import shutil
import stat

from orchestraty import gitio

CHAIN_DIR = "orch-hooks"
ORIGINAL_FILE = "original-hooks-path"          # the earlier core.hooksPath value ("" if unset), then its scope
HOOKS = ("pre-push",)
PASS_THROUGH = ("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
                "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
                "pre-receive", "update", "post-receive", "post-update", "pre-auto-gc", "post-rewrite",
                "sendemail-validate", "p4-changelist", "p4-prepare-changelist", "p4-post-changelist",
                "p4-pre-submit")


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
    """(core.hooksPath as git reads it in `top`, the config it is set in: local, global, system,
    worktree or command), or ("", "") when it is not set."""
    try:
        line = gitio.run(["config", "--show-scope", "--get", "core.hooksPath"], top).rstrip("\r\n")
    except gitio.GitError:
        return "", ""
    scope, _, value = line.partition("\t")
    return value.strip(), scope


def _hooks_folder(top, common, value):
    """The folder git runs hooks from in `top` when core.hooksPath is `value` (none: "")."""
    if not value:
        return os.path.join(common, "hooks")
    out = gitio.run(["-c", "core.hooksPath=" + value, "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
                    top)
    return os.path.normpath(out.strip())


def _read_original(chain):
    """(the earlier value, its scope) kept in the chain folder; the scope is "" in a chain from
    before it was kept. Raises OSError when there is none."""
    with open(os.path.join(chain, ORIGINAL_FILE), "r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh.read().splitlines()]
    return (lines + ["", ""])[0], (lines + ["", ""])[1]


def _is_chain(top, chain, value):
    return bool(value) and os.path.normcase(os.path.abspath(os.path.join(top, value))) == os.path.normcase(chain)


def chain_folder(worktree):
    return os.path.join(_paths(worktree)[1], CHAIN_DIR)


def installed(worktree):
    top, common = _paths(worktree)
    return _is_chain(top, os.path.join(common, CHAIN_DIR), _current_hooks_path(top)[0])


def install(worktree, python, script):
    """Put the chain in front of the repository's hooks (again: keeping the first earlier value,
    with a pass-through for every hook the earlier folder has now). Returns the chain folder."""
    top, common = _paths(worktree)
    chain = os.path.join(common, CHAIN_DIR)
    current, scope = _current_hooks_path(top)
    if _is_chain(top, chain, current):
        current, scope = _read_original(chain)      # installed before: keep the first original
    original_dir = _hooks_folder(top, common, current)
    os.makedirs(chain, exist_ok=True)
    with open(os.path.join(chain, ORIGINAL_FILE), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(current + "\n" + scope + "\n")
    wanted = set(_hook_names(original_dir)) | set(HOOKS) | set(PASS_THROUGH)
    for gone in set(_hook_names(chain)) - wanted - {ORIGINAL_FILE}:   # its hook is gone: one whose presence
        os.remove(os.path.join(chain, gone))              # alone changes what git does must go too
    for hook in sorted(wanted):
        original = os.path.join(original_dir, hook)
        text = dispatcher(hook, original, python, script) if hook in HOOKS else dispatcher(hook, original)
        path = os.path.join(chain, hook)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _set_hooks_path(top, _posix(chain))
    return chain


def unchained(worktree):
    """The hooks of the earlier folder that the chain does not run (added there since the chain
    was made, of a name it has no pass-through for ahead of time); [] when it is not installed."""
    top, common = _paths(worktree)
    chain = os.path.join(common, CHAIN_DIR)
    if not _is_chain(top, chain, _current_hooks_path(top)[0]):
        return []
    try:
        value, _ = _read_original(chain)
    except OSError:
        return []
    earlier = _hooks_folder(top, common, value)
    return [h for h in _hook_names(earlier) if not os.path.isfile(os.path.join(chain, h))]


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
        value, scope = _read_original(chain)
    except OSError:
        return False
    if installed(worktree):
        _set_hooks_path(top, "")                   # ours out of this repository's own config
        if value and (scope == "local" or (not scope and _current_hooks_path(top)[0] != value)):
            _set_hooks_path(top, value)            # the earlier value was this repository's own
    shutil.rmtree(chain, ignore_errors=True)
    return True
