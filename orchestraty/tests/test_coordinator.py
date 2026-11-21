"""The coordinator: sessions from spool lines, the lifecycle, housekeeping, the process, and
the hook starting it."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers import hook_payload, run_hook, spool_event
from orchestraty import cli, config
from orchestraty.capture import hook, install
from orchestraty.coordinator import main as cmain
from orchestraty.coordinator.lock import Lock, touch
from orchestraty.paths import State

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = PACKAGE_ROOT.parent / "engine"
NS = 1_000_000_000


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def watched(state, repo):
    config.watch(state, str(repo / ".git"))
    return repo


@pytest.fixture
def coordinator(state):
    made = []

    def make(**kw):
        c = cmain.Coordinator(state, **kw)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def test_a_session_in_a_watched_repository_is_recorded(state, watched, git, coordinator):
    spool_event(state.root, "s1", "SessionStart", watched, source="startup")
    spool_event(state.root, "s1", "PostToolUse", watched, tool_name="Edit", tool_use_id="t1",
                file_path=str(watched / "a.py"))
    c = coordinator()
    assert c.step() == 2
    s = c.record.session("s1")
    assert same(s["worktree"], watched) and s["branch"] == "main"
    assert s["base_commit"] == git(watched, "rev-parse", "HEAD") and s["base_guessed"] == 0
    assert s["state"] == "live" and s["turn"] == "working"
    events = c.record.events("s1")
    assert [e["event"] for e in events] == ["SessionStart", "PostToolUse"]
    assert events[1]["file_path"] == str(watched / "a.py") and events[1]["tool_name"] == "Edit"
    assert json.loads(events[0]["detail"]) == {"len": 1, "source": "startup"}
    assert c.step() == 0                                   # nothing read twice


def test_two_worktrees_of_one_repository_are_one_repository(state, watched, git, tmp_path, coordinator):
    git(watched, "worktree", "add", "-q", "-b", "feat-b", str(tmp_path / "wt-b"))
    spool_event(state.root, "a", "SessionStart", watched)
    spool_event(state.root, "b", "SessionStart", tmp_path / "wt-b" )
    c = coordinator()
    c.step()
    a, b = c.record.session("a"), c.record.session("b")
    assert a["repo_id"] == b["repo_id"] and len(c.record.repos()) == 1
    assert same(b["worktree"], tmp_path / "wt-b") and b["branch"] == "feat-b"


def test_sessions_outside_watched_repositories_leave_no_trace(state, repo, tmp_path, coordinator):
    plain = tmp_path / "plain"
    plain.mkdir()
    spool_event(state.root, "unwatched", "SessionStart", repo)
    spool_event(state.root, "nogit", "SessionStart", plain)
    c = coordinator()
    assert c.step() == 2
    assert c.record.events() == [] and c.record.sessions() == [] and c.record.repos() == []
    assert {sid: [why for why, *_ in folders.values()] for sid, folders in c.ignored.items()} == {
        "unwatched": ["not watched"], "nogit": ["not watched"]}           # a folder without git can be watched too
    # Only the project each works in is counted, for orch status and orch ui: no id, no event.
    kept = json.loads(open(state.unwatched, encoding="utf-8").read())
    assert kept["schema"] == 1 and len(kept["repositories"]) == 2
    named = {r["name"]: r for r in kept["repositories"]}
    r = named["repo"]
    assert sorted(r) == ["common_dir", "folder", "name", "sessions"] and r["sessions"] == 1
    assert same(r["common_dir"], repo / ".git") and same(r["folder"], repo)
    assert same(named["plain"]["common_dir"], plain) and same(named["plain"]["folder"], plain)
    # Watching the repository later: the session is picked up at its next event.
    config.watch(state, str(repo / ".git"))
    os.utime(state.config, (time.time() + 5, time.time() + 5))     # a visibly newer file
    assert c.reload_config()
    spool_event(state.root, "unwatched", "UserPromptSubmit", repo)
    c.step()
    s = c.record.session("unwatched")
    assert s and s["base_guessed"] == 1                     # first seen after its start
    left = json.loads(open(state.unwatched, encoding="utf-8").read())["repositories"]
    assert [r["name"] for r in left] == ["plain"]           # the repository is no longer listed as not watched


def test_a_session_started_in_a_parent_folder_counts_once_it_works_in_a_watched_repository(
        state, watched, tmp_path, coordinator):
    parent = tmp_path / "projects"
    parent.mkdir()
    spool_event(state.root, "p1", "SessionStart", parent)
    spool_event(state.root, "p2", "SessionStart", parent)
    c = coordinator()
    c.step()
    assert c.record.sessions() == []
    # One edits a file of the watched repository by its full path, from the parent folder ...
    spool_event(state.root, "p1", "PostToolUse", parent, tool_name="Edit", file_path=str(watched / "a.py"))
    # ... the other moved into it.
    spool_event(state.root, "p2", "UserPromptSubmit", watched)
    c.step()
    p1, p2 = c.record.session("p1"), c.record.session("p2")
    assert same(p1["worktree"], watched) and same(p2["worktree"], watched)
    assert p1["base_guessed"] == 1 and p1["turn"] == "working"
    assert c.record.events("p1")[0]["file_path"] == str(watched / "a.py")


def test_counted_sessions_of_a_repository_not_watched_end_and_time_out(state, repo, coordinator):
    spool_event(state.root, "u1", "SessionStart", repo)
    spool_event(state.root, "u2", "SessionStart", repo)
    c = coordinator()
    c.step()
    assert json.loads(open(state.unwatched, encoding="utf-8").read())["repositories"][0]["sessions"] == 2
    spool_event(state.root, "u1", "SessionEnd", repo, reason="exit")
    c.step()
    assert json.loads(open(state.unwatched, encoding="utf-8").read())["repositories"][0]["sessions"] == 1
    c.expire_unwatched(time.time_ns() + (c.cfg["session_timeout"] + 1) * 1_000_000_000)
    c.write_unwatched()
    assert not os.path.exists(state.unwatched) and "u2" not in c.ignored
    spool_event(state.root, "u3", "SessionStart", repo)
    c.step()
    assert os.path.exists(state.unwatched)
    c.close()                                                # a stopped coordinator counts nothing
    assert not os.path.exists(state.unwatched)


def test_the_lifecycle(state, watched, coordinator):
    c = coordinator()
    spool_event(state.root, "s1", "SessionStart", watched)
    c.step()
    assert c.record.session("s1")["turn"] == "idle"
    spool_event(state.root, "s1", "UserPromptSubmit", watched)
    c.step()
    assert c.record.session("s1")["turn"] == "working"
    spool_event(state.root, "s1", "Stop", watched, stop_hook_active=False)
    c.step()
    assert c.record.session("s1")["turn"] == "idle"
    spool_event(state.root, "s1", "SessionEnd", watched, reason="clear")
    c.step()
    s = c.record.session("s1")
    assert (s["state"], s["note"]) == ("ended", "ended: clear") and c.live_count() == 0
    spool_event(state.root, "s1", "UserPromptSubmit", watched)       # resumed
    c.step()
    assert c.record.session("s1")["state"] == "live" and c.live_count() == 1


def test_a_silent_session_times_out(state, watched, coordinator):
    now = [time.time_ns()]
    c = coordinator(now_ns=lambda: now[0])
    spool_event(state.root, "s1", "SessionStart", watched, ts=now[0])
    c.step()
    now[0] += (config.DEFAULTS["session_timeout"] + 60) * NS
    c.step()
    s = c.record.session("s1")
    assert (s["state"], s["note"]) == ("ended", "timed out") and c.live_count() == 0


def test_a_late_session_start_gives_a_guessed_base(state, watched, coordinator):
    spool_event(state.root, "s1", "SessionStart", watched, ts=time.time_ns() - 3600 * NS)
    c = coordinator()
    c.step()
    assert c.record.session("s1")["base_guessed"] == 1


def test_bad_lines_and_hook_errors(state, watched, coordinator):
    folder = Path(state.spool) / "claude"
    folder.mkdir(parents=True)
    (folder / "s1.jsonl").write_bytes(b'not json\n[1]\n{"ts":5,"pid":1,"agent":"claude","shim_error":"OSError"}\n'
                                      b'{"ts":6,"agent":"claude","event":"Stop"}\n')
    c = coordinator()
    assert c.step() == 4
    assert c.bad_lines == 2
    [error] = c.record.events()
    assert error["event"] == "shim_error" and json.loads(error["detail"]) == {"error": "OSError"}


def test_a_restarted_coordinator_neither_loses_nor_repeats(state, watched, coordinator):
    spool_event(state.root, "s1", "SessionStart", watched)
    first = coordinator()
    first.step()
    first.close()
    spool_event(state.root, "s1", "UserPromptSubmit", watched)
    second = coordinator()
    assert second.step() == 1
    assert [e["event"] for e in second.record.events("s1")] == ["SessionStart", "UserPromptSubmit"]
    assert second.live_count() == 1


def test_spool_files_of_ignored_and_long_ended_sessions_are_deleted(state, repo, watched, tmp_path, coordinator):
    plain = tmp_path / "plain"
    plain.mkdir()
    ignored = spool_event(state.root, "nogit", "SessionStart", plain)
    ended = spool_event(state.root, "old", "SessionEnd", watched, reason="other")
    live = spool_event(state.root, "live", "SessionStart", watched)
    now = [time.time_ns()]
    c = coordinator(now_ns=lambda: now[0])
    c.step()
    assert not ignored.exists() and ended.exists() and live.exists()
    unread = spool_event(state.root, "old2", "SessionEnd", watched, reason="other")
    c.step()
    with open(unread, "ab") as fh:
        fh.write(b'{"ts": 1, "event": "Sto')                    # a line still being written
    now[0] += (cmain.KEEP_ENDED_SPOOL_SECONDS + 60) * NS
    spool_event(state.root, "live", "UserPromptSubmit", watched, ts=now[0])   # still at work a day later
    c.step()
    assert not ended.exists() and live.exists()
    assert unread.exists()                                      # not read to its end: kept
    assert set(c.record.positions()) == {str(live), str(unread)}


# --------------------------------------------------------------------------------------- #
# The process
# --------------------------------------------------------------------------------------- #
def child_env(state, **extra):
    env = dict(os.environ, ORCH_HOME=state.root, PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=os.pathsep.join([str(PACKAGE_ROOT), str(ENGINE_ROOT)]))
    env.update(extra)
    return env


def fast_config(state, **cfg):
    data = dict(config.DEFAULTS, idle_seconds=1, session_timeout=3)
    data.update(cfg)
    os.makedirs(state.root, exist_ok=True)
    with open(state.config, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def wait_for(condition, seconds=20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


def lock_free(state):
    lock = Lock(state.lock)
    if lock.acquire():
        lock.release()
        return True
    return False


def stop_coordinator(state):
    if lock_free(state):
        return
    touch(state.stop)
    assert wait_for(lambda: lock_free(state), 15), "the coordinator did not stop"


def test_the_process_records_then_exits_when_idle(state, watched):
    fast_config(state, repos=[str(watched / ".git")])
    spool_event(state.root, "s1", "SessionStart", watched)
    spool_event(state.root, "s1", "SessionEnd", watched, reason="other")
    proc = subprocess.Popen([sys.executable, "-m", "orchestraty", "coordinator", "run"], env=child_env(state),
                            cwd=str(PACKAGE_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.wait(timeout=60) == cmain.OK, proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
    assert not os.path.exists(state.alive) and not os.path.exists(state.info)
    log = Path(state.logs, "coordinator.log").read_text(encoding="utf-8")
    assert "started" in log and "idle: exiting" in log


def test_only_one_coordinator_runs_and_stop_works(state):
    fast_config(state, idle_seconds=600)
    first = subprocess.Popen([sys.executable, "-m", "orchestraty", "coordinator", "run"], env=child_env(state),
                             cwd=str(PACKAGE_ROOT))
    try:
        assert wait_for(lambda: os.path.exists(state.info))
        second = subprocess.run([sys.executable, "-m", "orchestraty", "coordinator", "run"], env=child_env(state),
                                cwd=str(PACKAGE_ROOT), timeout=60)
        assert second.returncode == cmain.ALREADY_RUNNING
        assert cli.main(["coordinator", "stop"]) == 0
        assert first.wait(timeout=30) == cmain.OK
        assert cli.main(["coordinator", "stop"]) == 0          # "not running"
    finally:
        if first.poll() is None:
            first.kill()


def test_the_hook_starts_the_coordinator_once(state, watched, hook_script, monkeypatch):
    fast_config(state, repos=[str(watched / ".git")])
    install.write_launch(state)
    monkeypatch.delenv("ORCH_NO_COORDINATOR")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    try:
        proc = run_hook(hook_script, ["claude", "SessionStart"], hook_payload("SessionStart", "s1", watched))
        assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
        assert os.path.exists(state.spawned)
        assert wait_for(lambda: os.path.exists(state.info)), "no coordinator started"
        assert wait_for(lambda: os.path.exists(state.record) and _session_seen(state, "s1"))
        spawned_at = os.stat(state.spawned).st_mtime
        run_hook(hook_script, ["claude", "Stop"], hook_payload("Stop", "s1", watched))
        assert os.stat(state.spawned).st_mtime == spawned_at   # running: no second start
        info = json.loads(Path(state.info).read_text(encoding="utf-8"))
        assert info["pid"] != os.getpid()
    finally:
        stop_coordinator(state)


def test_a_repository_cannot_plant_a_module_in_the_coordinator(state, watched, hook_script, monkeypatch,
                                                               tmp_path):
    # The agent's folder is someone's repository. A package named like ours there must never
    # be imported: the coordinator is started from the state folder, not the agent's folder.
    marker = tmp_path / "planted-code-ran"
    planted = watched / "orchestraty"
    planted.mkdir()
    (planted / "__init__.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    (planted / "__main__.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    fast_config(state, repos=[str(watched / ".git")])
    install.write_launch(state)
    monkeypatch.delenv("ORCH_NO_COORDINATOR")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))          # an outer git's variable
    try:
        run_hook(hook_script, ["claude", "SessionStart"], hook_payload("SessionStart", "s1", watched), cwd=watched)
        assert wait_for(lambda: os.path.exists(state.record) and _session_seen(state, "s1"))
    finally:
        stop_coordinator(state)
    assert not marker.exists()


def _session_seen(state, sid):
    from orchestraty.coordinator.record import Record
    rec = Record(state.record, readonly=True)
    try:
        return rec.session(sid) is not None
    except Exception:
        return False
    finally:
        rec.close()


def test_start_up_is_skipped_without_a_launch_file_or_within_the_respawn_gap(state, monkeypatch):
    monkeypatch.delenv("ORCH_NO_COORDINATOR")
    os.makedirs(state.hooks)
    now = time.time()
    assert not hook.start_coordinator(state.root, now)                 # no launch file
    assert not os.path.exists(state.spawned)
    with open(state.launch, "w", encoding="utf-8") as fh:
        fh.write("python\n\n")                                         # not an absolute path
    assert not hook.start_coordinator(state.root, now)
    touch(state.spawned)
    install.write_launch(state)
    assert not hook.start_coordinator(state.root, time.time())        # a hook tried a moment ago
    touch(state.alive)
    assert hook.coordinator_alive(state.root, time.time())
    os.utime(state.alive, (now - 60, now - 60))
    assert not hook.coordinator_alive(state.root, time.time())


def test_the_launch_file_names_this_interpreter_and_the_import_path(state):
    install.write_launch(state)
    python, path = Path(state.launch).read_text(encoding="utf-8").splitlines()
    assert python == sys.executable
    parts = [os.path.normcase(p) for p in path.split(os.pathsep)]
    assert os.path.normcase(str(PACKAGE_ROOT)) in parts and os.path.normcase(str(ENGINE_ROOT)) in parts


@pytest.mark.skipif(os.name != "nt", reason="Windows command lines")
def test_windows_command_lines_quote_like_subprocess():
    import subprocess as sp
    for argv in (["C:\\Program Files\\Python\\python.exe", "-m", "orchestraty"], ["a b", 'c"d', "e\\", "", "f\\\\\"g"]):
        assert hook.command_line(argv) == sp.list2cmdline(argv)


# --------------------------------------------------------------------------------------- #
# One line that cannot be recorded, and a record that cannot be read
# --------------------------------------------------------------------------------------- #
def test_a_line_with_half_a_surrogate_pair_is_set_aside_and_the_others_kept(state, watched, coordinator):
    spool_event(state.root, "s1", "SessionStart", watched)
    poisoned = spool_event(state.root, "s2", "PostToolUse", watched, tool_name="Edit",
                           file_path=str(watched / "POISON.py"))
    poisoned.write_bytes(poisoned.read_bytes().replace(b"POISON", b"\\ud800"))     # valid JSON, no UTF-8 text
    spool_event(state.root, "s3", "SessionStart", watched)
    c = coordinator()
    assert c.step() == 3 and c.bad_lines == 1
    assert sorted(s["id"] for s in c.record.sessions()) == ["s1", "s3"] == sorted(c.sessions)
    assert sorted(e["session_id"] for e in c.record.events()) == ["s1", "s3"]
    assert c.step() == 0                                   # set aside for good, not read again


def test_a_line_that_cannot_be_recorded_loses_only_itself(state, watched, coordinator, monkeypatch):
    for sid in ("s1", "s2", "s3"):
        spool_event(state.root, sid, "SessionStart", watched)
    c = coordinator()
    add_event = c.record.add_event

    def add_event_but_s2(line, detail=None):
        if line.get("session_id") == "s2":
            raise ValueError("a line the record cannot take")
        return add_event(line, detail)
    monkeypatch.setattr(c.record, "add_event", add_event_but_s2)
    assert c.step() == 3 and c.bad_lines == 1
    assert sorted(s["id"] for s in c.record.sessions()) == ["s1", "s3"] == sorted(c.sessions)
    assert "s2" not in c.livemap.sessions                  # a session only that line made is not known either


def test_lines_of_a_step_the_record_failed_are_read_again(state, watched, coordinator, monkeypatch):
    import sqlite3
    spool_event(state.root, "s1", "SessionStart", watched)
    c = coordinator()
    save = c.record.save_positions

    def disk_full(changes):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(c.record, "save_positions", disk_full)
    with pytest.raises(sqlite3.OperationalError):
        c.step()
    assert c.record.sessions() == [] and c.record.positions() == {}
    monkeypatch.setattr(c.record, "save_positions", save)
    assert c.step() == 1                                   # read again, not lost
    assert [e["event"] for e in c.record.events("s1")] == ["SessionStart"]


def test_a_record_that_cannot_be_read_is_said_once_and_the_hooks_hold_off(state, watched):
    fast_config(state, repos=[str(watched / ".git")])
    Path(state.record).write_bytes(b"GARBAGE!" * 512)
    proc = subprocess.run([sys.executable, "-m", "orchestraty", "coordinator", "run"], env=child_env(state),
                          cwd=str(PACKAGE_ROOT), capture_output=True, timeout=60)
    assert proc.returncode == cmain.BAD_RECORD and proc.stderr == b""
    [line] = Path(state.logs, "coordinator.log").read_text(encoding="utf-8").splitlines()
    assert "cannot be read (file is not a database): move it aside" in line
    now = time.time()
    assert os.stat(state.spawned).st_mtime > now + cmain.HOLD_OFF_SECONDS - 30      # a hook starts none meanwhile
    os.replace(state.record, state.record + ".damaged")
    spool_event(state.root, "s1", "SessionEnd", watched, reason="other")
    proc = subprocess.run([sys.executable, "-m", "orchestraty", "coordinator", "run"], env=child_env(state),
                          cwd=str(PACKAGE_ROOT), capture_output=True, timeout=60)
    assert proc.returncode == cmain.OK, proc.stderr
    assert os.stat(state.spawned).st_mtime < time.time() - hook.RESPAWN_SECONDS      # the next hook may start one


def test_a_record_that_cannot_be_opened_at_all_is_said_once_too(state, watched):
    fast_config(state, repos=[str(watched / ".git")])
    os.makedirs(state.record)                              # a folder where the record should be
    proc = subprocess.run([sys.executable, "-m", "orchestraty", "coordinator", "run"], env=child_env(state),
                          cwd=str(PACKAGE_ROOT), capture_output=True, timeout=60)
    assert proc.returncode == cmain.BAD_RECORD and proc.stderr == b""
    [line] = Path(state.logs, "coordinator.log").read_text(encoding="utf-8").splitlines()
    assert f"the record at {state.record} cannot be opened (" in line and "move it aside" in line
    assert os.stat(state.spawned).st_mtime > time.time() + cmain.HOLD_OFF_SECONDS - 30


def test_a_record_that_cannot_be_written_holds_the_hooks_off_too(state, watched):
    """A record that opens and then fails every step (read-only here; a damaged page or a full
    disk do the same): the coordinator leaves after MAX_FAILURES steps in a row, and no hook
    starts another for a minute, which would fail the same way."""
    import stat
    from orchestraty.coordinator.record import Record
    fast_config(state, repos=[str(watched / ".git")])
    spool_event(state.root, "s1", "SessionStart", watched)
    Record(state.record).close()
    os.chmod(state.record, stat.S_IREAD)
    try:
        if os.access(state.record, os.W_OK):
            pytest.skip("this user writes read-only files")
        run = ("import sys; from orchestraty.coordinator import main; from orchestraty.paths import State; "
               "main.MAX_FAILURES = 2; sys.exit(main.run(State()))")
        proc = subprocess.run([sys.executable, "-c", run], env=child_env(state), cwd=str(PACKAGE_ROOT),
                              capture_output=True, timeout=60)
        assert proc.returncode == cmain.FAILED, proc.stderr
        assert "readonly database" in Path(state.logs, "coordinator.log").read_text(encoding="utf-8")
        assert os.stat(state.spawned).st_mtime > time.time() + cmain.HOLD_OFF_SECONDS - 30
    finally:
        os.chmod(state.record, stat.S_IREAD | stat.S_IWRITE)


def test_a_line_that_runs_out_of_memory_fails_the_step_and_is_read_again(state, watched, coordinator, monkeypatch):
    """sqlite3 reports SQLITE_NOMEM as a MemoryError: a lack of memory, not the line's fault."""
    for sid in ("s1", "s2"):
        spool_event(state.root, sid, "SessionStart", watched)
    c = coordinator()
    add_event = c.record.add_event

    def no_memory_for_s2(line, detail=None):
        if line.get("session_id") == "s2":
            raise MemoryError()
        return add_event(line, detail)
    monkeypatch.setattr(c.record, "add_event", no_memory_for_s2)
    with pytest.raises(MemoryError):
        c.step()
    assert c.bad_lines == 0 and c.record.sessions() == []
    monkeypatch.setattr(c.record, "add_event", add_event)
    assert c.step() == 2                                   # read again, not set aside
    assert sorted(s["id"] for s in c.record.sessions()) == ["s1", "s2"]


def test_a_failed_step_is_read_again_without_asking_the_record_where(state, watched, coordinator, monkeypatch):
    """The rewind goes back to where the reader stood before the step, which is what the record
    saved: asking the record could fail the same way the step did."""
    import sqlite3
    spool_event(state.root, "s1", "SessionStart", watched)
    c = coordinator()
    save, positions = c.record.save_positions, c.record.positions

    def io_error(*args):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(c.record, "save_positions", io_error)
    monkeypatch.setattr(c.record, "positions", io_error)
    with pytest.raises(sqlite3.OperationalError):
        c.step()
    monkeypatch.setattr(c.record, "save_positions", save)
    monkeypatch.setattr(c.record, "positions", positions)
    assert c.step() == 1                                   # read again, not skipped
    assert [e["event"] for e in c.record.events("s1")] == ["SessionStart"]


def _tree(c, folder):
    [key] = [k for k in c._trees if same(k, folder)]
    return c._trees[key]


def _due_now(c):
    """The timer is due, and so is every worktree (as if their time had come)."""
    c._last_scan = float("-inf")
    for seen in c._trees.values():
        seen["next"] = 0


def test_a_worktree_that_stays_the_same_is_looked_at_less_often(state, watched, git, tmp_path, coordinator):
    idle = tmp_path / "idle"
    git(watched, "worktree", "add", "-q", "-b", "idle", str(idle))
    spool_event(state.root, "s1", "SessionStart", watched)
    c = coordinator()
    c.step()
    every = c.cfg["level0_seconds"]
    assert _tree(c, idle)["every"] == every
    for expected in (2 * every, 4 * every, 8 * every, cmain.LEVEL0_MAX_SECONDS, cmain.LEVEL0_MAX_SECONDS):
        _due_now(c)
        c.step()
        assert _tree(c, idle)["every"] == min(expected, cmain.LEVEL0_MAX_SECONDS)
    # Not due: the next pass leaves it alone, even with a change in it ...
    (idle / "new.py").write_text("x = 1\n", encoding="utf-8")
    c._last_scan = float("-inf")
    c.step()
    assert not [s for s in c.record.sessions() if s["agent"] == "git"]
    # ... until its time comes: then it is a plain-git session, looked at every level0_seconds again.
    _due_now(c)
    c.step()
    [plain] = [s for s in c.record.sessions() if s["agent"] == "git"]
    assert same(plain["worktree"], idle) and _tree(c, idle)["every"] == every


def test_a_pass_looks_at_a_share_of_the_worktrees(state, watched, git, tmp_path, coordinator, monkeypatch):
    for k in range(5):
        git(watched, "worktree", "add", "-q", "-b", f"w{k}", str(tmp_path / f"w{k}"))
    monkeypatch.setattr(cmain, "LEVEL0_BUDGET_SECONDS", 0)
    monkeypatch.setattr(cmain, "LEVEL0_MIN_PER_PASS", 2)
    spool_event(state.root, "s1", "SessionStart", watched)
    c = coordinator()
    c.step()                                    # the hooked session's own worktree is not queued
    assert len(c._trees) == 2 and len(c._tree_queue) == 3
    spool_event(state.root, "s2", "SessionStart", tmp_path / "w4")
    c.step()                                    # read at once, between shares of the queue
    assert c.record.session("s2") is not None
    c.step()
    assert not c._tree_queue and len(c._trees) == 5
    c._last_scan = None                         # a full pass: every worktree at once
    c.step()
    assert not c._tree_queue


def test_the_refresh_file_makes_every_worktree_due(state, watched, git, tmp_path, coordinator):
    idle = tmp_path / "idle"
    git(watched, "worktree", "add", "-q", "-b", "idle", str(idle))
    spool_event(state.root, "s1", "SessionStart", watched)
    c = coordinator()
    c.step()
    for _ in range(3):
        _due_now(c)
        c.step()
    assert _tree(c, idle)["every"] > c.cfg["level0_seconds"]
    (idle / "new.py").write_text("x = 1\n", encoding="utf-8")
    open(state.rescan, "w").close()             # orch ui's Refresh
    c.step()
    assert not os.path.exists(state.rescan)
    [plain] = [s for s in c.record.sessions() if s["agent"] == "git"]
    assert same(plain["worktree"], idle)
