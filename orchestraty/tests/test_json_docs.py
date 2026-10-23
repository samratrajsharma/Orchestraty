"""docs/status-json.md and docs/events-json.md name every field orch status --json and
orch events --json give, and status-json.md no field they do not."""
import io
import json
import re
import time
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import claims, config, declarations, events, status
from orchestraty.capture.hook import take_slot
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.paths import State

DOCS = Path(__file__).resolve().parents[1] / "docs"
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
    (top / rel).write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(top / rel))
    settle(c)


def tables(page):
    """{heading: [field]} from a docs page: the first cell of each table row that is a `name`."""
    out, heading = {}, None
    for line in (DOCS / page).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        m = re.match(r"^\| `([a-z_]+)` \|", line)
        if m:
            out.setdefault(heading, []).append(m.group(1))
    return out


def test_the_json_docs_name_every_field(state, repo, git, tmp_path):
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    for sid, top in (("A", wa), ("B", wb)):
        spool_event(state.root, sid, "SessionStart", top)
    c = cmain.Coordinator(state)
    try:
        c.step()
        assert c.livemap.indexes.wait(120)
        edit(state, c, "A", wa, "a.py", BROKEN)
        edit(state, c, "B", wb, "c.py", USES)
        declarations.append(state, "A", ["a.py"], ["a.py::charge"], "a line")
        claims.append_claim(state, "A", str(repo / ".git"), "symbol", "a.py::charge", "a.py", True, 30)
        declarations.append(state, "B", ["a.py"], [])                            # O7 with A's claim
        c.step()

        # orch status --json, with the coordinator running
        Path(state.alive).touch()
        Path(state.info).write_text(json.dumps({"pid": 1, "started": time.time(), "version": "0"}), encoding="utf-8")
        data = status.collect(state)
        doc = tables("status-json.md")
        assert set(data) == set(doc["The object"])
        assert set(data["coordinator"]) == set(doc["`coordinator`"])
        [repository] = data["repositories"]
        assert set(repository) == set(doc["Repository"])
        a = repository["sessions"][0]
        assert set(a) == set(doc["Session"]) and set(a["changes"]) == set(doc["`changes`"])
        assert set(a["changes"]["symbols"][0]) == set(doc["Symbol"]) and set(a["declared"][0]) == set(doc["`declared`"])
        assert set(a["claims"][0]) == set(doc["`claims`"])
        assert repository["overlaps"] and all(set(f) == set(doc["Overlap"]) for f in repository["overlaps"])
        assert "O7" in {f["rule"] for f in repository["overlaps"]}
        assert repository["merge_order"] and all(set(m) == set(doc["`merge_order`"]) for m in repository["merge_order"])
        assert set(repository["together"]) == set(doc["`together`"]) and repository["together"]["results"] == []
        assert set(repository["typescript"]) == set(doc["`typescript`"]) and repository["typescript"]["runs"] == []
        text = (DOCS / "status-json.md").read_text(encoding="utf-8")
        for f in repository["overlaps"]:
            assert f"| `{f['rule']}` " in text and all(f"`{k}`" in text for k in f["detail"])

        # orch events --json: everything that happens next, too
        assert take_slot(slots.next_slot(state, "B"))
        c.step()
        edit(state, c, "A", wa, "a.py", ORIGINAL)
        claims.append_release(state, str(repo / ".git"), target="a.py::charge")
        spool_event(state.root, "B", "SessionEnd", wb, reason="other")
        c.step()
    finally:
        c.close()
    out = io.StringIO()
    events.run(state, out, as_json=True)
    got = [json.loads(line) for line in out.getvalue().splitlines()]
    assert {e["event"] for e in got} == set(events.KINDS)
    page = (DOCS / "events-json.md").read_text(encoding="utf-8")
    assert set(tables("events-json.md")["Schema 1"]) >= {"schema", "id", "event", "time", "ts", "session"}
    for kind in events.KINDS:
        assert f"| `{kind}` |" in page
    for key in sorted({k for e in got for k in e}):
        assert f"`{key}`" in page, key
