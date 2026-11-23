"""orch init --agent and orch restore --agent for Codex and Antigravity: merged into the hooks
already there, idempotent, backed up first, Codex's trust state left alone, and other entries
untouched on restore. The installed command lines run the real hook script."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestraty import cli, config, connections
from orchestraty.adapters import antigravity, codex, shell
from orchestraty.paths import State

TRUST = '[hooks.state."/x/hooks.json:pre_tool_use:0:0"]\ntrusted_hash = "sha256:abc"\n\n[features]\nhooks = true\n'


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def backups(state):
    return sorted(os.listdir(state.backups)) if os.path.isdir(state.backups) else []


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_command(command, data, cwd=None):
    """Run a hook's command line through the shell, as the agent does."""
    return subprocess.run(command, shell=True, input=data, capture_output=True, timeout=60, cwd=cwd)


def test_init_all_installs_each_integration_and_reports_the_missing_first_events(state, capsys):
    path = Path(os.environ["CODEX_HOME"]) / "config.toml"
    path.parent.mkdir()
    path.write_text(TRUST, encoding="utf-8")
    args = ["init", "--agent", "all", "--yes", "--no-git-hooks", "--no-mod"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert out.count("[Waiting for first event]") == 3 and "[Connected]" not in out
    targets = config.load(state)["installed"]
    assert {entry["agent"] for entry in targets} == {"claude", "codex", "antigravity"}
    assert {row["status"] for row in connections.summary(state)} == {"waiting"}
    assert path.read_text(encoding="utf-8") == TRUST
    assert cli.main(args) == 0
    assert config.load(state)["installed"] == targets                 # repeat setup preserves existing receipts


def test_init_all_reports_a_bad_config_and_still_installs_the_other_two(state, capsys):
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir()
    invalid = home / "hooks.json"
    invalid.write_text("invalid", encoding="utf-8")
    assert cli.main(["init", "--agent", "all", "--yes", "--no-git-hooks", "--no-mod"]) == 2
    out, err = capsys.readouterr()
    assert "Codex [Configuration needs attention]" in out and str(invalid) in err
    assert {e["agent"] for e in config.load(state)["installed"]} == {"claude", "antigravity"}
    assert invalid.read_text(encoding="utf-8") == "invalid"


# --------------------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------------------- #
def test_codex_hooks_merge_into_the_users_hooks_json_and_leave_its_trust_alone(state, tmp_path, capsys):
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir()
    theirs = {"description": "mine", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "echo checked", "timeout": 5}]}]}}
    (home / "hooks.json").write_text(json.dumps(theirs), encoding="utf-8")
    (home / "config.toml").write_text(TRUST, encoding="utf-8")
    trust = (home / "config.toml").read_bytes()

    assert cli.main(["init", "--agent", "codex", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "/hooks" in out and "add hooks for 7 Codex events" in out and '"apply_patch"' in out   # the change, shown
    got = read(home / "hooks.json")
    assert got["description"] == "mine"
    assert got["hooks"]["PreToolUse"][0] == theirs["hooks"]["PreToolUse"][0]          # theirs first, untouched
    ours = got["hooks"]["PreToolUse"][1]
    assert ours["matcher"] == "apply_patch" and shell.mentions(ours["hooks"][0]["command"], state.hook_script)
    assert ours["hooks"][0]["command"].endswith("codex PreToolUse")
    assert set(got["hooks"]) == set(codex.CAPTURED) and got["hooks"]["PostToolUse"][0]["matcher"] == "*"
    assert got["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 3
    assert (home / "config.toml").read_bytes() == trust                                # trust is the person's
    [first] = backups(state)
    assert read(Path(state.backups) / first / "settings.json") == theirs                 # backed up first

    assert cli.main(["init", "--agent", "codex", "--yes"]) == 0                          # again: nothing changes
    assert "nothing changed" in capsys.readouterr().out and read(home / "hooks.json") == got
    assert backups(state) == [first]

    assert cli.main(["restore", "--agent", "codex", "--yes"]) == 0
    assert read(home / "hooks.json") == theirs and (home / "config.toml").read_bytes() == trust
    assert cli.main(["restore", "--agent", "codex", "--yes"]) == 0
    assert "nothing changed" in capsys.readouterr().out


def test_codex_restore_from_backup_and_the_installed_command_runs_the_hook(state, tmp_path, capsys):
    home = Path(os.environ["CODEX_HOME"])
    assert cli.main(["init", "--agent", "codex", "--yes"]) == 0                          # no file before: made
    got = read(home / "hooks.json")
    command = got["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    payload = json.dumps({"session_id": "cx1", "transcript_path": None, "cwd": str(tmp_path),
                          "hook_event_name": "SessionStart", "model": "model-1", "permission_mode": "default",
                          "source": "startup"}).encode()
    proc = run_command(command, payload)
    assert (proc.returncode, proc.stdout) == (0, b"")
    line = json.loads((Path(state.root) / "spool" / "codex" / "cx1.jsonl").read_text(encoding="utf-8"))
    assert (line["event"], line["session_id"], line["source"]) == ("SessionStart", "cx1", "startup")
    assert cli.main(["restore", "--agent", "codex", "--from-backup", "--yes"]) == 1      # nothing to put back
    (home / "hooks.json").write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    assert cli.main(["init", "--agent", "codex", "--yes"]) == 0
    assert cli.main(["restore", "--agent", "codex", "--from-backup", "--yes"]) == 0
    assert read(home / "hooks.json") == {"hooks": {}}


def test_codex_print_and_refusals_change_nothing(state, tmp_path, capsys):
    home = Path(os.environ["CODEX_HOME"])
    assert cli.main(["init", "--agent", "codex", "--print"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert set(printed["hooks"]) == set(codex.CAPTURED) and not home.exists()
    assert cli.main(["init", "--agent", "codex", "--project", "--yes"]) == 2
    assert cli.main(["init", "--agent", "codex", "--antigravity-config", str(tmp_path), "--yes"]) == 2
    assert cli.main(["init", "--codex-home", str(home), "--yes"]) == 2                  # Claude Code's init
    assert cli.main(["init", "--agent", "codex"]) == 1                                  # no terminal, no --yes
    home.mkdir()
    for text in ("{not json", '{"hooks": []}'):                                     # not JSON, or hooks not an object
        (home / "hooks.json").write_text(text, encoding="utf-8")
        assert cli.main(["init", "--agent", "codex", "--yes"]) == 2
        assert (home / "hooks.json").read_text(encoding="utf-8") == text and backups(state) == []


# --------------------------------------------------------------------------------------- #
# Antigravity
# --------------------------------------------------------------------------------------- #
def test_antigravity_hooks_go_into_the_users_file_beside_other_hooks(state, tmp_path, capsys):
    folder = Path(os.environ["HOME"]) / ".gemini" / "config"
    folder.mkdir(parents=True)
    script = state.hook_script
    lint = {"enabled": True, "PreToolUse": [{"matcher": "run_command", "hooks": [
        {"type": "command", "command": "node lint.js", "timeout": 30}]}], "Stop": [{"command": "echo done"}]}
    earlier = {"PostToolUse": [{"matcher": ".*", "hooks": [{"command": f"python3 -S -I {script} antigravity x"}]}]}
    (folder / "hooks.json").write_text(json.dumps({"lint": lint, "capture": earlier}), encoding="utf-8")

    assert cli.main(["init", "--agent", "antigravity", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "every Antigravity conversation of this user" in out and '"orchestraty"' in out
    got = read(folder / "hooks.json")
    assert got["lint"] == lint                                         # another hook: untouched
    assert "capture" not in got                                        # an earlier hook of ours, replaced
    ours = got["orchestraty"]
    assert ours["enabled"] is True and set(ours) - {"enabled"} == set(antigravity.CAPTURED)
    assert "PreToolUse" not in ours and ours["PostToolUse"][0]["matcher"] == ".*"
    assert all(shell.mentions(h["command"], script) for e in ("SessionStart", "PreInvocation", "Stop") for h in ours[e])
    assert len(backups(state)) == 1

    assert cli.main(["init", "--agent", "antigravity", "--yes"]) == 0
    assert read(folder / "hooks.json") == got and len(backups(state)) == 1

    proc = run_command(ours["PreInvocation"][0]["command"],
                       json.dumps({"conversationId": "ag1", "workspacePaths": [str(tmp_path)],
                                   "invocationNum": 1}).encode())
    assert (proc.returncode, proc.stdout) == (0, b"{}")                # the answer Antigravity waits for

    assert cli.main(["restore", "--agent", "antigravity", "--yes"]) == 0
    assert read(folder / "hooks.json") == {"lint": lint}


def test_a_workspace_hooks_file_is_shown_kept_out_of_git_and_never_committed(state, repo, git, capsys,
                                                                             monkeypatch):
    monkeypatch.chdir(repo)
    head = git(repo, "rev-parse", "HEAD")
    assert cli.main(["init", "--agent", "antigravity", "--project", "--yes", "--no-git-hooks"]) == 0
    out = capsys.readouterr().out
    assert "inside your workspace" in out and "never commits it" in out and "info" in out
    assert "orchestraty" in read(repo / ".agents" / "hooks.json")
    exclude = repo / ".git" / "info" / "exclude"
    assert exclude.read_text(encoding="utf-8").splitlines().count("/.agents/hooks.json") == 1
    assert git(repo, "status", "--porcelain") == ""                    # out of git
    assert git(repo, "rev-parse", "HEAD") == head                      # nothing committed

    assert cli.main(["init", "--agent", "antigravity", "--project", "--yes", "--no-git-hooks"]) == 0
    assert exclude.read_text(encoding="utf-8").splitlines().count("/.agents/hooks.json") == 1   # added once

    assert cli.main(["restore", "--agent", "antigravity", "--project", "--yes"]) == 0
    assert read(repo / ".agents" / "hooks.json") == {}


def test_a_workspace_file_git_already_tracks_is_left_to_the_person(state, repo, git, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    (repo / ".agents").mkdir()
    (repo / ".agents" / "hooks.json").write_text("{}\n", encoding="utf-8")
    git(repo, "add", ".agents/hooks.json")
    git(repo, "commit", "-q", "-m", "their hooks")
    head = git(repo, "rev-parse", "HEAD")
    assert cli.main(["init", "--agent", "antigravity", "--project", "--yes", "--no-git-hooks"]) == 0
    assert "git tracks .agents/hooks.json" in capsys.readouterr().out
    assert "/.agents/hooks.json" not in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert git(repo, "rev-parse", "HEAD") == head and ".agents/hooks.json" in git(repo, "status", "--porcelain")


# --------------------------------------------------------------------------------------- #
# The command line, for each shell
# --------------------------------------------------------------------------------------- #
def test_the_command_line_suits_each_shell():
    argv = ["/opt/py 3/bin/python3", "-S", "-I", "/home/a b/.local/state/orchestraty/hooks/hook.py", "codex", "Stop"]
    assert shell.command_line(argv, windows=False) == (
        "'/opt/py 3/bin/python3' -S -I '/home/a b/.local/state/orchestraty/hooks/hook.py' codex Stop")
    plain = [r"C:\Python312\python.exe", "-S", "-I", r"C:\Users\me\.orchestraty\hooks\hook.py", "codex", "Stop"]
    assert shell.command_line(plain, windows=True) == " ".join(plain)          # the same in cmd and PowerShell
    spaced = [r"C:\Program Files\Python312\python.exe", "-S", "-I", r"C:\Users\Jo O'Neil\.orchestraty\hooks\hook.py",
              "antigravity", "Stop"]
    short = {spaced[0]: r"C:\PROGRA~1\PYTHON~1\python.exe", spaced[3]: r"C:\Users\JOONEI~1\.ORCHE~1\hooks\hook.py"}
    assert shell.command_line(spaced, windows=True, shorten=lambda p: short.get(p, p)) == (
        r"C:\PROGRA~1\PYTHON~1\python.exe -S -I C:\Users\JOONEI~1\.ORCHE~1\hooks\hook.py antigravity Stop")
    assert shell.command_line(spaced, windows=True, shorten=lambda p: p) == (       # no short names: PowerShell's
        r"& 'C:\Program Files\Python312\python.exe' -S -I 'C:\Users\Jo O''Neil\.orchestraty\hooks\hook.py' "
        "antigravity Stop")
    assert shell.mentions(shell.command_line(argv, windows=False), argv[3])
    assert not shell.mentions("node lint.js", argv[3]) and not shell.mentions(None, argv[3])


def test_codex_init_with_another_state_folder_takes_the_earlier_hooks_out(state, tmp_path, capsys, monkeypatch):
    home = Path(os.environ["CODEX_HOME"])
    old = tmp_path / "One Drive" / "state"                                  # before doctor's fix for a synced folder
    monkeypatch.setenv("ORCH_HOME", str(old))
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    monkeypatch.setenv("ORCH_HOME", state.root)
    from orchestraty import doctor, config
    [line] = [x for x in doctor.agent_checks(State(), config.load(State())) if "also runs the hooks" in x.text]
    assert str(old) in line.text and "orch init --agent codex again" in line.fix
    capsys.readouterr()
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    assert f"take out of it the hooks of the state folder {old}" in capsys.readouterr().out
    commands = [h["command"] for entries in read(home / "hooks.json")["hooks"].values() for e in entries
                for h in e["hooks"]]
    assert len(commands) == len(codex.CAPTURED) and all(shell.mentions(c, state.hook_script) for c in commands)
    # the forms orch init writes on each system are all recognised as another folder's
    for argv_script in (str(old / "hooks" / "hook.py"),):
        for windows in (True, False):
            line = shell.command_line(["/py/python", "-S", "-I", argv_script, "codex", "Stop"], windows=windows,
                                      shorten=lambda p: p)
            config = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": line}]}]}}
            assert codex.other_states(config, state.hook_script) == [str(old)], line
            assert codex.without_others(config, state.hook_script) == {}


def test_codex_takes_out_an_earlier_folders_hooks_whose_hook_script_is_gone(state, tmp_path, capsys, monkeypatch):
    home = Path(os.environ["CODEX_HOME"])
    old = tmp_path / "old state"                            # a space: on Windows its lines hold the short (8.3) form
    monkeypatch.setenv("ORCH_HOME", str(old))
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    (old / "hooks" / "hook.py").unlink()                    # the folder stays, so it has a long form, the script none
    monkeypatch.setenv("ORCH_HOME", state.root)
    capsys.readouterr()
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    assert f"take out of it the hooks of the state folder {old}" in capsys.readouterr().out
    commands = [h["command"] for entries in read(home / "hooks.json")["hooks"].values() for e in entries
                for h in e["hooks"]]
    assert len(commands) == len(codex.CAPTURED) and all(shell.mentions(c, state.hook_script) for c in commands)
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    out = capsys.readouterr().out
    assert "nothing changed" in out and "take out of it" not in out
    from orchestraty import doctor, config
    assert not [x for x in doctor.agent_checks(State(), config.load(State())) if "also runs the hooks" in x.text]


def test_an_earlier_folders_short_form_line_is_taken_out_as_it_is_written(state, tmp_path, monkeypatch):
    long_root, short_root = str(tmp_path / "old state"), str(tmp_path / "OLDSTA~1")
    monkeypatch.setattr(shell, "long_path", lambda p: long_root if p == short_root else p)   # the folder is there
    line = shell.command_line(["/py/python", "-S", "-I", os.path.join(short_root, "hooks", "hook.py"), "codex", "Stop"],
                              windows=True, shorten=lambda p: p)
    config = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": line}]}]}}
    assert codex.other_states(config, state.hook_script) == [long_root]
    assert codex.without_others(config, state.hook_script) == {}
    assert codex.without_orchestraty(config, state.hook_script) == {}


def test_an_earlier_state_folder_whose_path_has_an_apostrophe_is_found_in_every_form(state, tmp_path):
    for name, python in (("Jo O'Neil", "/py/python"), ("Jo O'Neil $x", "/py/python"), ("Jo O'Neil", "/py thon/python"),
                         ("o''neil", "/py/python")):
        old = tmp_path / name / "old"
        for windows in (True, False):          # double quotes, PowerShell's '' and sh's '"'"'
            line = shell.command_line([python, "-S", "-I", str(old / "hooks" / "hook.py"), "codex", "Stop"],
                                      windows=windows, shorten=lambda p: p)
            config = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": line}]}]}}
            assert codex.other_states(config, state.hook_script) == [str(old)], line
            assert codex.without_others(config, state.hook_script) == {}, line


def test_codex_restore_from_backup_leaves_out_an_earlier_state_folders_hooks(state, tmp_path, capsys, monkeypatch):
    home = Path(os.environ["CODEX_HOME"])
    old = tmp_path / "OneDrive" / "state"
    monkeypatch.setenv("ORCH_HOME", str(old))
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    monkeypatch.setenv("ORCH_HOME", state.root)
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0     # its backup holds old's hooks
    shutil.rmtree(old)                                                                  # README: it can be deleted
    capsys.readouterr()
    assert cli.main(["restore", "--agent", "codex", "--from-backup", "--yes"]) == 0
    assert f"without the hooks of the state folder {old}" in capsys.readouterr().out
    assert read(home / "hooks.json") == {}                    # no line left that runs a script that is gone



def test_antigravity_restore_from_backup_leaves_out_an_earlier_state_folders_hook(state, tmp_path, capsys,
                                                                                 monkeypatch):
    folder = Path(os.environ["HOME"]) / ".gemini" / "config"
    folder.mkdir(parents=True)
    (folder / "hooks.json").write_text(json.dumps({"lint": {"Stop": [{"command": "echo done"}]}}), encoding="utf-8")
    old = tmp_path / "OneDrive" / "state"
    monkeypatch.setenv("ORCH_HOME", str(old))
    assert cli.main(["init", "--agent", "antigravity", "--yes", "--no-git-hooks"]) == 0
    monkeypatch.setenv("ORCH_HOME", state.root)
    assert cli.main(["init", "--agent", "antigravity", "--yes", "--no-git-hooks"]) == 0  # its backup holds old's hook
    assert antigravity.other_states(read(folder / "hooks.json"), state.hook_script) == []
    shutil.rmtree(old)                                                                    # README: it can be deleted
    capsys.readouterr()
    assert cli.main(["restore", "--agent", "antigravity", "--from-backup", "--yes"]) == 0
    assert f"without the hooks of the state folder {old}" in capsys.readouterr().out
    assert read(folder / "hooks.json") == {"lint": {"Stop": [{"command": "echo done"}]}}   # nothing that runs a gone script

def test_the_plan_says_when_the_line_it_shows_is_written_in_short_form(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORCH_HOME", str(tmp_path / "fresh plan state"))     # not there yet: no short form yet
    home = Path(os.environ["CODEX_HOME"])
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    out = capsys.readouterr().out
    written = read(home / "hooks.json")["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert json.dumps(written) in out or "short 8.3 form" in out           # the line shown, or a word that it changes
    assert ("short 8.3 form" in out) == (os.name == "nt")
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    out = capsys.readouterr().out
    assert json.dumps(written) in out and "short 8.3 form" not in out       # the script is there: the line is the one


def test_the_command_line_quotes_in_double_quotes_when_the_program_needs_none():
    script = r"C:\Users\Jo O'Neil\.orchestraty\hooks\hook.py"
    argv = [r"C:\Python312\python.exe", "-S", "-I", script, "codex", "Stop"]
    assert shell.command_line(argv, windows=True, shorten=lambda p: p) == (     # read by cmd and PowerShell
        rf'C:\Python312\python.exe -S -I "{script}" codex Stop')
    for odd in (r"C:\Users\a $b\hook.py", r"C:\Users\a %b%\hook.py", r"C:\Users\a `b\hook.py"):
        line = shell.command_line(argv[:3] + [odd] + argv[4:], windows=True, shorten=lambda p: p)
        assert line.startswith("& ") and "'" + odd + "'" in line              # one of them would expand it
    assert shell.mentions(shell.command_line(argv, windows=True, shorten=lambda p: p), script)


def test_a_state_folder_with_a_space_gets_its_command_line_from_the_first_init(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORCH_HOME", str(tmp_path / "fresh state"))          # not there yet
    state = State()
    home = Path(os.environ["CODEX_HOME"])
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    first = read(home / "hooks.json")
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    assert "nothing changed" in capsys.readouterr().out                     # the same line: no new trust needed
    assert read(home / "hooks.json") == first and backups(state) == []
    command = first["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    payload = json.dumps({"session_id": "cx2", "cwd": str(tmp_path), "hook_event_name": "SessionStart",
                          "source": "startup"}).encode()
    proc = run_command(command, payload)                                    # cmd on Windows, sh elsewhere
    assert (proc.returncode, proc.stdout) == (0, b""), proc.stderr
    assert (Path(state.root) / "spool" / "codex" / "cx2.jsonl").exists()
    if os.name == "nt" and shutil.which("powershell"):
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], input=payload,
                              capture_output=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
