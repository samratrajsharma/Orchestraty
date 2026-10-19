"""Tests for the consent screen, questions, preview, output file and single-file build."""
import ast
import builtins
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import scan_bundle
import scan_cli
import scan_collect as sc
from conftest import SECRET, snapshot_tree

HERE = Path(__file__).resolve().parent


class Inputs:
    """Feeds scripted answers to the prompts and records every prompt shown."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if answer is KeyboardInterrupt:
            raise KeyboardInterrupt           # the person pressed Ctrl+C at this prompt
        return answer


def _run(machine, argv, answers=(), is_tty=True):
    lines = []
    inputs = Inputs(*answers)
    code = scan_cli.run(["--home", str(machine.home), "--system", machine.system, "--root", str(machine.root)] + argv,
                        input_fn=inputs, out=lines.append, is_tty=is_tty)
    return code, "\n".join(lines), inputs


def test_refusing_consent_scans_and_writes_nothing(linux_machine, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(sc, "collect", lambda *a, **k: called.append(1))
    target = tmp_path / "result.json"
    code, text, _ = _run(linux_machine, ["--out", str(target)], answers=["n"])
    assert code == 0 and not called and not target.exists()
    assert "Nothing was scanned" in text


def test_no_terminal_and_no_yes_means_no_scan(linux_machine, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "collect", lambda *a, **k: pytest.fail("collect must not run"))
    code, text, _ = _run(linux_machine, ["--out", str(tmp_path / "r.json")], is_tty=False)
    assert code == 2 and not (tmp_path / "r.json").exists()
    assert "PowerShell" in text


def test_full_interactive_run_saves_exactly_what_it_showed_and_nothing_else(linux_machine, tmp_path, make_repo,
                                                                            monkeypatch):
    m = linux_machine
    m.claude_sessions()
    m.claude_json({"h": {"command": "git"}})
    repo = make_repo(m.home / "somewhere" / "repo-given")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = out_dir / "result.json"
    answers = ["y",                          # consent
               str(repo),                    # repositories
               "y",                          # count helpers in .claude.json
               "2026-09-01", "20",           # helper incident
               "yesterday", "last week",     # change incident: two bad dates -> none
               "",                           # ...and no minutes
               "2026-09-20", "90",           # parallel incident
               "", "abc", "45"]              # collision incident: no date; bad minutes, then 45
    answers.append("")                       # save? default yes
    before = snapshot_tree(m.base, out_dir)
    writes = []
    real_open, real_os_open = builtins.open, os.open

    def spy(path, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            writes.append(os.path.abspath(str(path)))
        return real_open(path, mode, *args, **kwargs)

    def spy_os_open(path, flags, *a, **k):
        # subprocess opens the null device for git's stdin; that is not a file.
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) and os.fsdecode(path) not in (os.devnull, "\\\\.\\nul"):
            writes.append(os.path.abspath(os.fsdecode(path)))
        return real_os_open(path, flags, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(os, "open", spy_os_open)
    code, text, _ = _run(m, ["--out", str(target)], answers=answers)
    assert code == 0
    assert "looking at repository 1 of 1" in text
    assert writes == [os.path.abspath(str(target))], "the result file must be the only file written"
    after = snapshot_tree(m.base, out_dir)
    assert set(after) - set(before) == {str(target)} and all(after[k] == v for k, v in before.items())
    saved = target.read_text(encoding="utf-8")
    assert saved.strip() in text, "the saved file must be exactly the preview that was shown"
    result = json.loads(saved)
    assert result["schema"] == sc.SCHEMA
    assert result["mcp"]["claude_json_read"] is True
    assert result["repos"]["found_by"]["given"] == 1
    assert result["self_report"] == {
        "helper_incident": {"date": "2026-09-01", "minutes": 20},
        "change_incident": {"date": None, "minutes": None},
        "parallel_incident": {"date": "2026-09-20", "minutes": 90},
        "collision_incident": {"date": None, "minutes": 45},
        "window_days": 30,
    }
    assert SECRET not in saved


@pytest.mark.parametrize("argv", [["--system", "Linux"], ["--root", "somewhere"], ["--home", ""],
                                  ["--home", "", "--system", "Windows", "--root", "x"]])
def test_synthetic_flags_never_fall_back_to_the_real_machine(argv, monkeypatch):
    monkeypatch.setattr(sc, "collect", lambda *a, **k: pytest.fail("the real machine must not be scanned"))
    with pytest.raises(SystemExit) as exc:
        scan_cli.run(argv + ["--yes"], input_fn=Inputs(), out=lambda *_: None, is_tty=True)
    assert exc.value.code == 2


def test_a_terminal_is_recognised_including_git_bash(monkeypatch):
    class Stdin:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty

    monkeypatch.delenv("MSYSTEM", raising=False)
    monkeypatch.setattr(sys, "stdin", Stdin(True))
    assert scan_cli._terminal_available() is True
    monkeypatch.setattr(sys, "stdin", Stdin(False))                 # piped input: nobody to ask
    assert scan_cli._terminal_available() is False
    monkeypatch.setenv("MSYSTEM", "MINGW64")                         # Git Bash's mintty gives Python a pipe
    assert scan_cli._terminal_available() is (os.name == "nt")


def _main(machine, argv, answers=()):
    lines = []
    code = scan_cli.main(["--home", str(machine.home), "--system", machine.system, "--root", str(machine.root)]
                         + argv, input_fn=Inputs(*answers), out=lines.append, is_tty=True)
    return code, "\n".join(lines)


@pytest.mark.parametrize("where, answers, extra", [
    ("consent prompt", [KeyboardInterrupt], []),
    ("questions", ["y", "", KeyboardInterrupt], []),
    ("save prompt", ["y", "", KeyboardInterrupt], ["--no-questions"]),
])
def test_ctrl_c_at_a_prompt_stops_cleanly(linux_machine, tmp_path, where, answers, extra):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    code, text = _main(linux_machine, ["--out", str(out_dir)] + extra, answers)
    assert code == 130, where
    assert text.strip().endswith("Stopped. Nothing was saved.") and "Traceback" not in text
    assert list(out_dir.iterdir()) == []


def test_ctrl_c_during_the_scan_stops_cleanly(linux_machine, tmp_path, monkeypatch):
    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(sc, "collect", interrupted)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    code, text = _main(linux_machine, ["--out", str(out_dir), "--yes"])
    assert code == 130 and text.strip().endswith("Stopped. Nothing was saved.")
    assert list(out_dir.iterdir()) == []


def test_ctrl_c_while_saving_says_what_happened(linux_machine, tmp_path, monkeypatch):
    def half_then_interrupted(fh, text):
        fh.write(text[:20])
        fh.flush()
        raise KeyboardInterrupt
    monkeypatch.setattr(scan_cli, "_write_all", half_then_interrupted)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    code, text = _main(linux_machine, ["--out", str(out_dir), "--yes"])
    assert code == 130
    assert "Stopped while saving. The partly written file was removed" in text
    assert list(out_dir.iterdir()) == []


def test_ctrl_c_after_saving_does_not_claim_nothing_was_saved(linux_machine, tmp_path, monkeypatch):
    lines = []
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def out(line):
        lines.append(line)
        if line.startswith("\nSaved to"):
            raise KeyboardInterrupt
    code = scan_cli.main(["--home", str(linux_machine.home), "--system", "Linux", "--root", str(linux_machine.root),
                          "--out", str(out_dir), "--yes"], input_fn=Inputs(), out=out, is_tty=True)
    saved = list(out_dir.iterdir())
    assert code == 130 and len(saved) == 1
    assert lines[-1] == f"\nStopped. The result had already been saved to {saved[0]}."


def test_progress_shows_while_the_scan_runs_even_through_a_pipe(linux_machine, tmp_path):
    # Git Bash gives Python a pipe, not a console. Each progress line must arrive at once,
    # not when the scan ends; here the scan waits until the line has been seen.
    import queue
    import threading
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import scan_cli, scan_collect as sc\n"
            "def slow(env, progress=None, **kwargs):\n"
            "    progress(1, 2)\n"
            "    sys.stdin.readline()\n"
            "    return {'run': {}}\n"
            "sc.collect = slow\n"
            "raise SystemExit(scan_cli.main(sys.argv[1:]))\n" % str(HERE))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.Popen([sys.executable, "-c", code, "--yes", "--home", str(linux_machine.home),
                             "--system", "Linux", "--root", str(linux_machine.root), "--out", str(tmp_path)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout], daemon=True).start()
    seen = False
    try:
        while not seen:
            seen = b"looking at repository 1 of 2" in lines.get(timeout=30)
    except queue.Empty:
        pass
    finally:
        proc.stdin.write(b"\n")
        proc.stdin.close()
        proc.wait(timeout=60)
    assert seen, "the progress line only arrived after the scan ended"
    assert proc.returncode == 0


def test_minutes_are_stored_exactly_as_entered():
    # The scanner applies no cut-off of its own: every whole number is kept as given.
    for value in (0, 1, 7, 42, 99, 600, 12345):
        assert scan_cli._ask_minutes(Inputs(str(value)), lambda *_: None) == value
    for bad in (["²", "²"], ["1.5", "x"], ["-3", "ten"]):
        assert scan_cli._ask_minutes(Inputs(*bad), lambda *_: None) is None


def test_declining_to_save_writes_nothing(linux_machine, tmp_path):
    target = tmp_path / "result.json"
    code, text, _ = _run(linux_machine, ["--out", str(target), "--no-questions", "--claude-json", "no"],
                         answers=["y", "", "n"])
    assert code == 0 and not target.exists()
    assert "Not saved" in text


def test_claude_json_is_left_alone_unless_agreed(linux_machine, tmp_path):
    linux_machine.claude_sessions()                       # makes the Claude home exist, so the question is asked
    linux_machine.claude_json({"h": {"command": "git"}})
    target = tmp_path / "r.json"
    code, _, inputs = _run(linux_machine, ["--out", str(target), "--no-questions"], answers=["y", "", "", "y"])
    assert code == 0
    assert any("Count them" in p for p in inputs.prompts)
    assert json.loads(target.read_text(encoding="utf-8"))["mcp"]["claude_json_read"] is False


def test_synthetic_home_ignores_the_real_machines_settings(linux_machine, tmp_path, monkeypatch):
    decoy = tmp_path / "real-looking-claude"
    for i in range(5):
        p = decoy / "projects" / "p" / f"s{i}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(SECRET, encoding="utf-8")
    (decoy / ".claude.json").write_text(json.dumps({"mcpServers": {"x": {"command": "npx"}}}), encoding="utf-8")
    for var, value in (("CLAUDE_CONFIG_DIR", str(decoy)), ("CODEX_HOME", str(decoy)),
                       ("APPDATA", str(decoy)), ("XDG_CONFIG_HOME", str(decoy))):
        monkeypatch.setenv(var, value)
    target = tmp_path / "r.json"
    code, _, _ = _run(linux_machine, ["--out", str(target), "--yes", "--claude-json", "yes"])
    result = json.loads(target.read_text(encoding="utf-8"))
    assert code == 0
    assert result["agents"]["claude-code"]["sessions"]["sessions"] == 0
    assert result["mcp"]["total_servers"] == 0


def test_existing_file_is_never_overwritten_and_a_folder_gets_a_file(linux_machine, tmp_path):
    target = tmp_path / "r.json"
    target.write_text("keep me", encoding="utf-8")
    code, _, _ = _run(linux_machine, ["--out", str(target), "--yes"])
    assert code == 0 and target.read_text(encoding="utf-8") == "keep me"
    assert (tmp_path / "r-2.json").is_file()
    folder = tmp_path / "results"
    folder.mkdir()
    code, _, _ = _run(linux_machine, ["--out", str(folder), "--yes"])
    assert code == 0
    assert [p.name for p in folder.iterdir()][0].startswith("orch-scan-")


def test_console_text_is_plain_ascii():
    texts = [scan_cli.CONSENT_TEXT, scan_cli.CLAUDE_JSON_TEXT] + [q for _, q in scan_cli.QUESTIONS]
    for t in texts:
        t.encode("ascii")          # a cp1252 Windows console must be able to print it


def test_a_console_that_cannot_show_a_path_does_not_crash_after_saving(linux_machine, tmp_path):
    target = tmp_path / "Женя" / "r.json"        # a Cyrillic folder name
    target.parent.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update({"PYTHONIOENCODING": "cp1252", "PYTHONDONTWRITEBYTECODE": "1"})
    code = ("import sys; sys.path.insert(0, %r); import scan_cli; "
            "raise SystemExit(scan_cli.main(sys.argv[1:]))" % str(HERE))
    proc = subprocess.run([sys.executable, "-c", code, "--yes", "--home", str(linux_machine.home),
                           "--system", "Linux", "--root", str(linux_machine.root), "--out", str(target)],
                          env=env, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-400:]
    assert target.is_file()


def test_the_questions_carry_no_pass_marks():
    # Only a date and a number of minutes are asked for and stored: no comparison with a cut-off.
    tree = ast.parse((HERE / "scan_cli.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_ask_minutes")
    compares = [n for n in ast.walk(fn) if isinstance(n, ast.Compare)
                and any(isinstance(c, ast.Constant) and isinstance(c.value, (int, float)) for c in n.comparators)]
    assert compares == []


@pytest.mark.parametrize("name", ["scan_cli.py", "scan_bundle.py"])
def test_source_parses_as_python_3_9(name):
    ast.parse((HERE / name).read_text(encoding="utf-8"), feature_version=(3, 9))


@pytest.mark.parametrize("name", ["scan_cli.py", "scan_bundle.py"])
def test_standard_library_only_and_no_network_modules(name):
    allowed = {"__future__", "argparse", "datetime", "hashlib", "json", "os", "pathlib", "re", "sys"}
    tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods.discard("scan_collect")
    assert mods <= allowed, f"{name} imports {sorted(mods - allowed)}"


def test_single_file_build_runs_on_its_own(linux_machine, tmp_path):
    linux_machine.claude_sessions()
    built = tmp_path / "dist" / "orch-scan.py"
    digest = scan_bundle.build(built)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    src = built.read_text(encoding="utf-8")
    assert "import scan_collect" not in src
    ast.parse(src, feature_version=(3, 9))
    # Run it as a participant would, from an empty folder, with no access to the spike sources.
    work = tmp_path / "run"
    work.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, str(built), "--yes", "--home", str(linux_machine.home),
                           "--system", "Linux", "--root", str(linux_machine.root)],
                          cwd=work, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    written = list(work.iterdir())
    assert len(written) == 1 and written[0].name.startswith("orch-scan-")
    result = json.loads(written[0].read_text(encoding="utf-8"))
    assert result["agents"]["claude-code"]["sessions"]["sessions"] == 2   # the fixture's 20-day-old file is outside
