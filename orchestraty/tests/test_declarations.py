"""Declarations (the MCP tool declare): a spool line the coordinator stores, that orch status
shows while it holds, and that expires."""
import json
import time
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import config, declarations, status
from orchestraty.coordinator import main as cmain
from orchestraty.paths import State


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def one(state, repo):
    """A watched repository with one live session, A, and a coordinator that has seen it."""
    config.watch(state, str(repo / ".git"))
    spool_event(state.root, "A", "SessionStart", repo)
    c = cmain.Coordinator(state)
    c.step()
    yield c
    c.close()


def shown(state, now=None):
    [repo] = status.collect(state, now=now)["repositories"]
    return {s["id"]: s["declared"] for s in repo["sessions"]}


def test_a_declaration_reaches_the_record_and_expires(state, one):
    c = one
    declarations.append(state, "A", ["c.py", "b.py"], ["c.py::refund"], "  add a refund  ")
    assert shown(state) == {"A": []}                         # not in the record until the coordinator reads it
    c.step()
    [d] = c.record.declarations()
    assert (d["session_id"], d["paths"], d["symbols"], d["task"]) == ("A", ["c.py", "b.py"], ["c.py::refund"], "add a refund")
    assert d["expires"] - d["ts"] == 120 * 60 * 10**9
    [declared] = shown(state)["A"]
    assert (declared["paths"], declared["symbols"], declared["task"]) == (["c.py", "b.py"], ["c.py::refund"], "add a refund")
    text = status.render(status.collect(state))
    assert 'declared: c.py, b.py (refund) until ' in text and '"add a refund"' in text
    assert shown(state, now=time.time() + 121 * 60) == {"A": []}            # two hours later it no longer holds
    assert "declared" not in status.render(status.collect(state, now=time.time() + 121 * 60))


def test_the_time_a_declaration_holds_comes_from_the_config(state, one):
    c = one
    cfg = config.load(state)
    cfg["declare_minutes"] = 15
    config.save(state, cfg)
    c.reload_config()
    declarations.append(state, "A", ["c.py"])
    c.step()
    [d] = c.record.declarations()
    assert d["expires"] - d["ts"] == 15 * 60 * 10**9 and d["symbols"] == [] and d["task"] == ""


def test_declarations_of_another_shape_or_an_unknown_session_are_dropped(state, one):
    c = one
    declarations.append(state, "nobody", ["c.py"])
    folder = Path(state.spool) / "orch"
    junk = [{"event": "declare", "session_id": "A", "paths": []},
            {"event": "declare", "session_id": "A", "paths": "c.py"},
            {"event": "declare", "session_id": "A", "paths": ["c.py"], "symbols": [3]},
            {"event": "declare", "session_id": "A", "paths": ["x" * 400]},
            {"event": "declare", "paths": ["c.py"]},
            {"event": "declare", "session_id": "A", "paths": ["c.py"], "task": ["not", "a", "line"]}]
    with open(folder / "junk.jsonl", "ab") as fh:
        fh.write(b"".join(json.dumps(j).encode() + b"\n" for j in junk))
    c.step()
    assert c.record.declarations() == []
    assert declarations.parse_line({"event": "declare", "session_id": "A", "paths": ["a", "a", "b"]})["paths"] == ["a", "b"]
