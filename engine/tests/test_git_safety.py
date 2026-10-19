"""How the engine runs git: git's ownership check stays on for the user's own paths, every
command carries options that stop a repository's config from starting programs, and only the
engine's own clones are trusted. Every repository here is synthetic, in a temp folder.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from orch_engine import ingest, track
from orch_engine.config import Config
from orch_engine.ingest import UntrustedRepositoryError
from orch_engine.pipeline import build_index, build_index_at


def write(root, files):
    for rel, content in files.items():
        p = Path(root, *rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8"))


def make_repo(git_cmd, path):
    write(path, {"m.py": "def m():\n    return 1\n"})
    git_cmd(path, "init", "-q")
    git_cmd(path, "add", "-A")
    git_cmd(path, "commit", "-q", "-m", "first")
    return path


def _assume_other_owner(monkeypatch, repo):
    """Make git treat every repository as another user's. The probe runs git exactly as the
    engine does; skip where git still does not refuse (a git that ignores the variable)."""
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    argv, env = ingest.git_command(["rev-parse", "--show-toplevel"], str(repo))
    probe = subprocess.run(argv, cwd=repo, capture_output=True, env=env)
    if probe.returncode == 0:
        pytest.skip("the installed git does not honour GIT_TEST_ASSUME_DIFFERENT_OWNER")


def test_a_repository_another_user_owns_is_refused_with_the_command_to_trust_it(tmp_path, git_cmd, monkeypatch):
    repo = make_repo(git_cmd, tmp_path / "their repo")
    _assume_other_owner(monkeypatch, repo)
    cfg = Config(data_dir=str(tmp_path / "data"), use_cache=False)
    calls = [lambda: build_index_at(str(repo), "HEAD", cfg), lambda: build_index(str(repo), cfg, register=False),
             lambda: track.git_log(str(repo)), lambda: track.snapshot(str(repo), "HEAD", cfg),
             lambda: track.is_git(str(repo)), lambda: ingest.repo_meta(str(repo))]
    for call in calls:
        with pytest.raises(UntrustedRepositoryError) as e:
            call()
        message = str(e.value)
        assert "git config --global --add safe.directory" in message
        assert "their repo" in message and "never changes this setting" in message
    # nothing was changed on the user's behalf
    assert subprocess.run(["git", "config", "--global", "--get-all", "safe.directory"],
                          capture_output=True).returncode == 1


def test_a_folder_inside_another_users_repository_is_refused_not_trusted(tmp_path, git_cmd, monkeypatch):
    # The case the check exists for: a plain folder under a repository someone else planted.
    outer = make_repo(git_cmd, tmp_path / "shared")
    inner = outer / "project"
    write(inner, {"app.py": "def app():\n    return 2\n"})
    _assume_other_owner(monkeypatch, outer)
    with pytest.raises(UntrustedRepositoryError) as e:
        build_index(str(inner), Config(data_dir=str(tmp_path / "data"), use_cache=False), register=False)
    assert "shared" in str(e.value)


def test_the_engines_own_clones_keep_working(tmp_path, git_cmd, monkeypatch):
    data = tmp_path / "data"
    url = "https://example.invalid/owner/repo.git"
    repos = os.path.join(str(data), "repos")
    dest = ingest._dest_for(repos, ingest.validate_git_url(ingest.normalize_git_url(url)))
    make_repo(git_cmd, Path(dest))
    git_cmd(dest, "remote", "add", "origin", url)
    monkeypatch.setattr(ingest, "_refresh", lambda dest: None)      # no network in tests
    _assume_other_owner(monkeypatch, dest)
    idx = build_index_at(url, "HEAD", Config(data_dir=str(data), use_cache=False))
    assert "m.py::m" in idx.graph.nodes
    assert ingest.own_clone(dest) and ingest.own_clone(os.path.join(dest, "sub"))


def test_only_a_clone_folder_under_a_data_folder_the_engine_cloned_into_is_trusted(tmp_path):
    repos = tmp_path / "data" / "repos"
    ingest._register_clone_root(str(repos))
    clone = repos / "0123456789abcdef"
    assert ingest.own_clone(str(clone)) == os.path.join(os.path.realpath(str(repos)), "0123456789abcdef")
    assert ingest.own_clone(str(clone / "deep" / "er"))
    for other in (repos, repos / "not-a-clone", tmp_path / "data", tmp_path, tmp_path / "elsewhere" / "0123456789abcdef"):
        assert ingest.own_clone(str(other)) == "", other
    argv, env = ingest.git_command(["rev-parse", "HEAD"], str(tmp_path))
    assert "safe.directory=*" not in argv and "GIT_WORK_TREE" not in env
    # a clone folder without its own .git folder: git is pointed at it, never trusted
    argv, env = ingest.git_command(["rev-parse", "HEAD"], str(clone))
    real_clone = os.path.join(os.path.realpath(str(repos)), "0123456789abcdef")
    assert "safe.directory=*" not in argv
    assert env["GIT_DIR"] == os.path.join(real_clone, ".git") and env["GIT_WORK_TREE"] == real_clone
    (clone / ".git").mkdir(parents=True)
    argv, env = ingest.git_command(["rev-parse", "HEAD"], str(clone / "deep"))
    assert argv[argv.index("safe.directory=*") - 1] == "-c" and env["GIT_WORK_TREE"] == real_clone


def test_no_command_but_our_own_clone_and_fetch_may_use_the_network(tmp_path):
    for args in (["log", "-n1"], ["cat-file", "--batch"], ["ls-tree", "HEAD"], ["reset", "--hard"]):
        _, env = ingest.git_command(args, str(tmp_path))
        assert env["GIT_ALLOW_PROTOCOL"] == "none" and env["GIT_NO_LAZY_FETCH"] == "1", args
    for args in (["clone", "--", "https://example.invalid/x.git", "dest"], ["fetch", "origin"]):
        _, env = ingest.git_command(args, str(tmp_path))
        assert env["GIT_ALLOW_PROTOCOL"] == "http:https:ssh:git", args


def test_a_partial_clones_missing_object_never_starts_a_program(tmp_path, git_cmd):
    # A repository's own config can turn a read into a fetch: a partial clone fetches a
    # missing object on demand, through core.sshCommand on an ssh remote.
    repo = make_repo(git_cmd, tmp_path / "repo")
    write(repo, {"big.py": "def big():\n    return 2\n"})
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-q", "-m", "second")
    blob = git_cmd(repo, "rev-parse", "HEAD:big.py")
    obj = repo / ".git" / "objects" / blob[:2] / blob[2:]
    obj.chmod(stat.S_IWRITE | stat.S_IREAD)            # git leaves its objects read-only
    obj.unlink()
    marker = tmp_path / "ran.txt"
    program = tmp_path / "fake-ssh.sh"
    program.write_text("#!/bin/sh\necho ran > '%s'\nexit 1\n" % marker.as_posix(), encoding="utf-8", newline="\n")
    program.chmod(0o755)
    for key, value in (("core.repositoryformatversion", "1"), ("extensions.partialClone", "origin"),
                       ("remote.origin.url", "ssh://example.invalid/x.git"), ("remote.origin.promisor", "true"),
                       ("core.sshCommand", program.as_posix())):
        git_cmd(repo, "config", key, value)
    subprocess.run(["git", "cat-file", "-p", blob], cwd=repo, capture_output=True,
                   env=dict(os.environ, GIT_ALLOW_PROTOCOL="ssh"))      # control: plain git starts it
    if not marker.exists():
        pytest.skip("the installed git did not start core.sshCommand for the missing object")
    marker.unlink()
    with pytest.raises(RuntimeError):                  # the object is missing, so reading it fails
        build_index_at(str(repo), "HEAD", Config(data_dir=str(tmp_path / "data"), use_cache=False))
    assert not marker.exists(), "an engine git command started the repository's core.sshCommand"


@pytest.mark.parametrize("stderr", [
    "fatal: detected dubious ownership in repository at '/srv/their repo'\nTo add an exception for this "
    "directory, call:\n\n\tgit config --global --add safe.directory '/srv/their repo'\n",
    # git translates its explanation, never the command it suggests
    "fatal: unsichere Besitzverhältnisse im Repository unter '/srv/their repo' festgestellt\nUm eine Ausnahme "
    "für dieses Verzeichnis hinzuzufügen, rufen Sie auf:\n\n\tgit config --global --add safe.directory '/srv/their repo'\n",
])
def test_the_refusal_is_recognised_in_any_language(stderr):
    with pytest.raises(UntrustedRepositoryError) as e:
        ingest.check_ownership(128, stderr, "/srv")
    assert "/srv/their repo" in str(e.value)
    ingest.check_ownership(0, stderr, "/srv")          # git succeeded: nothing to raise


def test_the_suggested_command_quotes_the_path_so_pasting_it_runs_nothing(tmp_path, git_cmd, monkeypatch):
    repo = make_repo(git_cmd, tmp_path / "x$(touch PWNED) it's")
    _assume_other_owner(monkeypatch, repo)
    with pytest.raises(UntrustedRepositoryError) as e:
        track.git_log(str(repo))
    [command] = [line.strip() for line in str(e.value).splitlines() if "safe.directory" in line and "--add" in line]
    path = command.split("safe.directory ", 1)[1]
    assert path.startswith("'") and path.endswith("'") and '"' not in path
    assert "$(touch PWNED)" in path
    assert ("it''s" if os.name == "nt" else "it'\\''s") in path


def test_the_check_reports_a_refused_repository_as_an_error_not_a_regression(tmp_path, git_cmd, monkeypatch, capsys):
    from orch_engine import check
    repo = make_repo(git_cmd, tmp_path / "their repo")
    _assume_other_owner(monkeypatch, repo)
    assert check.main(["--repo", str(repo), "--base", "HEAD", "--data-dir", str(tmp_path / "data")]) == 2
    assert "safe.directory" in capsys.readouterr().err


def test_a_broken_clone_never_falls_through_to_a_repository_in_a_parent_folder(tmp_path, git_cmd):
    # The data folder can sit inside a repository (the default .orch_cache does). A clone
    # folder without .git must not make git walk up and use that outer repository.
    outer = make_repo(git_cmd, tmp_path / "outer")
    repos = outer / ".orch_cache" / "repos"
    broken = repos / "0123456789abcdef" / "src"
    broken.mkdir(parents=True)
    ingest._register_clone_root(str(repos))
    assert ingest.git_toplevel(str(broken)) == ""
    assert ingest.git_toplevel(str(outer / ".orch_cache")) != ""      # outside the clones: plain git


def test_an_outer_gits_variables_never_redirect_a_command(tmp_path, git_cmd, monkeypatch):
    # Run from inside a git hook, the engine inherits GIT_DIR, GIT_INDEX_FILE and the rest,
    # which would point every command at that other repository.
    repo = make_repo(git_cmd, tmp_path / "repo")
    other = make_repo(git_cmd, tmp_path / "other")
    write(other, {"other_only.py": "def o():\n    return 2\n"})
    git_cmd(other, "add", "-A")
    git_cmd(other, "commit", "-q", "-m", "second")
    for name, value in (("GIT_DIR", other / ".git"), ("GIT_WORK_TREE", other),
                        ("GIT_INDEX_FILE", other / ".git" / "index"), ("GIT_COMMON_DIR", other / ".git"),
                        ("GIT_OBJECT_DIRECTORY", other / ".git" / "objects")):
        monkeypatch.setenv(name, str(value))
    _, env = ingest.git_command(["rev-parse", "HEAD"], str(repo))
    assert not set(ingest.INHERITED_GIT_VARIABLES) & set(env)
    assert ingest.git_toplevel(str(repo)).replace("\\", "/").rstrip("/").endswith("/repo")
    cfg = Config(data_dir=str(tmp_path / "data"), use_cache=False)
    files = {pf.file for pf in build_index_at(str(repo), "HEAD", cfg).parsed_files}
    assert files == {"m.py"}


def test_every_git_command_carries_the_defensive_options(tmp_path):
    argv, _ = ingest.git_command(["log", "-n1"], str(tmp_path))
    opts = argv[1:argv.index("log")]
    assert opts == ["-c", "log.showSignature=false", "-c", "core.fsmonitor=false", "-c", "core.longpaths=true"]
    for banned in ("--textconv", "--filters"):
        assert banned not in argv


FAKE_SIGNED_COMMIT = (
    "tree {tree}\nparent {parent}\n"
    "author Engine Test <t@example.com> 1700000000 +0000\n"
    "committer Engine Test <t@example.com> 1700000000 +0000\n"
    "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEEtest\n -----END PGP SIGNATURE-----\n"
    "\nsigned\n")


def test_a_repositorys_own_config_cannot_start_a_program_through_git_log(tmp_path, git_cmd):
    repo = make_repo(git_cmd, tmp_path / "repo")
    marker = tmp_path / "ran.txt"
    program = tmp_path / "fake-gpg.sh"
    program.write_text("#!/bin/sh\necho ran > '%s'\nexit 1\n" % marker.as_posix(), encoding="utf-8", newline="\n")
    program.chmod(0o755)
    git_cmd(repo, "config", "log.showSignature", "true")
    git_cmd(repo, "config", "gpg.program", program.as_posix())
    body = FAKE_SIGNED_COMMIT.format(tree=git_cmd(repo, "rev-parse", "HEAD^{tree}"), parent=git_cmd(repo, "rev-parse", "HEAD"))
    sha = git_cmd(repo, "hash-object", "-t", "commit", "-w", "--stdin", stdin=body.encode())
    git_cmd(repo, "update-ref", "HEAD", sha)
    subprocess.run(["git", "log", "-n1"], cwd=repo, capture_output=True)    # control: plain git starts it
    if not marker.exists():
        pytest.skip("the installed git did not start gpg.program for the test signature")
    marker.unlink()
    cfg = Config(data_dir=str(tmp_path / "data"), use_cache=False)
    assert track.git_log(str(repo))[0]["sha"] == sha
    build_index_at(str(repo), "HEAD", cfg)
    track.snapshot(str(repo), "HEAD", cfg)
    build_index(str(repo), cfg, register=False)
    assert not marker.exists(), "an engine git command started the repository's gpg.program"
