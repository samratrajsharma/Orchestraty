"""Merge-order advice: worked out from the findings, the same way every time, one-way, mutual or
nothing at all; in orch status and orch merge-order."""
import pytest

from helpers import spool_event
from orchestraty import cli, config, mergeorder, status
from orchestraty.coordinator import main as cmain
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


def edit(state, c, sid, top, rel, text):
    (top / rel).write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(top / rel))
    settle(c)


@pytest.fixture
def two(state, repo, git, tmp_path):
    """A breaks charge in wt-a; B, in wt-b, writes c.py, which calls it the old way."""
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    for sid, top in (("A", wa), ("B", wb)):
        spool_event(state.root, sid, "SessionStart", top)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    yield c, wa, wb
    c.close()


def finding(rule, changer, receiver, symbol, latent=False):
    return {"rule": rule, "changer": changer, "receiver": receiver, "symbol": symbol, "file": "x.py",
            "sessions": sorted((changer, receiver)), "detail": {"latent": latent}}


def test_merge_order_one_way_mutual_and_none():
    sessions = {"A": {"branch": "refunds"}, "B": {"branch": "annual"}, "C": {"branch": ""}}
    one_way = mergeorder.advise([finding("O3", "A", "B", "pay.py::charge")], sessions)
    assert one_way == [{"sessions": ["A", "B"], "first": "A", "symbols": ["pay.py::charge"],
                        "line": "merge refunds first: annual uses charge in pay.py, which refunds changed."}]
    removed = mergeorder.advise([finding("O4", "C", "A", "pay.py::old")], sessions)
    assert removed[0]["line"] == "merge session C first: refunds uses old in pay.py, which session C removed."
    mutual = mergeorder.advise([finding("O3", "A", "B", "pay.py::charge"), finding("O3", "B", "A", "bill.py::total")],
                               sessions)
    assert mutual == [{"sessions": ["A", "B"], "first": "", "symbols": ["bill.py::total", "pay.py::charge"],
                       "line": "refunds and annual depend on each other (charge in pay.py; total in bill.py): merge "
                               "them together, or coordinate."}]
    assert mergeorder.advise([], sessions) == []
    assert mergeorder.advise([finding("O3", "A", "B", "pay.py::charge", latent=True)], sessions) == []
    assert mergeorder.advise([dict(finding("O2", "", "", "pay.py::charge"))], sessions) == []


def test_merge_order_in_orch_status_and_orch_merge_order(state, two, capsys):
    c, wa, wb = two
    [advice] = status.collect(state)["repositories"][0]["merge_order"]
    assert advice["first"] == "A" and advice["line"] == ("merge feat-a first: feat-b uses charge in a.py, which feat-a "
                                                         "changed.")
    assert "  merge order: merge feat-a first: feat-b uses charge in a.py" in status.render(status.collect(state))
    assert cli.main(["merge-order"]) == 0
    assert "  merge feat-a first: feat-b uses charge in a.py, which feat-a changed." in capsys.readouterr().out


def test_nothing_to_say_about_merge_order(state, capsys):
    assert cli.main(["merge-order"]) == 0
    assert "Nothing to say about merge order" in capsys.readouterr().out
