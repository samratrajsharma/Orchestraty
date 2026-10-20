"""The Claude Code mod: its files, what it may touch, how orch init loads it, and the two
things it reads from the core (status.json and the hook's Take mode)."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import run_hook, spool_event, spool_lines
from orchestraty import cli, config
from orchestraty.capture import install
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.paths import State

MOD = Path(install.MOD_SOURCE)
REGISTER = MOD / "hooks" / "register.js"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def test_the_plugin_files_are_in_place():
    manifest = json.loads((MOD / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "orchestraty" and "no network" in manifest["description"]
    hooks = json.loads((MOD / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert hooks == {"modules": ["./register.js"]} and REGISTER.is_file()


def test_the_mod_only_reads_shows_and_starts_orchestraty():
    # The promise in its manifest: Orchestraty's state folder and Orchestraty's own Python, no
    # network, no model calls, nothing that approves or rewrites a tool call.
    source = REGISTER.read_text(encoding="utf-8")
    used = set(re.findall(r"\$\.([a-z]+)\.", source))
    assert used <= {"env", "fs", "process", "ui", "clock", "command", "prompt", "session"}, used
    assert "$.fs.write" not in source and "$.http" not in source and "$.model" not in source
    events = set(re.findall(r"on\('([a-z.]+)'", source))
    assert events == {"session.start", "command.run", "ui.render"}, events


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_mod_is_valid_javascript(tmp_path):
    copy = tmp_path / "register.mjs"
    copy.write_text(REGISTER.read_text(encoding="utf-8"), encoding="utf-8")
    proc = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_orch_init_copies_the_mod_and_loads_it(state, capsys):
    settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    settings.parent.mkdir(parents=True)
    other = str(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "another-plugin")
    sep = ";" if os.name == "nt" else ":"
    settings.write_text(json.dumps({"env": {install.PLUGIN_DIRS: other, "KEEP": "1"}}), encoding="utf-8")
    assert cli.main(["init", "--yes"]) == 0
    assert "load Orchestraty's Claude Code mod" in capsys.readouterr().out
    assert (Path(state.mod) / "hooks" / "register.js").read_bytes() == REGISTER.read_bytes()
    env = json.loads(settings.read_text(encoding="utf-8"))["env"]
    assert env[install.PLUGIN_DIRS] == other + sep + state.mod and env["KEEP"] == "1"
    assert cli.main(["init", "--yes"]) == 0                       # no second copy in the list
    assert json.loads(settings.read_text(encoding="utf-8"))["env"][install.PLUGIN_DIRS].count(state.mod) == 1
    assert cli.main(["restore", "--yes"]) == 0
    env = json.loads(settings.read_text(encoding="utf-8"))["env"]
    assert env == {install.PLUGIN_DIRS: other, "KEEP": "1"}


def test_orch_init_no_mod_and_restore_leave_no_env_behind(state):
    settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    assert cli.main(["init", "--yes", "--no-mod"]) == 0
    assert "env" not in json.loads(settings.read_text(encoding="utf-8"))
    assert cli.main(["init", "--yes"]) == 0
    assert cli.main(["init", "--yes", "--no-mod"]) == 0           # taken out again
    assert "env" not in json.loads(settings.read_text(encoding="utf-8"))


def test_orch_config_wake_keeps_the_mod(state):
    settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    assert cli.main(["init", "--yes"]) == 0
    assert cli.main(["config", "wake", "on"]) == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert state.mod in data["env"][install.PLUGIN_DIRS]
    assert any(e["hooks"][0]["args"][-1] == "Wake" for e in data["hooks"]["Stop"])


def test_the_take_mode_hands_a_wake_note_over_once(state, hook_script):
    slots.write(slots.wake_slot(state, "S1"), b"another session changed charge(); you call it")
    proc = subprocess.run([sys.executable, "-S", "-I", str(hook_script), "claude", "Take", "wake", "S1"],
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout == b"another session changed charge(); you call it"
    [(_, line)] = spool_lines(state.spool)
    assert line["event"] == "Take" and line["took"] == "wake" and line["session_id"] == "S1"
    again = subprocess.run([sys.executable, "-S", "-I", str(hook_script), "claude", "Take", "wake", "S1"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    assert again.stdout == b""
    for bad in (["claude", "Take"], ["claude", "Take", "edit", "S1"], ["claude", "Take", "wake", "../x"]):
        assert subprocess.run([sys.executable, "-S", "-I", str(hook_script)] + bad, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=60).stdout == b""


def test_the_coordinator_keeps_status_json_current(state, repo):
    config.watch(state, str(repo / ".git"))
    spool_event(state.root, "s1", "SessionStart", repo)
    c = cmain.Coordinator(state)
    try:
        c.step()
        data = json.loads(Path(state.status).read_text(encoding="utf-8"))
        assert data["schema"] == 1 and data["repositories"][0]["sessions"][0]["id"] == "s1"
        spool_event(state.root, "s2", "SessionStart", repo)
        c._status_written = 0.0                                   # skip the once-a-second limit
        c.step()
        data = json.loads(Path(state.status).read_text(encoding="utf-8"))
        assert [s["id"] for s in data["repositories"][0]["sessions"]] == ["s1", "s2"]
    finally:
        c.close()
