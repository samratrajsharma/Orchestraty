"""The overlap rules in the coordinator: findings opened, refreshed, resolved, and shown."""
import os

import pytest

from helpers import spool_event
from orchestraty import config, status
from orchestraty.coordinator import main as cmain
from orchestraty.paths import State

BROKEN = "def charge(amount):\n    return amount\n"
USES = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def two(state, repo, git, tmp_path):
    """A watched repository with two worktrees, a hooked session in each, and a coordinator
    that has seen both."""
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    spool_event(state.root, "A", "SessionStart", wa)
    spool_event(state.root, "B", "SessionStart", wb)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    yield c, wa, wb
    c.close()


def edit(state, c, sid, top, rel, text):
    path = top / rel
    path.write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(path))
    c.step()
    c.livemap.indexes.wait(120)
    c._last_scan = None                     # the next step refreshes every session again
    c.step()


def keys(c, states=("open",)):
    return {f["key"] for f in c.record.findings(states=states)}


def test_a_breaking_change_and_its_use_in_another_session(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    assert keys(c) == {"O6|a.py::charge|A"}                       # b.py's caller left behind
    edit(state, c, "B", wb, "c.py", USES)
    assert keys(c) == {"O6|a.py::charge|A", "O3|a.py::charge|A|B|c.py"}
    [o3] = [f for f in c.record.findings() if f["rule"] == "O3"]
    assert (o3["changer"], o3["receiver"], o3["file"], o3["strength"]) == ("A", "B", "c.py", "strong")
    assert o3["detail"]["uses"] == [{"symbol": "c.py::use", "lines": [5]}]
    assert o3["detail"]["reasons"] == ["parameter currency removed"]


def test_new_findings_are_reported_once(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    assert [f.key for f in c.new_findings] in ([], ["O6|a.py::charge|A"])
    seen = {f["key"] for f in c.record.findings()}
    assert seen
    c._last_scan = None
    c._dirty_repos.add(c.sessions["A"]["repo_id"])                # the rules run again over the same changes
    c.step()
    assert c.new_findings == [] and keys(c) == seen


def test_a_finding_that_stops_holding_is_resolved(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    assert "O3|a.py::charge|A|B|c.py" in keys(c)
    os.remove(wb / "c.py")                                        # B drops the use
    c._last_scan = None
    c.step()
    assert "O3|a.py::charge|A|B|c.py" not in keys(c)
    assert "O3|a.py::charge|A|B|c.py" in keys(c, states=("resolved",))
    edit(state, c, "A", wa, "a.py", "def charge(amount, currency):\n    return amount\n")   # A reverts
    assert keys(c) == set()


def test_findings_wait_while_a_graph_is_being_built(state, two, monkeypatch):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    before = keys(c)
    monkeypatch.setattr(c.livemap, "graph", lambda sid: None)       # as if rebuilding
    monkeypatch.setattr(c.livemap, "base_graph", lambda sid: None)
    c._last_scan = None
    c._dirty_repos.add(c.sessions["A"]["repo_id"])
    c.step()
    assert keys(c) == before                                      # kept, not resolved


def test_an_ended_session_takes_its_findings_with_it(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    spool_event(state.root, "B", "SessionEnd", wb, reason="other")
    c.step()
    assert keys(c) == {"O6|a.py::charge|A"}


def test_the_same_function_in_both_and_the_same_file(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", "def charge(amount, currency):\n    return amount * 2\n")
    edit(state, c, "B", wb, "a.py", "def charge(amount, currency):\n    return amount * 3\n")
    assert keys(c) == {"O1|a.py|A|B", "O2|a.py::charge|A|B"}


def test_status_lists_the_overlaps_strongest_first(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    data = status.collect(state)
    overlaps = data["repositories"][0]["overlaps"]
    assert [o["rule"] for o in overlaps] == ["O3", "O6"]
    text = status.render(data)
    assert "overlaps:" in text
    assert "strong  O3  charge (a.py): breaking change in A, used in c.py by B" in text
    assert "strong  O6  charge (a.py): 1 caller left behind by A" in text
