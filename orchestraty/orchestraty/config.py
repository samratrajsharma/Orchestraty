"""The user's settings, in ``<state>/config.json``.

The ``orch`` commands write it; the coordinator only reads it, again whenever the file
changes. A missing or unreadable file means the defaults.

* ``repos``: the repositories being watched, each as the absolute path of its git common
  folder (shared by all its worktrees). Sessions elsewhere are read from the spool and dropped,
  unless ``watch_all`` is on.
* ``watch_all``: every project an agent works in is watched: a git repository, or a folder
  with no git (a folder project, followed through Orchestraty's own snapshots). ``orch init``
  asks for it. Off for a config written before it existed, so nobody's tracking widens unasked.
* ``exclude``: projects not watched even with ``watch_all`` on (``orch unwatch`` adds one):
  a repository's git common folder, or a folder project's folder.
* ``idle_seconds``: the coordinator exits after this long with no live session and no new event.
* ``session_timeout``: a session with no event for this long counts as ended.
* ``level0_seconds``: how often every live session is refreshed from git and the worktrees
  without a hooked session are looked at.
* ``steer``: a strong note stops the edit it rides on (the agent re-plans) instead of only
  informing it. Off by default.
* ``wake``: a strong note for a session that is waiting for its user wakes it. Off by default;
  needs the wake hook, which ``orch config wake on`` installs.
* ``settings``: the Claude Code settings file ``orch init`` installed the hooks in.
* ``installed``: every agent config file ``orch init`` wrote, ``{"agent": ..., "path": ...}``,
  so that ``orch uninstall`` finds them all, wherever they are.
* ``install_updates``: when init last changed each agent's hook definition, for connection checks.
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
* ``typescript``: TypeScript contracts, per repository (keyed like ``remote``): ``{"on": false,
  "timeout": 300}``; see ``livemap.typescript``. ``orch ts`` changes it.
"""
import json
import os
import time

DEFAULTS = {"repos": [], "idle_seconds": 600, "session_timeout": 1800, "level0_seconds": 5,
            "steer": False, "wake": False, "settings": "", "acted_minutes": 30,
            "declare_minutes": 120, "remote": {}, "remote_fetch_minutes": 5, "claim_minutes": 120, "together": {},
            "typescript": {}, "installed": [], "install_updates": {}, "watch_all": False, "exclude": []}


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
        cfg["exclude"] = [r for r in cfg["exclude"] if isinstance(r, str) and os.path.isabs(r)]
    return cfg


def save(state, cfg):
    os.makedirs(state.root, exist_ok=True)
    tmp = state.config + ".orch-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, state.config)


def installed_paths(cfg, agent, default):
    """Config targets recorded by init, with the normal user path as a fallback."""
    paths = [default]
    for entry in cfg["installed"]:
        if isinstance(entry, dict) and entry.get("agent") == agent:
            path = entry.get("path")
            if isinstance(path, str) and os.path.isabs(path) and not any(_same(path, p) for p in paths):
                paths.append(path)
    return paths


def remember_install(state, agent, path, changed=False):
    """Record the target, and require a fresh hook event when the definition changed."""
    cfg = load(state)
    entry = {"agent": agent, "path": os.path.abspath(path)}
    installed = list(cfg["installed"])
    for i, prior in enumerate(installed):
        if (isinstance(prior, dict) and prior.get("agent") == agent and isinstance(prior.get("path"), str)
                and _same(prior["path"], path)):
            if not changed:
                return
            installed[i] = entry
            break
    else:
        installed.append(entry)
    cfg["installed"] = installed
    updates = dict(cfg["install_updates"])
    prior_updates = updates.get(agent)
    latest = dict(prior_updates) if isinstance(prior_updates, dict) else {}
    latest[os.path.normcase(entry["path"])] = time.time_ns()
    updates[agent] = latest
    cfg["install_updates"] = updates
    save(state, cfg)


def is_excluded(cfg, project):
    return any(_same(r, project) for r in cfg["exclude"])


def is_watched(cfg, common_dir):
    """Whether a project is watched: listed in ``repos``, or ``watch_all`` is on and it is not
    excluded. `common_dir` is a repository's git common folder, or a folder project's folder."""
    if any(_same(r, common_dir) for r in cfg["repos"]):
        return True
    return bool(cfg["watch_all"]) and not is_excluded(cfg, common_dir)


def watched(cfg, known=()):
    """The projects to show: the listed ones first, then each of `known` (the projects the
    record holds) that is watched, once each."""
    out = list(cfg["repos"])
    for project in known:
        if isinstance(project, str) and not any(_same(p, project) for p in out) and is_watched(cfg, project):
            out.append(project)
    return out


def watch(state, common_dir, former_folder=None):
    """Watch a project and remove the folder-only entry if its real Git checkout is now known."""
    cfg = load(state)
    listed = any(_same(r, common_dir) for r in cfg["repos"])
    old = former_folder if former_folder and not _same(former_folder, common_dir) else None
    repos = [r for r in cfg["repos"] if not old or not _same(r, old)]
    excluded = [r for r in cfg["exclude"] if not _same(r, common_dir) and (not old or not _same(r, old))]
    if listed and repos == cfg["repos"] and excluded == cfg["exclude"]:
        return False
    cfg["repos"] = repos if listed else repos + [os.path.normpath(common_dir)]
    cfg["exclude"] = excluded
    save(state, cfg)
    return True


def unwatch(state, common_dir):
    """Stop watching a project: out of ``repos``, and with ``watch_all`` on, into ``exclude``.
    Returns False when it was not watched."""
    cfg = load(state)
    if not is_watched(cfg, common_dir):
        return False
    cfg["repos"] = [r for r in cfg["repos"] if not _same(r, common_dir)]
    if cfg["watch_all"] and not is_excluded(cfg, common_dir):
        cfg["exclude"] = cfg["exclude"] + [os.path.normpath(common_dir)]
    save(state, cfg)
    return True


def set_watch_all(state, on):
    """Turn watching every project on or off; returns True when it changed."""
    cfg = load(state)
    if cfg["watch_all"] == bool(on):
        return False
    cfg["watch_all"] = bool(on)
    save(state, cfg)
    return True
