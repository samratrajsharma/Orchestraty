"""Antigravity: its hook events, what the hook script reads from them, and how a note is handed
back at each. Built; not yet tested with the real agent.

Events. SessionStart, PreInvocation (before each call of its model, in every turn), PreToolUse,
PostToolUse, PostInvocation (after each call of its model) and Stop. SessionStart, PreToolUse,
PostToolUse and Stop are the common ones; PreInvocation is one of its own, kept by that name:
the session is working. There is no SessionEnd: a session ends when no event has come for the
session timeout. Captured: SessionStart, PreInvocation, PostToolUse (every tool, since a shell
command can change files too) and Stop. Not PreToolUse: it can take no note (below), and
Antigravity takes a PreToolUse hook that fails to answer as a refusal of the tool. Not
PostInvocation: nothing needs it.

What the hook script reads, all camelCase: ``conversationId`` is the session, the first of
``workspacePaths`` its folder, ``transcriptPath`` its transcript (never read: Antigravity
deletes old ones, so nothing may depend on it). A tool event has ``toolCall`` with ``name`` and
``args``. Its edit tools come in two sets, write_to_file, replace_file_content and
multi_replace_file_content, which name the file as ``TargetFile``, and write_file and edit_file,
as ``file_path`` (or ``path``); a value may be a ``file://`` URI, which is made a path.

Notes, in the outputs its hooks give: with its next step, PreInvocation's ``injectSteps`` with
the note as an ``ephemeralMessage`` (the model sees it on that call; it is not kept in the
transcript); at the end of a turn, Stop's ``decision`` continue with the note as ``reason``,
which keeps the agent going with the note as a system message. Antigravity waits for an answer
from its hooks: with no note the hook prints ``{}``, and at PreToolUse ``{"decision":"allow"}``.

What it cannot take: a note before an edit. Its PreToolUse hook can only allow or deny the
tool, not pass text to the model, so a note about a file comes with its next step once it has
changed that file, and none comes about a file it is not changing. No wake, either.
"""
import json

NAME = "antigravity"
TITLE = "Antigravity"
EVENTS = {"SessionStart": "SessionStart", "PreInvocation": "PreInvocation", "PreToolUse": "PreToolUse",
          "PostToolUse": "PostToolUse", "PostInvocation": "PostInvocation", "Stop": "Stop"}
CAPTURED = ("SessionStart", "PreInvocation", "PostToolUse", "Stop")

# What the hook script reads, in the spool line's names: {line field: payload field}.
READS = {"session_id": "conversationId", "cwd": "workspacePaths[0]", "transcript_path": "transcriptPath",
         "tool_name": "toolCall.name", "file_path": "toolCall.args (the edit tool's file field)"}
EDIT_TOOLS = {"write_to_file": ("TargetFile",), "replace_file_content": ("TargetFile",),
              "multi_replace_file_content": ("TargetFile",), "write_file": ("file_path", "path"),
              "edit_file": ("file_path", "path")}
SLOTS = {"PreInvocation": "next", "Stop": "stop"}
HEARS = ("next", "stop")
QUIET = ("{}", {"PreToolUse": '{"decision":"allow"}'})      # (by default, {event: answer})
CANNOT = ("no note before an edit: its PreToolUse hook can only allow or deny the tool, so a note about a "
          "file comes with its next step once it has changed that file; no wake")
# Tools that change no file: no need to look at the worktree again after them.
READ_ONLY_TOOLS = ("view_file", "list_dir", "find_by_name", "grep_search", "read_url_content", "search_web")


def output(kind, text, steer=False):
    """The bytes for a slot of this kind: next and stop (it takes no other)."""
    if kind == "next":
        return json.dumps({"injectSteps": [{"ephemeralMessage": text}]}).encode("utf-8")
    if kind == "stop":
        return json.dumps({"decision": "continue", "reason": text}).encode("utf-8")
    return text.encode("utf-8")
