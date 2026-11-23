"""The ``orch`` command line."""
import argparse
import json
import os
import shutil
import sys
import time

from orchestraty import __version__, plugin
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
    if args.agent == "all":
        return _init_all(args)
    wrong = _wrong_target(args)
    if wrong:
        return _refuse("init", wrong)
    if args.agent != "claude":
        return _init_agent(args)
    state = State()
    path = _settings_target(args)
    python = hooks.hook_python() if args.python is None else args.python    # --python "" is refused
    wake = config.load(state)["wake"]
    if args.print:
        print(json.dumps({"hooks": hooks.hook_block(python, state.hook_script, wake)}, indent=2))
        return 0
    try:
        hooks.check_python(python)
        hooks.check_settings_file(path)
        current = hooks.load_settings(path)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    found = plugin.installed(current, claude_config=args.claude_config, project=os.getcwd() if args.project else None)
    if found:
        return _init_for_plugin(args, state, path, python, wake, current, found[0])
    mod = not args.no_mod
    print("orch init will:")
    print(f"  - copy the hook script to {state.hook_script}")
    print(f"  - add hooks for {len(hooks.EVENTS)} Claude Code events to {path}")
    for root in hooks.other_states(current, state.hook_script):
        print(f"  - take out of it the hooks and the mod of the state folder {root}, which an orch init with another")
        print("    ORCH_HOME added, so no event is recorded twice (that folder itself stays)")
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
    _remember_settings(state, path, changed)
    if not changed:
        print(f"Already installed in {path}; nothing changed.")
    else:
        print(f"Installed in {path}." + (f" Backup: {saved}" if saved else ""))
        print("Sessions started from now on are seen; restart running sessions to include them.")
    if args.project:
        _offer_exclude(state, path, args.yes)
    return _init_rest(args, state, python)


def _remember_settings(state, path, changed):
    from orchestraty import config
    cfg = config.load(state)
    if cfg["settings"] != path:
        cfg["settings"] = path
        config.save(state, cfg)
    config.remember_install(state, "claude", path, changed=changed)


def _init_for_plugin(args, state, path, python, wake, current, plugin_id):
    """orch init with Claude Code's Orchestraty plugin installed: the plugin holds the hooks and
    the mod, so none go into the settings file; orch init installs the hook script and turns
    the plugin's hooks on."""
    earlier = hooks.without_orchestraty(current, state.hook_script, state.mod) != current
    print(f"Claude Code's Orchestraty plugin is installed ({plugin_id}). It holds the hooks for "
          f"{len(hooks.EVENTS)} Claude Code events and the mod, so orch init adds no hooks to {path}.")
    print("orch init will:")
    print(f"  - copy the hook script to {state.hook_script}")
    print(f"  - turn the plugin's hooks on: {plugin.switch_path(state)} names the interpreter they run")
    if earlier:
        print(f"  - take out of {path} the hooks and the mod an earlier orch init added, so nothing runs twice")
        print(f"    (the current file is copied to {state.backups} first)")
    if args.no_mod:
        print("    (--no-mod changes nothing here: the mod comes with the plugin)")
    if os.name == "nt":
        print("On Windows Claude Code runs a plugin's hooks through Git Bash, whose start makes each event slower than "
              "the settings hooks, which start Python directly; without the plugin, orch init writes those.")
        if not plugin.git_bash():
            print("No Git Bash was found: install Git for Windows, or set CLAUDE_CODE_GIT_BASH_PATH. orch doctor "
                  "checks it.")
    print(_scope_line(args) + " " + PRIVACY)
    if not confirm("Go ahead?", args.yes):
        print("Nothing changed.")
        return 1
    try:
        changed, saved = hooks.install_for_plugin(state, path, python, wake)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    _remember_settings(state, path, changed)
    if not changed:
        print("The plugin's hooks are already on; nothing changed.")
    else:
        print("The plugin's hooks are on."
              + (f" Took the earlier hooks out of {path}. Backup: {saved}" if saved else ""))
        print("Sessions started from now on are seen; restart running sessions to include them.")
    return _init_rest(args, state, python)


def _init_rest(args, state, python):
    if getattr(args, "nested", False):
        return 0
    _restart_older_coordinator(state)
    _offer_tracking(state, args.yes)
    code = _watch_here(state, os.getcwd(), quiet_outside=True)
    if code == 0 and not args.no_git_hooks:
        _git_chain_here(state, python, args.yes)
    if args.agent == "claude" and not args.project:
        code = max(code, _offer_other_agents(args))
    _show_connections(state, args.agent)
    return code


def _init_all(args):
    from orchestraty import config, connections
    from orchestraty.adapters import antigravity, codex
    if args.project or any(getattr(args, option) for option in _ONLY_FOR):
        return _refuse("init", "--agent all uses each agent's user config; use --agent NAME to select a custom target")
    state = State()
    python = hooks.hook_python() if args.python is None else args.python
    if args.print:
        print(json.dumps({"claude": {"hooks": hooks.hook_block(python, state.hook_script, config.load(state)["wake"])},
                          "codex": codex.hook_block(python, state.hook_script),
                          "antigravity": antigravity.hook_block(python, state.hook_script)}, indent=2))
        return 0
    print("Set up Orchestraty in the user config for Claude Code, Codex, and Antigravity:")
    print(f"  Claude Code: {_settings_target(args)}\n  Codex: {codex.config_path()}\n"
          f"  Antigravity: {antigravity.config_path()}")
    if not confirm("Install all three integrations?", args.yes):
        print("Nothing changed.")
        return 1
    results = [cmd_init(argparse.Namespace(**dict(vars(args), agent=agent, nested=True, yes=True)))
               for agent in connections.AGENTS]
    if all(results):
        _show_connections(state, "all")
        return max(results)
    return max(*results, _init_rest(args, state, python))


def _show_connections(state, agent):
    from orchestraty import connections
    print("\nAgent setup:")
    for row in connections.summary(state):
        if agent in ("all", "claude", row["agent"]):
            print(f"  {row['name']} [{connections.LABELS[row['status']]}] {', '.join(row['paths'])}")
            command = f" {row['command']}" if row["status"] in ("not_configured", "config_error") else ""
            print(f"    {row['message']}{command}")


def _offer_tracking(state, assume_yes):
    """Ask once whether every project an agent works in is followed (watch_all)."""
    from orchestraty import config
    if config.load(state)["watch_all"]:
        return
    print("Orchestraty can follow every project your agents work in, from wherever you run orch init: a git "
          "repository, or a folder without git, through its own snapshots in its state folder. Each one is "
          "recorded on this computer only, and nothing is sent anywhere; orch unwatch in a project leaves it out.")
    if confirm("Follow every project?", assume_yes):
        config.set_watch_all(state, True)
        print("Following every project your agents work in.")
    else:
        print("Only the projects you watch are followed: run orch watch in each (orch config track on changes this).")


def _agents_here():
    """The other agents found on this computer, by the folders they keep: [(agent, title)]."""
    from orchestraty.adapters import antigravity, codex
    found = []
    if os.path.isdir(os.path.dirname(codex.config_path())) or shutil.which("codex"):
        found.append(("codex", "Codex"))
    if os.path.isdir(os.path.dirname(os.path.dirname(antigravity.config_path()))) or any(
            shutil.which(name) for name in ("agy", "antigravity")):
        found.append(("antigravity", "Antigravity"))
    return found


def _offer_other_agents(args):
    """Offer to set up each other agent found here that orch init has not set up yet. --yes
    answers only for what orch init itself does: it names them, and changes no other agent's
    files unasked."""
    from orchestraty import connections
    done = {row["agent"] for row in connections.summary(State())
            if row["status"] not in ("not_configured", "config_error")}
    code = 0
    for agent, title in _agents_here():
        if agent in done:
            continue
        print()
        if args.yes:
            print(f"{title} is on this computer too: orch init --agent {agent} sets it up.")
            continue
        if not confirm(f"{title} is on this computer too. Set Orchestraty up for it?", False):
            print(f"Left {title} as it is: orch init --agent {agent} sets it up later.")
            continue
        nested = argparse.Namespace(**dict(vars(args), agent=agent, project=False, claude_config=None,
                                           codex_home=None, antigravity_config=None, nested=True))
        code = max(code, _init_agent(nested))
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
    from orchestraty.adapters import shell
    state = State()
    adapter = adapters.get(args.agent)
    path = _agent_target(args)
    python = hooks.hook_python() if args.python is None else args.python    # --python "" is refused
    block = adapter.hook_block(python, state.hook_script)
    if args.print:
        print(json.dumps(block, indent=2))
        return 0
    try:
        hooks.check_python(python)
        hooks.check_settings_file(path)
        current = hooks.load_settings(path)
    except hooks.Refused as exc:
        return _refuse("init", exc)
    print("orch init will:")
    print(f"  - copy the hook script to {state.hook_script}")
    print(f"  - add hooks for {len(adapter.CAPTURED)} {adapter.TITLE} events to {path}:")
    print("\n".join("      " + line for line in json.dumps(block, indent=2).splitlines()))
    if os.name == "nt" and not os.path.exists(state.hook_script) and not shell._plain(state.hook_script):
        print("    (the hook script's path is written in its short 8.3 form, which needs no quotes, where the volume")
        print("    keeps one: a path has it only once the script is there)")
    for root in adapter.other_states(current, state.hook_script):
        print(f"  - take out of it the hooks of the state folder {root}, which an orch init with another ORCH_HOME")
        print("    added, so no event is recorded twice (that folder itself stays)")
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
    from orchestraty import config
    config.remember_install(state, args.agent, path, changed=changed)
    if not changed:
        print(f"Already installed in {path}; nothing changed.")
    else:
        print(f"Installed in {path}." + (f" Backup: {saved}" if saved else ""))
    if args.agent == "codex":
        print("Review and trust Orchestraty's hooks in Codex desktop's Hooks settings or with /hooks in the CLI.")
    else:
        print("Conversations started from now on are seen.")
    if args.project:
        _offer_exclude(state, path, args.yes)
    return _init_rest(args, state, python)


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
    try:
        _append_exclude(state, exclude, rel)
    except (OSError, hooks.Refused) as exc:
        print(f"Not added: {exclude} cannot be written ({type(exc).__name__}); {rel} shows in git status until "
              "you ignore it.")
        return
    print(f"Added /{rel} to {exclude}.")


def _append_exclude(state, exclude, rel):
    """Add the line, after a copy of the file goes into the backups. A file that is not there is
    made empty first, so its copy is empty: orch uninstall then deletes it."""
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    with open(exclude, "ab"):
        pass
    hooks.backup(state, exclude, "install")
    with open(exclude, "rb") as fh:
        before = fh.read()
    with open(exclude, "ab") as fh:
        fh.write((b"" if not before or before.endswith(b"\n") else b"\n") + ("/" + rel + "\n").encode("utf-8"))


def _git_chain_here(state, python, assume_yes):
    """Offer the push line: a pre-push hook chained in front of the repository's own hooks. Where
    it is in already, write it again: hooks added since get a pass-through, the pre-push runs this
    state folder's hook script, and a chain folder that is gone is rebuilt."""
    from orchestraty import gitio
    from orchestraty.capture import githooks
    here = os.getcwd()
    try:
        if gitio.worktree_info(here) is None:
            return
        if githooks.installed(here):
            lost = githooks.lost(here)
            chain = githooks.install(here, python, state.hook_script)
            if lost:
                earlier = os.path.join(os.path.dirname(chain), "hooks")
                print(f"Rebuilt the push line's folder {chain}, which was gone or incomplete. The hooks folder "
                      f"before it is not known, so it runs the hooks in {earlier}.")
            else:
                print("The push line is already in this repository's git hooks.")
            _say_left(chain)
            return
        why = githooks.refusal(here)
    except (gitio.GitError, gitio.UntrustedRepositoryError):
        return
    except OSError as exc:
        print(f"Could not write the push line into this repository's git hooks: {exc}", file=sys.stderr)
        return
    if why:
        print(f"No git hooks changed: {why}.")
        return
    print("The push line: before each push, git prints one line when this push leaves callers of a")
    print("changed function behind. It sets core.hooksPath in this repository's own git config to a")
    print("folder inside .git; the hooks you have, and ones added to that folder later, keep running,")
    print("and their exit codes still decide.")
    if not confirm("Add it?", assume_yes):
        print("No git hooks changed (orch init --no-git-hooks skips this question).")
        return
    try:
        chain = githooks.install(here, python, state.hook_script)
    except (gitio.GitError, gitio.UntrustedRepositoryError, githooks.Refused, OSError) as exc:
        print(f"Could not add the push line to this repository's git hooks: {exc}", file=sys.stderr)
        return
    print(f"Added: {chain}")
    _say_left(chain)


def _say_left(chain):
    """Name the hooks in the chain folder that orch init left as they are: another tool's."""
    from orchestraty.capture import githooks
    left = githooks.others(chain)
    if left:
        print(f"Left as found in {chain}: {', '.join(left)}, which orch init did not write"
              + ("; the push line does not run while that pre-push is there" if "pre-push" in left else "") + ".")


def _watch_here(state, path, quiet_outside=False):
    """Watch the repository holding `path`. Returns an exit code."""
    from orchestraty import config, gitio
    try:
        info = gitio.worktree_info(path)
    except gitio.GitError as exc:
        if not gitio.missing():
            print(f"Not watched: {exc}", file=sys.stderr)
            return 2
        print("git is not on the PATH, so this checkout is followed as a folder without git; install git "
              "(or put it on the PATH) to follow it as a repository.", file=sys.stderr)
        info, quiet_outside = None, False
    except gitio.UntrustedRepositoryError as exc:
        print(f"Not watched: {exc}", file=sys.stderr)
        return 2
    if info is None:
        if quiet_outside:                          # orch init outside a repository: tracking said what is followed
            return 0
        from orchestraty.livemap import snapshots
        ok, why = snapshots.can_follow(path, state.root)
        if not ok:
            print(f"orch watch: {path} cannot be followed: it is {why}", file=sys.stderr)
            return 2
        folder = os.path.normpath(path)
        added = config.watch(state, folder)
        name = os.path.basename(folder)
        print(f"Watching {name}, a folder without git: Orchestraty follows it through its own snapshots, in its "
              "state folder; nothing is written into it." if added else f"Already watching {name}.")
        return 0
    added = config.watch(state, info["common_dir"], former_folder=info["top"])
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
    except (gitio.GitError, gitio.UntrustedRepositoryError) as exc:
        return _refuse("unwatch", exc)
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


def cmd_doctor(args):
    from orchestraty import doctor
    return doctor.run(State(), os.getcwd(), index=not args.no_index)


SWITCH_KEYS = {"track": "watch_all"}           # orch config's name -> config.json's key
TRACK_ON = ("Every project an agent works in is followed from its next step, on this computer only: a git repository, "
            "or a folder without git through Orchestraty's own snapshots in its state folder. orch unwatch in a "
            "project leaves it out.")
TRACK_OFF = "Only the projects you watch (orch watch) are followed."


def set_switch(state, name, on):
    """Turn steer, wake or track (watch_all) on or off in config.json; for wake, also update the plugin's switch
    when the plugin runs the hooks, or else reinstall the hooks where orch init installed them. orch config and orch ui both
    use it. Returns (the lines to show, hooks.Refused or None when the settings file would not
    take the change; config.json is saved either way)."""
    from orchestraty import config
    if name == "track":
        config.set_watch_all(state, on)
        return [f"track: {'on' if on else 'off'}", TRACK_ON if on else TRACK_OFF], None
    cfg = config.load(state)
    cfg[name] = on
    config.save(state, cfg)
    lines = [f"{name}: {'on' if on else 'off'}"]
    if name == "wake":
        switch = plugin.read_switch(state)
        if switch is not None:                   # the plugin runs the hooks: its switch carries wake
            plugin.write_switch(state, switch[0], cfg["wake"])
            return lines + ["Updated the plugin's hooks; sessions started from now on use it."], None
        path = cfg["settings"]
        if not path:
            return lines + ["Run orch init to install the hooks; the wake hook comes with them."], None
        try:
            mod = hooks.mod_in(hooks.load_settings(path), state.mod)
            changed, saved = hooks.install(state, path, hooks.hook_python(), cfg["wake"], mod)
        except hooks.Refused as exc:
            return lines, exc
        if changed:
            lines.append(f"Updated the hooks in {path}." + (f" Backup: {saved}" if saved else ""))
            lines.append("Sessions started from now on use it.")
    return lines, None


def cmd_config(args):
    from orchestraty import config
    state = State()
    cfg = config.load(state)
    if args.name is None:
        for name in ("steer", "wake", "track"):
            print(f"{name}: {'on' if cfg[SWITCH_KEYS.get(name, name)] else 'off'}")
        return 0
    if args.value is None:
        print(f"{args.name}: {'on' if cfg[SWITCH_KEYS.get(args.name, args.name)] else 'off'}")
        return 0
    lines, refused = set_switch(state, args.name, args.value == "on")
    print("\n".join(lines))
    if refused:
        return _refuse("config", refused)
    return 0


def cmd_status(args):
    from orchestraty import config, status
    state = State()
    data = status.collect(state, include_ended=args.all)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        sys.stdout.write(status.render(data, wake=config.load(state)["wake"]))
        if not any(s["state"] == "live" for repo in data["repositories"] for s in repo["sessions"]):
            _show_connections(state, "all")
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
    print(feedback.printable(f"{finding['rule']} {feedback.what(finding)}: labelled {args.verdict}. "
                             f"{_recorded_when(state)}"))
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


def _same_folder(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _pick_session(sessions, top):
    """The session the person picks among several working in one worktree; asked in the terminal."""
    ids = ", ".join(s["id"][:8] for s in sessions)
    if not (sys.stdin and sys.stdin.isatty()):
        raise ValueError(f"{len(sessions)} live sessions work in {top}: pass --session, one of {ids}")
    print(f"{len(sessions)} live sessions work in {top}:")
    for n, s in enumerate(sessions, 1):
        last = time.strftime("%H:%M", time.localtime(s["last_seen"] / 1e9))
        print(f"  {n}. {s['id'][:8]} ({s['agent']}), last event {last}")
    try:
        answer = input(f"Which one claims it? [1-{len(sessions)}] ").strip()
    except EOFError:
        answer = ""
    if answer.isdigit() and 1 <= int(answer) <= len(sessions):
        return sessions[int(answer) - 1]
    raise ValueError("no session picked; nothing claimed")


CLAIM_CATCH_UP = 3.0        # seconds orch claim waits for the record to show a session that just started


def _live_sessions(state):
    """([live session], {repository id: git common folder}) from the record, read without changing it."""
    from orchestraty.coordinator.record import Record
    if not os.path.exists(state.record):
        return [], {}
    record = Record(state.record, readonly=True)
    try:
        return ([s for s in record.sessions(states=("live",)) if s["agent"] != "cloud"],
                {r["id"]: r["common_dir"] for r in record.repos()})
    finally:
        record.close()


def _spool_recent(state, seconds=5):
    """Whether a hook wrote to the spool in the last `seconds`: then the record may not show that
    event yet, because the coordinator records it within about a second."""
    now = time.time()
    try:
        for agent in os.scandir(state.spool):
            if agent.is_dir(follow_symlinks=False):
                for entry in os.scandir(agent.path):
                    if entry.name.endswith(".jsonl") and now - entry.stat().st_mtime < seconds:
                        return True
    except OSError:
        pass
    return False


def _claim_session(state, ref):
    """(session, git common folder, worktree top, folder that relative targets start from) for
    orch claim: the live session `ref` names (its id or first characters), else the one working
    in this worktree, a session with hooks before a plain-git one. Asks which when several work
    here. Reads the record without changing it."""
    from orchestraty import claims, config, gitio
    cwd = os.getcwd()
    info = None
    if not ref:
        try:
            info = gitio.worktree_info(cwd)
        except (gitio.GitError, gitio.UntrustedRepositoryError) as exc:
            raise ValueError(str(exc)) from None
        if info is None:
            raise ValueError(f"{cwd} is not inside a git worktree: run it in the session's worktree, or pass --session")

    def matching(sessions):
        if ref:
            return [s for s in sessions if s["id"] == ref] or [s for s in sessions if s["id"].startswith(ref)]
        here = [s for s in sessions if _same_folder(s["worktree"], info["top"])]
        return [s for s in here if s["agent"] != "git"] or here

    sessions, repos = _live_sessions(state)
    deadline = time.monotonic() + CLAIM_CATCH_UP
    while not matching(sessions) and time.monotonic() < deadline and _spool_recent(state):
        time.sleep(0.25)                    # a session that just started: its first event is not recorded yet
        sessions, repos = _live_sessions(state)
    found = matching(sessions)
    if ref:
        if len(found) > 1:
            raise ValueError(f"{ref} is the start of {len(found)} live sessions' ids: give more of it")
        if not found:
            raise ValueError(f"no live session has the id {ref} (orch status lists them)")
        session = found[0]
        top, common_dir = session["worktree"], repos.get(session["repo_id"], "")
        base = cwd if claims.inside(cwd, top) else top
    else:
        if not found:
            raise ValueError(f"no live session works in {info['top']}. A session with hooks shows from its first "
                             "event, one without (plain git) once its worktree has changes; or pass --session")
        session = found[0] if len(found) == 1 else _pick_session(found, info["top"])
        top, common_dir, base = info["top"], info["common_dir"], cwd
    if not common_dir or not config.is_watched(config.load(state), common_dir):
        raise ValueError(f"{os.path.basename(top) or top} is not watched: run orch watch in it first")
    return session, common_dir, top, base


def cmd_claim(args):
    from orchestraty import claims, config
    state = State()
    try:
        minutes = claims.parse_minutes(args.duration) if args.duration else claims.default_minutes(config.load(state))
        session, common_dir, top, base = _claim_session(state, args.session)
        kind, target, file = claims.resolve(top, base, args.target)
    except (ValueError, claims.Refused) as exc:
        return _refuse("claim", exc)
    claims.append_claim(state, session["id"], common_dir, kind, target, file, args.hard, minutes)
    until = time.strftime("%H:%M", time.localtime(time.time() + minutes * 60))
    what = claims.describe({"kind": kind, "target": target, "file": file})
    print(f"Claimed {what} for session {session['id'][:8]} ({session['agent']}) until {until}"
          + (", hard." if args.hard else "."))
    if args.hard:
        print("Another session's edit there waits for its user's answer where its agent can ask (Claude Code); "
              "a session whose agent cannot ask (Codex, Antigravity) is told only.")
    else:
        print("Another session is told before it edits there (an Antigravity session once it has changed it).")
    print(_recorded_when(state))
    return 0


def _claims_now(state):
    """The watched repositories as orch status has them, and [(repository, session, claim)] for
    the claims that hold, by number."""
    from orchestraty import status
    data = status.collect(state)
    rows = [(r, s, c) for r in data["repositories"] for s in r["sessions"] for c in s["claims"]]
    return data["repositories"], sorted(rows, key=lambda row: row[2]["id"])


def _hhmm(iso):
    import calendar
    return time.strftime("%H:%M", time.localtime(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))))


def cmd_claims(args):
    from orchestraty import claims
    repos, rows = _claims_now(State())
    if not rows:
        print("No claim holds. orch claim <file, folder or function> makes one for the session in this worktree.")
        return 0
    for repo in repos:
        table = [(f"#{c['id']}", s["id"][:8], s["agent"], claims.describe(c), "until " + _hhmm(c["until"]),
                  "hard" if c["hard"] else "") for r, s, c in rows if r is repo]
        if not table:
            continue
        print(f"{repo['name']}  ({repo['common_dir']})")
        widths = [max(len(t[i]) for t in table) for i in range(len(table[0]))]
        for t in table:
            print("  " + "  ".join(v.ljust(w) for v, w in zip(t, widths)).rstrip())
    print("Release one: orch release <its number, or what it claims>.")
    return 0


def cmd_release(args):
    import re
    from orchestraty import claims, gitio
    state = State()
    ref = args.target.strip()
    rows = _claims_now(state)[1]
    if re.fullmatch(r"#?\d+", ref):
        chosen = [(r, s, c) for r, s, c in rows if c["id"] == int(ref.lstrip("#"))]
        if not chosen:
            return _refuse("release", f"no claim {ref} holds (orch claims lists those that do)")
        repo = chosen[0][0]
        claims.append_release(state, repo["common_dir"], claim_id=chosen[0][2]["id"])
    else:
        try:
            info = gitio.worktree_info(os.getcwd())
        except (gitio.GitError, gitio.UntrustedRepositoryError) as exc:
            return _refuse("release", exc)
        if info is None:
            return _refuse("release", f"{os.getcwd()} is not inside a git worktree: release by number (orch claims)")
        mine = [(r, s, c) for r, s, c in rows if _same_folder(r["common_dir"], info["common_dir"])]
        target = ref if any(c["target"] == ref for _, _, c in mine) else None
        if target is None:
            try:
                target = claims.resolve(info["top"], os.getcwd(), ref)[1]
            except claims.Refused as exc:
                return _refuse("release", exc)
        chosen = [(r, s, c) for r, s, c in mine if c["target"] == target]
        if not chosen:
            return _refuse("release", f"no claim on {target} holds in this repository (orch claims lists those "
                                      "that do)")
        claims.append_release(state, chosen[0][0]["common_dir"], target=target)
    for _, s, c in chosen:
        print(f"Released #{c['id']}: {claims.describe(c)}, claimed by {s['id'][:8]} ({s['agent']}).")
    print(_recorded_when(state))
    return 0


def cmd_handoff(args):
    from orchestraty import handoff
    state = State()
    try:
        session = handoff.pick_session(state, args.session, os.getcwd())
        if args.tests:
            print(f"Running {args.tests} in {session['worktree']} (up to {args.timeout} s)...")
        packet = handoff.build(state, session, task=args.task, tests=args.tests, timeout=args.timeout)
    except handoff.Refused as exc:
        return _refuse("handoff", exc)
    out = os.path.abspath(args.out) if args.out else None
    if out and os.path.exists(out):
        return _refuse("handoff", f"{out} exists already: name a file that does not")
    packet_id, md = handoff.save(state, packet)
    if out:
        with open(md, "r", encoding="utf-8") as src, open(out, "w", encoding="utf-8", newline="\n") as dst:
            dst.write(src.read())
    s = packet["session"]
    print(f"Handoff {packet_id}: session {s['id'][:8]} ({s['agent']}, {s['branch'] or 'detached HEAD'}), "
          f"head {packet['head'][:12] or '(none)'}.")
    print(f"Packet: {md} (and {packet_id}.json beside it)" + (f"; a copy in {out}" if out else "") + ".")
    if packet["uncommitted"]:
        print(f"{len(packet['uncommitted'])} file(s) were not committed: they are not in the head, and stay in "
              f"{s['worktree']}.")
    if packet["tests"]:
        t = packet["tests"]
        print("Tests: " + (f"did not finish within {t['timeout']} s." if t["timed_out"]
                           else f"exited {t['exit_code']}."))
    print(f"Resume it: orch resume {packet_id} --agent claude|codex|antigravity|\"<command>\"")
    return 0


def cmd_resume(args):
    from orchestraty import handoff, resume
    state = State()
    try:
        packet, md = handoff.load(state, args.id)
        with open(md, "r", encoding="utf-8") as fh:
            text = fh.read()
        p = resume.plan(packet, md, text, args.agent, args.worktree)
    except (handoff.Refused, resume.Refused) as exc:
        return _refuse("resume", exc)
    except OSError as exc:
        return _refuse("resume", f"the packet could not be read: {exc}")
    print(f"orch resume {packet['id']} makes a worktree on a new branch, {p['branch']}, from {p['head'][:12]}, and "
          "starts the agent there:")
    for line in resume.describe(p, md, len(text)):
        print(line)
    if args.dry_run:
        return 0
    if p["missing"]:
        return _refuse("resume", f"{p['missing']} is not on PATH: install it, or pass the command to run as --agent")
    if not confirm("Go ahead?", args.yes):
        print("Nothing done.")
        return 1
    try:
        resume.make_worktree(p)
    except resume.Refused as exc:
        return _refuse("resume", exc)
    print(f"Made {p['worktree']} on {p['branch']}. Starting the agent; Orchestraty is done once it starts.")
    try:
        return resume.start(p, text)
    except OSError as exc:
        return _refuse("resume", f"the agent could not start: {exc}")


def cmd_merge_order(args):
    from orchestraty import status
    data = status.collect(State())
    lines = []
    for repo in data["repositories"]:
        if repo["merge_order"]:
            lines.append(f"{repo['name']}  ({repo['common_dir']})")
            lines += ["  " + m["line"] for m in repo["merge_order"]]
    if not lines:
        print("Nothing to say about merge order: no session uses a change another session made that breaks it.")
        return 0
    print("\n".join(lines))
    return 0


def _seconds(text):
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError("a whole number of seconds, 1 or more")
    return value


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
    return _save_shown("stats", text, args.out, args.yes, "this summary")


def _save_shown(command, text, out, assume_yes, what):
    """Save text the user has just been shown to the file they named, once they say yes."""
    path = os.path.abspath(out)
    if not confirm(f"Save {what} to {path}?", assume_yes):
        print("Not saved.")
        return 1
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        return _refuse(command, exc)
    print(f"Saved to {path}. Orchestraty sends it nowhere; sharing it is up to you.")
    return 0


def _typed_comment():
    """The user's comment, typed at the terminal: lines until an empty one, or the end of input."""
    print("Your comment, in your own words: what helped, what got in the way, what was missing. An empty line "
          "ends it.", file=sys.stderr)
    lines = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line.rstrip())
    return "\n".join(lines)


def cmd_feedback(args):
    from orchestraty import stats
    comment = args.comment
    if comment is None:
        if sys.stdin and sys.stdin.isatty():
            comment = _typed_comment()
        else:
            print("No terminal to type a comment in: --comment \"...\" adds one.", file=sys.stderr)
            comment = ""
    text = json.dumps(stats.feedback(stats.collect(State(), days=args.days), comment), indent=2) + "\n"
    sys.stdout.write(text)                              # first, so it is read before it is saved
    if not args.out:
        print("Not saved and not sent: copy it from here, or run again with --out FILE to save it. Orchestraty "
              "sends nothing anywhere.")
        return 0
    return _save_shown("feedback", text, args.out, args.yes, "this feedback")


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
    except (gitio.GitError, gitio.UntrustedRepositoryError) as exc:
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


def cmd_together(args):
    from orchestraty import config, together
    state = State()
    try:
        common_dir, name = _remote_repo(state, args)
    except ValueError as exc:
        return _refuse("together", exc)
    entry = together.settings(config.load(state), common_dir)
    if args.action == "set":
        if not (args.command or "").strip():
            return _refuse("together", 'orch together set takes the command to run, such as "python -m pytest -q"')
        changes = {"command": args.command.strip()}
        if args.timeout is not None:
            changes["timeout"] = args.timeout
        entry = together.save_settings(state, common_dir, **changes)
        print(f"Together check for {name}: `{entry['command']}`, up to {entry['timeout']} s"
              + ("." if entry["on"] else ". It is off: orch together on turns it on."))
        return 0
    if args.action == "on":
        if not entry["command"]:
            return _refuse("together", 'set the command first: orch together set "<command>"')
        together.save_settings(state, common_dir, on=True)
        print(f"Together check on for {name}. When the rules flag a medium or strong overlap between two sessions "
              f"there, `{entry['command']}` runs (up to {entry['timeout']} s) on each session's working copy alone and "
              "on both merged, in folders of Orchestraty's own outside the repository. Nothing is written to your "
              ".git, and nothing runs while it is off.")
        return 0
    if args.action == "off":
        together.save_settings(state, common_dir, on=False)
        print(f"Together check off for {name}.")
        return 0
    print(f"together check: {'on' if entry['on'] else 'off'}, for {name}; command: "
          + (f"`{entry['command']}`, up to {entry['timeout']} s" if entry["command"] else "none set"))
    return 0


def cmd_ts(args):
    from orchestraty import config
    from orchestraty.livemap import typescript
    state = State()
    try:
        common_dir, name = _remote_repo(state, args)
    except ValueError as exc:
        return _refuse("ts", exc)
    if args.action == "on":
        changes = {"on": True}
        if args.timeout is not None:
            changes["timeout"] = args.timeout
        entry = typescript.save_settings(state, common_dir, **changes)
        print(f"TypeScript contracts on for {name}. For a session that changes .ts files, the project's own "
              f"TypeScript compiler (node_modules/.bin/tsc, else tsc on PATH) writes the declarations of its working "
              f"copy and of its base commit into Orchestraty's state folder, up to {entry['timeout']} s each, never in "
              "a hook and never into the repository. Without a tsconfig.json or a compiler, or when the project does "
              "not compile, the exports are read from the source instead, and what that finds is heuristic.")
        return 0
    if args.action == "off":
        typescript.save_settings(state, common_dir, on=False)
        print(f"TypeScript contracts off for {name}: the compiler no longer runs.")
        return 0
    entry = typescript.settings(config.load(state), common_dir)
    print(f"TypeScript contracts: {'on' if entry['on'] else 'off'}, for {name}; up to {entry['timeout']} s a run.")
    key = _repo_key(common_dir)
    for sid, run in sorted(typescript.runs(state).items()):
        if run.get("repo") == key:
            print(f"  {sid[:8]}: {typescript.describe(run)} ({run.get('at', '')})")
    return 0


def _repo_key(common_dir):
    from orchestraty.coordinator.record import repo_key
    return repo_key(common_dir)


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


def cmd_ui(args):
    from orchestraty.ui import server
    return server.main(port=args.port, browser=not args.no_browser, demo=args.demo)


def _port(text):
    try:
        port = int(text)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"not a port number: {text}")
    return port


def cmd_coordinator(args):
    from orchestraty.coordinator import main as coordinator
    state = State()
    if args.action == "run":
        return coordinator.run(state)
    stopped = stop_coordinator(state, args.wait)
    if stopped is None:
        print("The coordinator is not running.")
        return 0
    if stopped:
        print("The coordinator stopped.")
        return 0
    print("The coordinator did not stop in time.", file=sys.stderr)
    return 1


def stop_coordinator(state, wait=10.0):
    """Ask the coordinator to stop and wait until it has: True when it stopped, False when it
    did not in time, None when none was running. The next agent event starts one again."""
    from orchestraty.coordinator.lock import Lock, touch
    lock = Lock(state.lock)
    if lock.acquire():
        lock.release()
        return None
    touch(state.stop)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if lock.acquire():
            lock.release()
            return True
        time.sleep(0.1)
    return False


def running_version(state):
    """The version the running coordinator says it is, or None when none runs (or it did not say)."""
    from orchestraty.coordinator.lock import is_fresh
    if not is_fresh(state.alive, 10.0, time.time()):
        return None
    try:
        with open(state.info, "r", encoding="utf-8") as fh:
            version = json.load(fh).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def _restart_older_coordinator(state):
    """After an upgrade the coordinator that runs is still the earlier version: stop it, so the
    next agent event starts this one."""
    older = running_version(state)
    if older is None or older == __version__:
        return
    if stop_coordinator(state):
        print(f"Restarted Orchestraty's background process: it ran {older}; the next agent event starts {__version__}.")
    else:
        print(f"Orchestraty's background process still runs {older}: run orch coordinator stop.", file=sys.stderr)


def cmd_restore(args):
    wrong = _wrong_target(args)
    if wrong:
        return _refuse("restore", wrong)
    if args.agent != "claude":
        return _restore_agent(args)
    state = State()
    path = _settings_target(args)
    try:
        hooks.check_settings_file(path)
        current = hooks.load_settings(path)
    except hooks.Refused as exc:
        return _refuse("restore", exc)
    found = plugin.installed(current, claude_config=args.claude_config, project=os.getcwd() if args.project else None)
    off = " and turn off the hooks of Claude Code's Orchestraty plugin" if plugin.read_switch(state) else ""
    if args.from_backup:
        question = f"Put back {path} as it was before the last orch init{off}?"
    else:
        question = f"Remove Orchestraty's hooks from {path}{off}?"
    if not confirm(question, args.yes):
        print("Nothing changed.")
        return 1
    try:
        if args.from_backup:
            if hooks.latest_backup(state, path) is None:
                print(f"No backup of {path} from before an install; run orch restore without --from-backup.")
                return 1
            switched = plugin.remove_switch(state)
            folder, others = hooks.restore_backup(state, path)
            _put_back(path, folder, others, "the hooks and the mod")
            _plugin_left(switched, found)
            return 0
        switched = plugin.remove_switch(state)
        changed, saved = hooks.uninstall(state, path)
    except hooks.Refused as exc:
        return _refuse("restore", exc)
    if changed:
        print(f"Removed Orchestraty's hooks from {path}. Backup: {saved}")
    elif not switched:
        print(f"No Orchestraty hooks in {path}; nothing changed.")
    _plugin_left(switched, found)
    return 0


def _put_back(path, folder, others, what):
    """What orch restore --from-backup says it did."""
    if others:
        print(f"Put back {path} from {folder}, without {what} of the state folder {', '.join(others)}: an orch init "
              "with another ORCH_HOME added them, and the last orch init took them out.")
    else:
        print(f"Put back {path} from {folder}.")


def _plugin_left(switched, found):
    """What orch restore says about Claude Code's Orchestraty plugin, which it cannot uninstall."""
    if switched:
        print("Turned off the hooks of Claude Code's Orchestraty plugin: they do nothing until orch init runs again.")
    if found:
        print(f"The plugin itself stays installed, and its mod, MCP server and skill still load; to remove it, run "
              f"claude plugin uninstall {found[0]}.")


def cmd_uninstall(args):
    from orchestraty import uninstall
    return uninstall.run(State(), keep_state=args.keep_state, assume_yes=args.yes,
                         ask=lambda question: confirm(question, False))


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
            folder, others = hooks.restore_backup(state, path, adapter)
            if folder is None:
                print(f"No backup of {path} from before an install; run orch restore without --from-backup.")
                return 1
            _put_back(path, folder, others, "the hooks")
            return 0
        changed, saved = hooks.uninstall_agent(state, adapter, path)
    except hooks.Refused as exc:
        return _refuse("restore", exc)
    if changed:
        print(f"Removed Orchestraty's hooks from {path}; other hooks there are kept. Backup: {saved}")
    else:
        print(f"No Orchestraty hooks in {path}; nothing changed.")
    return 0


def _add_target_options(parser, all_agents=False):
    choices = ["claude", "codex", "antigravity"] + (["all"] if all_agents else [])
    parser.add_argument("--agent", choices=choices, default="claude", help="the agent to configure (default: claude)")
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
    _add_target_options(init, all_agents=True)
    init.add_argument("--python", metavar="PATH", help="the absolute path of a Python 3.10 or later for the hooks (default: this one's base); "
                                                         "orch init runs it once to check")
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

    uninstall = commands.add_parser(
        "uninstall", help="take out everything Orchestraty added, then its state folder",
        description="Take out everything Orchestraty added: every agent's hook entries, the mod, the plugin's "
                    "hooks (and the plugin, with Claude Code's own command), the git hook chain in every watched "
                    "repository and the cloud branches' shadows; then, after asking, the state folder. Prints what "
                    "it will do first, leaves every entry that is not Orchestraty's, puts back the backups orch init "
                    "made where that loses nothing (or where you say so), and changes nothing when run again.")
    uninstall.add_argument("--keep-state", action="store_true",
                           help="keep the state folder: the record, the backups and the settings")
    uninstall.add_argument("--yes", action="store_true",
                           help="do not ask; a file that changed since orch init keeps its changes")
    uninstall.set_defaults(handler=cmd_uninstall)

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

    claim = commands.add_parser("claim", help="reserve a file, folder or function for a session",
                                description="Reserve a file, a folder (every file in it) or a Python function or "
                                            "class (path::name, or module.name) for a session: another session is "
                                            "told before it edits there. A hard claim makes that edit wait for its "
                                            "user's answer, where the agent can ask. Claims end when they expire, "
                                            "are released, or their session ends.")
    claim.add_argument("target", help="a file, a folder, path::name or module.name")
    claim.add_argument("--hard", action="store_true", help="another session's edit there asks its user first")
    claim.add_argument("--for", dest="duration", metavar="TIME",
                       help="how long it holds: 30m, 2h or 1h30m, up to 24h (default: claim_minutes, 120)")
    claim.add_argument("--session", metavar="ID", help="the session it is for (its id, or its first characters); "
                                                        "default: the live session in this worktree")
    claim.set_defaults(handler=cmd_claim)
    claims_list = commands.add_parser("claims", help="the claims that hold",
                                      description="List the claims that hold in each watched repository. Reads the "
                                                  "record without changing it.")
    claims_list.set_defaults(handler=cmd_claims)
    release = commands.add_parser("release", help="end a claim",
                                  description="End a claim before it expires: by its number (orch claims), or by "
                                              "what it claims, which ends every claim on it in this repository.")
    release.add_argument("target", help="the claim's number, or the file, folder or function it claims")
    release.set_defaults(handler=cmd_release)

    hand = commands.add_parser("handoff", help="package a session's work for another agent",
                               description="Package a session's work from the record and git: its task line, "
                                           "branch, base commit and head, the files it changed, its contract "
                                           "changes, the open findings that involve it and the callers it left "
                                           "behind. Stored as Markdown and JSON in the state folder; no prompt or "
                                           "transcript is ever read.")
    hand.add_argument("--session", metavar="ID", help="the session (its id, or its first characters; default: the "
                                                      "one working in this worktree)")
    hand.add_argument("--task", help="the task line (default: the one the agent declared, else the branch)")
    hand.add_argument("--tests", metavar="COMMAND", help="a command that runs the tests, run in the session's "
                                                         "worktree; its exit code and last lines go in the packet")
    hand.add_argument("--timeout", type=_seconds, default=600, metavar="SECONDS",
                      help="the time --tests may take (default 600)")
    hand.add_argument("--out", metavar="FILE", help="also write the Markdown to FILE")
    hand.set_defaults(handler=cmd_handoff)
    res = commands.add_parser("resume", help="start an agent on a handoff, in a new worktree",
                              description="Make a worktree on a new branch from a handoff's head, and start an "
                                          "agent's own command-line tool there, in this terminal, with the packet "
                                          "as its first message. Says what will run and asks first. The agent keeps "
                                          "its own permissions and model settings.")
    res.add_argument("id", help="the handoff's id (orch handoff printed it), or its first characters")
    res.add_argument("--agent", required=True, metavar="AGENT",
                     help="claude, codex or antigravity, or a command of your own, which gets the packet on stdin")
    res.add_argument("--worktree", metavar="PATH", help="where to make the worktree (default: beside the session's)")
    res.add_argument("--dry-run", action="store_true", help="only say what would run")
    res.add_argument("--yes", action="store_true", help="do not ask first")
    res.set_defaults(handler=cmd_resume)
    merge = commands.add_parser("merge-order", help="which session's branch to merge first",
                                description="Merge-order advice from the findings: a session whose work uses "
                                            "another's breaking change merges after it; two that use each other's "
                                            "merge together. Reads the record without changing it.")
    merge.set_defaults(handler=cmd_merge_order)

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

    feedback = commands.add_parser(
        "feedback", help="your feedback: the stats summary and your comment, shown first; sends nothing",
        description="Your feedback for whoever asked you to try Orchestraty: the summary of orch stats (counts "
                    "only) and your own comment, typed at a prompt. Prints it all; saves it only to the file you "
                    "name, once you have seen it; sends nothing anywhere: sharing it is up to you.")
    feedback.add_argument("--out", metavar="FILE", help="save it to FILE once you have seen it")
    feedback.add_argument("--days", type=_days, default=14, metavar="N", help="the last N days (default 14)")
    feedback.add_argument("--comment", metavar="TEXT", help="your comment, instead of typing it at the prompt")
    feedback.add_argument("--yes", action="store_true", help="save without asking")
    feedback.set_defaults(handler=cmd_feedback)

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

    tog = commands.add_parser("together", help="the together check: run a check on two sessions' changes merged",
                              description="Opt-in, for each repository: when the rules flag a medium or strong "
                                          "overlap between two sessions, run your command on each session's working "
                                          "copy alone and on both merged, outside the repository, and tell both "
                                          "sessions when they pass alone and fail together. Off by default.")
    tog.add_argument("action", nargs="?", choices=["on", "off", "set"])
    tog.add_argument("command", nargs="?", help='with set: the command to run, such as "python -m pytest -q"')
    tog.add_argument("--timeout", type=_seconds, metavar="SECONDS", help="with set: the time it may take (default 120)")
    tog.add_argument("--repo", metavar="DIR", help="a folder inside the repository (default: here)")
    tog.set_defaults(handler=cmd_together)
    ts = commands.add_parser("ts", help="TypeScript contracts: compare the exported API of .ts files",
                             description="Opt-in, for each repository: for a session that changes .ts files, run the "
                                         "project's TypeScript compiler on its working copy and its base commit, "
                                         "writing the declarations into the state folder (never into the repository, "
                                         "never in a hook), and compare the exported API. Off by default.")
    ts.add_argument("action", nargs="?", choices=["on", "off"])
    ts.add_argument("--timeout", type=_seconds, metavar="SECONDS", help="with on: the time a run may take (default 300)")
    ts.add_argument("--repo", metavar="DIR", help="a folder inside the repository (default: here)")
    ts.set_defaults(handler=cmd_ts)

    cfg = commands.add_parser("config", help="show or switch steer and wake",
                              description="steer: a strong note stops the edit it rides on, so the agent re-plans "
                                          "(off: the note only informs). wake: a strong note wakes a session that "
                                          "waits for its user (installs a background hook). track: every project an "
                                          "agent works in is followed, with or without git (orch unwatch leaves one "
                                          "out).")
    cfg.add_argument("name", nargs="?", choices=["steer", "wake", "track"])
    cfg.add_argument("value", nargs="?", choices=["on", "off"])
    cfg.set_defaults(handler=cmd_config)

    doctor = commands.add_parser("doctor", help="check what Orchestraty needs, with a fix for each problem",
                                 description="Check the Python, the state folder, the hook script and each agent's "
                                             "hooks, that the coordinator starts, git's version and the engine, "
                                             "and time the first index of the repository here. Each problem comes "
                                             "with its fix; the exit code is 1 when there is one.")
    doctor.add_argument("--no-index", action="store_true", help="do not time the first index of this repository")
    doctor.set_defaults(handler=cmd_doctor)

    ui = commands.add_parser("ui", help="the dashboard, in your browser (this computer only)",
                             description="Serve the dashboard on 127.0.0.1 and open it in the browser through a link "
                                         "that works once. Runs until Ctrl+C.")
    ui.add_argument("--port", type=_port, default=None, metavar="N",
                    help="the port to listen on (default: 47392, or a free one when that is taken)")
    ui.add_argument("--no-browser", action="store_true", help="only print the link")
    ui.add_argument("--demo", action="store_true",
                    help="a sandbox: two pretend sessions in a temporary folder, run step by step from the page")
    ui.set_defaults(handler=cmd_ui)

    coordinator = commands.add_parser("coordinator", help="run or stop the coordinator by hand",
                                      description="Hooks start the coordinator when needed; these are for "
                                                  "debugging.")
    coordinator.add_argument("action", choices=["run", "stop"])
    coordinator.add_argument("--wait", type=float, default=10.0, help="seconds to wait for a stop (default 10)")
    coordinator.set_defaults(handler=cmd_coordinator)
    return parser


def safe_output(streams=None):
    """Let stdout and stderr take any text. On Windows a pipe or a file gets the ANSI code page
    (cp1252), which has no Japanese and no U+FFFD, so a name or a test's output with one ended
    the command with a traceback, often after it had done its work: a pipe or a file gets UTF-8,
    as orch writes its files. A terminal keeps its encoding (a Windows console takes any text);
    what it cannot show becomes "?" on stdout, and an escape on stderr, as Python has it."""
    for stream, errors in streams or ((sys.stdout, "replace"), (sys.stderr, "backslashreplace")):
        if not hasattr(stream, "reconfigure"):
            continue                                   # None (no console), or not a text stream
        try:
            if stream.isatty():
                stream.reconfigure(errors=errors)
            else:
                stream.reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError):
            pass                                       # closed: keep it as it is


def main(argv=None):
    safe_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    import sqlite3
    try:
        return handler(args)
    except sqlite3.DatabaseError as exc:                # only the record is SQLite
        print(f"orch: the record {State().record} cannot be read ({exc}); orch doctor says what to do",
              file=sys.stderr)
        return 1
