"""The hook script: one agent event in, one spool line out.

Agents run it on their hook events as

    python -S -I <state>/hooks/hook.py <agent> <event>

with the event's JSON on stdin. ``orch init`` copies it into the state folder's ``hooks``
folder, and the state folder is the one above it, so the hook takes no path argument; run
from anywhere else (a folder not named ``hooks``) it does nothing. It appends one line to
``<state>/spool/<agent>/<session_id>.jsonl``:

    {"ts": <ns>, "pid": <pid>, "agent": "...", "event": "...", "len": <payload bytes>,
     "session_id": "...", "hook_event_name": "...", "tool_name": "...", "tool_use_id": "...",
     "cwd": "...", "transcript_path": "...", "file_path": "...", "source": "...",
     "reason": "...", "stop_hook_active": true}

Only those fields are copied from the payload, found with bytes.find and checked to be valid
JSON values, never parsed; a missing or malformed field is left out. ``tool_name`` and
``tool_use_id`` are read for tool events only, ``file_path`` only for the tools that edit a
file (NotebookEdit's ``notebook_path`` is recorded as ``file_path``), ``source`` for
SessionStart, ``reason`` for SessionEnd and ``stop_hook_active`` for Stop and SubagentStop.
The payload itself (prompts, file contents, commands, tool output) is never stored; ``len`` is
its size.

Silent and fail-open: it prints nothing and always exits 0. An internal failure appends one
``{"shim_error": "<exception class>"}`` line instead, so failures show up in the data.

It also starts the coordinator when that is not running (its heartbeat file is stale), at
most once every few seconds across all hooks: detached, with no handle or stream of the hook's,
from the state folder (never the agent's folder, whose files must not end up on the
coordinator's import path), and with no GIT_ or PYTHON variable of the agent's environment.
``orch init`` writes the interpreter and the import path to use into ``hooks/launch.txt``.
Setting ``ORCH_NO_COORDINATOR`` turns the start-up off (tests and measurements).

Notes. The coordinator writes each note in advance into a slot file under
``<state>/notes/<session>/``; the hook only takes the slot its event calls for and prints it,
exactly as written (it builds no JSON):

* PreToolUse of an edit tool: ``edit/<key of the file>.json``, before the edit;
* PostToolUse: ``next.json``, with the tool's result;
* Stop: ``stop.json``, unless ``stop_hook_active`` says a stop hook already kept Claude going.

A slot is taken exactly once (``take_slot``) and the spool line records ``"took"``.

Wake (opt-in): run as ``hook.py <agent> Wake`` from an ``asyncRewake`` Stop hook, it waits
in the background for ``wake.txt``; when that appears it prints it to stderr and exits 2, which
wakes Claude with the text. It leaves quietly when the session's spool grows (the person is
back) or after an hour.

Take: run as ``hook.py <agent> Take wake <session id>`` by Orchestraty's Claude Code mod, it
takes that session's wake note the same way and prints it, so the mod can hand it over.

git: run as ``hook.py git <hook name>`` from the git hooks ``orch init`` chains in, it
records the event (with the worktree folder) and, for pre-push, prints the line in
``<state>/push/<key of the worktree>.txt`` if there is one. It never blocks a push.

Built-in modules only (sys, os, time, and _winapi on Windows), and no json and no site, so the
interpreter starts fast. The append is atomic with several writers: on Windows the file is
opened with FILE_APPEND_DATA only, so the kernel places every write at the end of the file
(O_APPEND through the C runtime lost events with five writers); on POSIX it is O_APPEND.
"""
import os
import sys
import time

FIELDS = (b"session_id", b"hook_event_name", b"tool_name", b"tool_use_id", b"cwd", b"transcript_path")
# Where the keys sit in real payloads (recorded from Claude Code 2.1.283): session_id,
# transcript_path, cwd, hook_event_name and tool_name come before tool_input and
# tool_response, so their first occurrence is the top-level one. tool_use_id comes after them,
# so a nested key of that name inside tool_input or tool_response would come first: it is
# taken from its last occurrence. file_path is the first key of that name: tool_input comes
# before tool_response, and an edit tool's input holds no nested objects with that key.
LAST = (b"tool_use_id",)
TOOL_FIELDS = (b"tool_name", b"tool_use_id")
TOOL_EVENTS = (b"PreToolUse", b"PostToolUse", b"PostToolUseFailure")
EDIT_TOOLS = {b'"Edit"': b"file_path", b'"Write"': b"file_path", b'"MultiEdit"': b"file_path",
              b'"NotebookEdit"': b"notebook_path"}
EVENT_FIELDS = {b"SessionStart": b"source", b"SessionEnd": b"reason"}
FLAG_EVENTS = (b"Stop", b"SubagentStop")
MAX_FIELD = 4096            # bytes; longer values are left out
KEY_TAIL = 24
_KEY_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
_UNESCAPE = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
WAKE_GRACE = 1.5            # seconds: the Stop line of the same turn is written meanwhile
WAKE_POLL = 0.25
WAKE_MAX = 3500.0           # leave before the hook's one-hour timeout
STALE_SECONDS = 10.0        # the coordinator touches its heartbeat every 2 seconds
RESPAWN_SECONDS = 5.0       # at most one start attempt in this time, by any hook
COORDINATOR_ARGS = ("-m", "orchestraty", "coordinator", "run")
_SPACE = b" \t\r\n"
_ESCAPES = b'"\\/bfnrtu'
_HEX = b"0123456789abcdefABCDEF"


def _string_token(raw, j):
    """The JSON string token starting at raw[j] (a quote), quotes included, or None if it is
    not a valid JSON string: unterminated, a bad escape, a raw control character or bad UTF-8."""
    k = j + 1
    end = min(len(raw), j + 2 + MAX_FIELD)
    while k < end:
        c = raw[k]
        if c == 34:                              # the closing quote
            token = raw[j:k + 1]
            try:
                token.decode("utf-8")
            except UnicodeDecodeError:
                return None
            return token
        if c == 92:                              # a backslash escape
            e = raw[k + 1:k + 2]
            if not e or e not in _ESCAPES:
                return None
            if e == b"u":
                digits = raw[k + 2:k + 6]
                if len(digits) != 4 or any(d not in _HEX for d in digits):
                    return None
                k += 6
            else:
                k += 2
            continue
        if c < 32:
            return None
        k += 1
    return None


def _value_at(raw, key, last):
    """The index just past `"key":` and its spaces for the first (or last) such key, or -1. A
    "key" not followed by a colon (for example the word used as a value) is skipped. Keys
    inside string values cannot match: their quotes are escaped there."""
    needle = b'"' + key + b'"'
    start, end = 0, len(raw)
    while True:
        i = raw.rfind(needle, 0, end) if last else raw.find(needle, start)
        if i < 0:
            return -1
        j = i + len(needle)
        while j < len(raw) and raw[j] in _SPACE:
            j += 1
        if j < len(raw) and raw[j] == 58:        # ':'
            j += 1
            while j < len(raw) and raw[j] in _SPACE:
                j += 1
            return j
        if last:
            end = i
        else:
            start = i + 1


def field(raw, key, last=False):
    """The raw JSON string value of the first (or, with last=True, the last) `"key": "..."` in
    raw, quotes included, or None when that key's value is not a valid string."""
    j = _value_at(raw, key, last)
    if j < 0 or j >= len(raw) or raw[j] != 34:
        return None
    return _string_token(raw, j)


def flag(raw, key):
    """True or False for the first `"key": true|false` in raw, or None."""
    j = _value_at(raw, key, False)
    if j < 0:
        return None
    for word, value in ((b"true", True), (b"false", False)):
        after = j + len(word)
        if raw[j:after] == word and (after == len(raw) or raw[after] in b" \t\r\n,}"):
            return value
    return None


def json_unescape(token):
    """The text of a JSON string token (bytes, quotes included), without the json module."""
    s = token[1:-1].decode("utf-8")
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        e = s[i + 1]
        if e == "u":
            code = int(s[i + 2:i + 6], 16)
            i += 6
            if 0xD800 <= code <= 0xDBFF and s[i:i + 2] == "\\u":       # a surrogate pair
                low = int(s[i + 2:i + 6], 16)
                if 0xDC00 <= low <= 0xDFFF:
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    i += 6
            out.append(chr(code))
        else:
            out.append(_UNESCAPE[e])
            i += 2
    return "".join(out)


def json_string(value):
    """A JSON string literal (bytes) for any text, without the json module."""
    out = [b'"']
    for ch in value:
        o = ord(ch)
        if ch == '"':
            out.append(b'\\"')
        elif ch == "\\":
            out.append(b"\\\\")
        elif o < 32 or 0xD800 <= o <= 0xDFFF:
            out.append(b"\\u%04x" % o)
        else:
            out.append(ch.encode("utf-8"))
    out.append(b'"')
    return b"".join(out)


def _fnv1a64(text):
    h = 0xCBF29CE484222325
    for byte in text.encode("utf-8", "surrogatepass"):
        h = ((h ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def file_key(path):
    """The slot key of an absolute path: the same as orchestraty.delivery.slots.file_key (a
    test compares them), written again here because the hook imports nothing of ours."""
    p = os.path.normpath(path).replace("\\", "/")
    if sys.platform in ("win32", "darwin"):
        p = p.lower()
    tail = "".join(c if c in _KEY_SAFE else ("__" if c == "_" else "_%x_" % ord(c)) for c in p[-KEY_TAIL:])
    return tail[-KEY_TAIL:] + "~" + _fnv1a64(p)


def slot_for(root, raw, event, session):
    """(slot path, kind) of the note this event may take, or (None, None)."""
    if session == b"nosession":
        return None, None
    folder = os.path.join(root, "notes", session.decode("ascii"))
    if event == b"PreToolUse":
        key = EDIT_TOOLS.get(field(raw, b"tool_name"))
        token = field(raw, key) if key else None
        if token is None:
            return None, None
        return os.path.join(folder, "edit", file_key(json_unescape(token)) + ".json"), b"edit"
    if event in (b"PostToolUse", b"PostToolUseFailure"):
        return os.path.join(folder, "next.json"), b"next"
    if event == b"Stop" and flag(raw, b"stop_hook_active") is not True:
        return os.path.join(folder, "stop.json"), b"stop"
    return None, None


def safe_name(value, limit, fallback):
    """Letters, digits, '-' and '_' only, so it is safe as a file name and inside JSON."""
    kept = bytes(c for c in value if 48 <= c <= 57 or 65 <= c <= 90 or 97 <= c <= 122 or c in b"-_")[:limit]
    return kept or fallback


def event_line(raw, agent, event, now_ns, pid):
    """(session file name, spool line) for one payload."""
    parts = [b'{"ts":%d,"pid":%d,"agent":"%s","event":"%s","len":%d' % (now_ns, pid, agent, event, len(raw))]
    session = b""
    tool = None
    not_a_tool_event = bool(event) and event not in TOOL_EVENTS
    for key in FIELDS:
        if not_a_tool_event and key in TOOL_FIELDS:
            continue
        token = field(raw, key, last=key in LAST)
        if token is not None:
            parts.append(b',"%s":%s' % (key, token))
            if key == b"session_id":
                session = token[1:-1]
            elif key == b"tool_name":
                tool = token
    path_key = EDIT_TOOLS.get(tool) if event in TOOL_EVENTS else None
    if path_key:
        token = field(raw, path_key)
        if token is not None:
            parts.append(b',"file_path":%s' % token)
    extra = EVENT_FIELDS.get(event)
    if extra:
        token = field(raw, extra)
        if token is not None:
            parts.append(b',"%s":%s' % (extra, token))
    if event in FLAG_EVENTS:
        value = flag(raw, b"stop_hook_active")
        if value is not None:
            parts.append(b',"stop_hook_active":%s' % (b"true" if value else b"false"))
    parts.append(b"}\n")
    return safe_name(session, 64, b"nosession").decode("ascii") + ".jsonl", b"".join(parts)


def error_line(agent, exc, now_ns, pid):
    name = safe_name(type(exc).__name__.encode("ascii", "replace"), 64, b"Exception")
    return b'{"ts":%d,"pid":%d,"agent":"%s","shim_error":"%s"}\n' % (now_ns, pid, agent, name)


def append(path, line):
    """Append one line atomically, creating the file (and its folder) when missing."""
    try:
        _append(path, line)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _append(path, line)


if os.name == "nt":
    import _winapi

    def _append(path, line):
        handle = _winapi.CreateFile(path, 0x0004,       # FILE_APPEND_DATA, and no FILE_WRITE_DATA
                                    0x7,                # share read, write and delete
                                    0, 4,               # no security attributes; OPEN_ALWAYS
                                    0x80, 0)            # FILE_ATTRIBUTE_NORMAL
        try:
            _winapi.WriteFile(handle, line)
        finally:
            _winapi.CloseHandle(handle)

    # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: a console of its own that nobody sees. Not
    # DETACHED_PROCESS: with no console at all, every git the coordinator runs would get a new
    # console (seconds each, and a window may flash up).
    DETACHED = 0x08000000 | 0x00000200
    BREAKAWAY = 0x01000000                      # CREATE_BREAKAWAY_FROM_JOB: outlive the agent's job

    class _Startup:
        """What CreateProcess reads from a STARTUPINFO: no window, no handles of ours."""
        dwFlags = 0
        wShowWindow = 0
        hStdInput = None
        hStdOutput = None
        hStdError = None
        lpAttributeList = None

    def command_line(argv):
        """The Windows command line for argv (the rules of subprocess.list2cmdline)."""
        result = []
        for arg in argv:
            backslashes = []
            if result:
                result.append(" ")
            quote = (" " in arg) or ("\t" in arg) or not arg
            if quote:
                result.append('"')
            for c in arg:
                if c == "\\":
                    backslashes.append(c)
                elif c == '"':
                    result.append("\\" * len(backslashes) * 2)
                    backslashes = []
                    result.append('\\"')
                else:
                    if backslashes:
                        result.extend(backslashes)
                        backslashes = []
                    result.append(c)
            if backslashes:
                result.extend(backslashes)
            if quote:
                result.extend(backslashes)
                result.append('"')
        return "".join(result)

    def take_slot(path):
        """The bytes of a ready-made slot file, taken exactly once, or None. The file is opened
        with no sharing, so a second hook trying at the same moment fails, and deleted when
        closed. (A rename is not enough here: Windows lets two renames of one file both
        succeed through handles that are already open.)"""
        try:
            handle = _winapi.CreateFile(path, 0x80000000 | 0x00010000,   # GENERIC_READ | DELETE
                                        0,                               # no sharing at all
                                        0, 3,                            # OPEN_EXISTING
                                        0x04000000, 0)                   # FILE_FLAG_DELETE_ON_CLOSE
        except OSError:
            return None
        try:
            chunks = []
            while True:
                data, _ = _winapi.ReadFile(handle, 65536)
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks)
        finally:
            _winapi.CloseHandle(handle)

    def _spawn(argv, cwd, env):
        line = command_line(argv)
        try:
            handles = _winapi.CreateProcess(argv[0], line, None, None, False, DETACHED | BREAKAWAY, env, cwd,
                                            _Startup())
        except OSError:                         # the agent's job allows no breakaway
            handles = _winapi.CreateProcess(argv[0], line, None, None, False, DETACHED, env, cwd, _Startup())
        _winapi.CloseHandle(handles[0])
        _winapi.CloseHandle(handles[1])
else:
    def _append(path, line):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)

    def take_slot(path):
        """The bytes of a ready-made slot file, taken exactly once, or None: renamed to a name
        of this process's own (only one rename of a name can succeed), read, deleted."""
        mine = "%s.%d.taken" % (path, os.getpid())
        try:
            os.rename(path, mine)
        except OSError:
            return None
        try:
            with open(mine, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.remove(mine)
            except OSError:
                pass

    def _spawn(argv, cwd, env):
        if os.fork():
            return
        try:                                    # the child: its own session, no streams of ours
            os.chdir(cwd)
            os.setsid()
            fd = os.open(os.devnull, os.O_RDWR)
            for target in (0, 1, 2):
                os.dup2(fd, target)
            os.execve(argv[0], argv, env)
        finally:
            os._exit(127)


def coordinator_alive(root, now):
    try:
        return now - os.stat(os.path.join(root, "coordinator.alive")).st_mtime < STALE_SECONDS
    except OSError:
        return False


def start_coordinator(root, now):
    """Start the coordinator if its heartbeat is stale and no hook tried in the last
    RESPAWN_SECONDS. Returns True when it started one. Two hooks starting one at the same
    moment is harmless: the coordinator's lock lets only one run."""
    if coordinator_alive(root, now) or os.environ.get("ORCH_NO_COORDINATOR"):
        return False                            # running, or switched off (tests, measurements)
    spawned = os.path.join(root, "coordinator.spawned")
    try:
        if now - os.stat(spawned).st_mtime < RESPAWN_SECONDS:
            return False
    except OSError:
        pass
    try:
        with open(os.path.join(root, "hooks", "launch.txt"), "rb") as fh:
            lines = fh.read().decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False                            # orch init has not said how to start it
    python = lines[0] if lines else ""
    if not os.path.isabs(python) or not os.path.isfile(python):
        return False
    fd = os.open(spawned, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    os.utime(spawned, None)
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("GIT_", "PYTHON")) or k.upper() == "PYTHONDONTWRITEBYTECODE"}
    if len(lines) > 1 and lines[1]:
        env["PYTHONPATH"] = lines[1]
    env["ORCH_HOME"] = root                     # the state folder this hook serves, whatever the agent's env says
    _spawn([python] + list(COORDINATOR_ARGS), root, env)
    return True


def state_folder(script):
    """The state folder this script serves: the parent of its own folder, which must be named
    hooks. None anywhere else, so a copy in the wrong place writes nothing."""
    folder = os.path.dirname(os.path.abspath(script))
    if os.path.basename(folder) != "hooks":
        return None
    return os.path.dirname(folder)


def wait_for_wake(root, agent, raw):
    """The Wake mode: 2 (after printing the wake note to stderr) when one arrives, else 0."""
    token = field(raw, b"session_id")
    session = safe_name(token[1:-1] if token else b"", 64, b"nosession")
    if session == b"nosession":
        return 0
    wake = os.path.join(root, "notes", session.decode("ascii"), "wake.txt")
    spool = os.path.join(root, "spool", agent.decode("ascii"), session.decode("ascii") + ".jsonl")
    started, baseline = time.time(), None
    while time.time() - started < WAKE_MAX:
        data = take_slot(wake)
        if data:
            append(spool, b'{"ts":%d,"pid":%d,"agent":"%s","event":"Wake","session_id":"%s","took":"wake"}\n'
                   % (time.time_ns(), os.getpid(), agent, session))
            sys.stderr.buffer.write(data)
            sys.stderr.buffer.flush()
            return 2
        if time.time() - started >= WAKE_GRACE:
            try:
                size = os.stat(spool).st_size
            except OSError:
                size = 0
            if baseline is None:
                baseline = size
            elif size > baseline:
                return 0                          # a new event: the person is back
        time.sleep(WAKE_POLL)
    return 0


def take_for_mod(root, agent, args):
    """The Take mode: print a session's waiting wake note, taken exactly once."""
    if len(args) < 2 or args[0] != "wake":
        return 0
    session = safe_name(args[1].encode("utf-8", "replace"), 64, b"nosession")
    if session == b"nosession":
        return 0
    data = take_slot(os.path.join(root, "notes", session.decode("ascii"), "wake.txt"))
    if data:
        spool = os.path.join(root, "spool", agent.decode("ascii"), session.decode("ascii") + ".jsonl")
        append(spool, b'{"ts":%d,"pid":%d,"agent":"%s","event":"Take","session_id":"%s","took":"wake"}\n'
               % (time.time_ns(), os.getpid(), agent, session))
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    return 0


def git_event(root, event, argv):
    """git's hooks: record the event with the worktree folder; for pre-push, print the push line."""
    cwd = os.getcwd()
    line = b'{"ts":%d,"pid":%d,"agent":"git","event":"%s","cwd":%s,"args":%d}\n' % (
        time.time_ns(), os.getpid(), event, json_string(cwd), len(argv))
    append(os.path.join(root, "spool", "git", time.strftime("%Y-%m-%d") + ".jsonl"), line)
    if event == b"pre-push":
        try:
            with open(os.path.join(root, "push", file_key(cwd) + ".txt"), "rb") as fh:
                text = fh.read(2000)
        except OSError:
            return
        if text:
            sys.stderr.buffer.write(text if text.endswith(b"\n") else text + b"\n")
            sys.stderr.buffer.flush()


def main(argv, script=__file__):
    """Returns the exit code: 0, or 2 when a Wake waiter wakes its session."""
    root = state_folder(script)
    if root is None or not argv:                 # nowhere to write, or no agent named: stay silent
        return 0
    agent = safe_name(argv[0].encode("utf-8", "replace"), 32, b"unknown")
    folder = os.path.join(root, "spool", agent.decode("ascii"))
    path = None
    try:
        event = safe_name(argv[1].encode("utf-8", "replace") if len(argv) > 1 else b"", 40, b"")
        if agent == b"git":
            git_event(root, event, argv[2:])
            return 0
        if event == b"Take":                     # before stdin: the mod gives none
            return take_for_mod(root, agent, argv[2:])
        raw = sys.stdin.buffer.read()
        if event == b"Wake":
            return wait_for_wake(root, agent, raw)
        name, line = event_line(raw, agent, event, time.time_ns(), os.getpid())
        path = os.path.join(folder, name)
        slot, kind = slot_for(root, raw, event, name[:-6].encode("ascii"))
        note = take_slot(slot) if slot else None
        if note:
            line = line[:-2] + b',"took":"' + kind + b'"}\n'
        append(path, line)
        if note:
            sys.stdout.buffer.write(note)
            sys.stdout.buffer.flush()
        start_coordinator(root, time.time())
    except BaseException as exc:                 # fail open, but leave a trace in the data
        line = error_line(agent, exc, time.time_ns(), os.getpid())
        for target in (path, os.path.join(folder, "shim_errors.jsonl")):
            if target is None:
                continue
            try:
                append(target, line)
                break
            except BaseException:
                continue
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:
        code = 0
    sys.exit(code)
