"""Tests for the Codex hook-config generator. Every folder is synthetic."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import capture_codex as cx
import capture_hooks

# The hooks.json schema Codex accepts (codex-rs/config/src/hook_config.rs): top-level keys,
# event names, and the fields of a command handler.
TOP_LEVEL = {"description", "hooks"}
CODEX_EVENTS = {"PreToolUse", "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact", "SessionStart",
                "SessionEnd", "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop", "Interrupt"}
HANDLER_FIELDS = {"type", "command", "commandWindows", "timeout", "async", "statusMessage", "additionalContextLimit"}


def test_hooks_json_uses_only_what_codex_accepts(tmp_path):
    root = tmp_path / "orch-phase0"
    data = json.loads(Path(cx.install(str(root), sys.executable)).read_text(encoding="utf-8"))
    assert set(data) <= TOP_LEVEL
    assert set(data["hooks"]) == set(cx.EVENTS) and set(cx.EVENTS) <= CODEX_EVENTS
    for event, groups in data["hooks"].items():
        [group] = groups
        assert set(group) <= {"matcher", "hooks"} and ("matcher" in group) == (event == "PostToolUse")
        [hook] = group["hooks"]
        assert set(hook) <= HANDLER_FIELDS and hook["type"] == "command" and isinstance(hook["command"], str)
        assert isinstance(hook["timeout"], int) and 1 <= hook["timeout"] <= (3 if event == "SessionEnd" else 600)


def test_it_writes_only_the_codex_profile_the_shim_and_the_spool(tmp_path):
    root = tmp_path / "orch-phase0"
    cx.install(str(root), sys.executable)
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert written == ["orch-phase0", "orch-phase0/profiles", "orch-phase0/profiles/codex",
                       "orch-phase0/profiles/codex/config.toml", "orch-phase0/profiles/codex/hooks.json",
                       "orch-phase0/shim", "orch-phase0/shim/capture_shim.py", "orch-phase0/spool",
                       "orch-phase0/spool/codex"]


def test_update_checks_are_off_and_codexs_own_config_is_kept_byte_for_byte(tmp_path):
    root = tmp_path / "orch-phase0"
    profile = root / "profiles" / "codex"
    profile.mkdir(parents=True)
    theirs = 'model = "x"\r\n\r\n[projects."C:\\\\w"]\r\ntrust_level = "trusted"\r\n'
    with open(profile / "config.toml", "w", encoding="utf-8", newline="") as fh:
        fh.write(theirs)
    cx.install(str(root), sys.executable)
    with open(profile / "config.toml", "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    assert text == ("check_for_update_on_startup = false\n"
                    f"sqlite_home = '{profile}'\n" + theirs), "Codex's own lines are kept byte for byte"
    cx.install(str(root), sys.executable)
    with open(profile / "config.toml", "r", encoding="utf-8", newline="") as fh:
        assert fh.read() == text, "a second run changes nothing"


def test_a_different_update_setting_is_refused_and_nothing_is_written(tmp_path):
    root = tmp_path / "orch-phase0"
    profile = root / "profiles" / "codex"
    profile.mkdir(parents=True)
    (profile / "config.toml").write_text("check_for_update_on_startup = true\n", encoding="utf-8")
    with pytest.raises(capture_hooks.Refused):
        cx.install(str(root), sys.executable)
    assert sorted(p.name for p in profile.iterdir()) == ["config.toml"]
    assert not (root / "shim").exists()
    with pytest.raises(capture_hooks.Refused):
        cx.merged_config("", "C:\\it's")


def test_other_hooks_are_kept_and_a_second_run_changes_nothing(tmp_path):
    root = tmp_path / "orch-phase0"
    profile = root / "profiles" / "codex"
    profile.mkdir(parents=True)
    theirs = {"hooks": [{"type": "command", "command": "echo hi"}]}
    (profile / "hooks.json").write_text(json.dumps({"description": "mine", "hooks": {"Stop": [theirs]}}),
                                        encoding="utf-8")
    first = Path(cx.install(str(root), sys.executable)).read_text(encoding="utf-8")
    second = Path(cx.install(str(root), sys.executable)).read_text(encoding="utf-8")
    assert first == second
    data = json.loads(second)
    assert data["description"] == "mine" and data["hooks"]["Stop"][0] == theirs and len(data["hooks"]["Stop"]) == 2


def test_async_is_never_set_on_session_end(tmp_path):
    data = json.loads(Path(cx.install(str(tmp_path / "orch-phase0"), sys.executable, use_async=True)).read_text(
        encoding="utf-8"))
    for event, groups in data["hooks"].items():
        assert groups[0]["hooks"][0].get("async") is (None if event == "SessionEnd" else True)


def test_a_root_with_another_name_is_refused(tmp_path):
    with pytest.raises(capture_hooks.Refused):
        cx.install(str(tmp_path / "home"), sys.executable)
    assert list(tmp_path.iterdir()) == []


def test_cmd_refuses_paths_with_characters_it_treats_specially():
    for bad in ("C:\\a&b\\py.exe", "C:\\100%\\py.exe", "C:\\a^b", "C:\\a|b", "C:\\a<b", "C:\\a>b", "C:\\w!x", 'C:\\"q"'):
        with pytest.raises(capture_hooks.Refused):
            cx.shell_command([bad, "-S", "x"], windows_shell="cmd", windows=True)
    assert cx.shell_command(["C:\\ok\\py.exe", "-S"], windows_shell="cmd", windows=True) == "C:\\ok\\py.exe -S"
    # PowerShell quotes instead, so the same characters are fine there.
    assert cx.shell_command(["C:\\a&b\\py.exe"], windows=True) == "& 'C:\\a&b\\py.exe'"


def test_shell_commands_quote_every_path():
    argv = ["/opt/py thon/bin/python3", "-S", "-I", "/p/it's shim.py", "/spool dir", "codex", "Stop"]
    posix = cx.shell_command(argv, windows=False)
    assert posix.startswith("'/opt/py thon/bin/python3' -S -I ") and "'/p/it'\"'\"'s shim.py'" in posix
    ps = cx.shell_command(["C:\\P y\\python.exe", "-S", "-I", "C:\\it's\\shim.py", "C:\\sp", "codex", "Stop"],
                          windows=True)
    assert ps == "& 'C:\\P y\\python.exe' -S -I 'C:\\it''s\\shim.py' 'C:\\sp' 'codex' 'Stop'"
    cmd = cx.shell_command(["C:\\P y\\python.exe", "-S", "C:\\sp"], windows_shell="cmd", windows=True)
    assert cmd == '"C:\\P y\\python.exe" -S C:\\sp'


def _shell_runs(command):
    """The shell Codex would use here, with the command, as an argv list."""
    if os.name == "nt":
        return ["powershell.exe", "-NoProfile", "-Command", command]
    return ["/bin/sh", "-c", command]


@pytest.mark.skipif(os.name == "nt" and not shutil.which("powershell.exe"), reason="no PowerShell here")
def test_the_generated_command_runs_in_codexs_shell_and_writes_one_spool_line(tmp_path):
    root = tmp_path / "orch-phase0"
    data = json.loads(Path(cx.install(str(root), sys.executable)).read_text(encoding="utf-8"))
    command = data["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    payload = json.dumps({"session_id": "thread-1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                          "tool_use_id": "call_9", "cwd": "/w", "transcript_path": None}).encode()
    proc = subprocess.run(_shell_runs(command), input=payload, capture_output=True, timeout=120)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    line = json.loads((root / "spool" / "codex" / "thread-1.jsonl").read_text(encoding="utf-8"))
    assert line["agent"] == "codex" and line["tool_use_id"] == "call_9" and "transcript_path" not in line


@pytest.mark.parametrize("text", [
    '"check_for_update_on_startup" = true\n',                                # a quoted key
    'notes = """\n[not a table]\n"""\ncheck_for_update_on_startup = true\n',  # after a multi-line string
    '[profiles.x]\nsqlite_home = "elsewhere"\n',                            # the same name inside a table
], ids=["quoted-key", "after-multi-line-string", "inside-a-table"])
def test_a_wanted_key_the_scan_cannot_read_is_refused_and_nothing_is_written(tmp_path, text):
    root = tmp_path / "orch-phase0"
    profile = root / "profiles" / "codex"
    profile.mkdir(parents=True)
    (profile / "config.toml").write_bytes(text.encode("utf-8"))
    with pytest.raises(capture_hooks.Refused):
        cx.install(str(root), sys.executable)
    assert (profile / "config.toml").read_bytes() == text.encode("utf-8")


def test_a_byte_order_mark_stays_first_and_its_first_line_is_still_read(tmp_path):
    root = tmp_path / "orch-phase0"
    profile = root / "profiles" / "codex"
    profile.mkdir(parents=True)
    bom = chr(0xFEFF)
    (profile / "config.toml").write_bytes((bom + "check_for_update_on_startup = true\n").encode("utf-8"))
    with pytest.raises(capture_hooks.Refused):                                    # line 1 is seen, and refused
        cx.install(str(root), sys.executable)
    (profile / "config.toml").write_bytes((bom + 'model = "x"\n').encode("utf-8"))
    cx.install(str(root), sys.executable)
    text = (profile / "config.toml").read_bytes().decode("utf-8")
    assert text.startswith(bom + "check_for_update_on_startup = false\n") and text.endswith('model = "x"\n')


def test_powershell_commands_double_typographic_quotes_too():
    path = "C:\\O" + chr(0x2019) + "Brien\\py.exe"
    assert cx.shell_command([path], windows=True) == "& 'C:\\O" + chr(0x2019) * 2 + "Brien\\py.exe'"
