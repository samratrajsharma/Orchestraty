"""Installs the git-hook capture chain into a scratch repository, and removes it again.

Status: throwaway (Phase 0). Not part of the product.

    python capture_githooks.py install   <repo> [--root <phase-0 folder>] [--python <absolute python>]
    python capture_githooks.py uninstall <repo> [--root <phase-0 folder>]

Only repositories under <root>/repos are accepted (scratch repositories, never a real one).

How the chain works. Git runs the hooks in one folder: core.hooksPath if set (husky sets it
to .husky/_), else .git/hooks (where lefthook and pre-commit install theirs). The installer
points core.hooksPath at a new folder inside the repository's git folder,
<git dir>/orch-capture-hooks, which holds one small sh script per hook:
  * for the five captured hooks (post-commit, pre-push, post-checkout, post-merge,
    post-rewrite) the script runs capture_githook.py, silently and ignoring its result, then
    runs the hook that was there before with the same arguments and stdin, and exits with
    that hook's exit code (so a failing pre-push hook still stops the push);
  * for every other hook the earlier folder had, the script only runs the earlier hook.
The earlier setting is kept in the chain folder, and uninstall puts it back. A hook manager
that reinstalls itself later (npm install running husky, lefthook install) replaces the
chain; install the chain after the managers.
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys

import capture_hooks
from capture_githook import HOOKS

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDER = os.path.join(HERE, "capture_githook.py")
CHAIN_DIR = "orch-capture-hooks"
ORIGINAL_FILE = "original-hooks-path"        # the earlier core.hooksPath value ("" if unset)
STDIN_HOOKS = {"pre-push", "post-rewrite"}


class Refused(capture_hooks.Refused):
    pass


def _git(repo, *args):
    proc = subprocess.run(["git", "-c", "maintenance.auto=false", "-C", repo] + list(args),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


def checked_repo(root, repo):
    """The repository's absolute top folder, or Refused: it must sit under <root>/repos with
    no link on the way, and be the top of a git working tree."""
    root = os.path.abspath(root)
    if os.path.basename(root) != capture_hooks.ROOT_NAME:
        raise Refused(f"the phase-0 folder must be named {capture_hooks.ROOT_NAME}")
    repos = os.path.join(root, "repos")
    repo = os.path.abspath(repo)
    rel = os.path.relpath(repo, repos)
    if rel.startswith("..") or rel == "." or os.path.isabs(rel):
        raise Refused("only repositories under <root>/repos are accepted")
    probe = root
    for part in os.path.relpath(repo, root).split(os.sep):
        probe = os.path.join(probe, part)
        if capture_hooks._is_link(probe):
            raise Refused("a part of the repository path is a link")
    code, top = _git(repo, "rev-parse", "--show-toplevel")
    if code != 0 or os.path.normcase(os.path.realpath(top)) != os.path.normcase(os.path.realpath(repo)):
        raise Refused("not the top folder of a git working tree")
    return repo


def _sh_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _posix(path):
    return path.replace("\\", "/")


def dispatcher(hook, original, python=None, recorder=None, spool=None):
    """The sh script for one hook. Without python/recorder/spool it only passes through."""
    lines = ["#!/bin/sh",
             "# Phase 0 capture chain (throwaway): records this hook, then runs the one that was here before.",
             "# The recording never blocks and prints nothing; the earlier hook's exit code decides.",
             "original=" + _sh_quote(_posix(original))]
    if python is None:
        lines += ['if [ -x "$original" ]; then exec "$original" "$@"; fi', "exit 0"]
        return "\n".join(lines) + "\n"
    record = " ".join([_sh_quote(_posix(python)), "-S", "-I", _sh_quote(_posix(recorder)),
                       _sh_quote(_posix(spool)), hook, '"$@"'])
    if hook in STDIN_HOOKS:
        lines += [
            "input=$(mktemp 2>/dev/null) || input=",
            'if [ -n "$input" ]; then',
            '  cat > "$input"',
            f'  {record} < "$input" >/dev/null 2>&1',
            '  if [ -x "$original" ]; then "$original" "$@" < "$input"; status=$?; else status=0; fi',
            '  rm -f "$input"',
            "  exit $status",
            "fi",
            # No temporary file: the earlier hook still gets stdin; the record goes without it.
            f"{record} </dev/null >/dev/null 2>&1",
        ]
    else:
        lines.append(f"{record} </dev/null >/dev/null 2>&1")
    lines += ['if [ -x "$original" ]; then exec "$original" "$@"; fi', "exit 0"]
    return "\n".join(lines) + "\n"


def _hook_names(folder):
    """Hook names present in a hooks folder (sample files excluded)."""
    try:
        return sorted(e.name for e in os.scandir(folder)
                      if e.is_file() and not e.name.endswith(".sample") and "." not in e.name)
    except OSError:
        return []


def install(root, repo, python):
    if not os.path.isabs(python) or not os.path.isfile(python):
        raise Refused("--python must be the absolute path of an existing interpreter")
    repo = checked_repo(root, repo)
    # The recorder and the shim it imports go into <root>/shim, the spool folder into <root>/spool.
    shim_dir = os.path.dirname(capture_hooks.checked_target(root, os.path.join("shim", "capture_shim.py")))
    spool = capture_hooks.checked_target(root, "spool")
    os.makedirs(os.path.join(spool, "git"), exist_ok=True)
    os.makedirs(shim_dir, exist_ok=True)
    for source in (capture_hooks.SHIM, RECORDER):
        with open(source, "rb") as fh:
            capture_hooks._write_atomic(os.path.join(shim_dir, os.path.basename(source)), fh.read())
    recorder = os.path.join(shim_dir, os.path.basename(RECORDER))

    code, common = _git(repo, "rev-parse", "--git-common-dir")
    if code != 0:
        raise Refused("could not find the repository's git folder")
    common = common if os.path.isabs(common) else os.path.join(repo, common)
    chain = os.path.join(os.path.abspath(common), CHAIN_DIR)
    code, current = _git(repo, "config", "--get", "core.hooksPath")
    current = current if code == 0 else ""
    if current and os.path.normcase(os.path.abspath(os.path.join(repo, current))) == os.path.normcase(chain):
        with open(os.path.join(chain, ORIGINAL_FILE), "r", encoding="utf-8") as fh:
            current = fh.read().strip()              # installed before: keep the first original
    original_dir = os.path.join(repo, current) if current else os.path.join(os.path.abspath(common), "hooks")

    os.makedirs(chain, exist_ok=True)
    with open(os.path.join(chain, ORIGINAL_FILE), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(current + "\n")
    for hook in sorted(set(_hook_names(original_dir)) | set(HOOKS)):
        original = os.path.join(original_dir, hook)
        text = dispatcher(hook, original, python, recorder, spool) if hook in HOOKS else dispatcher(hook, original)
        path = os.path.join(chain, hook)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    code, _ = _git(repo, "config", "core.hooksPath", _posix(chain))
    if code != 0:
        raise Refused("could not set core.hooksPath")
    return chain


def uninstall(root, repo):
    repo = checked_repo(root, repo)
    code, common = _git(repo, "rev-parse", "--git-common-dir")
    common = common if os.path.isabs(common) else os.path.join(repo, common)
    chain = os.path.join(os.path.abspath(common), CHAIN_DIR)
    try:
        with open(os.path.join(chain, ORIGINAL_FILE), "r", encoding="utf-8") as fh:
            original = fh.read().strip()
    except OSError:
        return False                                  # not installed
    if original:
        _git(repo, "config", "core.hooksPath", original)
    else:
        _git(repo, "config", "--unset", "core.hooksPath")
    shutil.rmtree(chain)
    return True


def main(argv=None):
    p = argparse.ArgumentParser(description="Install or remove the git-hook capture chain in a scratch repository.")
    p.add_argument("action", choices=["install", "uninstall"])
    p.add_argument("repo")
    p.add_argument("--root", default=capture_hooks.default_root())
    p.add_argument("--python", default=sys.executable)
    args = p.parse_args(argv)
    try:
        if args.action == "install":
            print(f"installed {install(args.root, args.repo, os.path.abspath(args.python))}")
        else:
            print("removed" if uninstall(args.root, args.repo) else "not installed")
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
