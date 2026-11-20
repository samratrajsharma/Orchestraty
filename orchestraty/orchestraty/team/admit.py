"""The incoming gate: the one way a teammate's record enters this machine.

A record is read from the tip of its ref only, never its history, and only when that commit's
tree is the one file record.json, no larger than 256 KiB (checked before it is read). Then
``admit`` decides, in order:

1. **Structure** (``recordfile.parse_structure``): size, JSON without duplicate keys, floats or
   deep nesting, the type and length of every known field. Unknown keys are dropped and counted.
2. **Grammar** of every field, as the outgoing gate checks it. A field that fails drops the
   whole record (``bad-grammar:<field>``), since a writer that follows the rules never sends one.
   Two exceptions, for teammates on other versions: a reason that matches none of this version's
   forms is dropped alone (its function then only says its contract changed), and an agent kind
   this version does not know becomes ``other``.
3. **Writer.** The ref's person and device must be the record's. The person must be in the people
   file and the commit's author one of their emails, or, under ``people.unlisted: share``, be
   ``unlisted-`` and the hash of the author's email.
4. **Sequence.** A seq not above the highest admitted for that device is a rollback.
5. **Time.** Times later than now become now (a claim may end up to a day ahead): they are shown,
   while liveness runs on this machine's clock.
6. **This machine's own policy**, again: what it does not share is dropped and counted as
   ``policy:<field>``, such as a task line under ``task: ticket`` or a session on a never branch.
7. **Existence.** A claim on a path that is neither at the session's tip nor on the default
   branch is dropped.

A record that is not taken gets a drop reason from a closed set (DROPS); what is dropped from a
record that is taken is counted in another (DISCARDS). Neither ever holds a value from the record.
Per fetch, at most 64 writers and 4 devices a person (the newest) are read, and at most 4 MiB.
"""
import re
from collections import Counter, namedtuple
from dataclasses import replace

from orchestraty.team import grammar, identity, recordfile

DROPS = ("too-large", "bad-json", "duplicate-key", "bad-structure", "newer-format", "bad-grammar", "writer-mismatch",
         "unlisted", "rollback", "not-a-record")
DISCARDS = ("unknown-field", "unknown-reason", "policy", "cap")
MAX_WRITERS = 64
MAX_DEVICES = 4
MAX_FETCH_BYTES = 4 * 1024 * 1024
CLAIM_AHEAD = 24 * 3600              # the longest a claim holds (claims.MAX_MINUTES)
_AUTHOR = re.compile(r"author .*<([^<>\n]*)> -?[0-9]+ [+-][0-9]{4}")
_TICKET = re.compile(r"[A-Z][A-Z0-9]{1,9}-[0-9]{1,7}|(?:#|GH-)[0-9]{1,7}")

Tip = namedtuple("Tip", "commit author blob why")       # blob None unless read; why "" or the drop reason
Admitted = namedtuple("Admitted", "record why discarded")   # record None when not taken


class _Drop(Exception):
    def __init__(self, why):
        super().__init__(why)
        self.why = why


def read_tip(shadow, ref, budget=None):
    """The tip of one record ref in the shadow: Tip(commit, author email, record.json's bytes,
    why). The bytes are read only when the commit's tree is that one plain file, at most 256 KiB
    and within `budget` bytes; otherwise why is not-a-record, too-large or cap. History is not read."""
    rc, commit = shadow.git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    if rc != 0:
        return Tip("", "", None, "not-a-record")
    commit = commit.strip()
    rc, body = shadow.git(["cat-file", "commit", commit])
    head = body.split("\n\n", 1)[0].split("\n") if rc == 0 else []
    tree = next((line[5:] for line in head if line.startswith("tree ")), "")
    author = next((m.group(1) for m in map(_AUTHOR.fullmatch, head) if m), "")
    rc, listing = shadow.git(["ls-tree", "-l", "-z", tree]) if tree else (1, "")
    entries = [e for e in listing.split("\0") if e] if rc == 0 else []
    if len(entries) != 1:
        return Tip(commit, author, None, "not-a-record")
    meta, _, name = entries[0].partition("\t")
    fields = meta.split()
    if name != "record.json" or len(fields) != 4 or fields[0] != "100644" or fields[1] != "blob":
        return Tip(commit, author, None, "not-a-record")
    size = int(fields[3])
    if size > recordfile.MAX_BYTES:
        return Tip(commit, author, None, "too-large")
    if budget is not None and size > budget:
        return Tip(commit, author, None, "cap")
    rc, data = shadow.git(["cat-file", "blob", fields[2]], raw=True)
    if rc != 0:
        return Tip(commit, author, None, "not-a-record")
    return Tip(commit, author, data, "")


def exists_in(shadow, head):
    """exists(path, tip) for admit: whether a path is at a session's tip, or at `head` (the
    default branch as last fetched), in the shadow."""
    def exists(path, tip):
        for rev in (tip, head):
            if (rev and shadow.git(["rev-parse", "--verify", "--quiet", rev + "^{commit}"])[0] == 0
                    and shadow.git(["cat-file", "-e", f"{rev}:{path}"])[0] == 0):
                return True
        return False
    return exists


def admit(blob, ref_person, ref_device, author_email, people, effective, last_seq, now, exists=None):
    """Admitted(record, why, discarded) for one record's bytes: the record as this machine takes
    it, or None and the drop reason; `discarded` counts what was dropped from it. `last_seq` is
    the highest seq admitted for this person and device (0 for none); `exists(path, tip)` says
    whether a claimed path is there."""
    discarded = Counter()
    try:
        parsed = recordfile.parse_structure(blob)
    except recordfile.Refused as exc:
        return Admitted(None, exc.why, discarded)
    if parsed.unknown:
        discarded["unknown-field"] += parsed.unknown
    try:
        record = _Grammar(discarded).record(parsed.record)
        _writer(record, ref_person, ref_device, author_email, people, effective)
        if last_seq and record.seq <= last_seq:
            raise _Drop("rollback")
    except _Drop as drop:
        return Admitted(None, drop.why, discarded)
    record = _clamped(record, now)
    record = _Policy(effective, discarded).record(record)
    if exists is not None:
        record = replace(record, sessions=tuple(_existing(e, exists) for e in record.sessions))
    return Admitted(record, "", discarded)


def admit_all(tips, people, effective, last_seqs, now, exists=None):
    """[(person, device, Tip, Admitted)] for one fetch's tips, given as [(person, device, Tip)].
    Of one person's devices only the newest four (by written) are taken; the rest are counted
    as cap, as are tips read_tips did not read."""
    out = []
    for person, device, tip in tips:
        if tip.why:
            verdict = Admitted(None, "", Counter(cap=1)) if tip.why == "cap" else Admitted(None, tip.why, Counter())
        else:
            verdict = admit(tip.blob, person, device, tip.author, people, effective,
                            last_seqs.get((person, device), 0), now, exists)
        out.append((person, device, tip, verdict))
    by_person = {}
    for i, (person, _, _, verdict) in enumerate(out):
        if verdict.record is not None:
            by_person.setdefault(person, []).append(i)
    for person, rows in by_person.items():
        rows.sort(key=lambda i: out[i][3].record.written, reverse=True)
        for i in rows[MAX_DEVICES:]:
            p, d, tip, _ = out[i]
            out[i] = (p, d, tip, Admitted(None, "", Counter(cap=1)))
    return out


def read_tips(shadow, refs, budget=None):
    """[(person, device, Tip)] for record refs [(person, device, ref)], sorted: at most 64
    writers, and at most 4 MiB of records read in all. A ref past either limit is not read
    (its Tip's why is cap)."""
    budget = MAX_FETCH_BYTES if budget is None else budget
    out = []
    for n, (person, device, ref) in enumerate(sorted(refs)):
        if n >= MAX_WRITERS:
            out.append((person, device, Tip("", "", None, "cap")))
            continue
        tip = read_tip(shadow, ref, budget)
        if tip.blob is not None:
            budget -= len(tip.blob)
        out.append((person, device, tip))
    return out


def writer_row(repo_id, remote_name, person, device, tip, verdict, now, store="ref"):
    """The team_writers row for one writer as read now: what status and the dashboard show of it.
    A ref whose person or device is no name a record could have is kept cleaned, as a note would
    show it; an admitted record's sessions are kept as entries (the confirmed line among them, for
    people only)."""
    from orchestraty.delivery.notes import clean
    record = verdict.record
    if record is not None:
        verdict_word = "admitted"
    else:
        verdict_word = "ignored" if verdict.discarded.get("cap") and not verdict.why else "dropped"
    return {"repo_id": repo_id, "remote": remote_name,
            "person": person if grammar.person(person) else clean(person, 60),
            "device": device if grammar.device(device) else clean(device, 20), "store": store,
            "commit_id": tip.commit if grammar.sha(tip.commit) else "", "seq": record.seq if record else 0,
            "written": record.written if record else 0, "read": int(now * 1_000_000_000), "verdict": verdict_word,
            "why": summary(verdict),
            "entries": [{"agent": e.agent, "state": e.state, "branch": e.branch, "pushed": e.pushed is not None,
                         "ticket": e.ticket or "", "line": e.line or ""} for e in record.sessions] if record else []}


def summary(verdict):
    """What team_writers.why keeps: the drop reason, or what was dropped, counted."""
    if verdict.why:
        return verdict.why
    return ", ".join(f"{key} {n}" for key, n in sorted(verdict.discarded.items()))


# ------------------------------------------------------------------------------- grammar

class _Grammar:
    """Every field against its grammar: a failure drops the record, naming the field."""

    def __init__(self, discarded):
        self.discarded = discarded

    def record(self, r):
        _need(grammar.person(r.person), "writer.person")
        _need(grammar.device(r.device), "writer.device")
        _need(r.label == "" or grammar.label(r.label), "writer.label")
        _need(grammar.seq(r.seq), "seq")
        _need(grammar.time(r.written), "written")
        _need(grammar.tool(r.tool), "tool")
        _need(grammar.sha(r.policy), "policy")
        return replace(r, sessions=tuple(self.entry(e) for e in r.sessions))

    def entry(self, e):
        f = "sessions[]."
        _need(grammar.session_id(e.id), f + "id")
        _need(grammar.enum("state", e.state), f + "state")
        for name in ("started", "last_seen", "ended"):
            _need(grammar.time(getattr(e, name)), f + name)
        _need(grammar.enum("turn", e.turn), f + "turn")
        _need(grammar.branch(e.branch), f + "branch")
        _need(grammar.sha(e.base), f + "base")
        if e.pushed is not None:
            _need(grammar.ref(e.pushed.ref), f + "pushed.ref")
            _need(grammar.sha(e.pushed.tip), f + "pushed.tip")
        _need(e.ticket is None or (isinstance(e.ticket, str) and bool(_TICKET.fullmatch(e.ticket))), f + "task.ticket")
        _need(e.line is None or grammar.task_line(e.line), f + "task.line")
        for c in e.claims:
            self.claim(c)
        work = None if e.work is None else self.work(e.work)
        agent = e.agent if grammar.enum("agent", e.agent) else "other"
        return replace(e, agent=agent, work=work)

    def claim(self, c):
        f = "sessions[].claims[]."
        _need(grammar.enum("claim_kind", c.kind), f + "kind")
        if c.kind == "symbol":
            _need(grammar.symbol(c.target), f + "target")
            _need(c.file == c.target.split("::", 1)[0] and grammar.path(c.file), f + "file")
        else:
            _need(grammar.path(c.target), f + "target")
            _need(c.file == "", f + "file")
        _need(grammar.time(c.since), f + "since")
        _need(grammar.time(c.until), f + "until")

    def work(self, w):
        f = "sessions[].work."
        _need(grammar.sha(w.tip), f + "tip")
        for path, status in w.files:
            _need(grammar.path(path), f + "files[]")
            _need(grammar.status(status), f + "files[]")
        _need(w.more_files >= 0 and w.more_symbols >= 0, f + "more")
        return replace(w, symbols=tuple(self.symbol(s) for s in w.symbols))

    def symbol(self, s):
        f = "sessions[].work.symbols[]."
        _need(grammar.symbol(s.symbol), f + "symbol")
        _need(s.file == s.symbol.split("::", 1)[0] and grammar.path(s.file), f + "file")
        for name in ("kind", "change", "contract"):
            _need(grammar.enum(name, getattr(s, name)), f + name)
        _need(len(s.lines) == 2 and 0 <= s.lines[0] <= s.lines[1] <= 10 ** 7, f + "lines")
        _need(s.moved_from == "" or grammar.path(s.moved_from), f + "moved_from")
        _need(all(isinstance(r, str) and r for r in s.reasons), f + "reasons")
        reasons = tuple(r for r in s.reasons if grammar.reason(r))
        if len(reasons) < len(s.reasons):
            self.discarded["unknown-reason"] += len(s.reasons) - len(reasons)
        breaks = tuple((r, conditions) for r, conditions in s.breaks if r in reasons)
        _need(grammar.breaks(recordfile.breaks_json(breaks), reasons), f + "breaks")
        return replace(s, reasons=reasons, breaks=breaks)


def _need(ok, field):
    if not ok:
        raise _Drop(f"bad-grammar:{field}")


# ------------------------------------------------------------------------------- the writer

def _writer(record, ref_person, ref_device, author_email, people, effective):
    if (record.person, record.device) != (ref_person, ref_device):
        raise _Drop("writer-mismatch")
    if record.person in people:
        if people.handle_for(author_email) != record.person:
            raise _Drop("writer-mismatch")
        return
    if effective.unlisted != "share":
        raise _Drop("unlisted")
    if not record.person.startswith("unlisted-"):
        raise _Drop("unlisted")
    if not isinstance(author_email, str) or record.person != identity.unlisted_handle(author_email):
        raise _Drop("writer-mismatch")


# ------------------------------------------------------------------------------- time, policy, existence

def _clamped(record, now):
    """Times after now become now; a claim may end at most a day ahead."""
    top = recordfile.minute(now)

    def at_most(t, limit=top):
        return min(t, recordfile.minute(limit))

    sessions = []
    for e in record.sessions:
        claims = tuple(replace(c, since=at_most(c.since), until=at_most(c.until, top + CLAIM_AHEAD)) for c in e.claims)
        sessions.append(replace(e, started=at_most(e.started), last_seen=at_most(e.last_seen), ended=at_most(e.ended),
                                claims=claims))
    return replace(record, written=at_most(record.written), sessions=tuple(sessions))


class _Policy:
    """This machine's effective policy, on a teammate's record: what it does not share is dropped
    and counted as policy:<field>."""

    def __init__(self, effective, discarded):
        self.effective, self.discarded = effective, discarded

    def drop(self, field, n=1):
        self.discarded[f"policy:{field}"] += n

    def record(self, r):
        sessions = []
        for e in r.sessions:
            if not self.effective.branch_shared(e.branch) or self.effective.word_denied(e.branch):
                self.drop("branch")
                continue
            sessions.append(self.entry(e))
        return replace(r, sessions=tuple(sessions))

    def entry(self, e):
        pol = self.effective
        changes = {}
        if e.turn and not pol.activity:
            self.drop("turn")
            changes["turn"] = ""
        if e.ticket is not None and (pol.task == "none" or not grammar.ticket(e.ticket, pol.ticket_keys,
                                                                                  pol.ticket_issues)
                                     or pol.word_denied(e.ticket)):
            self.drop("task.ticket")
            changes["ticket"] = None
        if e.line is not None and (pol.task != "confirmed" or pol.word_denied(e.line)):
            self.drop("task.line")
            changes["line"] = None
        if e.claims:
            kept = tuple(c for c in e.claims if pol.claims and not self.denied(c.file or c.target, c.target))
            if len(kept) < len(e.claims):
                self.drop("claims", len(e.claims) - len(kept))
            changes["claims"] = kept
        if e.work is not None:
            if not pol.unpushed or e.pushed is not None:
                self.drop("work")
                changes["work"] = None
            else:
                changes["work"] = self.work(e.work)
        return replace(e, **changes)

    def denied(self, path, text):
        return self.effective.path_denied(path) or self.effective.word_denied(text)

    def work(self, w):
        files = tuple((p, s) for p, s in w.files if not self.denied(p, p))
        if len(files) < len(w.files):
            self.drop("work.files", len(w.files) - len(files))
        paths = {p for p, _ in files}
        symbols = tuple(s for s in w.symbols if s.file in paths and not self.effective.word_denied(s.symbol))
        if len(symbols) < len(w.symbols):
            self.drop("work.symbols", len(w.symbols) - len(symbols))
        return replace(w, files=files, symbols=symbols)


def _existing(e, exists):
    """The session without claims on a path that is nowhere: not at its tip, nor on the default branch."""
    tip = e.pushed.tip if e.pushed is not None else e.work.tip if e.work is not None else ""
    return replace(e, claims=tuple(c for c in e.claims if exists(c.file or c.target, tip)))
