"""orch doctor, in an isolated state folder: a fix for each problem, and every check passing once
orch init has run in a repository."""
import json
import os
import sys
import time

import pytest

from orchestraty import cli, config, doctor
from orchestraty.adapters import claude
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


def test_hooks_of_another_state_folder_are_a_problem(state_root, tmp_path):
    assert cli.main(["init", "--yes", "--no-git-hooks"]) == 0
    settings_file = tmp_path / "claude-config" / "settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    old = tmp_path / "OneDrive" / "state"                                    # an orch init with another ORCH_HOME
    for event, entries in claude.hook_block(sys.executable, str(old / "hooks" / "hook.py")).items():
        settings["hooks"][event] = entries + settings["hooks"][event]
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    state = State()
    lines = doctor.agent_checks(state, config.load(state))
    assert doctor.problem(f"Claude Code: {settings_file} also runs the hooks of the state folder {old}, which an orch "
                          "init with another ORCH_HOME added, so every event is recorded there too",
                          "run orch init again: it takes them out") in lines
    assert doctor.ok(f"Claude Code: hooks in {settings_file}") in lines                 # this folder's are right
    assert cli.main(["init", "--yes", "--no-git-hooks"]) == 0
    assert all(line.kind != "fix" for line in doctor.agent_checks(state, config.load(state)))


def test_a_state_folder_that_denies_writing_is_said_at_once(state_root, monkeypatch):
    """On Windows a folder whose access list denies writing still passes os.access, and
    tempfile then tries name after name (2**31 of them). One try is enough."""
    state_root.mkdir(parents=True)
    real_open, tries = os.open, []

    def denied(path, flags, *args, **kwargs):
        if os.path.dirname(os.path.abspath(path)) == str(state_root) and flags & os.O_CREAT:
            tries.append(path)
            if len(tries) > 50:
                raise OSError("tried too often")
            raise PermissionError(13, "Access is denied", path)
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", denied)
    check = doctor.state_check(State(), {})
    assert check.kind == "fix" and check.text.endswith("cannot be written to (PermissionError)")
    assert len(tries) == 1


def test_the_state_folder_check_leaves_nothing_behind(state_root):
    state_root.mkdir(parents=True)
    assert doctor.state_check(State(), {}).kind == "ok" and os.listdir(state_root) == []


def test_a_record_that_cannot_be_read_says_to_move_it_aside(state_root):
    state_root.mkdir(parents=True)
    state = State()
    assert doctor.record_check(state).kind == "note" and not os.path.exists(state.record)    # none made
    with open(state.record, "wb") as fh:
        fh.write(b"GARBAGE!" * 512)
    check = doctor.record_check(state)
    assert check.kind == "fix" and check.text.endswith("cannot be read (file is not a database)")
    assert "aside" in check.fix and "orch init" not in check.fix


def test_a_coordinator_that_cannot_read_its_record_says_so(state_root, tmp_path):
    from orchestraty.coordinator import main as cmain
    assert doctor.BAD_RECORD == cmain.BAD_RECORD
    broken = tmp_path / "bad" / "orchestraty"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("", encoding="utf-8")
    (broken / "__main__.py").write_text(f"raise SystemExit({cmain.BAD_RECORD})\n", encoding="utf-8")
    hooks_folder = state_root / "hooks"
    hooks_folder.mkdir(parents=True)
    (hooks_folder / "launch.txt").write_text(f"{sys.executable}\n{broken.parent}\n", encoding="utf-8")
    check = doctor.coordinator_check(State(), seconds=30)
    assert check.kind == "fix" and "cannot be read" in check.text
    assert "aside" in check.fix and "orch init" not in check.fix


def test_a_record_that_cannot_be_written_is_a_problem(state_root):
    """The coordinator opens a read-only record and then fails every step, so the record reading
    whole is not enough."""
    import stat
    from orchestraty.coordinator.record import Record
    state_root.mkdir(parents=True)
    state = State()
    Record(state.record).close()
    os.chmod(state.record, stat.S_IREAD)
    try:
        if os.access(state.record, os.W_OK):
            pytest.skip("this user writes read-only files")
        check = doctor.record_check(state)
        assert check.kind == "fix" and check.text == f"record: {state.record} cannot be written to (read-only)"
        assert "write access" in check.fix and "orch init" not in check.fix
        assert not os.path.exists(state.record + "-wal") and not os.path.exists(state.record + "-shm")  # none made
    finally:
        os.chmod(state.record, stat.S_IREAD | stat.S_IWRITE)
    assert doctor.record_check(state).kind == "ok"


def test_a_record_that_cannot_be_opened_is_named_itself(state_root):
    state_root.mkdir(parents=True)
    state = State()
    os.makedirs(state.record)
    assert doctor.state_check(state, {}).kind == "ok"               # so no line above is to blame
    check = doctor.record_check(state)
    assert check.kind == "fix" and check.text == f"record: {state.record} is a folder, not a file"
    assert "aside" in check.fix and "state folder" not in check.fix and "orch init" not in check.fix


def _fake_coordinator(state_root, tmp_path, source):
    """launch.txt's import path leads to an orchestraty whose __main__ is `source`."""
    fake = tmp_path / "fake" / "orchestraty"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("", encoding="utf-8")
    (fake / "__main__.py").write_text(source, encoding="utf-8")
    hooks_folder = state_root / "hooks"
    hooks_folder.mkdir(parents=True)
    (hooks_folder / "launch.txt").write_text(f"{sys.executable}\n{fake.parent}\n", encoding="utf-8")


def test_a_coordinator_that_cannot_read_its_record_after_it_seemed_to_start(state_root, tmp_path):
    """run() touches its heartbeat and writes coordinator.json before it opens the record, so
    doctor can see both, stop it, and must still look at how it exited."""
    from orchestraty.coordinator import main as cmain
    _fake_coordinator(state_root, tmp_path, (
        "import os, time\n"
        "names = [os.path.join(os.environ['ORCH_HOME'], n) for n in ('coordinator.alive', 'coordinator.json')]\n"
        "for name in names:\n"
        "    open(name, 'w').close()\n"
        "time.sleep(1)\n"
        "for name in names:\n"
        "    os.remove(name)\n"
        f"raise SystemExit({cmain.BAD_RECORD})\n"))
    check = doctor.coordinator_check(State(), seconds=30)
    assert check.kind == "fix" and "cannot be read" in check.text, check


def test_doctor_ends_the_hold_off_its_own_start_caused(state_root):
    """The coordinator doctor starts on a damaged record holds the hooks off for a minute. That
    start was doctor's, not a hook's: once the record is moved aside, the next hook starts one."""
    from orchestraty.capture import install
    state_root.mkdir(parents=True)
    state = State()
    install.write_launch(state)
    with open(state.record, "wb") as fh:
        fh.write(b"GARBAGE!" * 512)
    check = doctor.coordinator_check(state, seconds=30)
    assert check.kind == "fix" and "cannot be read" in check.text
    assert os.stat(state.spawned).st_mtime < time.time() - hook.RESPAWN_SECONDS
    assert "within a minute" in check.fix and "orch doctor again" in check.fix       # a hook may have tried since


def test_a_state_folder_where_files_cannot_be_deleted_says_so(state_root, monkeypatch):
    """Where files can be made there but not deleted, the probe file stays behind: doctor says
    what is wrong, and removes the probe files earlier runs left once it can."""
    state_root.mkdir(parents=True)
    (state_root / ".orch-doctor-notes").write_text("not doctor's", encoding="utf-8")
    real_remove = os.remove

    def denied(path, *args, **kwargs):
        if os.path.dirname(os.path.abspath(path)) == str(state_root):
            raise PermissionError(13, "Access is denied", path)
        return real_remove(path, *args, **kwargs)
    monkeypatch.setattr(os, "remove", denied)
    check = doctor.state_check(State(), {})
    assert check.kind == "fix" and check.text.endswith("files cannot be deleted in it (PermissionError)")
    assert "delete" in check.fix
    assert len(os.listdir(state_root)) == 2                          # the probe file stayed
    monkeypatch.setattr(os, "remove", real_remove)
    assert doctor.state_check(State(), {}).kind == "ok"
    assert os.listdir(state_root) == [".orch-doctor-notes"]          # gone now, and nothing else
