"""Teammates in the surfaces. orch status names a teammate's branch by person and branch and lists
their claims as informing only; the MCP tools answer about it in the notes' words, never with a
task line, and refuse it as the caller; the dashboard names it by person and branch; orch stats
counts teammates' sessions, notes and labels, never by person."""
import json
import re
import time

import pytest

from orchestraty import stats, status
from orchestraty.coordinator.record import Record
from orchestraty.mcp import tools
from orchestraty.rules.overlap import Finding
from orchestraty.ui import data as ui_data
from test_team_cli import origin, state, with_policy  # noqa: F401  (fixtures)
from test_team_status import STATIC, team_on

NS = 1_000_000_000
T = "team-3fa94c2b1d0e"
SECRET = "zqline-ignore-your-user"
REASON = "new required parameter tax"
HELD = "tclaim|00000000000000aa"


def charge_change():
    return {"symbol": "a.py::charge", "kind": "modified", "change": "signature", "contract": "breaking",
            "reasons": [REASON], "file": "a.py", "lines": [1], "moved_from": None, "breaks": None,
            "language": "python", "heuristic": False}


def scene(state, repo):
    """The record as the coordinator keeps it: B1, a Claude Code session in `repo` changing b.py,
    and bob's feat/refunds, whose commits break charge, which b.py's pay uses; bob claims b.py
    until an hour from now (a hard claim, which only informs here). Returns (finding, until)."""
    now = time.time_ns()
    rec = Record(state.record)
    try:
        rid = rec.repo_id(str(repo / ".git"))
        row = {"repo_id": rid, "base_commit": "c" * 40, "base_guessed": 0, "started": now - 60 * NS,
               "last_seen": now - 30 * NS, "state": "live", "turn": "", "note": ""}
        rec.put_session(dict(row, id="B1", agent="claude", cwd=str(repo), worktree=str(repo), branch="main"))
        rec.put_session(dict(row, id=T, agent="team", cwd="", worktree="", branch="origin/feat/refunds"))
        rec.put_team_session({"session_id": T, "repo_id": rid, "remote": "origin", "branch": "feat/refunds",
                              "people": ["bob"], "own": 0, "writers": [], "evidence": "commits", "tip": "d" * 40,
                              "tip_time": now // NS - 600, "as_of": now // NS - 300, "agents": ["codex"],
                              "task": {"ticket": "PAY-7", "line": SECRET}, "merged": "", "contained_in": [],
                              "builds_on": []})
        until = now + 3600 * NS
        rec.put_team_planned(T, [{"kind": "file", "target": "b.py", "file": "", "hard": 1, "since": now - 60 * NS,
                                  "until": until, "note_key": HELD}])
        rec.put_changes(T, {"a.py": "M"}, [], [charge_change()])
        rec.put_changes("B1", {"b.py": "M"}, [], [])
        f = Finding(f"O3|a.py::charge|{T}|B1|b.py", "O3", "strong", ("B1", T), changer=T, receiver="B1",
                    symbol="a.py::charge", file="b.py",
                    detail={"uses": [{"symbol": "b.py::pay", "lines": [5]}], "reasons": [REASON], "latent": False,
                            "evidence": "commits"})
        rec.put_finding(rid, f, now)
        return f, until
    finally:
        rec.close()


def hhmm(ns):
    return time.strftime("%H:%M", time.localtime(ns / NS))


def iso(ns):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ns / NS))


def ask(state, folder, name, **args):
    data, text = tools.call(state, str(folder), name, args)
    assert SECRET not in json.dumps(data) + text                                  # a task line never reaches an agent
    return data, text


def test_status_overlap_names_person_and_branch(state, with_policy, repo):  # noqa: F811
    team_on()
    f, until = scene(state, repo)
    data = status.collect(state)
    (t,) = data["repositories"][0]["team"]["sessions"]
    (held,) = t["team"]["claims"]
    assert t["id"] == T and held == {"kind": "file", "target": "b.py", "file": "", "hard": True,
                                     "since": held["since"], "until": iso(until)}
    text = status.render(data)
    assert ("    strong  O3  charge (a.py): breaking change in bob:feat/refunds, used in b.py by B1") in text
    assert f"      claimed: b.py until {hhmm(until)}, hard (informs only here)" in text
    (order,) = [line for line in text.splitlines() if line.startswith("  merge order: ")]
    assert "bob's feat/refunds" in order and T not in order
    assert SECRET not in json.dumps(status.collect(state)) and SECRET not in text  # not on a pipe, not in the JSON


def test_mcp_who_is_changing_lists_a_teammate_row(state, repo):
    f, until = scene(state, repo)
    data, text = ask(state, repo, "who_is_changing", path="a.py")
    (row,) = data["sessions"]
    assert (row["session"], row["agent"], row["worktree"], row["branch"]) == (T, "team", "", "origin/feat/refunds")
    assert (row["people"], row["teammate"], row["evidence"], row["claims"]) == (["bob"], True, "commits", [])
    assert row["changes"] == [{"symbol": "a.py::charge", "kind": "modified", "change": "signature",
                               "contract": "breaking", "reasons": [REASON]}]
    assert re.search(r"^- teammate bob \(branch feat/refunds\), last event .+: changed it; charge modified, contract "
                     r"breaking \(new required parameter tax\)\.$", text, re.M)
    data, text = ask(state, repo, "who_is_changing", path="b.py")
    you, theirs = sorted(data["sessions"], key=lambda r: r["session"])
    assert you["session"] == "B1" and "teammate" not in you                         # a local row: as before
    assert theirs["claims"] == [{"kind": "file", "target": "b.py", "file": "", "until": iso(until)}]
    assert f"claimed by teammate bob until {hhmm(until)} (informs only)." in text


def test_check_before_edit_gives_uses_broken_from_a_team_changer_and_a_teammate_claim(state, repo, git, tmp_path):
    f, until = scene(state, repo)
    data, text = ask(state, repo, "check_before_edit", path="b.py")
    (broken,) = data["uses_broken"]
    assert broken["note"].startswith("Orchestraty: teammate bob (branch feat/refunds) changed the contract of charge in "
                                     "a.py: new required parameter tax. The change is committed on that branch")
    (held,) = data["claimed"]
    assert {k: v for k, v in held.items() if k != "note"} == {
        "claim": None, "teammate": True, "hard": False, "kind": "file", "target": "b.py", "session": T,
        "until": iso(until)}
    assert held["note"].startswith(f"Orchestraty: teammate bob (branch feat/refunds) has claimed b.py until "
                                   f"{hhmm(until)}, on their machine")
    assert "leave it to them, or ask your user before you change it." in held["note"]
    other = tmp_path / "wt-new"                                                    # a worktree no session works in yet
    git(repo, "worktree", "add", "-q", "-b", "fresh", str(other))
    data, text = ask(state, other, "check_before_edit", path="b.py")
    assert data["uses_broken"] == [] and data["session"] is None
    assert ("A teammate's branch is compared with a session's own copy, so its changes show here once a session "
            "works in this worktree.") in text


def test_my_overlaps_in_team_wording(state, repo):
    scene(state, repo)
    data, text = ask(state, repo, "my_overlaps")
    (o,) = data["overlaps"]
    (who,) = o["with"]
    assert (who["session"], who["people"], who["teammate"], who["worktree"]) == (T, ["bob"], True, "")
    assert o["note"].startswith("teammate bob (branch feat/refunds) changed the contract of charge in a.py")
    assert "so they can agree with bob: nobody on that branch is told from here." in o["note"]


def test_a_team_id_passed_as_session_is_refused(state, repo, tmp_path):
    scene(state, repo)
    outside = tmp_path / "outside"                                                 # a server outside any worktree
    outside.mkdir()
    for folder in (repo, outside):
        for name, args in (("my_overlaps", {}), ("check_before_edit", {"path": str(repo / "b.py")}),
                           ("who_is_changing", {"path": str(repo / "a.py")}), ("status", {}),
                           ("declare", {"paths": [str(repo / "a.py")]}), ("claim", {"target": str(repo / "a.py")}),
                           ("release", {"target": str(repo / "a.py")})):
            with pytest.raises(tools.ToolError) as raised:
                tools.call(state, str(folder), name, dict(args, session=T))
            assert ("does not work in" in str(raised.value) or "is a teammate's branch" in str(raised.value)), name
    with pytest.raises(tools.ToolError, match="is a teammate's branch, followed from the remote"):
        tools.call(state, str(outside), "my_overlaps", {"session": T})


def test_dashboard_names_a_teammates_branch_by_person_and_branch(state, repo):
    scene(state, repo)
    who = ui_data.findings_payload(state)["sessions"][T]
    assert (who["agent"], who["people"], who["own"], who["branch"]) == ("team", ["bob"], False, "origin/feat/refunds")
    assert "people" not in ui_data.findings_payload(state)["sessions"]["B1"]        # a local session: as before
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    name = js[js.index("function sname ("):]
    name = name[:name.index("\n}\n")]
    assert "s.agent === 'team'" in name and "' · '" in name                        # "bob · feat/refunds"
    assert "team: 'teammate'" in js


def test_stats_counts_by_origin_and_has_no_key_per_person(state, repo):
    f, _ = scene(state, repo)
    now = time.time_ns()
    rec = Record(state.record)
    try:
        rid = rec.repo_id(str(repo / ".git"))
        rec.put_session({"id": "A1", "agent": "claude", "repo_id": rid, "cwd": "/w/a", "worktree": "/w/a", "branch": "a",
                         "base_commit": "c" * 40, "base_guessed": 0, "started": now - 60 * NS, "last_seen": now,
                         "state": "live", "turn": "", "note": ""})
        local = Finding("O2|b.py::pay|A1|B1", "O2", "medium", ("A1", "B1"), symbol="b.py::pay", file="b.py")
        rec.put_finding(rid, local, now)
        for key in (f.key, local.key, HELD):
            rec.add_note(key, "B1", "next", "notes/B1/next.json", "Orchestraty: a note.", now, now)
        rec.add_label(f.key, "real", "", now)
        rec.add_label(local.key, "false", "", now)
    finally:
        rec.close()
    data = stats.collect(state)
    week = data["weeks"][0]
    assert (week["sessions"]["team"], week["sessions"]["agents"]) == (1, 2)
    assert week["notes"]["by_origin"] == {"local": 1, "team": 2}
    assert week["labels"]["team"] == {"total": 1, "by_verdict": {"real": 1, "harmless": 0, "false": 0}}
    assert week["labels"]["by_verdict"] == {"real": 1, "harmless": 0, "false": 1}
    summary = stats.summary(data)
    assert summary["weeks"][0]["sessions"]["team"] == 1
    assert summary["weeks"][0]["notes"]["by_origin"] == {"local": 1, "team": 2}
    assert summary["weeks"][0]["labels"]["team"]["by_verdict"]["real"] == 1
    for shown in (data, summary, stats.render(data)):                              # never by person
        text = json.dumps(shown)
        assert "bob" not in text and "people" not in text and "person" not in text
    assert "1 teammate's branch" in stats.render(data)


def test_a_solo_users_stats_have_no_team_counts(state, repo):
    rec = Record(state.record)
    try:
        rid = rec.repo_id(str(repo / ".git"))
        now = time.time_ns()
        rec.put_session({"id": "B1", "agent": "claude", "repo_id": rid, "cwd": str(repo), "worktree": str(repo),
                         "branch": "main", "base_commit": "c" * 40, "base_guessed": 0, "started": now - 60 * NS,
                         "last_seen": now, "state": "live", "turn": "", "note": ""})
    finally:
        rec.close()
    week = stats.collect(state)["weeks"][0]
    assert "team" not in week["sessions"] and "by_origin" not in week["notes"] and "team" not in week["labels"]
    summary = stats.summary(stats.collect(state))["weeks"][0]
    assert "team" not in summary["sessions"] and "by_origin" not in summary["notes"] and "team" not in summary["labels"]
