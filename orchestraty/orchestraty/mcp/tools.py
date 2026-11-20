"""The MCP tools: what an agent can ask Orchestraty before it edits, what it can say it is
about to change, and what it can claim for itself.

Every answer comes from what the coordinator keeps, the record, opened read-only: who works
where and what each session changed, as of the coordinator's last pass. The server builds no
code graph of its own. Each tool returns structured JSON and a short text, says what the answer
covers and what it does not, and words what other sessions did with the notes' templates
(``delivery.notes``): identifiers only, cleaned, so no session's words reach another.
``status`` passes on the record's fields as ``orch status --json`` has them, except that other
sessions' declarations come without their task line and with their paths and functions
cleaned, as O7's findings are too.

The caller. An agent starts the server in its workspace folder (Claude Code's project folder,
``CLAUDE_PROJECT_DIR``, when it sets one). The caller's session is the live session working in
that folder's worktree, one with hooks before a plain-git one. When several work there, each
tool takes ``session``: the session's id, or its first characters as ``orch status`` shows them,
and only among the sessions working in the server's own worktree, so an agent cannot act in
another session's name or read its task line. A server started outside any worktree takes the
worktree from an absolute path or from ``session`` for the tools that read, returns no task line,
and refuses ``declare``, ``claim`` and ``release``. An agent without hooks has a session once its worktree has changes (a plain-git
session); before that, ``who_is_changing``, ``check_before_edit`` and ``status`` still answer.
"""
import calendar
import os
import time

from orchestraty import adapters, claims as claiming, config, declarations, feedback, gitio, sessionkinds, status
from orchestraty.coordinator.record import Record, repo_key
from orchestraty.delivery import notes as templates

NS = 1_000_000_000
LEVEL0_AGENT = sessionkinds.LEVEL0
CHANGED_KINDS = ("modified", "moved", "removed")
STRENGTH_ORDER = {"strong": 0, "medium": 1, "weak": 2}
SCOPE = ("Files come from git and the agents' edit events, functions and their contracts from Python files, as of the "
         "coordinator's last pass.")


class ToolError(Exception):
    """The tool cannot answer; the agent gets the reason as a tool result marked as an error."""


class ArgumentError(Exception):
    """The arguments do not fit the tool: a JSON-RPC error."""


def _iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _hhmm(iso):
    return time.strftime("%H:%M", time.localtime(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))))


def _ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def _real(path):
    return os.path.normcase(os.path.realpath(path))


def _same(a, b):
    return bool(a) and bool(b) and _real(a) == _real(b)


def _inside(path, top):
    p, t = _real(path), _real(top)
    return p == t or p.startswith(t.rstrip(os.sep) + os.sep)


def _ours(session, top):
    """Whether a session works in the worktree `top` or in a worktree inside it: Claude Code
    starts a desktop worktree session's MCP server in the main checkout, and puts its
    worktrees under that checkout's .claude/worktrees."""
    return bool(session["worktree"]) and _inside(session["worktree"], top)


def _worktree(folder):
    """worktree_info of the folder, or of the nearest folder above it that exists; None outside
    any worktree (or in one git will not read)."""
    folder = os.path.abspath(folder)
    while not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent
    try:
        return gitio.worktree_info(folder)
    except gitio.UntrustedRepositoryError:
        return None


def _file_of(symbol):
    return str(symbol or "").split("::", 1)[0]


def _key(f):
    """A finding's key as a tool shows it, each part cleaned: an O7 key holds what a session
    declared, an O5 key a route's path (shown only when it is a path, as notes show it)."""
    parts = f["key"].split("|")
    if f["rule"] == "O5" and len(parts) == 4:
        method, _, path = parts[1].partition(" ")
        return "|".join([parts[0], templates.clean_route(method, path)] + [templates.clean(x, 300) for x in parts[2:]])
    return "|".join(templates.clean(x, 300) for x in parts)


def _route(text):
    """'METHOD path' as a note shows it, or "" for none."""
    method, _, path = str(text or "").partition(" ")
    return templates.clean_route(method, path) if text else ""


def _key_text(key):
    """A finding's key, given as text, as a tool shows it (see _key)."""
    return _key({"key": str(key), "rule": str(key).split("|", 1)[0]})


def _folder(path):
    """A folder another session works in, cleaned as notes clean names, its separators kept."""
    return templates.clean(str(path or "").replace("\\", "/"), 300)


def _first_words(text):
    """Of a message that may end with another tool's own words (git's, a compiler's): ours only."""
    return templates.printable(str(text or "").split(": ", 1)[0], 120)


def _names(value, key=""):
    """Every name in a value from the record (files, functions, branches, ids) cleaned as notes
    clean them: a contract change's reasons as reasons, a folder with its separators."""
    if isinstance(value, dict):
        return {templates.clean(k, 300): _names(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_names(v, key) for v in value]
    if not isinstance(value, str):
        return value
    if key == "reasons":
        return templates.clean_reason(value)
    if key in ("worktree", "cwd"):
        return _folder(value)
    return templates.clean(value, 300)


def _session_view(s, own):
    """One session row of the status tool: names cleaned; another session's declarations
    without their task line."""
    declared = s.get("declared", [])
    out = _names({k: v for k, v in s.items() if k not in ("declared", "changes", "hears", "note")})
    note = str(s.get("note") or "")                 # ours ("plain git", "timed out"), but for an agent's end reason
    out["hears"], out["note"] = s.get("hears", []), (
        "ended: " + templates.clean(note[len("ended: "):], 60) if note.startswith("ended: ") else templates.printable(note))
    changes = s.get("changes") or {}
    out["changes"] = {"files": _names(changes.get("files", {})), "unattributed": _names(changes.get("unattributed", [])),
                      "symbols": _names(changes.get("symbols", [])),
                      "not_compared": {templates.clean(p, 300): templates.printable(why, 120)
                                       for p, why in (changes.get("not_compared") or {}).items()}}
    out["declared"] = [dict({"paths": [templates.clean(p, 200) for p in d["paths"]],
                             "symbols": [templates.clean(x, 200) for x in d["symbols"]],
                             "since": d["since"], "until": d["until"]},
                            **({"task": templates.printable(d.get("task", ""))} if own else {})) for d in declared]
    return out


def _agent_view(repo, mine):
    """The repository as the status tool shows it to an agent: every name another session
    controls (branches, folders, files, functions, claims, declared paths, route paths, finding
    keys) cleaned as notes clean them, and of what another program printed (the merged code's
    output, git's, a compiler's) only our own words and the names it gives."""
    out = dict(repo)
    out["sessions"] = [_session_view(s, mine(s)) for s in repo["sessions"]]
    overlaps = []
    for o in repo["overlaps"]:
        d = o.get("detail") or {}
        if o["rule"] == "O5":                               # a route's path is a string a session wrote
            changes = {templates.clean(sid, 200): dict(_names({k: v for k, v in c.items() if k not in ("to", "from")}),
                                                       to=_route(c.get("to")), **{"from": _route(c.get("from"))})
                       for sid, c in (d.get("changes") or {}).items()}
            detail = dict(_names({k: v for k, v in d.items() if k not in ("changes", "path", "method")}),
                          method=templates.clean(d.get("method", ""), 12), changes=changes,
                          path=templates.clean_route(d.get("method", ""), d.get("path", "")).split(" ", 1)[1])
        else:
            detail = _names(d)
        overlaps.append(dict(_names({k: v for k, v in o.items() if k not in ("key", "detail")}),
                             key=_key(o), detail=detail))
    out["overlaps"] = overlaps
    out["merge_order"] = [dict(_names({k: v for k, v in m.items() if k != "line"}), line=templates.printable(m["line"]))
                          for m in repo.get("merge_order") or []]
    together = dict(repo.get("together") or {})
    together["results"] = [dict(r, sessions=_names(r.get("sessions", [])),
                                findings=[_key_text(k) for k in r.get("findings", [])],
                                alone={templates.clean(k, 200): v for k, v in (r.get("alone") or {}).items()},
                                conflicts=_names(r.get("conflicts", [])),
                                first_failing_line=" at ".join(x for x in templates.failure(r.get("first_failing_line"))
                                                               if x),
                                error=_first_words(r.get("error")))
                           for r in together.get("results", [])]
    out["together"] = together
    typescript = dict(repo.get("typescript") or {})
    typescript["runs"] = [dict(run, session=templates.clean(run.get("session", ""), 200), why=_first_words(run.get("why")),
                               not_compared=_names(run.get("not_compared", [])),
                               **{side: dict(run[side], error=templates.clean(_first_words(run[side].get("error")), 120))
                                  for side in ("head", "base") if isinstance(run.get(side), dict)})
                          for run in typescript.get("runs", [])]
    out["typescript"] = typescript
    cloud = dict(repo.get("cloud") or {})
    if isinstance(cloud.get("fetch"), dict):
        cloud["fetch"] = dict(cloud["fetch"], error=_first_words(cloud["fetch"].get("error")))
    cloud["branches"] = [_session_view(s, False) for s in cloud.get("branches", [])]
    out["cloud"] = cloud
    if isinstance(repo.get("team"), dict):
        out["team"] = _team_view(repo["team"])
    return out


def _team_view(t):
    """The team block as an agent sees it: teammates' handles, branches and tickets cleaned as
    notes clean them, never a task line, a device or a label, and of the messages only our own
    words."""
    out = dict(t)
    out["policy"] = dict(t["policy"], why=_first_words(t["policy"].get("why")), fix=_first_words(t["policy"].get("fix")))
    if isinstance(t.get("me"), dict):
        out["me"] = dict(t["me"], person=templates.clean(t["me"]["person"], 60), label="")
    if isinstance(t.get("sent"), dict):
        out["sent"] = dict(t["sent"], error=_first_words(t["sent"].get("error")))
    out["writers"] = [dict(person=templates.clean(w["person"], 60), verdict=templates.clean(w.get("verdict", ""), 10),
                           device="", label="", written=templates.clean(w.get("written", ""), 25),
                           why=templates.clean(w.get("why", ""), 80),     # our own closed set of reasons
                           entries=[{"agent": templates.clean(e.get("agent", ""), 20),
                                     "state": templates.clean(e.get("state", ""), 10),
                                     "branch": templates.clean(e.get("branch", ""), 200), "pushed": e.get("pushed") is True,
                                     "ticket": templates.clean(e.get("ticket", ""), 20)}
                                    for e in w.get("entries", []) if isinstance(e, dict)])
                      for w in t.get("writers", []) if isinstance(w, dict)]
    if isinstance(t.get("fetch"), dict):
        out["fetch"] = dict(t["fetch"], error=_first_words(t["fetch"].get("error")))
    out["sessions"] = [_team_session_view(s) for s in t.get("sessions", []) if isinstance(s, dict)]
    return out


def _team_session_view(s):
    """A teammate's branch as an agent sees it: its names cleaned as notes clean them (a file a
    teammate committed is named by them), never a task line."""
    tm = s.get("team") if isinstance(s.get("team"), dict) else {}
    changes = s.get("changes") if isinstance(s.get("changes"), dict) else {}
    files = changes.get("files") if isinstance(changes.get("files"), dict) else {}
    return {"id": templates.clean(s.get("id", ""), 40), "agent": "team", "branch": templates.clean(s.get("branch", ""), 200),
            "base_commit": templates.clean(s.get("base_commit", ""), 40), "state": templates.clean(s.get("state", ""), 10),
            "last_event": templates.clean(s.get("last_event", ""), 25),
            "changes": {"files": {templates.clean(p, 200): templates.clean(v, 2) for p, v in sorted(files.items())},
                        "unattributed": [], "not_compared": {},
                        "symbols": [{"symbol": templates.clean(x.get("symbol", ""), 200),
                                     "kind": templates.clean(x.get("kind", ""), 10),
                                     "contract": templates.clean(x.get("contract", ""), 10),
                                     "reasons": [templates.clean_reason(r) for r in x.get("reasons") or []][:3]}
                                    for x in changes.get("symbols") or [] if isinstance(x, dict)]},
            "team": {"people": [templates.clean(p, 60) for p in tm.get("people", [])], "own": tm.get("own") is True,
                     "agents": [templates.clean(a, 20) for a in tm.get("agents", [])],
                     "ticket": templates.clean(tm.get("ticket", ""), 20), "merged": templates.clean(tm.get("merged", ""), 12),
                     "last_commit": templates.clean(tm.get("last_commit", ""), 25),
                     "as_of": templates.clean(tm.get("as_of", ""), 25)}}


def _file_done(status, symbol):
    """What a session did to the file a row of who_is_changing is about; for a function, said
    of the file (the function's own change, when it has one, is said instead)."""
    if status == "not attributed":
        return "changed in a folder two sessions share, not attributed"
    if symbol:
        return {"A": "added the file", "D": "deleted the file"}.get(status, "changed other parts of the file")
    return {"A": "added it", "D": "deleted it"}.get(status, "changed it")


def _bare(text):
    """A note's text as a tool shows it: without the "Orchestraty: " every note starts with."""
    return text[len("Orchestraty: "):] if text.startswith("Orchestraty: ") else text


def _by_id(sessions, ref):
    """The sessions whose id is `ref`, else those whose id starts with it."""
    return [s for s in sessions if s["id"] == ref] or [s for s in sessions if s["id"].startswith(ref)]


class Workspace:
    """What one tool call reads. The worktree the call is about: the one of the session it
    names, else the one holding the caller's folder, else (a server started outside any
    worktree) the one holding the absolute path it names. From the record, read-only: the
    sessions, the open findings of that worktree's repository, what its live sessions changed,
    and the declarations that hold."""

    def __init__(self, state, folder, now=None):
        self.state = state
        self.folder = os.path.abspath(folder)
        self.now = time.time() if now is None else now
        self.top, self.repo_id, self.common_key, self.head = None, None, None, ""
        self.me, self._several, self.vouched = None, [], False
        self.sessions, self.findings, self.changes, self.declared, self.claims = {}, [], {}, {}, []
        self.team_rows, self.team_claims, self._named_sessions = {}, [], None
        self.record = Record(state.record, readonly=True) if os.path.exists(state.record) else None
        if self.record is not None:
            self.sessions = {s["id"]: s for s in self.record.sessions()}
            for d in self.record.declarations(active_at=int(self.now * NS)):
                self.declared.setdefault(d["session_id"], []).append(d)
            self.claims = self.record.claims(active_at=int(self.now * NS))
            self.team_rows = {r["session_id"]: r for r in self.record.team_sessions()}
            self.team_claims = self.record.team_planned(active_at=int(self.now * NS))

    def close(self):
        if self.record is not None:
            self.record.close()
            self.record = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def locate(self, session=None, path=None):
        """Settle the worktree, the repository and the caller's session for this call."""
        here = _worktree(self.folder)
        self.vouched = here is not None                 # the server's own folder tells whose sessions call
        named = self._named(session, here) if session else None
        info = here
        if named is not None and named["worktree"] and (info is None or not _same(info["top"], named["worktree"])):
            info = _worktree(named["worktree"]) or info  # a session in a worktree inside the server's own
        if info is None and named is None and isinstance(path, str) and os.path.isabs(path):
            info = _worktree(os.path.dirname(path))
        repos = {r["key"]: r["id"] for r in self.record.repos()} if self.record is not None else {}
        if info is not None:
            self.top, self.head, self.common_key = info["top"], info["head"], repo_key(info["common_dir"])
            self.repo_id = repos.get(self.common_key)
        if named is not None:
            self.repo_id = named["repo_id"]
            self.top = info["top"] if info is not None and _same(info["top"], named["worktree"]) else named["worktree"]
            self.common_key = next((k for k, v in repos.items() if v == named["repo_id"]), self.common_key)
        if self.repo_id is not None:
            self.findings = self.record.findings(repo_id=self.repo_id)
            for s in self.live():
                self.changes[s["id"]] = self.record.changes(s["id"])
        if named is not None:
            self.me = named
        elif self.top is not None:
            here = [s for s in self.live() if _same(s["worktree"], self.top)]
            inside = [s for s in self.live() if _ours(s, self.top) and not _same(s["worktree"], self.top)]
            # With sessions in worktrees inside this one, any of them may be calling: never guess.
            hooked = [s for s in here + inside if s["agent"] != LEVEL0_AGENT]
            if len(hooked) > 1:
                self._several = hooked
            elif hooked or here:
                self.me = (hooked or here)[0]
            if self.me is not None and self.me["worktree"] and not _same(self.me["worktree"], self.top):
                moved = _worktree(self.me["worktree"])      # the one session there works in a worktree inside it
                if moved is not None:
                    self.top, self.head = moved["top"], moved["head"]

    def _named(self, ref, here):
        """The live session `ref` names: its id, the start of it, or the absolute path of the
        folder it works in. A server in a worktree answers for the sessions working there or in
        a worktree inside it, and no other; outside any worktree, for the one named."""
        live = [s for s in self.sessions.values() if s["state"] == "live"]
        ours = live if here is None else [s for s in live if _ours(s, here["top"])]
        where = f"{here['top']} or in a worktree inside it" if here is not None else ""
        if os.path.isabs(ref):
            info = _worktree(ref)
            if info is None:
                raise ToolError(f"{templates.clean(ref, 200)} is not inside a git worktree.")
            found = [s for s in live if _same(s["worktree"], info["top"])]
            if found and not [s for s in found if s in ours]:
                raise ToolError(f"{templates.clean(ref, 200)} is not {where}: this server answers only for the "
                                "sessions working there.")
            found = [s for s in found if s in ours]
            found = [s for s in found if s["agent"] != LEVEL0_AGENT] or found
            if len(found) == 1:
                return found[0]
            if found:
                raise ToolError(f"{len(found)} live sessions work in {templates.clean(info['top'], 200)}: pass your "
                                "session's id instead (orch status lists them).")
            raise ToolError(f"No live session works in {templates.clean(info['top'], 200)}.")
        found = _by_id(ours, ref)
        if len(found) == 1 and found[0]["agent"] == sessionkinds.TEAM:
            raise ToolError(f"{templates.clean(ref, 60)} is a teammate's branch, followed from the remote: its work is "
                            "on another machine, so no tool answers in its name.")
        if len(found) == 1:
            return found[0]
        if found:
            raise ToolError(f"{templates.clean(ref, 60)} is the start of {len(found)} live sessions' ids: give more of it.")
        if _by_id(live, ref):
            raise ToolError(f"The session {templates.clean(ref, 60)} does not work in {where}: this server answers "
                            "only for the sessions working there.")
        raise ToolError(f"No live session has the id {templates.clean(ref, 60)} (orch status lists them).")

    def named(self, s):
        """A session as a note names it: a teammate's branch with who works on it."""
        if s["agent"] != sessionkinds.TEAM:
            return s
        row = self.team_rows.get(s["id"]) or {}
        return dict(s, people=row.get("people") or [], own=bool(row.get("own")))

    def named_sessions(self):
        if self._named_sessions is None:
            self._named_sessions = {sid: self.named(s) for sid, s in self.sessions.items()}
        return self._named_sessions

    def held(self, s, path, symbol=None):
        """A teammate's claims that hold now on the file at `path` (asked about a function: on that
        function, or on its file or a folder above it)."""
        out = []
        for c in self.team_claims:
            if c["session_id"] != s["id"]:
                continue
            if c["kind"] == "symbol" and (c["target"] == symbol if symbol else c["file"] == path):
                out.append(c)
            elif c["kind"] != "symbol" and claiming.covers(c, path):
                out.append(c)
        return out

    def live(self):
        """The live sessions of this repository, oldest first."""
        if self.repo_id is None:
            return []
        return sorted((s for s in self.sessions.values() if s["state"] == "live" and s["repo_id"] == self.repo_id),
                      key=lambda s: (s["started"], s["id"]))

    def caller(self, required=True, strict=True):
        """The caller's live session. When several work in the caller's worktree: an error when
        `strict`, else None. When none does: an error when `required`, else None."""
        if self.me is not None:
            return self.me
        if self._several:
            if not strict:
                return None
            raise ToolError(f"{len(self._several)} live sessions work in {self.top} or in worktrees inside it, so "
                            "this server cannot tell which one calls: pass `session`, the absolute path of the folder "
                            "you work in (or your session's id, one of "
                            + ", ".join(templates.clean(s["id"], 60) for s in self._several) + ").")
        if required:
            raise ToolError(f"No live session works in {self.top or self.folder}. A session with hooks shows from its "
                            "first event; an agent without hooks shows (as plain git) once its worktree has changes. "
                            "If you work in a worktree inside it, pass `session`: the absolute path of your folder.")
        return None

    def writer(self):
        """The caller's session, for a tool that changes something in its name: only a server
        started in that session's worktree can tell who calls."""
        if not self.vouched:
            raise ToolError(f"{self.folder} is not inside a git worktree, so this server cannot tell which session "
                            "calls it: declare, claim and release need it started in your workspace folder "
                            "(orch mcp --folder <your worktree>).")
        return self.caller()

    def mine(self, s):
        """Whether `s` is the caller's session, as the server's own folder tells it."""
        return self.vouched and self.me is not None and s["id"] == self.me["id"]

    def path(self, path, planned=False):
        """The posix path in the repository that `path` names: absolute, or relative to the
        caller's folder (or to its worktree's top, when the file is not found there).
        `planned`, for declare: a plain path (no space or control character) of a file or
        folder there, or of a new one in a folder there."""
        if self.top is None:
            raise ToolError(f"{self.folder} is not inside a git worktree: pass the file's absolute path, or start the "
                            "server in your workspace folder.")
        top = self.top
        if os.path.isabs(path):
            full = path
            holders = [t for t in [top] + [s["worktree"] for s in self.live() if s["worktree"]] if _inside(full, t)]
            if not holders:
                info = _worktree(os.path.dirname(full))
                if info is None or repo_key(info["common_dir"]) != self.common_key:
                    raise ToolError(f"{path} is not inside a worktree of this repository.")
                holders = [info["top"]]
            top = max(holders, key=lambda t: len(_real(t)))
        else:
            base = self.folder if _inside(self.folder, top) else top
            full = os.path.join(base, path)
            if not os.path.exists(full):
                full = os.path.join(top, path)
            if not _inside(full, top):
                raise ToolError(f"{path} is not inside this repository.")
        rel = os.path.relpath(os.path.realpath(full), os.path.realpath(top)).replace(os.sep, "/")
        if rel in (".", "") or rel == ".." or rel.startswith("../"):
            raise ToolError(f"{templates.clean(path, 200)} names no file in this repository.")
        if planned and not declarations.is_path(rel):
            raise ToolError(f"{templates.clean(path, 200)}: a declared path has no spaces or control characters; "
                            "declare its folder instead.")
        if planned and not (os.path.exists(full) or os.path.isdir(os.path.dirname(full))):
            new = full
            while not os.path.isdir(os.path.dirname(new)) and os.path.dirname(new) != new:
                new = os.path.dirname(new)
            first = os.path.relpath(new, top).replace(os.sep, "/")
            raise ToolError(f"{templates.clean(path, 200)}: neither it nor its folder is in this worktree. Declare a "
                            "file or folder that is there, or the new folder at the top of it, "
                            f"{templates.clean(first, 200)}.")
        return rel

    def symbol(self, symbol, path, planned=False):
        """'name' in `path` (a function, Class.method), or a full 'path::name' id, as the record
        names symbols. `planned`: as for path."""
        file, sep, name = symbol.strip().partition("::")
        file, name = (file.strip(), name.strip()) if sep else ("", file)
        if not name or (planned and not declarations.is_name(name)) or (sep and not file):
            raise ToolError(f'"{templates.clean(symbol.strip(), 200)}" is not a function or class name: give its name '
                            "(Class.method) or its full id (path::name).")
        return f"{self.path(file, planned) if sep else path}::{name}"

    def coordinator(self):
        return "running" if feedback.coordinator_running(self.state, self.now) else "not running: this is what it last saw"

    def common_dir(self, session):
        """The git common folder of a session's repository, as the record keeps it."""
        return next((r["common_dir"] for r in self.record.repos() if r["id"] == session["repo_id"]), "")


def _session_facts(ws, s, me):
    out = {"session": s["id"], "you": me is not None and s["id"] == me["id"], "agent": s["agent"],
           "worktree": templates.clean(os.path.basename(s["worktree"]) or "?", 60),
           "branch": templates.clean(s["branch"], 60) if s["branch"] else "",
           "last_event": _iso(s["last_seen"] / NS), "seconds_since_last_event": max(0, int(ws.now - s["last_seen"] / NS))}
    if s["agent"] == sessionkinds.TEAM:            # a teammate's branch: who works on it, by their cleaned handles
        row = ws.team_rows.get(s["id"]) or {}
        out.update(worktree="", people=[templates.clean(p, 60) for p in row.get("people") or []], teammate=True,
                   evidence=templates.clean(row.get("evidence") or "commits", 10))
    return out


def _change(c):
    return {"symbol": templates.clean(c["symbol"], 200), "kind": c["kind"], "change": c["change"],
            "contract": c["contract"], "reasons": [templates.clean_reason(r) for r in c.get("reasons", [])]}


def _declared(d, mine):
    out = {"paths": [templates.clean(p, 200) for p in d["paths"]],
           "symbols": [templates.clean(x, 200) for x in d["symbols"]], "until": _iso(d["expires"] / NS)}
    if mine:
        out["task"] = d["task"]                      # the caller's own words, back to the caller only
    return out


def _tail(ws, notes=()):
    """What an answer covers; the coverage line only once, after notes that end with it."""
    coverage = "" if any(n.endswith(templates.COVERAGE) for n in notes) else f" {templates.COVERAGE}"
    return f"{SCOPE}{coverage} The coordinator is {ws.coordinator()}."


# ------------------------------------------------------------------------------- the tools
def who_is_changing(ws, args):
    path = ws.path(args["path"])
    symbol = ws.symbol(args["symbol"], path) if args.get("symbol") else None
    path = _file_of(symbol) if symbol else path                     # a full id names its own file
    me = ws.caller(required=False, strict=False)
    rows, others_in_file = [], 0
    for s in ws.live():
        files, unattributed, symbols = ws.changes.get(s["id"], ({}, [], []))
        in_file = [c for c in symbols if path in (_file_of(c["symbol"]), _file_of(c.get("moved_from")))]
        named = [c for c in symbols if symbol in (c["symbol"], c.get("moved_from"))] if symbol else []
        declared = [d for d in ws.declared.get(s["id"], []) if path in d["paths"] or (symbol and symbol in d["symbols"])]
        held = ws.held(s, path, symbol) if s["agent"] == sessionkinds.TEAM else []
        changes_file = path in files or path in unattributed
        if symbol and not named and not declared and not held:
            others_in_file += changes_file and (me is None or s["id"] != me["id"])
            continue
        if not symbol and not changes_file and not declared and not held:
            continue
        row = _session_facts(ws, s, me)
        row.update(file=files.get(path) or ("not attributed" if path in unattributed else ""),
                   changes=[_change(c) for c in (named if symbol else in_file)],
                   declared=[_declared(d, ws.mine(s)) for d in declared])
        if s["agent"] == sessionkinds.TEAM:          # their shared claims: they only inform
            row["claims"] = [{"kind": c["kind"], "target": templates.clean(c["target"], 200),
                              "file": templates.clean(c["file"], 200), "until": _iso(c["until"] / NS)} for c in held]
        rows.append(row)
    target = (f"{templates.clean(symbol.split('::', 1)[1], 120)} in {templates.clean(path, 200)}" if symbol
              else templates.clean(path, 200))
    lines = [f"{target}: " + (f"{len(rows)} live session(s)." if rows else
                              "no live session is changing it or has declared it.")]
    for r in rows:
        what = []
        if r["file"] and not (symbol and r["changes"]):
            what.append(_file_done(r["file"], symbol))
        for c in r["changes"]:
            contract = f", contract {c['contract']}" if c["contract"] not in ("none", "") else ""
            reasons = f" ({'; '.join(c['reasons'][:3])})" if c["reasons"] else ""
            what.append(f"{c['symbol'].split('::', 1)[-1]} {c['kind']}{contract}{reasons}")
        for d in r["declared"]:
            what.append("declared it until " + _hhmm(d["until"]))
        teammate = templates.describe(ws.named(ws.sessions[r["session"]])).split(" (", 1)[0]
        for c in r.get("claims", []):
            what.append(f"claimed by {teammate} until {_hhmm(c['until'])} (informs only)")
        who = "you" if r["you"] else templates.describe(ws.named(ws.sessions[r["session"]]))
        lines.append(f"- {who}, last event {_ago(r['seconds_since_last_event'])}: {'; '.join(what or ['changing it'])}.")
    if symbol and others_in_file:
        lines.append(f"{others_in_file} other live session(s) change other parts of {templates.clean(path, 200)}.")
    lines.append(_tail(ws))
    data = {"path": path, "symbol": symbol, "sessions": rows, "others_in_file": others_in_file,
            "coverage": templates.COVERAGE, "scope": SCOPE, "coordinator": ws.coordinator()}
    return data, "\n".join(lines)


def check_before_edit(ws, args):
    path = ws.path(args["path"])
    me = ws.caller(required=False)
    items = []
    for f in sorted(ws.findings, key=lambda f: (f["rule"], f["key"])):
        changer = ws.sessions.get(f["changer"])
        if changer is None or f["rule"] not in ("O3", "O4", "O6"):
            continue
        if me is not None and f["rule"] != "O6" and f["receiver"] == me["id"] and f["file"] == path:
            elsewhere = not _same(changer["worktree"], me["worktree"])
            items.append(("uses_broken", f, templates.for_user(f, ws.named(changer), before_edit=True,
                                                               elsewhere=elsewhere)))
        elif me is None and f["rule"] == "O6":
            # No session works here yet: the callers in the base commit that no session has updated.
            d = f["detail"]
            here = [c for c in d.get("left", []) if c.get("file") == path]
            if here:
                like = {"rule": "O4" if d.get("kind") == "removed" else "O3", "symbol": f["symbol"], "file": path,
                        "detail": {"uses": [{"symbol": c["symbol"], "lines": c.get("lines", [])} for c in here],
                                   "reasons": d.get("reasons", [])}}
                items.append(("uses_broken", f, templates.for_user(like, changer, before_edit=True)))
    others_changed_file = 0
    for s in ws.live():
        if me is not None and (s["id"] == me["id"] or _same(s["worktree"], me["worktree"])):
            continue                                            # one folder: what each changed cannot be told apart
        files, unattributed, symbols = ws.changes.get(s["id"], ({}, [], []))
        theirs = [c for c in symbols if _file_of(c["symbol"]) == path and c["kind"] in CHANGED_KINDS]
        for c in theirs:
            items.append(("also_changing", {"symbol": c["symbol"], "session": s["id"]},
                          templates.for_both({"symbol": c["symbol"]}, ws.named(s))))
        others_changed_file += bool(not theirs and (path in files or path in unattributed))
    if me is not None:
        for f in ws.findings:
            if f["rule"] == "W1" and me["id"] in f["sessions"]:
                other = ws.sessions.get(next((x for x in f["sessions"] if x != me["id"]), ""))
                if other:
                    items.append(("same_folder", f, templates.same_folder(other)))
    for c in ws.claims:
        claimer = ws.sessions.get(c["session_id"])
        if (claimer is not None and claimer["repo_id"] == ws.repo_id and (me is None or c["session_id"] != me["id"])
                and claiming.covers(c, path)):
            # a hard claim: the caller's edit waits for its user only where its own hook asks
            ask = (me is not None and adapters.asks(me["agent"])) if c["hard"] else None
            items.append(("claimed", c, templates.claimed(c, claimer, ask=ask)))
    for c in ws.team_claims:                                    # a teammate's: it only informs, whatever its kind
        teammate = ws.sessions.get(c["session_id"])
        if (teammate is not None and teammate["state"] == "live" and teammate["repo_id"] == ws.repo_id
                and claiming.covers(c, path)):
            shared = (ws.team_rows.get(c["session_id"]) or {}).get("as_of") or None
            items.append(("team_claimed", c, templates.team_claimed(c, ws.named(teammate), shared_at=shared)))
    data = {"path": path, "session": me["id"] if me else None, "uses_broken": [], "also_changing": [],
            "same_folder": [], "claimed": [], "others_changed_file": others_changed_file,
            "notes": [t for _, _, t in items], "coverage": templates.COVERAGE, "scope": SCOPE,
            "coordinator": ws.coordinator()}
    for kind, f, text in items:
        if kind == "team_claimed":
            data["claimed"].append({"claim": None, "teammate": True, "hard": False, "kind": f["kind"],
                                    "target": templates.clean(f["target"], 200), "session": f["session_id"],
                                    "until": _iso(f["until"] / NS), "note": text})
        elif kind == "claimed":
            data[kind].append({"claim": f["id"], "kind": f["kind"], "target": templates.clean(f["target"], 200),
                               "hard": bool(f["hard"]), "session": f["session_id"], "until": _iso(f["expires"] / NS),
                               "note": text})
        elif kind == "also_changing":
            data[kind].append({"symbol": templates.clean(f["symbol"], 200), "session": f["session"], "note": text})
        else:
            data[kind].append({"finding": _key(f), "rule": f["rule"], "strength": f["strength"],
                               "symbol": templates.clean(f["symbol"], 200), "latent": bool(f["detail"].get("latent")),
                               "note": text})
    if items:
        text = f"Before you edit {templates.clean(path, 200)}:\n" + "\n".join("- " + _bare(t) for _, _, t in items)
    else:
        text = (f"Nothing known concerns {templates.clean(path, 200)}: no other live session broke or removed a function "
                "it uses, is changing a function in it, or has claimed it.")
    if others_changed_file:
        text += (f"\n{others_changed_file} other live session(s) also changed this file outside any function; "
                 "who_is_changing lists them.")
    text += "\n" + _tail(ws, data["notes"])
    if me is None:
        data["base_commit"] = ws.head
        text += (" No session works in this worktree yet, so the uses are those of the other sessions' base commit; "
                 f"this worktree is at {ws.head[:12] or 'no commit'}.")
        if any(s["agent"] == sessionkinds.TEAM for s in ws.live()):
            text += (" A teammate's branch is compared with a session's own copy, so its changes show here once a "
                     "session works in this worktree.")
    return data, text


def _overlap_note(ws, f, me):
    """What a finding tells the caller, in the notes' words."""
    return templates.about(f, me, ws.named_sessions())


def my_overlaps(ws, args):
    me = ws.caller()
    mine = [f for f in ws.findings if me["id"] in f["sessions"] or me["id"] in (f["changer"], f["receiver"])]
    # A latent finding is for the session whose file uses the change, before it edits that file.
    mine = [f for f in mine if not f["detail"].get("latent") or f["receiver"] == me["id"]]
    mine.sort(key=lambda f: (STRENGTH_ORDER.get(f["strength"], 9), f["rule"], f["key"]))
    rows = []
    for f in mine:
        others = [ws.sessions[x] for x in f["sessions"] if x != me["id"] and x in ws.sessions]
        rows.append({"finding": _key(f), "rule": f["rule"], "strength": f["strength"],
                     "symbol": templates.clean(f["symbol"], 200), "file": templates.clean(f["file"], 200),
                     "latent": bool(f["detail"].get("latent")),
                     "with": [_session_facts(ws, s, me) for s in others], "note": _bare(_overlap_note(ws, f, me))})
    if rows:
        text = f"{len(rows)} overlap(s) involve this session:\n" + "\n".join(
            f"- {r['strength']} {r['rule']}{' (latent)' if r['latent'] else ''}: {r['note']}" for r in rows)
    else:
        text = "No overlap involves this session."
    text += "\n" + _tail(ws, [r["note"] for r in rows])
    data = {"session": me["id"], "overlaps": rows, "coverage": templates.COVERAGE, "scope": SCOPE,
            "coordinator": ws.coordinator()}
    return data, text


def status_tool(ws, args):
    me = ws.caller(required=False, strict=False)
    if ws.common_key is None:
        raise ToolError(f"{ws.folder} is not inside a git worktree: pass `session`, or start the server in your "
                        "workspace folder.")
    data = status.collect(ws.state, now=ws.now)
    repo = next((r for r in data["repositories"] if repo_key(r["common_dir"]) == ws.common_key), None)
    if repo is None:
        raise ToolError("This repository is not watched: run orch watch (or orch init) in it.")
    repo = _agent_view(repo, ws.mine)                       # no session's words reach this agent's context
    out = {"schema": data["schema"], "coordinator": data["coordinator"], "repository": repo,
           "you": me["id"] if me else None}
    return out, status.render({"coordinator": data["coordinator"], "repositories": [repo]}).rstrip("\n")


def declare(ws, args):
    me = ws.writer()
    paths = list(dict.fromkeys(ws.path(p, planned=True) for p in args["paths"]))
    names = [x.strip() for x in args.get("symbols", []) if x.strip()]
    symbols = list(dict.fromkeys(ws.symbol(x, paths[0], planned=True) if "::" in x or len(paths) == 1
                                 else _bare_name(x) for x in names))
    if any(len(p) > declarations.MAX_ITEM for p in paths + symbols):
        raise ToolError(f"A path or function name is longer than {declarations.MAX_ITEM} characters.")
    task = declarations.one_line(args.get("task") or "")
    declarations.append(ws.state, me["id"], paths, symbols, task, now=ws.now)
    minutes = config.load(ws.state)["declare_minutes"]
    running = feedback.coordinator_running(ws.state, ws.now)
    data = {"session": me["id"], "paths": paths, "symbols": symbols, "task": task, "holds_minutes": minutes,
            "until": _iso(ws.now + minutes * 60), "recorded": "now" if running else "when the coordinator next runs"}
    text = (f"Declared for this session: {', '.join(templates.clean(p, 200) for p in paths)}"
            + (f"; functions: {', '.join(templates.clean(x.split('::', 1)[-1], 120) for x in symbols)}" if symbols else "")
            + f". It holds for {minutes} minutes. "
            + ("The coordinator records it now." if running else
               "The coordinator records it when it next runs (a hook starts it with the next event).")
            + " Other sessions asking who_is_changing see the paths and functions, never your task line.")
    return data, text


def _bare_name(name):
    """A function or class a declaration of several files names without its file."""
    if not declarations.is_name(name):
        raise ToolError(f'"{templates.clean(name, 200)}" is not a function or class name: give its name (Class.method) '
                        "or its full id (path::name).")
    return name


def claim(ws, args):
    me = ws.writer()
    common_dir = ws.common_dir(me)
    if not common_dir or not config.is_watched(config.load(ws.state), common_dir):
        raise ToolError("This repository is not watched: run orch watch (or orch init) in it.")
    top = me["worktree"]
    base = ws.folder if top and _inside(ws.folder, top) else top
    try:
        kind, target, file = claiming.resolve(top, base, args["target"])
    except claiming.Refused as exc:
        raise ToolError(templates.printable(exc, templates.MAX_NOTE).rstrip(".") + ".") from None
    hard = bool(args.get("hard", False))
    minutes = args.get("minutes") or claiming.default_minutes(config.load(ws.state))
    claiming.append_claim(ws.state, me["id"], common_dir, kind, target, file, hard, minutes, now=ws.now)
    running = feedback.coordinator_running(ws.state, ws.now)
    what = templates.reserved({"kind": kind, "target": target, "file": file})
    data = {"session": me["id"], "kind": kind, "target": target, "file": file, "hard": hard, "holds_minutes": minutes,
            "until": _iso(ws.now + minutes * 60), "recorded": "now" if running else "when the coordinator next runs"}
    text = (f"Claimed {what} for this session for {minutes} minutes{', hard' if hard else ''}. Other sessions are told "
            "before they edit it" + ("; a Claude Code session's edit waits for its user's answer, and other agents' "
                                     "sessions are told only" if hard else "")
            + ". " + ("The coordinator records it now." if running else
                      "The coordinator records it when it next runs (a hook starts it with the next event).")
            + " Release it with the tool release when you are done.")
    return data, text


def release(ws, args):
    me = ws.writer()
    ref = args["target"].strip()
    mine = [c for c in ws.claims if c["session_id"] == me["id"]]
    if ref.lstrip("#").isdigit():
        chosen = [c for c in mine if c["id"] == int(ref.lstrip("#"))]
    else:
        chosen = [c for c in mine if c["target"] == ref]
        if not chosen and me["worktree"]:
            base = ws.folder if _inside(ws.folder, me["worktree"]) else me["worktree"]
            try:
                target = claiming.resolve(me["worktree"], base, ref)[1]
            except claiming.Refused:
                target = None
            chosen = [c for c in mine if c["target"] == target]
    if not chosen:
        raise ToolError(f"No claim of this session on {templates.clean(ref, 200)} holds (status lists its claims).")
    common_dir = ws.common_dir(me)
    for c in chosen:
        claiming.append_release(ws.state, common_dir, claim_id=c["id"], session_id=me["id"], now=ws.now)
    data = {"session": me["id"], "released": [c["id"] for c in chosen]}
    text = ("Released " + ", ".join(f"#{c['id']} {templates.reserved(c)}" for c in chosen)
            + ". " + ("The coordinator records it now." if feedback.coordinator_running(ws.state, ws.now) else
                      "The coordinator records it when it next runs."))
    return data, text


_SESSION = {"type": "string", "minLength": 1, "maxLength": 1000,
            "description": "The absolute path of the folder you work in, or your session's id or its first characters "
                           "(orch status shows them): a session working in this server's worktree or in a worktree "
                           "inside it. Needed when the tools say they cannot tell which session calls (a worktree "
                           "session whose server runs in the main checkout, or several sessions in one folder), or "
                           "when the server runs outside your workspace (then only the tools that read take it)."}
_PATH = {"type": "string", "minLength": 1, "maxLength": 1000,
         "description": "The file: a path in the repository, absolute or relative to your workspace."}
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS = [
    {"name": "who_is_changing", "title": "Who is changing this?", "handler": who_is_changing,
     "description": "The live coding-agent sessions on this repository that are changing a file, or one function in "
                    "it, or have declared it: each one's agent, worktree, branch, last activity, and the function and "
                    "contract changes with their reasons. Ask before editing shared code.",
     "inputSchema": {"type": "object", "properties": {
         "path": _PATH, "symbol": {"type": "string", "minLength": 1, "maxLength": declarations.MAX_ITEM,
                                   "description": "A function or class in the file: its name (Class.method) or "
                                                  "full id (path::name)."},
         "session": _SESSION}, "required": ["path"], "additionalProperties": False},
     "annotations": _READ_ONLY},
    {"name": "check_before_edit", "title": "Check before editing a file", "handler": check_before_edit,
     "description": "Call before editing a file. Says, in Orchestraty's note wording: breaking or removed functions "
                    "from other sessions that this file uses, functions in it that another session is also "
                    "changing, and another session working in this same folder.",
     "inputSchema": {"type": "object", "properties": {"path": _PATH, "session": _SESSION}, "required": ["path"],
                     "additionalProperties": False},
     "annotations": _READ_ONLY},
    {"name": "my_overlaps", "title": "My overlaps", "handler": my_overlaps,
     "description": "The overlaps Orchestraty found between this session's changes and other sessions', strongest "
                    "first, each with what it tells you.",
     "inputSchema": {"type": "object", "properties": {"session": _SESSION}, "additionalProperties": False},
     "annotations": _READ_ONLY},
    {"name": "status", "title": "Repository status", "handler": status_tool,
     "description": "This repository as orch status --json gives it: the coordinator, every live session, what each "
                    "changed and declared, and the overlaps between them.",
     "inputSchema": {"type": "object", "properties": {"session": _SESSION}, "additionalProperties": False},
     "annotations": _READ_ONLY},
    {"name": "declare", "title": "Declare what you are about to change", "handler": declare,
     "description": "Say what you are about to change: file paths, and optionally functions and one short line about "
                    "the task. Other sessions asking who_is_changing see the paths and functions, never the task "
                    "line. It holds for 2 hours by default.",
     "inputSchema": {"type": "object", "properties": {
         "paths": {"type": "array", "minItems": 1, "maxItems": declarations.MAX_PATHS,
                   "items": {"type": "string", "minLength": 1, "maxLength": 1000},
                   "description": "The files you are about to change."},
         "symbols": {"type": "array", "maxItems": declarations.MAX_SYMBOLS,
                     "items": {"type": "string", "minLength": 1, "maxLength": declarations.MAX_ITEM},
                     "description": "Functions or classes you are about to change: names, or path::name."},
         "task": {"type": "string", "maxLength": declarations.MAX_TASK,
                  "description": "One short line about the task, kept on this machine."},
         "session": _SESSION}, "required": ["paths"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "claim", "title": "Claim a file, folder or function", "handler": claim,
     "description": "Reserve a file, a folder, or a Python function or class (path::name, or module.name) for this "
                    "session: other sessions are told before they edit it. A hard claim makes their edit wait for "
                    "their user's answer, where their agent can ask (Claude Code); others are told only. It holds for 2 "
                    "hours by default, or `minutes`, until you release it, or until this session ends.",
     "inputSchema": {"type": "object", "properties": {
         "target": {"type": "string", "minLength": 1, "maxLength": 1000,
                    "description": "A file or folder in the repository (absolute, or relative to your workspace), "
                                   "or a function or class as path::name or module.name."},
         "hard": {"type": "boolean", "description": "Make other sessions' edits there wait for their user's answer."},
         "minutes": {"type": "integer", "minimum": 1, "maximum": claiming.MAX_MINUTES,
                     "description": "How long it holds (default 120)."},
         "session": _SESSION}, "required": ["target"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "release", "title": "Release a claim", "handler": release,
     "description": "End a claim this session made: name what it claims, as you claimed it, or its number.",
     "inputSchema": {"type": "object", "properties": {
         "target": {"type": "string", "minLength": 1, "maxLength": 1000,
                    "description": "The file, folder or function you claimed, or the claim's number."},
         "session": _SESSION}, "required": ["target"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def listing():
    """The tools as tools/list gives them."""
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]


def check_arguments(schema, args):
    """Raise ArgumentError when `args` does not fit the tool's input schema."""
    if not isinstance(args, dict):
        raise ArgumentError("arguments must be an object")
    props = schema["properties"]
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ArgumentError("unknown argument(s): " + ", ".join(templates.clean(k, 60) for k in unknown))
    missing = [k for k in schema.get("required", []) if k not in args]
    if missing:
        raise ArgumentError("missing argument(s): " + ", ".join(missing))
    for key, value in args.items():
        spec = props[key]
        if spec["type"] == "string":
            if not isinstance(value, str) or not spec.get("minLength", 0) <= len(value) <= spec["maxLength"]:
                raise ArgumentError(f"{key} must be a string of {spec.get('minLength', 0)} to {spec['maxLength']} "
                                    "characters")
        elif spec["type"] == "boolean":
            if not isinstance(value, bool):
                raise ArgumentError(f"{key} must be true or false")
        elif spec["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int) or not spec["minimum"] <= value <= spec["maximum"]:
                raise ArgumentError(f"{key} must be a whole number from {spec['minimum']} to {spec['maximum']}")
        else:
            item = spec["items"]
            if (not isinstance(value, list) or not spec.get("minItems", 0) <= len(value) <= spec["maxItems"]
                    or not all(isinstance(v, str) and item["minLength"] <= len(v) <= item["maxLength"] for v in value)):
                raise ArgumentError(f"{key} must be a list of {spec.get('minItems', 0)} to {spec['maxItems']} strings "
                                    f"of {item['minLength']} to {item['maxLength']} characters")


def call(state, folder, name, args, now=None):
    """(structured result, text) of one tool. Raises ArgumentError for a call that does not fit
    the tool, ToolError when the tool cannot answer."""
    tool = BY_NAME.get(name)
    if tool is None:
        raise ArgumentError(f"unknown tool: {templates.clean(name, 60)}")
    args = {} if args is None else args
    check_arguments(tool["inputSchema"], args)
    with Workspace(state, folder, now) as ws:
        ws.locate(args.get("session"), args.get("path") or (args.get("paths") or [None])[0])
        return tool["handler"](ws, args)
