"""The git hook chain for the push line, on synthetic repositories with a bare remote."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from orchestraty import cli, config, doctor
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
    assert capsys.readouterr().out == "steer: off\nwake: off\ntrack: off\n"
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


# --------------------------------------------------------------------------------------- #
# The earlier folder: as git works it out, and every hook in it, also ones added later
# --------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("scope", ["global", "local"])
def test_a_hooks_path_with_a_tilde_keeps_running_and_uninstall_puts_back_only_its_own(state, repo, remote, hook_script,
                                                                                    tmp_path, scope):
    marker = (tmp_path / "theirs.log").as_posix()
    write_hook(Path(os.environ["HOME"]) / "ghooks" / "pre-push", f'echo theirs >> "{marker}"\nexit 1\n')
    where = ["--global"] if scope == "global" else []
    subprocess.run(["git", "config"] + where + ["core.hooksPath", "~/ghooks"], cwd=repo, check=True)
    githooks.install(str(repo), sys.executable, str(hook_script))
    proc = push(repo, remote)
    assert proc.returncode != 0 and Path(marker).exists(), "the user's own pre-push runs, and its exit code decides"
    assert githooks.uninstall(str(repo)) is True
    local = subprocess.run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=repo, capture_output=True,
                           text=True)
    assert local.stdout.strip() == ("~/ghooks" if scope == "local" else "")   # a global value stays global


def test_a_hook_added_to_the_earlier_folder_after_install_still_runs(state, repo, hook_script, tmp_path):
    githooks.install(str(repo), sys.executable, str(hook_script))
    write_hook(repo / ".git" / "hooks" / "commit-msg", "exit 1\n")         # by hand, or by a tool, afterwards
    (repo / "c.py").write_text("c = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "c.py"], cwd=repo, check=True, capture_output=True)
    proc = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", "c"], cwd=repo,
                          capture_output=True)
    assert proc.returncode != 0, "a commit-msg hook added after the chain must still decide"


def test_a_hook_with_no_pass_through_ahead_of_time_is_named_and_orch_init_adds_it(state, repo, monkeypatch, capsys):
    from orchestraty import doctor
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0
    assert githooks.unchained(str(repo)) == [] and doctor.git_hooks_check(str(repo)) is None
    write_hook(repo / ".git" / "hooks" / "reference-transaction", "exit 0\n")
    assert githooks.unchained(str(repo)) == ["reference-transaction"]
    assert "reference-transaction" in doctor.git_hooks_check(str(repo)).text
    assert cli.main(["init", "--yes"]) == 0
    assert "already in this repository's git hooks" in capsys.readouterr().out
    assert githooks.unchained(str(repo)) == [] and githooks.installed(str(repo))


def test_a_pass_through_whose_hook_is_gone_and_that_git_would_obey_is_taken_out_again(state, repo, hook_script):
    hooks = repo / ".git" / "hooks"
    write_hook(hooks / "push-to-checkout", "exit 0\n")                   # its presence replaces git's own update
    chain = Path(githooks.install(str(repo), sys.executable, str(hook_script)))
    assert (chain / "push-to-checkout").exists() and (chain / githooks.ORIGINAL_FILE).exists()
    (hooks / "push-to-checkout").unlink()
    githooks.install(str(repo), sys.executable, str(hook_script))
    assert not (chain / "push-to-checkout").exists() and (chain / "commit-msg").exists()
    assert (chain / githooks.ORIGINAL_FILE).exists()


# --------------------------------------------------------------------------------------- #
# What orch init again leaves, rebuilds and rewrites; what uninstall puts back
# --------------------------------------------------------------------------------------- #
def commit(cwd):
    return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m",
                           "c"], cwd=cwd, capture_output=True, timeout=120)


def local_hooks_path(repo):
    return subprocess.run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=repo, capture_output=True,
                          text=True).stdout.strip()


def test_orch_init_again_leaves_the_hooks_a_manager_wrote_into_the_chain_folder(state, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0
    chain = Path(githooks.chain_folder(str(repo)))
    write_hook(chain / "pre-commit", "echo manager >&2\nexit 1\n")       # lefthook install writes into core.hooksPath
    write_hook(chain / "post-index-change", "exit 0\n")                  # not wanted by the chain: not ours to remove
    before = {name: (chain / name).read_bytes() for name in ("pre-commit", "post-index-change")}
    capsys.readouterr()
    assert cli.main(["init", "--yes"]) == 0
    assert {name: (chain / name).read_bytes() for name in before} == before
    assert "Left as found" in capsys.readouterr().out
    assert commit(repo).returncode != 0, "the manager's pre-commit still runs and decides"
    assert b"# Orchestraty:" in (chain / "pre-push").read_bytes()


def test_orch_init_says_when_it_cannot_write_the_chain(state, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0

    def denied(*args):
        raise PermissionError("denied")
    monkeypatch.setattr(githooks, "install", denied)
    capsys.readouterr()
    assert cli.main(["init", "--yes"]) == 0
    assert "Could not write the push line" in capsys.readouterr().err


def test_a_chain_folder_that_is_gone_is_named_by_doctor_and_rebuilt_by_orch_init(state, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0
    write_hook(repo / ".git" / "hooks" / "pre-commit", "exit 1\n")
    chain = Path(githooks.chain_folder(str(repo)))
    shutil.rmtree(chain)
    assert commit(repo).returncode == 0                                  # core.hooksPath names nothing: no hook runs
    problem = doctor.git_hooks_check(str(repo))
    assert problem.kind == "fix" and "gone" in problem.text and "orch init again" in problem.fix
    capsys.readouterr()
    assert cli.main(["init", "--yes"]) == 0
    assert "Rebuilt" in capsys.readouterr().out
    assert (chain / githooks.ORIGINAL_FILE).exists() and githooks.installed(str(repo))
    assert commit(repo).returncode != 0, ".git/hooks runs again"
    assert doctor.git_hooks_check(str(repo)) is None
    shutil.rmtree(chain)
    assert githooks.uninstall(str(repo)) is True and local_hooks_path(repo) == ""


def test_only_the_names_git_runs_as_hooks_are_passed_through_or_named(state, repo, hook_script):
    githooks.install(str(repo), sys.executable, str(hook_script))
    hooks = repo / ".git" / "hooks"
    write_hook(hooks / "README", "notes\n")
    write_hook(hooks / "run-tests-helper", "exit 1\n")
    assert githooks.unchained(str(repo)) == [] and doctor.git_hooks_check(str(repo)) is None
    chain = Path(githooks.install(str(repo), sys.executable, str(hook_script)))
    assert not (chain / "README").exists() and not (chain / "run-tests-helper").exists()


def test_a_relative_hooks_path_runs_each_worktrees_own_copy(state, repo, hook_script, tmp_path, git):
    log = tmp_path / "ran.log"
    wt = tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wt))
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=repo, check=True)
    for top, name in ((repo, "main"), (wt, "wt-b")):
        write_hook(top / ".githooks" / "pre-commit", f'echo {name} >> "{log.as_posix()}"\n')
    githooks.install(str(repo), sys.executable, str(hook_script))
    assert commit(wt).returncode == 0 and log.read_text(encoding="utf-8").split() == ["wt-b"]
    assert commit(repo).returncode == 0 and log.read_text(encoding="utf-8").split() == ["wt-b", "main"]
    write_hook(wt / ".githooks" / "pre-commit", "exit 1\n")
    assert commit(wt).returncode != 0, "a linked worktree's own hook decides there"
    assert githooks.uninstall(str(wt)) is True and local_hooks_path(repo) == ".githooks"


def test_a_hooks_path_set_for_one_worktree_is_refused_and_uninstall_still_takes_ours_out(state, repo, hook_script,
                                                                                       monkeypatch, capsys):
    chain = Path(githooks.install(str(repo), sys.executable, str(hook_script)))
    subprocess.run(["git", "config", "extensions.worktreeConfig", "true"], cwd=repo, check=True)
    subprocess.run(["git", "config", "--worktree", "core.hooksPath", ".husky/_"], cwd=repo, check=True)
    assert not githooks.installed(str(repo))                             # this worktree's value comes first
    assert githooks.uninstall(str(repo)) is True
    assert local_hooks_path(repo) == "" and not chain.exists()
    with pytest.raises(githooks.Refused):
        githooks.install(str(repo), sys.executable, str(hook_script))
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0
    assert "git config --worktree" in capsys.readouterr().out
    assert local_hooks_path(repo) == "" and not chain.exists()


@pytest.mark.parametrize("earlier", ["global", "relative"])
def test_uninstall_of_a_chain_from_before_the_scope_was_kept(state, repo, hook_script, earlier):
    if earlier == "global":                                              # a value from the global config, since changed
        value = (Path(os.environ["HOME"]) / "ghooks-v").as_posix()
        subprocess.run(["git", "config", "--global", "core.hooksPath", value], cwd=repo, check=True)
    else:
        value = ".husky/_"
        subprocess.run(["git", "config", "core.hooksPath", value], cwd=repo, check=True)
    chain = Path(githooks.install(str(repo), sys.executable, str(hook_script)))
    (chain / githooks.ORIGINAL_FILE).write_text(value + "\n", encoding="utf-8", newline="\n")   # one line: no scope
    githooks.install(str(repo), sys.executable, str(hook_script))       # orch init again
    assert (chain / githooks.ORIGINAL_FILE).read_text(encoding="utf-8").splitlines() == [value, ""]
    if earlier == "global":
        changed = (Path(os.environ["HOME"]) / "ghooks-w").as_posix()
        subprocess.run(["git", "config", "--global", "core.hooksPath", changed], cwd=repo, check=True)
    assert githooks.uninstall(str(repo)) is True
    assert local_hooks_path(repo) == ("" if earlier == "global" else value)


def test_the_refresh_keeps_the_scope_it_recorded(state, repo, hook_script):
    subprocess.run(["git", "config", "core.hooksPath", ".husky/_"], cwd=repo, check=True)
    chain = Path(githooks.install(str(repo), sys.executable, str(hook_script)))
    githooks.install(str(repo), sys.executable, str(hook_script))
    assert (chain / githooks.ORIGINAL_FILE).read_text(encoding="utf-8").splitlines() == [".husky/_", "local"]


def test_doctor_names_a_push_line_whose_hook_script_is_gone_and_orch_init_rewrites_it(state, repo, monkeypatch,
                                                                                      tmp_path):
    gone = tmp_path / "old state" / "hooks" / "hook.py"                 # an earlier ORCH_HOME, deleted since
    githooks.install(str(repo), sys.executable, str(gone))
    problem = doctor.git_hooks_check(str(repo))
    assert problem.kind == "fix" and gone.as_posix() in problem.text and "orch init again" in problem.fix
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0
    assert doctor.git_hooks_check(str(repo)) is None
    pre_push = (Path(githooks.chain_folder(str(repo))) / "pre-push").read_text(encoding="utf-8")
    assert Path(state.hook_script).as_posix() in pre_push and gone.as_posix() not in pre_push
