"""Cloud agents' branches: the settings, the shadow repository's fetch (never a credential
prompt; a failure is kept, and the wait doubles), and orch remote."""
import http.server
import json
import threading
import time

import pytest

from orchestraty import cli, config, remote
from orchestraty.paths import State


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def origin(repo, git, tmp_path):
    """The user's repository gets a remote, origin: a local bare repository."""
    path = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(path))
    git(repo, "remote", "add", "origin", str(path))
    return path


@pytest.fixture
def agent(origin, git, tmp_path):
    """A cloud agent's own clone of origin: push(branch, {file: text}) pushes a branch from main."""
    clone = tmp_path / "agent-clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))

    def push(branch, files, start="origin/main"):
        git(clone, "fetch", "-q", "origin")
        git(clone, "checkout", "-q", "-B", branch, start)
        for name, text in files.items():
            (clone / name).write_text(text, encoding="utf-8")
            git(clone, "add", name)
        git(clone, "commit", "-q", "-m", f"work on {branch}")
        git(clone, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
        return git(clone, "rev-parse", "HEAD")
    return push


def turn_on(state, repo, **changes):
    config.watch(state, str(repo / ".git"))
    return remote.save_settings(state, str(repo / ".git"), on=True, **changes)


def fetch(state, repo):
    chosen = remote.settings(config.load(state), str(repo / ".git"))
    return remote.fetch_now(state, str(repo / ".git"), chosen, 300)


class _AsksForCredentials(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def test_no_credential_prompt_and_an_unreachable_remote_is_a_recorded_failure(state, repo, origin, git):
    env = remote.fetch_env({"PATH": "/bin", "GIT_DIR": "/x"})
    assert (env["GIT_TERMINAL_PROMPT"], env["GIT_ASKPASS"], env["SSH_ASKPASS"], env["GCM_INTERACTIVE"]) == ("0", "", "",
                                                                                                         "never")
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"] and "GIT_DIR" not in env
    assert "GIT_SSH_COMMAND" not in remote.fetch_env({"PATH": "/bin"}, ssh_configured=True)
    server = http.server.HTTPServer(("127.0.0.1", 0), _AsksForCredentials)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        git(repo, "remote", "set-url", "origin", f"http://127.0.0.1:{server.server_port}/repo.git")
        turn_on(state, repo)
        started = time.monotonic()
        first = fetch(state, repo)                                       # asks for a password: git must not wait
        assert time.monotonic() - started < 60
    finally:
        server.shutdown()
        server.server_close()
    assert first["ok"] is False and first["failures"] == 1 and first["error"]
    assert first["next"] - first["at"] == 600                            # the wait doubles after a failure
    git(repo, "remote", "set-url", "origin", str(origin.parent / "no-such-remote.git"))
    second = fetch(state, repo)
    assert (second["ok"], second["failures"], second["next"] - second["at"]) == (False, 2, 1200)
    shadow = remote.Shadow(state, str(repo / ".git"))
    assert not shadow.due(time.time())                                   # no retry before then
    git(repo, "remote", "set-url", "origin", "https://user:secret-token@example.invalid/repo.git")
    assert "secret-token" not in json.dumps(fetch(state, repo))


def test_orch_remote_on_off_patterns_and_fetch(state, repo, origin, agent, capsys):
    assert cli.main(["remote", "on", "--repo", str(repo), "--yes"]) == 2              # not watched yet
    assert "not watched" in capsys.readouterr().err
    config.watch(state, str(repo / ".git"))
    assert cli.main(["remote", "on", "--repo", str(repo), "--remote", "upstream", "--yes"]) == 2
    assert "no remote named upstream" in capsys.readouterr().err
    assert cli.main(["remote", "on", "--repo", str(repo)]) == 1                        # no terminal to ask in
    assert "Nothing changed." in capsys.readouterr().out
    assert cli.main(["remote", "fetch", "--repo", str(repo)]) == 2                     # off
    assert cli.main(["remote", "on", "--repo", str(repo), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "fetch from origin" in out and "Your .git is not changed." in out
    assert remote.settings(config.load(state), str(repo / ".git"))["on"] is True
    assert cli.main(["remote", "patterns", "add", "agent-*/work", "--repo", str(repo)]) == 0
    assert cli.main(["remote", "patterns", "add", "two*stars*", "--repo", str(repo)]) == 2
    assert cli.main(["remote", "patterns", "remove", "copilot/*", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert cli.main(["remote", "patterns", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out.split() == ["claude/*", "codex/*", "agent-*/work"]
    agent("agent-7/work", {"b.py": "x = 7\n"})
    assert cli.main(["remote", "fetch", "--repo", str(repo)]) == 0
    assert "ok, 1 branch(es) match" in capsys.readouterr().out
    assert cli.main(["remote", "--repo", str(repo)]) == 0
    assert "Cloud branches of repo: on. Remote: origin." in capsys.readouterr().out
    assert cli.main(["remote", "off", "--repo", str(repo)]) == 0
    assert remote.settings(config.load(state), str(repo / ".git"))["on"] is False


def test_patterns_and_urls():
    assert remote.matches("claude/fix-x", ["claude/*"]) and remote.matches("claude/a/b", ["claude/*"])
    assert not remote.matches("claudex/y", ["claude/*"]) and remote.matches("main", ["main"])
    assert remote.matches("agent-7/work", ["agent-*/work"]) and not remote.matches("agent-7/play", ["agent-*/work"])
    assert remote.valid_pattern("claude/*") and not remote.valid_pattern("a*b*") and not remote.valid_pattern("-x")
    assert not remote.valid_pattern("has space/*") and not remote.valid_pattern("")
    assert remote.safe_url("https://me:token@example.com/r.git") == "https://example.com/r.git"
    assert remote.settings({}, "/r/.git") == {"on": False, "remote": "origin", "patterns": ["claude/*", "codex/*",
                                                                                            "copilot/*"]}
