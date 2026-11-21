"""The record goes with the user's own push: the coordinator sees git's "update by push" reflog line
(or, with reflogs off, the remote-tracking refs moving while FETCH_HEAD stays), waits 3 seconds and
pushes this device's record on a thread of its own. A push git refused, a fetch, or no push at all
sends nothing. The pre-push hook only names the remote and starts the coordinator, and only while
team sync is on somewhere; it runs no git and touches no network."""
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from orchestraty import remote
from orchestraty.capture import hook
from orchestraty.coordinator.record import Record
from orchestraty.team import identity, store
from team_harness import BILLING, NS, run_git, team  # noqa: F401  (fixtures)

pytestmark = pytest.mark.heavy        # git-heavy: the quick Windows run in CI leaves it to the full run

REFUSE_FEATURES = ("#!/bin/sh\n"
                   "while read old new ref; do\n"
                   "  case \"$ref\" in refs/heads/feat/*) echo \"no feature branches here\" >&2; exit 1;; esac\n"
                   "done\n"
                   "exit 0\n")


def common(machine):
    return str(machine.clone / ".git")


def fetch(machine):
    """The first team fetch: until one has read the remote, nothing is pushed."""
    shadow = remote.Shadow(machine.state, common(machine))
    assert store.fetch_records(shadow, remote.remote_url(common(machine), "origin"), "origin",
                               machine.clock() / NS)


def on_remote(team, machine):  # noqa: F811
    device = identity.Device.load(machine.state).id
    return run_git(team.remote, "for-each-ref", "--format=%(objectname)", f"refs/orchestraty/{machine.name}/{device}")


def pre_push_line(machine, top):
    """The line git's pre-push hook appends while team sync is on: it names the remote."""
    path = Path(machine.state.root) / "spool" / "git" / (time.strftime("%Y-%m-%d") + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": machine.clock(), "pid": 1, "agent": "git", "event": "pre-push", "cwd": str(top), "args": 2,
            "remote": "origin"}
    with open(path, "ab") as fh:
        fh.write(json.dumps(line).encode("utf-8") + b"\n")


def working(machine, sid="A1", branch="feat/refunds"):
    """Team sync on, a session that changed and committed charge, and a first team fetch."""
    machine.team_on()
    top = machine.start(sid, branch)
    machine.settle()
    fetch(machine)
    machine.edit(sid, "pkg/billing.py", BILLING.replace("currency)", "currency, tax)"))
    machine.commit(sid, "refunds: tax")
    machine.settle()
    return top


def sent_rows(machine):
    rec = Record(machine.state.record, readonly=True)
    try:
        return [r for repo in rec.repos() for r in rec.team_sent(repo["id"])]
    finally:
        rec.close()


def test_nothing_is_pushed_without_the_users_push(team):  # noqa: F811
    alice = team.alice
    working(alice)
    for _ in range(3):
        team.clock.advance(150)
        alice.settle()
    assert on_remote(team, alice) == "" and sent_rows(alice) == []


def test_a_successful_push_sends_the_record_within_seconds(team):  # noqa: F811
    alice = team.alice
    top = working(alice)
    pre_push_line(alice, top)
    alice.push("A1", "-u")
    alice.settle()
    assert on_remote(team, alice) == ""                                            # git push --all may go on: 3 s
    team.clock.advance(3)
    alice.settle()
    tip = on_remote(team, alice)
    record = json.loads(run_git(team.remote, "cat-file", "blob", f"{tip}:record.json"))
    assert record["sessions"][0]["pushed"]["ref"] == "refs/heads/feat/refunds"
    (row,) = sent_rows(alice)
    assert (row["result"], row["seq"], row["commit_id"], row["store"]) == ("pushed", 1, tip, "ref")


def test_a_rejected_user_push_sends_nothing(team):  # noqa: F811
    alice = team.alice
    hook_file = Path(team.remote) / "hooks" / "pre-receive"
    hook_file.parent.mkdir(exist_ok=True)
    hook_file.write_bytes(REFUSE_FEATURES.encode("utf-8"))
    os.chmod(hook_file, 0o755)
    top = working(alice)
    pre_push_line(alice, top)
    with pytest.raises(AssertionError, match="no feature branches"):
        alice.push("A1", "-u")
    for _ in range(2):
        team.clock.advance(3)
        alice.settle()
    assert on_remote(team, alice) == "" and sent_rows(alice) == []


def test_git_fetch_alone_triggers_nothing(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    working(alice)
    bob.start("B1", "feat/invoices")
    bob.edit("B1", "pkg/invoices.py", "def total():\n    return 1\n")
    bob.commit("B1", "invoices")
    bob.push("B1", "-u")
    run_git(alice.clone, "fetch", "-q", "origin")                                   # fetch: lines, never push ones
    assert "fetch" in (alice.clone / ".git" / "logs" / "refs" / "remotes" / "origin" / "feat" / "invoices").read_text()
    for _ in range(2):
        team.clock.advance(3)
        alice.settle()
    assert on_remote(team, alice) == ""


def test_push_detected_without_the_push_line(team):  # noqa: F811
    alice = team.alice
    working(alice)
    alice.push("A1", "-u")                                                         # no hook line: the reflog says it
    assert not (Path(alice.state.root) / "spool" / "git").exists()
    alice.settle()
    team.clock.advance(3)
    alice.settle()
    assert on_remote(team, alice) != ""


def test_push_detected_with_reflogs_off(team):  # noqa: F811
    alice = team.alice
    run_git(alice.clone, "config", "core.logAllRefUpdates", "false")
    logs = alice.clone / ".git" / "logs" / "refs" / "remotes"
    if logs.exists():
        for path in sorted(logs.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        logs.rmdir()
    working(alice)
    alice.push("A1", "-u")
    assert not logs.exists()                                                       # git wrote no reflog
    alice.settle()
    team.clock.advance(3)
    alice.settle()
    assert on_remote(team, alice) != ""


def test_a_push_while_no_coordinator_ran_is_caught_up_on_start(team):  # noqa: F811
    alice = team.alice
    working(alice)
    alice.close()                                                                  # no coordinator runs
    alice.push("A1", "-u")
    alice.settle()                                                                 # the next one starts
    team.clock.advance(3)
    alice.settle()
    assert on_remote(team, alice) != ""


def test_a_coordinator_brought_up_for_a_push_leaves_soon(team):  # noqa: F811
    alice = team.alice
    alice.team_on()
    fetch(alice)
    run_git(alice.clone, "checkout", "-q", "-b", "feat/plain")
    run_git(alice.clone, "commit", "-q", "--allow-empty", "-m", "plain")
    run_git(alice.clone, "push", "-q", "-u", "origin", "feat/plain")
    c = alice.coordinator()
    c.step()
    assert c.busy()                                                                # the record is still to go
    team.clock.advance(3)
    alice.settle()
    assert not c.busy() and c.idle_after() == c.cfg["team_idle_seconds"]           # no agent session: a minute
    alice.start("A1", "feat/refunds")
    alice.settle()
    assert c.idle_after() == c.cfg["idle_seconds"]                                 # an agent's session: as ever


def git_lines(root):
    folder = Path(root) / "spool" / "git"
    return [json.loads(line) for path in sorted(folder.glob("*.jsonl")) for line in path.read_text().splitlines()]


def test_pre_push_starts_the_coordinator_only_when_team_is_enabled(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()
    started = []
    monkeypatch.setattr(hook, "start_coordinator", lambda folder, now: started.append(folder) or True)
    monkeypatch.chdir(tmp_path)
    hook.git_event(str(root), b"pre-push", ["origin", "https://example.invalid/team.git"])
    assert "remote" not in git_lines(root)[-1] and started == []                   # as 0.1.1
    (root / "team").mkdir()
    (root / "team" / "enabled").write_bytes(b"")
    hook.git_event(str(root), b"post-commit", [])
    assert started == []                                                           # only a push
    hook.git_event(str(root), b"pre-push", ["origin", "https://example.invalid/team.git"])
    assert git_lines(root)[-1]["remote"] == "origin" and started == [str(root)]


def test_pre_push_hook_runs_no_git_and_no_network(tmp_path, monkeypatch):
    root = tmp_path / "state"
    (root / "team").mkdir(parents=True)
    (root / "team" / "enabled").write_bytes(b"")
    (root / "push").mkdir()
    started = []

    def refuse(*args, **kwargs):
        raise AssertionError(f"the hook ran a program or opened a socket: {args[:1]}")

    monkeypatch.setattr(hook, "start_coordinator", lambda folder, now: started.append(folder) or True)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.chdir(tmp_path)
    hook.record(str(root), b"git", b"pre-push", ["git", "pre-push", "origin", "git@example.invalid:team.git"])
    assert git_lines(root)[-1]["remote"] == "origin" and started == [str(root)]
