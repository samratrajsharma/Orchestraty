"""The smoke scenario of scripts/smoke.py, from the source tree: what CI runs on the built wheel
with the installed orch, here with python -m orchestraty. Two scripted sessions, played through
the hook entries orch init installs, with a real coordinator that the first hook starts; the
second session hears of the first one's change before it edits."""
import importlib.util
import os
import sys
from pathlib import Path

from orchestraty.coordinator.lock import Lock

ROOT = Path(__file__).resolve().parents[2]


def load_smoke():
    spec = importlib.util.spec_from_file_location("orch_smoke", ROOT / "scripts" / "smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_smoke_scenario_reaches_a_note(tmp_path):
    smoke = load_smoke()
    folder = tmp_path / "smoke"
    folder.mkdir()
    env = smoke.isolated_env(str(folder), pythonpath=os.pathsep.join([str(ROOT / "orchestraty"), str(ROOT / "engine")]))
    said = []
    steps = smoke.Steps(say=said.append)
    note = smoke.smoke([sys.executable, "-m", "orchestraty"], str(folder), env, steps)
    assert "changed the contract of charge in a.py: parameter currency can no longer be passed by position" in note
    assert "b.py uses it: pay (line 5)" in note
    assert [name for name, _ in steps.taken] == [
        "orch --version", "orch init --print", "a repository with two worktrees", "orch init --yes",
        "two sessions start (the first hook starts the coordinator)",
        "session A changes the contract of charge in a.py", "session B hears of it before it edits b.py",
        "orch doctor"]
    assert "     No problems found." in said
    lock = Lock(os.path.join(env["ORCH_HOME"], "coordinator.lock"))
    assert lock.acquire()                                      # no coordinator left running
    lock.release()
    assert not os.path.exists(os.path.join(str(tmp_path), "state"))       # the test's own state folder untouched
