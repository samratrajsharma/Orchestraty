"""Claims enforced through the pre-edit slots: another session hears of a claim once, before it
edits what the claim covers (a claimed function's file, any file in a claimed folder); a hard
claim makes a Claude Code edit wait for the user's answer and only informs agents that cannot
ask; a claim that ends takes back its notes. The real hook script takes the notes."""
import json
import os
import time
from pathlib import Path

import pytest

from helpers import ag, patch_event, run_hook, spool_event
from orchestraty import claims, cli, config, status
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.paths import State

BROKEN = "def charge(amount):\n    return amount\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def start(state, repo, git, tmp_path, others=(("B", "claude"),), **cfg):
    """A (Claude Code) in worktree wt-a, and each of `others` (session, agent) in a worktree of
    its own, wt-<session>."""
    config.watch(state, str(repo / ".git"))
    if cfg:
        settings = config.load(state)
        settings.update(cfg)
        config.save(state, settings)
    tops = {"A": tmp_path / "wt-a"}
    for sid, _ in others:
        tops[sid] = tmp_path / f"wt-{sid.lower()}"
    for sid, top in tops.items():
        git(repo, "worktree", "add", "-q", "-b", f"feat-{sid.lower()}", str(top))
    spool_event(state.root, "A", "SessionStart", tops["A"])
    for sid, agent in others:
        spool_event(state.root, sid, "SessionStart", tops[sid], agent=agent)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    return c, tops


def claim(state, c, target, *args, cwd):
    """orch claim, run in `cwd`, then the coordinator's passes."""
    here = os.getcwd()
    os.chdir(cwd)
    try:
        assert cli.main(["claim", target] + list(args)) == 0
    finally:
        os.chdir(here)
    settle(c)
    return c.record.claims(active_at=time.time_ns())[-1]


def pre_edit(hook_script, sid, top, path):
    """Claude Code's PreToolUse for an Edit of `path`, through the real hook script."""
    payload = json.dumps({"session_id": sid, "transcript_path": "/t/x.jsonl", "cwd": str(top),
                          "hook_event_name": "PreToolUse", "tool_name": "Edit",
                          "tool_input": {"file_path": str(path), "old_string": "1", "new_string": "2"},
                          "tool_use_id": "toolu_1"}).encode()
    proc = run_hook(hook_script, ["claude", "PreToolUse"], payload)
    assert proc.returncode == 0
    return proc.stdout


def until(cl):
    return time.strftime("%H:%M", time.localtime(cl["expires"] / 1e9))


def claim_notes(c, sid):
    return [n for n in c.record.notes(session_id=sid) if n["finding_key"].startswith("claim|")]


def test_b_hears_of_a_claim_once_before_it_edits_the_claimed_file(state, repo, git, tmp_path, hook_script):
    c, tops = start(state, repo, git, tmp_path)
    try:
        cl = claim(state, c, "a.py", cwd=tops["A"])
        assert claim_notes(c, "A") == []                                   # the claimer hears nothing of it
        slot = slots.edit_slot(state, "B", str(tops["B"] / "a.py"))
        with open(slot, "rb") as fh:
            waiting = fh.read()
        out = json.loads(waiting)["hookSpecificOutput"]
        assert out["hookEventName"] == "PreToolUse" and "permissionDecision" not in out
        text = out["additionalContext"]
        assert text.startswith("Orchestraty: another Claude Code session (worktree wt-a, branch feat-a) has claimed "
                               f"a.py until {until(cl)} (claim #{cl['id']}).")
        assert f"orch release {cl['id']}" in text
        assert pre_edit(hook_script, "B", tops["B"], tops["B"] / "b.py") == b""      # not covered
        assert pre_edit(hook_script, "B", tops["B"], tops["B"] / "a.py") == waiting  # the real hook hands it over
        settle(c)
        assert [(n["kind"], n["state"]) for n in claim_notes(c, "B")] == [("edit", "delivered")]
        assert pre_edit(hook_script, "B", tops["B"], tops["B"] / "a.py") == b""      # once per claim
        (tops["B"] / "a.py").write_text(BROKEN, encoding="utf-8")                    # B goes on and changes it
        spool_event(state.root, "B", "PostToolUse", tops["B"], tool_name="Edit", file_path=str(tops["B"] / "a.py"))
        settle(c)
        assert [(n["kind"], n["state"]) for n in claim_notes(c, "B")] == [("edit", "delivered")]
    finally:
        c.close()


def test_the_note_waits_across_a_coordinator_restart(state, repo, git, tmp_path, hook_script):
    c, tops = start(state, repo, git, tmp_path)
    cl = claim(state, c, "a.py", cwd=tops["A"])
    c.close()
    again = cmain.Coordinator(state)
    try:
        settle(again)
        assert "has claimed a.py" in pre_edit(hook_script, "B", tops["B"], tops["B"] / "a.py").decode()
        settle(again)
        assert [n["state"] for n in claim_notes(again, "B")] == ["delivered"]
        assert again.record.claim(cl["id"])["ended"] is None
    finally:
        again.close()


def test_a_hard_claim_asks_claude_codes_user_and_only_informs_codex(state, repo, git, tmp_path, hook_script):
    c, tops = start(state, repo, git, tmp_path, others=(("B", "claude"), ("C", "codex")))
    try:
        cl = claim(state, c, "a.py", "--hard", cwd=tops["A"])
        asked = pre_edit(hook_script, "B", tops["B"], tops["B"] / "a.py")
        out = json.loads(asked)["hookSpecificOutput"]
        assert out["permissionDecision"] == "ask"                           # the edit waits for the user's answer
        reason = out["permissionDecisionReason"]
        assert f"has claimed a.py until {until(cl)} (hard claim #{cl['id']})" in reason
        assert reason == out["additionalContext"]                           # the agent hears it too
        assert "This edit waits for the user's answer" in reason
        assert b"allow" not in asked and b"deny" not in asked                 # never allow, never a silent deny

        patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** End Patch"
        told = run_hook(hook_script, ["codex", "PreToolUse"], patch_event("PreToolUse", "C", tops["C"], patch)).stdout
        informed = json.loads(told)["hookSpecificOutput"]
        assert "permissionDecision" not in informed and b'"ask"' not in told  # the normal permission flow
        assert "cannot be made to wait for its user before an edit, so this note only informs" in informed[
            "additionalContext"]
        settle(c)
        assert [n["state"] for n in claim_notes(c, "B") + claim_notes(c, "C")] == ["delivered", "delivered"]
        text = status.render(status.collect(state))
        assert ("codex: a Codex hook cannot make an edit wait for the user's answer, so a hard claim only informs "
                "it.") in text
        assert "claude: " not in text
    finally:
        c.close()


def test_a_function_is_claimed_through_its_file_and_a_folder_through_any_file_in_it(state, repo, git, tmp_path,
                                                                                    hook_script):
    c, tops = start(state, repo, git, tmp_path)
    try:
        (tops["A"] / "pkg").mkdir()
        (tops["A"] / "pkg" / "x.py").write_text("X = 1\n", encoding="utf-8")
        fn = claim(state, c, "a.py::charge", cwd=tops["A"])
        area = claim(state, c, "pkg", cwd=tops["A"])
        assert (fn["kind"], fn["file"], area["kind"]) == ("symbol", "a.py", "area")
        note = pre_edit(hook_script, "B", tops["B"], tops["B"] / "a.py").decode()
        assert f"has claimed charge in a.py until {until(fn)} (claim #{fn['id']})" in note
        deep = tops["B"] / "pkg" / "deep" / "y.py"                          # B's own copy: no need for it to exist
        note = pre_edit(hook_script, "B", tops["B"], deep).decode()
        assert f"has claimed the folder pkg/ until {until(area)} (claim #{area['id']})" in note
        settle(c)
        lines = [json.loads(x) for x in (Path(state.root) / "spool" / "claude" / "B.jsonl").read_text(
            encoding="utf-8").splitlines()]
        assert [x.get("took") for x in lines if x["event"] == "PreToolUse"] == ["edit", "area"]
        assert [(n["kind"], n["state"]) for n in claim_notes(c, "B")] == [("edit", "delivered")] * 2
        assert pre_edit(hook_script, "B", tops["B"], tops["B"] / "pkg" / "z.py") == b""     # once per claim
    finally:
        c.close()


def test_an_edit_in_a_hard_claimed_folder_asks_before_any_other_note_there(state, repo, git, tmp_path, hook_script):
    c, tops = start(state, repo, git, tmp_path)
    try:
        (tops["A"] / "pkg").mkdir()
        (tops["A"] / "pkg" / "x.py").write_text("X = 1\n", encoding="utf-8")
        mine = claim(state, c, "pkg/x.py", cwd=tops["A"])                     # its own slot, soft
        area = claim(state, c, "pkg", "--hard", cwd=tops["A"])
        assert slots.waiting(slots.area_slot(state, "B", str(tops["B"] / "pkg"), ask=True))
        first = json.loads(pre_edit(hook_script, "B", tops["B"], tops["B"] / "pkg" / "x.py"))["hookSpecificOutput"]
        assert first["permissionDecision"] == "ask" and f"hard claim #{area['id']}" in first["permissionDecisionReason"]
        second = json.loads(pre_edit(hook_script, "B", tops["B"], tops["B"] / "pkg" / "x.py"))["hookSpecificOutput"]
        assert "permissionDecision" not in second and f"(claim #{mine['id']})" in second["additionalContext"]
        settle(c)
        assert [n["state"] for n in claim_notes(c, "B")] == ["delivered", "delivered"]
    finally:
        c.close()


def test_a_session_already_changing_the_claimed_file_hears_with_its_next_step_too(state, repo, git, tmp_path,
                                                                                hook_script):
    c, tops = start(state, repo, git, tmp_path)
    try:
        (tops["B"] / "a.py").write_text(BROKEN, encoding="utf-8")
        spool_event(state.root, "B", "PostToolUse", tops["B"], tool_name="Edit", file_path=str(tops["B"] / "a.py"))
        settle(c)
        cl = claim(state, c, "a.py", cwd=tops["A"])
        kinds = {n["kind"]: n for n in claim_notes(c, "B")}
        assert set(kinds) == {"edit", "next"}
        with open(slots.next_slot(state, "B"), "rb") as fh:
            nxt = json.loads(fh.read())["hookSpecificOutput"]["additionalContext"]
        assert f"has claimed a.py until {until(cl)}" in nxt and "You have changed it in your worktree" in nxt
        payload = json.dumps({"session_id": "B", "cwd": str(tops["B"]), "hook_event_name": "PostToolUse",
                              "tool_name": "Read", "tool_input": {}, "tool_use_id": "toolu_2"}).encode()
        assert b"has claimed a.py" in run_hook(hook_script, ["claude", "PostToolUse"], payload).stdout
        settle(c)
        assert {n["state"] for n in claim_notes(c, "B")} == {"delivered"}         # whichever first: heard once
        assert pre_edit(hook_script, "B", tops["B"], tops["B"] / "a.py") == b""
    finally:
        c.close()


def test_antigravity_is_told_with_its_next_step_once_it_has_changed_what_is_claimed(state, repo, git, tmp_path,
                                                                                   hook_script):
    c, tops = start(state, repo, git, tmp_path, others=(("B", "antigravity"),))
    try:
        cl = claim(state, c, "a.py", "--hard", cwd=tops["A"])
        assert claim_notes(c, "B") == []                                    # it takes no note before an edit
        (tops["B"] / "a.py").write_text(BROKEN, encoding="utf-8")
        spool_event(state.root, "B", "PostToolUse", tops["B"], agent="antigravity", tool_name="write_to_file",
                    file_path=str(tops["B"] / "a.py"))
        settle(c)
        assert [n["kind"] for n in claim_notes(c, "B")] == ["next"]
        proc = run_hook(hook_script, ["antigravity", "PreInvocation"], ag("PreInvocation", "B", [tops["B"]]))
        message = json.loads(proc.stdout)["injectSteps"][0]["ephemeralMessage"]
        assert f"has claimed a.py until {until(cl)} (hard claim #{cl['id']})" in message
        text = status.render(status.collect(state))
        assert "antigravity: no Antigravity hook can make an edit wait for the user's answer" in text
    finally:
        c.close()


def test_a_claim_that_ends_takes_back_its_notes(state, repo, git, tmp_path, hook_script, monkeypatch):
    c, tops = start(state, repo, git, tmp_path)
    try:
        cl = claim(state, c, "a.py", cwd=tops["A"])
        slot = slots.edit_slot(state, "B", str(tops["B"] / "a.py"))
        assert slots.waiting(slot)
        monkeypatch.chdir(tops["A"])
        assert cli.main(["release", str(cl["id"])]) == 0
        settle(c)
        assert not slots.waiting(slot) and [n["state"] for n in claim_notes(c, "B")] == ["withdrawn"]
        assert pre_edit(hook_script, "B", tops["B"], tops["B"] / "a.py") == b""
        claims.append_claim(state, "A", str(repo / ".git"), "file", "b.py", "", False, 1)      # one minute
        settle(c)
        assert slots.waiting(slots.edit_slot(state, "B", str(tops["B"] / "b.py")))
        c.now_ns = lambda: time.time_ns() + 120 * 1_000_000_000                  # its minute has run out
        c.deliverer.now_ns = c.now_ns
        c._last_housekeeping = 0
        settle(c)
        assert not slots.waiting(slots.edit_slot(state, "B", str(tops["B"] / "b.py")))
        assert [n["state"] for n in claim_notes(c, "B")] == ["withdrawn", "withdrawn"]
    finally:
        c.close()


def test_steer_never_turns_a_claim_note_into_a_deny(state, repo, git, tmp_path, hook_script):
    c, tops = start(state, repo, git, tmp_path, steer=True)
    try:
        claim(state, c, "a.py", cwd=tops["A"])
        out = json.loads(pre_edit(hook_script, "B", tops["B"], tops["B"] / "a.py"))["hookSpecificOutput"]
        assert "permissionDecision" not in out and "has claimed a.py" in out["additionalContext"]
    finally:
        c.close()
