"""The live map: for each live session, the files it has changed since its base commit and
what those changes do to the code's contracts, kept current while the agents work.

A session is refreshed after each of its tool calls that can change files, and every few
seconds on a timer (which catches editors, formatters and people). A refresh asks git which
files differ from the base commit, keeps those that belong to the session, parses the changed
Python files as they are on disk and as they were at the base (parses are cached by content),
and lets the engine's ``diff`` say which functions and classes were added, removed, moved or
modified; ``contracts.classify`` rates each signature change.

Which files belong to a session:
* a file named by one of its edit events is always its own;
* a file already changed when the session was first seen is not, until its size or time changes;
* in a folder that two live sessions share, other changes are marked not attributed rather
  than guessed.
"""
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field

from orch_engine.ingest import MAX_BLOB_BYTES, read_blobs
from orch_engine.track import diff, file_fingerprint

from orchestraty import gitio
from orchestraty.livemap import changes as git_changes
from orchestraty.livemap.contracts import BREAKING, COMPATIBLE, NONE, REVIEW, classify, worst
from orchestraty.livemap.index import BaseIndexes, ParseCache, blob_id, view

PYTHON = (".py",)
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "TodoWrite", "TodoRead",
                   "ExitPlanMode", "BashOutput", "ListMcpResourcesTool", "ReadMcpResourceTool"}


@dataclass
class SymbolChange:
    symbol: str                 # "path::Qual.name" (for a move, where it is now)
    kind: str                   # added | removed | moved | modified
    change: str                 # body | signature | both | "" (what of it changed)
    contract: str               # breaking | review | compatible | none
    reasons: list
    file: str
    lines: list                 # [start, end] in the session's copy (in the base for a removal)
    moved_from: str = ""

    def as_dict(self):
        return {"symbol": self.symbol, "kind": self.kind, "change": self.change, "contract": self.contract,
                "reasons": list(self.reasons), "file": self.file, "lines": list(self.lines),
                "moved_from": self.moved_from}


@dataclass
class SessionView:
    session_id: str
    repo_key: str
    worktree: str
    base: str
    files: dict = field(default_factory=dict)          # posix path -> "A" | "M" | "D"
    unattributed: list = field(default_factory=list)   # changed in a shared folder, named by no session
    symbols: dict = field(default_factory=dict)        # symbol id -> SymbolChange
    head: dict = field(default_factory=dict)           # changed .py path -> ParsedFile (None: deleted)
    head_blobs: dict = field(default_factory=dict)     # changed .py path -> blob id on disk (None: none)
    error: str = ""

    def key(self):
        """What a refresh compares to tell whether anything changed."""
        return (tuple(sorted(self.files.items())), tuple(sorted(self.unattributed)),
                tuple(sorted((k, s.kind, s.change, s.contract, tuple(s.reasons), tuple(s.lines))
                             for k, s in self.symbols.items())))

    def breaking(self):
        return {k: s for k, s in self.symbols.items() if s.contract == BREAKING}


def _fingerprint(parsed):
    files, modules, symbols = {}, {}, {}
    for pf in parsed:
        fp = file_fingerprint(pf)
        files[pf.file] = pf.loc
        modules[pf.file] = fp["module_hash"]
        symbols.update(fp["symbols"])
    return {"files": files, "modules": modules, "symbols": symbols, "imports": [], "calls": []}


def analyse(base, head):
    """{symbol id: SymbolChange} between the base and head parses of the changed files
    ({path: ParsedFile or None})."""
    bfp = _fingerprint([pf for pf in base.values() if pf is not None])
    hfp = _fingerprint([pf for pf in head.values() if pf is not None])
    d = diff(bfp, hfp)
    bs, hs = bfp["symbols"], hfp["symbols"]
    out = {}
    for k in d["symbols_added"]:
        out[k] = SymbolChange(k, "added", "", COMPATIBLE, [], hs[k]["file"], hs[k]["lines"])
    for k in d["symbols_removed"]:
        out[k] = SymbolChange(k, "removed", "", BREAKING, ["removed"], bs[k]["file"], bs[k]["lines"])
    for m in d["symbols_moved"]:
        old, new = bs[m["from"]], hs[m["to"]]
        verdict, reasons = REVIEW, ["moved from " + old["file"]]
        if m["signature_changed"]:
            v, r = classify(new["kind"], old.get("signature"), new.get("signature"))
            verdict, reasons = worst(verdict, v), reasons + r
        out[m["to"]] = SymbolChange(m["to"], "moved", "signature" if m["signature_changed"] else "", verdict,
                                    reasons, new["file"], new["lines"], moved_from=m["from"])
    for k, change in d["symbol_changes"].items():
        if k in out:
            continue
        verdict, reasons = NONE, []
        if change in ("signature", "both"):
            verdict, reasons = classify(hs[k]["kind"], bs[k].get("signature"), hs[k].get("signature"))
        out[k] = SymbolChange(k, "modified", change, verdict, reasons, hs[k]["file"], hs[k]["lines"])
    return out


def _stat(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


class LiveMap:
    def __init__(self, state, log=None):
        self.log = log or logging.getLogger("orchestraty.coordinator")
        self.cache = ParseCache()
        self.indexes = BaseIndexes(os.path.join(state.root, "engine"), self.log)
        self.views = {}                                # session id -> SessionView
        self.sessions = {}                             # session id -> session row (live ones tracked)
        self.edits = defaultdict(set)                  # session id -> paths its edit events named
        self.dirty_at_start = {}                       # session id -> {path: (size, mtime)}
        self.pending = set()
        self._base_blobs = {}                          # (commit, path) -> blob id or None
        self._graphs = {}                              # session id -> (view key, CodeGraph)

    # sessions
    def track(self, session, repo_key, adopt_existing=False):
        """Start following a live session. Files already changed in its folder are not its own
        until they change again, unless adopt_existing (a plain-git worktree owns them all)."""
        sid = session["id"]
        if sid in self.sessions:
            return
        self.sessions[sid] = session
        self.views[sid] = SessionView(sid, repo_key, session["worktree"], session["base_commit"])
        try:
            already = {} if adopt_existing else git_changes.changed_files(session["worktree"], session["base_commit"])
        except (gitio.GitError, gitio.UntrustedRepositoryError):
            already = {}
        self.dirty_at_start[sid] = {p: _stat(os.path.join(session["worktree"], *p.split("/"))) for p in already}
        self.pending.add(sid)
        self.indexes.get(repo_key, session["base_commit"], session["worktree"])

    def forget(self, sid):
        for table in (self.sessions, self.views, self.edits, self.dirty_at_start, self._graphs):
            table.pop(sid, None)
        self.pending.discard(sid)

    def rel(self, session, path):
        """A path an event named, as a posix path inside the session's worktree, or None."""
        if not isinstance(path, str) or not path:
            return None
        top = session["worktree"]
        full = os.path.normpath(path if os.path.isabs(path) else os.path.join(session.get("cwd") or top, path))
        try:
            rel = os.path.relpath(full, top)
        except ValueError:                         # another drive
            return None
        if rel == os.curdir or rel.startswith(os.pardir + os.sep) or rel == os.pardir or os.path.isabs(rel):
            return None
        return rel.replace(os.sep, "/")

    def note_event(self, session, event, line):
        sid = session["id"]
        if sid not in self.sessions:
            return
        if event in ("PostToolUse", "PostToolUseFailure"):
            path = self.rel(session, line.get("file_path"))
            if path:
                self.edits[sid].add(path)
            if path or line.get("tool_name") not in READ_ONLY_TOOLS:
                self.pending.add(sid)

    def mark_all(self):
        self.pending.update(self.sessions)

    # refresh
    def refresh_pending(self):
        """Refresh every session waiting for it; returns the ids whose view changed."""
        changed = set()
        for sid in sorted(self.pending):
            if sid in self.sessions and self.refresh(sid):
                changed.add(sid)
        self.pending.clear()
        for key in self.indexes.take_fresh():          # callers can be found for these now
            changed.update(sid for sid, v in self.views.items() if (v.repo_key, v.base) == key)
        return changed

    def refresh(self, sid):
        session, old = self.sessions[sid], self.views[sid]
        top, base = session["worktree"], session["base_commit"]
        try:
            files = git_changes.changed_files(top, base)
        except (gitio.GitError, gitio.UntrustedRepositoryError) as exc:
            self.log.warning("session %s: no file list: %s", sid[:8], exc)
            return False
        sharing = [o for o, s in self.sessions.items() if o != sid and s["worktree"] == top]
        named_by_others = set().union(*(self.edits[o] for o in sharing)) if sharing else set()
        dirty = self.dirty_at_start.get(sid, {})
        mine, unattributed = {}, []
        for path, status in files.items():
            if path in self.edits[sid]:
                mine[path] = status
            elif path in dirty and dirty[path] == _stat(os.path.join(top, *path.split("/"))):
                continue                                # untouched since the session was first seen
            elif sharing:
                if path not in named_by_others:
                    unattributed.append(path)
            else:
                mine[path] = status
        new = SessionView(sid, old.repo_key, top, base, files=mine, unattributed=sorted(unattributed))
        try:
            base_pf, new.head, new.head_blobs = self._parses(top, base,
                                                             {p: s for p, s in mine.items() if p.endswith(PYTHON)})
            new.symbols = analyse(base_pf, new.head)
        except (gitio.GitError, gitio.UntrustedRepositoryError, RuntimeError) as exc:
            new.error = str(exc)[:300]
            self.log.warning("session %s: no symbols: %s", sid[:8], exc)
        self.views[sid] = new
        return new.key() != old.key()

    def _parses(self, top, base, python):
        """({path: base ParsedFile or None}, {path: head ParsedFile or None}, {path: head blob id})
        for the changed .py files."""
        head, head_blobs = {}, {}
        for path, status in python.items():
            head[path] = head_blobs[path] = None
            if status == "D":
                continue
            try:
                with open(os.path.join(top, *path.split("/")), "rb") as fh:
                    data = fh.read(MAX_BLOB_BYTES + 1)
            except OSError:
                continue
            if len(data) <= MAX_BLOB_BYTES:
                head_blobs[path] = blob_id(data)
                head[path] = self.cache.get(path, data, head_blobs[path])
        wanted = [p for p, s in python.items() if s != "A" and (base, p) not in self._base_blobs]
        if wanted:
            found = git_changes.blob_ids(top, base, wanted)
            for p in wanted:
                self._base_blobs[(base, p)] = found.get(p)
        blobs = {p: self._base_blobs.get((base, p)) for p, s in python.items() if s != "A"}
        missing = sorted({b for p, b in blobs.items() if b and not self.cache.known(p, b)})
        data = read_blobs(top, missing) if missing else {}
        base_pf = {}
        for path in python:
            blob = blobs.get(path)
            if not blob:
                base_pf[path] = None
            elif blob in data:
                base_pf[path] = self.cache.get(path, data[blob], blob) if len(data[blob]) <= MAX_BLOB_BYTES else None
            else:
                base_pf[path] = self.cache.get(path, b"", blob)        # a cached parse: the key is all it needs
        return base_pf, head, head_blobs

    # code graphs
    def graph(self, sid):
        """The session's code graph (its changed .py files over the base commit's index), or
        None while that index is being built."""
        v = self.views.get(sid)
        if v is None:
            return None
        idx = self.indexes.get(v.repo_key, v.base, v.worktree)
        if idx is None:
            return None
        key = tuple(sorted(v.head_blobs.items(), key=lambda kv: kv[0]))
        cached = self._graphs.get(sid)
        if cached and cached[0] == key:
            return cached[1]
        g = view(idx, v.head)
        self._graphs[sid] = (key, g)
        return g

    def index_state(self, sid):
        """"ready", "building" or "failed: <why>" for the session's base index."""
        v = self.views.get(sid)
        if v is None:
            return "unknown"
        if not v.base:
            return "no base commit"
        why = self.indexes.failed(v.repo_key, v.base)
        if why:
            return "failed: " + why
        return "ready" if self.indexes.get(v.repo_key, v.base, v.worktree) is not None else "building"
