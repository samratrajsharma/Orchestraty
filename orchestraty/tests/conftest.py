import os
import shutil
import subprocess

import pytest

from orchestraty.capture import install

GIT_SWITCHES = ["-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                "-c", "maintenance.auto=false", "-c", "gc.auto=0"]


@pytest.fixture(scope="session", autouse=True)
def the_repository_the_tests_live_in_is_never_touched():
    """Fail the run if any test changed this repository's git hooks setting."""
    from pathlib import Path
    top = Path(__file__).resolve().parents[2]

    def hooks():
        out = subprocess.run(["git", "--no-optional-locks", "config", "--get", "core.hooksPath"], cwd=top,
                             capture_output=True, text=True)
        return out.stdout.strip(), (top / ".git" / "orch-hooks").exists()

    before = hooks()
    yield
    assert hooks() == before, "a test changed the git hooks of the repository the tests live in"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own state folder, agent config folders, home folder and working
    folder, so nothing can reach the real ones: a command run "here" (orch init, orch watch)
    acts on an empty temporary folder, never on the repository the tests live in, and the
    agents' user files (~/.codex, ~/.gemini) are under the temporary home."""
    monkeypatch.setenv("ORCH_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("ORCH_NO_COORDINATOR", "1")       # hooks start no coordinator unless a test asks
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path


@pytest.fixture
def state_root(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def hook_script(state_root):
    """The hook script copied where orch init puts it: <state>/hooks/hook.py."""
    target = state_root / "hooks" / "hook.py"
    target.parent.mkdir(parents=True)
    shutil.copyfile(install.HOOK_SOURCE, target)
    return target


@pytest.fixture
def git(tmp_path_factory, monkeypatch):
    """git for building fixture repositories, with no user or system config (autocrlf,
    hooks, signing), which the code under test then sees too."""
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def run(cwd, *args):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com")
        proc = subprocess.run(["git"] + GIT_SWITCHES + list(args), cwd=cwd, capture_output=True, env=env,
                              timeout=120)
        if proc.returncode != 0:
            raise AssertionError(f"git {args} failed: {proc.stderr.decode('utf-8', 'replace')}")
        return proc.stdout.decode("utf-8").strip()
    return run


@pytest.fixture
def repo(tmp_path, git):
    """A repository with one commit on main: a.py defines charge(), b.py calls it."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    (path / "a.py").write_text("def charge(amount, currency):\n    return amount\n", encoding="utf-8")
    (path / "b.py").write_text("from a import charge\n\n\ndef pay():\n    return charge(1, 'EUR')\n",
                               encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    return path
