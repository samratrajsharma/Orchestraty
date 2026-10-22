"""orch events: the record's changes in the order it learned of them, read-only, once or
followed."""
import io
import json
import time

import pytest

from helpers import spool_event
from orchestraty import cli, config, declarations, events
from orchestraty.capture.hook import take_slot
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.paths import State

ORIGINAL = "def charge(amount, currency):\n    return amount\n"
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


def edit(state, c, sid, top, rel, text):
    path = top / rel
    path.write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(path))
    settle(c)


@pytest.fixture
def scenario(state, repo, git, tmp_path):
    """Two sessions start; A breaks charge, B writes a call to it and takes its note; A declares
    and then puts charge back; B ends, leaving its change, which is then followed as plain git.
    Returns the coordinator and the time B ended."""
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    for sid, top in (("A", wa), ("B", wb)):
        spool_event(state.root, sid, "SessionStart", top)
        spool_event(state.root, sid, "UserPromptSubmit", top)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    assert take_slot(slots.next_slot(state, "B"))
    c.step()
    declarations.append(state, "A", ["a.py"], ["a.py::charge"], "words of A's own")
    c.step()
    edit(state, c, "A", wa, "a.py", ORIGINAL)
    time.sleep(0.05)
    ended = time.time()
    time.sleep(0.05)
    spool_event(state.root, "B", "SessionEnd", wb, reason="other")
    c.step()
    yield c, ended
    c.close()


def printed(capsys, *args):
    assert cli.main(["events"] + list(args)) == 0
    return capsys.readouterr().out.splitlines()


def test_orch_events_once_lists_the_scenarios_events_in_order(state, scenario, capsys):
    lines = printed(capsys, "--once", "--json")
    got = [json.loads(line) for line in lines]
    assert all(e["schema"] == events.EVENTS_SCHEMA for e in got)
    assert [e["id"] for e in got] == sorted(e["id"] for e in got) and len({e["id"] for e in got}) == len(got)
    # The scenario's steps, in order; within one coordinator pass the order is the record's own.
    c_py = "O3|a.py::charge|A|B|c.py"

    def step(e):
        kind = e["event"]
        if kind in ("finding_opened", "note_written"):
            return 2 if e["finding"] == c_py else 1
        if kind == "session_started":
            return 0 if e["session"] in ("A", "B") else 7
        return {"note_delivered": 3, "declared": 4, "finding_resolved": 5, "note_withdrawn": 5, "session_ended": 6}[kind]
    steps = [step(e) for e in got]
    assert steps == sorted(steps) and set(steps) == set(range(8)), [(e["event"], e.get("finding")) for e in got]
    assert {e["event"] for e in got} == set(events.KINDS) - {"claimed", "claim_ended"}     # claims: test_claims.py
    assert sorted(e["session"] for e in got if e["event"] == "session_started")[:2] == ["A", "B"]
    assert (got[-1]["event"], got[-1]["agent"]) == ("session_started", "git") and got[-1]["worktree"].endswith("wt-b")
    assert {e["rule"] for e in got if e["event"] == "finding_opened"} == {"O3", "O6"}
    started = next(e for e in got if e["event"] == "session_started" and e["session"] == "A")
    assert (started["agent"], started["branch"]) == ("claude", "feat-a") and started["worktree"].endswith("wt-a")
    opened = next(e for e in got if e["event"] == "finding_opened" and e["rule"] == "O3" and e["file"] == "c.py")
    assert (opened["strength"], opened["symbol"], opened["changer"], opened["receiver"]) == ("strong", "a.py::charge",
                                                                                            "A", "B")
    delivered = next(e for e in got if e["event"] == "note_delivered")
    assert delivered["session"] == "B" and delivered["note_kind"] == "next" and isinstance(delivered["note"], int)
    declared = next(e for e in got if e["event"] == "declared")
    assert declared["paths"] == ["a.py"] and declared["symbols"] == ["a.py::charge"]
    assert "words of A's own" not in "\n".join(lines)      # a declaration's task line is never an event
    assert next(e for e in got if e["event"] == "session_ended")["ended"] == "ended: other"
    # the record is only read
    assert printed(capsys, "--once", "--json") == lines


def test_the_text_form_and_since(state, scenario, capsys):
    c, ended = scenario
    text = printed(capsys)
    assert len(text) == len(printed(capsys, "--json"))
    kinds = [line.split("  ")[1].strip() for line in text]
    assert kinds[0] == "session started" and kinds[-2:] == ["session ended", "session started"]
    assert any("strong O3 a.py::charge, in c.py (A -> B)" in line for line in text)
    assert any(line.split("  ")[1].strip() == "note delivered" and " next note to B on O3|" in line for line in text)
    later = printed(capsys, "--json", "--since", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended + 5)))
    assert later == []
    since = [json.loads(line) for line in printed(capsys, "--json", "--since", f"{ended:.3f}")]
    assert [(e["event"], e["session"][:4]) for e in since] == [("session_ended", "B"), ("session_started", "git-")]
    assert len(printed(capsys, "--json", "--since", "1d")) == len(text)
    assert cli.main(["events", "--since", "last tuesday"]) == 2
    assert "not a time" in capsys.readouterr().err


def test_following_prints_the_changes_made_from_then_on(state, scenario, repo):
    c, _ = scenario
    out, polls = io.StringIO(), []

    def keep_going():
        polls.append(1)
        if len(polls) == 1:                                 # while it follows, A ends
            spool_event(state.root, "A", "SessionEnd", repo, reason="clear")
            c.step()
        return len(polls) < 3

    assert events.run(state, out, as_json=True, follow=True, keep_going=keep_going, sleep=lambda s: None) == 0
    got = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [(e["event"], e["session"]) for e in got] == [("session_ended", "A")]
    assert len(polls) == 3


def test_following_a_record_that_does_not_exist_yet_waits_for_it(state):
    out = io.StringIO()
    assert events.run(state, out, follow=True, keep_going=lambda: False, sleep=lambda s: None) == 0
    assert out.getvalue() == ""
    assert cli.main(["events", "--once"]) == 0


def test_parse_since():
    now = 1_800_000_000.0
    assert events.parse_since("90s", now) == now - 90 and events.parse_since("2h", now) == now - 7200
    assert events.parse_since("1700000000", now) == 1_700_000_000.0
    assert events.parse_since("2026-01-02T03:04:05Z") == 1767323045.0
    assert events.parse_since("2026-01-02") == time.mktime(time.strptime("2026-01-02", "%Y-%m-%d"))
    for bad in ("", "soon", "5w", "2026-13-01"):
        with pytest.raises(ValueError):
            events.parse_since(bad)
