"""orch team: turning team sync on and off for a repository (asking first), what it would share, and
the files it starts from. The team remote is a local bare repository: nothing leaves the machine."""
import json
import os
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import cli, config, team
from orchestraty.coordinator import main as cmain
from orchestraty.paths import State
from orchestraty.team import identity, policy, recordfile, seal

POLICY = """\
version: 1
sync: on
visibility: private
share:
  claims: true
  task: ticket
tickets:
  keys: [PAY]
"""
PEOPLE = "version: 1\npeople:\n  - handle: alice\n    emails: [alice@example.com]\n"


def token(salt=0):
    alnum = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "gh" + "p_" + "".join(alnum[(5 + salt + i * 11) % 62] for i in range(36))


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def commit_files(git, top, files, message="team files"):
    for rel, text in files.items():
        path = Path(top) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(top, "add", rel)
    git(top, "commit", "-q", "-m", message)


@pytest.fixture
def origin(state, repo, git, tmp_path, monkeypatch):
    """`repo`, watched, with a bare origin; alice is its git user. Returns the origin's path."""
    git(repo, "config", "user.email", "alice@example.com")
    bare = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    config.watch(state, str(repo / ".git"))
    monkeypatch.chdir(repo)
    return bare


@pytest.fixture
def with_policy(origin, repo, git):
    commit_files(git, repo, {".orchestraty/policy.yml": POLICY, ".orchestraty/people.yml": PEOPLE})
    git(repo, "push", "-q", "origin", "main")
    return origin


class Asked:
    """cli.confirm, answering from a list and keeping the questions."""

    def __init__(self, *answers):
        self.answers, self.questions = list(answers), []

    def __call__(self, question, assume_yes):
        self.questions.append(question)
        return True if assume_yes else self.answers.pop(0)


def entry(state, repo):
    return team.settings(config.load(state), str(repo / ".git"))


def test_team_on_refuses_without_policy(state, origin, repo, git, capsys, monkeypatch):
    assert cli.main(["team", "on", "--yes"]) == 2
    err = capsys.readouterr().err
    assert "there is no team policy on origin's default branch" in err and "orch team init" in err
    assert not entry(state, repo)["on"] and not Path(team.marker(state)).exists()
    commit_files(git, repo, {".orchestraty/policy.yml": POLICY.replace("version: 1", "version: 9")})
    git(repo, "push", "-q", "origin", "main")
    assert cli.main(["team", "on", "--yes"]) == 2
    assert "version 9, which this version does not know; fix: upgrade Orchestraty" in capsys.readouterr().err
    git(repo, "config", "--unset", "user.email")
    assert cli.main(["team", "on", "--yes"]) == 2
    assert "fix: git config user.email you@example.com" in capsys.readouterr().err
    assert not entry(state, repo)["on"]


def test_team_on_shows_share_set_and_asks(state, with_policy, repo, capsys, monkeypatch):
    assert cli.main(["team", "on"]) == 1                                   # no terminal: nothing asked, nothing changed
    assert "No terminal to ask in" in capsys.readouterr().out and not entry(state, repo)["on"]
    asked = Asked(True, False)
    monkeypatch.setattr(cli, "confirm", asked)
    assert cli.main(["team", "on"]) == 1
    out = capsys.readouterr().out
    assert asked.questions == ["Read them now?", "Share this with your teammates?"]
    assert "a read-only fetch" in out and "who you are: alice (in .orchestraty/people.yml)" in out
    assert "its live claims" in out and "the ticket in its branch's name (PAY)" in out
    assert "Not: task lines, unpushed work, activity, prompts" in out and "refs/orchestraty/alice/<this device>" in out
    assert not entry(state, repo)["on"] and identity.Device.load(state) is None
    monkeypatch.setattr(cli, "confirm", Asked(True, True))
    assert cli.main(["team", "on"]) == 0
    device = identity.Device.load(state)
    assert f"you are alice on device {device.id}" in capsys.readouterr().out
    chosen = entry(state, repo)
    assert chosen["on"] and chosen["remote"] == "origin" and chosen["consented"] == policy.parse(POLICY).normalised()
    assert Path(team.marker(state)).exists()
    assert cli.main(["team", "off"]) == 0 and not entry(state, repo)["on"]
    monkeypatch.setattr(cli, "confirm", Asked())
    assert cli.main(["team", "on", "--yes"]) == 0 and entry(state, repo)["on"]          # --yes asks nothing
    assert identity.Device.load(state) == device                                        # the device is made once


def test_consent_pinned_and_widening_re_asks(state, with_policy, repo, git, capsys, monkeypatch):
    assert cli.main(["team", "on", "--yes"]) == 0
    pinned = entry(state, repo)["consented"]
    assert cli.main(["team", "on", "--yes"]) == 0
    assert "asks for nothing new" in capsys.readouterr().out
    commit_files(git, repo, {".orchestraty/policy.yml": POLICY.replace("task: ticket", "task: confirmed")})
    git(repo, "push", "-q", "origin", "main")
    asked = Asked(True, False)
    monkeypatch.setattr(cli, "confirm", asked)
    assert cli.main(["team", "on"]) == 1
    out = capsys.readouterr().out
    assert "allows more than you agreed to before" in out and "share.task: ticket to confirmed" in out
    assert entry(state, repo)["consented"] == pinned                                    # the old consent holds
    _, _, force, agreed, eff = team_commands().effective_for(state, str(repo / ".git"))
    assert eff.task == "ticket" and force.task == "confirmed"                           # widening waits for consent
    assert cli.main(["team"]) == 0
    assert "allows more than you agreed to: orch team on to review it" in capsys.readouterr().out
    monkeypatch.setattr(cli, "confirm", Asked(True, True))
    assert cli.main(["team", "on"]) == 0
    assert entry(state, repo)["consented"]["share"]["task"] == "confirmed"


def team_commands():
    from orchestraty.team import commands
    return commands


def test_preview_prints_exact_seal_bytes_and_withheld(state, with_policy, repo, git, tmp_path, capsys, monkeypatch):
    clean, leaky = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat/PAY-142-refunds", str(clean))
    git(repo, "worktree", "add", "-q", "-b", f"feat/{token()}", str(leaky))
    git(clean, "push", "-q", "-u", "origin", "feat/PAY-142-refunds")
    spool_event(state.root, "A", "SessionStart", clean)
    spool_event(state.root, "B", "SessionStart", leaky)
    c = cmain.Coordinator(state)
    try:
        c.step()
    finally:
        c.close()
    assert cli.main(["team", "on", "--yes"]) == 0
    capsys.readouterr()
    now = 1_795_099_235.0
    monkeypatch.setattr(team_commands().time, "time", lambda: now)
    assert cli.main(["team", "preview"]) == 0
    out = capsys.readouterr().out
    expected = team_commands().sealed_now(state, str(repo / ".git"), now)
    assert expected.data.decode("ascii") in out and "withheld: sessions[].branch (github-token)" in out
    assert token() not in out
    record = recordfile.parse_structure(expected.data).record
    (session,) = record.sessions
    head = git(clean, "rev-parse", "HEAD")
    assert (session.branch, session.ticket, session.pushed) == (
        "feat/PAY-142-refunds", "PAY-142", recordfile.Pushed("refs/heads/feat/PAY-142-refunds", head))
    assert record.person == "alice" and record.device == identity.Device.load(state).id and record.seq == 1
    seal.verify(expected.data)
    assert cli.main(["team", "preview", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["bytes"] == expected.data.decode("ascii") and data["held"] == ""
    assert data["withheld"] == [{"field": "sessions[].branch", "rule": "github-token"}]
    assert data["record"] == json.loads(expected.data)


def test_task_line_needs_confirmation_and_scan(state, with_policy, repo, git, tmp_path, capsys, monkeypatch):
    wt = tmp_path / "wt-a"
    git(repo, "worktree", "add", "-q", "-b", "feat/refunds", str(wt))
    spool_event(state.root, "A", "SessionStart", wt)
    c = cmain.Coordinator(state)
    try:
        c.step()
        monkeypatch.chdir(wt)
        assert cli.main(["team", "task", "--line", f"rotate {token(2)}"]) == 2
        err = capsys.readouterr().err
        assert "holds a secret (github-token), so it is not stored" in err and token(2) not in err
        assert cli.main(["team", "task", "--line", "two\tcolumns"]) == 2
        assert cli.main(["team", "task", "--from-declared"]) == 2                     # the agent declared nothing
        assert cli.main(["team", "task", "--line", "Refund flow for partial captures"]) == 0
        assert cli.main(["team", "task", "--ticket", "PAY-142"]) == 0
        assert cli.main(["team", "task", "--ticket", "pay 142"]) == 2
        c.step()
        assert c.record.confirmed("A")["line"] == "Refund flow for partial captures"
        assert c.record.confirmed("A")["ticket"] == "PAY-142"
        from orchestraty import declarations
        declarations.append(state, "A", ["a.py"], [], "Fix the refund rounding")
        c.step()
        assert cli.main(["team", "task", "--from-declared"]) == 1                     # no terminal: nothing stored
        assert "Nothing stored" in capsys.readouterr().out
        monkeypatch.setattr(cli, "confirm", Asked(True))
        monkeypatch.setattr(team_commands(), "_edited", lambda declared: declared + " in pounds")
        assert cli.main(["team", "task", "--from-declared"]) == 0
        c.step()
        assert c.record.confirmed("A")["line"] == "Fix the refund rounding in pounds"
        assert cli.main(["team", "task", "--clear"]) == 0
        c.step()
        assert c.record.confirmed("A") == {"session_id": "A", "ticket": "", "line": "", "ts": c.record.confirmed("A")["ts"]}
    finally:
        c.close()


def test_init_prints_and_write_asks_never_commits(state, origin, repo, git, capsys, monkeypatch):
    head = git(repo, "rev-parse", "HEAD")
    assert cli.main(["team", "init"]) == 0
    out = capsys.readouterr().out
    assert "# .orchestraty/policy.yml" in out and "  - handle: alice\n    emails: [alice@example.com]\n" in out
    assert "/.orchestraty/ @your-org/leads" in out and not (repo / ".orchestraty").exists()
    monkeypatch.setattr(cli, "confirm", Asked(False))
    assert cli.main(["team", "init", "--write"]) == 1 and not (repo / ".orchestraty").exists()
    monkeypatch.setattr(cli, "confirm", Asked(True))
    assert cli.main(["team", "init", "--write"]) == 0
    assert git(repo, "status", "--porcelain") == "?? .orchestraty/" and git(repo, "rev-parse", "HEAD") == head
    assert policy.parse((repo / ".orchestraty" / "policy.yml").read_bytes()).sync_on()
    assert cli.main(["team", "check"]) == 0
    (repo / ".orchestraty" / "people.yml").write_text("version: 1\npeople: []\n", encoding="utf-8")
    monkeypatch.setattr(cli, "confirm", Asked(True))
    assert cli.main(["team", "init", "--write"]) == 0
    assert "people.yml is there already: kept as it is" in capsys.readouterr().out
    assert (repo / ".orchestraty" / "people.yml").read_text(encoding="utf-8") == "version: 1\npeople: []\n"


def test_check_exits_1_with_line_numbers(state, origin, repo, capsys):
    folder = repo / ".orchestraty"
    folder.mkdir()
    (folder / "policy.yml").write_text("version: 1\nsync: on\n\tvisibility: private\n", encoding="utf-8")
    (folder / "people.yml").write_text(PEOPLE + "  - handle: alice\n    emails: [a2@example.com]\n", encoding="utf-8")
    assert cli.main(["team", "check"]) == 1
    out = capsys.readouterr().out
    assert "policy.yml: the team policy cannot be read; fix: fix .orchestraty/policy.yml line 3: a tab" in out
    assert "people.yml: line 5: the handle alice is given twice" in out
    (folder / "policy.yml").write_text(POLICY, encoding="utf-8")
    (folder / "people.yml").write_text(PEOPLE, encoding="utf-8")
    assert cli.main(["team", "check", str(repo)]) == 0
    assert "policy.yml: ok" in capsys.readouterr().out
    (folder / "policy.yml").write_text("version: 1\nsync: on\n", encoding="utf-8")
    assert cli.main(["team", "check", str(folder)]) == 1
    assert "the policy must say visibility: private or public" in capsys.readouterr().out


def test_whoami_prints_people_snippet_for_unlisted(state, with_policy, repo, git, capsys):
    git(repo, "config", "user.email", "Carol.Jones@example.com")
    assert cli.main(["team", "whoami"]) == 0
    out = capsys.readouterr().out
    handle = identity.unlisted_handle("carol.jones@example.com")
    assert f"you are: {handle} (not in .orchestraty/people.yml, as in this working copy" in out
    assert "  - handle: carol-jones\n    emails: [Carol.Jones@example.com]\n" in out
    assert "device: none yet" in out
    git(repo, "config", "user.email", "alice@example.com")
    assert cli.main(["team", "on", "--yes"]) == 0
    capsys.readouterr()
    assert cli.main(["team", "whoami"]) == 0
    out = capsys.readouterr().out
    assert "you are: alice (in .orchestraty/people.yml, as in force on origin's default branch)" in out
    assert f"device: {identity.Device.load(state).id}" in out and "To be listed" not in out


def test_enabled_marker_follows_config(state, with_policy, repo, capsys):
    marker = Path(team.marker(state))
    assert not marker.exists()
    assert cli.main(["team", "on", "--yes"]) == 0 and marker.exists()
    assert cli.main(["team", "off"]) == 0 and not marker.exists()
    assert cli.main(["team", "device", "--label", "laptop"]) == 0 and identity.Device.load(state).label == "laptop"
    c = cmain.Coordinator(state)
    try:
        cfg = json.loads(Path(state.config).read_text(encoding="utf-8"))
        cfg["team"][next(iter(cfg["team"]))]["on"] = True                       # edited by hand
        Path(state.config).write_text(json.dumps(cfg), encoding="utf-8")
        c.reload_config()
        assert marker.exists()
        cfg["team"][next(iter(cfg["team"]))]["on"] = False
        before = os.stat(state.config).st_mtime_ns
        Path(state.config).write_text(json.dumps(cfg), encoding="utf-8")
        os.utime(state.config, ns=(before + 2 * 10 ** 9, before + 2 * 10 ** 9))   # a coarse clock: same time otherwise
        c.reload_config()
        assert not marker.exists()
    finally:
        c.close()
    assert (Path(state.root) / "team" / "device.json").exists()                  # the device stays
