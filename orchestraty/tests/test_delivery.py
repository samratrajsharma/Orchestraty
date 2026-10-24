"""Delivery end to end inside one coordinator: findings become notes in slots, the real hook
script takes them, the coordinator learns they were delivered; steer, wake and the push line."""
import json
import os
from pathlib import Path

import pytest

from helpers import run_hook, spool_event
from orchestraty import config
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.paths import State

BROKEN = "def charge(amount):\n    return amount\n"
USES = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def make_two(state, repo, git, tmp_path, **cfg):
    config.watch(state, str(repo / ".git"))
    if cfg:
        c = config.load(state)
        c.update(cfg)
        config.save(state, c)
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    spool_event(state.root, "A", "SessionStart", wa)
    spool_event(state.root, "B", "SessionStart", wb)
    spool_event(state.root, "A", "UserPromptSubmit", wa)
    spool_event(state.root, "B", "UserPromptSubmit", wb)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    return c, wa, wb


@pytest.fixture
def two(state, repo, git, tmp_path):
    c, wa, wb = make_two(state, repo, git, tmp_path)
    yield c, wa, wb
    c.close()


def settle(c):
    """Let the live map, the indexes and the rules catch up, as the coordinator's loop would."""
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def edit(state, c, sid, top, rel, text):
    path = top / rel
    path.write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(path))
    settle(c)


def read_slot(path):
    with open(path, "rb") as fh:
        return json.loads(fh.read())


def notes_of(c, sid):
    return {(n["kind"], n["finding_key"]): n for n in c.record.notes(session_id=sid)}


def test_a_strong_overlap_reaches_both_agents(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    edit_slot = slots.edit_slot(state, "B", str(wb / "c.py"))
    out = read_slot(edit_slot)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and "permissionDecision" not in out
    assert "changed the contract of charge in a.py: parameter currency removed" in out["additionalContext"]
    assert "c.py uses it: use (line 5)" in out["additionalContext"]
    told_a = read_slot(slots.next_slot(state, "A"))["hookSpecificOutput"]["additionalContext"]
    assert "which you changed (parameter currency removed), is used in c.py" in told_a
    assert "That session is told before it next edits that file." in told_a
    stop = read_slot(slots.stop_slot(state, "A"))["hookSpecificOutput"]
    assert stop["hookEventName"] == "Stop" and "pay in b.py (line 5)" in stop["additionalContext"]
    b = notes_of(c, "B")
    assert b[("edit", "O3|a.py::charge|A|B|c.py")]["state"] == "written"
    # B already changed c.py and may never edit it again: it also hears with its next tool result
    told_b = read_slot(slots.next_slot(state, "B"))["hookSpecificOutput"]
    assert told_b["hookEventName"] == "PostToolUse"
    assert "c.py uses it: use (line 5). Check these uses in the code you changed" in told_b["additionalContext"]


def test_a_session_hears_a_finding_once_whichever_note_it_takes_first(state, two):
    from orchestraty.capture.hook import take_slot
    c, wa, wb = two
    edit(state, c, "B", wb, "c.py", USES)                           # B's call is in before A's change
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit_slot = slots.edit_slot(state, "B", str(wb / "c.py"))
    assert slots.waiting(edit_slot) and slots.waiting(slots.next_slot(state, "B"))
    assert take_slot(slots.next_slot(state, "B"))                   # its next tool result
    c.step()
    b = notes_of(c, "B")
    assert b[("next", "O3|a.py::charge|A|B|c.py")]["state"] == "delivered"
    assert b[("edit", "O3|a.py::charge|A|B|c.py")]["state"] == "delivered"
    assert not slots.waiting(edit_slot)                             # not told again before its next edit


def test_the_note_comes_before_the_first_edit_of_a_file_that_uses_the_change(state, two, hook_script):
    # B has not touched b.py, which calls charge: its first edit of b.py gets the note anyway.
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    slot = slots.edit_slot(state, "B", str(wb / "b.py"))
    assert slots.waiting(slot)
    payload = json.dumps({"session_id": "B", "hook_event_name": "PreToolUse", "cwd": str(wb), "tool_name": "Edit",
                          "tool_input": {"file_path": str(wb / "b.py"), "old_string": "1", "new_string": "2"},
                          "tool_use_id": "toolu_1"}).encode()
    note = json.loads(run_hook(hook_script, ["claude", "PreToolUse"], payload).stdout)
    assert "changed the contract of charge in a.py" in note["hookSpecificOutput"]["additionalContext"]
    assert "b.py uses it: pay (line 5)" in note["hookSpecificOutput"]["additionalContext"]
    # A latent finding tells nobody else: A hears about B only once B changes a file that uses it.
    assert ("next", "O3|a.py::charge|A|B|b.py") not in notes_of(c, "A")
    assert not slots.waiting(slots.next_slot(state, "A"))
    (wb / "b.py").write_text("from a import charge\n\n\ndef pay():\n    return charge(2, 'EUR')\n", encoding="utf-8")
    spool_event(state.root, "B", "PostToolUse", wb, tool_name="Edit", file_path=str(wb / "b.py"))
    settle(c)
    assert ("next", "O3|a.py::charge|A|B|b.py") in notes_of(c, "A")              # now real: A is told
    assert notes_of(c, "B")[("edit", "O3|a.py::charge|A|B|b.py")]["state"] == "delivered"
    assert not slots.waiting(slot)                                                # B is not told twice
    assert ("next", "O3|a.py::charge|A|B|b.py") not in notes_of(c, "B")           # not even with a result


def test_the_hook_takes_the_note_and_the_coordinator_counts_it_delivered(state, two, hook_script):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    payload = json.dumps({"session_id": "B", "hook_event_name": "PreToolUse", "cwd": str(wb), "tool_name": "Edit",
                          "tool_input": {"file_path": str(wb / "c.py"), "old_string": "2", "new_string": "3"},
                          "tool_use_id": "toolu_9"}).encode()
    proc = run_hook(hook_script, ["claude", "PreToolUse"], payload)
    assert "changed the contract of charge" in json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    c.step()
    note = notes_of(c, "B")[("edit", "O3|a.py::charge|A|B|c.py")]
    assert note["state"] == "delivered" and note["delivered"] >= note["written"] >= note["cause_ts"]
    assert run_hook(hook_script, ["claude", "PreToolUse"], payload).stdout == b""   # once per finding


def test_a_slot_taken_without_a_spool_line_still_counts_as_delivered(state, two):
    # The hook's spool line confirms a delivery; a slot gone from disk proves it even if that
    # line never arrives (a hook killed after printing, a lost spool file).
    from orchestraty.capture.hook import take_slot
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    assert take_slot(slots.next_slot(state, "A"))
    c.step()
    assert notes_of(c, "A")[("next", "O3|a.py::charge|A|B|c.py")]["state"] == "delivered"


def test_time_to_awareness_is_recorded(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    note = notes_of(c, "B")[("edit", "O3|a.py::charge|A|B|c.py")]
    assert 0 <= note["written"] - note["cause_ts"] < 30 * 10**9


def test_a_note_withdrawn_before_it_was_seen_comes_back_with_its_finding(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    slot = slots.edit_slot(state, "B", str(wb / "c.py"))
    assert slots.waiting(slot)
    os.remove(wb / "c.py")
    settle(c)
    assert not slots.waiting(slot)
    assert notes_of(c, "B")[("edit", "O3|a.py::charge|A|B|c.py")]["state"] == "withdrawn"
    edit(state, c, "B", wb, "c.py", USES)                            # the use is back
    assert slots.waiting(slot)


def test_a_delivered_note_is_not_repeated_when_its_finding_comes_back(state, two, hook_script):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    post = json.dumps({"session_id": "A", "hook_event_name": "PostToolUse", "tool_name": "Read"}).encode()
    assert run_hook(hook_script, ["claude", "PostToolUse"], post).stdout          # A hears who uses charge
    c.step()
    os.remove(wb / "c.py")
    settle(c)
    edit(state, c, "B", wb, "c.py", USES)
    assert not slots.waiting(slots.next_slot(state, "A"))
    assert notes_of(c, "A")[("next", "O3|a.py::charge|A|B|c.py")]["state"] == "delivered"


def test_steer_stops_the_edit_with_the_reason(state, repo, git, tmp_path):
    c, wa, wb = make_two(state, repo, git, tmp_path, steer=True)
    try:
        edit(state, c, "A", wa, "a.py", BROKEN)
        edit(state, c, "B", wb, "c.py", USES)
        out = read_slot(slots.edit_slot(state, "B", str(wb / "c.py")))["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "changed the contract of charge" in out["permissionDecisionReason"]
    finally:
        c.close()


def test_wake_only_for_a_session_waiting_for_its_user(state, repo, git, tmp_path):
    c, wa, wb = make_two(state, repo, git, tmp_path, wake=True)
    try:
        write = wb / "c.py"
        write.write_text(USES, encoding="utf-8")
        spool_event(state.root, "B", "PostToolUse", wb, tool_name="Write", file_path=str(write))
        spool_event(state.root, "B", "Stop", wb, stop_hook_active=False)          # B now waits for its user
        settle(c)
        edit(state, c, "A", wa, "a.py", BROKEN)
        wake = slots.wake_slot(state, "B")
        assert slots.waiting(wake)
        assert "changed the contract of charge" in Path(wake).read_text(encoding="utf-8")
        assert not slots.waiting(slots.wake_slot(state, "A"))                    # A is working
    finally:
        c.close()


def test_no_wake_for_a_session_that_is_working(state, repo, git, tmp_path):
    c, wa, wb = make_two(state, repo, git, tmp_path, wake=True)
    try:
        (wb / "c.py").write_text(USES, encoding="utf-8")
        spool_event(state.root, "B", "PostToolUse", wb, tool_name="Write", file_path=str(wb / "c.py"))
        settle(c)                                                   # B is mid-turn: it will see the edit note
        edit(state, c, "A", wa, "a.py", BROKEN)
        assert slots.waiting(slots.edit_slot(state, "B", str(wb / "c.py")))
        assert not slots.waiting(slots.wake_slot(state, "B"))
        assert ("wake", "O3|a.py::charge|A|B|c.py") not in notes_of(c, "B")
    finally:
        c.close()


def test_a_taken_wake_note_counts_for_the_edit_note_too(state, repo, git, tmp_path):
    from orchestraty.capture.hook import take_slot
    c, wa, wb = make_two(state, repo, git, tmp_path, wake=True)
    try:
        (wb / "c.py").write_text(USES, encoding="utf-8")
        spool_event(state.root, "B", "PostToolUse", wb, tool_name="Write", file_path=str(wb / "c.py"))
        spool_event(state.root, "B", "Stop", wb, stop_hook_active=False)
        settle(c)
        edit(state, c, "A", wa, "a.py", BROKEN)
        assert take_slot(slots.wake_slot(state, "B"))                            # the waiter took it
        spool_event(state.root, "B", "Wake", wb, took="wake")
        c.step()
        b = notes_of(c, "B")
        assert b[("wake", "O3|a.py::charge|A|B|c.py")]["state"] == "delivered"
        assert b[("edit", "O3|a.py::charge|A|B|c.py")]["state"] == "delivered"
        assert not slots.waiting(slots.edit_slot(state, "B", str(wb / "c.py")))  # not told twice
    finally:
        c.close()


def test_the_push_line_follows_callers_left_behind(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    push = Path(state.root) / "push" / (slots.file_key(str(wa)) + ".txt")
    assert push.read_text(encoding="utf-8").startswith("orchestraty: 1 caller(s) of charge still use its old form")
    edit(state, c, "A", wa, "b.py", "from a import charge\n\n\ndef pay():\n    return charge(1)\n")
    assert not push.exists()


LEFT = "from a import charge\n\n\ndef bill():\n    return charge(3, 'EUR')\n"
UPDATED = "from a import charge\n\n\ndef pay():\n    return charge(1)\n"


def two_callers(state, repo, git, tmp_path):
    (repo / "d.py").write_text(LEFT, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a second caller")
    return make_two(state, repo, git, tmp_path)


def test_a_waiting_note_follows_its_finding(state, repo, git, tmp_path):
    # A's note about callers left behind waits for the end of its turn; meanwhile B updates
    # one of them. The note A takes names only the caller still left behind.
    c, wa, wb = two_callers(state, repo, git, tmp_path)
    try:
        edit(state, c, "A", wa, "a.py", BROKEN)
        stop = slots.stop_slot(state, "A")
        before = read_slot(stop)["hookSpecificOutput"]["additionalContext"]
        assert "b.py" in before and "d.py" in before
        edit(state, c, "B", wb, "b.py", UPDATED)
        after = read_slot(stop)["hookSpecificOutput"]["additionalContext"]
        assert "d.py" in after and "b.py" not in after
        [note] = [n for n in c.record.notes(session_id="A") if n["kind"] == "stop"]
        assert note["state"] == "written" and note["text"] == after
    finally:
        c.close()


def test_a_note_already_taken_is_not_given_again_when_its_finding_changes(state, repo, git, tmp_path):
    from orchestraty.capture.hook import take_slot
    c, wa, wb = two_callers(state, repo, git, tmp_path)
    try:
        edit(state, c, "A", wa, "a.py", BROKEN)
        assert take_slot(slots.stop_slot(state, "A"))
        c.step()
        edit(state, c, "B", wb, "b.py", UPDATED)
        assert not slots.waiting(slots.stop_slot(state, "A"))
        [note] = [n for n in c.record.notes(session_id="A") if n["kind"] == "stop"]
        assert note["state"] == "delivered" and "b.py" in note["text"]
    finally:
        c.close()


def test_a_finished_sessions_change_keeps_its_push_line(state, two):
    # People push after the agent has gone: the worktree it left is followed as plain git.
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    push = Path(state.root) / "push" / (slots.file_key(str(wa)) + ".txt")
    assert push.exists()
    spool_event(state.root, "A", "SessionEnd", wa, reason="other")
    settle(c)
    assert push.read_text(encoding="utf-8").startswith("orchestraty: 1 caller(s) of charge still use its old form")
    [plain] = [s for s in c.record.sessions(states=("live",)) if s["agent"] == "git"]
    assert os.path.normcase(plain["worktree"]) == os.path.normcase(str(wa))
    (wa / "a.py").write_text("def charge(amount, currency):\n    return amount\n", encoding="utf-8")   # reverted
    c._last_scan = None
    settle(c)
    assert not push.exists()


def test_the_last_session_to_finish_keeps_its_push_line(state, two):
    # No session is left to run the timer: the end itself must look at the worktree.
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    spool_event(state.root, "B", "SessionEnd", wb, reason="other")
    c.step()
    spool_event(state.root, "A", "SessionEnd", wa, reason="other")
    c.step()
    c.livemap.indexes.wait(120)
    c.step()
    push = Path(state.root) / "push" / (slots.file_key(str(wa)) + ".txt")
    assert push.exists() and "caller(s) of charge" in push.read_text(encoding="utf-8")


def test_a_note_already_delivered_is_not_repeated_by_the_session_that_took_over(state, two):
    from orchestraty.capture.hook import take_slot
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    slot = slots.edit_slot(state, "B", str(wb / "c.py"))
    assert take_slot(slot)                                        # B has seen it
    c.step()
    spool_event(state.root, "A", "SessionEnd", wa, reason="other")
    settle(c)
    assert not slots.waiting(slot)
    told = [n for n in c.record.notes(session_id="B") if n["kind"] == "edit" and n["finding_key"].endswith("|B|c.py")]
    assert [n["state"] for n in told] == ["delivered"]           # once, from A; nothing from its successor


def test_a_note_never_seen_is_still_given_by_the_session_that_took_over(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    spool_event(state.root, "A", "SessionEnd", wa, reason="other")
    settle(c)
    slot = slots.edit_slot(state, "B", str(wb / "c.py"))
    assert slots.waiting(slot)
    text = read_slot(slot)["hookSpecificOutput"]["additionalContext"]
    assert "changes made outside any agent session (worktree wt-a, branch feat-a)" in text


def test_an_ended_session_takes_its_notes_back(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    assert slots.waiting(slots.edit_slot(state, "B", str(wb / "c.py")))
    spool_event(state.root, "B", "SessionEnd", wb, reason="other")
    c.step()
    assert not os.path.exists(slots.folder(state, "B"))
    assert {n["state"] for n in c.record.notes(session_id="B")} == {"withdrawn"}


def test_sessions_without_hooks_get_no_notes(state, repo, git, tmp_path):
    config.watch(state, str(repo / ".git"))
    wa, wc = tmp_path / "wt-a", tmp_path / "wt-c"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-c", str(wc))
    (wc / "c.py").write_text(USES, encoding="utf-8")                # an agent with no hooks works here
    spool_event(state.root, "A", "SessionStart", wa)
    c = cmain.Coordinator(state)
    try:
        c.step()
        c.livemap.indexes.wait(120)
        edit(state, c, "A", wa, "a.py", BROKEN)
        receivers = {n["session_id"] for n in c.record.notes()}
        assert receivers == {"A"}                                   # A hears; the plain-git one cannot
        told = read_slot(slots.next_slot(state, "A"))["hookSpecificOutput"]["additionalContext"]
        assert "changes made outside any agent session (worktree wt-c, branch feat-c)" in told
        assert "nobody there is told" in told and "That session is told" not in told
    finally:
        c.close()
