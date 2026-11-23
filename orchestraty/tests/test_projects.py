"""Every project an agent works in: watching all of them (watch_all), leaving some out, and
folder projects, followed through Orchestraty's own snapshots when there is no git."""
import json
import os

import pytest

from helpers import spool_event, tree
from orchestraty import cli, config, connections, gitio, status
from orchestraty.coordinator import main as cmain
from orchestraty.livemap import snapshots
from orchestraty.paths import State

ORIGINAL = "def charge(amount, currency):\n    return amount\n"
BROKEN = "def charge(amount):\n    return amount\n"
CALLER = "from a import charge\n\n\ndef pay():\n    return charge(1, 'EUR')\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def folder(tmp_path):
    """A project folder with no git: a function and a caller of it."""
    top = tmp_path / "shop"
    top.mkdir()
    (top / "a.py").write_text(ORIGINAL, encoding="utf-8")
    (top / "b.py").write_text(CALLER, encoding="utf-8")
    return top


@pytest.fixture
def coordinator(state):
    made = []

    def make():
        c = cmain.Coordinator(state)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


# ------------------------------------------------------------------ the rules in config.json
def test_watch_all_watches_every_project_but_the_excluded_ones(state, tmp_path):
    a, b = str(tmp_path / "a" / ".git"), str(tmp_path / "b")
    cfg = config.load(state)
    assert cfg["watch_all"] is False and cfg["exclude"] == []          # an older config.json widens nothing
    assert not config.is_watched(cfg, a)
    assert config.set_watch_all(state, True) and not config.set_watch_all(state, True)
    cfg = config.load(state)
    assert config.is_watched(cfg, a) and config.is_watched(cfg, b)
    assert config.unwatch(state, b) and config.load(state)["exclude"] == [os.path.normpath(b)]
    assert not config.is_watched(config.load(state), b)
    assert config.watch(state, b) and config.load(state)["exclude"] == []
    assert config.watched(config.load(state), [a, b, a]) == [os.path.normpath(b), a]


def test_a_folder_can_be_a_project_but_never_the_home_folder_or_a_drive(state, tmp_path, folder):
    os.makedirs(os.path.expanduser("~"), exist_ok=True)
    assert snapshots.can_follow(str(folder), state.root) == (True, "")
    assert snapshots.can_follow(os.path.expanduser("~"), state.root)[1] == "the home folder"
    assert snapshots.can_follow(os.path.dirname(os.path.expanduser("~")), state.root)[1] == "the home folder"
    assert snapshots.can_follow(os.path.abspath(os.sep), state.root)[1] in ("a drive's top folder", "the home folder")
    os.makedirs(state.root, exist_ok=True)
    assert snapshots.can_follow(state.root, state.root)[1] == "Orchestraty's own folder"


# ------------------------------------------------------------------ snapshots
def test_a_snapshot_keeps_files_once_and_skips_what_is_not_the_project(state, folder, monkeypatch):
    (folder / "node_modules" / "x").mkdir(parents=True)
    (folder / "node_modules" / "x" / "index.js").write_text("x", encoding="utf-8")
    (folder / "env").mkdir()
    (folder / "env" / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    (folder / "env" / "lib.py").write_text("x = 1", encoding="utf-8")
    (folder / "big.bin").write_bytes(b"0" * (snapshots.MAX_FILE_BYTES + 1))
    before = tree(folder)
    base = snapshots.take(state.root, str(folder))
    assert base.startswith("snap:") and snapshots.take(state.root, str(folder)) == base    # the same: one snapshot
    assert sorted(snapshots.manifest(state.root, str(folder), base)) == ["a.py", "b.py"]
    assert tree(folder) == before                                          # nothing written into the project
    assert snapshots.changed_files(state.root, str(folder), base) == {}
    original = (folder / "a.py").read_bytes()
    (folder / "a.py").write_text(BROKEN, encoding="utf-8")
    (folder / "c.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "b.py").unlink()
    assert snapshots.changed_files(state.root, str(folder), base) == {"a.py": "M", "c.py": "A", "b.py": "D"}
    blobs = snapshots.blob_ids(state.root, str(folder), base, ["a.py"])
    assert snapshots.read_blobs(state.root, blobs.values()) == {blobs["a.py"]: original}
    monkeypatch.setattr(snapshots, "MAX_FILES", 1)
    with pytest.raises(snapshots.TooLarge):
        snapshots.take(state.root, str(folder))


# ------------------------------------------------------------------ the coordinator
def test_with_watch_all_every_repository_and_folder_is_followed(state, repo, folder, tmp_path, coordinator):
    config.set_watch_all(state, True)
    spool_event(state.root, "in-repo", "SessionStart", repo)
    spool_event(state.root, "in-folder", "SessionStart", folder)
    c = coordinator()
    c.step()
    r, f = c.record.session("in-repo"), c.record.session("in-folder")
    assert r and same(r["worktree"], repo) and len(r["base_commit"]) == 40
    assert f and same(f["worktree"], folder) and f["base_commit"].startswith("snap:") and f["branch"] == ""
    data = status.collect(state)
    kinds = {p["name"]: p["kind"] for p in data["repositories"]}
    assert kinds == {"repo": "git", "shop": "folder"}
    assert "shop  (a folder without git: " in status.render(data)


def test_an_excluded_project_is_not_followed(state, folder, coordinator):
    config.set_watch_all(state, True)
    config.unwatch(state, str(folder))
    spool_event(state.root, "s", "SessionStart", folder)
    c = coordinator()
    c.step()
    assert c.record.sessions() == []


def test_a_session_leaves_its_plain_parent_when_an_edit_targets_a_nested_repository(state, folder, git, coordinator):
    project = folder / "checkout"
    project.mkdir()
    git(project, "init", "-q")
    path = project / "code.py"
    path.write_text(ORIGINAL, encoding="utf-8")
    git(project, "add", "code.py")
    git(project, "commit", "-qm", "base")
    config.set_watch_all(state, True)
    spool_event(state.root, "move", "SessionStart", folder, agent="codex")
    c = coordinator()
    c.step()
    first = c.record.session("move")
    assert same(first["worktree"], folder) and first["base_commit"].startswith("snap:")
    assert "checkout/code.py" not in snapshots.manifest(state.root, str(folder), first["base_commit"])
    c.record.add_claim(first["repo_id"], "move", "file", "a.py", "a.py", False, 1, 8 * 10**18)
    c.record.add_declaration("move", ["a.py"], [], "", 1, 8 * 10**18)
    path.write_text(BROKEN, encoding="utf-8")
    spool_event(state.root, "move", "PostToolUse", folder, agent="codex", tool_name="apply_patch", file_path=str(path))
    spool_event(state.root, "move", "Stop", folder, agent="codex")
    c.step()
    moved = c.record.session("move")
    assert moved["repo_id"] != first["repo_id"] and same(moved["worktree"], project)
    assert moved["branch"] == "main" and moved["base_guessed"] and moved["turn"] == "idle"
    assert c.record.changes("move")[0] == {"code.py": "M"}
    assert c.record.claims(repo_id=first["repo_id"])[0]["why"] == "session changed project"
    assert c.record.declarations(active_at=c.now_ns()) == []
    assert [r["kind"] for r in status.collect(state)["repositories"]] == ["git"]


def test_antigravity_workspace_roots_and_shell_directory_identify_the_right_worktree(state, repo, folder, git,
                                                                                           tmp_path, coordinator):
    other = tmp_path / "other worktree"
    git(repo, "worktree", "add", "-q", "-b", "other", str(other))
    config.set_watch_all(state, True)
    roots = [str(folder), str(repo), str(other)]
    spool_event(state.root, "multi", "SessionStart", folder, agent="antigravity", workspace_paths=roots)
    c = coordinator()
    c.step()
    first = c.record.session("multi")
    assert same(first["worktree"], repo)
    spool_event(state.root, "multi", "PostToolUse", folder, agent="antigravity", tool_name="run_command",
                workspace_paths=roots, tool_cwd=str(other))
    spool_event(state.root, "multi", "PreInvocation", folder, agent="antigravity", workspace_paths=roots)
    c.step()
    moved = c.record.session("multi")
    assert moved["repo_id"] == first["repo_id"] and same(moved["worktree"], other) and moved["branch"] == "other"


def test_a_failed_git_probe_reports_the_received_event_and_recovers_on_the_next_one(state, repo, coordinator,
                                                                                   monkeypatch, capsys):
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0
    capsys.readouterr()
    run = gitio.run

    def failing(*args, **kwargs):
        raise gitio.GitError("git rev-parse: FileNotFoundError") from FileNotFoundError("git")

    monkeypatch.setattr(gitio, "run", failing)
    monkeypatch.setattr(gitio, "missing", lambda: False)            # git is installed, and fails
    assert cli.main(["watch", str(repo)]) == 2
    assert "Could not inspect Git worktree" in capsys.readouterr().err
    assert config.load(state)["repos"] == []
    spool_event(state.root, "retry", "SessionStart", repo, agent="codex")
    c = coordinator()
    c.step()
    assert c.record.session("retry") is None
    row = next(row for row in connections.summary(state) if row["agent"] == "codex")
    assert row["status"] == "event_issue" and "Git could not inspect" in row["message"]
    monkeypatch.setattr(gitio, "run", run)
    spool_event(state.root, "retry", "UserPromptSubmit", repo, agent="codex")
    c.step()
    assert same(c.record.session("retry")["worktree"], repo)
    row = next(row for row in connections.summary(state) if row["agent"] == "codex")
    assert row["status"] == "event_received"


def test_with_no_git_on_the_path_a_checkout_is_followed_as_a_folder(state, repo, coordinator, monkeypatch, capsys):
    assert cli.main(["init", "--agent", "codex", "--yes", "--no-git-hooks"]) == 0

    def no_git(*args, **kwargs):
        raise gitio.GitError("git rev-parse: FileNotFoundError") from FileNotFoundError("git")

    monkeypatch.setattr(gitio, "run", no_git)
    monkeypatch.setattr(gitio, "missing", lambda: True)             # GitHub Desktop keeps its git off the PATH
    assert cli.main(["watch", str(repo)]) == 0
    assert "followed as a folder" in capsys.readouterr().err
    config.set_watch_all(state, True)
    spool_event(state.root, "nogit", "SessionStart", repo, agent="codex")
    c = coordinator()
    c.step()
    s = c.record.session("nogit")
    assert same(s["worktree"], repo) and s["base_commit"].startswith("snap:")
    (repo / "a.py").write_text(BROKEN, encoding="utf-8")
    spool_event(state.root, "nogit", "PostToolUse", repo, agent="codex", tool_name="apply_patch",
                file_path=str(repo / "a.py"))
    c.step()
    assert c.record.changes("nogit")[0] == {"a.py": "M"} and same(c.record.session("nogit")["worktree"], repo)
    row = next(row for row in connections.summary(state) if row["agent"] == "codex")
    assert row["status"] == "event_received"


def test_a_folder_project_has_its_changes_and_callers_left_behind(state, folder, coordinator):
    config.set_watch_all(state, True)
    before = tree(folder)
    spool_event(state.root, "S", "SessionStart", folder)
    c = coordinator()
    c.step()
    assert c.livemap.indexes.wait(120)
    (folder / "a.py").write_text(BROKEN, encoding="utf-8")
    spool_event(state.root, "S", "PostToolUse", folder, tool_name="Edit", file_path=str(folder / "a.py"))
    c.step()
    c.livemap.indexes.wait(120)
    c._last_scan = None
    c.step()
    files, _, symbols = c.record.changes("S")
    assert files == {"a.py": "M"} and [s["symbol"] for s in symbols] == ["a.py::charge"]
    assert {f["key"] for f in c.record.findings()} == {"O6|a.py::charge|S"}   # b.py still calls the old form
    after = tree(folder)
    assert set(after) == set(before)                                        # nothing added to the project


def test_without_watch_all_a_folder_is_offered_and_can_be_watched(state, folder, coordinator):
    spool_event(state.root, "s", "SessionStart", folder)
    c = coordinator()
    c.step()
    assert c.record.sessions() == []
    kept = json.loads(open(state.unwatched, encoding="utf-8").read())["repositories"]
    assert [(r["name"], same(r["folder"], folder)) for r in kept] == [("shop", True)]
    config.watch(state, str(folder))
    os.utime(state.config, None)
    c._cfg_stamp = None
    c.reload_config()
    spool_event(state.root, "s", "UserPromptSubmit", folder)
    c.step()
    assert c.record.session("s")["base_commit"].startswith("snap:")


# ------------------------------------------------------------------ orch init
def test_orch_init_asks_to_follow_every_project_and_sets_up_the_agents_it_finds(state, tmp_path, monkeypatch,
                                                                                  capsys):
    os.makedirs(os.environ["CODEX_HOME"])                    # Codex has been used on this computer
    monkeypatch.chdir(tmp_path)                              # anywhere: no repository needed
    assert cli.main(["init", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Following every project your agents work in." in out
    assert "Codex is on this computer too: orch init --agent codex sets it up." in out    # named, not changed
    cfg = config.load(state)
    assert cfg["watch_all"] is True and {e["agent"] for e in cfg["installed"]} == {"claude"}
    asked = []
    monkeypatch.setattr(cli, "confirm", lambda question, yes: asked.append(question) or True)
    assert cli.main(["init"]) == 0                           # run by hand: it asks
    assert any(q.startswith("Codex is on this computer too") for q in asked)
    assert {e["agent"] for e in config.load(state)["installed"]} == {"claude", "codex"}
    assert os.path.isfile(os.path.join(os.environ["CODEX_HOME"], "hooks.json"))
    assert cli.main(["config", "track"]) == 0 and "track: on" in capsys.readouterr().out
    assert cli.main(["config", "track", "off"]) == 0 and config.load(state)["watch_all"] is False
