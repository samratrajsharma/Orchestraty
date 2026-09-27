"""Tests for the git-hook capture chain. Every repository is synthetic, in a temp folder.

The simulated hook managers below lay out hooks the way husky, lefthook and pre-commit do,
so the chain is tested on every CI job. When the real tools are installed in the phase-0
tools folder, the same checks also run against them (skipped otherwise)."""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import capture_githook as rec
import capture_githooks as gh
import capture_hooks

SWITCHES = ["-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "protocol.file.allow=always",
            "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "user.name=Test", "-c", "user.email=t@example.com"]
TOOLS = Path(os.environ.get("ORCH_PHASE0_TOOLS") or os.path.join(capture_hooks.default_root(), "tools"))


def git(repo, *args, stdin=None, env=None, check=True):
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    if env:
        e.update(env)
    proc = subprocess.run(["git"] + SWITCHES + list(args), cwd=repo, input=stdin, capture_output=True, env=e,
                          timeout=120)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr.decode(errors='replace')}")
    return proc


@pytest.fixture
def phase0(tmp_path, monkeypatch):
    """A synthetic phase-0 folder with an empty global git config, a repo and a bare remote."""
    root = tmp_path / "orch-phase0"
    (root / "repos").mkdir(parents=True)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    repo = root / "repos" / "scratch"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("1\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "base")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    return root, repo, remote


def spool_events(root):
    lines = []
    for path in sorted((root / "spool" / "git").glob("*.jsonl")):
        lines += [json.loads(line) for line in path.read_bytes().splitlines()]
    return lines


def write_hook(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:     # Path.write_text(newline=) needs 3.10
        fh.write("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)


def exercise(repo, remote):
    """Fire all five captured hooks: commit, checkout, merge, rebase (post-rewrite), push."""
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "second")                                   # post-commit
    git(repo, "checkout", "-q", "-b", "feat")                                    # post-checkout
    (repo / "c.txt").write_text("3\n", encoding="utf-8")
    git(repo, "add", "c.txt")
    git(repo, "commit", "-q", "-m", "feat")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feat", "feat")              # post-merge
    git(repo, "commit", "-q", "--amend", "-m", "merge feat, amended")            # post-rewrite (amend)
    return git(repo, "push", "-q", str(remote), "main", check=False)             # pre-push


# ----------------------------------------------------------------- the recorder


def test_the_recorder_keeps_ids_and_flags_but_never_a_remote_url():
    assert rec.kept_args("pre-push", ["origin", "https://user:tok3n@git.example.com/x.git"]) == [b"origin"]
    assert rec.kept_args("post-checkout", ["a" * 40, "b" * 40, "1"]) == [b"a" * 40, b"b" * 40, b"1"]
    assert rec.kept_args("post-rewrite", ["amend"]) == [b"amend"]
    rows = rec.ref_lines("pre-push", b"refs/heads/main " + b"a" * 40 + b" refs/heads/we ird\" " + b"0" * 40 + b"\n")
    assert rows == []                                                             # 5 fields: not a ref line
    rows = rec.ref_lines("pre-push", b"refs/heads/main " + b"a" * 40 + b' refs/heads/we"ird ' + b"0" * 40 + b"\n")
    assert rows == [[b"refs/heads/main", b"a" * 40, b"?", b"0" * 40]]


def test_the_recorder_line_is_valid_json_for_any_folder_name(tmp_path):
    cwd = str(tmp_path / 'we "ird" \\ folder é代')
    line = rec.event_line("post-commit", [], b"", cwd, 1, 2)
    event = json.loads(line)
    assert event["cwd"] == cwd and event["event"] == "post-commit" and event["head"] == ""
    assert rec.json_string("a\x01\ud800b") == b'"a\\u0001\\ud800b"'


def test_the_recorder_is_silent_and_fails_open(tmp_path):
    blocked = tmp_path / "spool-is-a-file"
    blocked.write_text("x", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-S", "-I", "-B", str(Path(rec.__file__)), str(blocked), "post-commit"],
                          capture_output=True, timeout=60, cwd=tmp_path)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""


# ----------------------------------------------------------------- the chain, with simulated managers


def test_all_five_hooks_are_recorded_and_ours_prints_nothing(phase0):
    root, repo, remote = phase0
    gh.install(str(root), str(repo), sys.executable)
    push = exercise(repo, remote)
    assert push.returncode == 0 and push.stdout == b"" and push.stderr == b""
    events = spool_events(root)
    assert {e["event"] for e in events} == set(rec.HOOKS)
    [pre_push] = [e for e in events if e["event"] == "pre-push"]
    assert pre_push["refs"][0][0] == "refs/heads/main" and pre_push["head"] == "refs/heads/main"
    assert all(e["cwd"] and os.path.normcase(e["cwd"]) == os.path.normcase(str(repo)) for e in events)


def test_a_failing_recorder_never_blocks_and_prints_nothing(phase0):
    root, repo, remote = phase0
    gh.install(str(root), str(repo), sys.executable)
    shutil.rmtree(root / "spool")
    (root / "spool").write_text("not a folder", encoding="utf-8")                # every record now fails
    (repo / "d.txt").write_text("4\n", encoding="utf-8")
    git(repo, "add", "d.txt")
    commit = git(repo, "commit", "-q", "-m", "still works")
    push = git(repo, "push", "-q", str(remote), "main", check=False)
    assert commit.stdout == b"" and commit.stderr == b""
    assert push.returncode == 0 and push.stdout == b"" and push.stderr == b""


@pytest.mark.parametrize("manager", ["husky-like", "lefthook-like", "pre-commit-like"])
def test_the_earlier_hooks_still_run_and_their_exit_code_decides(phase0, tmp_path, manager):
    root, repo, remote = phase0
    marker = tmp_path / "earlier-hooks.log"
    m = marker.as_posix()
    if manager == "husky-like":                  # core.hooksPath=.husky/_, which runs .husky/<hook>
        runner = 'n=$(basename "$0"); s="$(dirname "$(dirname "$0")")/$n"; [ -f "$s" ] || exit 0; sh -e "$s" "$@"\n'
        for hook in ("pre-push", "pre-commit", "post-commit"):
            write_hook(repo / ".husky" / "_" / hook, runner)
        write_hook(repo / ".husky" / "pre-push", f'cat >> "{m}"\necho "pre-push $1" >> "{m}"\nexit 1\n')
        write_hook(repo / ".husky" / "pre-commit", f'echo pre-commit >> "{m}"\n')
        git(repo, "config", "core.hooksPath", ".husky/_")
    else:                                        # both install into .git/hooks
        hooks = repo / ".git" / "hooks"
        write_hook(hooks / "pre-push", f'cat >> "{m}"\necho "pre-push $1" >> "{m}"\nexit 1\n')
        write_hook(hooks / "pre-commit", f'echo pre-commit >> "{m}"\n')
    gh.install(str(root), str(repo), sys.executable)
    (repo / "e.txt").write_text("5\n", encoding="utf-8")
    git(repo, "add", "e.txt")
    git(repo, "commit", "-q", "-m", "change")                                    # pre-commit passes through
    head = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    push = git(repo, "push", "-q", str(remote), "main", check=False)
    assert push.returncode != 0, "a failing pre-push hook must still stop the push"
    assert git(remote, "rev-parse", "--verify", "-q", "refs/heads/main", check=False).returncode != 0
    log = marker.read_text(encoding="utf-8")
    assert "pre-commit" in log, "a hook the chain does not record must still pass through"
    assert "pre-push " in log, "the earlier pre-push hook must run, with its arguments"
    assert f"refs/heads/main {head} refs/heads/main" in log, "the earlier hook must get the ref lines on stdin"
    assert [e["event"] for e in spool_events(root)].count("pre-push") == 1


def test_uninstall_restores_the_earlier_setting(phase0):
    root, repo, _ = phase0
    git(repo, "config", "core.hooksPath", ".husky/_")
    chain = gh.install(str(root), str(repo), sys.executable)
    assert git(repo, "config", "--get", "core.hooksPath").stdout.decode().strip() == Path(chain).as_posix()
    gh.install(str(root), str(repo), sys.executable)                              # a second install keeps the original
    assert gh.uninstall(str(root), str(repo)) is True
    assert git(repo, "config", "--get", "core.hooksPath").stdout.decode().strip() == ".husky/_"
    assert not Path(chain).exists()
    gh.install(str(root), str(repo), sys.executable)
    git(repo, "config", "--unset", "core.hooksPath")
    git(repo, "config", "core.hooksPath", chain.replace("\\", "/"))
    (Path(chain) / gh.ORIGINAL_FILE).write_text("\n", encoding="utf-8")         # installed with nothing set
    gh.uninstall(str(root), str(repo))
    assert git(repo, "config", "--get", "core.hooksPath", check=False).returncode == 1


def test_only_scratch_repositories_under_the_phase0_folder_are_accepted(phase0, tmp_path):
    root, repo, _ = phase0
    elsewhere = tmp_path / "real-project"
    elsewhere.mkdir()
    git(elsewhere, "init", "-q")
    for bad_root, bad_repo in ((root, elsewhere), (tmp_path / "not-phase0", repo), (root, root / "repos")):
        with pytest.raises(gh.Refused):
            gh.install(str(bad_root), str(bad_repo), sys.executable)
    assert git(elsewhere, "config", "--get", "core.hooksPath", check=False).returncode == 1


# ----------------------------------------------------------------- the real tools, when installed


def _tool(*parts):
    path = TOOLS.joinpath(*parts)
    return path if path.exists() else None


def _failing_push(repo, remote, root, env=None):
    (repo / "f.txt").write_text("6\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "change", env=env)
    push = git(repo, "push", "-q", str(remote), "main", check=False, env=env)
    assert push.returncode != 0, push.stderr
    assert git(remote, "rev-parse", "--verify", "-q", "refs/heads/main", check=False).returncode != 0
    assert "pre-push" in [e["event"] for e in spool_events(root)]


@pytest.mark.skipif(not _tool("npm", "node_modules", "husky", "bin.js") or not shutil.which("node"),
                    reason="husky is not installed in the phase-0 tools folder")
def test_real_husky_still_runs_and_stops_the_push(phase0, tmp_path):
    root, repo, remote = phase0
    marker = (tmp_path / "husky.log").as_posix()
    subprocess.run(["node", str(_tool("npm", "node_modules", "husky", "bin.js"))], cwd=repo, check=True,
                   capture_output=True, timeout=120)
    (repo / ".husky" / "pre-commit").write_text(f'echo husky-pre-commit >> "{marker}"\n', encoding="utf-8")
    (repo / ".husky" / "pre-push").write_text(f'echo husky-pre-push >> "{marker}"\nexit 1\n', encoding="utf-8")
    gh.install(str(root), str(repo), sys.executable)
    _failing_push(repo, remote, root)
    assert Path(marker).read_text(encoding="utf-8").split() == ["husky-pre-commit", "husky-pre-push"]


def _lefthook_binary():
    for name in ("lefthook-windows-x64", "lefthook-linux-x64", "lefthook-darwin-arm64", "lefthook-darwin-x64"):
        for exe in ("lefthook.exe", "lefthook"):
            found = _tool("npm", "node_modules", name, "bin", exe)
            if found:
                return found
    return None


@pytest.mark.skipif(not _lefthook_binary(), reason="lefthook is not installed in the phase-0 tools folder")
def test_real_lefthook_still_runs_and_stops_the_push(phase0, tmp_path):
    root, repo, remote = phase0
    marker = (tmp_path / "lefthook.log").as_posix()
    (repo / "lefthook.yml").write_text(textwrap.dedent(f"""\
        pre-commit:
          commands:
            mark:
              run: echo lefthook-pre-commit >> "{marker}"
        pre-push:
          commands:
            stop:
              run: exit 1
        """), encoding="utf-8")
    env = {"LEFTHOOK_BIN": str(_lefthook_binary())}
    subprocess.run([str(_lefthook_binary()), "install"], cwd=repo, check=True, capture_output=True, timeout=120,
                   env={**os.environ, **env})
    gh.install(str(root), str(repo), sys.executable)
    _failing_push(repo, remote, root, env=env)
    assert "lefthook-pre-commit" in Path(marker).read_text(encoding="utf-8")


def _pre_commit():
    return _tool("pre-commit-venv", "Scripts", "pre-commit.exe") or _tool("pre-commit-venv", "bin", "pre-commit")


@pytest.mark.skipif(not _pre_commit(), reason="pre-commit is not installed in the phase-0 tools folder")
def test_real_pre_commit_still_runs_and_stops_the_push(phase0, tmp_path):
    root, repo, remote = phase0
    # A local hook only: remote hook repositories would be cloned from the network.
    (repo / ".pre-commit-config.yaml").write_text(textwrap.dedent("""\
        repos:
          - repo: local
            hooks:
              - id: stop-push
                name: stop push
                entry: git rev-parse --verify --quiet refs/heads/does-not-exist
                language: system
                pass_filenames: false
                always_run: true
                stages: [pre-push]
        """), encoding="utf-8")
    env = {"PRE_COMMIT_HOME": str(tmp_path / "pre-commit-home")}
    subprocess.run([str(_pre_commit()), "install", "--hook-type", "pre-push"], cwd=repo, check=True,
                   capture_output=True, timeout=120, env={**os.environ, **env})
    gh.install(str(root), str(repo), sys.executable)
    _failing_push(repo, remote, root, env=env)
