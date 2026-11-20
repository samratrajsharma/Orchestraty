"""Stage 2.2 from end to end on two machines: alice pushes her work, bob's machine sees her branch
within one fetch, alice's branch is squash-merged and bob's session of it ends. And a user with no
team setup runs no team git command and gets no team folder, step after step."""
import os
import socket
import subprocess
from pathlib import Path

from orchestraty import status
from orchestraty.coordinator.team import session_id
from team_harness import BILLING, run_git, team  # noqa: F401  (fixtures)
from test_team_solo import _subcommand, play, solo  # noqa: F401  (fixtures)

TAX = BILLING.replace("currency)", "currency, tax)")


def test_two_machines_end_to_end(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    bob.start("B1", "feat/refunds")
    bob.settle()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)                       # alice works and pushes
    team.clock.advance(150)
    bob.settle()                                                                   # bob's next fetch
    sid = session_id(str(bob.clone / ".git"), "origin", "feat/x")
    (repo,) = status.collect(bob.state)["repositories"]
    (s,) = repo["team"]["sessions"]
    assert (s["id"], s["team"]["people"], s["changes"]["files"]) == (sid, ["alice"], {"pkg/billing.py": "M"})
    assert [c["contract"] for c in s["changes"]["symbols"] if c["symbol"] == "pkg/billing.py::charge"] == ["breaking"]
    run_git(alice.clone, "fetch", "-q", "origin")                                  # squash-merged, as a host does it
    run_git(alice.clone, "merge", "-q", "--squash", "origin/feat/x")
    run_git(alice.clone, "commit", "-q", "-m", "feat/x (#1)")
    run_git(alice.clone, "push", "-q", "origin", "HEAD:main")
    team.clock.advance(150)
    bob.settle()
    (repo,) = status.collect(bob.state, include_ended=True)["repositories"]
    assert repo["team"]["sessions"] == []                                          # no teammate's branch now
    c = bob.coordinator()
    assert sid not in c.sessions
    from orchestraty.coordinator.record import Record
    rec = Record(bob.state.record, readonly=True)
    try:
        ended = rec.session(sid)
    finally:
        rec.close()
    assert (ended["state"], ended["note"]) == ("ended", "merged into the default branch (merge-tree)")


def test_no_team_setup_makes_no_team_git_calls_and_no_team_folder(solo, monkeypatch):  # noqa: F811
    state, repo, tmp_path = solo
    started, connected = [], []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **k):
            started.append([str(x) for x in args] if not isinstance(args, (str, bytes)) else [str(args)])
            super().__init__(args, *a, **k)

    def refuse(*args, **kwargs):
        connected.append(args)
        raise OSError("no network without team sync")
    monkeypatch.setattr(subprocess, "Popen", Spy)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    play(state, tmp_path, steps=50)
    team_words = ("orchestraty/", "orch-team", "orch-out", "ls-remote", "merge-tree", "patch-id")
    assert not [argv for argv in started if any(w in " ".join(argv) for w in team_words)]
    assert {_subcommand(argv) for argv in started} <= {"cat-file", "diff", "ls-files", "ls-tree", "rev-list",
                                                       "rev-parse", "symbolic-ref", "worktree"}
    assert connected == []
    root = Path(state.root)
    assert not (root / "team").exists() and not (root / "shadows").exists()
    assert not [p for p in root.rglob("*.team.json")]
    assert os.path.exists(state.record)
