"""The coordinator: one process per user and environment, started on demand by a hook.

It holds the single-writer lock, touches the heartbeat, reads the spools every
``SCAN_SECONDS``, records the events of watched repositories, keeps each session's
lifecycle, and exits after ``idle_seconds`` with no live session and no new event.

A session is created from its first event: its worktree, repository, branch and base commit
(HEAD then) come from git in the event's folder. The base counts as guessed unless that first
event is a fresh SessionStart. Sessions outside watched repositories, or outside git, are
ignored: their spool lines are read and dropped, their spool files deleted once read, and
nothing about them is recorded.
"""
import json
import logging
import logging.handlers
import os
import sys
import time

from orchestraty import __version__, config, gitio
from orchestraty.coordinator.lock import Heartbeat, Lock
from orchestraty.coordinator.record import Record, SchemaError
from orchestraty.coordinator.spool import SpoolReader

SCAN_SECONDS = 0.15
HOUSEKEEPING_SECONDS = 10
FRESH_START_SECONDS = 60          # a SessionStart read later than this gives a guessed base
KEEP_ENDED_SPOOL_SECONDS = 24 * 3600
MAX_FAILURES = 20
NS = 1_000_000_000
WORKING_EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure")
IDLE_EVENTS = ("SessionStart", "Stop", "StopFailure")
DETAIL_FIELDS = ("source", "reason", "stop_hook_active", "len")
LOCK_WAIT_SECONDS = 3.0

# Exit codes of run()
OK, ALREADY_RUNNING, NEWER_RECORD, FAILED = 0, 3, 4, 5


class Coordinator:
    """The work of one coordinator process, one step at a time (the loop is in run())."""

    def __init__(self, state, now_ns=time.time_ns, log=None):
        self.state = state
        self.now_ns = now_ns
        self.log = log or logging.getLogger("orchestraty.coordinator")
        self.record = Record(state.record)
        self.reader = SpoolReader(state.spool, self.record.positions())
        self.sessions = {s["id"]: s for s in self.record.sessions(states=("live",))}
        self.ignored = {}                  # session id -> why (kept in memory only)
        self.cfg = dict(config.DEFAULTS)
        self._cfg_stamp = object()
        self.bad_lines = 0
        self._last_housekeeping = 0
        self.reload_config()

    def close(self):
        self.record.close()

    # configuration
    def reload_config(self):
        """Read config.json again if it changed. Sessions ignored as "not watched" get another
        look at their next event. Returns True when it changed."""
        try:
            stamp = os.stat(self.state.config).st_mtime_ns
        except OSError:
            stamp = None
        if stamp == self._cfg_stamp:
            return False
        self._cfg_stamp = stamp
        self.cfg = config.load(self.state)
        self.ignored = {sid: why for sid, why in self.ignored.items() if why != "not watched"}
        return True

    # one pass
    def step(self):
        """Read and record everything new in the spools; returns the number of lines read."""
        lines = self.reader.poll()
        if lines or self.reader.changed:
            with self.record.transaction():
                for agent, _path, raw in lines:
                    self.handle(agent, raw)
                self.record.save_positions(self.reader.take_changes())
        now = self.now_ns()
        if now - self._last_housekeeping >= HOUSEKEEPING_SECONDS * NS:
            self._last_housekeeping = now
            self.expire(now)
            self.clean_spool(now)
        return len(lines)

    def handle(self, agent, raw):
        try:
            line = json.loads(raw)
        except ValueError:
            self.bad_lines += 1
            return
        if not isinstance(line, dict):
            self.bad_lines += 1
            return
        ts = line.get("ts")
        if not isinstance(ts, int) or isinstance(ts, bool):
            ts = self.now_ns()
        if "shim_error" in line:
            self.record.add_event({"ts": ts, "agent": agent, "event": "shim_error"},
                                  {"error": str(line["shim_error"])[:64]})
            return
        sid = line.get("session_id")
        event = line.get("event") or line.get("hook_event_name") or ""
        if not isinstance(sid, str) or not sid or not isinstance(event, str):
            return
        session = self.session_for(sid, agent, line, event, ts)
        if session is None:
            return
        row = {c: line.get(c) if isinstance(line.get(c), str) else None
               for c in ("session_id", "tool_name", "tool_use_id", "cwd", "file_path")}
        row.update(ts=ts, agent=agent, event=event)
        self.record.add_event(row, {k: line[k] for k in DETAIL_FIELDS if k in line})
        self.apply(session, event, line, ts)
        self.record.put_session(session)

    def session_for(self, sid, agent, line, event, ts):
        if sid in self.ignored:
            return None
        session = self.sessions.get(sid) or self.record.session(sid)
        if session is not None:
            self.sessions[sid] = session
            return session
        cwd = line.get("cwd") if isinstance(line.get("cwd"), str) else ""
        try:
            info = gitio.worktree_info(cwd)
        except gitio.UntrustedRepositoryError:
            info, why = None, "untrusted"
        else:
            why = "not a git worktree" if info is None else None
        if info is not None and not config.is_watched(self.cfg, info["common_dir"]):
            why = "not watched"
        if why:
            self.ignored[sid] = why
            self.log.info("ignoring a %s session: %s", agent, why)
            return None
        fresh = event == "SessionStart" and self.now_ns() - ts < FRESH_START_SECONDS * NS
        session = {"id": sid, "agent": agent, "repo_id": self.record.repo_id(info["common_dir"]), "cwd": cwd,
                   "worktree": info["top"], "branch": info["branch"], "base_commit": info["head"],
                   "base_guessed": 0 if fresh else 1, "started": ts, "last_seen": ts,
                   "state": "live", "turn": "", "note": ""}
        self.sessions[sid] = session
        self.log.info("session %s (%s) in %s, base %s%s", sid[:8], agent, info["top"], info["head"][:12] or "-",
                      "" if fresh else " (guessed)")
        return session

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
        """End live sessions with no event for longer than the session timeout."""
        limit = now - self.cfg["session_timeout"] * NS
        stale = [s for s in self.sessions.values() if s["state"] == "live" and s["last_seen"] < limit]
        if stale:
            with self.record.transaction():
                for s in stale:
                    s["state"], s["note"], s["turn"] = "ended", "timed out", ""
                    self.record.put_session(s)
        for s in stale:
            del self.sessions[s["id"]]

    def clean_spool(self, now):
        """Delete spool files read to the end whose session is ignored, or ended more than a
        day ago."""
        gone = []
        for path, (_fid, offset) in list(self.reader.positions.items()):
            sid = os.path.splitext(os.path.basename(path))[0]
            if sid in self.ignored:
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

    def live_count(self):
        return sum(1 for s in self.sessions.values() if s["state"] == "live")


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
        coordinator = Coordinator(state, log=log)
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
                if failures >= MAX_FAILURES:
                    return FAILED
                time.sleep(1)
                continue
            if coordinator.live_count() == 0 and time.monotonic() - last_event > coordinator.cfg["idle_seconds"]:
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
    except SchemaError as exc:
        log.error("%s", exc)
        return NEWER_RECORD
    finally:
        heartbeat.stop(remove=True)
        _remove(state.info)
        _remove(state.stop)
        if coordinator is not None:
            coordinator.close()
        lock.release()
