"""The Antigravity capture leg: the shim's agy mode and the set-up tool. Every payload is synthetic."""
import json
import os
import subprocess
import sys

import pytest

import capture_agy
import capture_hooks

SHIM = capture_hooks.SHIM
CANARY = "CODE-CANARY"


def payload(event, tool="write_to_file"):
    p = {"conversationId": "c0ffee-12", "workspacePaths": ["C:\\w\\wt-a", "C:\\other"],
         "transcriptPath": "C:\\Users\\x\\.gemini\\antigravity\\brain\\c0ffee-12\\transcript.jsonl",
         "artifactDirectoryPath": "C:\\x", "modelName": "some-model"}
    if event in ("PreToolUse", "PostToolUse"):
        p["toolCall"] = {"name": tool, "args": {"TargetFile": "C:\\w\\wt-a\\data.py", "CodeContent": CANARY,
                                                "name": "not-the-tool"}}
        p["stepIdx"] = 7
        if event == "PostToolUse":
            p["error"] = ""
    return json.dumps(p).encode("utf-8")


def run_shim(tmp_path, event, raw):
    spool = str(tmp_path / "spool")
    proc = subprocess.run([sys.executable, "-S", "-I", SHIM, spool, "agy", event], input=raw,
                          capture_output=True, timeout=60)
    lines = []
    folder = os.path.join(spool, "agy")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        with open(os.path.join(folder, name), "rb") as fh:
            lines += [(name, json.loads(line)) for line in fh]
    return proc, lines


def test_pre_tool_use_is_recorded_and_answered_with_allow(tmp_path):
    proc, [(name, line)] = run_shim(tmp_path, "PreToolUse", payload("PreToolUse"))
    assert proc.returncode == 0 and json.loads(proc.stdout) == {"decision": "allow"}
    assert name == "c0ffee-12.jsonl"
    assert line["session_id"] == "c0ffee-12" and line["hook_event_name"] == "PreToolUse"
    assert line["tool_name"] == "write_to_file" and line["step"] == 7 and line["cwd"] == "C:\\w\\wt-a"
    assert line["transcript_path"].endswith("transcript.jsonl") and line["agent"] == "agy"
    with open(os.path.join(str(tmp_path / "spool"), "agy", name), "rb") as fh:
        assert CANARY.encode() not in fh.read()                   # tool input never stored


@pytest.mark.parametrize("event", ["SessionStart", "PreInvocation", "PostToolUse", "PostInvocation", "Stop"])
def test_every_other_event_is_recorded_and_answered_with_an_empty_object(tmp_path, event):
    proc, [(_, line)] = run_shim(tmp_path, event, payload(event))
    assert json.loads(proc.stdout) == {} and line["event"] == event
    assert ("tool_name" in line) == (event == "PostToolUse")


def test_a_broken_payload_still_gets_its_answer(tmp_path):
    proc, lines = run_shim(tmp_path, "PreToolUse", b"\xff not json")
    assert proc.returncode == 0 and json.loads(proc.stdout) == {"decision": "allow"}
    assert lines and lines[0][0] == "nosession.jsonl"


def test_the_workspace_hooks_cover_every_event_with_the_shim():
    data = capture_agy.hooks_json("C:\\py\\python.exe", "C:\\p\\shim\\capture_shim.py", "C:\\p\\spool")
    [(name, hooks)] = data.items()
    assert name == "orch-capture" and hooks["enabled"] is True
    for event in capture_agy.EVENTS:
        entries = hooks[event]
        handler = entries[0]["hooks"][0] if event in capture_agy.TOOL_EVENTS else entries[0]
        assert handler["command"].endswith(" agy " + event) and handler["type"] == "command"
        assert ("matcher" in entries[0]) == (event in capture_agy.TOOL_EVENTS)
    with pytest.raises(capture_agy.Refused):
        capture_agy.hooks_json("C:\\Program Files\\python.exe", "C:\\s.py", "C:\\sp")


def test_set_up_writes_only_inside_the_leg_folder(tmp_path):
    root = tmp_path / "orch-phase0"
    root.mkdir()
    if " " in str(root):
        pytest.skip("the temporary folder holds a space")
    leg = capture_agy.setup(str(root), sys.executable)
    for wt in ("wt-a", "wt-b"):
        with open(os.path.join(leg, wt, ".agents", "hooks.json"), encoding="utf-8") as fh:
            assert "orch-capture" in json.load(fh)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=os.path.join(leg, wt),
                                capture_output=True, text=True).stdout
        assert status == ""                                      # .agents/ is kept out of git
    hooks_path = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=os.path.join(leg, "repo"),
                                capture_output=True, text=True).stdout.strip()
    assert hooks_path.endswith("orch-capture-hooks")
    with open(os.path.join(leg, "PROMPTS.md"), encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("\n1. ") == 2 and "wt-b" in text
    assert sorted(os.listdir(str(root))) == ["repos", "shim", "spool"]
    with pytest.raises(capture_agy.Refused):
        capture_agy.setup(str(root), sys.executable)                  # never over an earlier set-up
    assert capture_agy.setup(str(root), sys.executable, fresh=True) == leg
