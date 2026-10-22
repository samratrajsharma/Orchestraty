"""The two-call helper: hook events for a homemade agent, through the installed hook script,
in this process or in a process of its own, with the same bytes either way."""
import json
import os
import uuid
from pathlib import Path

import pytest

from helpers import spool_lines
from orchestraty import helper
from orchestraty.capture import install
from orchestraty.delivery import notes, slots
from orchestraty.helper import Orchestraty
from orchestraty.paths import State

MODES = [pytest.param(True, id="in-process"), pytest.param(False, id="subprocess")]


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def installed(state, hook_script):
    install.write_launch(state)
    return hook_script


def lines_of(state, agent="helper-mybot", session="S1"):
    path = Path(state.spool) / agent / (session + ".jsonl")
    return path.read_bytes().splitlines(keepends=True) if path.exists() else []


@pytest.mark.parametrize("in_process", MODES)
def test_a_session_reaches_the_spool_as_an_agents_hook_events(state, installed, tmp_path, in_process):
    orch = Orchestraty(agent="mybot", cwd=tmp_path, session_id="S1", in_process=in_process)
    assert orch.start() is None
    assert orch.before_write("src/a.py") is None                 # no note waiting
    assert orch.after_write(tmp_path / "src" / "a.py") is None
    assert orch.end_turn() is None
    assert orch.end() is None
    got = [line for _, line in spool_lines(state.spool)]
    assert [g["event"] for g in got] == ["SessionStart", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"]
    assert {(g["agent"], g["session_id"], g["cwd"]) for g in got} == {("helper-mybot", "S1", str(tmp_path))}
    target = str(tmp_path / "src" / "a.py")
    assert [(g["tool_name"], g["file_path"]) for g in got[1:3]] == [("Write", target)] * 2
    assert got[0]["source"] == "startup" and got[3]["stop_hook_active"] is False and got[4]["reason"] == "other"
    assert not any("took" in g for g in got)


@pytest.mark.parametrize("in_process", MODES)
def test_each_call_hands_over_its_note_as_text_once(state, installed, tmp_path, in_process):
    orch = Orchestraty(agent="mybot", cwd=tmp_path, session_id="S1", in_process=in_process)
    target = str(tmp_path / "a.py")
    slots.write(slots.edit_slot(state, "S1", target), notes.pre_tool_output("before a.py"))
    slots.write(slots.next_slot(state, "S1"), notes.post_tool_output("with the next step"))
    slots.write(slots.stop_slot(state, "S1"), notes.stop_output("at the end of the turn"))
    assert orch.before_write("b.py") is None                      # another file's note waits
    assert orch.before_write("a.py") == "before a.py"
    assert orch.before_write("a.py") is None                      # taken once
    assert orch.after_write("a.py") == "with the next step"
    assert orch.end_turn() == "at the end of the turn"
    assert [line.get("took") for _, line in spool_lines(state.spool)] == [None, "edit", None, "next", "stop"]
    slots.write(slots.edit_slot(state, "S1", target), notes.pre_tool_output("held", steer=True))
    assert orch.before_write(target) == "held"                    # steer's wrapper too
    assert not os.listdir(os.path.join(slots.folder(state, "S1"), "edit"))


EVENTS = [("SessionStart", {"source": "startup"}, None),
          ("PreToolUse", "write", "edit"),
          ("PreToolUse", "write", None),
          ("PostToolUse", "write", "next"),
          ("PostToolUse", "write", None),
          ("Stop", {"stop_hook_active": False}, "stop"),
          ("Stop", {"stop_hook_active": True}, None),
          ("SessionEnd", {"reason": "other"}, None)]


@pytest.mark.parametrize("folder", ["work", "wérk 代码 \"q\" (1)"])
def test_in_process_writes_the_same_bytes_and_takes_slots_the_same_way(state, installed, tmp_path, folder):
    cwd = tmp_path / folder
    orch = Orchestraty(agent="mybot", cwd=cwd, session_id="S1")
    target = str(cwd / "pkg" / "m_1.py")
    for event, fields, kind in EVENTS:
        raw = orch.payload(event, orch._write(target) if fields == "write" else fields)
        slot = {"edit": slots.edit_slot(state, "S1", target), "next": slots.next_slot(state, "S1"),
                "stop": slots.stop_slot(state, "S1")}.get(kind)
        if slot:
            slots.write(slot, notes.post_tool_output(f"{event} note"))
        printed = orch.run_hook(event, raw)                       # the hook script, as a process
        [by_process] = lines_of(state)[-1:]
        assert slot is None or not os.path.exists(slot)
        if slot:
            slots.write(slot, notes.post_tool_output(f"{event} note"))
        line = json.loads(by_process)
        returned = orch.run_in_process(event, raw, now_ns=line["ts"], pid=line["pid"])
        [in_process] = lines_of(state)[-1:]
        assert in_process == by_process, (event, kind)            # byte for byte
        assert returned == printed and (returned != b"") == (kind is not None)
        assert slot is None or not os.path.exists(slot)
        assert line.get("took") == kind


def test_in_process_failures_leave_the_hooks_trace(state, installed, tmp_path, monkeypatch):
    orch = Orchestraty(agent="mybot", cwd=tmp_path, session_id="S1")
    orch.start()
    hook = helper._load(state.hook_script)

    def broken(*args):
        raise RuntimeError("boom")
    monkeypatch.setattr(hook, "event_line", broken)
    assert orch.before_write("a.py") is None
    trace = Path(state.spool, "helper-mybot", "shim_errors.jsonl").read_bytes()
    assert b'"agent":"helper-mybot","shim_error":"RuntimeError"}' in trace


def test_names_and_session_ids(state, tmp_path):
    assert helper.agent_name("mybot") == "helper-mybot"
    assert helper.agent_name("my:bot/2 é") == "helper-mybot2"          # no ':' ever reaches a folder name
    assert helper.agent_name("") == "helper-agent" and len(helper.agent_name("x" * 80)) == 32
    orch = Orchestraty(agent="b", cwd=tmp_path)
    assert str(uuid.UUID(orch.session_id)) == orch.session_id               # a new id unless one is given
    assert Orchestraty(agent="b", cwd=tmp_path, session_id="a:b/c").session_id == "abc"
    assert Orchestraty(agent="b", cwd="relative").cwd == os.path.abspath("relative")


@pytest.mark.parametrize("in_process", MODES)
def test_it_never_raises_into_the_agents_loop(state, tmp_path, in_process, monkeypatch):
    orch = Orchestraty(agent="mybot", cwd=tmp_path, in_process=in_process)
    for call in (orch.start, lambda: orch.before_write("a.py"), lambda: orch.after_write("a.py"), orch.end_turn,
                 orch.end):
        assert call() is None                                     # no hook script installed: nothing, quietly
    assert orch.before_write(None) is None and orch.after_write(b"\xff") is None
    install.install_script(state)
    with open(state.launch, "w", encoding="utf-8") as fh:
        fh.write(str(tmp_path / "no-such-python") + "\n")         # falls back to this interpreter
    slots.write(slots.next_slot(state, orch.session_id), notes.post_tool_output("still here"))
    assert orch.after_write("a.py") == "still here"
    Path(state.hook_script).write_text("this is not python(\n", encoding="utf-8")
    assert orch.before_write("a.py") is None and orch.end_turn() is None


def test_note_text():
    assert helper.note_text(b"") is None and helper.note_text(b"  \n") is None
    assert helper.note_text(notes.stop_output("x")) == "x"
    assert helper.note_text(b'{"reason": "r"}') == "r" and helper.note_text(b"[1]") is None
    assert helper.note_text("plain words\n".encode()) == "plain words"
