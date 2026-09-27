"""Capture shim: one hook event in, one spool line out.

Status: throwaway (Phase 0). Not part of the product.

An agent hook runs it as

    python -S -I capture_shim.py <spool_dir> <agent> [<event>]

with the hook payload (JSON) on stdin. It appends one line to
<spool_dir>/<agent>/<session_id>.jsonl, and writes nothing else anywhere:

    {"ts": <ns>, "pid": <pid>, "agent": "...", "event": "...", "len": <payload bytes>,
     "session_id": "...", "hook_event_name": "...", "tool_name": "...",
     "tool_use_id": "...", "cwd": "...", "transcript_path": "..."}

Only those six fields are copied from the payload, found with bytes.find, never parsed.
Missing or malformed fields are left out. The payload body, the prompt text and the tool
input are never stored; `len` is the payload's size in bytes.

Silent and fail-open: it prints nothing and always exits 0. An internal failure appends
one {"shim_error": "<exception class>"} line instead, so failures show up in the data.

Built-in modules only (sys, os, time, and _winapi on Windows), and no json or site, so the
interpreter starts fast. The append is atomic with several writers: on Windows the file is
opened with FILE_APPEND_DATA only, so the kernel places every write at the end of the file
(O_APPEND through the C runtime lost events with 5 writers); on POSIX it is O_APPEND.
"""
import os
import sys
import time

FIELDS = (b"session_id", b"hook_event_name", b"tool_name", b"tool_use_id", b"cwd", b"transcript_path")
MAX_FIELD = 4096            # bytes; longer values are left out
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


def field(raw, key):
    """The raw JSON string value of the first `"key": "..."` in raw, quotes included, or None.
    A "key" that is not followed by a colon and a string (for example the word used as a value)
    is skipped. Keys inside string values cannot match: their quotes are escaped there."""
    needle = b'"' + key + b'"'
    start = 0
    while True:
        i = raw.find(needle, start)
        if i < 0:
            return None
        j = i + len(needle)
        while j < len(raw) and raw[j] in b" \t\r\n":
            j += 1
        if j < len(raw) and raw[j] == 58:        # ':'
            j += 1
            while j < len(raw) and raw[j] in b" \t\r\n":
                j += 1
            if j < len(raw) and raw[j] == 34:
                return _string_token(raw, j)
            return None
        start = i + 1


def safe_name(value, limit, fallback):
    """Letters, digits, '-' and '_' only, so it is safe as a file name and inside JSON."""
    kept = bytes(c for c in value if 48 <= c <= 57 or 65 <= c <= 90 or 97 <= c <= 122 or c in b"-_")[:limit]
    return kept or fallback


def event_line(raw, agent, event, now_ns, pid):
    """(session file name, spool line) for one payload."""
    parts = [b'{"ts":%d,"pid":%d,"agent":"%s","event":"%s","len":%d' % (now_ns, pid, agent, event, len(raw))]
    session = b""
    for key in FIELDS:
        token = field(raw, key)
        if token is not None:
            parts.append(b',"%s":%s' % (key, token))
            if key == b"session_id":
                session = token[1:-1]
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
else:
    def _append(path, line):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)


def main(argv):
    if not argv:
        return                                   # no spool given: nowhere to write, stay silent
    spool = argv[0]
    agent = safe_name(argv[1].encode("utf-8", "replace") if len(argv) > 1 else b"", 32, b"unknown")
    folder = os.path.join(spool, agent.decode("ascii"))
    path = None
    try:
        event = safe_name(argv[2].encode("utf-8", "replace") if len(argv) > 2 else b"", 40, b"")
        raw = sys.stdin.buffer.read()
        name, line = event_line(raw, agent, event, time.time_ns(), os.getpid())
        path = os.path.join(folder, name)
        append(path, line)
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


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BaseException:
        pass
