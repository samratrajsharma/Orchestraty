"""The live map: for each live session, the files it has changed since its base commit and
what those changes do to the code's contracts, kept current while the agents work.

A session is refreshed after each of its tool calls that can change files, and every few
seconds on a timer (which catches editors, formatters and people). A refresh asks git which
files differ from the base commit, keeps those that belong to the session, parses the changed
Python files as they are on disk and as they were at the base (parses are cached by content),
and lets the engine's ``diff`` say which functions and classes were added, removed, moved or
modified; ``contracts.classify`` rates each signature change and says which calls it breaks.
A session's HTTP routes are compared with its base commit's on demand (``routes``), once the
base commit's index is ready, since a route's prefix can come from another file. Where
TypeScript contracts are on for the repository (``orch ts on``), the exported API of the
session's .ts files is compared too (``typescript``), and its changes join the functions'.

Which files belong to a session:
* a file named by one of its edit events is always its own;
* a file already changed when the session was first seen is not, until its size or time changes,
  unless the record already gave it to the session (a coordinator that restarted, a session
  back after it ended): what the record held stays as it was;
* in a folder that two live sessions share, other changes are marked not attributed rather
  than guessed.

A changed Python file whose content is not known right now is not compared: one that does not
parse while its base does (an edit half done), that cannot be read, or that has grown past
MAX_BLOB_BYTES. It stands as the session's copy last showed it whole, else as its base commit has
it, so none of its functions looks removed; the view lists it (``not_compared``) until it reads
whole again. A deletion is still one.

A branch read from elsewhere (a cloud agent's, ``remote``) has no folder: its view is read from
a shadow repository's objects, base commit to the branch's tip, through the same parses and
analysis (``branch_view``), and only when the tip or the base moved. The live map keeps these
branches in ``branches`` (``cloud`` is the older name for it).
"""
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field

from orch_engine.ingest import MAX_BLOB_BYTES, read_blobs
from orch_engine.track import diff, file_fingerprint

from orchestraty import adapters, gitio
from orchestraty.livemap import changes as git_changes
from orchestraty.livemap import routes as route_changes
from orchestraty.livemap import tsimports, typescript
from orchestraty.livemap.contracts import ANY, BREAKING, COMPATIBLE, NONE, REVIEW, classify, worst
from orchestraty.livemap.index import BaseIndexes, ParseCache, blob_id, view

PYTHON = (".py",)
NOT_PARSED, TOO_LARGE, NOT_READ = "does not parse", "too large", "not readable"   # why a file is not compared
READ_ONLY_TOOLS = ({"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "TodoWrite", "TodoRead",
                    "ExitPlanMode", "BashOutput", "ListMcpResourcesTool", "ReadMcpResourceTool"}
                   | adapters.READ_ONLY_TOOLS)                 # and the other agents' read-only tools


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
    breaks: dict = None         # breaking reason -> the calls it breaks (see contracts); None: not known
    language: str = "python"    # python | typescript (an export of a TypeScript module)
    heuristic: bool = False     # read from source, not from the compiler's declarations (TypeScript)

    def as_dict(self):
        return {"symbol": self.symbol, "kind": self.kind, "change": self.change, "contract": self.contract,
                "reasons": list(self.reasons), "file": self.file, "lines": list(self.lines),
                "moved_from": self.moved_from, "breaks": self.breaks, "language": self.language,
                "heuristic": self.heuristic}


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
    git_dir: str = ""                                  # a cloud branch: the shadow repository it is read from
    tip: str = ""                                      # and the branch's tip there
    ts_blobs: dict = field(default_factory=dict)       # changed .ts/.js path -> blob id on disk (orch ts on)
    not_compared: dict = field(default_factory=dict)   # changed .py path -> why its functions were not compared

    @property
    def source(self):
        """Where git reads this view's commits: its worktree, or the shadow of a cloud branch."""
        return self.git_dir or self.worktree

    def content(self):
        """What the changed code files hold: their blob ids (the code graphs are kept by these too)."""
        return (tuple(sorted(self.head_blobs.items(), key=lambda kv: kv[0])),
                tuple(sorted(self.ts_blobs.items(), key=lambda kv: kv[0])))

    def key(self):
        """What a refresh compares to tell whether anything changed. The content counts too: a call
        can change its form inside a function already changed, on the same lines."""
        return (tuple(sorted(self.files.items())), tuple(sorted(self.unattributed)),
                tuple(sorted((k, s.kind, s.change, s.contract, tuple(s.reasons), tuple(s.lines), repr(s.breaks))
                             for k, s in self.symbols.items())),
                self.content(), tuple(sorted(self.not_compared.items())))

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
        out[k] = SymbolChange(k, "removed", "", BREAKING, ["removed"], bs[k]["file"], bs[k]["lines"],
                              breaks={"removed": [ANY]})
    for m in d["symbols_moved"]:
        old, new = bs[m["from"]], hs[m["to"]]
        verdict, reasons, breaks = REVIEW, ["moved from " + old["file"]], {}
        if m["signature_changed"]:
            v, r, breaks = classify(new["kind"], old.get("signature"), new.get("signature"), old["kind"])
            verdict, reasons = worst(verdict, v), reasons + r
        out[m["to"]] = SymbolChange(m["to"], "moved", "signature" if m["signature_changed"] else "", verdict,
                                    reasons, new["file"], new["lines"], moved_from=m["from"], breaks=breaks)
    for k, change in d["symbol_changes"].items():
        if k in out:
            continue
        verdict, reasons, breaks = NONE, [], {}
        if change in ("signature", "both"):
            verdict, reasons, breaks = classify(hs[k]["kind"], bs[k].get("signature"), hs[k].get("signature"),
                                                bs[k]["kind"])
        out[k] = SymbolChange(k, "modified", change, verdict, reasons, hs[k]["file"], hs[k]["lines"], breaks=breaks)
    return out


def known(base, head, unread, before):
    """(head parses to compare, {path: why not compared}). A changed file whose head does not parse
    while its base does (an edit half done), or that could not be read (`unread`), stands as the
    session's copy last showed it whole (`before`: the previous view's head), else as its base
    commit has it, so nothing in it is taken as removed. A deletion stays one."""
    head, unread = dict(head), dict(unread)
    for path, pf in head.items():
        if pf is not None and pf.error and path not in unread and not (base.get(path) is not None and base[path].error):
            unread[path] = NOT_PARSED
    for path in unread:
        last = before.get(path)
        head[path] = last if last is not None and not last.error else base.get(path)
    return head, unread


def branch_view(git_dir, repo_key, sid, base, tip, before=None, cache=None, base_blobs=None):
    """A branch's view, from commits alone: the files and functions changed from `base` to `tip`
    in the repository `git_dir` (a shadow), through the same parses and analysis as a worktree's.
    `before` is the branch's last view: a file that does not parse now stands as it showed
    there. `cache` and `base_blobs` are the live map's parse cache and base blob ids, to share
    (fresh ones otherwise). A branch that cannot be read gives a view whose `error` says why."""
    cache = ParseCache() if cache is None else cache
    base_blobs = {} if base_blobs is None else base_blobs
    new = SessionView(sid, repo_key, "", base, git_dir=git_dir, tip=tip)
    try:
        new.files = git_changes.commit_diff(git_dir, base, tip)
        python = {p: s for p, s in new.files.items() if p.endswith(PYTHON)}
        base_pf, head, new.head_blobs, unread = _parses(cache, base_blobs, git_dir, base, python, at=tip)
        new.head, new.not_compared = known(base_pf, head, unread, before.head if before is not None else {})
        new.symbols = analyse(base_pf, new.head)
    except (gitio.GitError, gitio.UntrustedRepositoryError, RuntimeError) as exc:
        new.error = str(exc)[:300]
    return new


def _parses(cache, base_blobs, top, base, python, at=None):
    """({path: base ParsedFile or None}, {path: head ParsedFile or None}, {path: head blob id},
    {path: why its head is not known}) for the changed .py files: the head as it is on disk in
    `top`, or (with `at`) as it is at that commit of the repository `top`. `base_blobs` keeps
    each (commit, path)'s blob id once read."""
    head, head_blobs, unread = (_heads_at(cache, top, at, python) if at is not None
                                else _heads_on_disk(cache, top, python))
    wanted = [p for p, s in python.items() if s != "A" and (base, p) not in base_blobs]
    if wanted:
        found = git_changes.blob_ids(top, base, wanted)
        for p in wanted:
            base_blobs[(base, p)] = found.get(p)
    blobs = {p: base_blobs.get((base, p)) for p, s in python.items() if s != "A"}
    missing = sorted({b for p, b in blobs.items() if b and not cache.known(p, b)})
    data = read_blobs(top, missing) if missing else {}
    base_pf = {}
    for path in python:
        blob = blobs.get(path)
        if not blob:
            base_pf[path] = None
        elif blob in data:
            base_pf[path] = cache.get(path, data[blob], blob) if len(data[blob]) <= MAX_BLOB_BYTES else None
        else:
            base_pf[path] = cache.get(path, b"", blob)        # a cached parse: the key is all it needs
    return base_pf, head, head_blobs, unread


def _heads_on_disk(cache, top, python):
    head, head_blobs, unread = {}, {}, {}
    for path, status in python.items():
        head[path] = head_blobs[path] = None
        if status == "D":
            continue
        try:
            with open(os.path.join(top, *path.split("/")), "rb") as fh:
                data = fh.read(MAX_BLOB_BYTES + 1)
        except OSError:
            unread[path] = NOT_READ
            continue
        if len(data) <= MAX_BLOB_BYTES:
            head_blobs[path] = blob_id(data)
            head[path] = cache.get(path, data, head_blobs[path])
        else:
            unread[path] = TOO_LARGE
    return head, head_blobs, unread


def _heads_at(cache, top, commit, python):
    head_blobs, unread = {p: None for p in python}, {}
    head_blobs.update(git_changes.blob_ids(top, commit, [p for p, s in python.items() if s != "D"]))
    missing = sorted({b for p, b in head_blobs.items() if b and not cache.known(p, b)})
    data = read_blobs(top, missing) if missing else {}
    head = {}
    for path, blob in head_blobs.items():
        if blob in data and len(data[blob]) > MAX_BLOB_BYTES:
            blob = head_blobs[path] = None
            unread[path] = TOO_LARGE
        head[path] = cache.get(path, data.get(blob, b""), blob) if blob else None
    return head, head_blobs, unread


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
        self._routes = {}                              # session id -> (view key, RouteView)
        self._ts_uses = {}                             # session id -> (view key, tsimports.Imports)
        self.ts = typescript.TypeScript(state, self.log)
        self.branches = {}                             # session id -> (shadow git dir, tip): branches read from elsewhere

    @property
    def cloud(self):
        """The older name of `branches`."""
        return self.branches

    # sessions
    def track(self, session, repo_key, adopt_existing=False, recorded=None):
        """Start following a live session. Files already changed in its folder are not its own
        until they change again, unless adopt_existing (a plain-git worktree owns them all).
        `recorded` is what the record holds for a session seen before (a coordinator that
        restarted, a session back after it ended): ({path: status}, [unattributed path]). Its
        files stay its own and its unattributed ones stay unattributed."""
        sid = session["id"]
        if sid in self.sessions:
            return
        own, unattributed = recorded or ({}, [])
        self.sessions[sid] = session
        self.views[sid] = SessionView(sid, repo_key, session["worktree"], session["base_commit"])
        self.edits[sid].update(own)                    # its own, even in a folder it shares
        try:
            already = {} if adopt_existing else git_changes.changed_files(session["worktree"], session["base_commit"])
        except (gitio.GitError, gitio.UntrustedRepositoryError):
            already = {}
        known_before = set(own) | set(unattributed)
        self.dirty_at_start[sid] = {p: _stat(os.path.join(session["worktree"], *p.split("/"))) for p in already
                                    if p not in known_before}
        self.pending.add(sid)
        self.indexes.get(repo_key, session["base_commit"], session["worktree"])

    def track_branch(self, session, repo_key, git_dir, tip):
        """Start following a branch read from elsewhere (a cloud agent's): its commits are read
        from the shadow repository `git_dir`, from the session's base commit to `tip`."""
        sid = session["id"]
        self.sessions[sid] = session
        self.branches[sid] = (git_dir, tip)
        self.views[sid] = SessionView(sid, repo_key, "", session["base_commit"], git_dir=git_dir)
        self.pending.add(sid)
        self.indexes.get(repo_key, session["base_commit"], git_dir)

    def move_branch(self, sid, tip):
        """The branch moved: read it again."""
        if sid in self.branches:
            self.branches[sid] = (self.branches[sid][0], tip)
            self.pending.add(sid)

    def reread_branch(self, sid):
        """Read a branch again at its next refresh if the last read failed."""
        v = self.views.get(sid)
        if sid in self.branches and v is not None and v.error:
            v.tip = ""
            self.pending.add(sid)

    track_cloud, move_cloud, reread_cloud = track_branch, move_branch, reread_branch     # the older names

    def forget(self, sid):
        for table in (self.sessions, self.views, self.edits, self.dirty_at_start, self._graphs, self._routes,
                      self._ts_uses, self.branches):
            table.pop(sid, None)
        self.pending.discard(sid)
        self.ts.forget(sid)

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
            others = line.get("other_files") if isinstance(line.get("other_files"), list) else []
            paths = [p for p in (self.rel(session, f) for f in [line.get("file_path")] + others) if p]
            self.edits[sid].update(paths)                  # a patch can change several files
            if paths or line.get("tool_name") not in READ_ONLY_TOOLS:
                self.pending.add(sid)

    def mark_all(self):
        self.pending.update(self.sessions)

    # refresh
    def refresh_pending(self):
        """Refresh every session waiting for it; returns the ids whose view changed."""
        changed = set()
        self.pending.update(sid for sid in self.ts.take() if sid in self.sessions)   # a compiler run finished
        for sid in sorted(self.pending):
            if sid in self.sessions and self.refresh(sid):
                changed.add(sid)
        self.pending.clear()
        for key in self.indexes.take_fresh():          # callers can be found for these now
            changed.update(sid for sid, v in self.views.items() if (v.repo_key, v.base) == key)
        return changed

    def refresh(self, sid):
        if sid in self.branches:
            return self._refresh_branch(sid)
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
            base_pf, head, new.head_blobs, unread = self._parses(top, base,
                                                                 {p: s for p, s in mine.items() if p.endswith(PYTHON)})
            new.head, new.not_compared = known(base_pf, head, unread, old.head)
            new.symbols = analyse(base_pf, new.head)
        except (gitio.GitError, gitio.UntrustedRepositoryError, RuntimeError) as exc:
            new.error = str(exc)[:300]
            self.log.warning("session %s: no symbols: %s", sid[:8], exc)
        if self.ts.on(old.repo_key):
            self._typescript(sid, new, mine)
        self.views[sid] = new
        return new.key() != old.key()

    def _typescript(self, sid, view, mine):
        """Add the session's TypeScript contract changes to its view (orch ts on)."""
        code = {p: s for p, s in mine.items() if p.endswith(tsimports.CODE_EXTS)}
        for path, status in code.items():
            data = None
            if status != "D":
                try:
                    with open(os.path.join(view.worktree, *path.split("/")), "rb") as fh:
                        data = fh.read(MAX_BLOB_BYTES + 1)
                except OSError:
                    data = None
            view.ts_blobs[path] = blob_id(data) if data is not None and len(data) <= MAX_BLOB_BYTES else None
        ts_files = {p: s for p, s in code.items() if tsimports.is_typescript(p)}
        content = tuple(sorted((p, view.ts_blobs.get(p)) for p in ts_files))
        for c in self.ts.changes(sid, view.worktree, view.base, view.repo_key, ts_files, content):
            view.symbols[c["symbol"]] = SymbolChange(
                c["symbol"], c["kind"], "signature" if c["kind"] == "modified" else "", c["contract"], list(c["reasons"]),
                c["file"], [c["line"], c["line"]] if c["line"] else [], language="typescript", heuristic=c["heuristic"])

    def _refresh_branch(self, sid):
        """A branch's view (``branch_view``), read from the shadow repository. Nothing is read
        while neither its tip nor its base moved."""
        session, old = self.sessions[sid], self.views[sid]
        git_dir, tip = self.branches[sid]
        base = session["base_commit"]
        if old.tip == tip and old.base == base and not old.error:
            return False
        new = branch_view(git_dir, old.repo_key, sid, base, tip, before=old, cache=self.cache,
                          base_blobs=self._base_blobs)
        if new.error:
            self.log.warning("cloud branch %s: not read: %s", session.get("branch", sid), new.error)
        self.views[sid] = new
        return new.key() != old.key() or new.error != old.error

    def _parses(self, top, base, python, at=None):
        return _parses(self.cache, self._base_blobs, top, base, python, at)

    # code graphs
    def graph(self, sid):
        """The session's code graph (its changed .py files over the base commit's index), or
        None while that index is being built."""
        v = self.views.get(sid)
        if v is None:
            return None
        idx = self.indexes.get(v.repo_key, v.base, v.source)
        if idx is None:
            return None
        key = (tuple(sorted(v.head_blobs.items(), key=lambda kv: kv[0])),     # a file not compared stands in
               tuple(sorted(v.not_compared.items())))                       # for itself: not the same as gone
        cached = self._graphs.get(sid)
        if cached and cached[0] == key:
            return cached[1]
        g = view(idx, v.head)
        self._graphs[sid] = (key, g)
        return g

    def routes(self, sid):
        """The session's route changes (``livemap.routes.RouteView``) against its base commit, or
        None while that commit's index is being built."""
        v = self.views.get(sid)
        if v is None:
            return None
        idx = self.indexes.get(v.repo_key, v.base, v.source)
        if idx is None:
            return None
        key = (v.base, tuple(sorted(v.head_blobs.items(), key=lambda kv: kv[0])), tuple(sorted(v.not_compared.items())))
        cached = self._routes.get(sid)
        if cached and cached[0] == key:
            return cached[1]
        found = route_changes.compare(*route_changes.session_routes(idx, v.head))
        self._routes[sid] = (key, found)
        return found

    def ts_uses(self, sid):
        """Who imports what in the session's copy of the code (``tsimports.Imports``): its base
        commit's TypeScript and JavaScript files with its own changes put in. None while that
        commit's index is being built, for a cloud branch, or with TypeScript contracts off."""
        v = self.views.get(sid)
        if v is None or not v.worktree or not self.ts.on(v.repo_key):
            return None
        idx = self.indexes.get(v.repo_key, v.base, v.source)
        if idx is None:
            return None
        code = {p: s for p, s in v.files.items() if p.endswith(tsimports.CODE_EXTS)}
        key = (v.base, tuple(sorted(code.items())), tuple(sorted(v.ts_blobs.items(), key=lambda kv: kv[0])))
        cached = self._ts_uses.get(sid)
        if cached and cached[0] == key:
            return cached[1]
        texts = {pf.file: pf.text for pf in idx.parsed_files if pf.file.endswith(tsimports.CODE_EXTS)}
        for path, status in code.items():
            texts.pop(path, None)
            if status != "D":
                text = typescript._read(os.path.join(v.worktree, *path.split("/")))
                if text is not None:
                    texts[path] = text

        def read(path):
            return texts[path] if path in texts else typescript._read(os.path.join(v.worktree, *path.split("/")))
        imports = tsimports.Imports(texts, tsimports.Resolver(set(texts), read))
        self._ts_uses[sid] = (key, imports)
        return imports

    def base_graph(self, sid):
        """The code graph of the session's base commit, or None while it is being built."""
        v = self.views.get(sid)
        if v is None:
            return None
        idx = self.indexes.get(v.repo_key, v.base, v.source)
        return idx.graph if idx is not None else None

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
        return "ready" if self.indexes.get(v.repo_key, v.base, v.source) is not None else "building"
