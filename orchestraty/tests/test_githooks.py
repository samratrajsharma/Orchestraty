"""The git hook chain for the push line, on synthetic repositories with a bare remote."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestraty import cli, config
from orchestraty.capture import githooks, install
from orchestraty.delivery import slots
from orchestraty.paths import State


def write_hook(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    os.chmod(path, 0o755)


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def remote(tmp_path, git):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    return bare


def push(git_env_repo, remote):
    env = {k: v for k, v in os.environ.items() if k != "ORCH_NO_COORDINATOR"}
    return subprocess.run(["git", "-c", "protocol.file.allow=always", "push", "-q", str(remote), "main"],
                          cwd=git_env_repo, capture_output=True, env=env, timeout=120)


def test_pre_push_prints_the_push_line_and_the_push_goes_through(state, repo, remote, hook_script):
    githooks.install(str(repo), sys.executable, str(hook_script))
    assert githooks.installed(str(repo))
    line = Path(state.root) / "push" / (slots.file_key(str(repo)) + ".txt")
    line.parent.mkdir(parents=True)
    line.write_text("orchestraty: 1 caller(s) of charge still use its old form (pay). Run: orch status\n",
                    encoding="utf-8")
    proc = push(repo, remote)
    assert proc.returncode == 0, proc.stderr
    assert b"orchestraty: 1 caller(s) of charge still use its old form (pay)" in proc.stderr
    line.unlink()
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "x.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", "x"], cwd=repo,
                   check=True, capture_output=True)
    quiet = push(repo, remote)
    assert quiet.returncode == 0 and b"orchestraty" not in quiet.stderr


@pytest.mark.parametrize("manager", ["git-hooks-folder", "husky-like"])
def test_the_earlier_hooks_still_run_and_their_exit_code_decides(state, repo, remote, hook_script, tmp_path, manager):
    marker = (tmp_path / "earlier.log").as_posix()
    if manager == "husky-like":                  # core.hooksPath=.husky/_, which runs .husky/<hook>
        runner = 'n=$(basename "$0"); s="$(dirname "$(dirname "$0")")/$n"; [ -f "$s" ] || exit 0; sh -e "$s" "$@"\n'
        for hook in ("pre-push", "pre-commit"):
            write_hook(repo / ".husky" / "_" / hook, runner)
        write_hook(repo / ".husky" / "pre-push", f'cat >> "{marker}"\necho "pre-push $1" >> "{marker}"\nexit 1\n')
        write_hook(repo / ".husky" / "pre-commit", f'echo pre-commit >> "{marker}"\n')
        subprocess.run(["git", "config", "core.hooksPath", ".husky/_"], cwd=repo, check=True)
    else:
        hooks = repo / ".git" / "hooks"
        write_hook(hooks / "pre-push", f'cat >> "{marker}"\necho "pre-push $1" >> "{marker}"\nexit 1\n')
        write_hook(hooks / "pre-commit", f'echo pre-commit >> "{marker}"\n')
    githooks.install(str(repo), sys.executable, str(hook_script))
    (repo / "e.py").write_text("e = 5\n", encoding="utf-8")
    subprocess.run(["git", "add", "e.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", "change"], cwd=repo,
                   check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    proc = push(repo, remote)
    assert proc.returncode != 0, "a failing pre-push hook must still stop the push"
    log = Path(marker).read_text(encoding="utf-8")
    assert "pre-commit" in log, "a hook the chain does nothing with must still pass through"
    assert "pre-push " in log, "the earlier pre-push hook runs, with its arguments"
    assert f"refs/heads/main {head} refs/heads/main" in log, "the earlier hook gets the ref lines on stdin"


def test_uninstall_puts_the_earlier_setting_back(state, repo, hook_script):
    subprocess.run(["git", "config", "core.hooksPath", ".husky/_"], cwd=repo, check=True)
    chain = githooks.install(str(repo), sys.executable, str(hook_script))
    current = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=repo, capture_output=True, text=True)
    assert current.stdout.strip() == Path(chain).as_posix()
    githooks.install(str(repo), sys.executable, str(hook_script))     # again: keeps the first original
    assert githooks.uninstall(str(repo)) is True
    current = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=repo, capture_output=True, text=True)
    assert current.stdout.strip() == ".husky/_" and not Path(chain).exists()
    assert githooks.uninstall(str(repo)) is False
    githooks.install(str(repo), sys.executable, str(hook_script))
    subprocess.run(["git", "config", "--unset", "core.hooksPath"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", Path(chain).as_posix()], cwd=repo, check=True)
    (Path(chain) / githooks.ORIGINAL_FILE).write_text("\n", encoding="utf-8")   # installed with nothing set
    githooks.uninstall(str(repo))
    gone = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=repo, capture_output=True)
    assert gone.returncode == 1


def test_the_chain_works_from_a_linked_worktree(state, repo, remote, hook_script, tmp_path, git):
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(tmp_path / "wt-b"))
    githooks.install(str(tmp_path / "wt-b"), sys.executable, str(hook_script))
    assert githooks.installed(str(repo))                          # one setting for every worktree
    line = Path(state.root) / "push" / (slots.file_key(str(tmp_path / "wt-b")) + ".txt")
    line.parent.mkdir(parents=True)
    line.write_text("orchestraty: from wt-b\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "ORCH_NO_COORDINATOR"}
    proc = subprocess.run(["git", "-c", "protocol.file.allow=always", "push", "-q", str(remote), "feat-b"],
                          cwd=tmp_path / "wt-b", capture_output=True, env=env, timeout=120)
    assert proc.returncode == 0 and b"orchestraty: from wt-b" in proc.stderr


# --------------------------------------------------------------------------------------- #
# orch init, orch unwatch, orch config
# --------------------------------------------------------------------------------------- #
def test_orch_init_adds_the_chain_and_unwatch_takes_it_out(state, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0
    assert githooks.installed(str(repo)) and "Added:" in capsys.readouterr().out
    assert cli.main(["init", "--yes"]) == 0
    assert "already in this repository's git hooks" in capsys.readouterr().out
    assert cli.main(["unwatch"]) == 0
    assert not githooks.installed(str(repo)) and "Took the push line out" in capsys.readouterr().out


def test_orch_init_can_skip_the_chain(state, repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes", "--no-git-hooks"]) == 0
    assert not githooks.installed(str(repo))


def test_orch_config_steer_and_wake(state, repo, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(repo)
    assert cli.main(["config"]) == 0
    assert capsys.readouterr().out == "steer: off\nwake: off\n"
    assert cli.main(["config", "steer", "on"]) == 0 and config.load(state)["steer"] is True
    assert cli.main(["config", "wake", "on"]) == 0
    assert "Run orch init" in capsys.readouterr().out             # no hooks installed yet
    assert cli.main(["init", "--yes", "--no-git-hooks"]) == 0     # installs with the wake entry
    settings = Path(config.load(state)["settings"])
    stop = __import__("json").loads(settings.read_text(encoding="utf-8"))["hooks"]["Stop"]
    wake = [e for e in stop if e["hooks"][0]["args"][-1] == "Wake"]
    assert len(wake) == 1 and wake[0]["hooks"][0]["asyncRewake"] is True
    assert wake[0]["hooks"][0]["timeout"] == install.WAKE_TIMEOUT_SECONDS
    capsys.readouterr()
    assert cli.main(["config", "wake", "off"]) == 0
    assert "Updated the hooks" in capsys.readouterr().out
    stop = __import__("json").loads(settings.read_text(encoding="utf-8"))["hooks"]["Stop"]
    assert [e["hooks"][0]["args"][-1] for e in stop] == ["Stop"]
    assert cli.main(["config", "wake"]) == 0 and capsys.readouterr().out == "wake: off\n"
