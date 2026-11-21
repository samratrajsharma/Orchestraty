"""Teammates' branches in the rules on this machine (coordinator wiring): a pair is not compared
when one's copy holds the other's commits (a local copy holding their tip, or their branch built
on local commits, or a local copy behind on the same branch); teammates' claims take part in O7
and never become claims here; and no claim or declaration attaches to a teammate's session."""
import time

import pytest

from helpers import spool_event
from orchestraty import claims, declarations
from orchestraty.coordinator.record import Record
from orchestraty.coordinator.team import session_id
from team_harness import BILLING, NS, run_git, team  # noqa: F401  (fixtures)

pytestmark = pytest.mark.heavy        # git-heavy: the quick Windows run in CI leaves it to the full run

TAX = BILLING.replace("currency)", "currency, tax)")
FEE = BILLING.replace("currency)", "currency, fee)")
CLAIMS = "version: 1\nsync: on\nvisibility: private\nshare:\n  claims: true\n"


def common(machine):
    return str(machine.clone / ".git")


def sid_of(machine, branch):
    return session_id(common(machine), "origin", branch)


def from_branch(machine, sid, branch, start):
    """A session in a new worktree on `branch`, made from `start` (such as origin/feat/x)."""
    run_git(machine.clone, "fetch", "-q", "origin")
    top = machine.folder / f"wt-{sid}"
    run_git(machine.clone, "worktree", "add", "-q", "-b", branch, str(top), start)
    machine.worktrees[sid], machine.agents[sid] = top, "claude"
    spool_event(machine.state.root, sid, "SessionStart", top, agent="claude", ts=machine.clock())
    machine.settle()
    return top


def between(machine, a, b):
    rec = Record(machine.state.record, readonly=True)
    try:
        return [f for f in rec.findings() if {a, b} <= set(f["sessions"])]
    finally:
        rec.close()


def team_row(machine, sid):
    rec = Record(machine.state.record, readonly=True)
    try:
        return rec.team_session(sid)
    finally:
        rec.close()


def read_again(team, machine, seconds=150):  # noqa: F811
    team.clock.advance(seconds)
    machine.settle()


def test_bob_continuing_alices_branch_is_not_compared_with_it(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    read_again(team, bob)
    from_branch(bob, "B1", "feat/x-more", "origin/feat/x")                         # bob goes on from alice's tip
    bob.edit("B1", "pkg/billing.py", TAX.replace("return amount", "return amount * 2"))
    read_again(team, bob)
    t = sid_of(bob, "feat/x")
    assert team_row(bob, t)["contained_in"] == ["B1"]
    assert between(bob, "B1", t) == []


def test_alice_stacked_on_bob_is_not_compared(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    bob.push_work("B1", "feat/b", "pkg/billing.py", TAX)                          # bob's change, pushed
    from_branch(alice, "A1", "feat/a", "origin/feat/b")                            # alice builds on it
    alice.edit("A1", "pkg/refunds.py", "from pkg.billing import charge\n\n\ndef refund(amount):\n"
                                       "    return charge(-amount, 'EUR', 0)\n")
    alice.commit("A1", "refunds, with tax")
    alice.push("A1", "-u")
    alice.settle()
    team.clock.advance(3)
    alice.settle()
    read_again(team, bob)
    t = sid_of(bob, "feat/a")
    assert team_row(bob, t)["builds_on"] == ["B1"]
    assert between(bob, "B1", t) == []                                             # not bob's charge, as hers


def test_bob_behind_on_the_same_branch_is_not_compared(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    from_branch(bob, "B1", "feat/x-mine", "origin/feat/x")
    alice.edit("A1", "pkg/billing.py", FEE)                                       # alice goes on
    alice.commit("A1", "fee")
    alice.push("A1")
    alice.settle()
    team.clock.advance(3)
    alice.settle()
    bob.edit("B1", "pkg/billing.py", TAX.replace("return amount", "return amount + 1"))
    read_again(team, bob)
    t = sid_of(bob, "feat/x")
    assert team_row(bob, t)["builds_on"] == ["B1"]
    assert between(bob, "B1", t) == []


def test_teammate_claim_enters_o7_and_never_claims_locally(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    (alice.clone / ".orchestraty" / "policy.yml").write_text(CLAIMS, encoding="utf-8")
    run_git(alice.clone, "commit", "-q", "-a", "-m", "share claims")
    run_git(alice.clone, "push", "-q", "origin", "HEAD:main")
    alice.team_on()
    bob.team_on()
    alice.start("A1", "feat/x")
    alice.settle()
    claims.append_claim(alice.state, "A1", common(alice), "area", "pkg", "", True, 120, now=team.clock() / NS)
    alice.settle()
    alice.edit("A1", "pkg/billing.py", TAX)
    alice.commit("A1", "tax")
    alice.push("A1", "-u")
    alice.settle()
    team.clock.advance(3)
    alice.settle()
    bob.start("B1", "feat/refunds")
    bob.settle()
    declarations.append(bob.state, "B1", ["pkg/refunds.py"], [], "", now=team.clock() / NS)
    read_again(team, bob)
    t = sid_of(bob, "feat/x")
    rec = Record(bob.state.record, readonly=True)
    try:
        planned = rec.team_planned(session_id=t)
        local = rec.claims()
        (o7,) = [f for f in rec.findings() if f["rule"] == "O7"]
    finally:
        rec.close()
    assert [(p["kind"], p["target"], p["hard"]) for p in planned] == [("area", "pkg", 1)]   # kept as shared
    assert local == []                                                             # never a claim here
    assert o7["key"] == f"O7|pkg/refunds.py|B1|{t}" and o7["detail"]["how"] == {"B1": "declared", t: "claimed"}


def test_claims_and_declarations_naming_a_team_id_are_dropped(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    read_again(team, bob)
    t = sid_of(bob, "feat/x")
    claims.append_claim(bob.state, t, common(bob), "file", "pkg/billing.py", "", False, 30, now=team.clock() / NS)
    declarations.append(bob.state, t, ["pkg/billing.py"], [], "", now=team.clock() / NS)
    bob.settle()
    rec = Record(bob.state.record, readonly=True)
    try:
        assert rec.claims() == [] and rec.declarations() == []
    finally:
        rec.close()
    assert time
