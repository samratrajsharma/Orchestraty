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
    assert c.ignored == {"unwatched": "not watched", "nogit": "not a git worktree"}
    # Watching the repository later: the session is picked up at its next event.
    config.watch(state, str(repo / ".git"))
    os.utime(state.config, (time.time() + 5, time.time() + 5))     # a visibly newer file
    assert c.reload_config()
    spool_event(state.root, "unwatched", "UserPromptSubmit", repo)
    c.step()
    s = c.record.session("unwatched")
    assert s and s["base_guessed"] == 1                     # first seen after its start


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
