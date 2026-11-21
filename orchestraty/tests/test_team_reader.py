"""Teammates' records, read: the team fetch brings every writer's ref and the branches records name
into the shadow (at the coordinator's start, every team_fetch_minutes and right after its own
push), and each record goes through the incoming gate into team_writers. This device's own
record is skipped and the person's other devices are read. A failed fetch keeps what was read
and waits longer; the fetch prunes nothing of the cloud fetch's."""
import os

import pytest

from orchestraty import config, remote
from orchestraty.coordinator.record import Record
from orchestraty.team import identity, store, teamfile
from team_harness import BILLING, NS, Machine, run_git, team  # noqa: F401  (fixtures)

pytestmark = pytest.mark.heavy        # git-heavy: the quick Windows run in CI leaves it to the full run

TAX = BILLING.replace("currency)", "currency, tax)")


def common(machine):
    return str(machine.clone / ".git")


def writers(machine):
    rec = Record(machine.state.record, readonly=True)
    try:
        return {(w["person"], w["device"]): w for w in rec.team_writers(rec.repo_id(common(machine)))}
    finally:
        rec.close()


def fetched(machine):
    return teamfile.read(remote.Shadow(machine.state, common(machine))).get("fetch") or {}


def shadow_refs(machine, prefix):
    rc, out = remote.Shadow(machine.state, common(machine)).git(["for-each-ref", "--format=%(refname)", prefix])
    return out.split()


def test_bob_reads_alices_record_after_one_fetch(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    alice.push_work("A1", "feat/refunds", "pkg/billing.py", TAX)
    bob.team_on()
    bob.settle()                                                                   # a fetch at its start
    device = alice.device()["id"]
    row = writers(bob)[("alice", device)]
    assert row["verdict"] == "admitted" and row["store"] == "ref"
    assert [(e["agent"], e["branch"], e["pushed"]) for e in row["entries"]] == [("claude", "feat/refunds", True)]
    status = fetched(bob)
    assert status["ok"] is True and status["writers"] == 1
    assert "refs/orch-team/origin/heads/feat/refunds" in shadow_refs(bob, "refs/orch-team/origin/heads/")


def test_own_device_skipped_own_other_device_read(team, tmp_path):  # noqa: F811
    alice = team.alice
    alice.team_on()
    alice.push_work("A1", "feat/refunds", "pkg/billing.py", TAX)
    laptop = Machine(tmp_path, "laptop", "alice@example.com", team.remote, alice.git, team.clock)
    try:
        laptop.team_on()                                                           # alice again, another device
        laptop.settle()
        here, there = alice.device()["id"], laptop.device()["id"]
        assert here != there
        assert writers(laptop)[("alice", here)]["verdict"] == "admitted"            # your work on another machine
        team.clock.advance(150)
        alice.settle()
        assert ("alice", here) not in writers(alice)                               # this device's own: skipped
    finally:
        laptop.close()


def test_a_ref_that_is_not_a_record_is_reported(team):  # noqa: F811
    bob = team.bob
    code = run_git(team.remote, "rev-parse", "main")                               # a code commit, not a record
    run_git(team.remote, "update-ref", "refs/orchestraty/alice/0123456789ab", code)
    bob.team_on()
    bob.settle()
    row = writers(bob)[("alice", "0123456789ab")]
    assert (row["verdict"], row["why"]) == ("dropped", "not-a-record")


def test_offline_keeps_last_state_and_backs_off(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    alice.push_work("A1", "feat/refunds", "pkg/billing.py", TAX)
    bob.team_on()
    bob.settle()
    device = alice.device()["id"]
    assert writers(bob)[("alice", device)]["verdict"] == "admitted"
    away = str(team.remote) + ".away"
    os.rename(team.remote, away)                                                    # the remote cannot be read
    try:
        team.clock.advance(150)
        bob.settle()
        status = fetched(bob)
        assert status["ok"] is False and status["failures"] == 1 and status["error"]
        assert status["next"] - status["at"] >= 2 * 120                             # it waits longer
        assert writers(bob)[("alice", device)]["verdict"] == "admitted"            # what was read stays
    finally:
        os.rename(away, team.remote)
    team.clock.advance(status["next"] - status["at"])
    bob.settle()
    status = fetched(bob)
    assert status["ok"] is True and status["failures"] == 0


def test_fetch_due_within_two_and_a_half_minutes(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    bob.settle()
    first = fetched(bob)
    assert 0.8 * 120 <= first["next"] - first["at"] <= 1.2 * 120                    # two minutes, give or take a fifth
    alice.push_work("A1", "feat/refunds", "pkg/billing.py", TAX)
    team.clock.advance(150 - 3)                                                     # push_work moved it 3 s on
    bob.settle()
    assert fetched(bob)["at"] > first["at"]
    assert ("alice", alice.device()["id"]) in writers(bob)


def test_team_fetch_never_prunes_cloud_refs(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    run_git(alice.clone, "push", "-q", "origin", "main:refs/heads/claude/fix-tax")  # a cloud agent's branch
    remote.save_settings(bob.state, common(bob), on=True, remote="origin", patterns=["claude/*"])
    chosen = remote.settings(config.load(bob.state), common(bob))
    assert remote.fetch_now(bob.state, common(bob), chosen, 300)["ok"]
    assert "refs/orch/origin/claude/fix-tax" in shadow_refs(bob, "refs/orch/origin/")
    bob.team_on()
    bob.settle()
    assert fetched(bob)["ok"] is True
    assert "refs/orch/origin/claude/fix-tax" in shadow_refs(bob, "refs/orch/origin/")


def test_named_branch_missing_on_remote_does_not_fail_the_fetch(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    alice.push_work("A1", "feat/refunds", "pkg/billing.py", TAX)
    alice.push_work("A2", "feat/invoices", "pkg/invoices.py", "def total():\n    return 1\n")
    run_git(team.remote, "update-ref", "-d", "refs/heads/feat/refunds")           # gone, though the record names it
    bob.team_on()
    bob.settle()
    assert fetched(bob)["ok"] is True
    heads = shadow_refs(bob, "refs/orch-team/origin/heads/")
    assert "refs/orch-team/origin/heads/feat/invoices" in heads and "refs/orch-team/origin/heads/feat/refunds" not in heads
    shadow = remote.Shadow(bob.state, common(bob))
    outbox = store.Outbox(shadow, "origin", "bob", identity.Device.load(bob.state).id, "bob@example.com")
    assert outbox.remote_tip() == ""                                               # bob has sent nothing yet
    assert NS
