"""The grammar of every field a shared record may hold. Both gates check it: a record leaves this
machine (seal) and enters another (admit) only with fields that follow it, so no field is free
text. Each check is a predicate: True when the value follows its field's grammar.

The patterns are fixed here; none is made from a policy, a record or anything else a teammate
wrote. Ticket keys come from the policy, but only after each is checked to be a key
(``[A-Z][A-Z0-9]{1,9}``), so the ticket pattern is ours too.
"""
import re
import unicodedata

from orchestraty.livemap.contracts import REASON_FORMS

_HANDLE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_UNLISTED = re.compile(r"unlisted-[0-9a-f]{8}")
_DEVICE = re.compile(r"[0-9a-f]{12}")
_LABEL = _HANDLE
_SESSION = re.compile(r"s-[0-9a-f]{12}")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_BRANCH_CHARS = re.compile(r"[A-Za-z0-9._/+-]+")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_DRIVE = re.compile(r"[A-Za-z]:")
_DUPLICATE = re.compile(r"#[1-9][0-9]{0,5}")
_TICKET_KEY = re.compile(r"[A-Z][A-Z0-9]{1,9}")
_ISSUE = re.compile(r"(?:#|GH-)[0-9]{1,7}")
_NOT_IN_PATHS = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")       # controls, bidi and zero-width, surrogates, ...
LOCALS = "<locals>"

MAX_PERSON, MAX_LABEL, MAX_BRANCH, MAX_PATH, MAX_SYMBOL = 39, 24, 200, 300, 300
MAX_REASON, MAX_TICKET, MAX_TASK_LINE = 80, 17, 120
MAX_SEGMENTS = 32
MAX_CONDITIONS = 32
MAX_KEYWORDS = 64
MAX_TIME, MAX_SEQ = 2 ** 40, 2 ** 53

ENUMS = {
    "agent": ("claude", "codex", "antigravity", "git", "other"),
    "state": ("live", "ended"),
    "turn": ("working", "idle", ""),
    "kind": ("added", "removed", "moved", "modified"),
    "change": ("body", "signature", "both", ""),
    "contract": ("breaking", "review", "compatible", "none"),
    "claim_kind": ("file", "area", "symbol"),
}
STATUSES = ("A", "M", "D")


def _text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def handle(value):
    """A handle the people file may give someone: lowercase letters and digits in words joined
    by single dashes, at most 39 characters, not starting with the reserved ``unlisted-``.
    Lowercase only, because Windows and macOS keep refs/orchestraty/Alice and .../alice as one
    file; no leading dash, which git would take."""
    return _text(value, MAX_PERSON) and bool(_HANDLE.fullmatch(value)) and not value.startswith("unlisted-")


def person(value):
    """Who wrote a record: a handle, or ``unlisted-`` and eight hex digits."""
    return handle(value) or (isinstance(value, str) and bool(_UNLISTED.fullmatch(value)))


def device(value):
    return isinstance(value, str) and bool(_DEVICE.fullmatch(value))


def label(value):
    return _text(value, MAX_LABEL) and bool(_LABEL.fullmatch(value))


def session_id(value):
    """A session as a record names it: never a local session id, which stays on this machine."""
    return isinstance(value, str) and bool(_SESSION.fullmatch(value))


def enum(name, value):
    """Whether `value` is one of the enum `name`'s values (agent, state, turn, kind, change,
    contract, claim_kind)."""
    return isinstance(value, str) and value in ENUMS.get(name, ())


def status(value):
    return value in STATUSES if isinstance(value, str) else False


def sha(value):
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def branch(value):
    """A branch name: letters, digits and ``. _ / + -``, at most 200 characters, with the rules
    of git check-ref-format --branch on top: no component starting with a dot or ending in
    .lock, no ``..`` or ``//``, no leading or trailing slash, no trailing dot, no leading
    dash, and not HEAD."""
    if not _text(value, MAX_BRANCH) or not _BRANCH_CHARS.fullmatch(value):
        return False
    if value.startswith("-") or value.endswith(".") or value == "HEAD" or ".." in value:
        return False
    parts = value.split("/")
    return all(p and not p.startswith(".") and not p.endswith(".lock") for p in parts)


def ref(value):
    """A branch's full ref: refs/heads/ and a branch name."""
    return isinstance(value, str) and value.startswith("refs/heads/") and branch(value[len("refs/heads/"):])


def path(value):
    """A repository-relative path: segments joined by /, in NFC; no empty, . or .. segment, no
    leading /, drive or ~, no backslash, no segment with a space at either end, no control,
    format (bidi, zero-width), surrogate, private-use or unassigned character and no line or
    paragraph separator; at most 32 segments and 300 characters."""
    if not _text(value, MAX_PATH) or "\\" in value or value.startswith("~") or _DRIVE.match(value):
        return False
    try:
        if not unicodedata.is_normalized("NFC", value):
            return False
    except UnicodeError:
        return False
    if any(unicodedata.category(c) in _NOT_IN_PATHS for c in value):
        return False
    parts = value.split("/")
    return len(parts) <= MAX_SEGMENTS and all(p and p not in (".", "..") and p == p.strip(" ") for p in parts)


def symbol(value):
    """A function or class: ``path::Name``, where Name is identifiers joined by dots (a nested
    function's ``<locals>`` among them), with ``#n`` after a duplicate's name."""
    if not _text(value, MAX_SYMBOL) or value.count("::") != 1:
        return False
    file, name = value.split("::")
    if not path(file):
        return False
    m = _DUPLICATE.search(name)
    if m and m.end() == len(name):
        name = name[:m.start()]
    parts = name.split(".")
    if not parts or parts[0] == LOCALS or parts[-1] == LOCALS:
        return False
    return all(p.isidentifier() and p.isascii() or p == LOCALS for p in parts)


def reason(value):
    """A reason for a contract change: one of the forms contracts.py gives (REASON_FORMS),
    with its path, if it names one, a repository path."""
    if not _text(value, MAX_REASON):
        return False
    for form in REASON_FORMS:
        m = form.fullmatch(value)
        if m:
            return "path" not in form.groupindex or path(m.group("path"))
    return False


def _condition(c):
    if not isinstance(c, list) or not c or c[0] not in ("any", "name", "positional", "missing", "keywords",
                                                         "on_class"):
        return False
    what, rest = c[0], c[1:]
    if what in ("any", "on_class"):
        return not rest
    if what == "name":
        return len(rest) == 1 and _ident(rest[0])
    if what == "positional":
        return len(rest) == 1 and _int(rest[0]) and 1 <= rest[0] <= 255
    if what == "missing":
        return len(rest) == 2 and _ident(rest[0]) and (rest[1] is None or (_int(rest[1]) and 0 <= rest[1] <= 255))
    return (len(rest) == 1 and isinstance(rest[0], list) and len(rest[0]) <= MAX_KEYWORDS
            and all(_ident(k) for k in rest[0]))


def _ident(value):
    return isinstance(value, str) and bool(_IDENT.fullmatch(value))


def breaks(value, reasons):
    """Which calls a breaking change breaks: {reason: [condition]}, each reason among `reasons`
    (the ones kept), each condition one of contracts.py's shapes."""
    if not isinstance(value, dict):
        return False
    for key, conditions in value.items():
        if key not in reasons or not isinstance(conditions, list) or not 0 < len(conditions) <= MAX_CONDITIONS:
            return False
        if not all(_condition(c) for c in conditions):
            return False
    return True


def ticket_keys(keys):
    """The ticket keys a policy names, checked: each [A-Z][A-Z0-9]{1,9}. Raises ValueError."""
    if not isinstance(keys, (list, tuple)):
        raise ValueError("ticket keys are a list")
    for key in keys:
        if not isinstance(key, str) or not _TICKET_KEY.fullmatch(key):
            raise ValueError(f"a ticket key is two to ten capital letters and digits, starting with a letter: "
                             f"{str(key)[:20]!r}")
    return tuple(keys)


def ticket_pattern(keys):
    """The pattern that finds tickets (KEY-123) of these keys in a text, or None for no key.
    Made of checked keys only (ValueError otherwise), never of a pattern anyone wrote."""
    keys = ticket_keys(keys)
    if not keys:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(keys) + r")-[0-9]{1,7}(?![0-9])")


def ticket(value, keys, issues):
    """A ticket: KEY-123 with KEY among `keys`, or #123 and GH-123 when `issues` is on."""
    if not _text(value, MAX_TICKET):
        return False
    if issues and _ISSUE.fullmatch(value):
        return True
    key, dash, number = value.partition("-")
    return (bool(dash) and key in ticket_keys(keys) and number.isdigit() and number.isascii()
            and 1 <= len(number) <= 7)


def task_line(value):
    """A task line its author confirmed: one line of printable text, at most 120 characters,
    with no space at either end. The secret scanner checks it too, when it is sealed."""
    return _text(value, MAX_TASK_LINE) and value.isprintable() and value == value.strip()


def time(value):
    """A time as a record gives it: whole minutes, in seconds since the epoch."""
    return _int(value) and 0 <= value <= MAX_TIME and value % 60 == 0


def seq(value):
    return _int(value) and 1 <= value <= MAX_SEQ
