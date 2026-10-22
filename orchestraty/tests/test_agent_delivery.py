"""Notes for Codex and Antigravity sessions, end to end inside one coordinator: each gets only
the notes its hooks can hand over, in the form its agent accepts, and the real hook script
takes them at that agent's events."""
import json
import os

import pytest

from helpers import ag, codex, patch_event, run_hook, spool_event
from orchestraty import config, status
from orchestraty.adapters import antigravity as ag_adapter
from orchestraty.adapters import codex as codex_adapter
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.paths import State

BROKEN = "def charge(amount):\n    return amount\n"
USES = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def start(state, repo, git, tmp_path, agent_b):
    """A (Claude Code) in worktree wt-a, B (`agent_b`) in wt-b."""
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    spool_event(state.root, "A", "SessionStart", wa)
    spool_event(state.root, "B", "SessionStart", wb, agent=agent_b)
    spool_event(state.root, "A", "UserPromptSubmit", wa)
    spool_event(state.root, "B", "PreInvocation" if agent_b == "antigravity" else "UserPromptSubmit", wb,
                agent=agent_b)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    return c, wa, wb


def edit(state, c, sid, top, rel, text, agent="claude"):
    path = top / rel
    path.write_text(text, encoding="utf-8")
    tool = {"claude": "Edit", "codex": "apply_patch", "antigravity": "write_to_file"}[agent]
    spool_event(state.root, sid, "PostToolUse", top, agent=agent, tool_name=tool, file_path=str(path))
    settle(c)


def test_a_codex_session_hears_before_it_edits_and_with_its_next_result(state, repo, git, tmp_path, hook_script):
    c, wa, wb = start(state, repo, git, tmp_path, "codex")
    try:
        edit(state, c, "B", wb, "c.py", USES, agent="codex")
        edit(state, c, "A", wa, "a.py", BROKEN)
        edit_slot = slots.edit_slot(state, "B", str(wb / "c.py"))
        with open(edit_slot, "rb") as fh:
            data = fh.read()
        text = json.loads(data)["hookSpecificOutput"]["additionalContext"]
        assert data == codex_adapter.output("edit", text)
        assert "changed the contract of charge in a.py: parameter currency removed" in text
        with open(slots.next_slot(state, "A"), "rb") as fh:
            told_a = json.loads(fh.read())["hookSpecificOutput"]["additionalContext"]
        assert "That session is told before it next edits that file." in told_a
        patch = ("*** Begin Patch\n*** Update File: c.py\n@@\n-    return charge(2, 'USD')\n+    return charge(2)\n"
                 "*** End Patch")
        proc = run_hook(hook_script, ["codex", "PreToolUse"], patch_event("PreToolUse", "B", wb, patch))
        assert proc.stdout == data                                  # the real hook hands it over
        settle(c)
        about_c = [n for n in c.record.notes(session_id="B") if n["finding_key"].endswith("|c.py")]
        assert {n["kind"] for n in about_c} == {"edit", "next"} and {n["state"] for n in about_c} == {"delivered"}
    finally:
        c.close()


def test_a_codex_session_that_left_callers_behind_is_kept_going_in_codexs_form(state, repo, git, tmp_path,
                                                                                hook_script):
    c, wa, wb = start(state, repo, git, tmp_path, "codex")
    try:
        edit(state, c, "B", wb, "a.py", BROKEN, agent="codex")          # b.py's pay still calls charge(1, 'EUR')
        with open(slots.stop_slot(state, "B"), "rb") as fh:
            stop = json.loads(fh.read())
        assert stop["decision"] == "block" and "pay in b.py (line 5)" in stop["reason"]
        kept = run_hook(hook_script, ["codex", "Stop"], codex("Stop", "B", wb, stop_hook_active=True))
        assert kept.stdout == b""                                          # already kept going once
        proc = run_hook(hook_script, ["codex", "Stop"], codex("Stop", "B", wb, stop_hook_active=False))
        assert json.loads(proc.stdout) == stop
    finally:
        c.close()


def test_an_antigravity_session_hears_with_its_next_step_and_never_before_an_edit(state, repo, git, tmp_path,
                                                                                   hook_script):
    c, wa, wb = start(state, repo, git, tmp_path, "antigravity")
    try:
        edit(state, c, "A", wa, "a.py", BROKEN)
        # b.py in B's copy calls charge, but B is not changing it: a latent finding, which an
        # agent hears before its first edit of that file. Antigravity cannot: no note at all.
        assert c.record.notes(session_id="B") == []
        assert not os.path.exists(os.path.join(slots.folder(state, "B"), "edit"))
        edit(state, c, "B", wb, "c.py", USES, agent="antigravity")
        kinds = {n["kind"] for n in c.record.notes(session_id="B")}
        assert kinds == {"next"}
        with open(slots.next_slot(state, "B"), "rb") as fh:
            data = fh.read()
        text = json.loads(data)["injectSteps"][0]["ephemeralMessage"]
        assert data == ag_adapter.output("next", text) and "c.py uses it: use (line 5)" in text
        with open(slots.next_slot(state, "A"), "rb") as fh:
            told_a = json.loads(fh.read())["hookSpecificOutput"]["additionalContext"]
        assert "That session is told with its next step." in told_a
        proc = run_hook(hook_script, ["antigravity", "PreInvocation"], ag("PreInvocation", "B", [wb], invocationNum=4))
        assert proc.stdout == data
        settle(c)
        assert [n["state"] for n in c.record.notes(session_id="B")] == ["delivered"]
    finally:
        c.close()


def test_an_antigravity_session_that_left_callers_behind_is_kept_going_in_its_form(state, repo, git, tmp_path,
                                                                                     hook_script):
    c, wa, wb = start(state, repo, git, tmp_path, "antigravity")
    try:
        edit(state, c, "B", wb, "a.py", BROKEN, agent="antigravity")
        with open(slots.stop_slot(state, "B"), "rb") as fh:
            stop = json.loads(fh.read())
        assert stop["decision"] == "continue" and "pay in b.py (line 5)" in stop["reason"]
        proc = run_hook(hook_script, ["antigravity", "Stop"], ag("Stop", "B", [wb], fullyIdle=True))
        assert json.loads(proc.stdout) == stop
    finally:
        c.close()


def test_an_antigravity_step_counts_as_working(state, repo, git, tmp_path):
    c, wa, wb = start(state, repo, git, tmp_path, "antigravity")
    try:
        assert c.sessions["B"]["turn"] == "working" and c.sessions["B"]["agent"] == "antigravity"
        spool_event(state.root, "B", "Stop", wb, agent="antigravity")
        c.step()
        assert c.sessions["B"]["turn"] == "idle"
    finally:
        c.close()


def test_two_codex_sessions_of_one_shared_process_are_two_sessions_in_their_own_worktrees(
        state, repo, git, tmp_path, hook_script):
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    env = dict(os.environ, PWD=str(wa), ORCH_NO_COORDINATOR="1")      # A's process, environment and folder for both
    for session, top in (("cx-a", wa), ("cx-b", wb)):
        raw = codex("SessionStart", session, top, source="startup")
        assert run_hook(hook_script, ["codex", "SessionStart"], raw, cwd=str(wa), env=env).returncode == 0
    (wb / "a.py").write_text(BROKEN, encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** End Patch"
    run_hook(hook_script, ["codex", "PostToolUse"], patch_event("PostToolUse", "cx-b", wb, patch), cwd=str(wa), env=env)
    c = cmain.Coordinator(state)
    try:
        settle(c)
        assert {sid: (s["agent"], os.path.normcase(s["worktree"])) for sid, s in c.sessions.items()} == {
            "cx-a": ("codex", os.path.normcase(str(wa))), "cx-b": ("codex", os.path.normcase(str(wb)))}
        assert c.record.changes("cx-b")[0] == {"a.py": "M"} and c.record.changes("cx-a")[0] == {}
        assert [s["agent"] for r in status.collect(state)["repositories"] for s in r["sessions"]] == ["codex", "codex"]
    finally:
        c.close()
