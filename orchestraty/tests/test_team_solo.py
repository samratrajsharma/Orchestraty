"""Guards on what a solo user sees: with no team set up, Orchestraty behaves exactly as 0.1.1.

A fixed scenario (two worktree sessions of one repository, a breaking change in one used in the
other, cloud branches off) is played through the coordinator with a fixed clock and fixed commit
dates, and what it leaves is compared with what 0.1.1 left: the text and JSON of orch status, the
files in the state folder, the git commands the coordinator runs (and no network at all), and the
command line of the cloud fetch. Team code must change none of them for a user who never turned
it on. Paths are written relative to the test's temporary folder, so the files hold no machine's
names.
"""
import json
import os
import re
import socket
import subprocess
from pathlib import Path

import pytest

from helpers import spool_event, tree
from orchestraty import config, remote, status
from orchestraty.coordinator import main as cmain
from orchestraty.paths import State

NS = 1_000_000_000
T = 1_795_000_000 * NS                    # the scenario's first event; the clock stands just after it
NOW = T + 5 * NS
GOLDEN = Path(__file__).parent / "fixtures" / "solo"
A_PY = b"def charge(amount, currency):\n    return amount\n"
B_PY = b"from a import charge\n\n\ndef pay():\n    return charge(1, 'EUR')\n"
BROKEN = b"def charge(amount):\n    return amount\n"
USES = b"from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"
SLOT = re.compile(r"[^/]*~[0-9a-f]{16}(\.[a-z]+)$")       # a slot or push file: named after a path on this disk


@pytest.fixture
def solo(tmp_path, git, monkeypatch):
    """The repository, its two worktrees and the state folder, before any event."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00Z")      # the same commit ids on every machine
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00Z")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.py").write_bytes(A_PY)
    (repo / "b.py").write_bytes(B_PY)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    for name in ("a", "b"):
        git(repo, "worktree", "add", "-q", "-b", f"feat-{name}", str(tmp_path / f"wt-{name}"))
    state = State(str(tmp_path / "state"))
    config.watch(state, str(repo / ".git"))
    return state, repo, tmp_path


def play(state, tmp_path, steps=0):
    """The scenario through one coordinator: A breaks charge(), B then calls it the old way."""
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    spool_event(state.root, "A", "SessionStart", wa, ts=T)
    spool_event(state.root, "B", "SessionStart", wb, ts=T + NS)
    c = cmain.Coordinator(state, now_ns=lambda: NOW)

    def settle():
        for _ in range(3):
            c.step()
            c.livemap.indexes.wait(120)
            c._last_scan = None
        c.step()
    try:
        settle()
        (wa / "a.py").write_bytes(BROKEN)
        spool_event(state.root, "A", "PostToolUse", wa, ts=T + 2 * NS, tool_name="Edit", file_path=str(wa / "a.py"))
        settle()
        (wb / "c.py").write_bytes(USES)
        spool_event(state.root, "B", "PostToolUse", wb, ts=T + 3 * NS, tool_name="Edit", file_path=str(wb / "c.py"))
        settle()
        for _ in range(steps):
            c.step()
        return tree(state.root)
    finally:
        c.close()


def local(text, tmp_path):
    """Text with the temporary folder as <tmp> and every path written with /."""
    return text.replace(str(tmp_path), "<tmp>").replace("\\", "/")


def local_json(value, tmp_path):
    if isinstance(value, str):
        return local(value, tmp_path)
    if isinstance(value, list):
        return [local_json(v, tmp_path) for v in value]
    if isinstance(value, dict):
        return {k: local_json(v, tmp_path) for k, v in value.items()}
    return value


def test_solo_status_text_and_json_are_0_1_1(solo):
    state, repo, tmp_path = solo
    play(state, tmp_path)
    data = status.collect(state, now=T / NS + 100)
    assert local(status.render(data), tmp_path) == (GOLDEN / "status.txt").read_text(encoding="utf-8")
    assert local_json(data, tmp_path) == json.loads((GOLDEN / "status.json").read_text(encoding="utf-8"))


def test_solo_state_folder_files_unchanged(solo):
    state, repo, tmp_path = solo
    files = sorted(SLOT.sub(r"<slot>\1", f) for f in play(state, tmp_path, steps=50))
    assert files == [
        "config.json", "engine", "engine/cache", "engine/cache/.hmac_key",
        "engine/cache/commit-7542b35e74b03b7a5e6e789123ba90648497affb-2000000-v5.pkl",
        "notes", "notes/A", "notes/A/next.json", "notes/A/stop.json", "notes/B", "notes/B/edit",
        "notes/B/edit/<slot>.json", "notes/B/edit/<slot>.json", "notes/B/next.json",
        "push", "push/<slot>.txt",
        "record.sqlite", "record.sqlite-shm", "record.sqlite-wal",
        "spool", "spool/claude", "spool/claude/A.jsonl", "spool/claude/B.jsonl",
        "status.json"]


def _subcommand(argv):
    """The git subcommand of a command line: the first word after git's own options."""
    i = 1
    while i < len(argv):
        if argv[i] in ("-c", "-C"):
            i += 2
        elif argv[i].startswith("-"):
            i += 1
        else:
            return argv[i]
    return ""


def test_solo_git_commands_unchanged_and_no_network(solo, monkeypatch):
    state, repo, tmp_path = solo
    started, connected = [], []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **k):
            started.append([str(x) for x in args] if not isinstance(args, (str, bytes)) else [str(args)])
            super().__init__(args, *a, **k)

    def refuse(*args, **kwargs):
        connected.append(args)
        raise OSError("no network in a solo run")
    monkeypatch.setattr(subprocess, "Popen", Spy)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    play(state, tmp_path, steps=50)
    assert {os.path.splitext(os.path.basename(argv[0]))[0] for argv in started} == {"git"}
    assert sorted({_subcommand(argv) for argv in started}) == [
        "cat-file", "diff", "ls-files", "ls-tree", "rev-list", "rev-parse", "symbolic-ref", "worktree"]
    assert connected == []


def test_cloud_fetch_argv_unchanged(state_root, repo, git, tmp_path, monkeypatch):
    state = State(str(state_root))
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    config.watch(state, str(repo / ".git"))
    chosen = remote.save_settings(state, str(repo / ".git"), on=True)
    calls = []
    real = subprocess.run

    def spy(argv, *args, **kwargs):
        if "fetch" in argv:
            calls.append((list(argv), kwargs))
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", spy)
    assert remote.fetch_now(state, str(repo / ".git"), chosen, 300)["ok"]
    shadow = remote.Shadow(state, str(repo / ".git"))
    [(argv, kwargs)] = calls
    assert argv == [
        "git", "-c", "log.showSignature=false", "-c", "core.fsmonitor=false", "-c", "core.longpaths=true",
        "-c", "core.hooksPath=" + os.path.join(shadow.path, "no-hooks"), "-c", "core.askPass=",
        "-c", "credential.interactive=never", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30",
        "fetch", "--no-tags", "--prune", "--no-recurse-submodules", "--quiet", os.path.normpath(str(origin)),
        "+HEAD:refs/orch/origin/HEAD", "+refs/heads/claude/*:refs/orch/origin/claude/*",
        "+refs/heads/codex/*:refs/orch/origin/codex/*", "+refs/heads/copilot/*:refs/orch/origin/copilot/*"]
    assert kwargs["cwd"] == shadow.path and kwargs["stdin"] == subprocess.DEVNULL
    env = kwargs["env"]
    assert {k: env[k] for k in ("GIT_TERMINAL_PROMPT", "GIT_ASKPASS", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE",
                                "GCM_INTERACTIVE", "GIT_ALLOW_PROTOCOL", "GIT_OPTIONAL_LOCKS", "GIT_SSH_COMMAND")} == {
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "", "SSH_ASKPASS_REQUIRE": "never",
        "GCM_INTERACTIVE": "never", "GIT_ALLOW_PROTOCOL": "file:git:http:https:ssh", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=20"}
