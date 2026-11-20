"""The outgoing gate: the one way a record leaves this machine.

``seal(projection, effective, me, device, seq, now)`` makes the canonical bytes of a record from a
projection of this machine's sessions, in this order:

1. **Projection.** ``Projection`` and ``SessionFacts`` are closed sets of named fields. There is
   no field for a path outside the repository, a worktree, a host name, a prompt, a transcript,
   a note, an email or a commit message, nor for the agent's own session id: a session goes under
   the opaque id its device gives it.
2. **Policy.** What the effective policy does not share is dropped. A session on a branch it
   does not share, or on no branch, is left out whole: not even its existence is shared.
3. **Grammar.** Each field must follow its grammar (``team.grammar``), or its item is withheld.
4. **Deny.** A path that the built-in list or deny.paths names, and any text with a deny word,
   is withheld with its item.
5. **The secret scanner.** A hit withholds the smallest item that still means something: the
   session for its branch, a file and its functions for a path, a function for its name or a
   reason, and the ticket, the line or the claim for theirs.
6. **Limits.** At most 32 sessions, 20 claims each, 300 files and 300 functions of unpushed work
   (what is cut is counted in ``more``), 3 reasons a function, and 256 KiB in all.
7. **The bytes, once more.** The whole record is scanned again. A hit there means an earlier
   step has a bug, so the record is held, and the log names the rule only.

Each withheld item comes back as Withheld(field, rule, fingerprint). The fingerprint is an HMAC
of the value with the device's secret, so two withheld values can be told apart; the value
itself is never kept, logged, printed or put in an exception. ``verify(data)`` is the check
before any push: it reads the bytes again and puts every field through the grammar and the
scanner, so bytes that did not come from ``seal`` are never pushed.
"""
import logging
import re
from collections import namedtuple
from dataclasses import dataclass, replace

from orchestraty.team import grammar, recordfile, scan

log = logging.getLogger(__name__)

MAX_LINE_NUMBER = 10 ** 7
_ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}")             # a \u escape is no part of any run of the scanner's
_TICKET = re.compile(r"[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}|(?:#|GH-)[0-9]{1,7}")

Withheld = namedtuple("Withheld", "field rule fingerprint")
Sealed = namedtuple("Sealed", "data withheld record held")     # data is None when the record is held


class SealError(Exception):
    """Bytes or a writer that cannot be a sealed record. The message names a field and a rule,
    never a value."""


@dataclass(frozen=True)
class SessionFacts:
    """One local session as the projection gives it to the gate."""
    id: str                     # the opaque id its device gives it (Device.session_id)
    agent: str
    state: str
    started: int
    last_seen: int
    ended: int = 0
    turn: str = ""
    branch: str = ""            # "" on a detached HEAD: not shared
    base: str = ""
    pushed: recordfile.Pushed = None
    ticket: str = ""            # from the branch name, or given with orch team task --ticket
    line: str = ""              # only a task line its author confirmed
    claims: tuple = ()          # recordfile.Claim
    work: recordfile.Work = None    # committed but unpushed work, when nothing of the branch is pushed


@dataclass(frozen=True)
class Projection:
    tool: str                   # this Orchestraty's version
    policy: str                 # the blob id of the policy in force
    sessions: tuple = ()


def seal(projection, effective, me, device, seq, now):
    """Sealed(data, withheld, record, held) for this machine's record: `me` is identity.Me, and
    `device` the identity.Device whose secret fingerprints what is withheld. Raises SealError
    when sync is off or the writer cannot be named."""
    off = effective.off()
    if off:
        raise SealError(f"team sync is off: {off[0]}")
    if not grammar.person(me.person) or not grammar.device(device.id):
        raise SealError("the writer is not a person and a device")
    if not grammar.tool(projection.tool) or not grammar.sha(projection.policy):
        raise SealError("the projection names no tool version or policy blob")
    if not grammar.seq(seq):
        raise SealError("seq is out of range")
    gate = _Gate(effective, device.fingerprint)
    label = device.label if device.label and gate.keep(device.label, "label", "writer.label") else ""
    entries = [e for e in (gate.session(s) for s in projection.sessions) if e is not None]
    record = recordfile.Record(me.person, device.id, label, seq, _time(now), projection.tool, projection.policy,
                               _keep_sessions(entries))
    record, data = _fit(record)
    rules = _final_scan(data)
    if rules:
        log.error("a record was held back: the scan of its bytes found %s", ", ".join(rules))
        return Sealed(None, gate.withheld, record, "final scan")
    return Sealed(data, gate.withheld, record, "")


def verify(data, effective=None):
    """Check bytes before they are pushed: a record as seal writes it, every field following its
    grammar and passing the scanner (and the effective policy, when given). Raises SealError."""
    try:
        parsed = recordfile.parse_structure(data)
    except recordfile.Refused as exc:
        raise SealError(f"not a record ({exc.why})") from None
    if parsed.unknown:
        raise SealError("the record holds a field no seal writes")
    record = parsed.record
    for value, check, field in ((record.person, grammar.person, "writer.person"),
                                (record.device, grammar.device, "writer.device"), (record.seq, grammar.seq, "seq"),
                                (record.written, grammar.time, "written"), (record.tool, grammar.tool, "tool"),
                                (record.policy, grammar.sha, "policy")):
        if not check(value):
            raise SealError(f"{field} fails grammar")
    gate = _Gate(effective, None, strict=True)
    if record.label:
        gate.keep(record.label, "label", "writer.label")
    again = replace(record, sessions=tuple(e for e in (gate.session(s) for s in record.sessions) if e is not None))
    if recordfile.dumps(again) != data:
        raise SealError("the record is not as seal writes it")
    rules = _final_scan(data)
    if rules:
        raise SealError(f"the record's bytes hold what the scanner takes for a secret ({', '.join(rules)})")


def remember(rec, repo_id, ts, withheld):
    """Keep what was withheld from a record in the record database (team_withheld): field, rule
    and fingerprint, never the value."""
    for w in withheld:
        rec.add_withheld(repo_id, ts, w.field, w.rule, w.fingerprint)


# ------------------------------------------------------------------------------- the gate

class _Gate:
    """Policy, deny, grammar and the scanner for each item. With `strict` (verify), an item that
    would be withheld is an error instead, and nothing is rounded or mended."""

    def __init__(self, effective, fingerprint, strict=False):
        self.effective, self.fingerprint, self.strict = effective, fingerprint, strict
        self.withheld = []

    def hold(self, field, rule, value):
        if self.strict:
            raise SealError(f"{field} fails {rule}")
        self.withheld.append(Withheld(field, rule, self.fingerprint(str(value))))

    def failing(self, value, kind):
        """The rule a value fails, or None: its grammar, deny (paths, then words), the scanner."""
        if not self._grammar(value, kind):
            return "grammar"
        pol = self.effective
        if pol is not None:
            path = value.split("::", 1)[0] if kind == "symbol" else value if kind == "path" else None
            if path is not None and pol.path_denied(path):
                return "deny-path"
            words = value[len("refs/heads/"):] if kind == "ref" and value.startswith("refs/heads/") else value
            if kind != "sha" and pol.word_denied(words):
                return "deny-word"
        hits = scan.scan(value, sha=kind == "sha")
        return hits[0].rule if hits else None

    def keep(self, value, kind, field):
        """Whether a value may go; holds it, with the rule it fails, when not."""
        rule = self.failing(value, kind) if isinstance(value, str) else "grammar"
        if rule:
            self.hold(field, rule, value)
        return rule is None

    def _grammar(self, value, kind):
        if kind == "ticket":
            pol = self.effective
            return grammar.ticket(value, pol.ticket_keys, pol.ticket_issues) if pol else bool(_TICKET.fullmatch(value))
        if kind == "ident":
            return value.isidentifier() and value.isascii() and len(value) <= 64
        return {"branch": grammar.branch, "ref": grammar.ref, "sha": grammar.sha, "path": grammar.path,
                "symbol": grammar.symbol, "reason": grammar.reason, "line": grammar.task_line,
                "label": grammar.label}[kind](value)

    def mend(self, ok, field, value, mended):
        """`value` when ok; otherwise the gate's `mended` value (an error under strict)."""
        if ok:
            return value
        if self.strict:
            raise SealError(f"{field} fails grammar")
        return mended

    def time(self, value, field):
        if self.strict:
            return self.mend(grammar.time(value), field, value, 0)
        return _time(value)

    def session(self, s):
        pol = self.effective
        if pol is not None and (not s.branch or not pol.branch_shared(s.branch)):
            return None                                  # not shared: not even that it exists
        if not self.keep(s.branch, "branch", "sessions[].branch"):
            return None
        if not grammar.session_id(s.id):
            self.hold("sessions[].id", "grammar", s.id)
            return None
        if s.pushed is not None and not self.keep(s.pushed.ref, "ref", "sessions[].pushed.ref"):
            return None                                  # the branch's name on the remote: as the branch
        if not self.keep(s.base, "sha", "sessions[].base"):
            return None
        if not grammar.enum("state", s.state):
            self.hold("sessions[].state", "grammar", s.state)
            return None
        agent = self.mend(grammar.enum("agent", s.agent), "sessions[].agent", s.agent, "other")
        turn = s.turn if pol is None or pol.activity else ""
        turn = self.mend(grammar.enum("turn", turn), "sessions[].turn", turn, "")
        pushed = s.pushed
        if pushed is not None and not self.keep(pushed.tip, "sha", "sessions[].pushed.tip"):
            pushed = None
        mode = pol.task if pol is not None else "confirmed"
        ticket = s.ticket if s.ticket and mode in ("ticket", "confirmed") else None
        if ticket is not None and not self.keep(ticket, "ticket", "sessions[].task.ticket"):
            ticket = None
        line = s.line if s.line and mode == "confirmed" else None
        if line is not None and not self.keep(line, "line", "sessions[].task.line"):
            line = None
        claims = () if pol is not None and not pol.claims else tuple(
            c for c in (self.claim(c) for c in s.claims) if c is not None)
        if len(claims) > recordfile.MAX_CLAIMS:
            claims = tuple(sorted(claims, key=lambda c: c.since, reverse=True)[:recordfile.MAX_CLAIMS])
        work = None
        if s.work is not None and pushed is None and (pol is None or pol.unpushed):
            work = self.work(s.work)
        return recordfile.Entry(s.id, agent, s.state, self.time(s.started, "sessions[].started"),
                                self.time(s.last_seen, "sessions[].last_seen"), self.time(s.ended, "sessions[].ended"),
                                turn, s.branch, s.base, pushed, ticket, line, claims, work)

    def claim(self, c):
        field = "sessions[].claims[]"
        if not grammar.enum("claim_kind", c.kind):
            self.hold(field + ".kind", "grammar", c.kind)
            return None
        if not self.keep(c.target, "symbol" if c.kind == "symbol" else "path", field + ".target"):
            return None
        if c.kind == "symbol":
            if not self.keep(c.file, "path", field + ".file"):
                return None
            if c.file != c.target.split("::", 1)[0]:
                self.hold(field + ".file", "grammar", c.file)
                return None
        elif c.file != "":
            self.hold(field + ".file", "grammar", c.file)
            return None
        if not isinstance(c.hard, bool):
            self.hold(field + ".hard", "grammar", c.hard)
            return None
        return recordfile.Claim(c.kind, c.target, c.file, c.hard, self.time(c.since, field + ".since"),
                                self.time(c.until, field + ".until"))

    def work(self, w):
        if not self.keep(w.tip, "sha", "sessions[].work.tip"):
            return None
        files, kept, gone = [], set(), set()
        for path, status in w.files:
            if not grammar.status(status):
                self.hold("sessions[].work.files[]", "grammar", path)
                gone.add(path)
            elif self.keep(path, "path", "sessions[].work.files[]"):
                files.append((path, status))
                kept.add(path)
            else:
                gone.add(path)
        symbols = []
        for sym in w.symbols:
            if sym.file in gone:
                continue                                 # withheld with its file
            if sym.file not in kept:
                self.hold("sessions[].work.symbols[].file", "grammar", sym.file)
                continue
            s = self.symbol(sym)
            if s is not None:
                symbols.append(s)
        more_files, more_symbols = w.more_files, w.more_symbols
        if len(files) > recordfile.MAX_FILES:
            more_files += len(files) - recordfile.MAX_FILES
            files = files[:recordfile.MAX_FILES]
        if len(symbols) > recordfile.MAX_SYMBOLS:
            more_symbols += len(symbols) - recordfile.MAX_SYMBOLS
            symbols = symbols[:recordfile.MAX_SYMBOLS]
        if self.strict and (more_files, more_symbols) != (w.more_files, w.more_symbols):
            raise SealError("sessions[].work is over its limits")
        return recordfile.Work(w.tip, tuple(files), tuple(symbols), more_files, more_symbols)

    def symbol(self, s):
        field = "sessions[].work.symbols[]"
        if not self.keep(s.symbol, "symbol", field + ".symbol"):
            return None
        if s.symbol.split("::", 1)[0] != s.file:
            self.hold(field + ".file", "grammar", s.file)
            return None
        for name, value in (("kind", s.kind), ("change", s.change), ("contract", s.contract)):
            if not grammar.enum(name, value):
                self.hold(f"{field}.{name}", "grammar", value)
                return None
        reasons = tuple(s.reasons)
        if len(reasons) > recordfile.MAX_REASONS:
            reasons = self.mend(False, field + ".reasons", reasons, reasons[:recordfile.MAX_REASONS])
        if not all(self.keep(r, "reason", field + ".reasons[]") for r in reasons):
            return None
        breaks = tuple((r, conditions) for r, conditions in s.breaks if r in reasons)
        if len(breaks) != len(s.breaks):
            breaks = self.mend(False, field + ".breaks", breaks, breaks)
        if not grammar.breaks(recordfile.breaks_json(breaks), reasons):
            self.hold(field + ".breaks", "grammar", s.symbol)
            return None
        for name in _names(breaks):
            if not self.keep(name, "ident", field + ".breaks"):
                return None
        lines = tuple(s.lines)
        if not (len(lines) == 2 and all(isinstance(n, int) and not isinstance(n, bool) for n in lines)
                and 0 <= lines[0] <= lines[1] <= MAX_LINE_NUMBER):
            self.hold(field + ".lines", "grammar", s.symbol)
            return None
        if s.moved_from and not self.keep(s.moved_from, "path", field + ".moved_from"):
            return None
        return recordfile.Symbol(s.symbol, s.kind, s.change, s.contract, reasons, breaks, s.file, lines, s.moved_from)


def _names(breaks):
    """The names a symbol's breaks hold: parameters and keywords."""
    for _, conditions in breaks:
        for condition in conditions:
            for x in condition[1:]:
                if isinstance(x, str):
                    yield x
                elif isinstance(x, tuple):
                    yield from x


def _time(seconds):
    return min(max(recordfile.minute(seconds), 0), grammar.MAX_TIME - grammar.MAX_TIME % 60)


def _keep_sessions(entries):
    """At most 32 sessions: live ones first, then the latest seen. Their order is kept."""
    if len(entries) <= recordfile.MAX_SESSIONS:
        return tuple(entries)
    ranked = sorted(range(len(entries)), key=lambda i: (entries[i].state != "live", -entries[i].last_seen, i))
    chosen = set(ranked[:recordfile.MAX_SESSIONS])
    return tuple(e for i, e in enumerate(entries) if i in chosen)


def _fit(record):
    """(record, bytes) within 256 KiB: unpushed functions go first, then files, then claims,
    then the least recent sessions."""
    while True:
        try:
            return record, recordfile.dumps(record)
        except ValueError:
            if not record.sessions:
                raise
        record = _smaller(record)


def _smaller(record):
    sessions = list(record.sessions)
    sizes = [(len(e.work.symbols) if e.work else 0, len(e.work.files) if e.work else 0, len(e.claims)) for e in sessions]
    for part in range(3):
        i = max(range(len(sessions)), key=lambda k: sizes[k][part])
        n = sizes[i][part]
        if n:
            e, keep = sessions[i], n // 2
            if part == 0:
                e = replace(e, work=replace(e.work, symbols=e.work.symbols[:keep],
                                            more_symbols=e.work.more_symbols + n - keep))
            elif part == 1:
                kept = e.work.files[:keep]
                paths = {p for p, _ in kept}
                e = replace(e, work=replace(e.work, files=kept, more_files=e.work.more_files + n - keep,
                                            symbols=tuple(s for s in e.work.symbols if s.file in paths)))
            else:
                e = replace(e, claims=e.claims[:keep])
            sessions[i] = e
            return replace(record, sessions=tuple(sessions))
    return replace(record, sessions=tuple(sessions[:-1]))


def _final_scan(data):
    """The rules the scanner finds in a record's bytes, shas aside: a hit means a bug."""
    text = _ESCAPE.sub(" ", data.decode("ascii"))
    return sorted({h.rule for h in scan.scan(text) if h.rule != "hex-run"})
