"""The coordinator: one process per user and environment, started on demand by a hook.

It holds the single-writer lock, touches the heartbeat, reads the spools every
``SCAN_SECONDS``, records the events of watched repositories, keeps each session's
lifecycle, and exits after ``idle_seconds`` with no live session and no new event, or once
another Orchestraty is installed (an upgrade: the next hook starts the new one). Lines in
``<spool>/orch/`` are the user's own (``orch label``, ``orch missed``), an agent's declaration
(the MCP tool ``declare``), or a claim made or released (``orch claim``, ``orch release`` and
their MCP tools): stored in the record only. Where the together check is on (``together``), the
pairs of sessions a finding flags are checked on a worker thread, and the results taken in here.

A session is created from its first event: its worktree, repository, branch and base commit
(HEAD then) come from git in the event's folder. The base counts as guessed unless that first
event is a fresh SessionStart. Sessions outside watched repositories, or outside git, are
ignored: their spool lines are read and dropped, their spool files deleted once read, and
nothing about them is recorded.

Every live session is followed on the live map (``livemap.map``), refreshed after its tool
calls and on a timer. The timer also looks at the other worktrees of each repository with a
live session: one with changes and no hooked session becomes a plain-git session (agent
``git``, level 0), based on its merge base with the main worktree's HEAD, so agents that run
no hooks, editors and people are seen too. When a hooked session ends, its repository's
worktrees are looked at at once: a worktree it left with changes is followed as plain git, so
its changes stay on the map (and its push line stays) after the agent has gone. A hooked
session whose edits land in another worktree of its repository moves there: its agent left the
folder (for one, after a note said two sessions shared it).

In a repository whose cloud branches are on (``orch remote on``), each branch an agent pushed
that matches the patterns is a session too (agent ``cloud``, level 0, see
``coordinator.cloud``): based on its merge base with the remote's default branch, read from the
shadow repository, and ended when the branch is gone or merged. Its last event is its last
commit's time; it never times out, and its own agent is never told.
"""
import hashlib
import json
import logging
import logging.handlers
import os
import re
import sqlite3
import sys
import time

from orchestraty import __version__, claims, config, declarations, feedback, gitio, installed_version, remote, together
from orchestraty.coordinator.cloud import CloudBranches, session_id as cloud_session_id
from orchestraty.coordinator.lock import Heartbeat, Lock, touch
from orchestraty.coordinator.record import Record, SchemaError, repo_key, repo_name
from orchestraty.coordinator.spool import SpoolReader
from orchestraty.delivery import slots
from orchestraty.delivery.deliver import NO_NOTES, Deliverer
from orchestraty.livemap import changes as git_changes
from orchestraty.livemap import snapshots, typescript
from orchestraty.livemap.map import LiveMap
from orchestraty.rules import overlap

SCAN_SECONDS = 0.15
HOUSEKEEPING_SECONDS = 10
FRESH_START_SECONDS = 60          # a SessionStart read later than this gives a guessed base
TOGETHER_SECONDS = 60             # a pair of sessions is checked together at most once in this
KEEP_ENDED_SPOOL_SECONDS = 24 * 3600
MAX_FAILURES = 20
MAX_FOLDERS_LOOKED_AT = 16        # a session passed over is looked at again in at most this many folders
LEVEL0_BUDGET_SECONDS = 0.5       # plain-git worktrees looked at in one pass, at most this long ...
LEVEL0_MIN_PER_PASS = 4           # ... and at least this many of the queue
LEVEL0_MAX_SECONDS = 60           # a worktree that stays the same is looked at less often, down to this
NS = 1_000_000_000
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")   # as the hook names a session's spool file
WORKING_EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
                  "PreInvocation")          # Antigravity's: before each call of its model
IDLE_EVENTS = ("SessionStart", "Stop", "StopFailure")
DETAIL_FIELDS = ("source", "reason", "stop_hook_active", "len", "took")
LOCK_WAIT_SECONDS = 3.0
HOLD_OFF_SECONDS = 60             # no hook starts a coordinator for this long after one could not open its
                                  # record, or failed MAX_FAILURES steps in a row
LEVEL0_AGENT = "git"
CLOUD_AGENT = remote.CLOUD_AGENT

# Exit codes of run()
OK, ALREADY_RUNNING, NEWER_RECORD, FAILED, BAD_RECORD = 0, 3, 4, 5, 6


class Coordinator:
    """The work of one coordinator process, one step at a time (the loop is in run())."""

    def __init__(self, state, now_ns=time.time_ns, log=None):
        self.state = state
        self.now_ns = now_ns
        self.log = log or logging.getLogger("orchestraty.coordinator")
        self.record = Record(state.record)
        self.reader = SpoolReader(state.spool, self.record.positions())
        self.sessions = {s["id"]: s for s in self.record.sessions(states=("live",))}
        self.ignored = {}                  # session id -> {folder: why it was passed over} (in memory only)
        self.unwatched = {}                # repository key -> {common_dir, folder, sessions: {id: last line}}
        self._unwatched_dirty = True       # a stale unwatched.json from an earlier coordinator goes
        self.cfg = dict(config.DEFAULTS)
        self._cfg_stamp = object()
        self.bad_lines = 0
        self._last_housekeeping = 0
        self._last_scan = float("-inf")    # due at once, a share per pass; None: a full pass (tests set it)
        self._scan_now = set()             # repositories to look at now (a hooked session ended)
        self._trees = {}                   # worktree (normcase) -> {repo, print, every, next}: when to look again
        self._tree_queue = []              # worktrees queued to be looked at (see look_at_trees)
        self._bases = {}                   # (worktree, head, main head) -> the merge base a new one starts from
        self._repo_paths = {}              # repository id -> a worktree of it, to list the others
        self._stayed = set()               # (session id, folder): an edit there that is no move
        self.changed = set()               # sessions whose live map changed in the last step
        self.new_findings = []             # findings first seen in the last step
        self._dirty_repos = set()          # repositories whose findings must be worked out again
        self._last_expiry = None           # when expire() last looked for declarations that stopped holding
        self.reload_config()
        self.livemap = LiveMap(state, self.log)
        self.livemap.ts.cfg = self.cfg
        self.deliverer = Deliverer(state, self.record, self.log, now_ns)
        self._push_dirty = True
        self._status_dirty = True
        self._status_written = 0.0
        self.upgraded = None               # the version installed since this one started, once seen
        self._repo_keys = {r["id"]: r["key"] for r in self.record.repos()}
        self.cloud = CloudBranches(state, self.log)
        self.together = together.Worker(state)
        self._together_due = {}            # (session, session) -> the check to queue once its minute is up
        self._together_queued = {}         # (session, session) -> when its last check was queued (monotonic)
        for session in self.sessions.values():
            if session["agent"] != CLOUD_AGENT:            # cloud branches are tracked by scan_cloud, from their tips
                self._track(session)

    def close(self):
        self.record.close()
        _remove(self.state.unwatched)          # counted by a running coordinator only

    def _ended_hooked(self, session):
        """A hooked session ended: look at its repository's worktrees at once."""
        if session["agent"] not in NO_NOTES and session.get("repo_id") is not None:
            self._repo_paths[session["repo_id"]] = session["worktree"]
            self._scan_now.add(session["repo_id"])

    def _track(self, session):
        if session.get("repo_id") is not None and session.get("worktree"):
            self._repo_paths.setdefault(session["repo_id"], session["worktree"])
        self._dirty_repos.add(session["repo_id"])          # a new session can overlap before it changes anything
        key = self._repo_keys.get(session["repo_id"])
        if key is None:
            self._repo_keys = {r["id"]: r["key"] for r in self.record.repos()}
            key = self._repo_keys.get(session["repo_id"], "")
        files, unattributed, _ = self.record.changes(session["id"])   # a session seen before keeps what it changed
        self.livemap.track(session, key, adopt_existing=session["agent"] == LEVEL0_AGENT,
                           recorded=(files, unattributed))

    def _end(self, session, note):
        session["state"], session["note"], session["turn"] = "ended", note, ""
        self.record.put_session(session)
        self.livemap.forget(session["id"])
        self.deliverer.forget_session(session["id"])
        self.sessions.pop(session["id"], None)
        self._dirty_repos.add(session["repo_id"])
        self._push_dirty = True

    # configuration
    def reload_config(self):
        """Read config.json again if it changed. Every session passed over gets another look at
        its next line (its repository may be watched now). Returns True when it changed."""
        try:
            stamp = os.stat(self.state.config).st_mtime_ns
        except OSError:
            stamp = None
        if stamp == self._cfg_stamp:
            return False
        self._cfg_stamp = stamp
        self.cfg = config.load(self.state)
        self.ignored = {sid: {} for sid in self.ignored}
        for key in [k for k, r in self.unwatched.items() if config.is_watched(self.cfg, r["common_dir"])]:
            del self.unwatched[key]                 # watched now: its sessions count at their next line
            self._unwatched_dirty = True
        if hasattr(self, "livemap"):
            self.livemap.ts.cfg = self.cfg
            self.livemap.mark_all()                         # TypeScript contracts may have been turned on or off
        return True

    # one pass
    def step(self):
        """Read and record everything new in the spools; returns the number of lines read."""
        saved = dict(self.reader.positions)     # what the record holds: going back to it reads nothing
        lines = self.reader.poll()
        if lines or self.reader.changed:
            try:
                with self.record.transaction():
                    for agent, _path, raw in lines:
                        self.handle_line(agent, raw)
                    self.record.save_positions(self.reader.take_changes())
            except BaseException:
                self.reader.rewind(saved)       # nothing of it was saved: read it all again
                raise
        now = self.now_ns()
        if now - self._last_housekeeping >= HOUSEKEEPING_SECONDS * NS:
            self._last_housekeeping = now
            self.check_upgrade()
            self.expire(now)
            self.expire_unwatched(now)
            self.clean_spool(now)
            if not self.together.busy():            # a check that runs for hours keeps its folder
                together.clean_scratch(self.state)
            if not self.livemap.ts.busy():          # nor is a compiler run's folder taken from under it
                typescript.clean(self.state, {s["worktree"] for s in self.sessions.values() if s.get("worktree")})
        self.update_map()
        self.update_findings()
        self.check_together()
        self.deliver()
        if lines or self.changed:
            self._status_dirty = True
        self.write_unwatched()
        self.write_status()
        return len(lines)

    def check_upgrade(self):
        """Note another Orchestraty installed since this coordinator started (an upgrade): run()
        then leaves, and the next agent event starts the one installed."""
        found = installed_version()
        if found and found != __version__:
            self.upgraded = found

    def write_status(self, force=False):
        """Keep <state>/status.json (orch status --json) current for the mod, at most once a second."""
        mono = time.monotonic()
        if not (force or (self._status_dirty and mono - self._status_written >= 1.0)):
            return False
        from orchestraty import status
        try:
            data = status.collect(self.state)
        except Exception:
            self.log.exception("status not written")
            return False
        tmp = self.state.status + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.state.status)
        self._status_dirty, self._status_written = False, mono
        return True

    def deliver(self):
        """Fill the slots that have notes waiting, and keep the push lines current."""
        with self.record.transaction():
            written = self.deliverer.fill(steer=self.cfg["steer"])
        for slot in written:
            self.log.info("note ready: %s", slot)
        if self._push_dirty:
            self._push_dirty = False
            self._status_dirty = True
            self.deliverer.push_lines(self.sessions, self.record.findings())
        return written

    def _took(self, session, line, ts):
        """A hook took a slot (its spool line says which kind)."""
        kind, sid = line.get("took"), session["id"]
        if kind == "edit" and isinstance(line.get("file_path"), str):
            slot = slots.edit_slot(self.state, sid, line["file_path"])
        elif kind == "area" and isinstance(line.get("file_path"), str):
            slot = self._area_taken(sid, line["file_path"])
            if slot is None:
                return
        elif kind in ("next", "stop", "wake"):
            slot = {"next": slots.next_slot, "stop": slots.stop_slot, "wake": slots.wake_slot}[kind](self.state, sid)
        else:
            return
        self.deliverer.taken(os.path.relpath(slot, self.state.root).replace(os.sep, "/"), ts)

    def _area_taken(self, sid, path):
        """The folder slot above the file `path` that the hook took: the nearest one with notes
        written to it that is gone from disk, or None."""
        above, folder = path, os.path.dirname(path)
        while folder and folder != above:
            for ask in (True, False):
                slot = slots.area_slot(self.state, sid, folder, ask)
                rel = os.path.relpath(slot, self.state.root).replace(os.sep, "/")
                if not slots.waiting(slot) and self.record.notes(slot=rel, states=("written",)):
                    return slot
            above, folder = folder, os.path.dirname(folder)
        return None

    def update_map(self):
        """Refresh the live map where needed and record what changed. On the timer every live
        hooked session is refreshed and the plain-git worktrees due are queued; each pass then
        looks at a share of the queue (look_at_trees), so a pass stays short. A full pass (the
        first after `_last_scan` is cleared) refreshes every session and every worktree at once.
        orch ui's Refresh (the rescan file) makes every worktree due now."""
        mono = time.monotonic()
        if os.path.exists(self.state.rescan):
            _remove(self.state.rescan)
            for seen in self._trees.values():
                seen["every"], seen["next"] = 0, 0
            self.ignored = {sid: {} for sid in self.ignored}
            if self._last_scan is not None:
                self._last_scan = float("-inf")
            self._status_dirty = True
        full = self._last_scan is None
        if full or mono - self._last_scan >= self.cfg["level0_seconds"] or self._scan_now:
            self._last_scan = mono
            self.livemap.mark_all(skip_agent=None if full else LEVEL0_AGENT)
            forced, self._scan_now = self._scan_now, set()
            self.scan_level0(forced, full)
            self.scan_cloud()
        self.look_at_trees(None if full else LEVEL0_BUDGET_SECONDS)
        self.changed = self.livemap.refresh_pending()
        if self.changed:
            with self.record.transaction():
                for sid in sorted(self.changed):
                    v = self.livemap.views.get(sid)
                    if v is not None:
                        self.record.put_changes(sid, v.files, v.unattributed,
                                                [c.as_dict() for c in v.symbols.values()], v.not_compared)
        return self.changed

    def update_findings(self):
        """Run the overlap rules for every repository where something changed: record new
        findings, refresh the known ones, and resolve those that stopped holding. A finding
        that needs a code graph still being built is kept as it is meanwhile. Returns the
        findings first seen now."""
        repos = self._dirty_repos | {self.sessions[sid]["repo_id"] for sid in self.changed if sid in self.sessions}
        self._dirty_repos = set()
        self.new_findings = []
        if repos:
            with self.record.transaction():                 # a claim that ended takes back its notes
                self.deliverer.withdraw_claims({c["id"] for c in self.record.claims(active_at=self.now_ns())})
        for repo_id in sorted(repos):
            ids = sorted(sid for sid, s in self.sessions.items()
                         if s["repo_id"] == repo_id and s["state"] == "live" and sid in self.livemap.views)
            views = {sid: self.livemap.views[sid] for sid in ids}
            graph_ready = {sid: self.livemap.graph(sid) is not None for sid in ids}
            base_ready = {sid: self.livemap.base_graph(sid) is not None for sid in ids}
            routes_ready = {sid: self.livemap.routes(sid) is not None for sid in ids}
            hooked = {sid for sid in ids if self.sessions[sid]["agent"] not in NO_NOTES}
            found = overlap.evaluate(views, self.livemap.graph, self.livemap.base_graph, hooked=hooked,
                                     planned=self._planned(repo_id, ids, self.now_ns()), routes=self.livemap.routes,
                                     ts_uses=self.livemap.ts_uses)
            now = self.now_ns()
            with self.record.transaction():
                # Resolve first: a note withdrawn here must not count as already told when a
                # finding about the same change, under another session, makes its notes below.
                keys = {f.key for f in found}
                for old in self.record.findings(repo_id=repo_id):
                    if old["key"] in keys:
                        continue
                    waiting = ((old["rule"] in ("O3", "O4") and graph_ready.get(old["receiver"]) is False)
                               or (old["rule"] == "O6" and base_ready.get(old["changer"]) is False)
                               or (old["rule"] == "O5" and any(routes_ready.get(s) is False for s in old["sessions"])))
                    if not waiting:
                        self.record.resolve_finding(old["key"], now)
                        self.deliverer.withdraw(old["key"])
                for f in found:
                    if self.record.put_finding(repo_id, f, now):
                        self.new_findings.append(f)
                        self.log.info("finding %s (%s)", f.key, f.strength)
                    # Every evaluation, not only for new findings: a latent finding that became
                    # real now calls for the note to the session that made the change. Notes
                    # already made are not made again.
                    cause = max((self.sessions[s]["last_seen"] for s in f.sessions if s in self.sessions), default=now)
                    self.deliverer.add(f.as_dict(), self.sessions, cause, wake=self.cfg["wake"])
                changed = {sid: list(views[sid].files) for sid in ids}
                for c in self.record.claims(active_at=now, repo_id=repo_id):
                    self.deliverer.add_claim(c, self.sessions, changed)
            self._together_jobs(repo_id, found)
            self._push_dirty = True
        return self.new_findings

    # the together check (opt-in)
    def _together_jobs(self, repo_id, found):
        """Mark the pairs of sessions a finding flags (O2 to O5, not latent) as due for a
        together check, where it is on for this repository and has a command."""
        key = self._repo_keys.get(repo_id, "")
        entry = together.settings_for_key(self.cfg, key)
        if not (entry["on"] and entry["command"]):
            return
        common_dir = next((r["common_dir"] for r in self.record.repos() if r["id"] == repo_id), "")
        for f in found:
            if f.rule not in together.RULES or f.detail.get("latent") or len(f.sessions) != 2:
                continue
            a, b = f.sessions
            sa, sb = self.sessions.get(a), self.sessions.get(b)
            if (not sa or not sb or CLOUD_AGENT in (sa["agent"], sb["agent"]) or not sa["worktree"]
                    or not sb["worktree"] or _same(sa["worktree"], sb["worktree"])):
                continue                                     # no working copies of their own to run
            job = self._together_due.get((a, b))
            if job is None:
                job = {"findings": [], "sessions": [a, b], "worktrees": {a: sa["worktree"], b: sb["worktree"]},
                       "bases": {a: sa["base_commit"], b: sb["base_commit"]}, "common_dir": common_dir, "key": key,
                       "command": entry["command"], "timeout": entry["timeout"]}
                self._together_due[(a, b)] = job
            if f.key not in job["findings"]:
                job["findings"].append(f.key)

    def check_together(self):
        """Queue the due checks whose pair's minute is up, and take in the finished ones: each kept
        for orch status, and a note to both sessions when they pass alone and fail together."""
        mono = time.monotonic()
        for pair, job in list(self._together_due.items()):
            entry = together.settings_for_key(self.cfg, job["key"])
            if not (entry["on"] and entry["command"]) or not all(sid in self.sessions for sid in pair):
                del self._together_due[pair]                     # turned off meanwhile, or a session ended
                continue
            last = self._together_queued.get(pair)
            if last is not None and mono - last < TOGETHER_SECONDS:
                continue
            job.update(command=entry["command"], timeout=entry["timeout"])
            if self.together.queue(job):
                self._together_queued[pair] = mono
                del self._together_due[pair]
        for result in self.together.take():
            if result["result"] == "unchanged":
                continue
            together.save_result(self.state, result)
            self._status_dirty = True
            self.log.info("together check of %s: %s", " and ".join(result["sessions"]), result["result"])
            if result["result"] == "fails together":
                with self.record.transaction():
                    self.deliverer.add_together(result, self.sessions)

    def _planned(self, repo_id, ids, now):
        """{session id: [item]} for O7: what the live sessions `ids` of one repository declared
        (the MCP tool declare) and claimed (orch claim), as far as it holds at `now`. An item is
        {"path", "symbol", "how"}. A declared file that the declaration names functions of
        stands for those functions only; its bare function names (of several files) are left
        out."""
        ids, planned = set(ids), {}
        for d in self.record.declarations(active_at=now):
            if d["session_id"] in ids:
                named = [x for x in d["symbols"] if "::" in x]
                files = {x.split("::", 1)[0] for x in named}
                items = planned.setdefault(d["session_id"], [])
                items += [{"path": p, "symbol": "", "how": "declared"} for p in d["paths"] if p not in files]
                items += [{"path": x.split("::", 1)[0], "symbol": x, "how": "declared"} for x in named]
        for c in self.record.claims(active_at=now, repo_id=repo_id):
            if c["session_id"] in ids:
                symbol = c["target"] if c["kind"] == "symbol" else ""
                planned.setdefault(c["session_id"], []).append({"path": claims.path_of(c), "symbol": symbol,
                                                                "how": "claimed"})
        return planned

    def scan_level0(self, forced=(), full=False):
        """Plain-git sessions: worktrees with changes and no live hooked session, in each
        repository with a live session of any kind, or whose hooked session just ended. Lists
        each repository's worktrees and queues the ones due: every one in a full pass and in a
        forced repository (looked at in this pass), else those whose time has come (see
        look_at_trees). A plain-git session whose worktree folder is gone ends."""
        live = [s for s in self.sessions.values() if s["state"] == "live" and s["worktree"]]
        gone = [s for s in live if s["agent"] == LEVEL0_AGENT and not os.path.isdir(s["worktree"])]
        if gone:
            with self.record.transaction():
                for s in gone:
                    self._end(s, "its worktree is gone")
            live = [s for s in live if s not in gone]
        hooked = [s for s in live if s["agent"] != LEVEL0_AGENT]
        repos = {}
        for s in live:
            if not snapshots.is_snapshot(s["base_commit"]) and os.path.isdir(s["worktree"]):
                repos.setdefault(s["repo_id"], s["worktree"])     # a folder project has no worktrees to list
        for repo_id in forced:
            if repo_id in self._repo_paths:
                repos.setdefault(repo_id, self._repo_paths[repo_id])
        mono = time.monotonic()
        queued = {item[1]: item for item in self._tree_queue}
        listed = set()
        for repo_id, any_worktree in repos.items():
            try:
                trees = git_changes.worktrees(any_worktree)
            except (gitio.GitError, gitio.UntrustedRepositoryError):
                continue
            busy = {os.path.normcase(s["worktree"]) for s in hooked if s["repo_id"] == repo_id}
            main_head = trees[0]["head"] if trees else ""
            for i, tree in enumerate(trees):
                key = os.path.normcase(tree["path"])
                listed.add(key)
                if key in busy:                             # nothing to look at: a hooked session works here
                    self._scan_tree(repo_id, tree, i == 0, main_head, busy)
                    queued.pop(key, None)
                    continue
                now = full or repo_id in forced
                seen = self._trees.get(key)
                if now or seen is None or seen["next"] <= mono:
                    old = queued.get(key)
                    queued[key] = (0 if now or (old and old[0] == 0) else 1, key, repo_id, tree, i == 0, main_head)
        for key in [k for k in self._trees if k not in listed and self._trees[k]["repo"] in repos]:
            del self._trees[key]                            # a worktree that is gone
        self._tree_queue = sorted(queued.values(), key=lambda item: (item[0], self._trees.get(item[1], {}).get("next", 0)))

    def look_at_trees(self, budget):
        """Look at the queued worktrees: the ones due now in full, the rest in turn until
        `budget` seconds have passed (at least LEVEL0_MIN_PER_PASS of them), so a pass stays short
        and new spool lines are read soon; the others wait for the next pass. A worktree that
        looks the same as last time (the same files, of the same size and time) is looked at
        less often, up to every LEVEL0_MAX_SECONDS; one that changed, every level0_seconds."""
        start, done = time.monotonic(), 0
        while self._tree_queue:
            in_turn, key, repo_id, tree, is_main, main_head = self._tree_queue[0]   # in_turn 0: due now
            if in_turn and budget is not None and done >= LEVEL0_MIN_PER_PASS \
                    and time.monotonic() - start >= budget:
                break
            self._tree_queue.pop(0)
            done += 1
            seen = self._trees.get(key)
            print_ = self._scan_tree(repo_id, tree, is_main, main_head, ())
            step = self.cfg["level0_seconds"]
            if seen is not None and print_ is not None and print_ == seen["print"]:
                step = min(LEVEL0_MAX_SECONDS, max(step, seen["every"] * 2))
            self._trees[key] = {"repo": repo_id, "print": print_, "every": step, "next": time.monotonic() + step}

    def _scan_tree(self, repo_id, tree, is_main, main_head, busy):
        """Look at one worktree; returns what it looked like (None when git could not say)."""
        path = tree["path"]
        sid = "git-" + hashlib.sha1(os.path.normcase(path).encode("utf-8", "surrogateescape")).hexdigest()[:12]
        existing = self.sessions.get(sid)
        if os.path.normcase(path) in busy:
            if existing:
                with self.record.transaction():
                    self._end(existing, "a hooked session works here")
            return None
        if existing:
            base = existing["base_commit"]
        elif is_main or not main_head:
            base = tree["head"]
        else:
            known = (os.path.normcase(path), tree["head"], main_head)
            if known not in self._bases:
                if len(self._bases) > 1000:
                    self._bases.clear()
                self._bases[known] = git_changes.merge_base(path, tree["head"], main_head) or tree["head"]
            base = self._bases[known]
        try:
            files = git_changes.changed_files(path, base)
        except (gitio.GitError, gitio.UntrustedRepositoryError):
            return None
        looks = (base, tuple(sorted((p, s, _size_and_time(os.path.join(path, *p.split("/"))))
                                    for p, s in files.items())))
        with self.record.transaction():
            if not files:
                if existing:
                    self._end(existing, "clean")
                return looks
            now = self.now_ns()
            if existing is None:
                existing = {"id": sid, "agent": LEVEL0_AGENT, "repo_id": repo_id, "cwd": path, "worktree": path,
                            "branch": tree["branch"], "base_commit": base, "base_guessed": 0, "started": now,
                            "last_seen": now, "state": "live", "turn": "", "note": "plain git"}
                self.sessions[sid] = existing
                self._track(existing)
                self.log.info("plain-git session %s in %s, base %s", sid, path, base[:12])
            else:
                seen = self._trees.get(os.path.normcase(path))
                if sid in self.livemap.sessions and (seen is None or seen["print"] != looks):
                    self.livemap.pending.add(sid)          # its view follows the worktree, not the timer
            existing["last_seen"] = now
            self.record.put_session(existing)
        return looks

    def scan_cloud(self):
        """Cloud branches: fetch where due (on a thread), then make each matching branch of the
        last fetch a session, follow the ones that moved, and end the ones gone or merged."""
        now = self.now_ns()
        watched = set()
        for common_dir in self.cfg["repos"]:
            key = repo_key(common_dir)
            watched.add(key)
            chosen = remote.settings(self.cfg, common_dir)
            mine = {sid: s for sid, s in self.sessions.items()
                    if s["agent"] == CLOUD_AGENT and self._repo_keys.get(s["repo_id"]) == key}
            if not chosen["on"]:
                self._end_cloud(mine.values(), "remote branches off")
                continue
            shadow = remote.Shadow(self.state, common_dir)
            if key in self.cloud.fetched:                   # a fetch finished: what failed to read may read now
                self.cloud.fetched.discard(key)
                self.livemap.indexes.forget_failures(key)
                for sid in mine:
                    self.livemap.reread_cloud(sid)
            branches = None
            broken = shadow.broken() or any(remote.broken_error(self.livemap.views[sid].error)
                                            for sid in mine if sid in self.livemap.views)
            if not broken:
                try:
                    branches = self.cloud.branches(common_dir, chosen)
                except remote.ShadowBroken as exc:
                    self.log.warning("the shadow of %s lost an object it relied on (%s): fetching it afresh",
                                     common_dir, exc)
                    broken = True
                except (gitio.GitError, gitio.UntrustedRepositoryError) as exc:
                    self.log.warning("cloud branches of %s not read: %s", common_dir, exc)
                    continue
            if broken:
                if not shadow.broken():
                    shadow.mark_broken(now / NS)           # one fetch due now; failures then wait longer
                self.cloud.forget(common_dir)
            self.cloud.fetch_if_due(common_dir, chosen, self.cfg["remote_fetch_minutes"] * 60, now / NS)
            if not broken and branches is not None:       # None: nothing fetched yet, so nothing to end either
                self._sync_cloud(common_dir, key, chosen, branches, mine)
        self._end_cloud([s for s in self.sessions.values() if s["agent"] == CLOUD_AGENT
                         and self._repo_keys.get(s["repo_id"]) not in watched], "not watched")

    def _sync_cloud(self, common_dir, key, chosen, branches, mine):
        seen = set()
        with self.record.transaction():
            for name, b in branches.items():
                sid = cloud_session_id(common_dir, chosen["remote"], name)
                session = self.sessions.get(sid)
                if b["merged"]:
                    if session is not None:
                        self._end(session, "merged into the default branch")
                    continue
                seen.add(sid)
                tip_ns = b["time"] * NS
                if session is None:
                    rid = self.record.repo_id(common_dir)
                    self._repo_keys[rid] = key
                    session = {"id": sid, "agent": CLOUD_AGENT, "repo_id": rid, "cwd": "", "worktree": "",
                               "branch": f"{chosen['remote']}/{name}", "base_commit": b["base"], "base_guessed": 0,
                               "started": self.now_ns(), "last_seen": tip_ns, "state": "live", "turn": "", "note": ""}
                    self.sessions[sid] = session
                    self.record.put_session(session)
                    self.log.info("cloud branch %s/%s, base %s", chosen["remote"], name, b["base"][:12])
                elif (session["base_commit"], session["last_seen"]) != (b["base"], tip_ns):
                    session["base_commit"], session["last_seen"] = b["base"], tip_ns
                    self.record.put_session(session)
                view = self.livemap.views.get(sid)
                if view is None or view.base != b["base"]:
                    self.livemap.forget(sid)
                    self._dirty_repos.add(session["repo_id"])
                    self.livemap.track_cloud(session, key, remote.Shadow(self.state, common_dir).path, b["tip"])
                elif self.livemap.cloud[sid][1] != b["tip"]:
                    self.livemap.move_cloud(sid, b["tip"])
            for sid, s in mine.items():
                if sid not in seen and sid in self.sessions and s["state"] == "live":
                    self._end(s, "the branch is gone")

    def _end_cloud(self, sessions, why):
        sessions = [s for s in sessions if s["state"] == "live"]
        if sessions:
            with self.record.transaction():
                for s in sessions:
                    self._end(s, why)

    def handle_line(self, agent, raw):
        """handle() one spool line in a savepoint of its own. A line that cannot be recorded is
        set aside (a bad line, logged) and only what it wrote is undone, so the other lines read
        with it are kept; a session it made is forgotten too. The record failing itself (disk
        full, a damaged file), or memory running out, fails the whole step instead, whose lines
        are then read again."""
        known = set(self.sessions)
        try:
            with self.record.savepoint():
                self.handle(agent, raw)
        except Exception as exc:
            if _record_failed(exc):
                raise
            self.bad_lines += 1
            self.log.exception("a spool line of %s was set aside: it could not be recorded", agent)
            for sid in set(self.sessions) - known:
                del self.sessions[sid]
                self.livemap.forget(sid)

    def handle(self, agent, raw):
        try:
            line = json.loads(raw)
        except ValueError:
            self.bad_lines += 1
            return
        if not isinstance(line, dict) or not _storable(line):
            self.bad_lines += 1
            return
        ts = line.get("ts")
        if not isinstance(ts, int) or isinstance(ts, bool):
            ts = self.now_ns()
        if agent == feedback.SPOOL_AGENT:
            if line.get("event") == "declare":
                self.handle_declaration(line, ts)
            elif line.get("event") in ("claim", "release"):
                self.handle_claim(line, ts)
            else:
                self.handle_feedback(line, ts)
            return
        if "shim_error" in line:
            self.record.add_event({"ts": ts, "agent": agent, "event": "shim_error"},
                                  {"error": str(line["shim_error"])[:64]})
            return
        sid = line.get("session_id")
        event = line.get("event") or line.get("hook_event_name") or ""
        if not isinstance(sid, str) or not sid or not isinstance(event, str):
            return
        if not SESSION_ID.fullmatch(sid):           # an id is a name: as the hook names the session's spool file
            self.bad_lines += 1
            return
        session = self.session_for(sid, agent, line, event, ts)
        if session is None:
            return
        if event == "PostToolUse" and session["state"] == "live":
            self._follow(session, line)
        row = {c: line.get(c) if isinstance(line.get(c), str) else None
               for c in ("session_id", "tool_name", "tool_use_id", "cwd", "file_path")}
        row.update(ts=ts, agent=agent, event=event)
        self.record.add_event(row, {k: line[k] for k in DETAIL_FIELDS if k in line})
        if line.get("took"):
            self._took(session, line, ts)
        self.apply(session, event, line, ts)
        self.record.put_session(session)
        if session["state"] != "live":
            self.livemap.forget(sid)
            self.deliverer.forget_session(sid)
            self._dirty_repos.add(session["repo_id"])
            self._push_dirty = True
            self._ended_hooked(session)
            return
        if sid not in self.livemap.sessions:
            self._track(session)
        self.livemap.note_event(session, event, line)

    def handle_feedback(self, line, ts):
        """A line orch label or orch missed appended: the user's verdict on a finding, or a note
        that should have come. Kept in the record only; no note is ever made from it."""
        item = feedback.parse_line(line)
        if item is None:
            self.bad_lines += 1
        elif item["event"] == "missed":
            self.record.add_missed(ts, item["session_id"], item["text"])
        elif self.record.finding(item["finding"]) is not None:
            self.record.add_label(item["finding"], item["verdict"], item["why"], ts)
        else:
            self.log.info("a label for a finding the record does not have was dropped")

    def handle_declaration(self, line, ts):
        """A line the MCP tool declare appended: what an agent says it is about to change. Kept
        until declare_minutes have passed; its task line never goes into another session's note."""
        item = declarations.parse_line(line)
        if item is None:
            self.bad_lines += 1
        elif self.record.session(item["session_id"]) is None:
            self.log.info("a declaration for a session the record does not have was dropped")
        else:
            ts = self._made(ts)
            self.record.add_declaration(item["session_id"], item["paths"], item["symbols"], item["task"], ts,
                                        ts + self.cfg["declare_minutes"] * 60 * NS)
            self._status_dirty = True
            if item["session_id"] in self.sessions:                 # O7 compares it with the others'
                self._dirty_repos.add(self.sessions[item["session_id"]]["repo_id"])

    def _made(self, ts):
        """When a declaration or a claim counts as made: its line's time, or now when that is
        later. Where the clock is coarse (Windows before Python 3.13), a writer's time in
        seconds, made nanoseconds, can land just past this clock's reading of the same tick;
        the rules, which run in this same pass, must see it hold."""
        return min(ts, self.now_ns())

    def handle_claim(self, line, ts):
        """A line orch claim, orch release or their MCP tools appended. A claim is stored for a
        live session of a watched repository, and holds until it expires or ends; a release
        ends the claims it names that have neither ended nor expired by its time (only its
        session's, when it names one): those read before it, whatever their lines' times."""
        item = claims.parse_line(line)
        if item is None:
            self.bad_lines += 1
            return
        if not config.is_watched(self.cfg, item["repo"]):
            self.log.info("a %s for a repository that is not watched was dropped", item["event"])
            return
        repo_id = self.record.repo_id(item["repo"])
        if item["event"] == "claim":
            s = self.sessions.get(item["session_id"])
            if s is None or s["state"] != "live" or s["repo_id"] != repo_id or s["agent"] == CLOUD_AGENT:
                self.log.info("a claim for a session not live in that repository was dropped")
                return
            ts = self._made(ts)
            self.record.add_claim(repo_id, s["id"], item["kind"], item["target"], item["file"], item["hard"], ts,
                                  ts + item["minutes"] * 60 * NS)
        else:
            for c in self.record.claims(repo_id=repo_id):
                if c["ended"] is not None or c["expires"] <= ts:
                    continue
                named = c["id"] == item["id"] if "id" in item else c["target"] == item["target"]
                if named and (not item["session_id"] or c["session_id"] == item["session_id"]):
                    # Made as a claim is: a release time just past this clock's reading must not
                    # leave the claim holding in this pass, which takes its notes back.
                    self.record.end_claim(c["id"], max(self._made(ts), c["ts"]), "released")
        self._dirty_repos.add(repo_id)
        self._status_dirty = True

    def _follow(self, session, line):
        """A hooked session's edit landed in another worktree of its repository: the agent
        works there now, so the session moves with it (new worktree, branch and base: that
        worktree's HEAD, counted as guessed) and no longer counts in the folder it left. Its
        waiting notes are taken back, and the worktree it left is looked at at once (with
        changes and no hooked session, it is followed as plain git). Returns True on a move."""
        path = line.get("file_path")
        if session["agent"] == LEVEL0_AGENT or not isinstance(path, str) or not path:
            return False
        cwd = line.get("cwd") if isinstance(line.get("cwd"), str) else ""
        path = os.path.normpath(os.path.join(cwd or session["worktree"], path))
        folder = os.path.dirname(path)
        if _inside(path, session["worktree"]) and not _nested(folder, session["worktree"]):
            return False
        if (session["id"], folder) in self._stayed:
            return False
        try:
            info = gitio.worktree_info(folder)
        except (gitio.GitError, gitio.UntrustedRepositoryError):
            info = None
        if (info is None or repo_key(info["common_dir"]) != self._repo_keys.get(session["repo_id"])
                or not info["top"] or _same(info["top"], session["worktree"])):
            self._stayed.add((session["id"], folder))
            return False
        self.log.info("session %s moved from %s to %s", session["id"][:8], session["worktree"], info["top"])
        self._ended_hooked(session)
        self.livemap.forget(session["id"])
        self.record.put_changes(session["id"], {}, [], [])   # its old folder's files are not its own in the new one
        self.deliverer.forget_session(session["id"])
        self._dirty_repos.add(session["repo_id"])
        self._push_dirty = True
        session.update(cwd=cwd or info["top"], worktree=info["top"], branch=info["branch"], base_commit=info["head"],
                       base_guessed=1)
        return True

    def session_for(self, sid, agent, line, event, ts):
        """The session a line belongs to, made from its first line in a watched worktree. A
        session passed over (its folder is not a git worktree of a watched repository) is looked
        at again whenever a line comes from another folder or names a file in one, so a session
        started in a parent folder counts once it works in a watched repository. One working in
        a repository that is not watched is counted in unwatched.json, by its repository only."""
        if sid not in self.ignored:
            session = self.sessions.get(sid) or self.record.session(sid)
            if session is not None:
                self.sessions[sid] = session
                return session
        cwd = line.get("cwd") if isinstance(line.get("cwd"), str) else ""
        passed = self.ignored.setdefault(sid, {})      # folder -> (why, common dir or None)
        info, unwatched_in = None, None
        for folder in _folders(line, cwd) or [""]:
            if folder not in passed:
                if len(passed) >= MAX_FOLDERS_LOOKED_AT:
                    continue
                passed[folder] = self._judge(folder, from_cwd=bool(cwd) and _same(folder, cwd))
                found = passed[folder]
                if found[0] is None:
                    info = found[2]
                    break
                self.log.info("ignoring a %s session: %s", agent, found[0])
            why, common_dir = passed[folder][:2]
            if why == "not watched" and unwatched_in is None:
                unwatched_in = (common_dir, folder)
        if info is None:
            self._count_unwatched(sid, unwatched_in, ts, ended=event == "SessionEnd")
            if event == "SessionEnd":
                del self.ignored[sid]
            return None
        if info.get("folder"):                         # a folder project: its base is a snapshot taken now
            try:
                info = dict(info, head=snapshots.take(self.state.root, info["top"]))
            except (snapshots.TooLarge, OSError) as exc:
                passed[info["top"]] = (f"too large to follow without git ({exc})", None, None)
                self.log.info("ignoring a %s session: %s", agent, passed[info["top"]][0])
                return None
        del self.ignored[sid]
        self._count_unwatched(sid, None, ts, ended=True)
        fresh = event == "SessionStart" and self.now_ns() - ts < FRESH_START_SECONDS * NS
        rid = self.record.repo_id(info["common_dir"])
        self._repo_keys[rid] = repo_key(info["common_dir"])
        session = {"id": sid, "agent": agent, "repo_id": rid, "cwd": cwd,
                   "worktree": info["top"], "branch": info["branch"], "base_commit": info["head"],
                   "base_guessed": 0 if fresh else 1, "started": ts, "last_seen": ts,
                   "state": "live", "turn": "", "note": ""}
        self.sessions[sid] = session
        self.log.info("session %s (%s) in %s, base %s%s", sid[:8], agent, info["top"], info["head"][:12] or "-",
                      "" if fresh else " (guessed)")
        return session

    def _judge(self, folder, from_cwd=False):
        """(why a session there is passed over or None, its project's key or None, the
        worktree's info when it counts). A session's own folder (`from_cwd`) that is no git
        worktree can be a folder project, followed through snapshots: its key is the folder."""
        if not folder:
            return "not a git worktree", None, None
        try:
            info = gitio.worktree_info(folder)
        except gitio.UntrustedRepositoryError:
            return "untrusted", None, None
        except gitio.GitError:
            info = None
        if info is None:
            ok = from_cwd and snapshots.can_follow(folder, self.state.root)[0]
            if not ok:
                return "not a git worktree", None, None
            if not config.is_watched(self.cfg, folder):
                return "not watched", folder, None
            return None, folder, {"top": folder, "common_dir": folder, "branch": "", "head": "", "folder": True}
        if not config.is_watched(self.cfg, info["common_dir"]):
            return "not watched", info["common_dir"], None
        return None, info["common_dir"], info

    def _count_unwatched(self, sid, where, ts, ended=False):
        """Count a passed-over session in the repository it works in (`where`: (common dir,
        folder)), or no longer (`ended`). Only the repository is kept, in memory and in
        unwatched.json; never the session's events."""
        if where is None and not ended:                 # a line from elsewhere: it is counted where it was
            return
        for key in [k for k, repo in self.unwatched.items() if sid in repo["sessions"]]:
            if ended or key != repo_key(where[0]):
                del self.unwatched[key]["sessions"][sid]
                if not self.unwatched[key]["sessions"]:
                    del self.unwatched[key]
                self._unwatched_dirty = True
        if ended:
            return
        common_dir, folder = where
        repo = self.unwatched.setdefault(repo_key(common_dir), {"common_dir": common_dir, "folder": folder,
                                                                "sessions": {}})
        if sid not in repo["sessions"]:
            self._unwatched_dirty = True
        repo["sessions"][sid] = max(ts, repo["sessions"].get(sid, 0))
        repo["folder"] = folder

    def expire_unwatched(self, now):
        """Stop counting sessions not heard from for the session timeout."""
        limit = now - self.cfg["session_timeout"] * NS
        for key in list(self.unwatched):
            sessions = self.unwatched[key]["sessions"]
            for sid in [s for s, seen in sessions.items() if seen < limit]:
                del sessions[sid]
                self.ignored.pop(sid, None)
                self._unwatched_dirty = True
            if not sessions:
                del self.unwatched[key]

    def write_unwatched(self, force=False):
        """Keep <state>/unwatched.json current: one entry per repository that is not watched and
        has sessions working in it now."""
        if not (force or self._unwatched_dirty):
            return
        self._unwatched_dirty = False
        repos = [{"common_dir": r["common_dir"], "name": repo_name(r["common_dir"]), "folder": r["folder"],
                  "sessions": len(r["sessions"])} for r in sorted(self.unwatched.values(),
                                                                   key=lambda r: r["common_dir"])]
        if not repos:
            _remove(self.state.unwatched)
            return
        tmp = self.state.unwatched + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"schema": 1, "repositories": repos}, fh)
            os.replace(tmp, self.state.unwatched)
        except OSError:
            self.log.exception("unwatched.json not written")

    @staticmethod
    def apply(session, event, line, ts):
        """The lifecycle: live until SessionEnd or a timeout; working or idle in between."""
        session["last_seen"] = max(session["last_seen"], ts)
        if event == "SessionEnd":
            reason = line.get("reason") if isinstance(line.get("reason"), str) else ""
            session["state"], session["note"] = "ended", ("ended: " + reason) if reason else "ended"
            session["turn"] = ""
            return
        if session["state"] != "live":
            session["state"], session["note"] = "live", ""
        if event in WORKING_EVENTS:
            session["turn"] = "working"
        elif event in IDLE_EVENTS:
            session["turn"] = "idle"

    # housekeeping
    def expire(self, now):
        """End live sessions with no event for longer than the session timeout, and the claims
        whose time ran out."""
        limit = now - self.cfg["session_timeout"] * NS
        stale = [s for s in self.sessions.values()        # a cloud branch lives as long as the branch does
                 if s["state"] == "live" and s["last_seen"] < limit and s["agent"] != CLOUD_AGENT]
        if stale:
            with self.record.transaction():
                for s in stale:
                    s["state"], s["note"], s["turn"] = "ended", "timed out", ""
                    self.record.put_session(s)
        for s in stale:
            del self.sessions[s["id"]]
            self.livemap.forget(s["id"])
            with self.record.transaction():
                self.deliverer.forget_session(s["id"])
            self._dirty_repos.add(s["repo_id"])
            self._push_dirty = True
            self._ended_hooked(s)
        with self.record.transaction():
            expired = self.record.expire_claims(now)
        for c in expired:
            self._dirty_repos.add(c["repo_id"])
            self._status_dirty = True
        for d in self.record.declarations_ended(now if self._last_expiry is None else self._last_expiry, now):
            if d["session_id"] in self.sessions:                         # it stopped holding: O7 again
                self._dirty_repos.add(self.sessions[d["session_id"]]["repo_id"])
                self._status_dirty = True
        self._last_expiry = now

    def clean_spool(self, now):
        """Delete spool files read to the end whose session is ignored, or ended more than a
        day ago, and orch's own day files read to the end and a day old."""
        gone = []
        for path, (_fid, offset) in list(self.reader.positions.items()):
            sid = os.path.splitext(os.path.basename(path))[0]
            if os.path.basename(os.path.dirname(path)) == feedback.SPOOL_AGENT:
                try:
                    if now - os.stat(path).st_mtime_ns < KEEP_ENDED_SPOOL_SECONDS * NS:
                        continue
                except FileNotFoundError:
                    pass
                except OSError:
                    continue
            elif sid in self.ignored:
                pass
            else:
                session = self.sessions.get(sid) or self.record.session(sid)
                if not session or session["state"] == "live" or now - session["last_seen"] < KEEP_ENDED_SPOOL_SECONDS * NS:
                    continue
            try:
                if os.stat(path).st_size != offset:
                    continue                                   # not read to the end yet
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                continue
            gone.append(path)
        if gone:
            with self.record.transaction():
                for path in gone:
                    self.reader.forget(path)
                    self.record.forget_position(path)

    def live_count(self, hooked_only=False):
        """Live sessions; with hooked_only, plain-git ones and cloud branches left out (they keep
        nothing awake)."""
        return sum(1 for s in self.sessions.values()
                   if s["state"] == "live" and not (hooked_only and s["agent"] in NO_NOTES))


def _storable(line):
    """Whether the record can store every text of a parsed spool line. JSON lets a string hold
    half of a surrogate pair (\\ud800), which UTF-8, and so SQLite, cannot."""
    try:
        json.dumps(line, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _record_failed(exc):
    """Whether an error is the record's own (disk full, I/O, a damaged file) or a lack of memory
    (sqlite3 reports SQLITE_NOMEM as a MemoryError), not one line's."""
    return isinstance(exc, (sqlite3.OperationalError, MemoryError)) or type(exc) is sqlite3.DatabaseError


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _size_and_time(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _folders(line, cwd):
    """The folders a spool line places its session in: its cwd, then the folder of the file it
    names (an agent started in a parent folder edits files of a repository below it)."""
    out = [os.path.normpath(cwd)] if cwd and os.path.isabs(cwd) else []
    path = line.get("file_path")
    if isinstance(path, str) and path:
        if not os.path.isabs(path):
            path = os.path.join(cwd, path) if out else ""
        folder = os.path.dirname(os.path.normpath(path)) if path else ""
        if folder and not any(_same(folder, f) for f in out):
            out.append(folder)
    return out


def _inside(path, top):
    """Whether `path` is `top` or inside it."""
    p, t = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(top))
    return p == t or p.startswith(t.rstrip(os.sep) + os.sep)


def _nested(folder, top):
    """Whether a folder inside `top` belongs to a worktree or repository nested in it (one made
    under the repository's own folder): a .git between the two."""
    folder = os.path.normpath(folder)
    while not _same(folder, top):
        if os.path.exists(os.path.join(folder, ".git")):
            return True
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent
    return False


def _logger(state):
    log = logging.getLogger("orchestraty.coordinator")
    log.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in log.handlers):
        os.makedirs(state.logs, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(os.path.join(state.logs, "coordinator.log"),
                                                       maxBytes=1 << 20, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    return log


def _write_info(state):
    info = {"pid": os.getpid(), "started": time.time(), "version": __version__, "python": sys.executable}
    tmp = state.info + ".orch-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    os.replace(tmp, state.info)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _not_started(state, log, exc):
    """A coordinator that could not open its record: why goes to the log, and no hook starts
    another for HOLD_OFF_SECONDS, which would fail the same way. Returns the exit code."""
    if isinstance(exc, SchemaError):
        log.error("%s", exc)
        code = NEWER_RECORD
    elif type(exc) is sqlite3.DatabaseError:                 # not a database, or damaged
        log.error("the record at %s cannot be read (%s): move it aside, with its -wal and -shm files, to start "
                  "a new record", state.record, exc)
        code = BAD_RECORD
    elif isinstance(exc, sqlite3.Error):                     # a folder, no access, read-only, ...
        log.error("the record at %s cannot be opened (%s): give your user read and write access to it, or move it "
                  "aside, with its -wal and -shm files, to start a new record", state.record, exc)
        code = BAD_RECORD
    else:
        log.exception("could not start")
        code = FAILED
    _hold_off(state)
    return code


def _hold_off(state, seconds=HOLD_OFF_SECONDS):
    """Keep the hooks from starting a coordinator for `seconds`. A hook starts none while
    coordinator.spawned is younger than its respawn gap, so its time is set that far ahead."""
    later = time.time() + seconds
    try:
        touch(state.spawned)
        os.utime(state.spawned, (later, later))
    except OSError:
        pass


def _end_hold_off(state):
    """The record opened: hooks may start a coordinator again as soon as this one leaves (while
    it runs, its heartbeat keeps them from starting one)."""
    now = time.time()
    try:
        if os.stat(state.spawned).st_mtime > now:
            os.utime(state.spawned, (now - HOLD_OFF_SECONDS, now - HOLD_OFF_SECONDS))
    except OSError:
        pass


def run(state, scan_seconds=SCAN_SECONDS):
    """Run the coordinator until it is idle or asked to stop. Returns an exit code."""
    lock = Lock(state.lock)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while not lock.acquire():                 # a coordinator that is leaving may still hold it
        if time.monotonic() > deadline:
            return ALREADY_RUNNING
        time.sleep(0.1)
    os.chdir(state.root)                      # never hold a worktree folder open
    log = _logger(state)
    heartbeat = Heartbeat(state.alive)
    heartbeat.start()
    _remove(state.stop)
    _write_info(state)
    coordinator = None
    try:
        try:
            coordinator = Coordinator(state, log=log)
        except Exception as exc:
            return _not_started(state, log, exc)
        _end_hold_off(state)
        log.info("started (pid %d, version %s)", os.getpid(), __version__)
        last_event = time.monotonic()
        failures = 0
        while True:
            if os.path.exists(state.stop):
                log.info("stop requested")
                break
            try:
                coordinator.reload_config()
                if coordinator.step():
                    last_event = time.monotonic()
                failures = 0
            except Exception:
                failures += 1
                log.exception("step failed (%d in a row)", failures)
                if failures >= MAX_FAILURES:       # a read-only or damaged record, a full disk: the next would fail too
                    log.error("leaving after %d failed steps; no hook starts another for %d s", failures,
                              HOLD_OFF_SECONDS)
                    _hold_off(state)
                    return FAILED
                time.sleep(1)
                continue
            if coordinator.upgraded:
                log.info("Orchestraty %s is installed now (this is %s): leaving, so the next agent event starts it",
                         coordinator.upgraded, __version__)
                break
            if (coordinator.live_count(hooked_only=True) == 0
                    and time.monotonic() - last_event > coordinator.cfg["idle_seconds"]):
                heartbeat.stop(remove=True)   # from here on, a hook starts a new coordinator
                if coordinator.step():         # ... unless something arrived meanwhile
                    last_event = time.monotonic()
                    heartbeat = Heartbeat(state.alive)
                    heartbeat.start()
                    continue
                log.info("idle: exiting")
                break
            time.sleep(scan_seconds)
        return OK
    finally:
        heartbeat.stop(remove=True)
        _remove(state.info)
        _remove(state.stop)
        if coordinator is not None:
            coordinator.close()
        lock.release()
