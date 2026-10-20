"""Tests for the installer and the orch init / orch restore commands. Every settings file is
synthetic and lives in tmp_path."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import spool_lines, symlinks_work
from orchestraty import cli
from orchestraty.capture import install as ins
from orchestraty.paths import State


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def settings(tmp_path):
    return tmp_path / "claude-config" / "settings.json"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def our_hook(state, event):
    return {"type": "command", "command": sys.executable,
            "args": ["-S", "-I", state.hook_script, "claude", event], "timeout": ins.TIMEOUT_SECONDS}


def test_install_into_a_missing_settings_file(state, settings):
    changed, saved = ins.install(state, str(settings), sys.executable)
    assert changed and saved is None
    assert Path(state.hook_script).read_bytes() == Path(ins.HOOK_SOURCE).read_bytes()
    data = read(settings)
    assert list(data) == ["hooks"] and sorted(data["hooks"]) == sorted(ins.EVENTS)


def test_every_entry_is_in_exec_form_with_the_right_matcher(state, settings):
    ins.install(state, str(settings), sys.executable)
    hooks = read(settings)["hooks"]
    for event in ins.EVENTS:
        [entry] = hooks[event]
        assert entry["hooks"] == [our_hook(state, event)]
        assert entry.get("matcher") == ins.MATCHERS.get(event)
    assert hooks["PreToolUse"][0]["matcher"] == "Edit|Write|MultiEdit|NotebookEdit"
    assert hooks["PostToolUse"][0]["matcher"] == "*"
    assert os.path.isabs(hooks["Stop"][0]["hooks"][0]["command"])


def test_other_settings_and_hooks_are_kept_and_a_second_run_changes_nothing(state, settings):
    other = {"hooks": [{"type": "command", "command": "/usr/bin/true"}]}
    write(settings, {"model": "x", "env": {"A": "1"}, "hooks": {"Stop": [other], "Custom": [other]}})
    assert ins.install(state, str(settings), sys.executable)[0]
    first = settings.read_bytes()
    assert ins.install(state, str(settings), sys.executable) == (False, None)
    assert settings.read_bytes() == first
    data = read(settings)
    assert data["model"] == "x" and data["env"] == {"A": "1"}
    assert data["hooks"]["Custom"] == [other]
    assert data["hooks"]["Stop"][0] == other and len(data["hooks"]["Stop"]) == 2
    assert len(os.listdir(state.backups)) == 1                     # only the change was backed up


def test_an_older_entry_of_ours_is_replaced_not_doubled(state, settings):
    old = {"hooks": [{"type": "command", "command": sys.executable, "args": ["-S", state.hook_script, "claude"],
                      "timeout": 99}]}
    write(settings, {"hooks": {"Stop": [old], "PostToolUse": [old]}})
    ins.install(state, str(settings), sys.executable)
    hooks = read(settings)["hooks"]
    assert hooks["Stop"] == [{"hooks": [our_hook(state, "Stop")]}]
    assert len(hooks["PostToolUse"]) == 1


def test_the_file_is_backed_up_before_a_change(state, settings):
    write(settings, {"model": "before"})
    before = settings.read_bytes()
    changed, saved = ins.install(state, str(settings), sys.executable)
    assert changed and saved
    assert Path(saved, ins.BACKUP_NAME).read_bytes() == before
    source, reason = Path(saved, ins.BACKUP_SOURCE).read_text(encoding="utf-8").splitlines()
    assert os.path.normcase(source) == os.path.normcase(str(settings)) and reason == "install"


def test_uninstall_removes_only_ours(state, settings):
    other = {"hooks": [{"type": "command", "command": "/usr/bin/true"}]}
    write(settings, {"model": "x", "hooks": {"Stop": [other]}})
    ins.install(state, str(settings), sys.executable)
    changed, saved = ins.uninstall(state, str(settings))
    assert changed and saved
    assert read(settings) == {"model": "x", "hooks": {"Stop": [other]}}
    assert ins.uninstall(state, str(settings)) == (False, None)


def test_uninstall_drops_a_hooks_object_it_emptied(state, settings):
    write(settings, {"model": "x"})
    ins.install(state, str(settings), sys.executable)
    ins.uninstall(state, str(settings))
    assert read(settings) == {"model": "x"}


def test_restore_from_backup_puts_back_the_exact_file_from_before_install(state, settings):
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b'{"model":  "original"}  ')               # odd spacing survives byte for byte
    original = settings.read_bytes()
    ins.install(state, str(settings), sys.executable)
    assert settings.read_bytes() != original
    folder = ins.restore_backup(state, str(settings))
    assert folder and settings.read_bytes() == original
    assert ins.restore_backup(state, str(settings)) == folder   # the same one again, never a toggle
    assert settings.read_bytes() == original


def test_restore_from_backup_uses_the_last_install(state, settings):
    write(settings, {"model": "original"})
    ins.install(state, str(settings), sys.executable)
    ins.uninstall(state, str(settings))
    write(settings, {"model": "changed later"})
    ins.install(state, str(settings), sys.executable)
    ins.restore_backup(state, str(settings))
    assert read(settings) == {"model": "changed later"}


def test_restore_from_backup_without_one(state, settings):
    write(settings, {"model": "x"})
    assert ins.restore_backup(state, str(settings)) is None
    assert read(settings) == {"model": "x"}


@pytest.mark.parametrize("content", [b"{not json", b"[1, 2]", b"\xff\xfe"])
def test_a_settings_file_that_is_not_a_json_object_is_refused_and_left_alone(state, settings, content):
    settings.parent.mkdir(parents=True)
    settings.write_bytes(content)
    with pytest.raises(ins.Refused):
        ins.install(state, str(settings), sys.executable)
    assert settings.read_bytes() == content
    assert not os.path.exists(state.backups)


def test_a_bom_is_accepted(state, settings):
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b"\xef\xbb\xbf" + json.dumps({"model": "x"}).encode())
    ins.install(state, str(settings), sys.executable)
    assert read(settings)["model"] == "x"


def test_a_linked_settings_file_or_folder_is_refused(state, tmp_path):
    if not symlinks_work(tmp_path):
        pytest.skip("symlinks are not available here")
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "settings.json").write_text("{}", encoding="utf-8")
    linked_folder = tmp_path / "linked-config"
    os.symlink(dotfiles, linked_folder, target_is_directory=True)
    with pytest.raises(ins.Refused):
        ins.install(state, str(linked_folder / "settings.json"), sys.executable)
    real = tmp_path / "real-config"
    real.mkdir()
    os.symlink(dotfiles / "settings.json", real / "settings.json")
    with pytest.raises(ins.Refused):
        ins.install(state, str(real / "settings.json"), sys.executable)
    assert (dotfiles / "settings.json").read_text(encoding="utf-8") == "{}"


def test_a_relative_missing_or_non_exe_python_is_refused(state, settings, tmp_path):
    bad = ["python", str(tmp_path / "no-python.exe")]
    if os.name == "nt":
        script = tmp_path / "python.cmd"
        script.write_text("", encoding="utf-8")
        bad.append(str(script))
    for python in bad:
        with pytest.raises(ins.Refused):
            ins.install(state, str(settings), python)
    assert not settings.exists()


def test_settings_path_choices(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    assert ins.settings_path(project=str(project)) == str(project / ".claude" / "settings.local.json")
    assert ins.settings_path(claude_config=str(tmp_path / "cfg")) == str(tmp_path / "cfg" / "settings.json")
    assert ins.settings_path(environ={"CLAUDE_CONFIG_DIR": str(tmp_path / "env")}) == str(
        tmp_path / "env" / "settings.json")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    assert ins.settings_path(environ={}) == str(tmp_path / "home" / ".claude" / "settings.json")


def test_the_hook_interpreter_is_a_real_file():
    python = ins.hook_python()
    assert os.path.isabs(python) and os.path.isfile(python)


def test_the_installed_hook_runs_and_writes_one_spool_line(state, settings):
    ins.install(state, str(settings), sys.executable)
    hook = read(settings)["hooks"]["PostToolUse"][0]["hooks"][0]
    body = json.dumps({"session_id": "s-1", "hook_event_name": "PostToolUse", "tool_name": "Write",
                       "tool_input": {"file_path": "/w/a.py", "content": "x"}, "tool_use_id": "toolu_1"}).encode()
    proc = subprocess.run([hook["command"]] + hook["args"], input=body, capture_output=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [(name, line)] = spool_lines(state.spool)
    assert name == "s-1.jsonl" and line["tool_use_id"] == "toolu_1" and line["file_path"] == "/w/a.py"


# --------------------------------------------------------------------------------------- #
# The commands
# --------------------------------------------------------------------------------------- #
def test_orch_init_asks_and_without_a_terminal_changes_nothing(settings, capsys):
    assert cli.main(["init"]) == 1
    out = capsys.readouterr().out
    assert "--yes" in out and "never prompts" in out and str(settings) in out
    assert not settings.exists()


def test_orch_init_yes_installs_and_reports(settings, state, capsys):
    assert cli.main(["init", "--yes"]) == 0
    assert sorted(read(settings)["hooks"]) == sorted(ins.EVENTS)
    assert "Installed in" in capsys.readouterr().out
    assert cli.main(["init", "--yes"]) == 0
    assert "Already installed" in capsys.readouterr().out


def test_orch_init_says_which_sessions_the_hooks_run_in(tmp_path, capsys):
    assert cli.main(["init", "--yes", "--claude-config", str(tmp_path / "profile")]) == 0
    out = capsys.readouterr().out
    assert f"session that uses the config folder {tmp_path / 'profile'}" in out
    assert "every Claude Code session of this user" not in out
    assert cli.main(["init", "--yes"]) == 0
    assert "every Claude Code session of this user" in capsys.readouterr().out


def test_orch_init_answers_from_a_terminal(settings, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert cli.main(["init"]) == 0 and settings.exists()


def test_orch_init_print_changes_nothing(settings, state, capsys):
    assert cli.main(["init", "--print"]) == 0
    block = json.loads(capsys.readouterr().out)["hooks"]
    assert sorted(block) == sorted(ins.EVENTS)
    assert block["Stop"][0]["hooks"][0]["args"][2] == state.hook_script
    assert not settings.exists() and not os.path.exists(state.root)


def test_orch_init_project_writes_the_local_settings_of_this_folder(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    assert cli.main(["init", "--project", "--yes"]) == 0
    assert (project / ".claude" / "settings.local.json").is_file()


def test_orch_init_refuses_bad_settings_with_exit_2(settings, capsys):
    settings.parent.mkdir(parents=True)
    settings.write_text("{oops", encoding="utf-8")
    assert cli.main(["init", "--yes"]) == 2
    assert "could not be read as JSON" in capsys.readouterr().err
    assert settings.read_text(encoding="utf-8") == "{oops"


def test_orch_restore_and_restore_from_backup(settings, capsys):
    write(settings, {"model": "x"})
    cli.main(["init", "--yes"])
    assert cli.main(["restore", "--yes"]) == 0
    assert read(settings) == {"model": "x"}
    assert "Removed" in capsys.readouterr().out
    assert cli.main(["restore", "--yes"]) == 0
    assert "nothing changed" in capsys.readouterr().out
    cli.main(["init", "--yes"])
    assert cli.main(["restore", "--from-backup", "--yes"]) == 0
    assert read(settings) == {"model": "x"}


def test_orch_restore_from_backup_without_a_backup(settings, capsys):
    write(settings, {"model": "x"})
    assert cli.main(["restore", "--from-backup", "--yes"]) == 1
    assert "No backup" in capsys.readouterr().out
