"""Cloud agents' branches at level 0: fetched read-only into a shadow repository, followed as
sessions of agent "cloud", compared with the live sessions here; the user's .git untouched."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import cli, config, remote, status
from orchestraty.coordinator import main as cmain
from orchestraty.coordinator.cloud import session_id as cloud_id
from orchestraty.delivery import slots
from orchestraty.paths import State

BROKEN = "def charge(amount):\n    return amount\n"
USES = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def origin(repo, git, tmp_path):
    """The user's repository gets a remote, origin: a local bare repository."""
    path = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(path))
    git(repo, "remote", "add", "origin", str(path))
    return path


@pytest.fixture
def agent(origin, git, tmp_path):
    """A cloud agent's own clone of origin: push(branch, {file: text}) pushes a branch from main."""
    clone = tmp_path / "agent-clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))

    def push(branch, files, start="origin/main"):
        git(clone, "fetch", "-q", "origin")
        git(clone, "checkout", "-q", "-B", branch, start)
        for name, text in files.items():
            (clone / name).write_text(text, encoding="utf-8")
            git(clone, "add", name)
        git(clone, "commit", "-q", "-m", f"work on {branch}")
        git(clone, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
        return git(clone, "rev-parse", "HEAD")
    push.clone = clone
    return push


def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c.cloud.wait(120)
        c._last_scan = None
    c.step()


def turn_on(state, repo, **changes):
    config.watch(state, str(repo / ".git"))
    return remote.save_settings(state, str(repo / ".git"), on=True, **changes)


def fetch(state, repo):
    chosen = remote.settings(config.load(state), str(repo / ".git"))
    return remote.fetch_now(state, str(repo / ".git"), chosen, 300)


def cloud_sessions(c, live=True):
    return {s["branch"]: s for s in c.record.sessions() if s["agent"] == "cloud" and (s["state"] == "live") == live}


def fingerprint(repo):
    """A hash of the user's .git: its refs, packed-refs, index, config, HEAD and the list of its objects."""
    gitdir, h = repo / ".git", hashlib.sha256()
    for path in sorted(p for p in (gitdir / "refs").rglob("*") if p.is_file()):
        h.update(path.relative_to(gitdir).as_posix().encode() + b"\0" + path.read_bytes())
    for name in ("packed-refs", "index", "config", "HEAD", "FETCH_HEAD"):
        path = gitdir / name
        h.update(name.encode() + b"\0" + (path.read_bytes() if path.exists() else b"(none)"))
    for path in sorted(p for p in (gitdir / "objects").rglob("*") if p.is_file()):
        h.update(path.relative_to(gitdir).as_posix().encode() + b"\0")
    return h.hexdigest()


@pytest.fixture
def scene(state, repo, origin, agent, git, tmp_path):
    """A local session B in worktree wt-b writes c.py, which calls charge the old way; a cloud
    agent pushes claude/drop-currency, where charge has no currency any more."""
    wb = tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    turn_on(state, repo)
    spool_event(state.root, "B", "SessionStart", wb)
    spool_event(state.root, "B", "UserPromptSubmit", wb)
    (wb / "c.py").write_text(USES, encoding="utf-8")
    spool_event(state.root, "B", "PostToolUse", wb, tool_name="Edit", file_path=str(wb / "c.py"))
    tip = agent("claude/drop-currency", {"a.py": BROKEN})
    c = cmain.Coordinator(state)
    yield c, wb, tip
    c.cloud.wait(120)
    c.close()


def test_a_breaking_change_on_a_cloud_branch_gives_the_local_session_its_note(state, repo, scene):
    c, wb, tip = scene
    assert fetch(state, repo)["ok"]
    settle(c)
    [cloud] = cloud_sessions(c).values()
    assert (cloud["branch"], cloud["worktree"], cloud["id"]) == (
        "origin/claude/drop-currency", "", cloud_id(str(repo / ".git"), "origin", "claude/drop-currency"))
    files, _, symbols = c.record.changes(cloud["id"])
    assert files == {"a.py": "M"}
    assert [(s["symbol"], s["contract"], s["reasons"]) for s in symbols] == [
        ("a.py::charge", "breaking", ["parameter currency removed"])]
    key = f"O3|a.py::charge|{cloud['id']}|B|c.py"
    assert c.record.finding(key)["state"] == "open"
    note = json.loads(Path(slots.edit_slot(state, "B", str(wb / "c.py"))).read_bytes())
    text = note["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("Orchestraty: the cloud branch origin/claude/drop-currency changed the contract of charge "
                           "in a.py: parameter currency removed. The change is in that branch, so your copy does not "
                           "show it yet; both meet when merged. c.py uses it: use (line 5).")
    assert "tell your user if the old form must stay: the agent on that branch works elsewhere and is not told" in text
    assert c.record.notes(session_id=cloud["id"]) == []                 # its own agent can't be told


def test_the_users_repository_is_untouched(state, repo, scene, agent, capsys):
    c, wb, tip = scene
    before = fingerprint(repo)
    assert cli.main(["remote", "on", "--repo", str(repo), "--yes"]) == 0
    assert cli.main(["remote", "fetch", "--repo", str(repo)]) == 0
    settle(c)
    assert cloud_sessions(c)
    agent("claude/drop-currency", {"b.py": "print('more')\n"}, start="claude/drop-currency")
    assert cli.main(["remote", "fetch", "--repo", str(repo)]) == 0
    settle(c)
    assert fingerprint(repo) == before                                 # refs, index, config, objects: the same
    shadow = remote.Shadow(state, str(repo / ".git"))
    alternates = Path(shadow.path, "objects", "info", "alternates").read_text(encoding="utf-8").strip()
    assert os.path.normcase(os.path.normpath(alternates)) == os.path.normcase(os.path.normpath(str(repo / ".git" / "objects")))
    capsys.readouterr()


def test_only_matching_branches_are_followed_and_nothing_happens_while_off(state, repo, origin, agent, git):
    config.watch(state, str(repo / ".git"))
    agent("claude/one", {"a.py": BROKEN})
    agent("codex/two", {"b.py": "x = 2\n"})
    agent("feature/three", {"b.py": "x = 3\n"})
    c = cmain.Coordinator(state)
    try:
        settle(c)                                                       # off: nothing fetched, nothing followed
        shadow = remote.Shadow(state, str(repo / ".git"))
        assert not shadow.exists() and shadow.status() == {} and cloud_sessions(c) == {}
        turn_on(state, repo)
        c.reload_config()
        settle(c)                                                       # on: the coordinator fetches by itself
        assert shadow.status()["ok"] is True
        assert sorted(cloud_sessions(c)) == ["origin/claude/one", "origin/codex/two"]
        refs = subprocess.run(["git", "for-each-ref", "--format=%(refname)"], cwd=shadow.path, capture_output=True,
                              text=True).stdout.split()
        assert sorted(refs) == ["refs/orch/origin/HEAD", "refs/orch/origin/claude/one", "refs/orch/origin/codex/two"]
        remote.save_settings(state, str(repo / ".git"), patterns=["claude/*"])
        c.reload_config()
        settle(c)
        assert sorted(cloud_sessions(c)) == ["origin/claude/one"]
        assert cloud_sessions(c, live=False)["origin/codex/two"]["note"] == "the branch is gone"
        remote.save_settings(state, str(repo / ".git"), on=False)
        c.reload_config()
        settle(c)
        assert cloud_sessions(c) == {} and cloud_sessions(c, live=False)["origin/claude/one"]["note"] == "remote branches off"
        at = shadow.status()["at"]
        settle(c)
        assert shadow.status()["at"] == at                              # off: nothing fetched
    finally:
        c.cloud.wait(120)
        c.close()


def test_a_merged_branch_ends_its_session_and_so_does_a_deleted_one(state, repo, origin, agent, git):
    turn_on(state, repo)
    agent("claude/merge-me", {"b.py": "x = 1\n"})
    agent("codex/delete-me", {"c.py": "y = 1\n"})
    fetch(state, repo)
    c = cmain.Coordinator(state)
    try:
        settle(c)
        assert sorted(cloud_sessions(c)) == ["origin/claude/merge-me", "origin/codex/delete-me"]
        git(agent.clone, "push", "-q", "origin", "claude/merge-me:main")             # merged into the default branch
        git(agent.clone, "push", "-q", "origin", "--delete", "codex/delete-me")      # gone
        fetch(state, repo)
        settle(c)
        assert cloud_sessions(c) == {}
        ended = cloud_sessions(c, live=False)
        assert ended["origin/claude/merge-me"]["note"] == "merged into the default branch"
        assert ended["origin/codex/delete-me"]["note"] == "the branch is gone"
    finally:
        c.cloud.wait(120)
        c.close()


def test_a_shadow_that_lost_an_object_to_the_users_gc_is_fetched_afresh(state, repo, origin, agent, git):
    # The user's own branch tmp holds commit X; the cloud branch builds on X, so the shadow
    # finds X in the user's objects and does not download it. Then the user's gc prunes X.
    git(repo, "checkout", "-q", "-b", "tmp")
    (repo / "d.py").write_text("z = 1\n", encoding="utf-8")
    git(repo, "add", "d.py")
    git(repo, "commit", "-q", "-m", "X")
    x = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "-q", "origin", "tmp:refs/heads/claude/on-x")
    agent("claude/on-x", {"a.py": BROKEN}, start="origin/claude/on-x")
    turn_on(state, repo)
    fetch(state, repo)
    shadow = remote.Shadow(state, str(repo / ".git"))
    assert not (Path(shadow.path) / "objects" / x[:2] / x[2:]).exists()            # read through the alternates
    c = cmain.Coordinator(state)
    try:
        settle(c)
        assert list(cloud_sessions(c)) == ["origin/claude/on-x"]
        git(repo, "checkout", "-q", "main")
        git(repo, "branch", "-q", "-D", "tmp")
        for ref in git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes").split():
            git(repo, "update-ref", "-d", ref)
        git(repo, "reflog", "expire", "--expire=now", "--all")
        git(repo, "gc", "-q", "--prune=now")
        assert subprocess.run(["git", "cat-file", "-e", x], cwd=repo).returncode != 0          # X is gone
        agent("claude/on-x", {"b.py": "x = 9\n"}, start="claude/on-x")                         # the branch moves on
        fetch(state, repo)
        settle(c)                                                      # reading it needs X: fetched afresh, read again
        assert not shadow.broken() and shadow.status()["ok"] is True
        assert subprocess.run(["git", "cat-file", "-e", x], cwd=shadow.path).returncode == 0   # now the shadow's own
        [cloud] = cloud_sessions(c).values()
        assert c.record.changes(cloud["id"])[0] == {"a.py": "M", "b.py": "M", "d.py": "A"}
    finally:
        c.cloud.wait(120)
        c.close()


def test_orch_status_shows_cloud_branches_and_the_last_fetch(state, repo, scene, git, origin):
    c, wb, tip = scene
    assert fetch(state, repo)["ok"]
    settle(c)
    [r] = status.collect(state)["repositories"]
    assert [s["id"] for s in r["sessions"]] == ["B"]                    # the local sessions, apart
    assert (r["cloud"]["on"], r["cloud"]["remote"], r["cloud"]["agents_told"]) == (True, "origin", False)
    assert r["cloud"]["fetch"]["ok"] is True and r["cloud"]["fetch"]["branches"] == 1
    [branch] = r["cloud"]["branches"]
    assert branch["branch"] == "origin/claude/drop-currency" and branch["changes"]["files"] == {"a.py": "M"}
    text = status.render(status.collect(state))
    assert "cloud branches from origin (claude/*, codex/*, copilot/*): level 0, their agents are not told" in text
    assert "origin/claude/drop-currency  last commit" in text and "charge [breaking: parameter currency removed]" in text
    assert "breaking change in origin/claude/drop-currency, used in c.py by B" in text
    # a failed fetch: kept, shown, and the wait before the next one doubled
    git(repo, "remote", "set-url", "origin", str(origin.parent / "no-such-remote.git"))
    for _ in range(2):
        assert fetch(state, repo)["ok"] is False
    [r] = status.collect(state)["repositories"]
    assert (r["cloud"]["fetch"]["ok"], r["cloud"]["fetch"]["failures"], r["cloud"]["fetch"]["branches"]) == (False, 2, 1)
    assert "failed (2 in a row)" in status.render(status.collect(state))
