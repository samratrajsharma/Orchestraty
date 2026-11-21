"""Where Orchestraty keeps its state.

One folder per user and environment, never inside a repository or a synced folder:

* Windows: ``%USERPROFILE%\\.orchestraty``. Not under AppData: the Claude desktop app is a
  packaged app, and a packaged app's writes there go to its own private copy, which a
  terminal never sees, so the folder would split in two.
* Elsewhere: ``$XDG_STATE_HOME/orchestraty``, by default ``~/.local/state/orchestraty``.

``ORCH_HOME`` overrides both. Windows and each WSL distribution have their own folder.
"""
import os

ENV_VAR = "ORCH_HOME"
WINDOWS_FOLDER = ".orchestraty"
POSIX_FOLDER = "orchestraty"


def state_root(environ=None, windows=None):
    """The state folder for this user and environment (absolute, not created)."""
    environ = os.environ if environ is None else environ
    windows = os.name == "nt" if windows is None else windows
    override = environ.get(ENV_VAR, "")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if windows:
        home = environ.get("USERPROFILE") or os.path.expanduser("~")
        return os.path.join(home, WINDOWS_FOLDER)
    base = environ.get("XDG_STATE_HOME", "")
    if not os.path.isabs(base):          # the XDG rule: a relative value is ignored
        base = os.path.join(environ.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, POSIX_FOLDER)


class State:
    """The parts of one state folder."""

    def __init__(self, root=None):
        self.root = os.path.abspath(root) if root else state_root()

    def _in(self, *parts):
        return os.path.join(self.root, *parts)

    @property
    def hooks(self):
        return self._in("hooks")

    @property
    def hook_script(self):
        return self._in("hooks", "hook.py")

    @property
    def launch(self):
        """How the hook script starts the coordinator: the interpreter, then the import path."""
        return self._in("hooks", "launch.txt")

    @property
    def spool(self):
        return self._in("spool")

    @property
    def record(self):
        return self._in("record.sqlite")

    @property
    def config(self):
        return self._in("config.json")

    @property
    def lock(self):
        return self._in("coordinator.lock")

    @property
    def alive(self):
        """Touched by the coordinator every few seconds; a stale one means it is not running."""
        return self._in("coordinator.alive")

    @property
    def spawned(self):
        """Touched by a hook just before it starts the coordinator, so hooks do not start a
        crowd of them."""
        return self._in("coordinator.spawned")

    @property
    def info(self):
        return self._in("coordinator.json")

    @property
    def stop(self):
        return self._in("coordinator.stop")

    @property
    def rescan(self):
        """Made by orch ui's Refresh: the coordinator looks at every worktree again at once."""
        return self._in("coordinator.rescan")

    @property
    def unwatched(self):
        """Kept by the coordinator: the repositories, not watched, that agent sessions work in
        now, and how many (no session id, no event), so orch status and orch ui can say so."""
        return self._in("unwatched.json")

    @property
    def mod(self):
        """Orchestraty's Claude Code mod, copied here by orch init."""
        return self._in("mod")

    @property
    def status(self):
        """The status the coordinator keeps current for the mod, as orch status --json gives it."""
        return self._in("status.json")

    @property
    def numbers(self):
        """The short numbers orch findings gave in the last 24 hours, for orch label."""
        return self._in("finding-numbers.json")

    @property
    def backups(self):
        return self._in("backups")

    @property
    def logs(self):
        return self._in("logs")
