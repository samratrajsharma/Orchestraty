import os

import pytest

from orchestraty import gitio


def same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def test_the_main_worktree(repo, git):
    info = gitio.worktree_info(str(repo))
    assert same(info["top"], repo) and same(info["common_dir"], repo / ".git")
    assert info["branch"] == "main" and info["head"] == git(repo, "rev-parse", "HEAD")


def test_a_subfolder_and_a_linked_worktree_share_the_common_folder(repo, git, tmp_path):
    (repo / "sub").mkdir()
    assert same(gitio.worktree_info(str(repo / "sub"))["top"], repo)
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(tmp_path / "wt-b"))
    info = gitio.worktree_info(str(tmp_path / "wt-b"))
    assert same(info["top"], tmp_path / "wt-b") and same(info["common_dir"], repo / ".git")
    assert info["branch"] == "feat-b"


def test_detached_head_and_unborn_branch(repo, git, tmp_path):
    git(repo, "checkout", "-q", "--detach")
    info = gitio.worktree_info(str(repo))
    assert info["branch"] == "" and len(info["head"]) == 40
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    info = gitio.worktree_info(str(empty))
    assert info["branch"] == "main" and info["head"] == ""


def test_not_a_worktree(tmp_path, git):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitio.worktree_info(str(plain)) is None
    assert gitio.worktree_info(str(tmp_path / "missing")) is None
    assert gitio.worktree_info("") is None


def test_an_outer_gits_variables_cannot_redirect_a_command(repo, git, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))       # what git sets for a hook in another repository
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    info = gitio.worktree_info(str(repo))
    assert same(info["top"], repo) and info["branch"] == "main"


def test_a_failing_command_raises(repo):
    with pytest.raises(gitio.GitError):
        gitio.run(["rev-parse", "--verify", "no-such-ref"], str(repo))
