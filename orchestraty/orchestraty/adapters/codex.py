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
for its user.
"""
import json

from orchestraty.adapters import claude

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
CANNOT = "no wake: no Codex hook can wake a session that waits for its user"
READ_ONLY_TOOLS = ()


def output(kind, text, steer=False):
    """The bytes for a slot of this kind: Claude Code's forms, but Stop's."""
    if kind == "stop":
        return json.dumps({"decision": "block", "reason": text}).encode("utf-8")
    return claude.output(kind, text, steer)
