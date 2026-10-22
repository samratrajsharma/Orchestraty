"""The helper's pass check: a homemade agent (examples/homemade-agent) uses it for two sessions,
through the installed hook script, with a real coordinator that the hook starts, in an
isolated state folder; the session about to edit a caller hears of the breaking change first."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orchestraty import config
from orchestraty.capture import install
from orchestraty.coordinator.lock import Lock, touch
from orchestraty.coordinator.record import Record
from orchestraty.paths import State

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "examples" / "homemade-agent" / "agent.py"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def caught_up(state, seconds=60.0):
    """Wait until the coordinator has read the whole run: every session of the agent ended."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.path.exists(state.record):
            record = Record(state.record, readonly=True)
            try:
                mine = [s for s in record.sessions() if s["agent"] == "helper-homemade"]
            finally:
                record.close()
            if len(mine) == 2 and all(s["state"] == "ended" for s in mine):
                return True
        time.sleep(0.1)
    return False


def stopped(state, seconds=30.0):
    """Ask the coordinator to stop, and wait until it has."""
    lock = Lock(state.lock)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if lock.acquire():
            lock.release()
            return True
        touch(state.stop)
        time.sleep(0.1)
    return False


def test_a_homemade_agent_hears_before_it_edits_a_caller(state, hook_script, git, tmp_path):
    install.write_launch(state)
    env = dict(os.environ, ORCH_HOME=state.root, PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=os.pathsep.join([str(ROOT / "orchestraty"), str(ROOT / "engine")]))
    env.pop("ORCH_NO_COORDINATOR", None)                 # the hook starts the coordinator, as for any agent
    try:
        proc = subprocess.run([sys.executable, str(AGENT), str(tmp_path / "demo")], env=env, capture_output=True,
                              cwd=str(tmp_path), stdin=subprocess.DEVNULL, timeout=300)
        assert caught_up(state), "the coordinator did not read the whole run"
    finally:
        assert stopped(state), "the coordinator did not stop"
    out = proc.stdout.decode("utf-8")
    assert proc.returncode == 0, out + proc.stderr.decode("utf-8", "replace")
    told_b = out.split("B, before writing b.py, hears:\n", 1)[1].split("\n", 1)[0]
    assert ("another helper-homemade session (worktree wt-a, branch feat-a) changed the contract of charge in a.py: "
            "parameter currency can no longer be passed by position.") in told_b
    assert "b.py uses it: pay (line 5). Check these uses before you change this file" in told_b
    assert "passing the arguments by name works with both forms" in told_b
    told_a = out.split("A, at the end of its turn, hears:\n", 1)[1].split("\n", 1)[0]
    assert "These callers still use it and no session has updated them: pay in b.py (line 5)." in told_a
    assert "A, before writing a.py, hears nothing." in out
    # What the record says: two sessions of the helper's agent; B's note taken before its edit.
    record = Record(state.record, readonly=True)
    try:
        sessions = record.sessions()
        assert {s["agent"] for s in sessions if not s["id"].startswith("git-")} == {"helper-homemade"}
        [b] = [s for s in sessions if s["worktree"].endswith("wt-b") and s["agent"] == "helper-homemade"]
        [edit] = [n for n in record.notes(session_id=b["id"]) if n["kind"] == "edit"]
        assert edit["state"] == "delivered" and edit["finding_key"].startswith("O3|a.py::charge|")
        writes = [e for e in record.events(b["id"]) if e["event"] == "PostToolUse"]
        assert writes and edit["delivered"] < writes[0]["ts"]                   # heard before it wrote
    finally:
        record.close()
    assert config.load(state)["repos"] == []                                    # unwatched at the end
