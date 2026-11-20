"""Delivery across people. A teammate's branch gets no notes: their own machine tells their
sessions. A breaking change read from their commits stops an edit under steer and wakes a
session waiting for its user; one their Orchestraty only reports (from commits not on the
remote) does neither. A teammate's claim only informs: never an ask and never a stop, told
once, and taken back when it goes. The push line names the teammates' branches that use what
this branch changed."""
import json
import os
from pathlib import Path

import pytest

from orchestraty import claims, config, remote
from orchestraty.capture.hook import take_slot
from orchestraty.coordinator.record import Record
from orchestraty.coordinator.team import session_id
from orchestraty.delivery import deliver, slots
from orchestraty.delivery import notes as templates
from orchestraty.paths import State
from orchestraty.rules.overlap import Finding
from team_harness import BILLING, NS, run_git, team  # noqa: F401  (fixtures)

T, T2 = "team-3fa94c2b1d0e", "team-0b1d77c2e4aa"
LOCAL = {"agent": "claude", "repo_id": 1, "cwd": "/w/wt-b", "worktree": "/w/wt-b", "branch": "feat/b", "base_commit": "c",
         "base_guessed": 0, "started": 5, "last_seen": 5, "state": "live", "turn": "", "note": ""}
UNTIL = 1_795_008_000 * NS
TAX = BILLING.replace("currency)", "currency, tax)")
REFUNDS = "from pkg.billing import charge\n\n\ndef refund(amount):\n    return charge(-amount, 'EUR')\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def teammate(rec, sid, people, branch, own=False, as_of=1_795_004_400):
    """A teammate's branch as the coordinator keeps it: a session of agent team and its row."""
    rec.put_session(dict(LOCAL, id=sid, agent="team", cwd="", worktree="", branch=f"origin/{branch}", base_commit=""))
    rec.put_team_session({"session_id": sid, "repo_id": 1, "remote": "origin", "branch": branch, "people": people,
                          "own": int(own), "writers": [], "evidence": "commits", "tip": "abc", "tip_time": as_of,
                          "as_of": as_of, "agents": ["claude"], "task": {}, "merged": "", "contained_in": [],
                          "builds_on": []})


@pytest.fixture
def world(state):
    """B1, a Claude Code session here, and T, asha's feat/refunds."""
    rec = Record(state.record)
    rec.put_session(dict(LOCAL, id="B1"))
    teammate(rec, T, ["asha"], "feat/refunds")
    try:
        yield rec, deliver.Deliverer(state, rec, None, now_ns=lambda: 10 * NS)
    finally:
        rec.close()


def sessions(rec):
    return {s["id"]: s for s in rec.sessions()}


def o3(changer, receiver, evidence="commits", strength="strong"):
    detail = {"uses": [{"symbol": "c.py::use", "lines": [5]}], "reasons": ["new required parameter tax"], "latent": False}
    if changer.startswith("team-"):
        detail["evidence"] = evidence
    return Finding(f"O3|a.py::charge|{changer}|{receiver}|c.py", "O3", strength, tuple(sorted((changer, receiver))),
                   changer=changer, receiver=receiver, symbol="a.py::charge", file="c.py", detail=detail)


def claim(kind="area", target="billing", hard=1, key="tclaim|00000000000000aa"):
    return {"kind": kind, "target": target, "file": "", "hard": hard, "since": 5 * NS, "until": UNTIL, "note_key": key}


def slot_out(path):
    return json.loads(Path(path).read_bytes())["hookSpecificOutput"]


def test_team_sessions_get_no_notes(world):
    rec, d = world
    teammate(rec, T2, ["bob"], "feat/invoices")
    s = sessions(rec)
    planned = d.plan(o3("B1", T).as_dict(), s, wake=True)
    assert [(sid, kind) for sid, kind, _, _ in planned] == [("B1", "next")]        # only the changer, here
    assert planned[0][3] == (
        "Orchestraty: charge in a.py, which you changed (new required parameter tax), is used in c.py by teammate asha (branch "
        "feat/refunds): use (line 5). Orchestraty on their machine tells their sessions once your change is pushed; if "
        "the old form must stay, mention it to your user. Static calls only; dynamic calls are not analysed.")
    for rule in ("O2", "O5", "O7"):
        f = Finding(f"{rule}|a.py::charge|B1|{T}", rule, "medium", ("B1", T), symbol="a.py::charge", file="a.py",
                    detail={"how": {}, "changes": {}})
        assert [sid for sid, *_ in d.plan(f.as_dict(), s)] == ["B1"]
    rec.put_team_planned(T2, [claim()])
    assert {sid for sid, *_ in d.plan_team_claim(rec.team_planned(session_id=T2)[0], s)} == {"B1"}


@pytest.mark.parametrize("evidence, strength, decision", [("commits", "strong", "deny"), ("record", "medium", None)])
def test_under_steer_a_commits_team_note_denies_the_edit_and_a_record_one_does_not(world, state, evidence, strength,
                                                                                   decision):
    rec, d = world
    f = o3(T, "B1", evidence=evidence, strength=strength)
    rec.put_finding(1, f, 10)
    assert d.add(f.as_dict(), sessions(rec), 10) == 2                             # the edit note and the next note
    assert d.fill(steer=True)
    out = slot_out(slots.edit_slot(state, "B1", os.path.join("/w/wt-b", "c.py")))
    assert out.get("permissionDecision") == decision
    text = out.get("permissionDecisionReason") or out["additionalContext"]
    assert text.startswith("Orchestraty: teammate asha (branch feat/refunds) changed the contract of charge in a.py")


def test_a_wake_note_only_for_commits_evidence(world):
    rec, d = world
    rec.put_session(dict(LOCAL, id="B1", turn="idle"))                             # waiting for its user
    rec.put_session(dict(LOCAL, id="A1", cwd="/w/wt-a", worktree="/w/wt-a"))
    s = sessions(rec)

    def kinds(f):
        return sorted(kind for sid, kind, _, _ in d.plan(f.as_dict(), s, wake=True) if sid == "B1")

    assert kinds(o3(T, "B1")) == ["edit", "next", "wake"]
    assert kinds(o3(T, "B1", evidence="record", strength="medium")) == ["edit", "next"]
    assert kinds(o3("A1", "B1")) == ["edit", "next", "wake"]                      # a local changer: as before


def test_two_team_branches_with_the_same_change_give_one_note_per_rule_symbol_and_file(world):
    rec, d = world
    teammate(rec, T2, ["bob"], "feat/refunds-2")                                   # a fork of the same tip
    s = sessions(rec)
    assert sum(d.add(o3(sid, "B1").as_dict(), s, 10) for sid in (T, T2)) == 2     # one edit note, one next note
    assert sorted(n["kind"] for n in rec.notes(session_id="B1")) == ["edit", "next"]


def test_a_teammates_hard_claim_never_asks_even_for_claude_code(world, state):
    rec, d = world
    rec.put_team_planned(T, [claim(hard=1)])
    item = rec.team_planned(session_id=T)[0]
    s = sessions(rec)
    [(sid, kind, slot, text)] = d.plan_team_claim(item, s)
    assert (sid, kind) == ("B1", "edit")
    assert slot == slots.area_slot(state, "B1", os.path.join("/w/wt-b", "billing"), ask=False)
    assert text.startswith("Orchestraty: teammate asha (branch feat/refunds) has claimed the folder billing/ until ")
    assert "shared through git at" in text and "leave it to them, or ask your user before you change it." in text
    assert d.add_team_claim(item, s) == 1
    assert d._deciders(rec.notes(session_id="B1"), steer=True) == (set(), set())
    assert d.fill(steer=True)
    out = slot_out(slot)
    assert "permissionDecision" not in out and out["additionalContext"] == text   # neither an ask nor a deny


def test_team_claim_notes_are_told_once_and_withdrawn_when_the_item_goes(world):
    rec, d = world
    one = claim(kind="file", target="billing/charge.py", hard=0)
    changed = {"B1": ["billing/charge.py"]}

    def held():
        return {i["note_key"] for i in rec.team_planned(active_at=10 * NS)}

    rec.put_team_planned(T, [one])
    s = sessions(rec)
    assert d.add_team_claim(rec.team_planned(session_id=T)[0], s, changed) == 2   # before an edit, and with its next step
    (after,) = [n for n in rec.notes(session_id="B1") if n["kind"] == "next"]
    assert after["text"].endswith("You have changed it in your worktree: ask your user before you go on.")
    rec.put_team_planned(T, [one])                                                 # their record, read again
    d.withdraw_claims(set(), held())
    assert d.add_team_claim(rec.team_planned(session_id=T)[0], s, changed) == 0
    assert {n["state"] for n in rec.notes(session_id="B1")} == {"pending"}
    rec.put_team_planned(T, [])                                                    # gone from their record
    d.withdraw_claims(set(), held())
    assert {n["state"] for n in rec.notes(session_id="B1")} == {"withdrawn"}
    assert d.add_team_claim(dict(one, session_id=T), s, changed) == 2             # back again: told again


def test_a_taken_team_claim_note_is_not_told_again_on_re_read(world, state):
    rec, d = world
    rec.put_team_planned(T, [claim()])
    s = sessions(rec)
    assert d.add_team_claim(rec.team_planned(session_id=T)[0], s) == 1
    assert d.fill()
    take_slot(slots.area_slot(state, "B1", os.path.join("/w/wt-b", "billing")))
    d.fill()                                                                       # the slot is gone: delivered
    rec.put_team_planned(T, [claim()])
    d.withdraw_claims(set(), {"tclaim|00000000000000aa"})
    assert d.add_team_claim(rec.team_planned(session_id=T)[0], s) == 0
    assert [n["state"] for n in rec.notes(session_id="B1")] == ["delivered"]


O6 = [{"rule": "O6", "symbol": "a.py::charge", "changer": "B1",
       "detail": {"left": [{"symbol": "b.py::pay"}, {"symbol": "c.py::refund"}]}}]
ASHA = {"id": T, "agent": "team", "branch": "origin/feat/refunds", "people": ["asha"], "own": False}


def test_push_line_texts():
    use = [(o3("B1", T).as_dict(), ASHA)]
    assert templates.push_line(O6) == ("orchestraty: 2 caller(s) of charge still use its old form (pay, refund). "
                                       "Run: orch status")
    assert templates.push_line(O6, []) == templates.push_line(O6)
    assert templates.push_line([], use) == ("orchestraty: charge, which this branch changed, is used on 1 teammate's "
                                            "branch (asha: feat/refunds). Run: orch status")
    assert templates.push_line(O6, use) == ("orchestraty: 2 caller(s) of charge still use its old form (pay, refund); it "
                                            "is also used on 1 teammate's branch (asha: feat/refunds). Run: orch status")
    two = use + [(o3("B1", T2).as_dict(), dict(ASHA, id=T2, people=["bob", "chen"], branch="origin/feat/x"))]
    assert templates.push_line([], two) == ("orchestraty: charge, which this branch changed, is used on 2 teammates' "
                                            "branches (asha: feat/refunds, bob and chen: feat/x). Run: orch status")
    mine = [(o3("B1", T2).as_dict(), dict(ASHA, id=T2, people=[], own=True, branch="origin/feat/x"))]
    assert templates.push_line([], mine) == ("orchestraty: charge, which this branch changed, is used on your branch "
                                             "feat/x on another machine. Run: orch status")
    many = [(o3("B1", f"team-{i:012x}").as_dict(),
             dict(ASHA, id=f"team-{i:012x}", people=["p" * 39 + str(i)], branch="origin/feat/" + "x" * 70))
            for i in range(30)]
    line = templates.push_line(O6, many)
    assert len(line) <= 400 and line.startswith("orchestraty: 2 caller(s) of charge")


def test_the_push_line_names_the_teammates_branches_that_use_this_branchs_change(world, state):
    rec, d = world
    f = o3("B1", T)
    rec.put_finding(1, f, 10)
    d.push_lines(sessions(rec), rec.findings())
    path = os.path.join(state.root, "push", slots.file_key("/w/wt-b") + ".txt")
    assert Path(path).read_text(encoding="utf-8") == (
        "orchestraty: charge, which this branch changed, is used on 1 teammate's branch (asha: feat/refunds). "
        "Run: orch status\n")
    latent = Finding(f.key.replace("c.py", "d.py"), "O3", "strong", f.sessions, changer="B1", receiver=T,
                     symbol="a.py::charge", file="d.py", detail=dict(f.detail, latent=True))
    rec.resolve_finding(f.key, 20)
    rec.put_finding(1, latent, 20)                                                 # not a use anyone is changing
    d.push_lines(sessions(rec), rec.findings())
    assert not os.path.exists(path)


def common(machine):
    return str(machine.clone / ".git")


def b1_notes(machine, states):
    rec = Record(machine.state.record, readonly=True)
    try:
        return [n for n in rec.notes(session_id="B1", states=states) if n["finding_key"].startswith("O3|")]
    finally:
        rec.close()


@pytest.mark.parametrize("taken", [False, True])
def test_a_change_seen_as_a_cloud_branch_then_as_a_teammates_is_told_once(team, taken):  # noqa: F811
    alice, bob = team.alice, team.bob
    remote.save_settings(bob.state, common(bob), on=True, remote="origin", patterns=["claude/*"])
    alice.team_on()
    bob.team_on()
    bob.start("B1", "feat/refunds")
    bob.settle()
    bob.edit("B1", "pkg/refunds.py", REFUNDS)
    bob.settle()
    alice.push_work("A1", "claude/fix-tax", "pkg/billing.py", TAX)                # a cloud agent's name, alice's work
    chosen = remote.settings(config.load(bob.state), common(bob))
    assert remote.fetch_now(bob.state, common(bob), chosen, 300)["ok"]
    bob.settle()
    first = b1_notes(bob, ("pending", "written"))
    assert first and all("agent on that branch works elsewhere" in n["text"] for n in first)   # told as a cloud branch
    if taken:
        edit = next(n for n in first if n["kind"] == "edit")
        take_slot(os.path.join(bob.state.root, *edit["slot"].split("/")))
        bob.settle()
    team.clock.advance(150)                                                        # the team fetch: alice's record
    bob.settle()
    t = session_id(common(bob), "origin", "claude/fix-tax")
    waiting = b1_notes(bob, ("pending", "written"))
    if taken:
        assert waiting == [] and b1_notes(bob, ("delivered",))                     # not told twice
    else:
        assert {n["finding_key"].split("|")[2] for n in waiting} == {t}            # the cloud one's notes withdrawn
        assert all(n["text"].startswith("Orchestraty: teammate alice (branch claude/fix-tax) changed the contract")
                   for n in waiting)


CLAIMS = "version: 1\nsync: on\nvisibility: private\nshare:\n  claims: true\n"


def tclaim_notes(machine, states=("pending", "written", "delivered")):
    rec = Record(machine.state.record, readonly=True)
    try:
        return [n for n in rec.notes(session_id="B1", states=states) if n["finding_key"].startswith("tclaim|")]
    finally:
        rec.close()


def test_a_teammates_claim_is_told_here_once_and_taken_back_when_its_time_runs_out(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    (alice.clone / ".orchestraty" / "policy.yml").write_text(CLAIMS, encoding="utf-8")
    run_git(alice.clone, "commit", "-q", "-a", "-m", "share claims")
    run_git(alice.clone, "push", "-q", "origin", "HEAD:main")
    alice.team_on()
    bob.team_on()
    bob.start("B1", "feat/refunds")
    bob.settle()
    alice.start("A1", "feat/x")
    alice.settle()
    claims.append_claim(alice.state, "A1", common(alice), "area", "pkg", "", True, 10, now=team.clock() / NS)
    for text in (TAX, TAX + "\n\nRATE = 1\n"):                                  # two pushes: her record, sent twice
        alice.edit("A1", "pkg/billing.py", text)
        alice.commit("A1", "tax")
        alice.push("A1", "-u")
        alice.settle()
        team.clock.advance(3)
        alice.settle()
        team.clock.advance(150)                                                    # bob's team fetch
        bob.settle()
        (note,) = tclaim_notes(bob)                                                # told once, whatever the reads
        assert note["kind"] == "edit" and note["slot"].startswith("notes/B1/area/") and ".ask." not in note["slot"]
        assert note["text"].startswith("Orchestraty: teammate alice (branch feat/x) has claimed the folder pkg/ until ")
    team.clock.advance(600)                                                        # the claim's ten minutes ran out
    bob.settle()
    assert bob.coordinator().sessions["B1"]["state"] == "live"
    assert [n["state"] for n in tclaim_notes(bob, states=None)] == ["withdrawn"]
