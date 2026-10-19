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
    def spool(self):
        return self._in("spool")

    @property
    def backups(self):
        return self._in("backups")

    @property
    def logs(self):
        return self._in("logs")
