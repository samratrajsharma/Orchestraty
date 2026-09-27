"""Pre-edit heads-up hook: ready-made context for the file an agent is about to edit.

Status: throwaway (Phase 0). Not part of the product.

Claude Code runs it before Edit, Write and MultiEdit (a PreToolUse hook) as

    python -S -I capture_headsup.py <headsup_dir>

with the hook payload on stdin. It takes the payload's "file_path", and if a ready-made file
exists for that path (<headsup_dir>/<key>.json, written by capture_headsup_writer.py) it copies
that file's bytes to stdout unchanged: a complete hookSpecificOutput JSON with
additionalContext. It builds no JSON and parses none. On a miss, or any failure, it prints
nothing, writes nothing and exits 0.

Built-in modules only; the payload field is found with capture_shim.field.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # -I leaves the script's folder out
import capture_shim  # noqa: E402

MAX_KEY = 180
_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


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
            if 0xD800 <= code <= 0xDBFF and s[i:i + 2] == "\\u":      # a surrogate pair
                low = int(s[i + 2:i + 6], 16)
                if 0xDC00 <= low <= 0xDFFF:
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    i += 6
            out.append(chr(code))
        else:
            out.append(_ESCAPES[e])
            i += 2
    return "".join(out)


def _fnv1a64(text):
    h = 0xCBF29CE484222325
    for byte in text.encode("utf-8", "surrogatepass"):
        h = ((h ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def key_for(path):
    """A file name for a target path. Separators are unified and, where the file system ignores
    case (Windows, macOS), case too. Other characters are escaped reversibly ("_" as "__",
    anything else as "_<hex>_"), so two different paths never share a key. Long keys keep
    their start and end plus a 64-bit hash of the whole."""
    p = path.replace("\\", "/")
    if sys.platform in ("win32", "darwin"):
        p = p.lower()
    key = "".join(c if c in _SAFE else ("__" if c == "_" else "_%x_" % ord(c)) for c in p)
    if len(key) > MAX_KEY:
        key = key[:60] + "~" + key[-90:] + "~" + _fnv1a64(p)
    return key


def main(argv):
    if not argv:
        return
    raw = sys.stdin.buffer.read()
    token = capture_shim.field(raw, b"file_path")
    if token is None:
        return
    path = os.path.join(argv[0], key_for(json_unescape(token)) + ".json")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return                                       # a miss: nothing at all
    if data:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BaseException:
        pass
