"""The same-folder note (W1) end to end: two sessions with hooks in one worktree each hear it
once, with their next tool result, through the real hook script."""
import json
import os

import pytest

from helpers import run_hook, spool_event
from orchestraty import config, status
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.paths import State

TEXT = ("Orchestraty: another Claude Code session (branch main) is working in this same folder. Its edits, branch "
        "switches and commits change this folder for you too, so either session can overwrite or switch the other's "
        "uncommitted work. To keep your work apart, use a separate worktree (`git worktree add <folder> -b <branch>`), "
        "or ask your user.")


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def coordinator(state, repo, **cfg):
    config.watch(state, str(repo / ".git"))
    if cfg:
        c = config.load(state)
        c.update(cfg)
        config.save(state, c)
    return cmain.Coordinator(state)


def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def start(state, sid, cwd):
    os.makedirs(cwd, exist_ok=True)
    spool_event(state.root, sid, "SessionStart", cwd, source="startup")


def w1(c, states=("open",)):
    return {f["key"]: f for f in c.record.findings(states=states) if f["rule"] == "W1"}


def w1_notes(c, sid):
    return {n["finding_key"]: n for n in c.record.notes(session_id=sid) if n["finding_key"].startswith("W1|")}


def take_next(hook_script, sid, cwd):
    """The session's next tool result: its PostToolUse hook prints what waits in its next slot."""
    payload = json.dumps({"session_id": sid, "hook_event_name": "PostToolUse", "cwd": str(cwd), "tool_name": "Read",
                          "tool_input": {"file_path": str(cwd / "a.py")}, "tool_use_id": "toolu_" + sid}).encode()
    out = run_hook(hook_script, ["claude", "PostToolUse"], payload).stdout
    return json.loads(out)["hookSpecificOutput"] if out else None


def test_two_sessions_in_one_folder_each_hear_it_once(state, repo, hook_script):
    c = coordinator(state, repo, steer=True)                      # it only informs, in steer mode too
    try:
        start(state, "A", repo / "src")                           # each started in a subfolder of its own
        start(state, "B", repo / "docs")
        settle(c)
        [key] = w1(c)
        assert key == f"W1|{c.sessions['A']['worktree']}|A|B" and w1(c)[key]["strength"] == "medium"
        assert f"medium  W1  A and B work in the same folder, {repo.name}" in status.render(status.collect(state))
        for me, cwd in (("A", repo / "src"), ("B", repo / "docs")):
            told = take_next(hook_script, me, cwd)
            assert told == {"hookEventName": "PostToolUse", "additionalContext": TEXT}
        settle(c)
        for me in ("A", "B"):
            [note] = w1_notes(c, me).values()
            assert (note["kind"], note["state"]) == ("next", "delivered")
            assert note["written"] - note["cause_ts"] >= 0
            assert not slots.waiting(slots.next_slot(state, me))                      # once
            assert take_next(hook_script, me, repo) is None
    finally:
        c.close()


def test_two_worktrees_of_one_repository_get_none(state, repo, git, tmp_path):
    wb = tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    c = coordinator(state, repo)
    try:
        start(state, "A", repo)
        start(state, "B", wb)
        settle(c)
        assert w1(c, states=None) == {} and c.record.notes() == []
    finally:
        c.close()


def test_a_third_session_gives_each_new_pair_its_own_note(state, repo, hook_script):
    c = coordinator(state, repo)
    try:
        start(state, "A", repo)
        start(state, "B", repo)
        settle(c)
        take_next(hook_script, "A", repo)
        take_next(hook_script, "B", repo)
        start(state, "C", repo / "tests")
        settle(c)
        top = c.sessions["A"]["worktree"]
        assert set(w1(c)) == {f"W1|{top}|A|B", f"W1|{top}|A|C", f"W1|{top}|B|C"}
        assert set(w1_notes(c, "A")) == {f"W1|{top}|A|B", f"W1|{top}|A|C"}
        assert set(w1_notes(c, "B")) == {f"W1|{top}|A|B", f"W1|{top}|B|C"}
        assert set(w1_notes(c, "C")) == {f"W1|{top}|A|C", f"W1|{top}|B|C"}
        told_c = take_next(hook_script, "C", repo / "tests")["additionalContext"]
        assert told_c.count("is working in this same folder") == 2                   # one slot, both notes
        told_a = take_next(hook_script, "A", repo)["additionalContext"]
        assert told_a == TEXT                                                          # about C, once
    finally:
        c.close()


def test_when_one_session_ends_the_finding_resolves(state, repo):
    c = coordinator(state, repo)
    try:
        start(state, "A", repo)
        start(state, "B", repo)
        settle(c)
        [key] = w1(c)
        assert slots.waiting(slots.next_slot(state, "A"))
        spool_event(state.root, "B", "SessionEnd", repo, reason="other")
        settle(c)
        assert w1(c) == {} and w1(c, states=("resolved",)).keys() == {key}
        assert w1_notes(c, "A")[key]["state"] == "withdrawn"                   # never taken: taken back
        assert not slots.waiting(slots.next_slot(state, "A"))
    finally:
        c.close()


def test_a_plain_git_session_in_the_folder_gets_none_and_causes_none(state, repo, git, tmp_path):
    wc = tmp_path / "wt-c"
    git(repo, "worktree", "add", "-q", "-b", "feat-c", str(wc))
    (wc / "c.py").write_text("x = 1\n", encoding="utf-8")         # changes made outside any agent session
    c = coordinator(state, repo, level0_seconds=3600)
    try:
        start(state, "A", repo)
        settle(c)
        [plain] = [s for s in c.sessions.values() if s["agent"] == "git"]
        start(state, "B", wc)                                     # a hooked session joins the plain-git one
        c.step()
        c.livemap.indexes.wait(120)
        c.step()
        assert plain["id"] in c.sessions and c.sessions[plain["id"]]["state"] == "live"   # both live in wt-c now
        assert w1(c, states=None) == {}
        assert [n for n in c.record.notes() if n["finding_key"].startswith("W1|")] == []
        settle(c)                                                 # the next look ends the plain-git one
        assert plain["id"] not in c.sessions and w1(c, states=None) == {}
    finally:
        c.close()


def test_a_session_that_moves_to_another_worktree_leaves_the_folder(state, repo, git, tmp_path):
    c = coordinator(state, repo)
    try:
        start(state, "A", repo)
        start(state, "B", repo)
        settle(c)
        [key] = w1(c)
        wx = tmp_path / "wt-x"                                     # B follows the note: a worktree of its own
        git(repo, "worktree", "add", "-q", "-b", "feat-x", str(wx))
        (wx / "x.py").write_text("x = 1\n", encoding="utf-8")
        spool_event(state.root, "B", "PostToolUse", wx, tool_name="Write", file_path=str(wx / "x.py"))
        settle(c)
        moved = c.record.session("B")
        assert os.path.normcase(moved["worktree"]) == os.path.normcase(str(wx)) and moved["branch"] == "feat-x"
        assert moved["base_guessed"] == 1
        assert w1(c) == {} and key in w1(c, states=("resolved",))
        files, _, _ = c.record.changes("B")
        assert files == {"x.py": "A"}
    finally:
        c.close()


def test_a_worktree_made_inside_the_repositorys_folder_counts_as_leaving(state, repo, git):
    c = coordinator(state, repo)
    try:
        start(state, "A", repo)
        start(state, "B", repo)
        settle(c)
        nested = repo / ".worktrees" / "b"
        git(repo, "worktree", "add", "-q", "-b", "feat-nested", str(nested))
        (nested / "y.py").write_text("y = 1\n", encoding="utf-8")
        spool_event(state.root, "B", "PostToolUse", nested, tool_name="Write", file_path=str(nested / "y.py"))
        settle(c)
        assert os.path.normcase(c.record.session("B")["worktree"]) == os.path.normcase(str(nested))
        assert w1(c) == {}
        # An edit in a plain subfolder is no move.
        (repo / "src").mkdir()
        (repo / "src" / "z.py").write_text("z = 1\n", encoding="utf-8")
        spool_event(state.root, "A", "PostToolUse", repo / "src", tool_name="Write", file_path=str(repo / "src" / "z.py"))
        settle(c)
        assert os.path.normcase(c.record.session("A")["worktree"]) == os.path.normcase(str(repo))
    finally:
        c.close()
