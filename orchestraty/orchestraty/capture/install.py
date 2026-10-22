"""Installs the hook script, and an agent's hook entries into its config file, and takes them
out again.

Which entries go where is the agent's adapter's business (``orchestraty/adapters``); this module
does the file work for every agent: the hook script copied into the state folder's ``hooks``
folder, the file copied to the state folder's ``backups`` before every change, written whole
(never edited in place), and a config file that is not valid JSON, or that is a link, refused and
left alone. Claude Code's entries (``adapters/claude``) are installed by ``install``; its names
are kept here too, where the rest of Orchestraty finds them.
"""
import json
import os
import shutil
import sys
import time

from orchestraty.adapters.claude import (AGENT, EDIT_MATCHER, EVENTS, MATCHERS, MOD_SOURCE, PLUGIN_DIRS,  # noqa: F401
                                         TIMEOUT_SECONDS, WAKE_TIMEOUT_SECONDS, hook_block, hook_entry,
                                         install_mod, merged_settings, mod_in, settings_path, wake_entry,
                                         with_mod, without_mod, without_ours)

HOOK_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")
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


def launch_lines(python=None):
    """What the hook script needs to start the coordinator: the interpreter that has this
    package, and the import path that finds this package and the engine where they are now
    (installed or not)."""
    import orchestraty
    paths = [os.path.dirname(os.path.dirname(os.path.abspath(orchestraty.__file__)))]
    try:
        import orch_engine
        paths.append(os.path.dirname(os.path.dirname(os.path.abspath(orch_engine.__file__))))
    except ImportError:
        pass
    unique = []
    for p in paths:
        if not any(_same(p, q) for q in unique):
            unique.append(p)
    return [python or sys.executable, os.pathsep.join(unique)]


def write_launch(state, python=None):
    os.makedirs(state.hooks, exist_ok=True)
    _write_atomic(state.launch, ("\n".join(launch_lines(python)) + "\n").encode("utf-8"))
    return state.launch


def install(state, path, python, wake=False, mod=False):
    """Install the hook script and merge our entries into the settings file at `path` (with
    the wake entry when `wake`, and our Claude Code mod when `mod`). Returns (changed, backup
    folder or None)."""
    check_python(python)
    check_settings_file(path)
    current = load_settings(path)
    script = install_script(state)
    write_launch(state)
    wanted = merged_settings(current, python, script, wake)
    wanted = with_mod(wanted, install_mod(state)) if mod else without_mod(wanted, state.mod)
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
    wanted = without_mod(without_ours(current, state.hook_script), state.mod)
    if wanted == current:
        return False, None
    saved = backup(state, path, "uninstall")
    _write_atomic(path, _dump(wanted))
    return True, saved


def install_agent(state, adapter, path, python):
    """Install the hook script and merge an agent's entries (its adapter's ``merged``) into its
    config file at `path`. Returns (changed, backup folder or None)."""
    check_python(python)
    check_settings_file(path)
    current = load_settings(path)
    try:
        wanted = adapter.merged(current, python, state.hook_script)
    except ValueError as exc:
        raise Refused(f"{path}: {exc}; fix it first") from exc
    install_script(state)
    write_launch(state)
    if os.path.exists(path) and wanted == current:
        return False, None
    saved = backup(state, path, "install")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_atomic(path, _dump(wanted))
    return True, saved


def uninstall_agent(state, adapter, path):
    """Take an agent's entries out of its config file (its adapter's ``without_ours``). Returns
    (changed, backup folder or None)."""
    check_settings_file(path)
    current = load_settings(path)
    wanted = adapter.without_ours(current, state.hook_script)
    if not os.path.exists(path) or wanted == current:
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
