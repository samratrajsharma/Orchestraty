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


def test_a_record_that_cannot_be_read_is_one_line_not_a_traceback(tmp_path, capsys):
    state = tmp_path / "state"
    state.mkdir()
    (state / "record.sqlite").write_bytes(b"GARBAGE!" * 512)
    for argv in (["status"], ["status", "--json"], ["findings", "--all"], ["events"]):
        assert main(argv) == 1, argv
        err = capsys.readouterr().err
        assert err.startswith("orch: the record ") and "cannot be read (file is not a database)" in err, err
        assert err.endswith("; orch doctor says what to do\n") and err.count("\n") == 1


def test_a_pipe_gets_utf_8_and_a_terminal_keeps_its_encoding():
    from helpers import piped
    from orchestraty.cli import safe_output
    text = "wt " + chr(0x65E5) + chr(0x672C) + " " + chr(0xFFFD) + chr(10)
    pipe, terminal, terminal_err = piped(), piped(tty=True), piped(tty=True)
    safe_output(((pipe, "replace"), (terminal, "replace"), (terminal_err, "backslashreplace")))
    for stream in (pipe, terminal, terminal_err):
        stream.write(text)
        stream.flush()
    assert pipe.buffer.getvalue() == text.encode("utf-8")
    assert terminal.encoding == "cp1252" and terminal.buffer.getvalue() == text.encode("cp1252", "replace")
    assert terminal_err.buffer.getvalue() == text.encode("cp1252", "backslashreplace")


def test_no_console_and_a_closed_stream_are_left_alone():
    from helpers import piped
    from orchestraty.cli import safe_output
    closed = piped()
    closed.close()
    safe_output(((None, "replace"), (closed, "replace")))     # pythonw has no stdout; neither raises
