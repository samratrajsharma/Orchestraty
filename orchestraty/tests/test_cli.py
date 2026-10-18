import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestraty import __version__
from orchestraty.cli import main

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_version_flag_prints_the_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"orch {__version__}"


def test_no_arguments_prints_help_and_succeeds(capsys):
    assert main([]) == 0
    assert "usage: orch" in capsys.readouterr().out


def test_unknown_argument_fails():
    with pytest.raises(SystemExit) as exit_info:
        main(["--no-such-flag"])
    assert exit_info.value.code == 2


def test_runs_as_a_module():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-m", "orchestraty", "--version"], cwd=PACKAGE_ROOT,
                            capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0
    assert result.stdout.strip() == f"orch {__version__}"
