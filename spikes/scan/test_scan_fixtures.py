"""Tests for the test fixtures themselves (conftest.py)."""
import ast
import subprocess
from pathlib import Path

import conftest

HERE = Path(__file__).resolve().parent
# Git commands that can start automatic maintenance (git's run_auto_maintenance callers).
MAINTAINING = {"commit", "merge", "rebase", "am", "cherry-pick", "fetch", "pull", "revert", "stash"}


def _config_switches(cmd):
    return {cmd[i + 1] for i, arg in enumerate(cmd[:-1]) if arg == "-c"}


def test_fixture_git_runs_no_background_maintenance(monkeypatch, tmp_path):
    # A detached maintenance process would change .git while a test compares snapshots.
    seen = []

    def fake_run(cmd, *args, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(conftest.subprocess, "run", fake_run)
    for args in (["commit", "-q", "-m", "x"], ["merge", "-q", "main"], ["rebase", "-q", "main"],
                 ["cherry-pick", "HEAD"], ["stash", "push", "-q"], ["init", "-q"]):
        conftest.git(tmp_path, *args)
    assert len(seen) == 6
    for cmd in seen:
        switches = _config_switches(cmd)
        assert "maintenance.auto=false" in switches and "gc.auto=0" in switches, cmd
        # The switches come before the subcommand, where git reads them.
        assert cmd.index("gc.auto=0") < len(conftest.FIXTURE_GIT_SWITCHES) + 1


def test_the_switches_really_turn_maintenance_off(tmp_path):
    # Real git, not a stub: the helper's configuration reports both settings as off.
    conftest.git(tmp_path, "init", "-q")
    out = conftest.git(tmp_path, "config", "--get", "maintenance.auto").decode().strip()
    assert out == "false"
    assert conftest.git(tmp_path, "config", "--get", "gc.auto").decode().strip() == "0"


def test_no_test_runs_a_maintaining_git_command_outside_the_helper():
    # Only conftest.git() may start git subcommands that trigger automatic maintenance.
    offenders = []
    for path in sorted(HERE.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) \
                    and node.elts[0].value == "git":
                words = [e.value if isinstance(e, ast.Constant) else None for e in node.elts[1:]]
                i = 0
                while i < len(words) and isinstance(words[i], str) and words[i].startswith("-"):
                    i += 2 if words[i] in ("-c", "-C") else 1      # skip global options
                if i < len(words) and words[i] in MAINTAINING:     # the subcommand itself
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
