"""Antigravity: its hook events, what the hook script reads from them, and how a note is handed
back at each. Built; not yet tested with the real agent.

Events. SessionStart, PreInvocation (before each call of its model, in every turn), PreToolUse,
PostToolUse, PostInvocation (after each call of its model) and Stop. SessionStart, PreToolUse,
PostToolUse and Stop are the common ones; PreInvocation is one of its own, kept by that name:
the session is working. There is no SessionEnd: a session ends when no event has come for the
session timeout. Captured: SessionStart, PreInvocation, PostToolUse (every tool, since a shell
command can change files too) and Stop. Not PreToolUse: it can take no note (below), and
Antigravity waits for that hook's answer before the tool runs. Not PostInvocation: nothing
needs it.

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
changed that file, and none comes about a file it is not changing. No wake, either, and no
ask: a hard claim only informs it, the same way.

Config writer. Antigravity reads ``hooks.json`` from the user's ``~/.gemini/config`` (every
workspace) and from a workspace's ``.agents`` folder. The file maps hook names to their events:
``{<name>: {"enabled": true, <event>: [...]}}``, a tool event's items being matcher groups
(``{"matcher": <regular expression>, "hooks": [<handler>]}``) and the others' handlers
(``{"type": "command", "command": ..., "timeout": <seconds>}``); it runs each ``command``
through a shell (``shell`` writes the line). Ours is the hook named ``orchestraty``; any other
handler that runs our hook script is taken out, so installing twice changes nothing, and other
hooks are kept. The user's file is the default; a workspace's is a file inside a repository,
which ``orch init`` shows and asks about first, offers to keep out of git, and never commits.
"""
import json
import os

from orchestraty.adapters import shell

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
CANNOT = {"edit": "its PreToolUse hook can only allow or deny the tool, so a note about a file comes with its "
                  "next step once it has changed that file",
          "wake": "no Antigravity hook can wake a session that waits for its user",      # {kind: why not}
          "ask": "no Antigravity hook can make an edit wait for the user's answer, so a hard claim only informs "
                 "it, with its next step once it has changed what is claimed"}
ASKS = False
# Tools that change no file: no need to look at the worktree again after them.
READ_ONLY_TOOLS = ("view_file", "list_dir", "find_by_name", "grep_search", "read_url_content", "search_web")
HOOK_NAME = "orchestraty"
MATCHERS = {"PostToolUse": ".*"}            # a regular expression: every tool
TIMEOUT_SECONDS = 10


def output(kind, text, steer=False):
    """The bytes for a slot of this kind: next and stop (it takes no other)."""
    if kind == "next":
        return json.dumps({"injectSteps": [{"ephemeralMessage": text}]}).encode("utf-8")
    if kind == "stop":
        return json.dumps({"decision": "continue", "reason": text}).encode("utf-8")
    return text.encode("utf-8")


# The config writer
def config_path(folder=None, project=None):
    """The hooks.json to change: a workspace's (.agents/hooks.json), the one in the folder
    given, or the user's (~/.gemini/config/hooks.json)."""
    if project:
        return os.path.join(os.path.abspath(project), ".agents", "hooks.json")
    folder = folder or os.path.join(os.path.expanduser("~"), ".gemini", "config")
    return os.path.join(os.path.abspath(folder), "hooks.json")


def handler(python, script, event):
    return {"type": "command", "command": shell.command_line([python, "-S", "-I", script, NAME, event]),
            "timeout": TIMEOUT_SECONDS}


def hook_block(python, script):
    """Our hook, as hooks.json holds it, for people who add it by hand."""
    block = {"enabled": True}
    for event in CAPTURED:
        h = handler(python, script, event)
        block[event] = [{"matcher": MATCHERS[event], "hooks": [h]}] if event in MATCHERS else [h]
    return {HOOK_NAME: block}


def _runs(item, script):
    """Whether a handler, or a matcher group's handlers, run our hook script."""
    if not isinstance(item, dict):
        return False
    hooks = item.get("hooks") if isinstance(item.get("hooks"), list) else []
    return shell.mentions(item.get("command"), script) or any(
        isinstance(h, dict) and shell.mentions(h.get("command"), script) for h in hooks)


def _commands(hook):
    """Every command line of a named hook's handlers."""
    found = []
    for items in hook.values() if isinstance(hook, dict) else ():
        for item in items if isinstance(items, list) else ():
            if isinstance(item, dict):
                found.append(item.get("command"))
                found += [h.get("command") for h in item.get("hooks") or [] if isinstance(h, dict)]
    return [c for c in found if isinstance(c, str)]


def other_states(config, script):
    """The state folders, other than the one `script` is in, whose hook script Orchestraty's
    named hook runs: an orch init with another ORCH_HOME wrote it. orch init replaces it whatever
    the folder; a backup taken before can still hold it. Each once, in the order found."""
    found = []
    for command in _commands(config.get(HOOK_NAME)):
        other = shell.shaped_script(command, NAME)
        if other and not shell.mentions(command, script):
            root = shell.long_path(os.path.dirname(os.path.dirname(other)))
            if root not in found:
                found.append(root)
    return found


def without_orchestraty(config, script):
    """The config with nothing of Orchestraty's (see without_ours)."""
    return without_ours(config, script)


def without_others(config, script):
    """The config without Orchestraty's named hook when it runs another state folder's script."""
    if other_states(config, script):
        return {name: hook for name, hook in config.items() if name != HOOK_NAME}
    return config


def without_ours(config, script):
    """The config without our hook, and without any other handler that runs our hook script
    (other hooks kept; one left with no event is dropped)."""
    config = {name: hook for name, hook in config.items() if name != HOOK_NAME}
    for name, hook in list(config.items()):
        if not isinstance(hook, dict):
            continue
        events = {e: items for e, items in hook.items() if isinstance(items, list)}
        cleaned = {e: [i for i in items if not _runs(i, script)] for e, items in events.items()}
        if cleaned == events:
            continue
        hook = dict(hook)
        for e, items in cleaned.items():
            if items:
                hook[e] = items
            else:
                del hook[e]
        if any(isinstance(v, list) for v in hook.values()):
            config[name] = hook
        else:
            del config[name]                # it ran nothing but our script
    return config


def merged(config, python, script):
    """The config with our hook in it (an earlier one of ours replaced)."""
    config = without_ours(config, script)
    config.update(hook_block(python, script))
    return config
