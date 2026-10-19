"""Installs the hook script and Claude Code's hook entries, and takes them out again.

``install`` copies the hook script into the state folder's ``hooks`` folder and merges one
entry per captured event into a Claude Code settings file. By default that is the user's own
settings (``~/.claude/settings.json``, or the folder ``CLAUDE_CONFIG_DIR`` names), so the
hooks run in every worktree, including ones made after installing; a project's
``.claude/settings.local.json`` covers only that folder. Each entry is in exec form (no shell):
``command`` is an absolute Python and ``args`` is ``["-S", "-I", <hook script>, "claude",
<event>]``.

Install rules: other settings and other hooks are kept; our entries (those that run our hook
script) are replaced, so installing twice changes nothing; the file is copied to the state
folder's ``backups`` before every change; a settings file that is not valid JSON, or that is
a link, is refused and left alone.
"""
import json
import os
import shutil
import sys
import time

HOOK_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")
EDIT_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"
# The Claude Code events captured. PreToolUse runs for the edit tools only; PostToolUse for
# every tool, since a shell command can change files too.
EVENTS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
          "PostToolUseFailure", "Stop", "SubagentStop", "StopFailure")
MATCHERS = {"PreToolUse": EDIT_MATCHER, "PostToolUse": "*", "PostToolUseFailure": "*"}
TIMEOUT_SECONDS = 10
AGENT = "claude"
BACKUP_NAME = "settings.json"
BACKUP_SOURCE = "source.txt"


class Refused(Exception):
    """Something the installer will not do; the message says why and what to do instead."""


def hook_python():
    """The interpreter the hooks run. On Windows a virtual environment's python.exe is a
    launcher that starts a second process, so the base interpreter is used: the hook script
    needs built-in modules only."""
    base = getattr(sys, "_base_executable", None)
    if os.name == "nt" and base and os.path.isfile(base):
        return base
    return sys.executable


def settings_path(claude_config=None, project=None, environ=None):
    """The Claude Code settings file to change: a project folder's settings.local.json, the
    settings.json in a given Claude Code config folder, or the user's own settings."""
    if project:
        return os.path.join(os.path.abspath(project), ".claude", "settings.local.json")
    environ = os.environ if environ is None else environ
    folder = claude_config or environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(os.path.abspath(folder), "settings.json")


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _is_link(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    return (st.st_mode & 0o170000) == 0o120000 or bool(getattr(st, "st_reparse_tag", 0) & 0x20000000)


def check_python(python):
    if not python or not os.path.isabs(python) or not os.path.isfile(python):
        raise Refused("the hook interpreter must be the absolute path of an existing Python")
    if os.name == "nt" and not python.lower().endswith(".exe"):
        raise Refused("hooks in exec form on Windows need a real .exe")


def check_settings_file(path):
    """Refuse a settings file, or a folder holding it, that is a link: replacing it would cut
    the link (to a dotfiles repository, for example)."""
    if _is_link(path) or _is_link(os.path.dirname(path)):
        raise Refused(f"{path} is a link; add the hooks to the file it points to yourself "
                      "(orch init --print shows them)")


def load_settings(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(f"{path} could not be read as JSON ({type(exc).__name__}); fix it first") from exc
    if not isinstance(data, dict):
        raise Refused(f"{path} does not hold a JSON object")
    return data


def hook_entry(python, script, event):
    hook = {"type": "command", "command": python, "args": ["-S", "-I", script, AGENT, event],
            "timeout": TIMEOUT_SECONDS}
    entry = {"hooks": [hook]}
    if event in MATCHERS:
        entry["matcher"] = MATCHERS[event]
    return entry


def hook_block(python, script):
    """The ``hooks`` object with only our entries, for people who add it by hand."""
    return {event: [hook_entry(python, script, event)] for event in EVENTS}


def _ours(entry, script):
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        args = hook.get("args") if isinstance(hook, dict) else None
        if isinstance(args, list) and any(isinstance(a, str) and _same(a, script) for a in args):
            return True
    return False


def without_ours(settings, script):
    """The settings with every entry that runs our hook script removed (other entries kept;
    an event left with no entries, and an empty hooks object, are dropped)."""
    settings = dict(settings)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    kept = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            kept[event] = entries
            continue
        rest = [e for e in entries if not _ours(e, script)]
        if rest or not entries:
            kept[event] = rest
    if kept:
        settings["hooks"] = kept
    else:
        del settings["hooks"]
    return settings


def merged_settings(settings, python, script):
    """The settings with our entries added after any others (earlier ones of ours replaced)."""
    settings = without_ours(settings, script)
    hooks = dict(settings.get("hooks") or {})
    for event in EVENTS:
        entries = hooks.get(event)
        hooks[event] = (list(entries) if isinstance(entries, list) else []) + [hook_entry(python, script, event)]
    settings["hooks"] = hooks
    return settings


def _write_atomic(path, data):
    tmp = path + ".orch-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    with open(path, "rb") as fh:              # read back once, so a scanner's first look is off the hot path
        fh.read()


def _dump(settings):
    return (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def backup(state, path, reason):
    """Copy a settings file into the state folder's backups, noting where it came from and
    what was about to change it (install, uninstall, restore). Returns the copy's folder, or
    None when there is no file to copy."""
    if not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    for n in range(1000):
        folder = os.path.join(state.backups, f"{stamp}-{n:03d}")
        try:
            os.makedirs(folder)
            break
        except FileExistsError:
            continue
    else:
        raise Refused("could not make a backup folder")
    shutil.copyfile(path, os.path.join(folder, BACKUP_NAME))
    with open(os.path.join(folder, BACKUP_SOURCE), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(os.path.abspath(path) + "\n" + reason + "\n")
    return folder


def latest_backup(state, path, reason="install"):
    """The newest backup of `path` taken before `reason` (by default: the file as it was
    before the last install), or None."""
    try:
        names = sorted(os.listdir(state.backups), reverse=True)
    except OSError:
        return None
    for name in names:
        folder = os.path.join(state.backups, name)
        try:
            with open(os.path.join(folder, BACKUP_SOURCE), "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        if (len(lines) >= 2 and _same(lines[0], path) and lines[1] == reason
                and os.path.isfile(os.path.join(folder, BACKUP_NAME))):
            return folder
    return None


def install_script(state):
    """Copy the hook script into the state folder; returns its path."""
    os.makedirs(state.hooks, exist_ok=True)
    with open(HOOK_SOURCE, "rb") as fh:
        _write_atomic(state.hook_script, fh.read())
    return state.hook_script


def install(state, path, python):
    """Install the hook script and merge our entries into the settings file at `path`.
    Returns (changed, backup folder or None)."""
    check_python(python)
    check_settings_file(path)
    current = load_settings(path)
    script = install_script(state)
    wanted = merged_settings(current, python, script)
    if os.path.exists(path) and wanted == current:
        return False, None
    saved = backup(state, path, "install")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_atomic(path, _dump(wanted))
    return True, saved


def uninstall(state, path):
    """Remove our entries from the settings file at `path`. Returns (changed, backup folder)."""
    check_settings_file(path)
    current = load_settings(path)
    wanted = without_ours(current, state.hook_script)
    if wanted == current:
        return False, None
    saved = backup(state, path, "uninstall")
    _write_atomic(path, _dump(wanted))
    return True, saved


def restore_backup(state, path):
    """Put back the file as it was before the last install (the current file is backed up
    first). Returns the backup folder used, or None when there is none."""
    check_settings_file(path)
    folder = latest_backup(state, path)
    if folder is None:
        return None
    with open(os.path.join(folder, BACKUP_NAME), "rb") as fh:
        data = fh.read()
    backup(state, path, "restore")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_atomic(path, data)
    return folder
