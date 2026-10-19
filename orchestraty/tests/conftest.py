import shutil

import pytest

from orchestraty.capture import install


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own state folder and Claude Code config folder, so nothing can
    reach the real ones."""
    monkeypatch.setenv("ORCH_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
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
