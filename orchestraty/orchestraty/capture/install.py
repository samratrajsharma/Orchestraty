"""Installs the hook script, and an agent's hook entries into its config file, and takes them
out again.

Which entries go where is the agent's adapter's business (``orchestraty/adapters``); this module
does the file work for every agent: the hook script copied into the state folder's ``hooks``
folder, the file copied to the state folder's ``backups`` before every change, written whole
(never edited in place), and a config file that is not valid JSON, or that is a link, refused and
left alone, as is one that cannot be written (read-only, or open in another program): no copy of
it is left beside it. Claude Code's entries (``adapters/claude``) are installed by ``install``; its names
are kept here too, where the rest of Orchestraty finds them. With Claude Code's Orchestraty
plugin installed, the plugin holds those hooks: ``install_for_plugin`` turns them on
(``orchestraty/plugin``) and adds none to the settings file.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

from orchestraty import plugin
from orchestraty.adapters import claude
from orchestraty.adapters.claude import (AGENT, EDIT_MATCHER, EVENTS, MATCHERS, MOD_SOURCE, PLUGIN_DIRS,  # noqa: F401
                                         TIMEOUT_SECONDS, WAKE_TIMEOUT_SECONDS, hook_block, hook_entry,
                                         install_mod, merged_settings, mod_in, other_states, settings_path,
                                         wake_entry, with_mod, without_mod, without_orchestraty, without_ours)

HOOK_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")
BACKUP_NAME = "settings.json"
BACKUP_SOURCE = "source.txt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def python_version(python):
    """(major, minor) of the Python at `python`, run as the hooks run it (-S -I), or None when it
    does not run as one (another program, a broken install, no answer in 30 seconds)."""
    probe = "import sys; sys.stdout.write('%d.%d' % sys.version_info[:2])"
    try:
        proc = subprocess.run([python, "-S", "-I", "-c", probe], capture_output=True, timeout=30,
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.fullmatch(rb"(\d+)\.(\d+)", proc.stdout.strip())
    return (int(m.group(1)), int(m.group(2))) if proc.returncode == 0 and m else None


def check_python(python):
    if not python or not os.path.isabs(python) or not os.path.isfile(python):
        raise Refused("the hook interpreter must be the absolute path of an existing Python")
    if os.name == "nt" and not python.lower().endswith(".exe"):
        raise Refused("hooks in exec form on Windows need a real .exe")
    if _same(python, sys.executable) or _same(python, hook_python()):
        return                                    # this one runs: orch runs in it
    version = python_version(python)
    if version is None or version < (3, 10):
        said = "Python %d.%d" % version if version else "not a Python"
        raise Refused(f"{python} is {said}; the hooks need Python 3.10 or later (the absolute path of its "
                      "python executable)")


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


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _write_atomic(path, data):
    """Write the file whole: a copy beside it, then the copy put in its place. When that cannot be
    done (the file is read-only, or on Windows another program has it open), the copy is deleted
    and the file is left as it was."""
    tmp = path + ".orch-tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        _discard(tmp)
        raise Refused(f"cannot write {path} ({type(exc).__name__}): it may be read-only, or open in another "
                      "program; it was left as it was") from exc
    try:
        with open(path, "rb") as fh:          # read back once, so a scanner's first look is off the hot path
            fh.read()
    except OSError:
        pass


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
    the wake entry when `wake`, and our Claude Code mod when `mod`); the plugin's hooks are
    turned off first, so the two never both run. Returns (changed, backup folder or None)."""
    check_python(python)
    check_settings_file(path)
    current = load_settings(path)
    script = install_script(state)
    write_launch(state)
    switched = plugin.remove_switch(state)
    wanted = merged_settings(current, python, script, wake)
    wanted = with_mod(wanted, install_mod(state)) if mod else without_mod(wanted, state.mod)
    if os.path.exists(path) and wanted == current:
        return switched, None
    saved = backup(state, path, "install")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_atomic(path, _dump(wanted))
    return True, saved


def install_for_plugin(state, path, python, wake=False):
    """With Claude Code's Orchestraty plugin installed, which holds the hooks and the mod:
    install the hook script, take our entries and our mod out of the settings file at `path`
    (an earlier orch init's), then turn the plugin's hooks on, with `python` and the wake
    waiter when `wake`. Returns (changed, backup folder or None)."""
    check_python(python)
    check_settings_file(path)
    current = load_settings(path)
    install_script(state)
    write_launch(state)
    wanted = without_orchestraty(current, state.hook_script, state.mod)
    changed, saved = False, None
    if wanted != current:
        saved = backup(state, path, "install")
        _write_atomic(path, _dump(wanted))
        changed = True
    if plugin.read_switch(state) != (python, bool(wake)):
        plugin.write_switch(state, python, wake)
        changed = True
    return changed, saved


def uninstall(state, path):
    """Remove our entries from the settings file at `path`. Returns (changed, backup folder)."""
    check_settings_file(path)
    current = load_settings(path)
    wanted = without_orchestraty(current, state.hook_script, state.mod)
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
    script = install_script(state)        # first: on Windows a path has its short form once it is there
    write_launch(state)
    try:
        wanted = adapter.merged(current, python, script)
    except ValueError as exc:
        raise Refused(f"{path}: {exc}; fix it first") from exc
    if os.path.exists(path) and wanted == current:
        return False, None
    saved = backup(state, path, "install")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_atomic(path, _dump(wanted))
    return True, saved


def uninstall_agent(state, adapter, path):
    """Take an agent's entries out of its config file (its adapter's ``without_orchestraty``: also
    those of another state folder). Returns (changed, backup folder or None)."""
    check_settings_file(path)
    current = load_settings(path)
    wanted = adapter.without_orchestraty(current, state.hook_script)
    if not os.path.exists(path) or wanted == current:
        return False, None
    saved = backup(state, path, "uninstall")
    _write_atomic(path, _dump(wanted))
    return True, saved


def restore_backup(state, path, adapter=claude):
    """Put back the file as it was before the last install (the current file is backed up
    first), but for the hooks (and Claude Code's mod) of another state folder in it (the
    `adapter`'s other_states): an orch init with another ORCH_HOME added those and the last
    install took them out, and once that folder is deleted they fail on every event. Returns
    (the backup folder used, those other state folders), or (None, []) when there is none."""
    check_settings_file(path)
    folder = latest_backup(state, path)
    if folder is None:
        return None, []
    with open(os.path.join(folder, BACKUP_NAME), "rb") as fh:
        data = fh.read()
    data, others = _without_others(data, adapter, state.hook_script)
    backup(state, path, "restore")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_atomic(path, data)
    return folder, others


def _without_others(data, adapter, script):
    """A backup's bytes without the entries of another state folder, and those folders: the bytes
    as they are when it holds none (or is not JSON)."""
    try:
        parsed = json.loads(data.decode("utf-8-sig") or "{}")
    except ValueError:
        return data, []
    others = adapter.other_states(parsed, script) if isinstance(parsed, dict) else []
    if not others:
        return data, []
    return _dump(adapter.without_others(parsed, script)), others
