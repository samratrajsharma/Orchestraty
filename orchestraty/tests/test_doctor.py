"""orch doctor, in an isolated state folder: a fix for each problem, and every check passing once
orch init has run in a repository."""
import json
import os
import sys
import time

from orchestraty import cli, doctor
from orchestraty.capture import hook
from orchestraty.coordinator.lock import Lock
from orchestraty.paths import State


def run_doctor(cwd, index=True):
    lines = []

    class Out:
        def write(self, text):
            lines.append(text)

        def flush(self):
            pass
    code = doctor.run(State(), str(cwd), out=Out(), index=index)
    return code, "".join(lines)


def test_before_orch_init_each_problem_says_to_run_it(state_root, tmp_path):
    code, out = run_doctor(tmp_path, index=False)
    assert code == 1
    assert f"fix   state folder: {state_root} does not exist yet\n      -> run orch init" in out
    assert "fix   hook script: none at" in out
    for agent, how in (("Claude Code", "orch init"), ("Codex", "orch init --agent codex"),
                       ("Antigravity", "orch init --agent antigravity")):
        assert f"note  {agent}: no Orchestraty hooks in" in out and f"({how} installs them)" in out
    assert "fix   no agent has Orchestraty's hooks, so no session is seen" in out
    assert "fix   coordinator: no " in out and "launch.txt, so no hook can start it" in out
    assert out.endswith("4 problems to fix.\n")
    assert not os.path.exists(state_root)                     # doctor creates nothing


def test_after_orch_init_every_check_passes(state_root, tmp_path, git, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.py").write_text("def charge(amount):\n    return amount\n", encoding="utf-8")
    (repo / "b.py").write_text("from a import charge\n\nTOTAL = charge(1)\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes", "--no-git-hooks"]) == 0
    code, out = run_doctor(repo)
    assert code == 0, out
    assert "ok    Python %d.%d" % sys.version_info[:2] in out
    assert f"ok    state folder: {state_root}" in out and "ok    hook script:" in out
    assert "ok    Claude Code: hooks in" in out and "note  Codex: no Orchestraty hooks" in out
    assert "ok    coordinator: starts (" in out and "and was stopped again" in out
    assert "ok    git 2." in out and "ok    engine: imports (schema" in out
    assert "ok    first index of " in out and ": 2 files in " in out
    assert out.endswith("No problems found.\n")
    lock = Lock(State().lock)
    assert lock.acquire()                                      # the coordinator it started has stopped
    lock.release()
    # another tool's entry after ours changes nothing; ours run by another interpreter does
    settings_file = tmp_path / "claude-config" / "settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    settings["hooks"]["PreToolUse"].append({"hooks": [{"type": "command", "command": "other-tool"}]})
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    assert run_doctor(repo, index=False)[0] == 0
    for entries in settings["hooks"].values():
        for entry in entries:
            for h in entry["hooks"]:
                if h["command"] != "other-tool":
                    h["command"] = os.path.join(str(tmp_path), "gone", "python")
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    code, out = run_doctor(repo, index=False)
    assert code == 1 and "fix   Claude Code: the hooks in" in out and "-> run orch init again" in out
    assert cli.main(["doctor", "--no-index"]) == 1


def test_a_coordinator_that_does_not_start(state_root, tmp_path):
    """launch.txt's import path leads to an orchestraty that exits at once (ahead of any installed one)."""
    broken = tmp_path / "broken" / "orchestraty"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("", encoding="utf-8")
    (broken / "__main__.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    hooks_folder = state_root / "hooks"
    hooks_folder.mkdir(parents=True)
    (hooks_folder / "launch.txt").write_text(f"{sys.executable}\n{broken.parent}\n", encoding="utf-8")
    check = doctor.coordinator_check(State(), seconds=30)
    assert check.kind == "fix" and check.text == "coordinator: exited with 3"
    assert "coordinator.log" in check.fix
    assert not hook.coordinator_alive(str(state_root), time.time())


def test_a_coordinator_that_meets_a_newer_record_says_so(state_root, tmp_path):
    from orchestraty.coordinator import main as cmain
    assert doctor.NEWER_RECORD == cmain.NEWER_RECORD
    broken = tmp_path / "newer" / "orchestraty"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("", encoding="utf-8")
    (broken / "__main__.py").write_text(f"raise SystemExit({cmain.NEWER_RECORD})\n", encoding="utf-8")
    hooks_folder = state_root / "hooks"
    hooks_folder.mkdir(parents=True)
    (hooks_folder / "launch.txt").write_text(f"{sys.executable}\n{broken.parent}\n", encoding="utf-8")
    check = doctor.coordinator_check(State(), seconds=30)
    assert check.kind == "fix" and "written by a newer Orchestraty" in check.text
    assert "upgrade Orchestraty" in check.fix and "aside" in check.fix and "orch init" not in check.fix


def test_a_running_coordinator_of_another_version_is_a_problem(state_root):
    state = State()
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "coordinator.alive").write_text("", encoding="utf-8")             # its heartbeat is fresh
    for version, kind in (("0.0.1", "fix"), (doctor.__version__, "ok")):
        with open(state.info, "w", encoding="utf-8") as fh:
            json.dump({"pid": 1, "started": time.time(), "version": version}, fh)
        check = doctor.coordinator_check(state)
        assert check.kind == kind, check
    with open(state.info, "w", encoding="utf-8") as fh:
        json.dump({"pid": 1, "started": time.time(), "version": "0.0.1"}, fh)
    check = doctor.coordinator_check(state)
    assert check.text == (f"coordinator: running version 0.0.1, not this one ({doctor.__version__}): it started "
                          "before the update")
    assert check.fix == "run orch coordinator stop; the next hook starts this version"


def test_a_state_folder_in_a_synced_folder(tmp_path):
    windows_onedrive = {"OneDrive": os.path.join(str(tmp_path), "OneDrive - Contoso")}
    assert doctor.synced_folder(os.path.join(str(tmp_path), "OneDrive - Contoso", "state"), windows_onedrive) == "OneDrive"
    assert doctor.synced_folder("/Users/me/Dropbox/orch", {}) == "Dropbox"
    assert doctor.synced_folder("/Users/me/Dropbox (Personal)/orch", {}) == "Dropbox"
    assert doctor.synced_folder("/Users/me/Library/Mobile Documents/com~apple~CloudDocs/orch", {}) == "iCloud Drive"
    assert doctor.synced_folder("/Users/me/Library/CloudStorage/GoogleDrive-me@example.com/My Drive/x", {}) == "Google Drive"
    assert doctor.synced_folder("/Users/me/Library/CloudStorage/Box-Box/x", {}) == "a cloud storage service"
    assert doctor.synced_folder("/home/me/.local/state/orchestraty", {}) == ""
    assert doctor.synced_folder("/home/me/dropboxed-notes/orch", {}) == ""
    state = State(os.path.join(str(tmp_path), "Dropbox", "orchestraty"))
    check = doctor.state_check(state, {})
    assert check.kind == "fix" and "is in a folder Dropbox syncs" in check.text
    assert check.fix.startswith("set ORCH_HOME to a folder outside it")


def test_python_and_git_versions():
    assert doctor.python_check((3, 9, 18), "/usr/bin/python3.9") == doctor.Check(
        "fix", "Python 3.9.18 (/usr/bin/python3.9): Orchestraty needs 3.10 or later",
        "install Python 3.10 or later, and Orchestraty with it (pipx install --python <it> orchestraty)")
    assert doctor.python_check((3, 12, 4), "/x/python").kind == "ok"
    assert doctor.git_version("git version 2.56.0.windows.2") == (2, 56, 0)
    assert doctor.git_check("git version 2.37.1") == doctor.Check(
        "fix", "git 2.37.1: the together check needs 2.38 or later (merge-tree --write-tree)", "update git")
    assert doctor.git_check("git version 2.38.0") == doctor.Check("ok", "git 2.38.0", "")
    assert doctor.git_check("") == doctor.Check("fix", "git: not found on the PATH", "install git 2.38 or later")


def test_an_engine_that_does_not_import(monkeypatch):
    monkeypatch.setitem(sys.modules, "orch_engine", None)          # import orch_engine now raises ImportError
    check = doctor.engine_check()
    assert check.kind == "fix" and check.text.startswith("engine: does not import (") and "orch_engine" in check.text
    assert check.fix.startswith("reinstall Orchestraty")


def test_not_in_a_repository(tmp_path):
    check = doctor.index_check(str(tmp_path))
    assert check == doctor.note("first index: not in a git repository; run orch doctor in one to time its first index")


def test_a_repository_over_the_file_cap_says_what_is_not_analysed(repo, monkeypatch):
    # The engine reads at most 20,000 files of a commit; here, with the cap at one, b.py is past it.
    from orch_engine import ingest
    commit_files = ingest.commit_files
    monkeypatch.setattr(ingest, "commit_files", lambda path, sha, max_bytes=ingest.MAX_BLOB_BYTES, max_files=20000:
                        commit_files(path, sha, max_bytes=max_bytes, max_files=1))
    check = doctor.index_check(str(repo))
    assert check.kind == "note" and ": 1 files in " in check.text
    assert "reads at most 1 files of a commit, and this one has more: files after a.py in path order are not " \
           "analysed" in check.text
