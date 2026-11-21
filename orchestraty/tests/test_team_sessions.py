"""Teammates' branches as sessions (coordinator/team.py): each remote branch an admitted record
names becomes a session of agent team, read from the commits the team fetch brought, whoever
named it. It lives while a record names it, it is not merged, the branch is there and it is not
stale; it gets no notes, never times out and keeps no coordinator awake; the rules compare it with this
machine's sessions. A branch a cloud pattern also matches is followed once, as a team session."""
import json

import pytest

from helpers import spool_event
from orchestraty import config, remote
from orchestraty.coordinator.record import Record
from orchestraty.coordinator.team import session_id
from team_harness import BILLING, NS, Machine, publish_raw, run_git, team  # noqa: F401  (fixtures)

pytestmark = pytest.mark.heavy        # git-heavy: the quick Windows run in CI leaves it to the full run

TAX = BILLING.replace("currency)", "currency, tax)")
DAY = 86400


def common(machine):
    return str(machine.clone / ".git")


def sid_of(machine, branch):
    return session_id(common(machine), "origin", branch)


def session(machine, sid):
    rec = Record(machine.state.record, readonly=True)
    try:
        found = [s for s in rec.sessions() if s["id"] == sid]
        return (found[0] if found else None), rec.team_session(sid), rec.changes(sid)
    finally:
        rec.close()


def read_again(team, machine, seconds=150):  # noqa: F811
    """One team fetch interval on the shared clock, then the machine's coordinator catches up."""
    team.clock.advance(seconds)
    machine.settle()


def test_a_second_machine_sees_the_first_ones_pushed_work(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    bob.start("B1", "feat/invoices")
    bob.settle()
    started = team.clock()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)                       # 3 s on the clock
    read_again(team, bob)                                                          # one fetch interval
    assert team.clock() - started <= (3 + 150) * NS
    s, row, (files, _, symbols) = session(bob, sid_of(bob, "feat/x"))
    assert (s["agent"], s["branch"], s["state"], s["worktree"]) == ("team", "origin/feat/x", "live", "")
    assert row["people"] == ["alice"] and row["own"] == 0 and row["evidence"] == "commits"
    assert files == {"pkg/billing.py": "M"}
    (charge,) = [x for x in symbols if x["symbol"] == "pkg/billing.py::charge"]
    assert charge["contract"] == "breaking"
    assert row["tip"] == run_git(team.remote, "rev-parse", "feat/x")


def test_a_pair_on_one_branch_is_one_session_with_two_people(team, tmp_path):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    run_git(bob.clone, "fetch", "-q", "origin")
    top = bob.folder / "wt-B1"
    run_git(bob.clone, "worktree", "add", "-q", "-b", "feat/x", str(top), "origin/feat/x")
    bob.worktrees["B1"], bob.agents["B1"] = top, "codex"
    spool_event(bob.state.root, "B1", "SessionStart", top, agent="codex", ts=bob.clock())
    bob.settle()
    bob.edit("B1", "pkg/refunds.py", "from pkg.billing import charge\n\n\ndef refund(amount):\n    return 0\n")
    bob.commit("B1", "refunds")
    run_git(top, "push", "-q", "-u", "origin", "feat/x")
    bob.settle()
    team.clock.advance(3)
    bob.settle()
    carol = Machine(tmp_path, "carol", "carol@example.com", team.remote, alice.git, team.clock)   # not listed
    try:
        carol.team_on()
        carol.settle()
        carol.settle()
        rec = Record(carol.state.record, readonly=True)
        try:
            live = [s for s in rec.sessions() if s["agent"] == "team" and s["state"] == "live"]
            (row,) = [rec.team_session(s["id"]) for s in live]
        finally:
            rec.close()
        assert [s["branch"] for s in live] == ["origin/feat/x"]
        assert row["people"] == ["alice", "bob"]
        assert row["agents"] == ["claude", "codex"]
        assert row["tip"] == run_git(team.remote, "rev-parse", "feat/x")             # bob's push, on top
    finally:
        carol.close()


def test_force_pushed_branch_is_read_from_the_fetched_tip(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    read_again(team, bob)
    top = alice.worktrees["A1"]
    (top / "pkg" / "billing.py").write_text(BILLING.replace("currency)", "currency, fee)"), encoding="utf-8")
    run_git(top, "commit", "-q", "-a", "--amend", "-m", "fee, not tax")
    run_git(top, "push", "-q", "-f", "origin", "feat/x")                           # alice's record still says the old tip
    rewritten = run_git(top, "rev-parse", "HEAD")
    read_again(team, bob)
    _, row, (files, _, symbols) = session(bob, sid_of(bob, "feat/x"))
    assert row["tip"] == rewritten
    (charge,) = [x for x in symbols if x["symbol"] == "pkg/billing.py::charge"]
    assert "fee" in json.dumps(charge["reasons"]) and "tax" not in json.dumps(charge["reasons"])


def test_branch_in_team_and_cloud_is_one_team_session(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    remote.save_settings(bob.state, common(bob), on=True, remote="origin", patterns=["claude/*"])
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "claude/fix-tax", "pkg/billing.py", TAX)                # a cloud agent's name, alice's work
    chosen = remote.settings(config.load(bob.state), common(bob))
    assert remote.fetch_now(bob.state, common(bob), chosen, 300)["ok"]
    bob.settle()
    read_again(team, bob)
    rec = Record(bob.state.record, readonly=True)
    try:
        sessions = [s for s in rec.sessions() if s["branch"] == "origin/claude/fix-tax"]
    finally:
        rec.close()
    live = [s for s in sessions if s["state"] == "live"]
    assert [s["agent"] for s in live] == ["team"]
    assert [(s["state"], s["note"]) for s in sessions if s["agent"] == "cloud"] == [
        ("ended", "followed as a teammate's branch")]                              # seen first as a cloud branch


def test_merged_gone_unnamed_stale_end_the_session(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    alice.push_work("A2", "feat/gone", "pkg/gone.py", "GONE = 1\n")
    alice.push_work("A3", "feat/unnamed", "pkg/unnamed.py", "UNNAMED = 1\n")
    alice.push_work("A4", "feat/old", "pkg/old.py", "OLD = 1\n")
    read_again(team, bob)
    assert all(session(bob, sid_of(bob, b))[0]["state"] == "live"
               for b in ("feat/x", "feat/gone", "feat/unnamed", "feat/old"))
    run_git(alice.clone, "fetch", "-q", "origin")                                  # feat/x, squash-merged on main
    run_git(alice.clone, "merge", "-q", "--squash", "origin/feat/x")
    run_git(alice.clone, "commit", "-q", "-m", "feat/x, squashed")
    run_git(alice.clone, "push", "-q", "origin", "HEAD:main")
    run_git(team.remote, "update-ref", "-d", "refs/heads/feat/gone")                # feat/gone, deleted
    device = alice.device()["id"]                                                  # feat/unnamed: in no record now
    old = json.loads(run_git(team.remote, "cat-file", "blob", f"refs/orchestraty/alice/{device}:record.json"))
    old["sessions"] = [s for s in old["sessions"] if s["branch"] != "feat/unnamed"]
    old["seq"] += 1
    publish_raw(team.remote, "alice", device,
                (json.dumps(old, sort_keys=True, separators=(",", ":")) + "\n").encode(), "alice@example.com")
    read_again(team, bob)
    notes = {b: session(bob, sid_of(bob, b))[0] for b in ("feat/x", "feat/gone", "feat/unnamed", "feat/old")}
    assert (notes["feat/x"]["state"], notes["feat/x"]["note"]) == ("ended", "merged into the default branch (merge-tree)")
    assert (notes["feat/gone"]["state"], notes["feat/gone"]["note"]) == ("ended", "the branch is gone")
    assert (notes["feat/unnamed"]["state"], notes["feat/unnamed"]["note"]) == ("ended", "no longer in a teammate's record")
    assert notes["feat/old"]["state"] == "live"
    newest = int(run_git(team.remote, "log", "-1", "--format=%ct", "feat/old"))
    read_again(team, bob, max(0, newest - team.clock() // NS) + 15 * DAY)          # nothing new for 15 days
    assert session(bob, sid_of(bob, "feat/old"))[0]["note"] == "stale"


def test_team_sessions_receive_no_notes_and_never_time_out(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    read_again(team, bob)
    sid = sid_of(bob, "feat/x")
    c = bob.coordinator()
    assert c.sessions[sid]["agent"] == "team" and c.live_count(hooked_only=True) == 0
    read_again(team, bob, c.cfg["session_timeout"] + 3600)                          # far past any timeout
    assert session(bob, sid)[0]["state"] == "live"
    slots = [p for p in (bob.folder / "state" / "notes").rglob("*") if p.is_file() and sid in str(p)]
    assert slots == []


def test_team_sessions_take_part_in_the_rules(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)                       # charge: a new required parameter
    bob.start("B1", "feat/refunds")
    bob.settle()
    bob.edit("B1", "pkg/refunds.py", "from pkg.billing import charge\n\n\ndef refund(amount):\n"
                                     "    return charge(-amount, 'EUR')\n\n\ndef again(a):\n    return charge(a, 'EUR')\n")
    read_again(team, bob)
    sid = sid_of(bob, "feat/x")
    assert session(bob, sid)[0]["state"] == "live"
    rec = Record(bob.state.record, readonly=True)
    try:
        found = [f for f in rec.findings() if sid in (f["changer"], f["receiver"])]
    finally:
        rec.close()
    (o3,) = [f for f in found if f["rule"] == "O3" and not f["detail"].get("latent")]
    assert (o3["changer"], o3["receiver"], o3["file"], o3["strength"]) == (sid, "B1", "pkg/refunds.py", "strong")
    assert o3["detail"]["evidence"] == "commits"                                   # read from alice's commits


def test_restart_tracks_team_sessions_from_records(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    read_again(team, bob)
    sid = sid_of(bob, "feat/x")
    before, _, _ = session(bob, sid)
    bob.close()                                                                    # the coordinator leaves
    bob.settle()                                                                   # a new one starts
    c = bob.coordinator()
    assert sid in c.livemap.branches                                               # followed again
    after, _, (files, _, _) = session(bob, sid)
    assert after["state"] == "live" and after["started"] == before["started"] and files == {"pkg/billing.py": "M"}
