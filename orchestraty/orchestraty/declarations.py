"""What an agent says it is about to change: the MCP tool ``declare`` (level 1).

The MCP server never writes the record. A declaration is one line in orch's own spool
(``<state>/spool/orch/<day>.jsonl``, beside the lines of ``orch label`` and ``orch missed``),
and the coordinator stores it in the ``declarations`` table with the time it stops holding:
``declare_minutes`` after it was made (120 by default, in config.json). ``orch status`` shows
the declarations that hold, for live sessions. A declaration holds paths of the worktree (a file
or folder there, or a new one in a folder that is there) with no spaces or control characters,
functions or classes by name or path::name, and one task line; the coordinator drops a line of
any other shape. The task line is the agent's own words: kept on this machine and shown to the
user, never put into another session's note or tool result.
Overlaps between declared work are a later rule's job; here they are recorded and shown.
"""
import re
import time

from orchestraty import feedback

MAX_PATHS = 50
MAX_SYMBOLS = 50
MAX_ITEM = 300
MAX_TASK = 200
MAX_SESSION = 200
_NAME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*)*(#[0-9]+)?")


def is_path(text):
    """Whether `text` can be a declared path: printable, with no space or line break."""
    return isinstance(text, str) and 0 < len(text) <= MAX_ITEM and all(c.isprintable() and not c.isspace() for c in text)


def is_name(text):
    """Whether `text` is a function or class name as the record has them: name, Class.method,
    a JavaScript $name, a duplicate's name#2."""
    return isinstance(text, str) and bool(_NAME.fullmatch(text))


def is_symbol(text):
    """Whether `text` can be a declared function or class: a name, or path::name."""
    file, sep, name = text.partition("::")
    return is_path(file) and is_name(name) if sep else is_name(text)


def one_line(task):
    """A task line as it is kept: its control and format characters made spaces, cut short."""
    return "".join(c if c.isprintable() else " " for c in task).strip()[:MAX_TASK]


def append(state, session_id, paths, symbols=(), task="", now=None):
    """Append one declaration for the coordinator to store."""
    feedback.append_line(state, "declare", time.time() if now is None else now, session_id=session_id,
                         paths=list(paths), symbols=list(symbols), task=one_line(task or ""))


def _strings(value, limit):
    """A list of distinct non-empty strings of fitting length, in order, or None."""
    if not isinstance(value, list) or len(value) > limit:
        return None
    if not all(isinstance(v, str) and 0 < len(v) <= MAX_ITEM for v in value):
        return None
    return list(dict.fromkeys(value))


def parse_line(line):
    """The declaration one line of orch's spool carries, checked, or None for a line of any
    other shape, whoever wrote it."""
    if line.get("event") != "declare":
        return None
    sid, task = line.get("session_id"), line.get("task", "")
    paths, symbols = _strings(line.get("paths"), MAX_PATHS), _strings(line.get("symbols", []), MAX_SYMBOLS)
    if not (isinstance(sid, str) and 0 < len(sid) <= MAX_SESSION) or not paths or symbols is None:
        return None
    if not isinstance(task, str) or not all(map(is_path, paths)) or not all(map(is_symbol, symbols)):
        return None
    return {"session_id": sid, "paths": paths, "symbols": symbols, "task": one_line(task)}
