"""Where team sync shows: orch team push, fetch, log and off --forget by hand; orch status's team
block with teammates' branches and writers; the MCP status tool, cleaned; the doctor's checks for
how pushes are seen and where records go; and the mod naming teammates' and cloud sessions. A
solo user's status is as it was."""
import json
import re
import shutil
import subprocess
import time
import types
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import config, remote, status
from orchestraty.mcp import tools
from orchestraty.team import checks, commands, teamfile
from team_harness import BILLING, NS, publish_raw, run_git, team  # noqa: F401  (fixtures)

pytestmark = pytest.mark.heavy        # git-heavy: the quick Windows run in CI leaves it to the full run

TAX = BILLING.replace("currency)", "currency, tax)")
REGISTER = Path(__file__).resolve().parents[1] / "orchestraty" / "mod" / "hooks" / "register.js"
CONFIRMED = "version: 1\nsync: on\nvisibility: private\nshare:\n  task: confirmed\n"


@pytest.fixture
def state(state_root):
    from orchestraty.paths import State
    return State(str(state_root))


def yes(question, assume_yes):
    return True


def no(question, assume_yes):
    return False


def run(action, machine, confirm=yes, **options):
    args = types.SimpleNamespace(action=action, repo=str(machine.clone), **options)
    return commands.ACTIONS[action](machine.state, args, confirm)


def common(machine):
    return str(machine.clone / ".git")


def ref_of(team, machine):  # noqa: F811
    return run_git(team.remote, "for-each-ref", "--format=%(objectname)",
                   f"refs/orchestraty/{machine.name}/{machine.device()['id']}")


def push_policy(machine, text):
    (machine.clone / ".orchestraty" / "policy.yml").write_text(text, encoding="utf-8")
    run_git(machine.clone, "add", ".orchestraty/policy.yml")
    run_git(machine.clone, "commit", "-q", "-m", "the team's policy")
    run_git(machine.clone, "push", "-q", "origin", "HEAD:main")


def test_outgoing_preview_matches_next_push_bytes(team, capsys, monkeypatch):  # noqa: F811
    alice = team.alice
    team.clock.advance(60 - (team.clock() // NS) % 60)                             # the start of a minute
    alice.team_on()
    alice.start("A1", "feat/x")
    alice.settle()
    alice.edit("A1", "pkg/billing.py", TAX)
    alice.commit("A1", "tax")
    alice.push("A1", "-u")
    alice.settle()                                                                 # the push seen: its record goes in 3 s
    monkeypatch.setattr(time, "time", lambda: team.clock() / NS)
    capsys.readouterr()
    assert run("preview", alice, json=True) == 0
    previewed = json.loads(capsys.readouterr().out)["bytes"]
    team.clock.advance(3)
    alice.settle()
    tip = ref_of(team, alice)
    assert run_git(team.remote, "cat-file", "blob", f"{tip}:record.json") + "\n" == previewed


def test_status_lists_teammates_branches_and_writers(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    team.clock.advance(150)
    bob.settle()
    data = status.collect(bob.state)
    (repo,) = data["repositories"]
    t = repo["team"]
    (s,) = t["sessions"]
    assert (s["agent"], s["branch"], s["worktree"], s["state"]) == ("team", "origin/feat/x", "", "live")
    assert s["team"]["people"] == ["alice"] and s["team"]["own"] is False and s["team"]["evidence"] == "commits"
    assert s["changes"]["files"] == {"pkg/billing.py": "M"}
    assert t["store"] == "ref" and t["fetch"]["ok"] is True and t["fetch"]["writers"] == 1
    assert [w["person"] for w in t["writers"]] == ["alice"]
    assert all(x["agent"] != "team" for x in repo["sessions"])                     # not one of the sessions here
    text = status.render(data)
    assert "teammates' branches from origin" in text
    assert re.search(r"    alice  feat/x  last commit .+ ago", text)
    assert "contracts: charge [breaking" in text


def test_status_json_unchanged_for_a_solo_user(state, repo, monkeypatch):
    config.watch(state, str(repo / ".git"))
    spool_event(state.root, "s1", "SessionStart", repo)
    from orchestraty.coordinator.main import Coordinator
    c = Coordinator(state)
    try:
        c.step()
        c.write_status(force=True)
    finally:
        c.close()
    data = json.loads(Path(state.status).read_text(encoding="utf-8"))
    assert all("team" not in r for r in data["repositories"])
    assert not (Path(state.root) / "team").exists()
    assert not list((Path(state.root) / "shadows").glob("*.team.json")) if (Path(state.root) / "shadows").exists() \
        else True
    assert c.team is None                                                          # no team sync made, no thread


def test_mcp_status_never_shows_a_task_line(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    push_policy(alice, CONFIRMED)
    run_git(bob.clone, "pull", "-q", "origin", "main")
    alice.team_on()
    bob.team_on()
    alice.start("A1", "feat/x")
    alice.settle()
    assert run("task", alice, session="A1", ticket=None, line="Refund flow for annual plans", from_declared=False,
               clear=False) == 0
    alice.edit("A1", "pkg/billing.py", TAX)
    alice.commit("A1", "tax")
    alice.push("A1", "-u")
    alice.settle()
    team.clock.advance(3)
    alice.settle()
    team.clock.advance(150)
    top = bob.start("B1", "feat/y")
    bob.settle()
    data, text = tools.call(bob.state, str(top), "status", {})
    assert "Refund flow" not in json.dumps(data) + text
    assert "Refund flow" in status.render(status.collect(bob.state, with_lines=True), tty=True)   # people only


def doctor_lines(machine):
    lines = []
    for item in checks.checks(machine.state, str(machine.clone),
                              lambda m: ("ok", m), lambda m, fix="": ("problem", m, fix), lambda m: ("note", m)):
        lines.append(item)
    return lines


def test_doctor_reports_reflog_off_and_branch_store(team):  # noqa: F811
    alice = team.alice
    alice.team_on()
    run_git(alice.clone, "config", "core.logAllRefUpdates", "false")
    teamfile.update(remote.Shadow(alice.state, common(alice)), store="branch")
    found = doctor_lines(alice)
    text = json.dumps(found)
    assert any(k == "note" and "reflog" in m and "remote-tracking" in m for k, m, *_ in found), text
    assert any(k == "note" and "orchestraty/records" in m and "branches-ignore" in m for k, m, *_ in found), text


def test_off_forget_asks_and_deletes_own_ref_with_lease(team, capsys):  # noqa: F811
    alice = team.alice
    alice.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    mine = ref_of(team, alice)
    assert mine
    assert run("off", alice, confirm=no, forget=True, yes=False) == 1                # asked, and said no
    assert ref_of(team, alice) == mine
    alice.team_on()
    device = alice.device()["id"]
    other = publish_raw(team.remote, "alice", device, b'{"format":1}\n', "alice@example.com")   # written over since
    with pytest.raises(commands.Refused, match="not deleted"):
        run("off", alice, forget=True, yes=True)
    assert ref_of(team, alice) == other                                            # the lease kept it
    run_git(team.remote, "update-ref", f"refs/orchestraty/alice/{device}", mine)
    assert run("off", alice, forget=True, yes=True) == 0
    assert ref_of(team, alice) == ""


def test_push_fetch_and_log_by_hand(team, capsys):  # noqa: F811
    alice = team.alice
    alice.team_on()
    alice.start("A1", "feat/x")
    alice.settle()
    assert run("fetch", alice) == 0
    assert run("push", alice) == 0                                                 # now, without a git push
    assert ref_of(team, alice) != ""
    capsys.readouterr()
    assert run("log", alice) == 0
    out = capsys.readouterr().out
    assert "seq 1" in out and "pushed" in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_mod_who_names_team_and_cloud_sessions(tmp_path):
    source = REGISTER.read_text(encoding="utf-8")
    parts = [re.search(r"^function " + name + r"\(.*?^}\n", source, re.S | re.M).group(0)
             for name in ("baseName", "who")]
    repo = {"sessions": [{"id": "A1", "agent": "claude", "worktree": "/w/wt-a"}],
            "cloud": {"branches": [{"id": "cloud-1", "agent": "cloud", "branch": "origin/claude/fix-tax"}]},
            "team": {"sessions": [{"id": "team-1", "agent": "team", "branch": "origin/feat/x",
                                   "team": {"people": ["alice", "bob"], "own": False}},
                                  {"id": "team-2", "agent": "team", "branch": "origin/feat/y",
                                   "team": {"people": [], "own": True}}]}}
    script = "".join(parts) + f"const repo = {json.dumps(repo)};\n" + \
        "console.log(JSON.stringify(['A1', 'cloud-1', 'team-1', 'team-2', 'gone-123456789'].map((s) => who(repo, s))))\n"
    path = tmp_path / "who.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == ["claude in wt-a", "the cloud branch origin/claude/fix-tax",
                                       "teammates alice and bob (branch origin/feat/x)",
                                       "your work on another machine (branch origin/feat/y)", "gone-123"]


def test_uninstall_asks_before_deleting_this_devices_record_ref(team):  # noqa: F811
    from orchestraty import uninstall
    alice = team.alice
    alice.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)
    mine = ref_of(team, alice)
    steps, _ = uninstall.plan(alice.state)
    (forget,) = [s for s in steps if s.kind == "forget"]
    assert "only what this device wrote" in forget.text
    said = uninstall.do(alice.state, forget, lambda question: False, keep_backups=False)
    assert said.startswith("Kept") and ref_of(team, alice) == mine
    said = uninstall.do(alice.state, forget, lambda question: True, keep_backups=False)
    assert said.startswith("Deleted") and ref_of(team, alice) == ""
