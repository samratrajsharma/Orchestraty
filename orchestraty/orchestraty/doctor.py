"""orch doctor: checks what Orchestraty needs, and says how to fix each problem it finds.

Each check gives one line: ``ok``, ``fix`` (a problem, with what to do about it on the next
line) or ``note`` (worth knowing, not a problem). The checks, in order:

* the Python running orch: 3.10 or later;
* the state folder: that files can be made and deleted in it, and that it is not in a folder a
  sync service copies to other machines (OneDrive, Dropbox, Google Drive, iCloud Drive), where
  the record (SQLite) and the spools would be copied half-written;
* the record: that SQLite opens it and reads every page of it (a damaged one is to be moved
  aside), and that it is not read-only;
* the hook script in the state folder: there, and the one this version installs;
* each agent's hooks (Claude Code, Codex, Antigravity), in the file orch init writes them to:
  the entries this installation writes, or none (a note); no agent with them is a problem, and so
  are the hooks of another state folder in the same file. For
  Claude Code, which setup runs them: that file's entries, or Orchestraty's Claude Code plugin
  (``orchestraty/plugin``), never both, and what the plugin needs (its switch's interpreter, the
  hooks this version writes, Git Bash on Windows);
* the coordinator: that it starts with what a hook starts it with, the interpreter and import
  path in ``hooks/launch.txt`` (one that is running already counts, unless it runs another
  version: one that started before an update); one that doctor starts is stopped again, and one
  that does not start leaves the hooks free to start the next at once;
* git: on the PATH, and 2.38 or later, which the together check needs (``merge-tree
  --write-tree``);
* inside a repository with the push line, only when there is a problem: its folder is gone
  while ``core.hooksPath`` still names it, its pre-push runs a hook script that is not there (a
  state folder moved or deleted), or a hook added to the earlier hooks folder is not run yet
  (``orch init`` again in that repository fixes each);
* the engine: that it imports;
* inside a repository, the time its first index takes: the engine reading every file at HEAD
  with no cache, as the coordinator does before the first note about that repository can come.

The exit code is 0 when nothing needs fixing, else 1.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import namedtuple

from orchestraty import __version__, config, plugin
from orchestraty.adapters import claude
from orchestraty.capture import hook
from orchestraty.capture import install as hooks
from orchestraty.coordinator.lock import Lock, touch

NEWER_RECORD = 4                  # the coordinator's exit code for a record a newer Orchestraty wrote
FAILED = 5                        # ... for any other start that failed
BAD_RECORD = 6                    # ... and for a record SQLite cannot open or read (not a database, damaged, a folder)
NEW_RECORD = "the next hook starts a new record within a minute, or run orch doctor again to start it now"
_PROBE = re.compile(r"\.orch-doctor-\d+-[0-9a-f]{8}")       # the files cannot_write makes

Check = namedtuple("Check", "kind text fix")
GIT_NEEDED = (2, 38)
START_SECONDS = 30.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_SYNC_NAMES = (("onedrive", "OneDrive"), ("dropbox", "Dropbox"), ("google drive", "Google Drive"),
               ("googledrive", "Google Drive"), ("my drive", "Google Drive"), ("icloud drive", "iCloud Drive"),
               ("iclouddrive", "iCloud Drive"), ("mobile documents", "iCloud Drive"))


def ok(text):
    return Check("ok", text, "")


def problem(text, fix):
    return Check("fix", text, fix)


def note(text):
    return Check("note", text, "")


# ------------------------------------------------------------------------------ Python and the state folder
def python_check(version=None, executable=None):
    version = tuple(version or sys.version_info[:3])
    text = "Python %d.%d.%d (%s)" % ((version + (0, 0, 0))[:3] + (executable or sys.executable,))
    if version[:2] < (3, 10):
        return problem(text + ": Orchestraty needs 3.10 or later",
                       "install Python 3.10 or later, and Orchestraty with it (pipx install --python <it> orchestraty)")
    return ok(text)


def _service(name):
    for prefix, service in _SYNC_NAMES:
        if name == prefix or name.startswith((prefix + " ", prefix + "-")):
            return service
    return ""


def synced_folder(path, environ=None):
    """The sync service whose folder `path` is in ("" for none): a folder that OneDrive's
    variables name, or one with a sync service's name (as Dropbox, "OneDrive - Company", or
    macOS's CloudStorage and Mobile Documents)."""
    environ = os.environ if environ is None else environ
    here = os.path.normcase(os.path.abspath(path))
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = environ.get(var)
        if root and os.path.isabs(root):
            root = os.path.normcase(os.path.abspath(root))
            if here == root or here.startswith(root.rstrip(os.sep) + os.sep):
                return "OneDrive"
    parts = [p.lower() for p in re.split(r"[\\/]+", os.path.abspath(path)) if p]
    for i, part in enumerate(parts):
        if _service(part):
            return _service(part)
        if part == "cloudstorage" and i + 1 < len(parts):        # macOS: ~/Library/CloudStorage/<service>
            return _service(parts[i + 1]) or "a cloud storage service"
    return ""


def state_check(state, environ=None):
    service = synced_folder(state.root, environ)
    if service:
        return problem(f"state folder: {state.root} is in a folder {service} syncs, which would copy the record "
                       "and the spools to other machines half-written",
                       "set ORCH_HOME to a folder outside it (only on this machine), then run orch init again: it "
                       "takes the hooks of this folder out")
    if not os.path.isdir(state.root):
        return problem(f"state folder: {state.root} does not exist yet", "run orch init")
    failed = cannot_write(state.root)
    if failed and failed[0] == "write":
        return problem(f"state folder: {state.root} cannot be written to ({type(failed[1]).__name__})",
                       "give your user write access to it, or set ORCH_HOME to a folder it can write")
    if failed:
        return problem(f"state folder: {state.root} can be written to, but files cannot be deleted in it "
                       f"({type(failed[1]).__name__})",
                       "give your user the right to delete files in it (the coordinator replaces and removes files "
                       "there), or set ORCH_HOME to a folder where it can")
    return ok(f"state folder: {state.root}")


def cannot_write(folder):
    """How making a file in `folder` and deleting it again fails: ("write", error) or ("delete",
    error), or None when both work. One try, with a name of its own: tempfile tries again on
    Windows after a PermissionError for as long as os.access says the folder is writable, which
    it says whatever the folder's access list denies, so it would try 2**31 names. The probe
    files of earlier runs that could not delete them are deleted first, where that works now."""
    _remove_old_probes(folder)
    probe = os.path.join(folder, f".orch-doctor-{os.getpid()}-{os.urandom(4).hex()}")
    try:
        os.close(os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError as exc:
        return "write", exc
    try:
        os.remove(probe)
    except FileNotFoundError:                   # another doctor run took it away just now
        pass
    except OSError as exc:
        return "delete", exc
    return None


def _remove_old_probes(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if _PROBE.fullmatch(name):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def record_check(state):
    """The record: that SQLite opens it and reads every page of it (PRAGMA quick_check), and
    that its files are not read-only (the coordinator opens such a record, then fails every
    step). Read only, and a record that is not there yet is not made."""
    if not os.path.exists(state.record):
        return note(f"record: none yet at {state.record} (the coordinator makes it)")
    if os.path.isdir(state.record):
        return problem(f"record: {state.record} is a folder, not a file", f"move it aside: {NEW_RECORD}")
    # Before opening it: SQLite makes the -wal and -shm files with the record's own permissions,
    # so opening a read-only record would leave read-only ones behind (POSIX).
    read_only = [path for path in (state.record, state.record + "-wal", state.record + "-shm")
                 if os.path.exists(path) and not os.access(path, os.W_OK)]
    if read_only:
        return problem(f"record: {' and '.join(read_only)} cannot be written to (read-only)",
                       "give your user write access to it (on Windows, clear its read-only attribute), then run "
                       "orch coordinator stop and orch doctor again")
    from orchestraty.coordinator.record import Record
    try:
        record = Record(state.record, readonly=True)
        try:
            found = [row[0] for row in record.db.execute("PRAGMA quick_check")]
        finally:
            record.close()
    except sqlite3.DatabaseError as exc:
        found = [str(exc)]
        if type(exc) is not sqlite3.DatabaseError:              # not a damaged file
            if "locked" in str(exc):
                return problem(f"record: {state.record} cannot be read now ({exc})",
                               "run orch doctor again once the coordinator is idle")
            return problem(f"record: {state.record} cannot be opened ({exc})",
                           "give your user read and write access to it, then run orch doctor again; or move it "
                           "aside, with its -wal and -shm files, to start a new record")
    if found != ["ok"]:
        return problem(f"record: {state.record} cannot be read ({found[0][:200]})",
                       f"run orch coordinator stop, then move {state.record} aside, with its -wal and -shm files: "
                       f"{NEW_RECORD}")
    return ok(f"record: {state.record}")


def hook_script_check(state):
    try:
        with open(state.hook_script, "rb") as fh:
            installed = fh.read()
    except OSError:
        return problem(f"hook script: none at {state.hook_script}", "run orch init")
    with open(hooks.HOOK_SOURCE, "rb") as fh:
        if fh.read() != installed:
            return problem(f"hook script: {state.hook_script} is another version's", "run orch init again")
    return ok(f"hook script: {state.hook_script}")


# ------------------------------------------------------------------------------ the agents' hooks
def _agents(state, cfg):
    """(title, the file orch init writes its hooks to, how to install them, its adapter, {python: wanted
    config})."""
    from orchestraty.adapters import antigravity, claude, codex
    python = hooks.hook_python()
    script = state.hook_script

    def claude_wanted(current):
        wanted = dict(current)
        wanted["hooks"] = claude.merged_settings(current, python, script, cfg["wake"])["hooks"]
        return wanted
    return [
        (claude.TITLE, cfg["settings"] or claude.settings_path(), "orch init", claude, claude_wanted),
        (codex.TITLE, codex.config_path(), "orch init --agent codex", codex,
         lambda current: codex.merged(current, python, script)),
        (antigravity.TITLE, antigravity.config_path(), "orch init --agent antigravity", antigravity,
         lambda current: antigravity.merged(current, python, script)),
    ]


def _canonical(value):
    """A config with the order of every list taken out: another tool's entries added after ours
    change nothing about ours."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted((_canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def _settings_line(title, path, how, current, wanted):
    try:
        same = _canonical(wanted(current)) == _canonical(current)
    except ValueError:
        same = False
    if same:
        return ok(f"{title}: hooks in {path}")
    return problem(f"{title}: the hooks in {path} are not the ones this installation writes "
                   "(another interpreter, or events missing)", f"run {how} again")


def agent_checks(state, cfg):
    """One line for each agent (for Claude Code, which setup runs its hooks), and a problem when
    none has Orchestraty's hooks."""
    out, installed = [], 0
    for title, path, how, adapter, wanted in _agents(state, cfg):
        without = adapter.without_ours
        try:
            current = hooks.load_settings(path)
        except hooks.Refused as exc:
            out.append(problem(f"{title}: {exc}", f"fix the file, then run {how} again"))
            continue
        others = adapter.other_states(current, state.hook_script)
        if others:
            out.append(problem(f"{title}: {path} also runs the hooks of the state folder {', '.join(others)}, which an "
                               "orch init with another ORCH_HOME added, so every event is recorded there too",
                               f"run {how} again: it takes them out"))
            current = adapter.without_others(current, state.hook_script)
        ours = without(current, state.hook_script) != current
        if title == claude.TITLE:
            lines, on = claude_checks(state, path, current, ours,
                                      lambda: _settings_line(title, path, how, current, wanted))
            out += lines
            installed += on
        elif not ours:
            out.append(note(f"{title}: no Orchestraty hooks in {path} ({how} installs them)"))
        else:
            out.append(_settings_line(title, path, how, current, wanted))
            installed += 1
    if not installed:
        out.append(problem("no agent has Orchestraty's hooks, so no session is seen",
                           "run orch init (Claude Code), or orch init --agent codex|antigravity"))
    return out


def _claude_target(path):
    """(Claude Code config folder, project folder) of the settings file orch init wrote."""
    if os.path.basename(path) == "settings.local.json":
        return None, os.path.dirname(os.path.dirname(path))
    return os.path.dirname(path), None


def claude_checks(state, path, current, ours, settings_line, environ=None):
    """Which setup runs Claude Code's hooks, the settings file's entries (``settings_line()``
    checks them) or Orchestraty's plugin (``orchestraty/plugin``), and what stands in its way.
    Returns (lines, 1 when hooks are installed by either, else 0)."""
    claude_config, project = _claude_target(path)
    found = plugin.installed(current, claude_config=claude_config, project=project, environ=environ)
    switch = plugin.read_switch(state)
    shadow = claude.mod_in(current, state.mod)
    if switch is None:                               # the settings setup, or none
        if not ours:
            if found:
                return [problem(f"Claude Code: the plugin {found[0]} is installed, but its hooks are off",
                                "run orch init")], 0
            return [note(f"Claude Code: no Orchestraty hooks in {path} (orch init installs them)")], 0
        lines = [settings_line()]
        if found:
            unused = (f", and the mod orch init loads from {state.mod} has its name, so Claude Code loads that in its "
                      "place, without its MCP server and skill" if shadow else "")
            lines.append(note(f"Claude Code: the plugin {found[0]} is installed too; its hooks stay off while the "
                              f"hooks are in {path}{unused}. orch init moves them to the plugin"))
        return lines, 1
    if ours:
        return [problem(f"Claude Code: hooks both in {path} and from the plugin, so each event would be recorded twice",
                        "run orch init again")], 1
    if not found:
        return [problem(f"Claude Code: {plugin.switch_path(state)} turns on the plugin's hooks, but no Orchestraty "
                        f"plugin is installed for {path}, so no hooks run",
                        f"install it ({plugin.INSTALL} in Claude Code), or run orch init to add the hooks to "
                        f"{path}")], 0
    pid, record = found
    version = f" {record['version']}" if isinstance(record.get("version"), str) else ""
    lines = [ok(f"Claude Code: hooks from the plugin {pid}{version} (turned on in {plugin.switch_path(state)}"
                + (", wake on)" if switch[1] else ")"))]
    if not os.path.isabs(switch[0]) or not os.path.isfile(switch[0]):
        lines.append(problem(f"Claude Code: the plugin's hooks would run {switch[0]}, which is not there",
                             "run orch init again"))
    copy = plugin.installed_hooks(record)
    if copy is not None and copy.get("hooks") != plugin.hooks_file([])["hooks"]:
        lines.append(problem(f"Claude Code: the copy of {pid} installed has another version's hooks",
                             f"update it: claude plugin update {pid}"))
    if shadow:
        lines.append(problem(f"Claude Code: the mod orch init loads from {state.mod} has the plugin's name, so "
                             f"Claude Code loads it in place of {pid}, whose hooks then do not run",
                             "run orch init again"))
    windows = os.name == "nt" if environ is None else environ.get("OS") == "Windows_NT"
    if windows and not plugin.git_bash(environ):
        lines.append(problem("Claude Code: it runs a plugin's hooks with Git Bash, and none was found",
                             "install Git for Windows, or set CLAUDE_CODE_GIT_BASH_PATH to its bash.exe"))
    return lines, 1


# ------------------------------------------------------------------------------ the coordinator
def _launch(state):
    try:
        with open(state.launch, "r", encoding="utf-8") as fh:
            return fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def _stop(state, seconds):
    lock = Lock(state.lock)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if lock.acquire():
            lock.release()
            return True
        try:
            touch(state.stop)
        except OSError:              # Windows: the coordinator is removing it; the next round asks again
            pass
        time.sleep(0.1)
    return False


def _running_version(state):
    """The version the running coordinator wrote to its info file, or None."""
    try:
        with open(state.info, "r", encoding="utf-8") as fh:
            version = json.load(fh).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def coordinator_check(state, seconds=START_SECONDS):
    if hook.coordinator_alive(state.root, time.time()):
        running = _running_version(state)
        if running and running != __version__:
            return problem(f"coordinator: running version {running}, not this one ({__version__}): it started "
                           "before the update", "run orch coordinator stop; the next hook starts this version")
        return ok("coordinator: running")
    lines = _launch(state)
    if not lines or not lines[0]:
        return problem(f"coordinator: no {state.launch}, so no hook can start it", "run orch init")
    python = lines[0]
    if not os.path.isabs(python) or not os.path.isfile(python):
        return problem(f"coordinator: the hooks would start it with {python}, which is not there",
                       "run orch init again")
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("GIT_", "PYTHON")) or k.upper() == "PYTHONDONTWRITEBYTECODE"}
    if len(lines) > 1 and lines[1]:
        env["PYTHONPATH"] = lines[1]
    env["ORCH_HOME"] = state.root
    kwargs = {"creationflags": NO_WINDOW | NEW_GROUP} if os.name == "nt" else {"start_new_session": True}
    started = time.monotonic()
    proc = subprocess.Popen([python] + list(hook.COORDINATOR_ARGS), cwd=state.root, env=env,   # as a hook starts it
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    while time.monotonic() - started < seconds and proc.poll() is None:
        if hook.coordinator_alive(state.root, time.time()) and os.path.exists(state.info):
            took = time.monotonic() - started
            if not _stop(state, seconds):
                return ok(f"coordinator: starts ({took:.1f} s); it stops by itself when idle")
            _reap(proc, seconds)
            # It touches its heartbeat and writes its info file before it opens the record.
            if proc.returncode not in (BAD_RECORD, NEWER_RECORD, FAILED):
                return ok(f"coordinator: starts ({took:.1f} s), and was stopped again")
            break
        time.sleep(0.1)
    if proc.poll() is None:
        _stop(state, seconds)
        _reap(proc, seconds)
    if proc.returncode in (BAD_RECORD, NEWER_RECORD, FAILED):
        _end_hold_off(state)
    shown = f"exited with {proc.returncode}" if proc.returncode is not None else f"did not start within {seconds:.0f} s"
    if proc.returncode == BAD_RECORD:
        return problem(f"coordinator: the record {state.record} cannot be read, so it does not start",
                       f"move {state.record} aside, with its -wal and -shm files: {NEW_RECORD}")
    if proc.returncode == NEWER_RECORD:
        return problem("coordinator: the record was written by a newer Orchestraty, which this one cannot read",
                       f"upgrade Orchestraty, or move {state.record} aside to start a new record")
    return problem(f"coordinator: {shown}",
                   f"see {os.path.join(state.logs, 'coordinator.log')}, then run orch init again")


def _reap(proc, seconds):
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _end_hold_off(state):
    """A coordinator that does not start keeps the hooks from starting another for a minute
    (coordinator.spawned set that far ahead). Doctor's own start is no reason for that: the
    user is about to act on what doctor says, and the next hook should then start one."""
    now = time.time()
    try:
        if os.stat(state.spawned).st_mtime > now:
            os.utime(state.spawned, (now - 2 * hook.RESPAWN_SECONDS, now - 2 * hook.RESPAWN_SECONDS))
    except OSError:
        pass


# ------------------------------------------------------------------------------ git and the engine
def git_version(text):
    """(major, minor, patch) from git --version's output, or None."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(g or 0) for g in m.groups()) if m else None


def git_check(output=None):
    if output is None:
        try:
            output = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=30,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout
        except (OSError, subprocess.TimeoutExpired):
            output = ""
    version = git_version(output)
    if version is None:
        return problem("git: not found on the PATH", "install git 2.38 or later")
    shown = ".".join(str(n) for n in version)
    if version[:2] < GIT_NEEDED:
        return problem(f"git {shown}: the together check needs 2.38 or later (merge-tree --write-tree)",
                       "update git")
    return ok(f"git {shown}")


def git_hooks_check(cwd):
    """A problem when the repository holding `cwd` has the push line and its chain folder is gone
    (``githooks.lost``), its pre-push runs a hook script that is not there
    (``githooks.missing_script``: a state folder moved or deleted since), or a hook added since
    is not run yet (``githooks.unchained``); else None."""
    from orchestraty import gitio
    from orchestraty.capture import githooks
    again = "run orch init again in this repository"
    try:
        if not gitio.worktree_info(cwd):
            return None
        if githooks.lost(cwd):
            chain = githooks.chain_folder(cwd)
            what = f"has lost its {githooks.ORIGINAL_FILE}" if os.path.isdir(chain) else "is gone"
            return problem(f"git hooks: core.hooksPath names the push line's folder {chain}, which {what}, so "
                           "this repository's own hooks may not run", again)
        script = githooks.missing_script(cwd)
        missing = githooks.unchained(cwd)
    except (gitio.GitError, gitio.UntrustedRepositoryError):
        return None
    if script:
        return problem(f"git hooks: the push line runs {script}, which is not there (its state folder was moved or "
                       "deleted), so each push prints an error", again)
    if not missing:
        return None
    return problem(f"git hooks: {', '.join(missing)} added since the push line was, so git does not run "
                   f"{'it' if len(missing) == 1 else 'them'}", again)


def engine_check():
    try:
        import orch_engine
        from orch_engine import models
    except Exception as exc:                  # noqa: BLE001 - whatever breaks the import is the answer
        return problem(f"engine: does not import ({type(exc).__name__}: {exc})",
                       "reinstall Orchestraty: pipx reinstall orchestraty (or uv tool install --reinstall orchestraty)")
    return ok(f"engine: imports (schema {models.ENGINE_SCHEMA}, {os.path.dirname(orch_engine.__file__)})")


def index_check(cwd, say=None):
    """The time the engine takes to index this repository's HEAD from nothing."""
    from orchestraty import gitio
    info = gitio.worktree_info(cwd)
    if not info:
        return note("first index: not in a git repository; run orch doctor in one to time its first index")
    if not info["head"]:
        return note(f"first index: {info['top']} has no commit yet")
    from orch_engine.config import Config
    from orch_engine.pipeline import build_index_at
    if say:
        say(f"      timing the first index of {info['top']} (--no-index skips this) ...")
    with tempfile.TemporaryDirectory(prefix="orch-doctor-") as data:
        started = time.monotonic()
        try:
            idx = build_index_at(info["top"], "HEAD", Config(data_dir=data, use_cache=False), use_cache=False)
        except Exception as exc:              # noqa: BLE001 - an untrusted folder, a broken repository, ...
            return problem(f"first index of {info['top']}: failed ({type(exc).__name__}: {str(exc)[:200]})",
                           "run git status there; orch status says more once a session works in it")
        seconds = time.monotonic() - started
    line = f"first index of {info['top']}: {len(idx.parsed_files)} files in {seconds:.1f} s"
    capped = next((e["reason"] for e in idx.not_analysed if e.get("reason", "").startswith("file cap")), "")
    if capped:                                # the engine stops at a number of files, in path order
        cap = re.search(r"\((\d+)\)", capped)
        last = max((pf.file for pf in idx.parsed_files), default="")
        return note(f"{line}. The engine reads at most {cap.group(1) if cap else 'so many'} files of a commit, and "
                    f"this one has more: files after {last} in path order are not analysed, so changes and uses "
                    "there give no notes")
    return ok(line)


# ------------------------------------------------------------------------------ all of it
def checks(state, cwd, index=True, say=None):
    """Every check, in order (a generator: each is shown as soon as it is done)."""
    cfg = config.load(state)
    yield python_check()
    yield state_check(state)
    yield record_check(state)
    yield hook_script_check(state)
    yield from agent_checks(state, cfg)
    yield coordinator_check(state)
    yield git_check()
    hooks_line = git_hooks_check(cwd)
    if hooks_line:
        yield hooks_line
    yield engine_check()
    if index:
        yield index_check(cwd, say)


def run(state, cwd, out=None, index=True):
    out = out or sys.stdout

    def say(line):
        print(line, file=out, flush=True)
    problems = 0
    for check in checks(state, cwd, index, say):
        say(f"{check.kind:<5} {check.text}")
        if check.kind == "fix":
            problems += 1
            say(f"      -> {check.fix}")
    say("No problems found." if not problems else f"{problems} problem{'s' if problems > 1 else ''} to fix.")
    return 1 if problems else 0
