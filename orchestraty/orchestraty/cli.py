"""The ``orch`` command line."""
import argparse
import json
import os
import sys

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
    state = State()
    path = _settings_target(args)
    python = args.python or hooks.hook_python()
    if args.print:
        print(json.dumps({"hooks": hooks.hook_block(python, state.hook_script)}, indent=2))
        return 0
    try:
        hooks.check_python(python)
        hooks.check_settings_file(path)
        hooks.load_settings(path)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    print("orch init will:")
    print(f"  - copy the hook script to {state.hook_script}")
    print(f"  - add hooks for {len(hooks.EVENTS)} Claude Code events to {path}")
    if os.path.exists(path):
        print(f"    (the current file is copied to {state.backups} first)")
    print(_scope_line(args) + " " + PRIVACY)
    if not confirm("Go ahead?", args.yes):
        print("Nothing changed.")
        return 1
    try:
        changed, saved = hooks.install(state, path, python)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    if not changed:
        print(f"Already installed in {path}; nothing changed.")
    else:
        print(f"Installed in {path}." + (f" Backup: {saved}" if saved else ""))
        print("Sessions started from now on are seen; restart running sessions to include them.")
    return 0


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
    init.set_defaults(handler=cmd_init)

    restore = commands.add_parser("restore", help="remove the hooks from Claude Code",
                                  description="Remove Orchestraty's hook entries, or put a backup back.")
    _add_target_options(restore)
    restore.add_argument("--from-backup", action="store_true",
                         help="put back the settings file as it was before the last orch init")
    restore.set_defaults(handler=cmd_restore)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)
