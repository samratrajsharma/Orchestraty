"""Sessions that are not an agent's on this machine: plain git (level 0), cloud agents' branches
and teammates' branches. Each kind is told apart in one place (``sessionkinds``), and every part
of Orchestraty treats a session of each kind the same way: a session with no hooks gets no notes
and keeps no coordinator awake; one with no working copy here is never claimed, declared,
checked together or handed off; one whose work lives elsewhere never times out and is not
listed among the sessions here."""
import time
from pathlib import Path

import pytest

from orchestraty import cli, config, handoff, status, together
from orchestraty.coordinator import main as cmain
from orchestraty.coordinator.record import Record
from orchestraty.delivery.deliver import Deliverer
from orchestraty.paths import State
from orchestraty.rules.overlap import Finding
from test_ui_server import act, srv  # noqa: F401  (the dashboard server fixture)

NS = 1_000_000_000
NO_NOTES = ["git", "cloud", "team"]
NO_COPY = ["cloud", "team"]
REMOTE = ["cloud", "team"]


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def put(state, repo, sid, agent, last_seen=None):
    """A live session row of this agent in the watched `repo`, as the coordinator keeps one:
    a worktree for a local session and for plain git, none for a branch read from elsewhere."""
    common = str(repo / ".git")
    config.watch(state, common)
    now = time.time_ns()
    remote = agent in ("cloud", "team")
    rec = Record(state.record)
    try:
        with rec.transaction():
            rid = rec.repo_id(common)
            rec.put_session({"id": sid, "agent": agent, "repo_id": rid, "cwd": "" if remote else str(repo),
                             "worktree": "" if remote else str(repo), "branch": "origin/feat-x" if remote else "main",
                             "base_commit": "", "base_guessed": 0, "started": now,
                             "last_seen": now if last_seen is None else last_seen, "state": "live", "turn": "",
                             "note": ""})
    finally:
        rec.close()
    return rid


@pytest.mark.parametrize("agent", REMOTE)
def test_not_expired_after_the_session_timeout(state, repo, agent):
    now = time.time_ns()
    put(state, repo, "R", agent, last_seen=now - (config.DEFAULTS["session_timeout"] + 60) * NS)
    c = cmain.Coordinator(state, now_ns=lambda: now)
    try:
        c.expire(now)
        assert "R" in c.sessions and c.record.session("R")["state"] == "live"
    finally:
        c.close()


@pytest.mark.parametrize("agent", NO_NOTES)
def test_not_counted_as_keeping_the_coordinator_awake(state, repo, agent):
    put(state, repo, "R", agent)
    c = cmain.Coordinator(state)
    try:
        assert (c.live_count(), c.live_count(hooked_only=True)) == (1, 0)
    finally:
        c.close()


@pytest.mark.parametrize("agent", NO_COPY)
def test_claim_and_declaration_lines_for_it_are_dropped(state, repo, agent):
    put(state, repo, "R", agent)
    c = cmain.Coordinator(state)
    try:
        ts = time.time_ns()
        with c.record.transaction():
            c.handle_claim({"event": "claim", "repo": str(repo / ".git"), "session_id": "R", "kind": "file",
                            "target": "a.py", "file": "", "hard": False, "minutes": 30}, ts)
            c.handle_declaration({"event": "declare", "session_id": "R", "paths": ["a.py"], "symbols": [],
                                  "task": "change a.py"}, ts)
        assert c.record.claims() == [] and c.record.declarations() == []
        assert c.bad_lines == 0                                     # dropped, not malformed
    finally:
        c.close()


@pytest.mark.parametrize("agent", NO_COPY)
def test_no_together_job_and_no_push_line(state, repo, agent):
    rid = put(state, repo, "R", agent)
    put(state, repo, "L", "claude")
    together.save_settings(state, str(repo / ".git"), on=True, command="true")
    c = cmain.Coordinator(state)
    try:
        f = Finding("O2|a.py::charge|L|R", "O2", "medium", ("L", "R"), symbol="a.py::charge", file="a.py",
                    detail={"symbol": "a.py::charge"})
        c._together_jobs(rid, [f])
        assert c._together_due == {}
        c.deliverer.push_lines(c.sessions, [{"rule": "O6", "changer": "R", "symbol": "a.py::charge",
                                             "detail": {"left": [{"symbol": "b.py::pay", "file": "b.py"}]}}])
        assert list((Path(state.root) / "push").iterdir()) == []
    finally:
        c.close()


@pytest.mark.parametrize("agent", NO_COPY)
def test_refused_by_handoff_and_the_dashboard_claim_form(state, repo, srv, agent):  # noqa: F811
    put(state, repo, "R", agent)
    with pytest.raises(handoff.Refused):
        handoff.pick_session(state, "R", str(repo))
    code, out = act(srv, "/api/claims", {"session": "R", "target": "a.py"})
    assert code == 404 and "no live session" in out["error"]


@pytest.mark.parametrize("agent", REMOTE)
def test_not_among_the_live_sessions_here(state, repo, agent):
    put(state, repo, "R", agent)
    put(state, repo, "L", "claude")
    assert [s["id"] for s in cli._live_sessions(state)[0]] == ["L"]
    [r] = status.collect(state)["repositories"]
    assert [s["id"] for s in r["sessions"]] == ["L"]
    assert [s["id"] for s in r["cloud"]["branches"]] == (["R"] if agent == "cloud" else [])


@pytest.mark.parametrize("agent", NO_NOTES)
def test_gets_no_notes(state, repo, agent):
    put(state, repo, "R", agent)
    put(state, repo, "L", "claude")
    rec = Record(state.record)
    try:
        sessions = {s["id"]: s for s in rec.sessions()}
        d = Deliverer(state, rec, None)
        for finding in (
                {"key": "O2|a.py::charge|L|R", "rule": "O2", "strength": "medium", "symbol": "a.py::charge",
                 "file": "a.py", "changer": "", "receiver": "", "sessions": ["L", "R"],
                 "detail": {"symbol": "a.py::charge"}},
                {"key": "O3|a.py::charge|L|R|b.py", "rule": "O3", "strength": "strong", "symbol": "a.py::charge",
                 "file": "b.py", "changer": "L", "receiver": "R", "sessions": ["L", "R"],
                 "detail": {"symbol": "a.py::charge", "reasons": ["parameter currency removed"], "latent": False,
                            "uses": [{"symbol": "b.py::pay", "lines": [5]}], "kind": "modified", "moved_to": ""}},
                {"key": "O6|a.py::charge|R", "rule": "O6", "strength": "strong", "symbol": "a.py::charge",
                 "file": "a.py", "changer": "R", "receiver": "", "sessions": ["R"],
                 "detail": {"symbol": "a.py::charge", "reasons": ["parameter currency removed"],
                            "left": [{"symbol": "b.py::pay", "file": "b.py", "lines": [5]}]}}):
            assert [p for p in d.plan(finding, sessions, wake=True) if p[0] == "R"] == []
    finally:
        rec.close()


def test_the_kinds():
    from orchestraty import sessionkinds
    assert sessionkinds.NO_NOTES == ("git", "cloud", "team")
    assert sessionkinds.NO_COPY == sessionkinds.REMOTE == ("cloud", "team")
    assert (sessionkinds.LEVEL0, sessionkinds.CLOUD, sessionkinds.TEAM) == ("git", "cloud", "team")
