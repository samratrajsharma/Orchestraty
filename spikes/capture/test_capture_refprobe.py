"""Tests for the custom-ref probe. The remote is always a local bare repository in a temp folder."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import capture_refprobe as rp

SWITCHES = ["-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "maintenance.auto=false",
            "-c", "gc.auto=0", "-c", "user.name=Test", "-c", "user.email=t@example.com"]


def git(repo, *args, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_GLOBAL": os.environ["GIT_CONFIG_GLOBAL"]})
    proc = subprocess.run(["git"] + SWITCHES + list(args), cwd=repo, capture_output=True, env=env, timeout=120)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr.decode(errors='replace')}")
    return proc.stdout.decode().strip()


def refs(repo):
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A scratch clone under <root>/repos, with a bare remote as origin that has main."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    root = tmp_path / "orch-phase0"
    repo = root / "repos" / "work"
    repo.mkdir(parents=True)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")                  # the clone's own identity, which the probe must not use
    git(repo, "config", "user.email", "t@example.com")
    (repo / "a.txt").write_text("1\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    return root, repo, remote


def refuse_custom_refs(remote):
    hook = remote / "hooks" / "pre-receive"
    with open(hook, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('#!/bin/sh\nwhile read old new ref; do\n  case "$ref" in refs/orchestraty/*)\n'
                 '    echo "custom refs are not allowed here" >&2; exit 1;;\n  esac\ndone\nexit 0\n')
    hook.chmod(0o755)


def test_an_accepted_custom_ref_is_pushed_listed_fetched_and_removed(clone):
    root, repo, remote = clone
    before_repo, before_remote = refs(repo), refs(remote)
    result = rp.run(str(root), str(repo), "origin", "p1", "laptop")
    custom = result["custom"]
    assert custom["ref"] == "refs/orchestraty/p1/laptop" and custom["push"] == "accepted"
    assert all(custom[k] for k in ("listed", "fetched", "nonce_matches", "deleted", "gone")), custom
    assert result["fallback"] is None and result["network"] is False
    assert refs(repo) == before_repo and refs(remote) == before_remote, "nothing may be left behind"
    assert sorted(p.name for p in (root / "repos").iterdir()) == ["work"], "the temporary repository is removed"
    commit = git(repo, "cat-file", "-p", result["probe_commit"])
    assert "author orchestraty-probe <probe@orchestraty.invalid>" in commit and "t@example.com" not in commit
    assert git(repo, "ls-tree", "--name-only", result["probe_commit"]) == "probe.json"


def test_a_refused_custom_ref_falls_back_to_a_branch(clone):
    root, repo, remote = clone
    refuse_custom_refs(remote)
    before_remote = refs(remote)
    result = rp.run(str(root), str(repo), str(remote), "p1", "laptop")
    assert result["custom"]["push"] == "rejected" and not result["custom"]["listed"]
    assert "pre-receive hook declined" in result["custom"]["push_summary"]
    fallback = result["fallback"]
    assert fallback["ref"] == "refs/heads/orchestraty/p1/laptop" and fallback["push"] == "accepted"
    assert all(fallback[k] for k in ("listed", "fetched", "nonce_matches", "deleted", "gone")), fallback
    assert refs(remote) == before_remote


def test_the_fallback_can_be_probed_as_well(clone):
    root, repo, _ = clone
    result = rp.run(str(root), str(repo), "origin", "p1", "laptop", also_fallback=True)
    assert result["custom"]["gone"] and result["fallback"]["gone"]


# Loopback addresses on a closed port, and an ssh that always fails: even if the check under
# test broke, nothing could leave the machine.
@pytest.mark.parametrize("url", ["https://127.0.0.1:9/scratch.git", "git@127.0.0.1:scratch.git",
                                 "ssh://git@127.0.0.1:9/scratch.git"])
def test_a_network_remote_is_refused_without_the_flag_and_nothing_is_written(clone, url, monkeypatch):
    monkeypatch.setenv("GIT_SSH_COMMAND", "false")
    root, repo, _ = clone
    git(repo, "remote", "add", "net", url)
    objects = git(repo, "count-objects", "-v")
    for remote in ("net", url):
        with pytest.raises(rp.Refused):
            rp.run(str(root), str(repo), remote, "p1", "laptop")
    assert git(repo, "count-objects", "-v") == objects, "no probe commit may be made before the refusal"


@pytest.mark.parametrize("person", ["", "../x", "a b", "x.lock", "-x", "a/b", "p" * 65])
def test_unsafe_person_or_device_names_are_refused(clone, person):
    root, repo, _ = clone
    with pytest.raises(rp.Refused):
        rp.run(str(root), str(repo), "origin", person, "laptop")
    with pytest.raises(rp.Refused):
        rp.run(str(root), str(repo), "origin", "p1", person)


def test_only_scratch_clones_under_the_phase0_folder_are_accepted(clone, tmp_path):
    root, repo, remote = clone
    elsewhere = tmp_path / "real-project"
    elsewhere.mkdir()
    git(elsewhere, "init", "-q")
    for bad_root, bad_repo in ((root, elsewhere), (tmp_path / "not-phase0", repo)):
        with pytest.raises(rp.Refused):
            rp.run(str(bad_root), str(bad_repo), str(remote), "p1", "laptop")


def test_the_command_line_reports_and_refuses_with_exit_2(clone):
    root, repo, remote = clone
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    args = [sys.executable, str(Path(rp.__file__)), "--root", str(root), "--repo", str(repo), "--person", "p1",
            "--device", "laptop"]
    proc = subprocess.run(args + ["--remote", str(remote)], capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "custom | refs/orchestraty/p1/laptop | accepted |" in proc.stdout
    assert "yes | yes | yes | yes | yes" in proc.stdout
    proc = subprocess.run(args + ["--remote", "https://127.0.0.1:9/scratch.git"], capture_output=True,
                          text=True, timeout=120, env=env)
    assert proc.returncode == 2 and "refused" in proc.stderr and proc.stdout == ""


def test_a_ref_already_on_the_remote_is_reported_and_left_alone(clone):
    root, repo, remote = clone
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "-q", str(remote), f"{head}:refs/orchestraty/p1/laptop")
    result = rp.run(str(root), str(repo), "origin", "p1", "laptop")
    assert result["custom"]["push"] == "exists" and result["fallback"] is None
    assert git(remote, "rev-parse", "refs/orchestraty/p1/laptop") == head, "someone else's ref is never touched"


def test_the_clones_own_failing_pre_push_hook_does_not_decide_the_result(clone):
    root, repo, _ = clone
    hook = repo / ".git" / "hooks" / "pre-push"
    with open(hook, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/bin/sh\necho local hook says no >&2\nexit 1\n")
    hook.chmod(0o755)
    result = rp.run(str(root), str(repo), "origin", "p1", "laptop")
    assert result["custom"]["push"] == "accepted" and result["custom"]["gone"]


def test_a_local_remote_must_be_a_bare_repository(clone, tmp_path):
    root, repo, _ = clone
    real = tmp_path / "real-project"
    real.mkdir()
    git(real, "init", "-q")
    for remote in (str(real), "file:///" + str(real).replace("\\", "/")):
        with pytest.raises(rp.Refused):
            rp.run(str(root), str(repo), remote, "p1", "laptop")
    assert git(real, "for-each-ref") == ""


@pytest.mark.parametrize("remote", ["\\\\127.0.0.1\\share\\scratch.git", "//127.0.0.1/share/scratch.git",
                                    "file://127.0.0.1/share/scratch.git", "file:////127.0.0.1/share/scratch.git"])
def test_shares_and_file_urls_with_a_host_are_network_remotes(clone, remote):
    root, repo, _ = clone
    with pytest.raises(rp.Refused):
        rp.run(str(root), str(repo), remote, "p1", "laptop")


def test_the_remote_ref_is_removed_even_when_a_later_step_fails(clone, monkeypatch):
    root, repo, remote = clone
    before = refs(remote)

    def broken(path):
        raise PermissionError("in use")
    monkeypatch.setattr(rp, "_remove_tree", broken)
    result = rp.run(str(root), str(repo), "origin", "p1", "laptop")
    custom = result["custom"]
    assert custom["push"] == "accepted" and custom["deleted"] and custom["gone"]
    assert "left behind" in custom.get("cleanup_error", "")
    assert refs(remote) == before
