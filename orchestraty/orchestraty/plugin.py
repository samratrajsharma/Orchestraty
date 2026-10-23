"""Orchestraty's Claude Code plugin (``plugins/claude-code`` in the repository): what its hook
commands run, whether Claude Code has it installed, and the switch that turns its hooks on.

The plugin holds the hooks ``orch init`` would otherwise add to the settings file (the same
events and matchers), the mod, the MCP server (``orch mcp``) and a skill. Its files are fixed
text, so a hook command cannot name an interpreter or the state folder. Each is a short sh
command instead: Claude Code runs a plugin's command hooks through sh on macOS and Linux, and
through Git Bash on Windows. It finds the state folder as the rest of Orchestraty does
(``ORCH_HOME``; else ``%USERPROFILE%\\.orchestraty`` on Windows; else
``$XDG_STATE_HOME/orchestraty`` or ``~/.local/state/orchestraty``), reads the interpreter from
``hooks/plugin.txt`` there, and replaces itself (``exec``) with the installed hook script run as
the settings hooks run it, ``<python> -S -I <state>/hooks/hook.py claude <event>``. With no
``plugin.txt``, no interpreter at the path it names, or no hook script, it does nothing and
exits 0. sh adds about a millisecond to the hook (``tests/test_plugin.py`` measures it).

``plugin.txt`` is the switch, so the plugin's hooks and the settings hooks never both run. orch
init writes it when the plugin is installed, and then adds no hooks to the settings file (and
takes out those an earlier orch init added); when the plugin is not installed it adds the
settings hooks and removes the file. A plugin installed after orch init therefore does nothing
until orch init runs again; orch restore removes the file. Line 1 is the interpreter, line 2
``wake`` while the wake hook is on (the plugin's asynchronous Stop hook waits only then).

Installed: Claude Code lists each install in ``installed_plugins.json`` under its plugins
folder (``<config>/plugins``, or ``CLAUDE_CODE_PLUGIN_CACHE_DIR``), as ``<name>@<marketplace>``
with its scope (``user``; ``project`` or ``local`` with the folder it is for). The plugin is
taken as installed for a settings file when one of its installs reaches the sessions that file
does (a user install for the user's settings; for a project's ``settings.local.json``, a user
install or one for that folder) and that file does not turn it off in ``enabledPlugins``.
"""
import json
import os
import shutil

from orchestraty.adapters import claude

NAME = "orchestraty"
SWITCH = "plugin.txt"
WAKE = "wake"
CACHE_DIR = "CLAUDE_CODE_PLUGIN_CACHE_DIR"
GIT_BASH = "CLAUDE_CODE_GIT_BASH_PATH"
INSTALL = f"/plugin install {NAME}@{NAME}"
DESCRIPTION = ("Orchestraty's hooks: each runs the hook script orch init installs in Orchestraty's state folder, "
               "with the interpreter orch init names in hooks/plugin.txt there. Without that file they do nothing.")

# The state folder, as paths.state_root and the mod's stateFolder find it.
STATE_SH = ('s=$ORCH_HOME; if [ -z "$s" ]; then if [ "$OS" = Windows_NT ]; then s=$USERPROFILE/.orchestraty; '
            'else case $XDG_STATE_HOME in /*) s=$XDG_STATE_HOME/orchestraty;; *) s=$HOME/.local/state/orchestraty;; '
            'esac; fi; fi; ')
_RUN = '[ -x "$p" ] && [ -f "$s/hooks/hook.py" ] && exec "$p" -S -I "$s/hooks/hook.py" claude %s; exit 0'


def command(event):
    """The plugin's shell command for one event."""
    return STATE_SH + '{ IFS= read -r p; } 2>/dev/null <"$s/hooks/plugin.txt" && ' + _RUN % event


def wake_command():
    """The asynchronous Stop hook's command: the wake waiter, only while the switch says wake."""
    return (STATE_SH + '{ IFS= read -r p; IFS= read -r w; } 2>/dev/null <"$s/hooks/plugin.txt" && [ "$w" = wake ] && '
            + _RUN % "Wake")


def hooks_file(modules):
    """The plugin's ``hooks/hooks.json``: the mod's hooks module(s), and a command hook for each
    event orch init would add to the settings file, with the same matchers and timeouts."""
    block = {}
    for event in claude.EVENTS:
        entry = {"matcher": claude.MATCHERS[event]} if event in claude.MATCHERS else {}
        entry["hooks"] = [{"type": "command", "command": command(event), "timeout": claude.TIMEOUT_SECONDS}]
        block[event] = [entry]
    block["Stop"].append({"hooks": [{"type": "command", "command": wake_command(), "asyncRewake": True,
                                     "timeout": claude.WAKE_TIMEOUT_SECONDS}]})
    return {"description": DESCRIPTION, "modules": list(modules), "hooks": block}


# ------------------------------------------------------------------------------ the switch
def switch_path(state):
    return os.path.join(state.hooks, SWITCH)


def read_switch(state):
    """(interpreter, wake) from plugin.txt, or None when there is none."""
    try:
        with open(switch_path(state), "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return (lines[0] if lines else "", len(lines) > 1 and lines[1] == WAKE)


def write_switch(state, python, wake):
    """Turn the plugin's hooks on, with this interpreter (and the wake waiter when `wake`)."""
    os.makedirs(state.hooks, exist_ok=True)
    text = python + "\n" + (WAKE + "\n" if wake else "")
    tmp = switch_path(state) + ".orch-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, switch_path(state))


def remove_switch(state):
    """Turn the plugin's hooks off. True when there was a switch to remove."""
    try:
        os.remove(switch_path(state))
        return True
    except FileNotFoundError:
        return False


# ------------------------------------------------------------------------------ installed?
def plugins_folder(claude_config=None, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get(CACHE_DIR):
        return os.path.abspath(os.path.expanduser(environ[CACHE_DIR]))
    folder = claude_config or environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(os.path.abspath(folder), "plugins")


def installs(claude_config=None, environ=None):
    """[(plugin id, install record)] for every install of a plugin named orchestraty."""
    path = os.path.join(plugins_folder(claude_config, environ), "installed_plugins.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    found = []
    for pid, records in (plugins.items() if isinstance(plugins, dict) else ()):
        if not isinstance(pid, str) or pid.split("@", 1)[0] != NAME:
            continue
        for record in records if isinstance(records, list) else [records]:
            if isinstance(record, dict):
                found.append((pid, record))
    return found


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def installed(settings, claude_config=None, project=None, environ=None):
    """The plugin's install that reaches the sessions `settings` (a settings file's contents)
    covers, as (plugin id, install record), or None. `project`: the folder of a project's
    settings.local.json, whose sessions a user install reaches too."""
    enabled = settings.get("enabledPlugins") if isinstance(settings.get("enabledPlugins"), dict) else {}
    for pid, record in installs(claude_config, environ):
        scope = record.get("scope")
        reaches = scope == "user" or (project is not None and scope in ("project", "local")
                                      and isinstance(record.get("projectPath"), str)
                                      and _same(record["projectPath"], project))
        if reaches and enabled.get(pid) is not False:
            return pid, record
    return None


def installed_hooks(record):
    """The hooks.json of an installed copy, or None when it cannot be read."""
    folder = record.get("installPath")
    if not isinstance(folder, str):
        return None
    try:
        with open(os.path.join(folder, "hooks", "hooks.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def git_bash(environ=None, which=shutil.which):
    """Windows: the Git Bash Claude Code runs a plugin's hooks with (CLAUDE_CODE_GIT_BASH_PATH,
    else bin\\bash.exe beside the git on the PATH), or None."""
    environ = os.environ if environ is None else environ
    given = environ.get(GIT_BASH)
    if given:
        return given if os.path.isfile(given) else None
    git = which("git")
    if not git:
        return None
    folder = os.path.dirname(os.path.realpath(git))
    for up in (1, 2):                                # Git\cmd\git.exe, Git\mingw64\bin\git.exe
        root = folder
        for _ in range(up):
            root = os.path.dirname(root)
        bash = os.path.join(root, "bin", "bash.exe")
        if os.path.isfile(bash):
            return bash
    return None
