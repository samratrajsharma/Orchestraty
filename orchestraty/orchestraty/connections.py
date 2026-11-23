"""Readiness of the three local harnesses, independent of whether a project is watched."""
from datetime import datetime, timezone
import json
import os
import sqlite3

from orchestraty import config, plugin
from orchestraty.adapters import antigravity, claude, codex
from orchestraty.capture import install
from orchestraty.coordinator.record import Record

AGENTS = (claude.NAME, codex.NAME, antigravity.NAME)
LABELS = {"not_configured": "Not set up", "waiting": "Waiting for first event", "event_received": "Connected",
          "event_issue": "Event needs attention", "config_error": "Configuration needs attention"}
ISSUES = {
    "hook_error": "A hook encountered an error. Run orch doctor and check the agent's hook output.",
    "invalid_event": "The hook event lacked a usable session ID. Check the agent's hook configuration and version.",
    "no_workspace": "The event did not identify a usable local project. Open a checkout or project folder in the agent.",
    "excluded": "The session's project is excluded. Open Projects to watch it again.",
    "not watched": "The session's project is outside the watch list. Open Projects to watch it.",
    "git_error": "Git could not inspect the checkout. Run git status in the agent's environment and check the coordinator log.",
    "untrusted": "Git refused the checkout based on ownership. Check git status and the coordinator log.",
    "snapshot_error": "The folder could not be snapshotted. Check the coordinator log or open a smaller workspace.",
}
WAITING = {
    "claude": "Start a fresh Claude Code session in a project and send a prompt.",
    "codex": "Review the hooks in Codex, then start a fresh session in a project and send a prompt.",
    "antigravity": "Check that the Orchestraty hook is enabled, then start a fresh Antigravity conversation in a project.",
}


def targets(cfg):
    """The values used by readiness and Doctor to find the same user and project configs."""
    return [(adapter, config.installed_paths(cfg, adapter.NAME, path), command) for adapter, path, command in (
        (claude, cfg["settings"] or claude.settings_path(), "orch init --agent claude"),
        (codex, codex.config_path(), "orch init --agent codex"),
        (antigravity, antigravity.config_path(), "orch init --agent antigravity"),
    )]


def _event(record, agent):
    if record is not None:
        try:
            value = json.loads(record.meta_get("connection/" + agent) or "null")
            if isinstance(value, dict) and isinstance(value.get("ts"), int) and 0 < value["ts"] < 8 * 10**18:
                return value
        except (sqlite3.Error, ValueError):
            pass
    return {"ts": 0, "issue": None}


def record_event(record, agent, ts, issue):
    """Keep one receipt per supported harness. No project path, session ID, or payload is stored here."""
    if agent in AGENTS and 0 < ts < 8 * 10**18 and ts >= _event(record, agent)["ts"]:
        record.meta_set("connection/" + agent, json.dumps({"ts": ts, "issue": issue}))


def _configured(state, adapter, paths):
    active, errors = [], []
    for path in paths:
        try:
            current = install.load_settings(path)
        except install.Refused:
            errors.append(path)
            continue
        if adapter == claude and current.get("disableAllHooks") is True:
            errors.append(path)
            continue
        if adapter == antigravity and isinstance(current.get(adapter.HOOK_NAME), dict):
            if current[adapter.HOOK_NAME].get("enabled") is False:
                errors.append(path)
                continue
        ours = adapter.without_ours(current, state.hook_script) != current
        if adapter == claude and not ours and plugin.read_switch(state):
            project = os.path.dirname(os.path.dirname(path)) if os.path.basename(path) == "settings.local.json" else None
            ours = bool(plugin.installed(current, claude_config=os.path.dirname(path) if not project else None,
                                         project=project))
        if ours:
            active.append(path)
    return active, errors


def _summary(state, cfg, record):
    out = []
    for adapter, paths, command in targets(cfg):
        active, errors = _configured(state, adapter, paths)
        last = _event(record, adapter.NAME)
        updates = cfg["install_updates"].get(adapter.NAME)
        timestamps = (updates.get(os.path.normcase(path)) for path in active) if isinstance(updates, dict) else ()
        installed_at = max((stamp for stamp in timestamps if isinstance(stamp, int)), default=0)
        seen = last["ts"] > 0 and last["ts"] >= installed_at
        at = datetime.fromtimestamp(last["ts"] // 10**9, timezone.utc).isoformat() if seen else None
        if active and not os.path.isfile(state.hook_script):
            status, message = "config_error", "The installed hook script is missing. Run setup again."
        elif errors and not active:
            status, message = "config_error", "The hook configuration is unreadable or disabled. Check the file and run setup again."
        elif active and seen and last.get("issue") in ISSUES:
            status, message = "event_issue", ISSUES[last["issue"]]
        elif active and seen:
            status, message = "event_received", "Orchestraty processed an event and assigned it to a watched project."
        elif active:
            status, message = "waiting", WAITING[adapter.NAME]
        else:
            status, message = "not_configured", "Run setup to connect this agent."
        out.append({"agent": adapter.NAME, "name": adapter.TITLE, "status": status, "message": message,
                    "paths": active or errors or paths, "command": command, "last_event": at})
    return out


def summary(state, cfg=None):
    cfg = config.load(state) if cfg is None else cfg
    record = None
    try:
        if os.path.isfile(state.record):
            record = Record(state.record, readonly=True)
        return _summary(state, cfg, record)
    finally:
        if record is not None:
            record.close()
