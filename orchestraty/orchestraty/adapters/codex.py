"""Codex: its hook events, what the hook script reads from them, and how a note is handed back
at each. Built; not yet tested with the real agent.

Events. Codex's event names are Claude Code's, the common ones: SessionStart, SessionEnd,
UserPromptSubmit, PreToolUse, PostToolUse, Stop and SubagentStop (there is no
PostToolUseFailure). All seven are captured; PreToolUse for the edit tool only.

What the hook script reads: Claude Code's names (``session_id``, ``hook_event_name``, ``cwd``,
``transcript_path``, ``tool_name``, ``tool_use_id``, ``source``, ``reason``,
``stop_hook_active``). Its one edit tool, apply_patch, names no file in a field: its input is
``{"command": <the patch>}``, and the files are on the patch's header lines (``*** Update File:
<path>``, and Add File, Delete File, Move to), relative to the session's ``cwd``. Shell commands
come as the tool ``Bash``.

One shared process. Since Codex 0.157, with ``daemon_auto_start`` on (the default), the hooks
of every Codex session on a machine run inside one shared background process (``codex
app-server``), with the environment, and the folder, of whichever session started it. So
nothing here takes a session from environment variables or from the hook process's own folder:
each event's session and folder are its payload's ``session_id`` and ``cwd``, which every event
Codex sends has (its hook input schema requires them). Were they ever missing, the way out is
to run Codex without the shared process: ``daemon_auto_start = false`` under ``[features]`` in
its config.toml, or ``codex --no-daemon``.

Notes, in the outputs Codex reads (its hooks' output schema): before an edit, PreToolUse's
``hookSpecificOutput.additionalContext`` (with steer on, ``permissionDecision`` deny and the note
as ``permissionDecisionReason``, which stops the tool and tells the model why); with the next
tool result, PostToolUse's ``hookSpecificOutput.additionalContext``; at the end of a turn,
Stop's ``decision`` block with the note as ``reason``, which keeps the turn going with the note
as its next prompt (``stop_hook_active`` then tells the next Stop not to take another). Those
are Claude Code's forms, except Stop's. No wake: no Codex hook can wake a session that waits
for its user. No ask: Codex takes only deny as a PreToolUse permission decision (``ask`` makes
the hook count as failed, and the tool runs), so a hard claim only informs a Codex session.

Config writer. Codex reads ``hooks.json`` in its home folder (``CODEX_HOME``, by default
``~/.codex``): ``{"hooks": {<event>: [{"matcher": ..., "hooks": [{"type": "command", "command":
..., "timeout": <seconds>}]}]}}``. It runs each ``command`` through a shell, with the payload on
stdin (``shell`` writes the line), and gives a SessionEnd hook at most 3 seconds. Our entries
(those whose command runs our hook script) are replaced, others kept, so installing twice
changes nothing. Codex runs a hook only after the person has reviewed and trusted it with
``/hooks``, and records that trust in its config.toml: nothing here writes the trust, or
config.toml at all.
"""
import json
import os

from orchestraty.adapters import claude, shell

NAME = "codex"
TITLE = "Codex"
EVENTS = {e: e for e in ("SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
                         "SubagentStop")}
CAPTURED = tuple(EVENTS)

# What the hook script reads: Claude Code's names. The edit tool's files are on the patch's lines.
READS = dict(claude.READS, file_path="tool_input.command: the patch's file lines")
PATCH_TOOLS = ("apply_patch",)
PATCH_HEADERS = ("Update File: ", "Add File: ", "Delete File: ", "Move to: ")
EDIT_TOOLS = {"apply_patch": ()}             # no file field: the files are in the patch
SLOTS = {"PreToolUse": "edit", "PostToolUse": "next", "Stop": "stop"}
HEARS = ("edit", "next", "stop")
QUIET = None                                 # with no note, the hook prints nothing
CANNOT = {"wake": "no Codex hook can wake a session that waits for its user",       # {kind: why not}
          "ask": "a Codex hook cannot make an edit wait for the user's answer, so a hard claim only informs it"}
ASKS = False
READ_ONLY_TOOLS = ()
MATCHERS = {"PreToolUse": "apply_patch", "PostToolUse": "*"}
TIMEOUT_SECONDS = 10
TIMEOUTS = {"SessionEnd": 3}                 # what Codex gives a SessionEnd hook at most


def output(kind, text, steer=False):
    """The bytes for a slot of this kind: Claude Code's forms, but Stop's."""
    if kind == "stop":
        return json.dumps({"decision": "block", "reason": text}).encode("utf-8")
    return claude.output(kind, text, steer)


# The config writer
def config_path(codex_home=None, environ=None):
    """The hooks.json to change: in the Codex home folder given, else CODEX_HOME, else ~/.codex."""
    environ = os.environ if environ is None else environ
    folder = codex_home or environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(os.path.abspath(folder), "hooks.json")


def hook_entry(python, script, event):
    hook = {"type": "command", "command": shell.command_line([python, "-S", "-I", script, NAME, event]),
            "timeout": TIMEOUTS.get(event, TIMEOUT_SECONDS)}
    entry = {"hooks": [hook]}
    if event in MATCHERS:
        entry["matcher"] = MATCHERS[event]
    return entry


def hook_block(python, script):
    """hooks.json with only our entries, for people who add them by hand."""
    return {"hooks": {event: [hook_entry(python, script, event)] for event in CAPTURED}}


def _ours(entry, script):
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    return isinstance(hooks, list) and any(isinstance(h, dict) and shell.mentions(h.get("command"), script)
                                           for h in hooks)


def without_ours(config, script):
    """The config with every entry that runs our hook script removed (other entries kept; an
    event left with no entries, and an empty hooks object, are dropped)."""
    config = dict(config)
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return config
    kept = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            kept[event] = entries
            continue
        rest = [e for e in entries if not _ours(e, script)]
        if rest or not entries:
            kept[event] = rest
    if kept:
        config["hooks"] = kept
    else:
        del config["hooks"]
    return config


def merged(config, python, script):
    """The config with our entries added after any others (earlier ones of ours replaced).
    Raises ValueError when its hooks are not an object (Codex would not read it either)."""
    if not isinstance(config.get("hooks", {}), dict):
        raise ValueError("its hooks are not a JSON object")
    config = without_ours(config, script)
    hooks = dict(config.get("hooks") or {})
    for event in CAPTURED:
        entries = hooks.get(event)
        hooks[event] = (list(entries) if isinstance(entries, list) else []) + [hook_entry(python, script, event)]
    config["hooks"] = hooks
    return config
