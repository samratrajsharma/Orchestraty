"""The user's settings, in ``<state>/config.json``.

The ``orch`` commands write it; the coordinator only reads it, again whenever the file
changes. A missing or unreadable file means the defaults.

* ``repos``: the repositories being watched, each as the absolute path of its git common
  folder (shared by all its worktrees). Sessions elsewhere are read from the spool and dropped.
* ``idle_seconds``: the coordinator exits after this long with no live session and no new event.
* ``session_timeout``: a session with no event for this long counts as ended.
* ``level0_seconds``: how often every live session is refreshed from git and the worktrees
  without a hooked session are looked at.
* ``steer``: a strong note stops the edit it rides on (the agent re-plans) instead of only
  informing it. Off by default.
* ``wake``: a strong note for a session that is waiting for its user wakes it. Off by default;
  needs the wake hook, which ``orch config wake on`` installs.
* ``settings``: the Claude Code settings file ``orch init`` installed the hooks in.
* ``acted_minutes``: how long after its delivery a note can count as acted on in ``orch stats``
  (a proxy: the session edited a file the note cites, or its finding stopped holding).
* ``declare_minutes``: how long what an agent declares it is about to change (the MCP tool
  ``declare``) holds.
* ``remote``: cloud agents' branches, per repository (keyed by its git common folder, as the
  record keys it): ``{"on": false, "remote": "origin", "patterns": [...]}``; see ``remote``.
  ``orch remote`` changes it.
* ``remote_fetch_minutes``: how often the coordinator fetches the cloud branches of a
  repository that has them on (a failure doubles the wait, up to an hour).
* ``claim_minutes``: how long a claim holds when ``orch claim --for`` (or the MCP tool's
  ``minutes``) does not say; see ``claims``.
* ``together``: the together check, per repository (keyed like ``remote``): ``{"on": false,
  "command": "", "timeout": 120}``; see ``together``. ``orch together`` changes it.
"""
import json
import os

DEFAULTS = {"repos": [], "idle_seconds": 600, "session_timeout": 1800, "level0_seconds": 5,
            "steer": False, "wake": False, "settings": "", "acted_minutes": 30,
            "declare_minutes": 120, "remote": {}, "remote_fetch_minutes": 5, "claim_minutes": 120, "together": {}}


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
            if isinstance(value, type(default)) and (isinstance(default, bool) or not isinstance(value, bool)):
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
