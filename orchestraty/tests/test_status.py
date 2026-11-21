"""orch status, orch watch and orch unwatch."""
import json
import os
import time
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import cli, config, status
from orchestraty.coordinator import main as cmain
from orchestraty.coordinator.lock import touch
from orchestraty.paths import State


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def record_sessions(state, repo, *events):
    start = time.time_ns()
    for i, (sid, event, cwd) in enumerate(events):
        spool_event(state.root, sid, event, cwd, ts=start + i * 1000)     # distinct, increasing times
    c = cmain.Coordinator(state)
    try:
        c.step()
    finally:
        c.close()


def test_nothing_watched_and_no_coordinator(state):
    data = status.collect(state)
    assert data == {"schema": 1, "coordinator": {"running": False}, "repositories": [], "unwatched": []}
    text = status.render(data)
    assert "not running" in text and "No repository is watched" in text


def test_repositories_not_watched_are_named_while_the_coordinator_runs(state, repo, tmp_path):
    other = tmp_path / "api"
    entry = {"common_dir": str(other / ".git"), "name": "api", "folder": str(other), "sessions": 2}
    Path(state.root).mkdir(parents=True, exist_ok=True)
    Path(state.unwatched).write_text(json.dumps({"schema": 1, "repositories": [entry]}), encoding="utf-8")
    assert status.collect(state)["unwatched"] == []          # a coordinator that stopped counts nothing
    Path(state.alive).touch()
    data = status.collect(state)
    assert data["unwatched"] == [entry]
    text = status.render(data)
    assert "Not watched, so these sessions are not compared:" in text
    assert f'api: 2 sessions. To watch it: orch watch "{other}"' in text
    config.watch(state, str(other / ".git"))                 # watched since: no longer named
    assert status.collect(state)["unwatched"] == []


def test_sessions_of_a_watched_repository(state, repo, git, tmp_path):
    config.watch(state, str(repo / ".git"))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(tmp_path / "wt-b"))
    record_sessions(state, repo, ("s-main", "SessionStart", repo), ("s-b", "SessionStart", tmp_path / "wt-b"),
                    ("s-b", "UserPromptSubmit", tmp_path / "wt-b"), ("s-old", "SessionStart", repo),
                    ("s-old", "SessionEnd", repo))
    data = status.collect(state)
    [r] = data["repositories"]
    assert r["name"] == "repo"
    assert [s["id"] for s in r["sessions"]] == ["s-main", "s-b"]          # live ones only
    b = r["sessions"][1]
    assert b["branch"] == "feat-b" and b["turn"] == "working" and b["base_guessed"] is False
    assert b["base_commit"] == git(repo, "rev-parse", "HEAD") and b["seconds_since_last_event"] < 60
    assert len(status.collect(state, include_ended=True)["repositories"][0]["sessions"]) == 3
    text = status.render(data)
    assert "repo  (" in text and "s-main" in text and "feat-b" in text and "working" in text
    assert "wt-b" in text


def test_a_running_coordinator_is_reported(state):
    os.makedirs(state.root)
    touch(state.alive)
    with open(state.info, "w", encoding="utf-8") as fh:
        json.dump({"pid": 4242, "started": time.time() - 30, "version": "x"}, fh)
    c = status.collect(state)["coordinator"]
    assert c["running"] and c["pid"] == 4242 and c["since"].endswith("Z")
    assert "running (pid 4242" in status.render(status.collect(state))
    os.utime(state.alive, (time.time() - 60, time.time() - 60))
    assert status.collect(state)["coordinator"] == {"running": False}


def test_status_reads_without_creating_a_record(state, repo):
    config.watch(state, str(repo / ".git"))
    data = status.collect(state)
    assert data["repositories"][0]["sessions"] == []
    assert not os.path.exists(state.record)
    assert "no live sessions" in status.render(data)


def test_orch_status_json(state, repo, capsys):
    config.watch(state, str(repo / ".git"))
    record_sessions(state, repo, ("s1", "SessionStart", repo))
    assert cli.main(["status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == 1 and data["repositories"][0]["sessions"][0]["id"] == "s1"
    assert cli.main(["status"]) == 0
    assert "s1" in capsys.readouterr().out


def test_orch_watch_and_unwatch(state, repo, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["watch"]) == 0
    assert "Watching repo" in capsys.readouterr().out
    assert config.is_watched(config.load(state), str(repo / ".git"))
    assert cli.main(["watch", str(repo)]) == 0
    assert "Already watching" in capsys.readouterr().out
    assert cli.main(["unwatch"]) == 0
    assert config.load(state)["repos"] == []
    plain = tmp_path / "plain"
    plain.mkdir()
    assert cli.main(["watch", str(plain)]) == 0                   # a folder without git is followed too
    assert "a folder without git" in capsys.readouterr().out
    assert config.is_watched(config.load(state), str(plain))
    os.makedirs(os.path.expanduser("~"), exist_ok=True)
    assert cli.main(["watch", os.path.expanduser("~")]) == 2       # never the home folder
    assert "the home folder" in capsys.readouterr().err


def test_orch_init_watches_the_repository_it_runs_in(state, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0
    assert "Watching repo" in capsys.readouterr().out
    assert config.is_watched(config.load(state), str(repo / ".git"))
    assert os.path.isfile(state.launch)


def test_a_damaged_config_means_the_defaults(state):
    os.makedirs(state.root)
    with open(state.config, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert config.load(state) == config.DEFAULTS
    with open(state.config, "w", encoding="utf-8") as fh:
        json.dump({"repos": ["relative/path", 5, os.path.abspath("x")], "idle_seconds": True,
                   "session_timeout": "soon"}, fh)
    cfg = config.load(state)
    assert cfg["repos"] == [os.path.abspath("x")]
    assert cfg["idle_seconds"] == config.DEFAULTS["idle_seconds"]
    assert cfg["session_timeout"] == config.DEFAULTS["session_timeout"]


JAPANESE = "日本"


def test_orch_status_findings_and_events_write_any_name_to_a_pipe(state, repo, git, tmp_path, monkeypatch):
    from helpers import run_piped
    config.watch(state, str(repo / ".git"))
    wt = tmp_path / f"wt ünï {JAPANESE}"
    git(repo, "worktree", "add", "-q", "-b", "feat-jp", str(wt))
    record_sessions(state, repo, ("s-jp", "SessionStart", wt), ("s-jp", "UserPromptSubmit", wt))
    for argv in (["status"], ["status", "--all"], ["findings", "--all"], ["events"], ["claims"], ["merge-order"]):
        code, out, err = run_piped(monkeypatch, argv)
        assert code == 0 and b"Traceback" not in err, argv
        out.decode("utf-8")                                          # UTF-8 through a pipe or a file
    assert JAPANESE in run_piped(monkeypatch, ["status"])[1].decode("utf-8")


def test_json_is_ascii_whatever_the_names(state, repo, git, tmp_path, monkeypatch):
    from helpers import run_piped
    config.watch(state, str(repo / ".git"))
    wt = tmp_path / f"wt {JAPANESE}"
    git(repo, "worktree", "add", "-q", "-b", "feat-jp", str(wt))
    record_sessions(state, repo, ("s-jp", "SessionStart", wt))
    for argv in (["status", "--json"], ["findings", "--json"], ["stats", "--json"], ["events", "--json"]):
        code, out, _ = run_piped(monkeypatch, argv)
        assert code == 0 and out.isascii(), argv                     # the same bytes in every encoding
        [json.loads(line) for line in out.splitlines()] if argv[0] == "events" else json.loads(out)
    data = json.loads(run_piped(monkeypatch, ["status", "--json"])[1])
    assert data["repositories"][0]["sessions"][0]["worktree"] == str(wt)


def test_a_together_result_with_a_replacement_character_goes_through_a_pipe(state, repo, tmp_path, monkeypatch):
    import sys
    from helpers import run_piped
    from orchestraty import together, usercommand
    config.watch(state, str(repo / ".git"))
    record_sessions(state, repo, ("A", "SessionStart", repo), ("B", "SessionStart", repo))
    script = tmp_path / "check.py"                   # a test run whose output is in the ANSI code page
    script.write_text('import sys\nsys.stdout.buffer.write(b"test_caf\\xe9 FAILED: caf\\xe9 != cafe\\n")\nsys.exit(1)\n',
                      encoding="utf-8")
    ran = usercommand.run(f'"{sys.executable}" "{script}"', str(tmp_path), 60)
    line = together.first_failing_line(ran["last_lines"])
    assert "�" in line
    together.save_settings(state, str(repo / ".git"), command="python -m pytest -q", on=True)
    together.save_result(state, {"repo": str(repo / ".git"), "sessions": ["A", "B"], "result": "fails together",
                                 "alone": {"A": {"exit_code": 0}, "B": {"exit_code": 0}}, "first_failing_line": line})
    code, out, err = run_piped(monkeypatch, ["status"])
    assert code == 0 and b"Traceback" not in err
    assert "together it fails: test_caf� FAILED" in out.decode("utf-8")
