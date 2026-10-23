"""Claude Code: its hook events, what the hook script reads from them, how a note is handed
back at each, and its config writer.

Events. Claude Code's event names are the common ones, the names the rest of Orchestraty uses.
Captured: SessionStart, SessionEnd, UserPromptSubmit, PreToolUse (for the edit tools only),
PostToolUse and PostToolUseFailure (for every tool, since a shell command can change files
too), Stop, SubagentStop and StopFailure.

What the hook script reads (``capture/hook.py``, which imports nothing of ours and keeps its own
copy; a test checks the two agree): ``session_id``, ``hook_event_name``, ``cwd``,
``transcript_path``; for tool events ``tool_name``, ``tool_use_id`` and the edited file
(``tool_input.file_path``; NotebookEdit's ``notebook_path``); ``source`` for SessionStart,
``reason`` for SessionEnd, ``stop_hook_active`` for Stop and SubagentStop.

Notes, each in the output its event takes: before an edit (PreToolUse, as
``additionalContext``, or, with steer on, as the reason of a deny), with the next tool result
(PostToolUse), at the end of a turn (Stop), and, with wake on, when the session waits for its
user (an ``asyncRewake`` Stop hook that exits 2 with the note). Before an edit of what another
session claimed hard, PreToolUse's permission decision ``ask``: the edit waits for the user's
answer, the note the reason shown to them and context for Claude. Never ``allow``, which would
skip the user's own permission prompt.

Config writer. The hook entries go into a Claude Code settings file. By default that is the
user's own settings (``~/.claude/settings.json``, or the folder ``CLAUDE_CONFIG_DIR`` names), so
the hooks run in every worktree, including ones made after installing; a project's
``.claude/settings.local.json`` covers only that folder. Each entry is in exec form (no shell):
``command`` is an absolute Python and ``args`` is ``["-S", "-I", <hook script>, "claude",
<event>]``. Other settings and other hooks are kept; our entries (those that run our hook
script) are replaced, so installing twice changes nothing. The file work (refusing a link or a
file that is not JSON, a backup before every change) is ``capture/install``'s.
"""
import json
import os
import shutil
import stat

NAME = "claude"
TITLE = "Claude Code"
MOD_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mod")
PLUGIN_DIRS = "CLAUDE_CODE_PLUGIN_DIRS"       # plugin folders Claude Code loads, as --plugin-dir does
EDIT_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"
# The Claude Code events captured. PreToolUse runs for the edit tools only; PostToolUse for
# every tool, since a shell command can change files too.
EVENTS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
          "PostToolUseFailure", "Stop", "SubagentStop", "StopFailure")
CAPTURED = EVENTS
MATCHERS = {"PreToolUse": EDIT_MATCHER, "PostToolUse": "*", "PostToolUseFailure": "*"}
TIMEOUT_SECONDS = 10
WAKE_TIMEOUT_SECONDS = 3600     # the wake waiter leaves on its own a little before this
AGENT = "claude"

# What the hook script reads, in the spool line's names: {line field: payload field}.
READS = {"session_id": "session_id", "hook_event_name": "hook_event_name", "cwd": "cwd",
         "transcript_path": "transcript_path", "tool_name": "tool_name", "tool_use_id": "tool_use_id",
         "source": "source", "reason": "reason", "stop_hook_active": "stop_hook_active"}
EDIT_TOOLS = {"Edit": ("file_path",), "Write": ("file_path",), "MultiEdit": ("file_path",),
              "NotebookEdit": ("notebook_path",)}
# The note slot each event may take ({event: kind}), and what the hook prints when it has none.
SLOTS = {"PreToolUse": "edit", "PostToolUse": "next", "PostToolUseFailure": "next", "Stop": "stop"}
HEARS = ("edit", "next", "stop", "wake")      # wake: from the opt-in asyncRewake Stop hook
QUIET = None                                  # with no note, it prints nothing
CANNOT = {}                                   # every kind of note reaches it
ASKS = True                                   # an edit can wait for the user's answer (a hard claim)


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# The output each hook prints, as bytes ready to write into a slot.
def pre_tool_output(text, steer=False):
    if steer:
        out = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": text}
    else:
        out = {"hookEventName": "PreToolUse", "additionalContext": text}
    return json.dumps({"hookSpecificOutput": out}).encode("utf-8")


def ask_output(text):
    """Before an edit: ask the user, with the note as the reason they see and as context for
    Claude. Never "allow": that would skip the user's own permission prompt."""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                              "permissionDecisionReason": text, "additionalContext": text}}
                      ).encode("utf-8")


def post_tool_output(text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}).encode("utf-8")


def stop_output(text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": text}}).encode("utf-8")


def output(kind, text, steer=False):
    """The bytes for a slot of this kind (edit, next, stop; wake is the plain text)."""
    return (pre_tool_output(text, steer) if kind == "edit" else
            post_tool_output(text) if kind == "next" else
            stop_output(text) if kind == "stop" else text.encode("utf-8"))


# The config writer
def settings_path(claude_config=None, project=None, environ=None):
    """The Claude Code settings file to change: a project folder's settings.local.json, the
    settings.json in a given Claude Code config folder, or the user's own settings."""
    if project:
        return os.path.join(os.path.abspath(project), ".claude", "settings.local.json")
    environ = os.environ if environ is None else environ
    folder = claude_config or environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(os.path.abspath(folder), "settings.json")


def hook_entry(python, script, event):
    hook = {"type": "command", "command": python, "args": ["-S", "-I", script, AGENT, event],
            "timeout": TIMEOUT_SECONDS}
    entry = {"hooks": [hook]}
    if event in MATCHERS:
        entry["matcher"] = MATCHERS[event]
    return entry


def wake_entry(python, script):
    """The opt-in Stop entry that waits in the background for a note to wake the session with."""
    return {"hooks": [{"type": "command", "command": python, "args": ["-S", "-I", script, AGENT, "Wake"],
                       "asyncRewake": True, "timeout": WAKE_TIMEOUT_SECONDS}]}


def hook_block(python, script, wake=False):
    """The ``hooks`` object with only our entries, for people who add it by hand."""
    block = {event: [hook_entry(python, script, event)] for event in EVENTS}
    if wake:
        block["Stop"].append(wake_entry(python, script))
    return block


def _ours(entry, script):
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        args = hook.get("args") if isinstance(hook, dict) else None
        if isinstance(args, list) and any(isinstance(a, str) and _same(a, script) for a in args):
            return True
    return False


def without_ours(settings, script):
    """The settings with every entry that runs our hook script removed (other entries kept;
    an event left with no entries, and an empty hooks object, are dropped)."""
    settings = dict(settings)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    kept = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            kept[event] = entries
            continue
        rest = [e for e in entries if not _ours(e, script)]
        if rest or not entries:
            kept[event] = rest
    if kept:
        settings["hooks"] = kept
    else:
        del settings["hooks"]
    return settings


def _plugin_dirs(env):
    sep = ";" if os.name == "nt" else ":"
    return sep, [p for p in str(env.get(PLUGIN_DIRS, "")).split(sep) if p]


def with_mod(settings, mod_dir):
    """The settings with our mod's folder in env.CLAUDE_CODE_PLUGIN_DIRS (others kept)."""
    settings = dict(settings)
    env = dict(settings.get("env") or {})
    sep, dirs = _plugin_dirs(env)
    if not any(_same(d, mod_dir) for d in dirs):
        dirs.append(mod_dir)
    env[PLUGIN_DIRS] = sep.join(dirs)
    settings["env"] = env
    return settings


def without_mod(settings, mod_dir):
    """The settings with our mod's folder taken out of env.CLAUDE_CODE_PLUGIN_DIRS."""
    if not isinstance(settings.get("env"), dict) or PLUGIN_DIRS not in settings["env"]:
        return settings
    settings = dict(settings)
    env = dict(settings["env"])
    sep, dirs = _plugin_dirs(env)
    kept = [d for d in dirs if not _same(d, mod_dir)]
    if kept:
        env[PLUGIN_DIRS] = sep.join(kept)
    else:
        del env[PLUGIN_DIRS]
    if env:
        settings["env"] = env
    else:
        del settings["env"]
    return settings


def mod_in(settings, mod_dir):
    env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    return any(_same(d, mod_dir) for d in _plugin_dirs(env)[1])


def install_mod(state):
    """Copy the mod into the state folder (replacing an older copy); returns its folder."""
    from orchestraty import gitio
    target = state.mod
    if os.path.isdir(target):
        gitio.remove_tree(target)          # an older copy may carry a read-only attribute (Windows)
    shutil.copytree(MOD_SOURCE, target, ignore=shutil.ignore_patterns("__pycache__", "*.test.ts"))
    for root, dirs, files in os.walk(target):  # the copy stays writable, so the next copy can replace it
        for name in dirs + files:
            p = os.path.join(root, name)
            os.chmod(p, os.stat(p).st_mode | stat.S_IWRITE)
    return target


def merged_settings(settings, python, script, wake=False):
    """The settings with our entries added after any others (earlier ones of ours replaced)."""
    settings = without_ours(settings, script)
    hooks = dict(settings.get("hooks") or {})
    for event in EVENTS:
        entries = hooks.get(event)
        hooks[event] = (list(entries) if isinstance(entries, list) else []) + [hook_entry(python, script, event)]
    if wake:
        hooks["Stop"].append(wake_entry(python, script))
    settings["hooks"] = hooks
    return settings
