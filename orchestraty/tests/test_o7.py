"""Rule O7: two sessions whose declared (the MCP tool declare) or claimed (orch claim) work
overlaps get a medium finding and one note each, with their next step; nothing is held back."""
import json
import time

import pytest

from helpers import ag, run_hook, spool_event
from orchestraty import claims, config, declarations, status
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.paths import State
from orchestraty.rules.overlap import declared_work

NS = 1_000_000_000


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def start(state, repo, git, tmp_path, agents=(("A", "claude"), ("B", "claude")), **cfg):
    config.watch(state, str(repo / ".git"))
    settings = config.load(state)
    settings.update(cfg)
    config.save(state, settings)
    tops = {}
    for sid, agent in agents:
        tops[sid] = tmp_path / f"wt-{sid.lower()}"
        git(repo, "worktree", "add", "-q", "-b", f"feat-{sid.lower()}", str(tops[sid]))
        spool_event(state.root, sid, "SessionStart", tops[sid], agent=agent)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    return c, tops


def item(path, symbol="", how="declared"):
    return {"path": path, "symbol": symbol, "how": how}


def test_which_planned_items_overlap():
    def met(x, y):
        return [(f.key, f.symbol, f.file, f.detail) for f in declared_work("A", "B", {"A": [x], "B": [y]})]

    plain = {"what": "a.py", "kind": "path", "how": {"A": "declared", "B": "declared"}}
    assert met(item("a.py"), item("a.py")) == [("O7|a.py|A|B", "", "a.py", plain)]
    assert met(item("a.py", "a.py::charge"), item("a.py", how="claimed"))[0][:3] == (
        "O7|a.py::charge|A|B", "a.py::charge", "a.py")                         # the narrower item
    assert met(item("pkg", how="claimed"), item("pkg/sub/x.py"))[0][3] == {
        "what": "pkg/sub/x.py", "kind": "path", "how": {"A": "claimed", "B": "declared"}}
    assert met(item("pkg"), item("pkg/sub"))[0][0] == "O7|pkg/sub|A|B"
    assert met(item("pkg", how="claimed"), item("pkg/x.py", "pkg/x.py::f"))[0][1] == "pkg/x.py::f"
    assert met(item("a.py", "a.py::charge"), item("a.py", "a.py::refund")) == []    # two functions of one file
    assert met(item("pkg"), item("pkg2/x.py")) == [] and met(item("a.py"), item("b.py")) == []
    both = declared_work("A", "B", {"A": [item("a.py"), item("a.py", how="claimed")], "B": [item("a.py")]})
    assert [f.detail["how"] for f in both] == [{"A": "claimed", "B": "declared"}]   # one finding, claimed wins


def test_two_sessions_with_overlapping_declarations_each_hear_once_and_nothing_is_held_back(
        state, repo, git, tmp_path, hook_script):
    c, tops = start(state, repo, git, tmp_path, steer=True)
    try:
        declarations.append(state, "A", ["a.py"], ["a.py::charge"], "words of A's own")
        declarations.append(state, "B", ["a.py", "b.py"], [], "words of B's own")
        settle(c)
        [f] = [f for f in c.record.findings() if f["rule"] == "O7"]
        assert (f["key"], f["strength"], f["symbol"], f["file"]) == ("O7|a.py::charge|A|B", "medium", "a.py::charge",
                                                                      "a.py")
        assert f["detail"]["how"] == {"A": "declared", "B": "declared"}
        texts = {}
        for sid in ("A", "B"):
            with open(slots.next_slot(state, sid), "rb") as fh:
                out = json.loads(fh.read())["hookSpecificOutput"]
            assert out["hookEventName"] == "PostToolUse" and "permissionDecision" not in out
            texts[sid] = out["additionalContext"]
        assert texts["A"].startswith("Orchestraty: another Claude Code session (worktree wt-b, branch feat-b) has "
                                     "declared charge in a.py, which you have declared too (rule O7).")
        assert "(worktree wt-a, branch feat-a) has declared charge in a.py" in texts["B"]
        assert all("nothing is held back" in t and "words of" not in t for t in texts.values())
        assert not any(slots.waiting(slots.edit_slot(state, sid, str(tops[sid] / "a.py"))) for sid in "AB")
        payload = json.dumps({"session_id": "B", "cwd": str(tops["B"]), "hook_event_name": "PostToolUse",
                              "tool_name": "Read", "tool_input": {}, "tool_use_id": "toolu_1"}).encode()
        assert b"rule O7" in run_hook(hook_script, ["claude", "PostToolUse"], payload).stdout
        settle(c)
        assert [(n["session_id"], n["state"]) for n in c.record.notes(finding_key=f["key"])] == [
            ("A", "written"), ("B", "delivered")]
        assert run_hook(hook_script, ["claude", "PostToolUse"], payload).stdout == b""     # one note each
        line = next(x for x in status.render(status.collect(state)).splitlines() if " O7 " in x)
        assert line.strip() == "medium  O7  a.py::charge: declared by A, declared by B"
    finally:
        c.close()


def test_claims_count_and_o7_ends_with_what_it_compared(state, repo, git, tmp_path):
    c, tops = start(state, repo, git, tmp_path, agents=(("A", "claude"), ("B", "codex"), ("C", "antigravity")),
                    declare_minutes=1)
    try:
        claims.append_claim(state, "A", str(repo / ".git"), "area", "pkg", "", False, 60)
        declarations.append(state, "B", ["pkg/x.py", "a.py"], ["a.py::refund"])
        declarations.append(state, "C", ["b.py"], [])
        settle(c)
        [f] = [f for f in c.record.findings() if f["rule"] == "O7"]
        assert (f["key"], f["detail"]["how"]) == ("O7|pkg/x.py|A|B", {"A": "claimed", "B": "declared"})
        with open(slots.next_slot(state, "B"), "rb") as fh:
            told = json.loads(fh.read())["hookSpecificOutput"]["additionalContext"]
        assert "has claimed pkg/x.py, which you have declared too (rule O7)" in told
        assert c.record.notes(session_id="C") == []                              # C overlaps with nobody yet
        declarations.append(state, "C", ["pkg"], [])
        settle(c)
        keys = sorted(f["key"] for f in c.record.findings() if f["rule"] == "O7")
        assert keys == ["O7|pkg/x.py|A|B", "O7|pkg/x.py|B|C", "O7|pkg|A|C"]
        with open(slots.next_slot(state, "C"), "rb") as fh:
            message = json.loads(fh.read())["injectSteps"][0]["ephemeralMessage"]
        assert "rule O7" in message

        # the declarations stop holding (declare_minutes: 1): the findings resolve, and their
        # notes not yet taken come back out; A's claim alone overlaps with nothing
        c.now_ns = lambda: time.time_ns() + 90 * NS
        c.deliverer.now_ns = c.now_ns
        c._last_housekeeping = 0
        settle(c)
        assert [f for f in c.record.findings() if f["rule"] == "O7"] == []
        assert {n["state"] for n in c.record.notes(finding_key="O7|pkg/x.py|A|B")} == {"withdrawn"}
    finally:
        c.close()


def test_antigravity_hears_o7_with_its_next_step(state, repo, git, tmp_path, hook_script):
    c, tops = start(state, repo, git, tmp_path, agents=(("A", "claude"), ("B", "antigravity")))
    try:
        claims.append_claim(state, "A", str(repo / ".git"), "file", "a.py", "", True, 60)
        declarations.append(state, "B", ["a.py"], [])
        settle(c)
        proc = run_hook(hook_script, ["antigravity", "PreInvocation"], ag("PreInvocation", "B", [tops["B"]]))
        message = json.loads(proc.stdout)["injectSteps"][0]["ephemeralMessage"]
        assert "has claimed a.py, which you have declared too (rule O7)" in message
    finally:
        c.close()
