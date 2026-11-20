"""Two teammates' machines on one machine, for the tests of team sync.

Each Machine is one teammate: a clone of a shared bare remote with its own git identity (local
user.name and user.email), its own state folder and its own coordinator, driven step by step on
a clock all machines share, so "within minutes" is asserted without sleeping. The remote is a
local bare repository reached by a file:// URL, and the machines' git allows that protocol only:
nothing goes over a network. Each Machine passes its State to what it runs, never through
ORCH_HOME, which conftest's isolated_home points elsewhere.

publish_raw() writes a record under refs/orchestraty/<person>/<device> on the remote with git's
plumbing alone, so tests can put any bytes there (a hostile record, a forged author) before the
transport that writes records exists.
"""
import json
import os
import subprocess
import types
from pathlib import Path

import pytest

from conftest import GIT_SWITCHES
from helpers import spool_event
from orchestraty import config
from orchestraty.coordinator import main as cmain
from orchestraty.paths import State

NS = 1_000_000_000
START = 1_795_000_000 * NS
IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
            "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")
BILLING = "def charge(amount, currency):\n    return amount\n"
REFUNDS = "from pkg.billing import charge\n\n\ndef refund(amount):\n    return charge(-amount, 'EUR')\n"
POLICY = ("# What Orchestraty may share between the teammates of this repository.\n"
          "version: 1\nsync: on\nvisibility: private\n")
PEOPLE = ("# Who is who: each teammate's handle and the emails their commits carry.\n"
          "version: 1\npeople:\n  - handle: alice\n    emails: [alice@example.com]\n"
          "  - handle: bob\n    emails: [bob@example.com]\n")


class Clock:
    """A fake clock the machines share: nanoseconds, moved on by hand."""

    def __init__(self, start=START):
        self.ns = start

    def __call__(self):
        return self.ns

    def advance(self, seconds):
        self.ns += int(seconds * NS)
        return self.ns


def git_env(identity=None):
    """The environment the harness runs git in: the test's empty user and system config, no
    prompt, the file protocol only, and an identity only where one is given (otherwise the
    repository's own user.name and user.email decide)."""
    env = {k: v for k, v in os.environ.items() if k not in IDENTITY}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file")
    if identity:
        name, email = identity
        env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    return env


def run_git(cwd, *args, identity=None, data=None):
    """git in `cwd`, as the harness runs it; its output, stripped. Fails the test on an error."""
    proc = subprocess.run(["git"] + GIT_SWITCHES + list(args), cwd=cwd, input=data, capture_output=True,
                          env=git_env(identity), timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr.decode('utf-8', 'replace')}")
    return proc.stdout.decode("utf-8").strip()


def url_of(path):
    """The file:// URL of a local repository."""
    return Path(path).resolve().as_uri()


class Machine:
    """One teammate's machine: its own clone of the shared bare remote, git identity (local
    user.name and user.email), state folder (State(tmp/<name>/state)) and Coordinator, driven
    step by step."""

    def __init__(self, tmp, name, email, remote_path, git, clock):
        self.name, self.email, self.clock, self.git = name, email, clock, git
        self.folder = Path(tmp) / name
        self.folder.mkdir(parents=True)
        self.clone = self.folder / "clone"
        run_git(self.folder, "clone", "-q", url_of(remote_path), str(self.clone))
        run_git(self.clone, "config", "user.name", name)
        run_git(self.clone, "config", "user.email", email)
        self.state = State(str(self.folder / "state"))
        config.watch(self.state, str(self.clone / ".git"))
        self.worktrees, self.agents = {}, {}
        self._coordinator = None

    def start(self, sid, branch, agent="claude"):
        """A session `sid` of `agent` in a new worktree on a new branch from origin/main: its
        SessionStart spool line, now on the shared clock. Returns the worktree."""
        top = self.folder / f"wt-{sid}"
        run_git(self.clone, "worktree", "add", "-q", "-b", branch, str(top), "origin/main")
        self.worktrees[sid], self.agents[sid] = top, agent
        spool_event(self.state.root, sid, "SessionStart", top, agent=agent, ts=self.clock())
        return top

    def edit(self, sid, path, text):
        """The session writes `text` to `path` (relative to its worktree): the file and its
        PostToolUse spool line."""
        target = self.worktrees[sid] / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        spool_event(self.state.root, sid, "PostToolUse", self.worktrees[sid], agent=self.agents[sid],
                    ts=self.clock(), tool_name="Edit", file_path=str(target))

    def commit(self, sid, msg):
        """Commit everything in the session's worktree, as this teammate; returns the commit."""
        top = self.worktrees[sid]
        run_git(top, "add", "-A")
        run_git(top, "commit", "-q", "-m", msg)
        return run_git(top, "rev-parse", "HEAD")

    def push(self, sid, *args):
        """A real git push of the session's branch to origin, which writes git's "update by
        push" reflog line; returns the commit pushed. `args` go before the remote's name."""
        top = self.worktrees[sid]
        branch = run_git(top, "symbolic-ref", "--short", "HEAD")
        run_git(top, "push", "-q", *args, "origin", branch)
        return run_git(top, "rev-parse", "HEAD")

    def coordinator(self):
        """This machine's coordinator (made once): Coordinator(State(root), now_ns=clock)."""
        if self._coordinator is None:
            self._coordinator = cmain.Coordinator(State(self.state.root), now_ns=self.clock)
        return self._coordinator

    def team_on(self):
        """orch team on for this clone, answering yes: it reads the policy in force on the remote's
        default branch, makes this machine's device (once), keeps the policy agreed to and makes
        the marker the pre-push hook looks for."""
        from orchestraty.team import commands
        args = types.SimpleNamespace(action="on", remote=None, yes=True, repo=str(self.clone))
        assert commands.on(self.state, args, lambda question, assume_yes: True) == 0

    def device(self):
        path = Path(self.state.root) / "team" / "device.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def settle(self, rounds=3):
        """Step the coordinator until what it started is done: the code graphs, team sync's and
        the cloud fetch's threads."""
        c = self.coordinator()
        for _ in range(rounds):
            c.step()
            c.livemap.indexes.wait(120)
            team = getattr(c, "team", None)
            if team is not None:
                team.wait(120)
            c.cloud.wait(120)
            c._last_scan = None
        c.step()

    def close(self):
        if self._coordinator is not None:
            self._coordinator.cloud.wait(120)
            self._coordinator.close()
            self._coordinator = None


def publish_raw(remote_path, person, device, data: bytes, author_email):
    """Plumbing only: writes bytes under refs/orchestraty/<person>/<device> on the bare remote,
    as record.json in a commit whose author is `author_email` (on top of that ref's last commit,
    when it has one). Lets tests read records before the transport that writes them exists, and
    forge hostile ones. Returns the commit."""
    ref, identity = f"refs/orchestraty/{person}/{device}", (person, author_email)
    blob = run_git(remote_path, "hash-object", "-w", "--stdin", data=data)
    tree = run_git(remote_path, "mktree", data=f"100644 blob {blob}\trecord.json\n".encode("utf-8"))
    old = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=remote_path, capture_output=True,
                         env=git_env()).stdout.decode("utf-8").strip()
    commit = run_git(remote_path, "commit-tree", tree, *(["-p", old] if old else []), "-m", "record",
                     identity=identity)
    run_git(remote_path, "update-ref", ref, commit)
    return commit


def _seed(tmp_path):
    """The team's repository as it starts: main with pkg/billing.py, pkg/refunds.py and the
    policy and people files, pushed by its owner to a new bare remote."""
    seed = tmp_path / "seed"
    seed.mkdir()
    run_git(seed, "init", "-q")
    for rel, text in (("pkg/__init__.py", ""), ("pkg/billing.py", BILLING), ("pkg/refunds.py", REFUNDS),
                      (".orchestraty/policy.yml", POLICY), (".orchestraty/people.yml", PEOPLE)):
        target = seed / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    run_git(seed, "add", "-A")
    run_git(seed, "commit", "-q", "-m", "the team's repository", identity=("owner", "owner@example.com"))
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "clone", "-q", "--bare", str(seed), str(remote))
    return remote


@pytest.fixture
def team(tmp_path, git):
    """remote.git (bare; main with pkg/billing.py, pkg/refunds.py, .orchestraty/policy.yml (sync
    on, private) and people.yml (alice, bob)); alice and bob Machines; a shared fake clock."""
    clock = Clock()
    remote = _seed(tmp_path)
    alice = Machine(tmp_path, "alice", "alice@example.com", remote, git, clock)
    bob = Machine(tmp_path, "bob", "bob@example.com", remote, git, clock)
    yield types.SimpleNamespace(remote=remote, url=url_of(remote), alice=alice, bob=bob, clock=clock)
    alice.close()
    bob.close()


REFUSE_HOOK = ("#!/bin/sh\n"
               "while read old new ref; do\n"
               "  case \"$ref\" in refs/orchestraty/*) echo \"no custom refs here\" >&2; exit 1;; esac\n"
               "done\n"
               "exit 0\n")


@pytest.fixture
def refusing_remote(tmp_path, git):
    """Two more bare remotes of the team's repository that refuse custom refs: `hidden` hides
    refs/orchestraty (receive.hideRefs), `hook` has a pre-receive hook that refuses them. Both
    take branches as usual."""
    seed = tmp_path / "remote.git" if (tmp_path / "remote.git").exists() else _seed(tmp_path)
    out = {}
    for kind in ("hidden", "hook"):
        path = tmp_path / f"refusing-{kind}.git"
        run_git(tmp_path, "clone", "-q", "--bare", str(seed), str(path))
        if kind == "hidden":
            run_git(path, "config", "receive.hideRefs", "refs/orchestraty")
        else:
            hook = path / "hooks" / "pre-receive"
            hook.parent.mkdir(exist_ok=True)
            hook.write_bytes(REFUSE_HOOK.encode("utf-8"))
            os.chmod(hook, 0o755)
        out[kind] = path
    return types.SimpleNamespace(**out)
