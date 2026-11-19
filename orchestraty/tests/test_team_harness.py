"""The two-machine harness itself (``team_harness``): before any test of team sync relies on it,
it must keep the machines apart, push the way git pushes, stay off the network, and let two
coordinators run in one process without sharing anything."""
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from orchestraty.coordinator.record import Record
from team_harness import git_env, publish_raw, refusing_remote, run_git, team  # noqa: F401  (fixtures)


def sessions(machine):
    rec = Record(machine.state.record, readonly=True)
    try:
        return {s["id"]: s for s in rec.sessions()}
    finally:
        rec.close()


def test_two_machines_do_not_share_state(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.start("A1", "feat/refunds")
    alice.edit("A1", "pkg/refunds.py", "def refund(amount):\n    return amount\n")
    bob.start("B1", "feat/invoices")
    alice.settle()
    bob.settle()
    assert set(sessions(alice)) == {"A1"} and set(sessions(bob)) == {"B1"}
    assert sessions(alice)["A1"]["branch"] == "feat/refunds" and sessions(bob)["B1"]["branch"] == "feat/invoices"
    for attr in ("root", "spool", "record", "config"):
        assert getattr(alice.state, attr) != getattr(bob.state, attr)
        assert Path(getattr(alice.state, attr)).resolve().is_relative_to(alice.folder.resolve())
    assert not list(Path(bob.state.spool).rglob("A1.jsonl")) and not list(Path(alice.state.spool).rglob("B1.jsonl"))
    assert run_git(alice.clone, "config", "user.email") == "alice@example.com"
    assert run_git(bob.clone, "config", "user.email") == "bob@example.com"
    alice.team_on()
    bob.team_on()
    devices = [m.device() for m in (alice, bob)]
    assert devices[0]["id"] != devices[1]["id"] and devices[0]["secret"] != devices[1]["secret"]
    assert all(len(d["id"]) == 12 and len(d["secret"]) == 32 for d in devices)
    assert all((Path(m.state.root) / "team" / "enabled").exists() for m in (alice, bob))
    alice.team_on()
    assert alice.device() == devices[0]                                         # made once


def test_machine_push_writes_an_update_by_push_reflog_line(team):  # noqa: F811
    alice = team.alice
    top = alice.start("A1", "feat/refunds")
    alice.edit("A1", "pkg/refunds.py", "def refund(amount):\n    return amount\n")
    commit = alice.commit("A1", "refunds without currency")
    assert alice.push("A1") == commit
    assert run_git(team.remote, "rev-parse", "refs/heads/feat/refunds") == commit
    reflog = run_git(top, "reflog", "show", "--format=%H %gs", "refs/remotes/origin/feat/refunds")
    assert f"{commit} update by push" in reflog.splitlines()
    assert run_git(top, "log", "-1", "--format=%an <%ae>") == "alice <alice@example.com>"


def test_harness_uses_only_the_file_protocol(team):  # noqa: F811
    for machine in (team.alice, team.bob):
        assert run_git(machine.clone, "remote", "get-url", "origin").startswith("file://")
    assert git_env()["GIT_ALLOW_PROTOCOL"] == "file"
    proc = subprocess.run(["git", "ls-remote", "http://127.0.0.1:9/repo.git"], capture_output=True, env=git_env(),
                          timeout=60)
    assert proc.returncode != 0 and b"not allowed" in proc.stderr


def test_publish_raw_and_the_refusing_remotes(team, refusing_remote):  # noqa: F811
    first = publish_raw(team.remote, "alice", "7f3a9c2e41b0", b'{"schema": 1}', "alice@example.com")
    second = publish_raw(team.remote, "alice", "7f3a9c2e41b0", b'{"schema": 1, "seq": 2}', "mallory@example.com")
    ref = "refs/orchestraty/alice/7f3a9c2e41b0"
    assert run_git(team.remote, "rev-parse", ref) == second
    assert run_git(team.remote, "rev-parse", ref + "^") == first                    # a chain, one ref per writer
    assert run_git(team.remote, "cat-file", "-p", ref + ":record.json") == '{"schema": 1, "seq": 2}'
    assert run_git(team.remote, "log", "-1", "--format=%ae", ref) == "mallory@example.com"   # anyone can forge
    run_git(team.alice.clone, "fetch", "-q", "origin")
    assert run_git(team.alice.clone, "for-each-ref", "refs/orchestraty/") == ""          # a plain fetch never brings it
    head = run_git(team.alice.clone, "rev-parse", "HEAD")
    for path in (refusing_remote.hidden, refusing_remote.hook):
        proc = subprocess.run(["git", "push", "--porcelain", str(path), f"{head}:refs/orchestraty/alice/dev"],
                              cwd=team.alice.clone, capture_output=True, env=git_env(), timeout=60)
        assert proc.returncode != 0 and b"[remote rejected]" in proc.stdout
        run_git(team.alice.clone, "push", "-q", str(path), "HEAD:refs/heads/feat/ok")      # branches go as usual


def _module_state():
    """repr() of every module-level dict, set and list of Orchestraty and its engine."""
    out = {}
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith(("orchestraty", "orch_engine")):
            continue
        for attr, value in list(vars(module).items()):
            if isinstance(value, (dict, set, list)) and not attr.startswith("__"):
                try:
                    out[(name, attr)] = repr(value)
                except Exception:                        # a value that cannot say what it holds
                    continue
    return out


def test_two_coordinators_in_one_process_share_no_module_state(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    logger = logging.getLogger("orchestraty.coordinator")
    handlers = list(logger.handlers)
    before = _module_state()
    alice.start("A1", "feat/refunds")
    bob.start("B1", "feat/invoices")
    a, b = alice.coordinator(), bob.coordinator()
    alice.edit("A1", "pkg/billing.py", "def charge(amount):\n    return amount\n")
    bob.edit("B1", "pkg/refunds.py", "from pkg.billing import charge\n\n\ndef refund(amount):\n"
                                     "    return charge(-amount, 'USD')\n")
    alice.settle()
    bob.settle()
    for name in ("record", "livemap", "deliverer", "cloud", "together", "sessions", "cfg"):
        assert getattr(a, name) is not getattr(b, name), name
    assert a.livemap.cache is not b.livemap.cache and a.livemap.indexes is not b.livemap.indexes
    assert set(a.sessions) == {"A1"} and set(b.sessions) == {"B1"}
    assert logger.handlers == handlers                                    # no coordinator logs to another's folder
    after = _module_state()
    folders = {str(m.folder) for m in (alice, bob)} | {str(m.folder.resolve()) for m in (alice, bob)}
    machines = tuple(folders | {f.replace("\\", "\\\\") for f in folders})       # as repr() writes a Windows path
    leaked = sorted(f"{name}.{attr}" for (name, attr), text in after.items()
                    if any(m in text for m in machines) and not any(m in before.get((name, attr), "") for m in machines))
    assert leaked == []
