"""Tests for the hook-config generator. Every folder is a synthetic phase-0 folder in tmp_path."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import capture_hooks as ch


def _tree(folder):
    return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*"))


def _symlinks_work(tmp_path):
    try:
        os.symlink(tmp_path, tmp_path / "probe-link", target_is_directory=True)
        os.unlink(tmp_path / "probe-link")
        return True
    except (OSError, NotImplementedError):
        return False


def test_it_writes_only_the_profile_settings_the_shim_and_the_spool_folder(tmp_path):
    root = tmp_path / "orch-phase0"
    settings = ch.install(str(root), sys.executable)
    assert Path(settings) == root / "profiles" / "claude" / "settings.json"
    assert _tree(tmp_path) == ["orch-phase0", "orch-phase0/profiles", "orch-phase0/profiles/claude",
                               "orch-phase0/profiles/claude/settings.json", "orch-phase0/shim",
                               "orch-phase0/shim/capture_shim.py", "orch-phase0/spool", "orch-phase0/spool/claude"]
    assert (root / "shim" / "capture_shim.py").read_bytes() == Path(ch.SHIM).read_bytes()


def test_hooks_are_in_exec_form_with_the_spool_as_an_argument(tmp_path):
    root = tmp_path / "orch-phase0"
    settings = json.loads(Path(ch.install(str(root), sys.executable)).read_text(encoding="utf-8"))
    shim = str(root / "shim" / "capture_shim.py")
    spool = str(root / "spool")
    assert sorted(settings["hooks"]) == sorted(ch.EVENTS)
    for event in ch.EVENTS:
        [entry] = settings["hooks"][event]
        [hook] = entry["hooks"]
        assert hook == {"type": "command", "command": sys.executable,
                        "args": ["-S", "-I", shim, spool, "claude", event], "timeout": ch.TIMEOUT_SECONDS}
        assert entry.get("matcher") == ch.MATCHERS.get(event)
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Edit|Write|MultiEdit"
    assert os.path.isabs(settings["hooks"]["Stop"][0]["hooks"][0]["command"])


def test_the_default_root_is_outside_appdata_on_windows(monkeypatch, tmp_path):
    # A packaged app's writes under AppData go to its own private copy, which other programs
    # don't see; the phase-0 folder must be one place for everyone.
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    root = ch.default_root()
    assert os.path.basename(root) == "orch-phase0"
    if os.name == "nt":
        assert root == os.path.join(str(tmp_path / "user"), "orch-phase0")
        assert not root.startswith(str(tmp_path / "localappdata"))
    else:
        assert root.endswith(os.path.join(".local", "state", "orch-phase0"))


def test_the_login_line_uses_the_pinned_program_and_leaves_nothing_set(tmp_path):
    root = tmp_path / "orch-phase0"
    assert ch.pinned_program(str(root), "claude") is None
    exe = root / "tools" / "claude" / ("claude.exe" if os.name == "nt" else "claude")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert ch.pinned_program(str(root), "claude") == str(exe)
    powershell, bash = ch.login_lines("CLAUDE_CONFIG_DIR", "C:\\p q\\it's", str(exe))
    assert powershell == (f"$env:CLAUDE_CONFIG_DIR = 'C:\\p q\\it''s'; & '{str(exe)}'; "
                          "Remove-Item Env:CLAUDE_CONFIG_DIR")
    assert bash.startswith("CLAUDE_CONFIG_DIR='C:\\p q\\it'\"'\"'s' ") and bash.endswith(f"'{str(exe)}'")


def test_the_command_line_prints_the_pinned_login_line(tmp_path):
    root = tmp_path / "orch-phase0"
    exe = root / "tools" / "claude" / ("claude.exe" if os.name == "nt" else "claude")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(Path(ch.__file__)), "--root", str(root), "--python", sys.executable],
                          capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.returncode == 0, proc.stderr
    assert f"& '{exe}'; Remove-Item Env:CLAUDE_CONFIG_DIR" in proc.stdout
    assert " claude\n" not in proc.stdout and "; claude" not in proc.stdout, "never the daily claude from PATH"


def test_auto_update_is_off_in_the_isolated_profile_and_other_env_is_kept(tmp_path):
    root = tmp_path / "orch-phase0"
    profile = root / "profiles" / "claude"
    profile.mkdir(parents=True)
    (profile / "settings.json").write_text(json.dumps({"env": {"KEEP": "me", "DISABLE_AUTOUPDATER": "0"}}),
                                           encoding="utf-8")
    settings = json.loads(Path(ch.install(str(root), sys.executable)).read_text(encoding="utf-8"))
    assert settings["env"] == {"KEEP": "me", "DISABLE_AUTOUPDATER": "1"}


def test_async_is_set_only_when_asked(tmp_path):
    root = tmp_path / "orch-phase0"
    settings = json.loads(Path(ch.install(str(root), sys.executable, use_async=True)).read_text(encoding="utf-8"))
    assert all(e["hooks"][0]["async"] is True for entries in settings["hooks"].values() for e in entries)


def test_existing_settings_are_kept_and_a_second_run_changes_nothing(tmp_path):
    root = tmp_path / "orch-phase0"
    profile = root / "profiles" / "claude"
    profile.mkdir(parents=True)
    other_hook = {"hooks": [{"type": "command", "command": "/usr/bin/true"}]}
    (profile / "settings.json").write_text(json.dumps({"model": "x", "hooks": {"Stop": [other_hook]}}),
                                           encoding="utf-8")
    first = Path(ch.install(str(root), sys.executable)).read_text(encoding="utf-8")
    second = Path(ch.install(str(root), sys.executable)).read_text(encoding="utf-8")
    assert first == second
    settings = json.loads(second)
    assert settings["model"] == "x"
    assert settings["hooks"]["Stop"][0] == other_hook and len(settings["hooks"]["Stop"]) == 2


@pytest.mark.parametrize("relative", [
    os.path.join("repos", "scratch", ".claude", "settings.json"),      # a repository's own settings
    os.path.join("profiles", "..", "..", "daily", ".claude", "settings.json"),
    os.path.join("..", "elsewhere"),
    "settings.json",
])
def test_targets_outside_the_profiles_shim_and_spool_folders_are_refused(tmp_path, relative):
    with pytest.raises(ch.Refused):
        ch.checked_target(str(tmp_path / "orch-phase0"), relative)


def test_a_root_with_another_name_is_refused_and_nothing_is_written(tmp_path):
    home = tmp_path / "home"                                            # e.g. --root ~ by mistake
    home.mkdir()
    with pytest.raises(ch.Refused):
        ch.install(str(home), sys.executable)
    assert _tree(tmp_path) == ["home"]


def test_a_link_inside_the_phase0_folder_is_refused(tmp_path):
    if not _symlinks_work(tmp_path):
        pytest.skip("symlinks are not available here")
    daily = tmp_path / "daily-claude"                                   # stands in for ~/.claude
    daily.mkdir()
    root = tmp_path / "orch-phase0"
    root.mkdir()
    os.symlink(daily, root / "profiles", target_is_directory=True)
    with pytest.raises(ch.Refused):
        ch.install(str(root), sys.executable)
    assert list(daily.iterdir()) == []


def test_the_command_line_refuses_with_exit_2_and_writes_nothing(tmp_path):
    proc = subprocess.run([sys.executable, str(Path(ch.__file__)), "--root", str(tmp_path / "not-phase0"),
                           "--python", sys.executable], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2 and "refused" in proc.stderr
    assert list(tmp_path.iterdir()) == []


def test_a_relative_or_missing_python_is_refused(tmp_path):
    for python in ("python", str(tmp_path / "no-python.exe")):
        with pytest.raises(ch.Refused):
            ch.install(str(tmp_path / "orch-phase0"), python)


def test_the_installed_hook_runs_and_writes_one_spool_line(tmp_path):
    root = tmp_path / "orch-phase0"
    settings = json.loads(Path(ch.install(str(root), sys.executable)).read_text(encoding="utf-8"))
    hook = settings["hooks"]["PostToolUse"][0]["hooks"][0]
    payload = json.dumps({"session_id": "s-1", "hook_event_name": "PostToolUse", "tool_use_id": "toolu_1"}).encode()
    proc = subprocess.run([hook["command"]] + hook["args"], input=payload, capture_output=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    line = json.loads((root / "spool" / "claude" / "s-1.jsonl").read_text(encoding="utf-8"))
    assert line["tool_use_id"] == "toolu_1" and line["event"] == "PostToolUse"


def test_the_powershell_login_line_doubles_every_single_quote_powershell_knows():
    profile = "C:\\O" + chr(0x2019) + "Brien\\p"
    powershell, _ = ch.login_lines("X", profile, "C:\\it's.exe")
    assert powershell == "$env:X = 'C:\\O" + chr(0x2019) * 2 + "Brien\\p'; & 'C:\\it''s.exe'; Remove-Item Env:X"
