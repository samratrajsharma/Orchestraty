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
  earlier folder has them (``WHEN_THERE``). ``unchained`` names any such hook added since;
  ``orch init`` again adds it, and ``orch doctor`` says so. A file of another name there (a
  README, a helper script) is not a hook: git never runs it, and the chain does not either.

The earlier folder is the one git itself works out (``git rev-parse --git-path hooks``: a ``~``
expanded, a relative path taken from the folder git runs the hook in, a worktree's top). A
relative value stays relative in the scripts, so each worktree runs its own copy, as it did
before. The earlier ``core.hooksPath`` value, and the config it was set in, are kept in the
chain folder; ``uninstall`` puts the value back in this repository's own config only when it
was set there, and otherwise only takes ours out, so a value from the user's global config is
not copied into the repository's (a chain from before the config was kept gets back only a
relative value, or one inside the repository). A value set for one worktree only (``git config
--worktree``) comes before the repository's, so the chain would not run there: ``install``
refuses it, and ``uninstall`` takes ours out of the repository's config whatever one worktree
reads.

``orch init`` again (``install`` again) rewrites only the chain's own scripts (the ones with
the ``# Orchestraty:`` line): a file a hook manager wrote into the chain folder (lefthook install
writes into ``core.hooksPath``) is left as it is. A chain folder that is gone while
``core.hooksPath`` still names it (``lost``) is rebuilt in front of ``.git/hooks``, since the
earlier folder is not known then. A hook manager that reinstalls itself later (npm install
running husky) replaces the chain; running ``orch init`` again puts it back in front.
"""
import os
import shlex
import shutil
import stat

from orchestraty import gitio

CHAIN_DIR = "orch-hooks"
ORIGINAL_FILE = "original-hooks-path"          # the earlier core.hooksPath value ("" if unset), then its scope
MARKER = b"\n# Orchestraty:"                    # the second line of every script the chain writes
HOOKS = ("pre-push",)
PASS_THROUGH = ("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
                "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
                "pre-receive", "update", "post-receive", "post-update", "pre-auto-gc", "post-rewrite",
                "sendemail-validate", "p4-changelist", "p4-prepare-changelist", "p4-post-changelist",
                "p4-pre-submit")
WHEN_THERE = ("push-to-checkout", "proc-receive", "reference-transaction", "post-index-change")
WORKTREE_SCOPE = ("core.hooksPath is set for this worktree only (git config --worktree), and git reads that "
                  "before the repository's config, so the push line would not run here")


class Refused(Exception):
    """The push line cannot go into this repository's hooks; the message says why."""


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
    """The hooks git would run that `folder` has: git's hook names only, never a README or a helper."""
    names = set(HOOKS) | set(PASS_THROUGH) | set(WHEN_THERE)
    try:
        return sorted(e.name for e in os.scandir(folder) if e.name in names and e.is_file())
    except OSError:
        return []


def _ours(path):
    """True when the file at `path` is one of the chain's scripts, or is not there."""
    try:
        with open(path, "rb") as fh:
            return MARKER in fh.read(512)
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _relative(value):
    """True for a core.hooksPath git takes from the folder it runs the hook in: not absolute, no ~."""
    return not (value.startswith(("/", "\\", "~")) or os.path.splitdrive(value)[0])


def _within(path, folder):
    path, folder = os.path.normcase(path), os.path.normcase(folder)
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:                             # on another drive
        return False


def _paths(worktree):
    top = gitio.run(["rev-parse", "--path-format=absolute", "--show-toplevel"], worktree).strip()
    common = gitio.run(["rev-parse", "--path-format=absolute", "--git-common-dir"], worktree).strip()
    return os.path.normpath(top), os.path.normpath(common)


def _current_hooks_path(top, where=()):
    """(core.hooksPath as git reads it in `top`, the config it is set in: local, global, system,
    worktree or command), or ("", "") when it is not set. `where` (["--local"]) reads one config."""
    try:
        line = gitio.run(["config", *where, "--show-scope", "--get", "core.hooksPath"], top).rstrip("\r\n")
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


def _installed_chain(worktree):
    """(top, common, chain) when core.hooksPath as git reads it in `worktree` is the chain, else None."""
    top, common = _paths(worktree)
    chain = os.path.join(common, CHAIN_DIR)
    return (top, common, chain) if _is_chain(top, chain, _current_hooks_path(top)[0]) else None


def chain_folder(worktree):
    return os.path.join(_paths(worktree)[1], CHAIN_DIR)


def installed(worktree):
    return _installed_chain(worktree) is not None


def lost(worktree):
    """True when core.hooksPath names the chain folder but the chain is gone (no earlier value is
    kept there), so git runs none of the repository's hooks, or not the ones it should."""
    found = _installed_chain(worktree)
    return found is not None and not os.path.isfile(os.path.join(found[2], ORIGINAL_FILE))


def refusal(worktree):
    """Why the push line cannot go into the hooks of the repository holding `worktree`, or None."""
    top, common = _paths(worktree)
    value, scope = _current_hooks_path(top)
    if scope == "worktree" and not _is_chain(top, os.path.join(common, CHAIN_DIR), value):
        return WORKTREE_SCOPE
    return None


def install(worktree, python, script):
    """Put the chain in front of the repository's hooks (again: keeping the first earlier value,
    with a pass-through for every hook the earlier folder has now, and leaving a file in the chain
    folder that is not one of its scripts). Returns the chain folder. Raises Refused when
    core.hooksPath is set for this worktree only."""
    top, common = _paths(worktree)
    chain = os.path.join(common, CHAIN_DIR)
    current, scope = _current_hooks_path(top)
    if _is_chain(top, chain, current):
        try:
            current, scope = _read_original(chain)  # installed before: keep the first original
        except OSError:
            current, scope = "", ""                 # lost: the earlier folder is not known, so .git/hooks
    elif scope == "worktree":
        raise Refused(WORKTREE_SCOPE)
    original_dir = _hooks_folder(top, common, current)
    base = _posix(current).rstrip("/") if current and _relative(current) else _posix(original_dir)
    os.makedirs(chain, exist_ok=True)
    with open(os.path.join(chain, ORIGINAL_FILE), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(current + "\n" + scope + "\n")
    wanted = set(_hook_names(original_dir)) | set(HOOKS) | set(PASS_THROUGH)
    for gone in set(_hook_names(chain)) - wanted:   # its hook is gone: one whose presence alone changes
        if _ours(os.path.join(chain, gone)):        # what git does must go too
            os.remove(os.path.join(chain, gone))
    for hook in sorted(wanted):
        path = os.path.join(chain, hook)
        if not _ours(path):
            continue                                # a hook manager's own file: left as it is
        original = base + "/" + hook                # a relative one: from the folder git runs the hook in
        text = dispatcher(hook, original, python, script) if hook in HOOKS else dispatcher(hook, original)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _set_hooks_path(top, _posix(chain))
    return chain


def others(chain):
    """The hooks in the chain folder that are not its own scripts (a hook manager wrote them
    there); install leaves them as they are."""
    return [h for h in _hook_names(chain) if not _ours(os.path.join(chain, h))]


def unchained(worktree):
    """The hooks of the earlier folder that the chain does not run (added there since the chain
    was made, of a name it has no pass-through for ahead of time); [] when it is not installed."""
    found = _installed_chain(worktree)
    if found is None:
        return []
    top, common, chain = found
    try:
        value, _ = _read_original(chain)
    except OSError:
        return []
    earlier = _hooks_folder(top, common, value)
    return [h for h in _hook_names(earlier) if not os.path.isfile(os.path.join(chain, h))]


def missing_script(worktree):
    """The hook script the chain's pre-push runs, when that script is not there (its state folder
    was moved or deleted since, and each push prints an error); else None."""
    found = _installed_chain(worktree)
    if found is None:
        return None
    try:
        with open(os.path.join(found[2], "pre-push"), "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        if len(words) > 5 and words[1:3] == ["-S", "-I"] and words[4:6] == ["git", "pre-push"]:
            return None if os.path.isfile(words[3]) else words[3]
    return None


def _set_hooks_path(top, value):
    """Set core.hooksPath, or unset it for an empty value."""
    args = ["config", "core.hooksPath", value] if value else ["config", "--unset", "core.hooksPath"]
    try:
        gitio.run(args, top)
    except gitio.GitError:
        if value:                                  # unsetting what is not set is fine
            raise


def _put_back(top, common, value, scope):
    """True when the earlier value was this repository's own: recorded as set in its config, or, in
    a chain from before the scope was kept, one that git does not read from elsewhere now and that is
    relative or inside the repository, which a value from the global config seldom is."""
    if scope:
        return scope == "local"
    if _current_hooks_path(top)[0] == value:
        return False
    return _relative(value) or any(_within(_hooks_folder(top, common, value), f) for f in (top, common))


def uninstall(worktree):
    """Take the chain out and put the earlier setting back. False when it was not there."""
    top, common = _paths(worktree)
    chain = os.path.join(common, CHAIN_DIR)
    ours = _is_chain(top, chain, _current_hooks_path(top, ["--local"])[0])   # whatever this worktree reads
    try:
        value, scope = _read_original(chain)
    except OSError:
        if not ours:
            return False
        value, scope = "", ""                      # lost: nothing known to put back
    if ours:
        _set_hooks_path(top, "")                   # ours out of this repository's own config
        if value and _put_back(top, common, value, scope):
            _set_hooks_path(top, value)            # the earlier value was this repository's own
    shutil.rmtree(chain, ignore_errors=True)
    return True
