"""record.json, format 1: what one person's coordinator shares about their work in a repository.

A record is a writer (person, device, label), a sequence number, when it was sealed, the tool's
version, the blob id of the policy it was sealed under, and at most 32 sessions. Each session
holds its agent kind, lifecycle times, branch, base and pushed tip, a ticket or a confirmed task
line, its live claims, and, only when nothing of it is pushed, the files and functions of its
committed but unpushed work. Never a prompt, a transcript, file contents, a path outside the
repository, a host name or a commit message: there is no field for any of them.

The bytes are canonical: ``json.dumps(..., sort_keys=True, separators=(",", ":"),
ensure_ascii=True)`` and a newline, so the same state gives the same bytes and no raw non-ASCII
byte leaves the machine. Integers only, and times are unix seconds rounded down to the minute
(0 for none). At most 256 KiB.

``parse_structure`` is the first look at bytes from elsewhere: their size before anything else,
then JSON with duplicate keys, floats, NaN and nesting deeper than any record goes refused, then
the type of every known field and the length of every list. A key this version does not know is
dropped and counted, never kept, so teammates on different versions do not blind each other. A
higher format is refused, so its reader can say "upgrade". Grammar is the gates' job, not this.
"""
import json
from collections import namedtuple
from dataclasses import dataclass

FORMAT = 1
MAX_BYTES = 256 * 1024
MAX_SESSIONS = 32
MAX_CLAIMS = 20
MAX_FILES = 300
MAX_SYMBOLS = 300
MAX_REASONS = 3
MAX_CONDITIONS = 32
MAX_KEYWORDS = 64
MAX_DEPTH = 10              # the deepest a record goes: work.symbols[].breaks[reason][i] of keywords
MAX_DIGITS = 16             # a whole number of more digits than seq's limit (2**53) is no record's
DROPS = ("too-large", "bad-json", "duplicate-key", "bad-structure", "newer-format")

Parsed = namedtuple("Parsed", "record unknown")


class Refused(ValueError):
    """Bytes that are no record of this format: `why` is one of DROPS."""

    def __init__(self, why, message):
        super().__init__(message)
        self.why = why


@dataclass(frozen=True)
class Pushed:
    ref: str
    tip: str


@dataclass(frozen=True)
class Claim:
    kind: str
    target: str
    file: str
    hard: bool
    since: int
    until: int


@dataclass(frozen=True)
class Symbol:
    symbol: str
    kind: str
    change: str
    contract: str
    reasons: tuple
    breaks: tuple               # ((reason, (condition, ...)), ...), sorted by reason
    file: str
    lines: tuple                # (first, last)
    moved_from: str


@dataclass(frozen=True)
class Work:
    tip: str
    files: tuple                # ((path, status), ...)
    symbols: tuple              # (Symbol, ...)
    more_files: int = 0
    more_symbols: int = 0


@dataclass(frozen=True)
class Entry:
    """One session."""
    id: str
    agent: str
    state: str
    started: int
    last_seen: int
    ended: int
    turn: str
    branch: str
    base: str
    pushed: Pushed = None
    ticket: str = None
    line: str = None
    claims: tuple = ()
    work: Work = None


@dataclass(frozen=True)
class Record:
    person: str
    device: str
    label: str
    seq: int
    written: int
    tool: str
    policy: str
    sessions: tuple = ()


def minute(seconds):
    """A time as a record holds it: whole seconds, rounded down to the minute."""
    seconds = int(seconds)
    return seconds - seconds % 60


def to_json(record):
    """The record as JSON values, every field given."""
    return {
        "format": FORMAT,
        "writer": {"person": record.person, "device": record.device, "label": record.label},
        "seq": record.seq,
        "written": record.written,
        "tool": record.tool,
        "policy": record.policy,
        "sessions": [_entry_json(e) for e in record.sessions],
    }


def dumps(record):
    """The canonical bytes of a record. Raises ValueError for one this format cannot hold, such
    as a time that is not a whole minute: the same check a reader makes."""
    obj = to_json(record)
    try:
        _Reader().record(obj)
    except Refused as exc:
        raise ValueError(str(exc)) from None
    data = _canonical(obj)
    if len(data) > MAX_BYTES:
        raise ValueError(f"the record is larger than {MAX_BYTES // 1024} KiB")
    return data


def content_key(record):
    """What tells two records apart beyond when they were written: the canonical bytes without
    `written` and `seq`. Equal keys mean nothing new to push."""
    obj = to_json(record)
    del obj["written"], obj["seq"]
    return _canonical(obj)


def parse_structure(blob):
    """Parsed(record, unknown) for the bytes of a record: `unknown` counts the keys dropped.
    Raises Refused."""
    if len(blob) > MAX_BYTES:
        raise Refused("too-large", f"the record is larger than {MAX_BYTES // 1024} KiB")
    try:
        obj = json.loads(blob.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_float=_no_float,
                         parse_constant=_no_float, parse_int=_whole)
    except _Duplicate:
        raise Refused("duplicate-key", "the record gives a key twice") from None
    except (ValueError, RecursionError):
        raise Refused("bad-json", "the record is not JSON of whole numbers") from None
    if _depth(obj) > MAX_DEPTH:
        raise Refused("bad-json", f"the record nests deeper than {MAX_DEPTH} levels")
    if isinstance(obj, dict) and _is_int(obj.get("format")) and obj["format"] > FORMAT:
        raise Refused("newer-format", f"the record is format {obj['format']}: upgrade Orchestraty to read it")
    reader = _Reader()
    return Parsed(reader.record(obj), reader.unknown)


# ------------------------------------------------------------------------------- writing

def _canonical(obj):
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def _entry_json(e):
    task = {}
    if e.ticket is not None:
        task["ticket"] = e.ticket
    if e.line is not None:
        task["line"] = e.line
    return {
        "id": e.id, "agent": e.agent, "state": e.state, "started": e.started, "last_seen": e.last_seen,
        "ended": e.ended, "turn": e.turn, "branch": e.branch, "base": e.base,
        "pushed": None if e.pushed is None else {"ref": e.pushed.ref, "tip": e.pushed.tip},
        "task": task,
        "claims": [{"kind": c.kind, "target": c.target, "file": c.file, "hard": c.hard, "since": c.since,
                    "until": c.until} for c in e.claims],
        "work": None if e.work is None else _work_json(e.work),
    }


def _work_json(w):
    return {
        "tip": w.tip,
        "files": [[path, status] for path, status in w.files],
        "symbols": [{"symbol": s.symbol, "kind": s.kind, "change": s.change, "contract": s.contract,
                     "reasons": list(s.reasons), "breaks": breaks_json(s.breaks), "file": s.file,
                     "lines": list(s.lines), "moved_from": s.moved_from} for s in w.symbols],
        "more": {"files": w.more_files, "symbols": w.more_symbols},
    }


def breaks_json(breaks):
    """A symbol's breaks as JSON values: {reason: [condition, ...]}, as contracts.py gives them."""
    return {reason: [_condition_json(c) for c in conditions] for reason, conditions in breaks}


def _condition_json(c):
    return [list(x) if isinstance(x, tuple) else x for x in c]


# ------------------------------------------------------------------------------- reading

class _Duplicate(Exception):
    pass


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise _Duplicate()
        out[key] = value
    return out


def _no_float(text):
    raise ValueError("no floats in a record")


def _whole(text):
    if len(text.lstrip("-")) > MAX_DIGITS:
        raise ValueError("a number too large for a record")
    return int(text)


def _depth(obj):
    """How deep containers nest, without recursion (the input is not trusted)."""
    deepest, stack = 0, [(obj, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, dict):
            deepest = max(deepest, depth)
            stack += [(v, depth + 1) for v in value.values()]
        elif isinstance(value, list):
            deepest = max(deepest, depth)
            stack += [(v, depth + 1) for v in value]
    return deepest


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class _Reader:
    """Checks the type of every known field and the length of every list, building the record;
    counts the keys it does not know."""

    def __init__(self):
        self.unknown = 0

    def record(self, obj):
        f = self._fields(obj, "the record", ("format", "writer", "seq", "written", "tool", "policy", "sessions"))
        if f["format"] != FORMAT or not _is_int(f["format"]):
            raise _bad("format")
        w = self._fields(f["writer"], "writer", ("person", "device", "label"))
        sessions = self._list(f["sessions"], "sessions", MAX_SESSIONS, self.entry)
        return Record(_str(w["person"], "writer.person"), _str(w["device"], "writer.device"),
                      _str(w["label"], "writer.label"), _int(f["seq"], "seq"), _minute(f["written"], "written"),
                      _str(f["tool"], "tool"), _str(f["policy"], "policy"), sessions)

    def entry(self, obj, where):
        f = self._fields(obj, where, ("id", "agent", "state", "started", "last_seen", "ended", "turn", "branch",
                                      "base", "pushed", "task", "claims", "work"))
        pushed = None
        if f["pushed"] is not None:
            p = self._fields(f["pushed"], where + ".pushed", ("ref", "tip"))
            pushed = Pushed(_str(p["ref"], where + ".pushed.ref"), _str(p["tip"], where + ".pushed.tip"))
        task = self._fields(f["task"], where + ".task", (), optional=("ticket", "line"))
        return Entry(
            _str(f["id"], where + ".id"), _str(f["agent"], where + ".agent"), _str(f["state"], where + ".state"),
            _minute(f["started"], where + ".started"), _minute(f["last_seen"], where + ".last_seen"),
            _minute(f["ended"], where + ".ended"), _str(f["turn"], where + ".turn"),
            _str(f["branch"], where + ".branch"), _str(f["base"], where + ".base"), pushed,
            None if "ticket" not in task else _str(task["ticket"], where + ".task.ticket"),
            None if "line" not in task else _str(task["line"], where + ".task.line"),
            self._list(f["claims"], where + ".claims", MAX_CLAIMS, self.claim),
            None if f["work"] is None else self.work(f["work"], where + ".work"))

    def claim(self, obj, where):
        f = self._fields(obj, where, ("kind", "target", "file", "hard", "since", "until"))
        if not isinstance(f["hard"], bool):
            raise _bad(where + ".hard")
        return Claim(_str(f["kind"], where + ".kind"), _str(f["target"], where + ".target"),
                     _str(f["file"], where + ".file"), f["hard"], _minute(f["since"], where + ".since"),
                     _minute(f["until"], where + ".until"))

    def work(self, obj, where):
        f = self._fields(obj, where, ("tip", "files", "symbols", "more"))
        more = self._fields(f["more"], where + ".more", ("files", "symbols"))
        return Work(_str(f["tip"], where + ".tip"), self._list(f["files"], where + ".files", MAX_FILES, _file),
                    self._list(f["symbols"], where + ".symbols", MAX_SYMBOLS, self.symbol),
                    _int(more["files"], where + ".more.files"), _int(more["symbols"], where + ".more.symbols"))

    def symbol(self, obj, where):
        f = self._fields(obj, where, ("symbol", "kind", "change", "contract", "reasons", "breaks", "file", "lines",
                                      "moved_from"))
        reasons = self._list(f["reasons"], where + ".reasons", MAX_REASONS, _str)
        lines = f["lines"]
        if not isinstance(lines, list) or len(lines) != 2:
            raise _bad(where + ".lines")
        return Symbol(_str(f["symbol"], where + ".symbol"), _str(f["kind"], where + ".kind"),
                      _str(f["change"], where + ".change"), _str(f["contract"], where + ".contract"), reasons,
                      _breaks(f["breaks"], where + ".breaks"), _str(f["file"], where + ".file"),
                      (_int(lines[0], where + ".lines"), _int(lines[1], where + ".lines")),
                      _str(f["moved_from"], where + ".moved_from"))

    def _fields(self, obj, where, required, optional=()):
        if not isinstance(obj, dict):
            raise _bad(where)
        missing = [k for k in required if k not in obj]
        if missing:
            raise Refused("bad-structure", f"{where} has no {missing[0]}")
        self.unknown += sum(1 for k in obj if k not in required and k not in optional)
        return {k: obj[k] for k in required + optional if k in obj}

    @staticmethod
    def _list(value, where, cap, item):
        if not isinstance(value, list) or len(value) > cap:
            raise _bad(where)
        return tuple(item(v, f"{where}[{i}]") for i, v in enumerate(value))


def _bad(where):
    return Refused("bad-structure", f"{where} is not of its type, or longer than its limit")


def _str(value, where):
    if not isinstance(value, str):
        raise _bad(where)
    return value


def _int(value, where):
    if not _is_int(value):
        raise _bad(where)
    return value


def _minute(value, where):
    if not _is_int(value) or value % 60:
        raise Refused("bad-structure", f"{where} is not a time in whole minutes")
    return value


def _file(value, where):
    if not isinstance(value, list) or len(value) != 2:
        raise _bad(where)
    return _str(value[0], where), _str(value[1], where)


def _breaks(value, where):
    if not isinstance(value, dict) or len(value) > MAX_REASONS:
        raise _bad(where)
    out = []
    for reason in sorted(value):
        conditions = value[reason]
        if not isinstance(conditions, list) or len(conditions) > MAX_CONDITIONS:
            raise _bad(where)
        out.append((reason, tuple(_condition(c, where) for c in conditions)))
    return tuple(out)


def _condition(value, where):
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise _bad(where)
    out = []
    for x in value:
        if isinstance(x, list):
            if len(x) > MAX_KEYWORDS or not all(isinstance(k, str) for k in x):
                raise _bad(where)
            out.append(tuple(x))
        elif x is None or isinstance(x, str) or _is_int(x):
            out.append(x)
        else:
            raise _bad(where)
    return tuple(out)
