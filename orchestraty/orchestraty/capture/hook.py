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

Only those fields are copied, each from the payload's own (top-level) members, whatever their
order: the script walks the bytes and skips each member's value whole, so a key of the same
name inside a tool's input or output is never read. Values are checked to be valid JSON
strings, never parsed; a missing or malformed field (half of a surrogate pair among them,
which no UTF-8 file can hold) is left out. ``tool_name`` and
``tool_use_id`` are read for tool events only, ``file_path`` only for the tools that edit a
file, from the tool input's own members (NotebookEdit's ``notebook_path`` is recorded as
``file_path``), ``source`` for
SessionStart, ``reason`` for SessionEnd and ``stop_hook_active`` for Stop and SubagentStop.
The payload itself (prompts, file contents, commands, tool output) is never stored; ``len`` is
its size.

Those are Claude Code's names, and an agent this script does not know is read as Claude Code:
most agents copy its shape. Two agents differ (their adapters, ``orchestraty/adapters``, say
the same; a test checks they agree), and the line keeps the names above for them too:

* Codex uses Claude Code's names, but its edit tool, apply_patch, has no file field: the files
  are on the header lines of the patch, the string ``tool_input.command`` (``*** Update File:
  <path>``, and Add File, Delete File, Move to; only that string is read, never the tool's
  output). The line gives the first as ``file_path`` (or the one whose note was taken) and the
  others as ``other_files``, made absolute from ``cwd``.
* Antigravity's names are camelCase: ``conversationId`` is the session, the first of
  ``workspacePaths`` the folder, ``transcriptPath`` the transcript; the tool is toolCall's own
  ``name`` (never a name among its args), and an edit tool names its file among
  ``toolCall.args``' own members (``TargetFile``, or ``file_path`` or ``path``), a ``file://``
  URI made a path.

Silent and fail-open: it prints nothing but a note, and always exits 0. An internal failure
appends one ``{"shim_error": "<exception class>"}`` line instead, so failures show up in the
data. Antigravity waits for an answer from its hooks, so for it the hook always prints one: the
note, or with none ``{"decision":"allow"}`` at PreToolUse and ``{}`` at its other events.

It also starts the coordinator when that is not running (its heartbeat file is stale), at
most once every few seconds across all hooks: detached, with no handle or stream of the hook's,
from the state folder (never the agent's folder, whose files must not end up on the
coordinator's import path), and with no GIT_ or PYTHON variable of the agent's environment.
``orch init`` writes the interpreter and the import path to use into ``hooks/launch.txt``.
A coordinator that could not open its record sets ``coordinator.spawned`` a minute ahead, so
no hook starts another that would fail the same way. Setting ``ORCH_NO_COORDINATOR`` turns the
start-up off (tests and measurements).

Notes. The coordinator writes each note in advance into a slot file under
``<state>/notes/<session>/``; the hook only takes the slot its event calls for and prints it,
exactly as written (it builds no JSON):

* PreToolUse of an edit tool: ``edit/<key of the file>.json``, before the edit;
* PostToolUse: ``next.json``, with the tool's result;
* Stop: ``stop.json``, unless ``stop_hook_active`` says a stop hook already kept Claude going.

A folder another session claimed has a slot of its own, ``area/<key of the folder>.json``, or
``.ask.json`` when the edit is to wait for the user's answer (a hard claim). PreToolUse takes a
folder's ``.ask.json`` slot first, the nearest folder above a file first, then a file's own edit
slot, then a folder's other slot, so an edit that must wait never leaves that for a later one;
the line records ``"took":"area"`` for a folder's slot. Looking costs one listing of the
``area`` folder, which is usually not there.

Codex takes the same slots (for a patch, the edit slot of the first of its files that has
one). Antigravity takes ``next.json`` at PreInvocation, before its next model call, and
``stop.json`` at Stop; its PreToolUse can only allow or deny, so it takes no edit slot. A slot is
taken exactly once (``take_slot``) and the spool line records ``"took"``.

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
# Each is read from the payload's own (top-level) members, whatever their order, and an edit
# tool's file from tool_input's own: a key of the same name inside a tool's input or output
# is never read, since the walk over an object's members skips each value whole.
TOOL_FIELDS = (b"tool_name", b"tool_use_id")
TOOL_EVENTS = (b"PreToolUse", b"PostToolUse", b"PostToolUseFailure")
EDIT_TOOLS = {b'"Edit"': b"file_path", b'"Write"': b"file_path", b'"MultiEdit"': b"file_path",
              b'"NotebookEdit"': b"notebook_path"}
EVENT_FIELDS = {b"SessionStart": b"source", b"SessionEnd": b"reason"}
FLAG_EVENTS = (b"Stop", b"SubagentStop")
# Codex: the names above. The files its edit tool changes are on the patch's header lines,
# each at the start of a line: in the JSON string, right after the two bytes \n.
PATCH_TOOLS = (b'"apply_patch"',)
PATCH_HEADERS = (b"Update File: ", b"Add File: ", b"Delete File: ", b"Move to: ")
MAX_PATCH_FILES = 32
# Antigravity: names of its own, and the tool in a toolCall object ({"name": ..., "args": ...}).
AG_SESSION = b"conversationId"
AG_CWD = b"workspacePaths"
MAX_WORKSPACE_PATHS = 16
AG_TRANSCRIPT = b"transcriptPath"
AG_TOOL_EVENTS = (b"PreToolUse", b"PostToolUse")
AG_EDIT_TOOLS = {b'"write_to_file"': (b"TargetFile",), b'"replace_file_content"': (b"TargetFile",),
                 b'"multi_replace_file_content"': (b"TargetFile",), b'"write_file"': (b"Path", b"file_path", b"path"),
                 b'"edit_file"': (b"Path", b"file_path", b"path")}
# The note slot each event may take, by agent (one not named takes Claude Code's), and what an
# agent that waits for an answer gets when there is no note: (by default, {event: answer}).
SLOTS = {b"claude": {b"PreToolUse": b"edit", b"PostToolUse": b"next", b"PostToolUseFailure": b"next",
                     b"Stop": b"stop"},
         b"codex": {b"PreToolUse": b"edit", b"PostToolUse": b"next", b"Stop": b"stop"},
         b"antigravity": {b"PreInvocation": b"next", b"Stop": b"stop"}}
QUIET = {b"antigravity": (b"{}", {b"PreToolUse": b'{"decision":"allow"}'})}
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
                if 0xD800 <= int(digits, 16) <= 0xDFFF:     # half of a surrogate pair: no text without the other
                    if int(digits, 16) > 0xDBFF or not _low_half(raw[k:k + 6]):
                        return None
                    k += 6
            else:
                k += 2
            continue
        if c < 32:
            return None
        k += 1
    return None


def _low_half(escape):
    """Whether `escape` is the JSON escape of the low half of a surrogate pair (\\udc00 to \\udfff)."""
    return (len(escape) == 6 and escape[:2] == b"\\u" and all(d in _HEX for d in escape[2:])
            and 0xDC00 <= int(escape[2:], 16) <= 0xDFFF)


def plain(raw):
    """raw with each escaped backslash and escaped quote made two underscores: the same length,
    so an index means the same in both, and every quote left in it starts or ends a string."""
    return raw.replace(b"\\\\", b"__").replace(b'\\"', b"__")


def _skip_space(s, k):
    while k < len(s) and s[k] in _SPACE:
        k += 1
    return k


def value_end(s, j):
    """The index just past the JSON value that starts at s[j] (s: the payload made plain), or -1.
    A string ends at its next quote; an object or an array where its brackets close, counting
    only the brackets between its strings. Each byte is looked at a set number of times: the
    next } and the next ] are kept until passed, so a long run of either costs no more."""
    c = s[j:j + 1]
    if c == b'"':
        k = s.find(b'"', j + 1)
        return k + 1 if k >= 0 else -1
    if c != b"{" and c != b"[":                     # a number, true, false or null
        k = j
        while k < len(s) and s[k] not in b",}] \t\r\n":
            k += 1
        return k if k > j else -1
    depth, k, n = 0, j, len(s)
    brace = bracket = -1                            # the next } and ] at or after k (n: none)
    while True:
        q = s.find(b'"', k)
        stop = n if q < 0 else q
        while True:                                 # the brackets before that string
            if brace < k:
                brace = s.find(b"}", k)
                brace = n if brace < 0 else brace
            if bracket < k:
                bracket = s.find(b"]", k)
                bracket = n if bracket < 0 else bracket
            close = min(brace, bracket, stop)
            depth += s.count(b"{", k, close) + s.count(b"[", k, close)
            if close == stop:
                break
            depth -= 1
            if depth == 0:
                return close + 1
            k = close + 1
        if q < 0:
            return -1
        k = s.find(b'"', q + 1) + 1                 # past that string
        if k == 0:
            return -1


def members(s, j):
    """{key: index of its value} for the members of the JSON object that starts at s[j] (s: the
    payload made plain), the first of each key; {} when no object starts there. Only that
    object's own members: each value is skipped whole, so a key inside one is never seen."""
    found = {}
    if j is None or s[j:j + 1] != b"{":
        return found
    k = _skip_space(s, j + 1)
    while s[k:k + 1] == b'"':
        end = s.find(b'"', k + 1)
        v = _skip_space(s, end + 1) if end >= 0 else len(s)
        if s[v:v + 1] != b":":
            break
        v = _skip_space(s, v + 1)
        found.setdefault(s[k + 1:end], v)
        k = value_end(s, v)
        if k < 0:
            break
        k = _skip_space(s, k)
        if s[k:k + 1] != b",":
            break
        k = _skip_space(s, k + 1)
    return found


_LAST_WALK = [(None, None)]     # (payload, what top_level found for it): the hook reads one payload


def top_level(raw):
    """(raw made plain, the payload's own members): where every reader starts. The payload
    last walked is kept, so the readers of one event walk it once."""
    last, found = _LAST_WALK[0]
    if last is not raw:
        s = plain(raw)
        found = (s, members(s, _skip_space(s, 3 if s.startswith(b"\xef\xbb\xbf") else 0)))   # past a BOM
        _LAST_WALK[0] = (raw, found)
    return found


def _patch_at(s, top):
    """Where a Codex apply_patch's patch starts: tool_input's own ``command``, or tool_input
    itself when it is the patch (a string)."""
    i = top.get(b"tool_input")
    return i if i is not None and s[i:i + 1] == b'"' else members(s, i).get(b"command")


def string_at(raw, i):
    """The JSON string token at raw[i], quotes included, or None: no index, or not a valid
    string of at most MAX_FIELD bytes."""
    if i is None or raw[i:i + 1] != b'"':
        return None
    return _string_token(raw, i)


def array_paths(raw, plain_raw, i):
    """Read the string items of a workspace array, skipping nested or invalid items."""
    if i is None or raw[i:i + 1] != b"[":
        return []
    paths = []
    i = _skip_space(plain_raw, i + 1)
    for _ in range(MAX_WORKSPACE_PATHS):
        if plain_raw[i:i + 1] == b"]":
            break
        token = string_at(raw, i)
        if token:
            path = uri_path(json_unescape(token))
            if path and path not in paths:
                paths.append(path)
        end = value_end(plain_raw, i)
        if end < 0:
            break
        i = _skip_space(plain_raw, end)
        if plain_raw[i:i + 1] != b",":
            break
        i = _skip_space(plain_raw, i + 1)
    return paths


def flag_at(raw, i):
    """True or False for the JSON true or false at raw[i], or None."""
    if i is None:
        return None
    for word, value in ((b"true", True), (b"false", False)):
        after = i + len(word)
        if raw[i:after] == word and (after == len(raw) or raw[after] in b" \t\r\n,}"):
            return value
    return None


def field(raw, key):
    """The raw JSON string value of the payload's own `"key": "..."`, quotes included, or None."""
    return string_at(raw, top_level(raw)[1].get(key))


def flag(raw, key):
    """True or False for the payload's own `"key": true|false`, or None."""
    return flag_at(raw, top_level(raw)[1].get(key))


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


def uri_path(text):
    """A path from a file:// URI (its %-escapes decoded), or the text as it is."""
    if not text.startswith("file://"):
        return text
    rest = text[7:]
    if rest[:1] == "/" and rest[2:3] == ":":        # file:///C:/...
        rest = rest[1:]
    if "%" not in rest:
        return rest
    data, out, i = rest.encode("utf-8", "surrogatepass"), bytearray(), 0
    while i < len(data):
        digits = data[i + 1:i + 3]
        if data[i] == 37 and len(digits) == 2 and all(d in _HEX for d in digits):
            out.append(int(digits, 16))
            i += 3
        else:
            out.append(data[i])
            i += 1
    return out.decode("utf-8", "replace")


def _patch_path(raw, s):
    """The path on a patch header line whose path starts at raw[s] (inside a JSON string),
    up to the line's end, or None."""
    e, end = s, min(len(raw), s + MAX_FIELD)
    while e < end and raw[e] != 34:                 # the string's closing quote
        if raw[e] == 92:                             # an escape; \n or \r ends the line
            if raw[e + 1:e + 2] in (b"n", b"r"):
                break
            e += 2
        else:
            e += 1
    token = b'"' + raw[s:e] + b'"'
    if e >= end or _string_token(token, 0) != token:
        return None
    return json_unescape(token).strip() or None


def patch_files(raw, s, command, cwd=""):
    """The files the patch in the JSON string at raw[command] (tool_input.command) changes, in
    order (at most MAX_PATCH_FILES), each made absolute from `cwd`; s is raw made plain. Only
    that string is read. Every header line starts after a newline, which the string holds as
    \\n; in s, a backslash left always starts an escape."""
    files = []
    if command is None or s[command:command + 1] != b'"':
        return files
    end, i = value_end(s, command), command
    while end > 0 and len(files) < MAX_PATCH_FILES:
        i = s.find(b"\\n*** ", i, end)
        if i < 0:
            break
        j = i + 6
        for header in PATCH_HEADERS:
            if raw.startswith(header, j):
                path = _patch_path(raw, j + len(header))
                if path and cwd and not os.path.isabs(path):
                    path = os.path.join(cwd, path)
                if path and path not in files:
                    files.append(path)
                break
        i = j
    return files


def _args_path(raw, args, keys):
    """The file an Antigravity edit tool names in toolCall.args (`args`: its members), a file://
    URI made a path, or None."""
    for key in keys:
        token = string_at(raw, args.get(key))
        if token is not None:
            return uri_path(json_unescape(token)) or None
    return None


def edit_files(raw, agent):
    """The files the tool of an edit event changes, for the agents that take edit notes: []
    when it is not an edit tool."""
    s, top = top_level(raw)
    tool = string_at(raw, top.get(b"tool_name"))
    tool_input = members(s, top.get(b"tool_input"))
    if agent == b"codex" and tool in PATCH_TOOLS:
        cwd = string_at(raw, top.get(b"tool_cwd")) or string_at(raw, top.get(b"cwd"))
        return patch_files(raw, s, _patch_at(s, top), json_unescape(cwd) if cwd else "")
    key = EDIT_TOOLS.get(tool)
    token = string_at(raw, tool_input.get(key)) if key else None
    return [json_unescape(token)] if token is not None else []


def session_of(raw, agent):
    """The payload's session id as the spool names its file (letters, digits, '-' and '_')."""
    token = field(raw, AG_SESSION if agent == b"antigravity" else b"session_id")
    return safe_name(token[1:-1] if token else b"", 64, b"nosession")


def area_note(folder, areas, path, suffix):
    """The note in the slot (named with `suffix`, one of `areas`) of the nearest folder above
    `path` that has one, taken; or None."""
    above, parent = path, os.path.dirname(path)
    while parent and parent != above:
        name = file_key(parent) + suffix
        note = take_slot(os.path.join(folder, "area", name)) if name in areas else None
        if note:
            return note
        above, parent = parent, os.path.dirname(parent)
    return None


def take_note(root, raw, event, session, agent=b"claude"):
    """(note, slot kind, the file whose slot it was) for the note this event takes, or
    (None, None, None). An edit event tries the slot of each file its tool changes, in order,
    and the slots of the folders above them (claimed areas): one that asks first."""
    kind = SLOTS.get(agent, SLOTS[b"claude"]).get(event)
    if kind is None or session == b"nosession":
        return None, None, None
    folder = os.path.join(root, "notes", session.decode("ascii"))
    if kind == b"edit":
        paths = edit_files(raw, agent)
        try:
            areas = set(os.listdir(os.path.join(folder, "area")))     # usually there is no such folder
        except OSError:
            areas = ()
        for path in paths if areas else ():                         # an edit that must wait comes first
            note = area_note(folder, areas, path, ".ask.json")
            if note:
                return note, b"area", path
        for path in paths:
            note = take_slot(os.path.join(folder, "edit", file_key(path) + ".json"))
            if note:
                return note, kind, path
        for path in paths if areas else ():
            note = area_note(folder, areas, path, ".json")
            if note:
                return note, b"area", path
        return None, None, None
    if kind == b"stop" and flag(raw, b"stop_hook_active") is True:
        return None, None, None                   # a stop hook already kept the agent going
    note = take_slot(os.path.join(folder, kind.decode("ascii") + ".json"))
    return (note, kind, None) if note else (None, None, None)


def quiet(agent, event):
    """What the hook prints when it has no note: nothing (None), or the answer the agent waits for."""
    answers = QUIET.get(agent)
    return answers[1].get(event, answers[0]) if answers else None


def safe_name(value, limit, fallback):
    """Letters, digits, '-' and '_' only, so it is safe as a file name and inside JSON."""
    kept = bytes(c for c in value if 48 <= c <= 57 or 65 <= c <= 90 or 97 <= c <= 122 or c in b"-_")[:limit]
    return kept or fallback


def event_line(raw, agent, event, now_ns, pid, first=None):
    """(session file name, spool line) for one payload. `first`: the file of a patch whose
    note was taken, which the line names as its file_path."""
    parts = [b'{"ts":%d,"pid":%d,"agent":"%s","event":"%s","len":%d' % (now_ns, pid, agent, event, len(raw))]
    if agent == b"antigravity":
        session = _antigravity_fields(raw, event, parts)
    else:
        session = _fields(raw, agent, event, parts, first)
    parts.append(b"}\n")
    return safe_name(session, 64, b"nosession").decode("ascii") + ".jsonl", b"".join(parts)


def _fields(raw, agent, event, parts, first):
    """Claude Code's names (Codex's, and those of an agent this script does not know), added to
    the line's parts. Returns the session id."""
    s, top = top_level(raw)
    session = b""
    tool = cwd = None
    not_a_tool_event = bool(event) and event not in TOOL_EVENTS
    for key in FIELDS:
        if not_a_tool_event and key in TOOL_FIELDS:
            continue
        token = string_at(raw, top.get(key))
        if token is not None:
            parts.append(b',"%s":%s' % (key, token))
            if key == b"session_id":
                session = token[1:-1]
            elif key == b"tool_name":
                tool = token
            elif key == b"cwd":
                cwd = token
    path_key = EDIT_TOOLS.get(tool) if event in TOOL_EVENTS else None
    if agent == b"codex" and event in TOOL_EVENTS:
        tool_cwd = string_at(raw, top.get(b"tool_cwd"))
        if tool_cwd:
            parts.append(b',"tool_cwd":' + tool_cwd)
            cwd = tool_cwd
    if path_key:
        token = string_at(raw, members(s, top.get(b"tool_input")).get(path_key))
        if token is not None:
            parts.append(b',"file_path":%s' % token)
    elif agent == b"codex" and event in TOOL_EVENTS and tool in PATCH_TOOLS:
        command = _patch_at(s, top)
        _files(parts, patch_files(raw, s, command, json_unescape(cwd) if cwd else ""), first)
    extra = EVENT_FIELDS.get(event)
    if extra:
        token = string_at(raw, top.get(extra))
        if token is not None:
            parts.append(b',"%s":%s' % (extra, token))
    if event in FLAG_EVENTS:
        value = flag_at(raw, top.get(b"stop_hook_active"))
        if value is not None:
            parts.append(b',"stop_hook_active":%s' % (b"true" if value else b"false"))
    return session


def _files(parts, files, first=None):
    """Adds a patch's files to the line's parts: file_path (the first, or `first`) and
    other_files (the rest)."""
    if first in files:
        files = [first] + [f for f in files if f != first]
    if files:
        parts.append(b',"file_path":' + json_string(files[0]))
    if len(files) > 1:
        parts.append(b',"other_files":[' + b",".join(json_string(f) for f in files[1:]) + b"]")


def _antigravity_fields(raw, event, parts):
    """Antigravity's names, added to the line's parts under the line's own. Returns the session id."""
    s, top = top_level(raw)
    session = b""
    token = string_at(raw, top.get(AG_SESSION))
    if token is not None:
        parts.append(b',"session_id":%s' % token)
        session = token[1:-1]
    call = members(s, top.get(b"toolCall")) if event in AG_TOOL_EVENTS else {}
    tool = string_at(raw, call.get(b"name"))         # toolCall's own name, never one in its args
    if tool is not None:
        parts.append(b',"tool_name":%s' % tool)
    roots = array_paths(raw, s, top.get(AG_CWD))
    if roots:
        parts.append(b',"cwd":' + json_string(roots[0]))
    if len(roots) > 1:
        parts.append(b',"workspace_paths":[' + b",".join(json_string(path) for path in roots) + b"]")
    token = string_at(raw, top.get(AG_TRANSCRIPT))
    if token is not None:
        parts.append(b',"transcript_path":%s' % token)
    keys = AG_EDIT_TOOLS.get(tool)
    args = members(s, call.get(b"args"))
    path = _args_path(raw, args, keys) if keys else None
    if path:
        parts.append(b',"file_path":' + json_string(path))
    if tool == b'"run_command"':
        directory = _args_path(raw, args, (b"Cwd", b"cwd"))
        if directory:
            parts.append(b',"tool_cwd":' + json_string(directory))
    return session


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
    """git's hooks: record the event with the worktree folder; for pre-push, print the push line.
    While team sync is on somewhere (<state>/team/enabled), a pre-push line also names the remote
    and starts the coordinator, which then reads git's reflog for the push: a push git refuses
    afterwards must send nothing, so this line never sends anything itself."""
    cwd = os.getcwd()
    team = event == b"pre-push" and os.path.exists(os.path.join(root, "team", "enabled"))
    remote = b',"remote":%s' % json_string(argv[0][:200] if argv else "") if team else b""
    line = b'{"ts":%d,"pid":%d,"agent":"git","event":"%s","cwd":%s,"args":%d%s}\n' % (
        time.time_ns(), os.getpid(), event, json_string(cwd), len(argv), remote)
    append(os.path.join(root, "spool", "git", time.strftime("%Y-%m-%d") + ".jsonl"), line)
    if team:
        start_coordinator(root, time.time())
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
    if not argv:                                 # no agent named: stay silent
        return 0
    agent = safe_name(argv[0].encode("utf-8", "replace"), 32, b"unknown")
    event = safe_name(argv[1].encode("utf-8", "replace") if len(argv) > 1 else b"", 40, b"")
    code, note = (0, None) if root is None else record(root, agent, event, argv)   # None: nowhere to write
    answer = note or quiet(agent, event)        # an agent that waits for an answer always gets one
    if answer:
        try:
            sys.stdout.buffer.write(answer)
            sys.stdout.buffer.flush()
        except BaseException:
            pass
    return code


def record(root, agent, event, argv):
    """Append the event's line and take its note: (exit code, the note or None). Never raises."""
    folder = os.path.join(root, "spool", agent.decode("ascii"))
    path = note = None
    try:
        if agent == b"git":
            git_event(root, event, argv[2:])
            return 0, None
        if event == b"Take":                     # before stdin: the mod gives none
            return take_for_mod(root, agent, argv[2:]), None
        raw = sys.stdin.buffer.read()
        if event == b"Wake":
            return wait_for_wake(root, agent, raw), None
        note, kind, first = take_note(root, raw, event, session_of(raw, agent), agent)
        name, line = event_line(raw, agent, event, time.time_ns(), os.getpid(), first)
        path = os.path.join(folder, name)
        if note:
            line = line[:-2] + b',"took":"' + kind + b'"}\n'
        append(path, line)
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
    return 0, note


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:
        code = 0
    sys.exit(code)
