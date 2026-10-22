"""orch handoff and orch resume: a packet built from the record and git (never a prompt), and an
agent started on it in a new worktree only after the user agrees."""
import io
import json
import os
import sys
from pathlib import Path

import pytest

from helpers import hook_payload, run_hook, spool_event
from orchestraty import cli, config, declarations, handoff, resume
from orchestraty.coordinator import main as cmain
from orchestraty.paths import State

BROKEN = "def charge(amount):\n    return amount\n"
USES = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"
SECRET = "SECRET-PROMPT-7f3a: never store me"
FAKE_AGENT = """import json, os, sys
data = {"argv": sys.argv[1:], "cwd": os.getcwd()}
if not sys.stdin.isatty():
    data["stdin"] = sys.stdin.read()
with open(os.environ["FAKE_AGENT_RECORD"], "w", encoding="utf-8") as fh:
    json.dump(data, fh)
"""


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def edit(state, c, sid, top, rel, text):
    (top / rel).write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(top / rel))
    settle(c)


@pytest.fixture
def two(state, repo, git, tmp_path, hook_script):
    """A breaks charge in wt-a (and leaves b.py's pay behind); B, in wt-b, writes c.py, which
    calls it the old way. A's prompt, which the hooks must never keep, holds a secret."""
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    for sid, top in (("A", wa), ("B", wb)):
        spool_event(state.root, sid, "SessionStart", top)
    proc = run_hook(hook_script, ["claude", "UserPromptSubmit"],
                    hook_payload("UserPromptSubmit", "A", wa, prompt=SECRET))
    assert proc.returncode == 0
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    yield c, wa, wb
    c.close()


def every_file(root):
    for path in Path(root).rglob("*"):
        if path.is_file():
            yield path


def test_the_packet_holds_the_work_and_no_prompt(state, two, git, capsys, monkeypatch):
    c, wa, wb = two
    declarations.append(state, "A", ["a.py"], ["a.py::charge"], "drop the currency from charge")
    c.step()
    monkeypatch.chdir(wa)
    assert cli.main(["handoff"]) == 0
    out = capsys.readouterr().out
    packet_id = out.split("Handoff ", 1)[1].split(":", 1)[0]
    assert f"orch resume {packet_id} --agent" in out and "1 file(s) were not committed" in out
    packet, md = handoff.load(state, packet_id[:4])
    assert packet["id"] == packet_id and md.endswith(packet_id + ".md")
    assert packet["session"] == {"id": "A", "agent": "claude", "worktree": str(wa), "branch": "feat-a",
                                 "state": "live"}
    assert packet["task"] == {"text": "drop the currency from charge", "from": "declared"}
    assert packet["head"] == git(wa, "rev-parse", "HEAD") == packet["base_commit"]
    assert packet["uncommitted"] == ["a.py"] and packet["files"] == {"a.py": "M"}
    [change] = packet["contract_changes"]
    assert (change["symbol"], change["contract"], change["reasons"]) == ("a.py::charge", "breaking",
                                                                         ["parameter currency removed"])
    assert [(f["rule"], f["with"]) for f in packet["findings"]] == [("O3", ["B"]), ("O6", [])]
    assert "is used in c.py by another Claude Code session (worktree wt-b, branch feat-b)" in packet["findings"][0][
        "note"]
    [left] = packet["left_behind"]
    assert left["symbol"] == "a.py::charge" and left["callers"] == [{"symbol": "b.py::pay", "file": "b.py", "lines": [5]}]
    text = Path(md).read_text(encoding="utf-8")
    assert text.startswith("# Handoff: drop the currency from charge\n")
    for part in ("- **Task** (as the agent declared it): drop the currency from charge", "- **Branch:** feat-a",
                 "- M a.py", "- charge in a.py: modified, contract breaking: parameter currency removed",
                 "- strong O3: charge in a.py, which you changed",
                 "- pay in b.py (lines 5) still calls charge the old way",
                 "Not run for this handoff", "Not committed"):
        assert part in text, part
    # the secret prompt is nowhere: not in the packet, not anywhere in the state folder
    for path in every_file(state.root):
        assert SECRET.encode() not in path.read_bytes(), path
    # a task line of the user's own, and the branch when there is no other
    assert cli.main(["handoff", "--task", "finish the refund flow", "--session", "B"]) == 0
    packet_b = handoff.load(state, capsys.readouterr().out.split("Handoff ", 1)[1].split(":", 1)[0])[0]
    assert packet_b["task"] == {"text": "finish the refund flow", "from": "user"}
    assert [(f["rule"], f["file"], f["latent"]) for f in packet_b["findings"]] == [("O3", "b.py", True),
                                                                                    ("O3", "c.py", False)]
    assert packet_b["left_behind"] == []
    assert cli.main(["handoff", "--session", "B"]) == 0
    packet_b = handoff.load(state, capsys.readouterr().out.split("Handoff ", 1)[1].split(":", 1)[0])[0]
    assert packet_b["task"] == {"text": "feat-b", "from": "branch"}


def test_tests_run_in_the_worktree_with_a_time_limit_and_out_writes_a_copy(state, two, tmp_path, capsys,
                                                                            monkeypatch):
    c, wa, wb = two
    monkeypatch.chdir(wa)
    script = tmp_path / "t.py"
    script.write_text("import os, sys\nprint('ran in', os.path.basename(os.getcwd()))\nprint('1 failed\\x1b[0m')\n"
                      "sys.exit(3)\n", encoding="utf-8")
    copy = tmp_path / "packet.md"
    assert cli.main(["handoff", "--tests", f'"{sys.executable}" "{script}"', "--out", str(copy)]) == 0
    out = capsys.readouterr().out
    packet = handoff.load(state, out.split("Handoff ", 1)[1].split(":", 1)[0])[0]
    t = packet["tests"]
    assert (t["exit_code"], t["timed_out"], t["last_lines"]) == (3, False, ["ran in wt-a", "1 failed [0m"])
    assert "Tests: exited 3." in out and "exited 3 after" in copy.read_text(encoding="utf-8")
    assert cli.main(["handoff", "--out", str(copy)]) == 2                        # never over a file
    slow = tmp_path / "slow.py"
    slow.write_text("import time\nprint('started', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    assert cli.main(["handoff", "--tests", f'"{sys.executable}" "{slow}"', "--timeout", "2"]) == 0
    packet = handoff.load(state, capsys.readouterr().out.split("Handoff ", 1)[1].split(":", 1)[0])[0]
    assert packet["tests"]["timed_out"] is True and packet["tests"]["exit_code"] is None
    assert packet["tests"]["seconds"] < 30 and "started" in packet["tests"]["last_lines"]


class Terminal(io.StringIO):
    def isatty(self):
        return True


def made_packet(state, capsys, monkeypatch, wa):
    monkeypatch.chdir(wa)
    assert cli.main(["handoff", "--task", "finish it"]) == 0
    return capsys.readouterr().out.split("Handoff ", 1)[1].split(":", 1)[0]


def test_resume_says_what_will_run_and_does_nothing_without_consent(state, two, git, repo, tmp_path, capsys,
                                                                    monkeypatch):
    c, wa, wb = two
    packet_id = made_packet(state, capsys, monkeypatch, wa)
    head = git(wa, "rev-parse", "HEAD")
    target = tmp_path / f"wt-a-{packet_id}"
    monkeypatch.setattr(resume, "which", lambda name: f"/opt/bin/{name}")
    for agent, shown in (("claude", "/opt/bin/claude <the packet: "), ("codex", "/opt/bin/codex <the packet: "),
                         ("antigravity", "/opt/bin/agy --prompt-interactive <the packet: ")):
        assert cli.main(["resume", packet_id, "--agent", agent, "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert f"git worktree add -b feat-a-{packet_id} {target} {head}" in out, out
        assert shown in out and "keeps its own permissions and model settings" in out
    assert cli.main(["resume", packet_id, "--agent", "my-agent --fast", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "  my-agent --fast\n" in out and "on its standard input" in out
    before = git(repo, "worktree", "list")
    assert cli.main(["resume", packet_id, "--agent", "codex"]) == 1                # no terminal to ask in
    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda question: "n")
    assert cli.main(["resume", packet_id, "--agent", "codex"]) == 1                # the person said no
    assert "Nothing done." in capsys.readouterr().out
    assert git(repo, "worktree", "list") == before and not target.exists()
    monkeypatch.setattr(resume, "which", lambda name: None)
    assert cli.main(["resume", packet_id, "--agent", "codex", "--yes"]) == 2         # not installed: nothing made
    assert "codex is not on PATH" in capsys.readouterr().err and not target.exists()
    assert cli.main(["resume", "nothing", "--agent", "codex"]) == 2


def test_resume_starts_the_agent_in_a_new_worktree_with_the_packet(state, two, git, tmp_path, capsys, monkeypatch):
    c, wa, wb = two
    packet_id = made_packet(state, capsys, monkeypatch, wa)
    packet, md = handoff.load(state, packet_id)
    text = Path(md).read_text(encoding="utf-8")
    fake = tmp_path / "fake_agent.py"
    fake.write_text(FAKE_AGENT, encoding="utf-8")
    record = tmp_path / "agent.json"
    monkeypatch.setenv("FAKE_AGENT_RECORD", str(record))
    monkeypatch.setitem(resume.AGENTS, "claude", ("claude", [str(fake)]))
    monkeypatch.setattr(resume, "which", lambda name: sys.executable)
    target = tmp_path / "taken-over"
    assert cli.main(["resume", packet_id, "--agent", "claude", "--worktree", str(target), "--yes"]) == 0
    got = json.loads(record.read_text(encoding="utf-8"))
    assert os.path.samefile(got["cwd"], target) and got["argv"] == [text]              # the packet, as its first message
    assert git(target, "rev-parse", "--abbrev-ref", "HEAD") == f"feat-a-{packet_id}"
    assert git(target, "rev-parse", "HEAD") == packet["head"]
    # a command of the user's own gets the packet on its standard input
    other = tmp_path / "own-command"
    assert cli.main(["resume", packet_id, "--agent", f'"{sys.executable}" "{fake}"', "--worktree", str(other),
                     "--yes"]) == 2                                                    # the branch exists now
    assert "exists already" in capsys.readouterr().err and not other.exists()
    packet_id = made_packet(state, capsys, monkeypatch, wa)
    assert cli.main(["resume", packet_id, "--agent", f'"{sys.executable}" "{fake}"', "--worktree", str(other),
                     "--yes"]) == 0
    got = json.loads(record.read_text(encoding="utf-8"))
    assert got["argv"] == [] and got["stdin"].replace("\r\n", "\n") == Path(handoff.load(state, packet_id)[1]).read_text(
        encoding="utf-8")
