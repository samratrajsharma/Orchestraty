"""What this machine's record would hold of its sessions (team/snapshot.py through the outgoing
gate): only committed work, never raw text, never cloud or teammates' sessions, nothing on a
detached HEAD, and each branch as the team remote has it."""
import json
import secrets

from helpers import spool_event
from orchestraty import declarations
from orchestraty.coordinator.record import Record
from orchestraty.team import commands
from team_harness import BILLING, NS, run_git, team  # noqa: F401  (fixtures)

UNPUSHED = """\
version: 1
sync: on
visibility: private
share:
  claims: true
  task: confirmed
  unpushed: true
"""
TAX = BILLING.replace("currency)", "currency, tax)")


def push_policy(machine, text):
    (machine.clone / ".orchestraty" / "policy.yml").write_text(text, encoding="utf-8")
    run_git(machine.clone, "add", ".orchestraty/policy.yml")
    run_git(machine.clone, "commit", "-q", "-m", "the team's policy")
    run_git(machine.clone, "push", "-q", "origin", "HEAD:main")


def record_of(machine, now):
    sealed = commands.sealed_now(machine.state, str(machine.clone / ".git"), now)
    assert sealed.data is not None and sealed.held == ""
    return json.loads(sealed.data), sealed


def session_on(record, branch):
    (s,) = [s for s in record["sessions"] if s["branch"] == branch]
    return s


def test_snapshot_shares_only_committed_work(team):  # noqa: F811
    alice = team.alice
    alice.team_on()
    alice.start("A1", "feat/invoices")
    alice.settle()                                                                 # its base: where it started
    alice.edit("A1", "pkg/billing.py", TAX)
    alice.commit("A1", "invoices: tax")
    alice.edit("A1", "pkg/draft.py", "def draft():\n    return 1\n")           # never committed
    alice.settle()
    now = team.clock() / NS
    record, _ = record_of(alice, now)
    assert session_on(record, "feat/invoices")["work"] is None                     # unpushed work: off by default
    push_policy(alice, UNPUSHED)
    alice.team_on()                                                                # agreed to again, wider
    alice.settle()
    record, sealed = record_of(alice, now)
    work = session_on(record, "feat/invoices")["work"]
    assert work["tip"] == run_git(alice.worktrees["A1"], "rev-parse", "HEAD")
    assert work["files"] == [["pkg/billing.py", "M"]] and work["more"] == {"files": 0, "symbols": 0}
    (charge,) = work["symbols"]
    assert (charge["symbol"], charge["kind"], charge["contract"]) == ("pkg/billing.py::charge", "modified", "breaking")
    assert charge["reasons"] == ["new required parameter tax"] and charge["breaks"]["new required parameter tax"]
    assert b"draft" not in sealed.data                                             # uncommitted: never
    alice.push("A1", "-u")
    s = session_on(record_of(alice, now)[0], "feat/invoices")
    assert s["work"] is None and s["pushed"]["ref"] == "refs/heads/feat/invoices"  # pushed work: read from commits


def test_snapshot_contains_no_raw_text(team):  # noqa: F811
    alice = team.alice
    push_policy(alice, UNPUSHED)
    alice.team_on()
    top = alice.start("A1", "feat/invoices")
    alice.settle()
    marks = {k: f"zq{k}{secrets.token_hex(6)}" for k in ("prompt", "declared", "commit")}
    spool_event(alice.state.root, "A1", "UserPromptSubmit", top, ts=team.clock(),
                prompt=f"SECRET-PROMPT {marks['prompt']}")
    alice.edit("A1", "pkg/billing.py", TAX)
    alice.commit("A1", f"invoices {marks['commit']}")
    declarations.append(alice.state, "A1", ["pkg/billing.py"], [], f"tax for {marks['declared']}")
    alice.settle()
    _, sealed = record_of(alice, team.clock() / NS)
    assert b"pkg/billing.py::charge" in sealed.data                                # the work is there
    for mark in ("SECRET-PROMPT",) + tuple(marks.values()):
        assert mark.encode() not in sealed.data, mark


def test_cloud_and_team_sessions_are_never_reshared(team):  # noqa: F811
    alice = team.alice
    alice.team_on()
    alice.start("A1", "feat/invoices")
    alice.settle()
    rec = Record(alice.state.record)
    try:
        (mine,) = [s for s in rec.sessions() if s["id"] == "A1"]
        rec.put_session(dict(mine, id="cloud-1a2b3c4d5e6f", agent="cloud", branch="origin/claude/fix-tax",
                             worktree="", cwd=""))
        rec.put_session(dict(mine, id="team-1a2b3c4d5e6f", agent="team", branch="origin/feat/refunds",
                             worktree="", cwd=""))
    finally:
        rec.close()
    record, sealed = record_of(alice, team.clock() / NS)
    assert "feat/invoices" in {s["branch"] for s in record["sessions"]}
    assert b"fix-tax" not in sealed.data and b"refunds" not in sealed.data
    assert sealed.withheld == []                                                   # left out, not withheld


def test_detached_head_is_not_shared(team):  # noqa: F811
    alice = team.alice
    alice.team_on()
    top = alice.folder / "wt-D1"
    run_git(alice.clone, "worktree", "add", "-q", "--detach", str(top), "origin/main")
    alice.worktrees["D1"], alice.agents["D1"] = top, "claude"
    spool_event(alice.state.root, "D1", "SessionStart", top, agent="claude", ts=team.clock())
    alice.edit("D1", "pkg/billing.py", TAX)
    alice.commit("D1", "on no branch")
    alice.start("A1", "feat/invoices")
    alice.settle()
    rec = Record(alice.state.record, readonly=True)
    try:
        (detached,) = [s for s in rec.sessions() if s["id"] == "D1"]
    finally:
        rec.close()
    assert detached["branch"] == "" and detached["state"] == "live"                # known here
    record, sealed = record_of(alice, team.clock() / NS)
    assert [s["branch"] for s in record["sessions"] if s["agent"] == "claude"] == ["feat/invoices"]
    assert sealed.withheld == []                                                   # left out, not withheld


def test_pushed_reads_push_then_upstream_on_the_team_remote_only(team, tmp_path):  # noqa: F811
    alice = team.alice
    fork = tmp_path / "fork.git"
    run_git(tmp_path, "clone", "-q", "--bare", str(team.remote), str(fork))
    run_git(alice.clone, "remote", "add", "fork", fork.resolve().as_uri())
    alice.team_on()
    for sid, branch in (("A1", "feat/up"), ("A2", "feat/fork"), ("A3", "feat/push")):
        alice.start(sid, branch)
        alice.edit(sid, "pkg/billing.py", BILLING + f"# {sid}\n")
        alice.commit(sid, sid)
    alice.push("A1", "-u")                                                         # its upstream: origin
    run_git(alice.worktrees["A2"], "push", "-q", "-u", "fork", "feat/fork")        # its upstream: another remote
    top = alice.worktrees["A3"]
    run_git(top, "push", "-q", "-u", "fork", "feat/push")                          # upstream on the fork,
    run_git(top, "config", "branch.feat/push.pushRemote", "origin")                # but pushed to origin
    run_git(top, "push", "-q", "origin", "feat/push")
    alice.settle()
    record, _ = record_of(alice, team.clock() / NS)
    pushed = {s["branch"]: s["pushed"] for s in record["sessions"]}
    tip = {b: run_git(alice.clone, "rev-parse", f"refs/remotes/origin/{b}") for b in ("feat/up", "feat/push")}
    assert pushed["feat/up"] == {"ref": "refs/heads/feat/up", "tip": tip["feat/up"]}
    assert pushed["feat/fork"] is None                                             # not on the team remote
    assert pushed["feat/push"] == {"ref": "refs/heads/feat/push", "tip": tip["feat/push"]}
