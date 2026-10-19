"""Shared pytest fixtures for the engine test suite.

The fixtures build a real RepoIndex over the bundled ``sample_repo`` with caching off,
so the suite is fast, fully offline and deterministic (no network, no model).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Make the project root importable regardless of how pytest is invoked.
ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_repo"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orch_engine.config import Config          # noqa: E402
from orch_engine.pipeline import build_index   # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def sample_repo_path() -> str:
    assert SAMPLE.is_dir(), f"sample_repo missing at {SAMPLE}"
    return str(SAMPLE)


@pytest.fixture(scope="session")
def engine_config(tmp_path_factory) -> Config:
    """A no-cache config writing into a throwaway data dir."""
    data_dir = tmp_path_factory.mktemp("engine_data")
    return Config(data_dir=str(data_dir), use_cache=False)


@pytest.fixture(scope="session")
def idx(sample_repo_path, engine_config):
    """A built RepoIndex over sample_repo."""
    return build_index(sample_repo_path, config=engine_config, register=False)


@pytest.fixture
def build_repo(tmp_path_factory):
    """Factory: write a dict of {relpath: source} to a temp dir and build a
    RepoIndex over it. Useful for exercising features that sample_repo
    does not cover (HTTP routes, ORM models, import cycles, near-duplicates)."""
    def _make(files, name="synthetic"):
        d = tmp_path_factory.mktemp(name)
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        cfg = Config(data_dir=str(d / "_data"), use_cache=False)
        return build_index(str(d), config=cfg, register=False)
    return _make


# --------------------------------------------------------------------------- #
# Synthetic git repositories, built with no user or system git config.
# --------------------------------------------------------------------------- #
FIXTURE_GIT_SWITCHES = ["-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false",
                        "-c", "core.autocrlf=false", "-c", "maintenance.auto=false", "-c", "gc.auto=0"]


@pytest.fixture
def git_cmd(tmp_path_factory, monkeypatch):
    """git for building fixture repositories. No user or system config reaches them
    (autocrlf, hooks, signing), and the engine's own git calls in the test see the same."""
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # The engine reads the system config on purpose; point it at an empty file, so a machine
    # that already trusts every folder (CI runners set safe.directory=*) changes nothing here.
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))

    def git(repo, *args, env=None, stdin=None):
        e = dict(os.environ, GIT_TERMINAL_PROMPT="0",
                 GIT_AUTHOR_NAME="Engine Test", GIT_AUTHOR_EMAIL="t@example.com",
                 GIT_COMMITTER_NAME="Engine Test", GIT_COMMITTER_EMAIL="t@example.com")
        e.update(env or {})
        r = subprocess.run(["git"] + FIXTURE_GIT_SWITCHES + list(args), cwd=repo, capture_output=True,
                           env=e, input=stdin, timeout=120)
        if r.returncode != 0:
            raise AssertionError(f"git {args} failed: {r.stderr.decode('utf-8', 'replace')}")
        return r.stdout.decode("utf-8").strip()
    return git


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


@pytest.fixture(scope="session")
def git_repo(tmp_path_factory):
    """A tiny 2-commit git repo. Returns {'path', 'base', 'head'}.

    Skips if git is not on PATH.
    """
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")

    repo = tmp_path_factory.mktemp("git_repo")
    _git(["init", "-q"], repo)
    # Local identity so commits succeed in CI / clean environments.
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Engine Test"], repo)
    _git(["config", "commit.gpgsign", "false"], repo)

    # --- base commit ---
    (repo / "a.py").write_text(
        "def alpha(x):\n"
        "    if x > 0:\n"
        "        return x\n"
        "    return -x\n",
        encoding="utf-8",
    )
    (repo / "b.py").write_text(
        "from a import alpha\n\n\ndef beta():\n    return alpha(1)\n",
        encoding="utf-8",
    )
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    # --- head commit: add a file, grow a.alpha's complexity, add a symbol ---
    (repo / "a.py").write_text(
        "def alpha(x):\n"
        "    if x > 0:\n"
        "        return x\n"
        "    elif x == 0:\n"
        "        return 1\n"
        "    return -x\n\n\n"
        "def gamma():\n    return 42\n",
        encoding="utf-8",
    )
    (repo / "c.py").write_text("def delta():\n    return 0\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "head"], repo)
    head = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    return {"path": str(repo), "base": base, "head": head}
