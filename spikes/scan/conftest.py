"""Synthetic home folders and git repositories for the scanner tests. Nothing here is real data."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

# A marker planted in every session and history file. It must never reach the output,
# and no file holding it may ever be opened.
SECRET = "SECRET-PROMPT-TEXT-7f3a"
DAY = 86400


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The scanner must never touch the network. Any socket use in a test fails it."""
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def _touch(path, content="", age_days=0.0, now=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    t = (now or time.time()) - age_days * DAY
    os.utime(path, (t, t))
    return path


def _config_dir(home, system):
    if system == "Windows":
        return home / "AppData" / "Roaming"
    if system == "Darwin":
        return home / "Library" / "Application Support"
    return home / ".config"


class SyntheticMachine:
    """A fake home folder and a fake filesystem root, with agent files in them."""

    def __init__(self, base, system):
        self.system = system
        self.base = base
        self.root = base / "root"
        self.home = base / "home"
        self.home.mkdir(parents=True)
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)     # the machine's temp folder
        self.private_files = []      # every session and history file planted (must never be opened)
        self.now = time.time()

    def env(self, **extra):
        from scan_collect import Env
        return Env.synthetic(self.home, self.system, root=self.root, now=self.now, **extra)

    def _plant(self, path, age):
        self.private_files.append(_touch(path, f'{{"prompt":"{SECRET}"}}\n', age, self.now))
        return path

    # -- sessions for every layout the scanner knows
    def claude_sessions(self, project="proj-a", ages=(0.5, 1, 20), home=None):
        base = (home or self.home / ".claude") / "projects" / project
        return [self._plant(base / f"s{i}.jsonl", age) for i, age in enumerate(ages)]

    def codex_sessions(self, ages=(2, 3)):
        base = self.home / ".codex"
        out = [self._plant(base / "sessions" / "2026" / "09" / f"{10 + i:02d}" /
                           f"rollout-2026-09-{10 + i:02d}T10-00-00-x{i}.jsonl", age)
               for i, age in enumerate(ages)]
        out.append(self._plant(base / "archived_sessions" / "2026" / "09" / "01" /
                               "rollout-2026-09-01T10-00-00-old.jsonl.zst", 60))
        return out

    def gemini_sessions(self, ages=(1,)):
        base = self.home / ".gemini" / "tmp" / "my-project" / "chats"
        return [self._plant(base / f"session-2026-09-2{i}T10-00-ab{i}.jsonl", age) for i, age in enumerate(ages)]

    def copilot_sessions(self, ages=(1,)):
        base = self.home / ".copilot" / "session-state"
        return [self._plant(base / f"sess{i}" / "events.jsonl", age) for i, age in enumerate(ages)]

    def cursor_sessions(self, ages=(1,)):
        base = self.home / ".cursor" / "projects" / "Users-dev-app" / "agent-transcripts"
        return [self._plant(base / f"0f0f0f0{i}-aaaa" / f"0f0f0f0{i}-aaaa.jsonl", age) for i, age in enumerate(ages)]

    def droid_sessions(self, ages=(1,)):
        base = self.home / ".factory" / "sessions" / "-Users-dev-app"
        return [self._plant(base / f"5f0c{i}.jsonl", age) for i, age in enumerate(ages)]

    def pi_sessions(self, ages=(1,)):
        base = self.home / ".pi" / "agent" / "sessions" / "--Users-dev-app--"
        return [self._plant(base / f"2026-09-20T10-00_{i}.jsonl", age) for i, age in enumerate(ages)]

    def history_files(self):
        for rel in (".claude/history.jsonl", ".codex/history.jsonl", ".codex/session_index.jsonl"):
            self._plant(self.home / rel, 1)

    def everything_private(self):
        self.claude_sessions()
        self.codex_sessions()
        self.gemini_sessions()
        self.copilot_sessions()
        self.cursor_sessions()
        self.droid_sessions()
        self.pi_sessions()
        self.history_files()

    # -- config files
    def claude_json(self, servers, base=None):
        import json
        body = {"mcpServers": servers, "oauthAccount": {"emailAddress": "owner@example.com"},
                "projects": {"/x": {"history": [{"display": SECRET}], "mcpServers": {}}}}
        return _touch((base or self.home) / ".claude.json", json.dumps(body))

    def json_file(self, path, obj):
        import json
        return _touch(path, json.dumps(obj))

    def config_dir(self):
        return _config_dir(self.home, self.system)


@pytest.fixture(params=["Windows", "Darwin", "Linux"])
def machine(request, tmp_path):
    return SyntheticMachine(tmp_path, request.param)


@pytest.fixture
def linux_machine(tmp_path):
    return SyntheticMachine(tmp_path, "Linux")


@pytest.fixture(scope="session")
def empty_gitconfig(tmp_path_factory):
    path = tmp_path_factory.mktemp("gitconfig") / "empty"
    path.write_text("", encoding="utf-8")
    return path


_GITCONFIG = []


@pytest.fixture(autouse=True)
def _isolated_git(empty_gitconfig):
    _GITCONFIG[:] = [empty_gitconfig]


def git(repo, *args, env=None, date=None):
    """Run git for building fixtures, ignoring the developer's own git config."""
    e = dict(os.environ)
    e.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(_GITCONFIG[0]),
              "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
              "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"})
    if env:
        e.update(env)
    if date:
        e["GIT_AUTHOR_DATE"] = e["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false",
                           "-c", "protocol.file.allow=always"] + list(args),
                          cwd=repo, env=e, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def commit(repo, path, text, author, days_ago=1.0, message=None):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days_ago * DAY))
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message or f"change {path}",
        env={"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@example.com"}, date=when)


@pytest.fixture
def make_repo():
    def _make(path):
        path.mkdir(parents=True, exist_ok=True)
        git(path, "init", "-q")
        commit(path, "README.md", "base\n", "alice", days_ago=30)
        return path
    return _make


def snapshot_tree(*roots):
    """{path: (size, mtime_ns)} for every file under the given folders."""
    snap = {}
    for root in roots:
        for dirpath, _, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                snap[p] = (st.st_size, st.st_mtime_ns)
    return snap
