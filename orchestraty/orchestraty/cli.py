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
    if args.claude_config:
        return f"They run in every Claude Code session that uses the config folder {os.path.abspath(args.claude_config)}."
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
    wrong = _wrong_target(args)
    if wrong:
        return _refuse("init", wrong)
    if args.agent != "claude":
        return _init_agent(args)
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


_ONLY_FOR = {"claude_config": "claude", "codex_home": "codex", "antigravity_config": "antigravity"}


def _wrong_target(args):
    """Why an option does not go with the agent chosen, or None."""
    for option, agent in _ONLY_FOR.items():
        if getattr(args, option, None) and args.agent != agent:
            return f"--{option.replace('_', '-')} goes with --agent {agent}"
    if args.project and args.agent == "codex":
        return "Codex reads hooks.json from its home folder only; --project goes with claude or antigravity"
    return None


def _agent_target(args):
    from orchestraty.adapters import antigravity, codex
    if args.agent == "codex":
        return codex.config_path(codex_home=args.codex_home)
    return antigravity.config_path(folder=args.antigravity_config, project=os.getcwd() if args.project else None)


def _agent_scope(args, adapter):
    if args.agent == "codex":
        return "They run in every Codex session that uses this home folder."
    if args.project:
        return f"They run in Antigravity conversations in this workspace, {os.getcwd()}."
    return "They run in every Antigravity conversation of this user, in every workspace."


def _init_agent(args):
    """orch init --agent codex|antigravity: show the entries, ask, back the file up, write it."""
    from orchestraty import adapters
    state = State()
    adapter = adapters.get(args.agent)
    path = _agent_target(args)
    python = args.python or hooks.hook_python()
    block = adapter.hook_block(python, state.hook_script)
    if args.print:
        print(json.dumps(block, indent=2))
        return 0
    try:
        hooks.check_python(python)
        hooks.check_settings_file(path)
        hooks.load_settings(path)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    print("orch init will:")
    print(f"  - copy the hook script to {state.hook_script}")
    print(f"  - add hooks for {len(adapter.CAPTURED)} {adapter.TITLE} events to {path}:")
    print("\n".join("      " + line for line in json.dumps(block, indent=2).splitlines()))
    if os.path.exists(path):
        print(f"    (other hooks there are kept; the current file is copied to {state.backups} first)")
    print(_agent_scope(args, adapter) + " " + PRIVACY)
    if args.project:
        print("That file is inside your workspace: orch init writes it, and never commits it.")
    if args.agent == "codex":
        print("Codex runs a hook only once you trust it with /hooks; orch init does not trust it for you.")
    if not confirm("Go ahead?", args.yes):
        print("Nothing changed.")
        return 1
    try:
        changed, saved = hooks.install_agent(state, adapter, path, python)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    if not changed:
        print(f"Already installed in {path}; nothing changed.")
    else:
        print(f"Installed in {path}." + (f" Backup: {saved}" if saved else ""))
    if args.agent == "codex":
        print("Now open Codex and run /hooks: review Orchestraty's hooks and trust them. Codex runs none until you do.")
    else:
        print("Conversations started from now on are seen.")
    if args.project:
        _offer_exclude(state, path, args.yes)
    code = _watch_here(state, os.getcwd(), quiet_outside=True)
    if code == 0 and not args.no_git_hooks:
        _git_chain_here(state, python, args.yes)
    return code


def _offer_exclude(state, path, assume_yes):
    """Offer to keep a workspace's hooks file out of git, through .git/info/exclude."""
    from orchestraty import gitio
    try:
        info = gitio.worktree_info(os.path.dirname(os.path.dirname(path)))
    except (gitio.GitError, gitio.UntrustedRepositoryError):
        return
    if info is None:
        return
    rel = os.path.relpath(path, info["top"]).replace(os.sep, "/")
    try:
        if gitio.run(["ls-files", "--", rel], info["top"]).strip():
            print(f"git tracks {rel}: this change shows in git status. orch init never commits it.")
            return
        try:
            gitio.run(["check-ignore", "-q", "--", rel], info["top"])
            return                                   # git ignores it already
        except gitio.GitError:
            pass
        exclude = gitio.run(["rev-parse", "--git-path", "info/exclude"], info["top"]).strip()
    except gitio.GitError:
        return
    exclude = os.path.normpath(os.path.join(info["top"], exclude))
    print(f"To keep {rel} out of git, orch init can add it to {exclude}: this repository's own")
    print("ignore list, which is never committed or shared.")
    if not confirm("Add it?", assume_yes):
        print(f"Not added; {rel} shows in git status until you ignore it.")
        return
    hooks.backup(state, exclude, "install")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    try:
        with open(exclude, "rb") as fh:
            before = fh.read()
    except FileNotFoundError:
        before = b""
    with open(exclude, "ab") as fh:
        fh.write((b"" if not before or before.endswith(b"\n") else b"\n") + ("/" + rel + "\n").encode("utf-8"))
    print(f"Added /{rel} to {exclude}.")


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
    from orchestraty import config, status
    state = State()
    data = status.collect(state, include_ended=args.all)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        sys.stdout.write(status.render(data, wake=config.load(state)["wake"]))
    return 0


def _recorded_when(state):
    from orchestraty import feedback
    if feedback.coordinator_running(state):
        return "The coordinator records it now."
    return "The coordinator records it when it next runs (a hook starts it with the next event)."


def cmd_findings(args):
    from orchestraty import feedback
    data = feedback.collect(State(), include_all=args.all, days=args.days)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        sys.stdout.write(feedback.render(data))
    return 0


def cmd_label(args):
    from orchestraty import feedback
    state = State()
    try:
        finding = feedback.target(state, args.finding)
        feedback.label(state, finding["key"], args.verdict, args.why)
    except feedback.Refused as exc:
        return _refuse("label", exc)
    print(f"{finding['rule']} {feedback.what(finding)}: labelled {args.verdict}. {_recorded_when(state)}")
    return 0


def cmd_missed(args):
    from orchestraty import feedback
    state = State()
    try:
        sid = feedback.session_ref(state, args.session) if args.session is not None else ""
        feedback.missed(state, args.text, sid)
    except feedback.Refused as exc:
        return _refuse("missed", exc)
    print("Reported" + (f" for session {sid[:8]}" if sid else "") + ". Your words stay in the record on this "
          f"machine; no note is made from them. {_recorded_when(state)}")
    return 0


def cmd_stats(args):
    from orchestraty import stats
    if args.out and not args.summary:
        return _refuse("stats", "--out saves the summary: add --summary")
    data = stats.collect(State(), days=args.days)
    if not args.summary:
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            sys.stdout.write(stats.render(data))
        return 0
    text = json.dumps(stats.summary(data), indent=2) + "\n"
    sys.stdout.write(text)                              # first, so it is read before it is saved
    if not args.out:
        return 0
    path = os.path.abspath(args.out)
    if not confirm(f"Save this summary to {path}?", args.yes):
        print("Not saved.")
        return 1
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        return _refuse("stats", exc)
    print(f"Saved to {path}. Orchestraty sends it nowhere; sharing it is up to you.")
    return 0


def _days(text):
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError("a whole number of days, 1 or more")
    return value


def _remote_repo(state, args):
    """(git common folder, name) of the watched repository holding --repo (default: here)."""
    from orchestraty import config, gitio
    path = os.path.abspath(getattr(args, "repo", None) or os.getcwd())
    try:
        info = gitio.worktree_info(path)
    except gitio.UntrustedRepositoryError as exc:
        raise ValueError(str(exc)) from None
    if info is None:
        raise ValueError(f"{path} is not inside a git worktree")
    name = os.path.basename(info["top"])
    if not config.is_watched(config.load(state), info["common_dir"]):
        raise ValueError(f"{name} is not watched: run orch watch in it first")
    return info["common_dir"], name


def _fetch_line(result):
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(result["at"]))
    if result["ok"]:
        return f"last fetch {when}: ok, {result['branches']} branch(es) match"
    return f"last fetch {when}: failed ({result['failures']} in a row): {result['error']}"


def cmd_remote(args):
    from orchestraty import config, remote
    state = State()
    try:
        common_dir, name = _remote_repo(state, args)
    except ValueError as exc:
        return _refuse("remote", exc)
    cfg = config.load(state)
    chosen = remote.settings(cfg, common_dir)
    shadow = remote.Shadow(state, common_dir)
    minutes = cfg["remote_fetch_minutes"]
    if args.action is None:
        print(f"Cloud branches of {name}: {'on' if chosen['on'] else 'off'}. Remote: {chosen['remote']}. "
              f"Patterns: {', '.join(chosen['patterns'])}.")
        if shadow.status():
            print(_fetch_line(shadow.status()))
        return 0
    if args.action == "on":
        chosen["remote"] = args.remote or chosen["remote"]
        try:
            url = remote.remote_url(common_dir, chosen["remote"])
        except remote.RemoteError as exc:
            return _refuse("remote", exc)
        print("orch remote on will, while the coordinator runs, every " + f"{minutes} minutes:")
        print(f"  - fetch from {chosen['remote']} ({remote.safe_url(url)}) its default branch and the branches "
              f"matching {', '.join(chosen['patterns'])};")
        print(f"  - into a repository of Orchestraty's own, {shadow.path}, which reads your repository's objects "
              "and writes none.")
        print("Your .git is not changed. This is the one thing Orchestraty does on the network, and git never "
              "asks for credentials: without them, a fetch fails and the failure is shown in orch status.")
        if not confirm("Turn it on?", args.yes):
            print("Nothing changed.")
            return 1
        remote.save_settings(state, common_dir, on=True, remote=chosen["remote"])
        print(f"Cloud branches on for {name}. orch remote fetch fetches now.")
        return 0
    if args.action == "off":
        remote.save_settings(state, common_dir, on=False, **({"remote": args.remote} if args.remote else {}))
        print(f"Cloud branches off for {name}: nothing more is fetched, and their sessions end. "
              f"The shadow repository stays in {shadow.path}; delete it to free its space.")
        return 0
    if args.action == "patterns":
        patterns = list(chosen["patterns"])
        if args.op is None:
            print("\n".join(patterns))
            return 0
        if not args.glob:
            return _refuse("remote", f"orch remote patterns {args.op} takes a pattern, such as agent/*")
        if args.op == "add":
            if not remote.valid_pattern(args.glob):
                return _refuse("remote", f"{args.glob} is not a branch pattern git can fetch (one '*' at most)")
            if args.glob not in patterns:
                patterns.append(args.glob)
        elif args.glob in patterns:
            patterns.remove(args.glob)
        else:
            return _refuse("remote", f"{args.glob} is not one of the patterns: {', '.join(patterns)}")
        remote.save_settings(state, common_dir, patterns=patterns)
        print("Patterns: " + (", ".join(patterns) or "none (no branch is followed)") + ".")
        return 0
    if not chosen["on"]:                                    # fetch
        return _refuse("remote", f"cloud branches are off for {name}: orch remote on first")
    result = remote.fetch_now(state, common_dir, chosen, minutes * 60, wait=remote.FETCH_TIMEOUT + 10)
    if result is None:
        print("Another fetch is running; try again in a moment.", file=sys.stderr)
        return 1
    print(_fetch_line(result) + ".")
    return 0 if result["ok"] else 1


def cmd_mcp(args):
    if args.config:
        from orchestraty.mcp import register
        sys.stdout.write(register.text(args.config))
        return 0
    from orchestraty.mcp import server
    return server.main(args.folder)


def cmd_events(args):
    from orchestraty import events
    try:
        since = events.parse_since(args.since) if args.since is not None else None
    except ValueError as exc:
        return _refuse("events", exc)
    try:
        return events.run(State(), sys.stdout, as_json=args.json, follow=args.follow, since=since)
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())     # nothing left to flush at exit
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
    wrong = _wrong_target(args)
    if wrong:
        return _refuse("restore", wrong)
    if args.agent != "claude":
        return _restore_agent(args)
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


def _restore_agent(args):
    """orch restore --agent codex|antigravity: take our hooks out (others kept), or put a backup back."""
    from orchestraty import adapters
    state = State()
    adapter = adapters.get(args.agent)
    path = _agent_target(args)
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
        changed, saved = hooks.uninstall_agent(state, adapter, path)
    except hooks.Refused as exc:
        return _refuse("restore", exc)
    if changed:
        print(f"Removed Orchestraty's hooks from {path}; other hooks there are kept. Backup: {saved}")
    else:
        print(f"No Orchestraty hooks in {path}; nothing changed.")
    return 0


def _add_target_options(parser):
    parser.add_argument("--agent", choices=["claude", "codex", "antigravity"], default="claude",
                        help="the agent: claude (Claude Code, the default), codex or antigravity")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--claude-config", metavar="DIR",
                       help="a Claude Code config folder (default: CLAUDE_CONFIG_DIR, else ~/.claude)")
    where.add_argument("--codex-home", metavar="DIR", help="Codex's home folder (default: CODEX_HOME, else ~/.codex)")
    where.add_argument("--antigravity-config", metavar="DIR",
                       help="the folder of Antigravity's user hooks.json (default: ~/.gemini/config)")
    where.add_argument("--project", action="store_true",
                       help="this folder's own file instead of the user's: .claude/settings.local.json for "
                            "Claude Code, .agents/hooks.json for Antigravity")
    parser.add_argument("--yes", action="store_true", help="do not ask before changing anything")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="orch",
        description="Keeps parallel coding-agent sessions aware of each other.",
    )
    parser.add_argument("--version", action="version", version=f"orch {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    init = commands.add_parser("init", help="install the hooks into an agent (Claude Code unless --agent)",
                               description="Install the hook script and an agent's hook entries.")
    _add_target_options(init)
    init.add_argument("--python", metavar="PATH", help="the interpreter the hooks run (default: this one's base)")
    init.add_argument("--print", action="store_true", help="only print the hook entries to add by hand")
    init.add_argument("--no-git-hooks", action="store_true", help="do not offer the push line in git's hooks")
    init.add_argument("--no-mod", action="store_true", help="do not load Orchestraty's Claude Code mod")
    init.set_defaults(handler=cmd_init)

    restore = commands.add_parser("restore", help="remove the hooks from an agent (Claude Code unless --agent)",
                                  description="Remove Orchestraty's hook entries, or put a backup back.")
    _add_target_options(restore)
    restore.add_argument("--from-backup", action="store_true",
                         help="put back the agent's file as it was before the last orch init")
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

    findings = commands.add_parser("findings", help="the findings, numbered for orch label",
                                   description="List the findings seen in the last days, the latest first, each "
                                               "with a number orch label takes for 24 hours. Reads the record "
                                               "without changing it.")
    findings.add_argument("--all", action="store_true", help="include resolved and latent findings")
    findings.add_argument("--days", type=_days, default=7, metavar="N", help="findings seen in the last N days "
                                                                             "(default 7)")
    findings.add_argument("--json", action="store_true", help="print JSON (schema in the 'schema' field)")
    findings.set_defaults(handler=cmd_findings)

    label = commands.add_parser("label", help="your verdict on a finding",
                                description="Label a finding real, harmless or false. Kept in the record on "
                                            "this machine, with your reason; never put into a note.")
    label.add_argument("finding", help="its number from orch findings (24 hours), or its key")
    label.add_argument("verdict", choices=["real", "harmless", "false"])
    label.add_argument("--why", default="", help="your reason, in your words (up to 500 characters)")
    label.set_defaults(handler=cmd_label)

    missed = commands.add_parser("missed", help="report a note that should have come",
                                 description="Report a note Orchestraty should have given. Kept in the record "
                                             "on this machine; never put into a note.")
    missed.add_argument("text", help="what should have been noted, in your words (up to 2000 characters)")
    missed.add_argument("--session", metavar="ID", help="the session that should have heard (its id, or its "
                                                         "first characters)")
    missed.set_defaults(handler=cmd_missed)

    stats = commands.add_parser("stats", help="how Orchestraty was used, week by week",
                                description="Usage, week by week, from the record: sessions, findings, notes and "
                                            "their time to awareness, notes acted on (a proxy), labels and missed "
                                            "notes. Reads the record without changing it; sends nothing anywhere.")
    stats.add_argument("--days", type=_days, default=7, metavar="N", help="the last N days (default 7)")
    stats.add_argument("--json", action="store_true", help="print JSON (schema in the 'schema' field)")
    stats.add_argument("--summary", action="store_true", help="an anonymous summary, counts only, shown first")
    stats.add_argument("--out", metavar="FILE", help="with --summary: save it to FILE once you have seen it")
    stats.add_argument("--yes", action="store_true", help="save without asking")
    stats.set_defaults(handler=cmd_stats)

    mcp = commands.add_parser("mcp", help="the MCP server agents ask before they edit",
                              description="Run Orchestraty's MCP server over stdio (an agent starts it in its "
                                          "workspace folder), or print how to register it with an agent. The server "
                                          "reads the record without changing it; declarations go to orch's spool.")
    mcp.add_argument("--config", choices=["claude", "codex", "antigravity"], metavar="AGENT",
                     help="print how to register the server with claude, codex or antigravity; writes nothing")
    mcp.add_argument("--folder", metavar="DIR", help="the caller's workspace folder (default: CLAUDE_PROJECT_DIR, "
                                                     "else here)")
    mcp.set_defaults(handler=cmd_mcp)

    events = commands.add_parser("events", help="what changes in the record, as a stream",
                                 description="Sessions started and ended, findings opened and resolved, notes "
                                             "written, delivered and withdrawn, and declarations, in the order the "
                                             "record learned of them. Reads the record without changing it.")
    how = events.add_mutually_exclusive_group()
    how.add_argument("--follow", action="store_true", help="keep printing the changes as they come, from now on or "
                                                           "from --since (polls about once a second)")
    how.add_argument("--once", action="store_true", help="print the changes there are and end (the default)")
    events.add_argument("--json", action="store_true", help="one JSON object per line (schema in the 'schema' field)")
    events.add_argument("--since", metavar="TS", help="from this time on: ISO 8601 (Z for UTC, else local), seconds "
                                                      "since the epoch, or a time ago such as 10m, 2h or 1d")
    events.set_defaults(handler=cmd_events)

    rem = commands.add_parser("remote", help="cloud agents' branches, fetched read-only and compared",
                              description="Branches agents push from elsewhere (a cloud agent, a personal agent "
                                          "running coding tasks), fetched into a repository of Orchestraty's own "
                                          "and compared with the sessions here as level-0 sessions. Off by "
                                          "default, for each watched repository.")
    where = argparse.ArgumentParser(add_help=False)
    where.add_argument("--repo", metavar="DIR", default=argparse.SUPPRESS,
                       help="a folder inside the repository (default: here)")
    actions = rem.add_subparsers(dest="action", metavar="<action>")
    on = actions.add_parser("on", parents=[where], help="turn it on (asks first)",
                            description="Fetch the default branch and the matching branches every few minutes.")
    on.add_argument("--remote", metavar="NAME", help="the remote to fetch from (default: origin)")
    on.add_argument("--yes", action="store_true", help="do not ask first")
    off = actions.add_parser("off", parents=[where], help="turn it off", description="Fetch nothing more.")
    off.add_argument("--remote", metavar="NAME", help="the remote to keep in the settings")
    actions.add_parser("fetch", parents=[where], help="fetch now", description="Fetch now, and say how it went.")
    pat = actions.add_parser("patterns", parents=[where], help="show, add or remove branch patterns",
                             description="The branches followed: refspec globs under refs/heads/ (one '*' at "
                                         "most). By default claude/*, codex/* and copilot/*.")
    pat.add_argument("op", nargs="?", choices=["add", "remove"])
    pat.add_argument("glob", nargs="?", help="a branch pattern, such as agent/*")
    rem.add_argument("--repo", metavar="DIR", help="a folder inside the repository (default: here)")
    rem.set_defaults(handler=cmd_remote)

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
