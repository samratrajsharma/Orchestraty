"""The two-call helper: hooks for agents people build themselves.

A homemade agent gets what an agent with hooks gets from two calls in its loop, one before it
writes a file and one after, and a third at the end of a turn:

    from orchestraty.helper import Orchestraty
    orch = Orchestraty(agent="mybot", cwd=worktree)   # a new session id unless one is given
    orch.start()                       # SessionStart
    note = orch.before_write(path)     # the note waiting for this file, or None (like PreToolUse)
    note = orch.after_write(path)      # records the edit; the "next" note, or None (like PostToolUse)
    note = orch.end_turn()             # callers left behind, or None (like Stop)
    orch.end()                         # SessionEnd

Each call goes the way an agent's hook goes: through the hook script ``orch init`` installs in
the state folder (``<state>/hooks/hook.py``), with the event's JSON in the shape Claude Code's
hook events have (``session_id``, ``cwd``, ``hook_event_name``, ``tool_name``,
``tool_input.file_path``). The script appends the spool line, takes the note waiting for that
event, and starts the coordinator when it is not running. The agent is ``helper-<name>``, kept
to what the hook keeps of an agent's name (letters, digits, ``-`` and ``_``), so it is safe as a
folder name and ``orch status`` shows it as given.

By default the hook's code runs in this process: the installed script is loaded as a module,
and its own functions build the line, take the slot and start the coordinator, as its main()
does, so a call starts no interpreter. With ``in_process=False`` each call starts the script as a
process, the way an agent runs a hook. A test checks that both write the same bytes and take
slots the same way.

A call returns the note's text, without the hook's JSON wrapper, or None. It never raises into
the agent's loop: on any error it returns None, the way the hook fails open.
"""
import json
import os
import subprocess
import sys
import time
import types
import uuid

from orchestraty.paths import State

PREFIX = "helper-"
MAX_AGENT = 32              # the hook keeps this much of an agent's name
MAX_SESSION = 64            # and this much of a session id, in file names
TIMEOUT = 10.0              # seconds a hook process may take
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_loaded = {}                # hook script path -> (its mtime, the module)


def _kept(text, limit):
    """Letters, digits, '-' and '_': what the hook keeps in agent names and session file names."""
    return "".join(c for c in str(text) if c.isascii() and (c.isalnum() or c in "-_"))[:limit]


def agent_name(name):
    """The agent as the spool and ``orch status`` name it: helper-<name>."""
    return (PREFIX + (_kept(name, MAX_AGENT) or "agent"))[:MAX_AGENT]


def note_text(output):
    """The note in a hook's output (bytes): the text inside its JSON wrapper, or None."""
    if not output or not output.strip():
        return None
    try:
        data = json.loads(output)
    except ValueError:
        return output.decode("utf-8", "replace").strip() or None
    if not isinstance(data, dict):
        return None
    out = data.get("hookSpecificOutput")
    for holder, key in ((out, "additionalContext"), (out, "permissionDecisionReason"), (data, "reason")):
        if isinstance(holder, dict) and isinstance(holder.get(key), str) and holder[key]:
            return holder[key]
    return None


def _load(script):
    """The installed hook script as a module (no bytecode is written beside it), loaded again
    when orch init has replaced it."""
    mtime = os.stat(script).st_mtime_ns
    cached = _loaded.get(script)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with open(script, "rb") as fh:
        source = fh.read()
    module = types.ModuleType("_orchestraty_hook")
    module.__file__ = script
    exec(compile(source, script, "exec"), module.__dict__)
    _loaded[script] = (mtime, module)
    return module


class Orchestraty:
    """One session of a homemade agent, working in `cwd` (a folder inside a worktree of a
    watched repository)."""

    def __init__(self, agent, cwd, session_id=None, in_process=True, state=None):
        self.agent = agent_name(agent)
        self.cwd = os.path.abspath(os.fspath(cwd))
        self.session_id = (_kept(session_id, MAX_SESSION) if session_id is not None else "") or str(uuid.uuid4())
        self.in_process = in_process
        self.state = state if isinstance(state, State) else State(state)

    def start(self):
        """SessionStart: the session begins."""
        self._call("SessionStart", {"source": "startup"})

    def before_write(self, path):
        """Before writing `path`: the note waiting for that file (a change another session made
        that this file uses), or None."""
        return self._call("PreToolUse", lambda: self._write(path))

    def after_write(self, path):
        """After writing `path`: records the edit; the note waiting for the session's next step
        (another session is changing the same function, or uses what this one changed), or None."""
        return self._call("PostToolUse", lambda: self._write(path))

    def end_turn(self):
        """At the end of a turn: the callers this session's changes left behind, or None."""
        return self._call("Stop", {"stop_hook_active": False})

    def end(self, reason="other"):
        """SessionEnd: the session is over; its waiting notes are taken back."""
        self._call("SessionEnd", {"reason": reason})

    def payload(self, event, fields):
        """The event's JSON, as an agent's hook gets it."""
        body = {"session_id": self.session_id, "cwd": self.cwd, "hook_event_name": event}
        body.update(fields)
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def _write(self, path):
        path = os.path.abspath(os.path.join(self.cwd, os.fspath(path)))
        return {"tool_name": "Write", "tool_input": {"file_path": path}}

    def _call(self, event, fields):
        try:
            raw = self.payload(event, fields() if callable(fields) else fields)
            return note_text(self.run_in_process(event, raw) if self.in_process else self.run_hook(event, raw))
        except Exception:
            return None

    # The two ways to reach the hook: both return what the hook prints (the note's slot), or b"".
    def run_hook(self, event, raw):
        """Start the installed hook script, as an agent does, with the interpreter orch init
        recorded (else this one)."""
        script = self.state.hook_script
        if not os.path.isfile(script):
            return b""
        proc = subprocess.run([self._python(), "-S", "-I", script, self.agent, event], input=raw,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=TIMEOUT,
                              creationflags=_NO_WINDOW)
        return proc.stdout

    def run_in_process(self, event, raw, now_ns=None, pid=None):
        """What the installed hook script's main() does for an agent event, run here with its
        own functions. `now_ns` and `pid` stand in for the clock and the process id (tests)."""
        script = self.state.hook_script
        if not os.path.isfile(script):
            return b""
        hook = _load(script)
        root = hook.state_folder(script)
        if root is None:
            return b""
        agent = hook.safe_name(self.agent.encode("utf-8", "replace"), MAX_AGENT, b"unknown")
        folder = os.path.join(root, "spool", agent.decode("ascii"))
        path = note = None
        try:
            event = hook.safe_name(event.encode("utf-8", "replace"), 40, b"")
            note, kind, first = hook.take_note(root, raw, event, hook.session_of(raw, agent), agent)
            name, line = hook.event_line(raw, agent, event, time.time_ns() if now_ns is None else now_ns,
                                         os.getpid() if pid is None else pid, first)
            path = os.path.join(folder, name)
            if note:
                line = line[:-2] + b',"took":"' + kind + b'"}\n'
            hook.append(path, line)
            hook.start_coordinator(root, time.time())
        except Exception as exc:                 # as the hook does: fail open, and leave a trace
            line = hook.error_line(agent, exc, time.time_ns(), os.getpid())
            for target in (path, os.path.join(folder, "shim_errors.jsonl")):
                if target is None:
                    continue
                try:
                    hook.append(target, line)
                    break
                except Exception:
                    continue
        return note or b""

    def _python(self):
        try:
            with open(self.state.launch, "r", encoding="utf-8") as fh:
                python = fh.readline().strip()
        except (OSError, UnicodeDecodeError):
            python = ""
        return python if os.path.isabs(python) and os.path.isfile(python) else sys.executable
