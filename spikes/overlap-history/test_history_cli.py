"""The command: it asks before reading anything, writes exactly its two files, and runs as one
zipapp file."""
import json
import os
import subprocess
import sys
import zipfile

import history_build
import history_cli


def test_nothing_is_read_without_a_yes(tmp_path, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(history_cli, "run", lambda *a, **k: called.append(1))
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    assert history_cli.main(["--out", str(tmp_path)]) == 1
    assert called == [] and os.listdir(str(tmp_path)) == []
    shown = capsys.readouterr().out
    assert "It never reads your prompts, the agent's replies or the contents of your files" in shown
    assert "Stopped. Nothing was read." in shown


def test_with_a_yes_it_writes_its_two_files_and_nothing_else(tmp_path, monkeypatch):
    out, projects = tmp_path / "out", tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert history_cli.main(["--projects", str(projects), "--out", str(out)]) == 0
    assert sorted(os.listdir(str(out))) == ["overlap-counts.json", "overlap-report.html"]
    with open(str(out / "overlap-counts.json"), encoding="utf-8") as fh:
        assert json.load(fh)["parallel_pairs"] == 0


def test_the_zipapp_holds_the_tool_engine_and_package_and_runs(tmp_path):
    target = history_build.build(str(tmp_path))
    with zipfile.ZipFile(target) as z:
        names = z.namelist()
    assert "__main__.py" in names and "history_cli.py" in names and "orch_engine/graph.py" in names
    assert "orchestraty/rules/overlap.py" in names
    assert not [n for n in names if "test_" in n or "__pycache__" in n or n.startswith("orchestraty/mod/")]
    projects, out = tmp_path / "projects", tmp_path / "out"
    projects.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([sys.executable, "-I", target, "--yes", "--projects", str(projects), "--out", str(out)],
                          capture_output=True, text=True, timeout=120, env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert sorted(os.listdir(str(out))) == ["overlap-counts.json", "overlap-report.html"]
