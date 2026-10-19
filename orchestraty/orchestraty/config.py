"""The user's settings, in ``<state>/config.json``.

The ``orch`` commands write it; the coordinator only reads it, again whenever the file
changes. A missing or unreadable file means the defaults.

* ``repos``: the repositories being watched, each as the absolute path of its git common
  folder (shared by all its worktrees). Sessions elsewhere are read from the spool and dropped.
* ``idle_seconds``: the coordinator exits after this long with no live session and no new event.
* ``session_timeout``: a session with no event for this long counts as ended.
* ``level0_seconds``: how often every live session is refreshed from git and the worktrees
  without a hooked session are looked at.
"""
import json
import os

DEFAULTS = {"repos": [], "idle_seconds": 600, "session_timeout": 1800, "level0_seconds": 5}


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def load(state):
    """The settings, with defaults for anything missing or of the wrong type."""
    cfg = dict(DEFAULTS)
    try:
        with open(state.config, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return cfg
    if isinstance(data, dict):
        for key, default in DEFAULTS.items():
            value = data.get(key)
            if isinstance(value, type(default)) and not isinstance(value, bool):
                cfg[key] = value
        cfg["repos"] = [r for r in cfg["repos"] if isinstance(r, str) and os.path.isabs(r)]
    return cfg


def save(state, cfg):
    os.makedirs(state.root, exist_ok=True)
    tmp = state.config + ".orch-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, state.config)


def is_watched(cfg, common_dir):
    return any(_same(r, common_dir) for r in cfg["repos"])


def watch(state, common_dir):
    """Add a repository; returns False when it was already watched."""
    cfg = load(state)
    if is_watched(cfg, common_dir):
        return False
    cfg["repos"] = cfg["repos"] + [os.path.normpath(common_dir)]
    save(state, cfg)
    return True


def unwatch(state, common_dir):
    """Remove a repository; returns False when it was not watched."""
    cfg = load(state)
    kept = [r for r in cfg["repos"] if not _same(r, common_dir)]
    if len(kept) == len(cfg["repos"]):
        return False
    cfg["repos"] = kept
    save(state, cfg)
    return True
