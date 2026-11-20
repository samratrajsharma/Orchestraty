"""Team sync: teammates' agents made aware of each other's work through git.

These are the building blocks the gates and the commands are made of: a small reader for the
YAML the repository's people and policy files are written in (``miniyaml``), the grammar every
field of a shared record must follow (``grammar``), the people file and CODEOWNERS (``people``,
``codeowners``), who this machine shares as (``identity``), what the repository's policy allows
(``policy``), the secret scanner (``scan``), the record file (``recordfile``), the gates out and
in (``seal``, ``admit``), the anonymous-read probe (``probe``) and the ``orch team`` commands.

No module here opens a socket: what reaches another machine goes through git, run as a
process of its own. None of this runs for a user who has not turned team sync on.

This module keeps each repository's team settings in config.json, under ``team`` and keyed by
the repository as the record keys it: whether it is on, the remote, the policy the person agreed
to (``consented``) and their own narrowing (``narrow``, ``never_branches``). ``<state>/team/enabled``
is there while any repository has team sync on: the pre-push hook looks only for that file.
"""
import os

from orchestraty import config
from orchestraty.coordinator.record import repo_key

DEFAULT_REMOTE = "origin"


def settings(cfg, common_dir):
    """{"on", "remote", "consented", "narrow", "never_branches"} for one repository, with the
    defaults: off, origin, nothing agreed to, no narrowing."""
    table = cfg.get("team") if isinstance(cfg.get("team"), dict) else {}
    entry = table.get(repo_key(common_dir))
    entry = entry if isinstance(entry, dict) else {}
    name = entry.get("remote")
    consented = entry.get("consented")
    return {"on": entry.get("on") is True, "remote": name if isinstance(name, str) and name else DEFAULT_REMOTE,
            "consented": consented if isinstance(consented, dict) else None,
            "narrow": entry.get("narrow"), "never_branches": entry.get("never_branches")}


def save_settings(state, common_dir, **changes):
    """Change one repository's team settings in config.json, and keep the enabled marker in step."""
    cfg = config.load(state)
    table = dict(cfg["team"])
    key = repo_key(common_dir)
    entry = dict(table[key]) if isinstance(table.get(key), dict) else {}
    entry.update(changes)
    table[key] = entry
    cfg["team"] = table
    config.save(state, cfg)
    sync_marker(state, cfg)
    return settings(cfg, common_dir)


def marker(state):
    return os.path.join(state.root, "team", "enabled")


def any_on(cfg):
    table = cfg.get("team") if isinstance(cfg.get("team"), dict) else {}
    return any(isinstance(e, dict) and e.get("on") is True for e in table.values())


def sync_marker(state, cfg):
    """Make <state>/team/enabled when a repository has team sync on, and remove it when none has."""
    path = marker(state)
    if any_on(cfg):
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb"):
                pass
    elif os.path.exists(path):
        os.remove(path)
