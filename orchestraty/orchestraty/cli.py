"""The ``orch`` command line."""
import argparse
import json
import os
import sys
import time

from orchestraty import __version__
from orchestraty.capture import install as hooks
from orchestraty.paths import State

PRIVACY = ("The hooks record event names, session ids, folders and the paths of edited files; never prompts, "
           "file contents, commands or tool output. Nothing leaves this machine.")


def _settings_target(args):
    return hooks.settings_path(claude_config=args.claude_config, project=os.getcwd() if args.project else None)


def _scope_line(args):
    if args.project:
        return f"They run in Claude Code sessions started in {os.getcwd()}."
    return "They run in every Claude Code session of this user, in every folder and worktree."


def confirm(question, assume_yes):
    """True when the person agrees. Without a terminal to ask in, only --yes agrees."""
    if assume_yes:
        return True
    if not (sys.stdin and sys.stdin.isatty()):
        print("No terminal to ask in: run again with --yes to go ahead.")
        return False
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _refuse(command, exc):
    print(f"orch {command}: {exc}", file=sys.stderr)
    return 2


def cmd_init(args):
    from orchestraty import config
    state = State()
    path = _settings_target(args)
    python = args.python or hooks.hook_python()
    wake = config.load(state)["wake"]
    if args.print:
        print(json.dumps({"hooks": hooks.hook_block(python, state.hook_script, wake)}, indent=2))
        return 0
    try:
        hooks.check_python(python)
        hooks.check_settings_file(path)
        hooks.load_settings(path)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    mod = not args.no_mod
    print("orch init will:")
    print(f"  - copy the hook script to {state.hook_script}")
    print(f"  - add hooks for {len(hooks.EVENTS)} Claude Code events to {path}")
    if mod:
        print(f"  - load Orchestraty's Claude Code mod from {state.mod}: a status line, a band for strong")
        print("    overlaps, a pane and /orch (Claude Code 2.1.287 or later; --no-mod leaves it out)")
    if os.path.exists(path):
        print(f"    (the current file is copied to {state.backups} first)")
    print(_scope_line(args) + " " + PRIVACY)
    if not confirm("Go ahead?", args.yes):
        print("Nothing changed.")
        return 1
    try:
        changed, saved = hooks.install(state, path, python, wake, mod)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    cfg = config.load(state)
    if cfg["settings"] != path:
        cfg["settings"] = path
        config.save(state, cfg)
    if not changed:
        print(f"Already installed in {path}; nothing changed.")
    else:
        print(f"Installed in {path}." + (f" Backup: {saved}" if saved else ""))
        print("Sessions started from now on are seen; restart running sessions to include them.")
    code = _watch_here(state, os.getcwd(), quiet_outside=True)
    if code == 0 and not args.no_git_hooks:
        _git_chain_here(state, python, args.yes)
    return code


def _git_chain_here(state, python, assume_yes):
    """Offer the push line: a pre-push hook chained in front of the repository's own hooks."""
    from orchestraty import gitio
    from orchestraty.capture import githooks
    try:
        if gitio.worktree_info(os.getcwd()) is None:
            return
        if githooks.installed(os.getcwd()):
            print("The push line is already in this repository's git hooks.")
            return
    except (gitio.GitError, gitio.UntrustedRepositoryError):
        return
    print("The push line: before each push, git prints one line when this push leaves callers of a")
    print("changed function behind. It sets core.hooksPath in this repository's own git config to a")
    print("folder inside .git; the hooks you have keep running, and their exit codes still decide.")
    if not confirm("Add it?", assume_yes):
        print("No git hooks changed (orch init --no-git-hooks skips this question).")
        return
    chain = githooks.install(os.getcwd(), python, state.hook_script)
    print(f"Added: {chain}")


def _watch_here(state, path, quiet_outside=False):
    """Watch the repository holding `path`. Returns an exit code."""
    from orchestraty import config, gitio
    try:
        info = gitio.worktree_info(path)
    except gitio.UntrustedRepositoryError as exc:
        print(f"Not watched: {exc}", file=sys.stderr)
        return 2
    if info is None:
        if quiet_outside:
            print("Run orch watch inside a repository to have its sessions compared.")
            return 0
        print(f"orch watch: {path} is not inside a git worktree", file=sys.stderr)
        return 2
    added = config.watch(state, info["common_dir"])
    name = os.path.basename(info["top"])
    print(f"Watching {name} ({info['common_dir']})." if added else f"Already watching {name}.")
    return 0


def cmd_watch(args):
    return _watch_here(State(), os.path.abspath(args.path or os.getcwd()))


def cmd_unwatch(args):
    from orchestraty import config, gitio
    state = State()
    path = os.path.abspath(args.path or os.getcwd())
    try:
        info = gitio.worktree_info(path)
    except gitio.UntrustedRepositoryError:
        info = None
    common_dir = info["common_dir"] if info else path
    if config.unwatch(state, common_dir):
        print(f"No longer watching {common_dir}.")
    else:
        print(f"{common_dir} was not watched.")
    if info:
        from orchestraty.capture import githooks
        try:
            if githooks.uninstall(path):
                print("Took the push line out of this repository's git hooks.")
        except gitio.GitError as exc:
            print(f"Could not take the push line out of the git hooks: {exc}", file=sys.stderr)
    return 0


def cmd_config(args):
    from orchestraty import config
    state = State()
    cfg = config.load(state)
    if args.name is None:
        for name in ("steer", "wake"):
            print(f"{name}: {'on' if cfg[name] else 'off'}")
        return 0
    if args.value is None:
        print(f"{args.name}: {'on' if cfg[args.name] else 'off'}")
        return 0
    cfg[args.name] = args.value == "on"
    config.save(state, cfg)
    print(f"{args.name}: {args.value}")
    if args.name == "wake":
        path = cfg["settings"]
        if not path:
            print("Run orch init to install the hooks; the wake hook comes with them.")
            return 0
        try:
            mod = hooks.mod_in(hooks.load_settings(path), state.mod)
            changed, saved = hooks.install(state, path, hooks.hook_python(), cfg["wake"], mod)
        except hooks.Refused as exc:
            return _refuse("config", exc)
        if changed:
            print(f"Updated the hooks in {path}." + (f" Backup: {saved}" if saved else ""))
            print("Sessions started from now on use it.")
    return 0


def cmd_status(args):
    from orchestraty import status
    data = status.collect(State(), include_ended=args.all)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        sys.stdout.write(status.render(data))
    return 0


def cmd_coordinator(args):
    from orchestraty.coordinator import main as coordinator
    from orchestraty.coordinator.lock import Lock, touch
    state = State()
    if args.action == "run":
        return coordinator.run(state)
    # stop: ask, then wait until the lock is free
    lock = Lock(state.lock)
    if lock.acquire():
        lock.release()
        print("The coordinator is not running.")
        return 0
    touch(state.stop)
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        if lock.acquire():
            lock.release()
            print("The coordinator stopped.")
            return 0
        time.sleep(0.1)
    print("The coordinator did not stop in time.", file=sys.stderr)
    return 1


def cmd_restore(args):
    state = State()
    path = _settings_target(args)
    if args.from_backup:
        question = f"Put back {path} as it was before the last orch init?"
    else:
        question = f"Remove Orchestraty's hooks from {path}?"
    try:
        hooks.check_settings_file(path)
        hooks.load_settings(path)
    except hooks.Refused as exc:
        return _refuse("restore", exc)
    if not confirm(question, args.yes):
        print("Nothing changed.")
        return 1
    try:
        if args.from_backup:
            folder = hooks.restore_backup(state, path)
            if folder is None:
                print(f"No backup of {path} from before an install; run orch restore without --from-backup.")
                return 1
            print(f"Put back {path} from {folder}.")
            return 0
        changed, saved = hooks.uninstall(state, path)
    except hooks.Refused as exc:
        return _refuse("restore", exc)
    if changed:
        print(f"Removed Orchestraty's hooks from {path}. Backup: {saved}")
    else:
        print(f"No Orchestraty hooks in {path}; nothing changed.")
    return 0


def _add_target_options(parser):
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--claude-config", metavar="DIR",
                       help="a Claude Code config folder (default: CLAUDE_CONFIG_DIR, else ~/.claude)")
    where.add_argument("--project", action="store_true",
                       help="this folder's .claude/settings.local.json instead of the user's settings")
    parser.add_argument("--yes", action="store_true", help="do not ask before changing anything")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="orch",
        description="Keeps parallel coding-agent sessions aware of each other.",
    )
    parser.add_argument("--version", action="version", version=f"orch {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    init = commands.add_parser("init", help="install the hooks into Claude Code",
                               description="Install the hook script and Claude Code's hook entries.")
    _add_target_options(init)
    init.add_argument("--python", metavar="PATH", help="the interpreter the hooks run (default: this one's base)")
    init.add_argument("--print", action="store_true", help="only print the hook entries to add by hand")
    init.add_argument("--no-git-hooks", action="store_true", help="do not offer the push line in git's hooks")
    init.add_argument("--no-mod", action="store_true", help="do not load Orchestraty's Claude Code mod")
    init.set_defaults(handler=cmd_init)

    restore = commands.add_parser("restore", help="remove the hooks from Claude Code",
                                  description="Remove Orchestraty's hook entries, or put a backup back.")
    _add_target_options(restore)
    restore.add_argument("--from-backup", action="store_true",
                         help="put back the settings file as it was before the last orch init")
    restore.set_defaults(handler=cmd_restore)

    watch = commands.add_parser("watch", help="compare the sessions of this repository",
                                description="Watch a repository: its sessions are recorded and compared.")
    watch.add_argument("path", nargs="?", help="a folder inside the repository (default: here)")
    watch.set_defaults(handler=cmd_watch)
    unwatch = commands.add_parser("unwatch", help="stop watching this repository",
                                  description="Stop watching a repository.")
    unwatch.add_argument("path", nargs="?", help="a folder inside the repository (default: here)")
    unwatch.set_defaults(handler=cmd_unwatch)

    status = commands.add_parser("status", help="the sessions on each watched repository",
                                 description="Show the coordinator and the sessions on each watched repository.")
    status.add_argument("--json", action="store_true", help="print JSON (schema in the 'schema' field)")
    status.add_argument("--all", action="store_true", help="include ended sessions")
    status.set_defaults(handler=cmd_status)

    cfg = commands.add_parser("config", help="show or switch steer and wake",
                              description="steer: a strong note stops the edit it rides on, so the agent re-plans "
                                          "(off: the note only informs). wake: a strong note wakes a session that "
                                          "waits for its user (installs a background hook).")
    cfg.add_argument("name", nargs="?", choices=["steer", "wake"])
    cfg.add_argument("value", nargs="?", choices=["on", "off"])
    cfg.set_defaults(handler=cmd_config)

    coordinator = commands.add_parser("coordinator", help="run or stop the coordinator by hand",
                                      description="Hooks start the coordinator when needed; these are for "
                                                  "debugging.")
    coordinator.add_argument("action", choices=["run", "stop"])
    coordinator.add_argument("--wait", type=float, default=10.0, help="seconds to wait for a stop (default 10)")
    coordinator.set_defaults(handler=cmd_coordinator)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)
