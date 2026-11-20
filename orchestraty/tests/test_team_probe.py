"""The anonymous-read probe: whether anyone can read a repository without signing in, asked over
http with no credentials, against local servers (dumb http, one that asks for a password, a
closed port)."""
import functools
import http.server
import json
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from orchestraty import remote
from orchestraty.paths import State
from orchestraty.team import policy, probe
from test_layout import SOURCE, _imported_modules

PRIVATE = "version: 1\nsync: on\nvisibility: private\n"


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


class Seen(http.server.SimpleHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).requests.append((self.path, self.headers.get("Authorization")))
        super().do_GET()


class AsksForPassword(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="team"')
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def dumb(tmp_path, repo, git):
    """A bare clone of `repo` served over dumb http: (url, server)."""
    root = tmp_path / "served"
    root.mkdir()
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(root / "repo.git"))
    git(root / "repo.git", "update-server-info")
    Seen.requests = []
    server = serve(functools.partial(Seen, directory=str(root)))
    yield f"http://127.0.0.1:{server.server_address[1]}/repo.git", server
    server.shutdown()
    server.server_close()


@pytest.fixture
def shadow(state_root, repo):
    return remote.Shadow(State(str(state_root)), str(repo / ".git"))


def test_dumb_http_repository_reads_anonymously(dumb, shadow):
    url, _ = dumb
    assert probe.anonymous_read(url) is True
    assert probe.check(shadow, url, now=1_795_099_200) is True
    assert probe.last(shadow) == (True, 1_795_099_200)
    assert json.loads(Path(shadow.team_path).read_text(encoding="utf-8")) == {
        "probe": {"anonymous_read": True, "checked": 1_795_099_200}}
    assert not probe.due(shadow, 1_795_099_200 + 3600) and probe.due(shadow, 1_795_099_200 + 7 * 86_400)


def test_401_server_is_not_public(git):
    server = serve(AsksForPassword)
    try:
        assert probe.anonymous_read(f"http://127.0.0.1:{server.server_address[1]}/team/repo.git") is False
    finally:
        server.shutdown()
        server.server_close()


def test_unreachable_is_unknown(git, shadow):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert probe.anonymous_read(f"http://127.0.0.1:{port}/repo.git", timeout=20) is None
    assert probe.last(shadow) == (None, 0) and probe.due(shadow, 1)
    for url in ("file:///srv/repo.git", "/srv/repo.git", "../repo.git", "C:\\repos\\repo.git", "", "git://host/r.git"):
        assert probe.anonymous_read(url) is None, url                         # not asked at all


def test_userinfo_never_sent_or_kept(dumb, shadow, monkeypatch):
    url, _ = dumb
    seen, real = [], subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **k):
            seen.append(" ".join(map(str, args)))
            super().__init__(args, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", Spy)
    secret_url = url.replace("http://", "http://alice:s3cret-word@")
    assert probe.check(shadow, secret_url, now=60) is True
    assert seen and not any("s3cret" in argv or "alice" in argv for argv in seen)
    assert Seen.requests and all(auth is None for _, auth in Seen.requests)
    assert "s3cret" not in Path(shadow.team_path).read_text(encoding="utf-8")


def test_ssh_url_mapping():
    for url, form in (("git@github.com:acme/app.git", "https://github.com/acme/app.git"),
                      ("git@github.com:acme/app", "https://github.com/acme/app.git"),
                      ("github.com:acme/app.git", "https://github.com/acme/app.git"),
                      ("ssh://git@github.com/acme/app", "https://github.com/acme/app.git"),
                      ("ssh://git@git.example.com:2222/acme/app.git", "https://git.example.com/acme/app.git"),
                      ("https://alice:tok@github.com/acme/app.git", "https://github.com/acme/app.git"),
                      ("https://github.com/acme/app", "https://github.com/acme/app"),
                      ("http://127.0.0.1:8080/r.git", "http://127.0.0.1:8080/r.git")):
        assert probe.https_form(url) == form, url
    for url in ("file:///srv/r.git", "/srv/r.git", "./r.git", "~/r.git", "C:\\r.git", "C:/r.git", "git://h/r.git",
                "https://exa mple.com/r.git", ""):
        assert probe.https_form(url) is None, url


def test_probe_contradiction_turns_sync_off(dumb, shadow):
    private = policy.parse(PRIVATE)
    assert policy.effective(private, private).sync_on()
    url, _ = dumb
    probe.check(shadow, url, now=60)
    answer, _ = probe.last(shadow)
    eff = policy.effective(private, private, anonymous_read=answer)
    assert not eff.sync_on() and eff.off() == policy.PROBE_PUBLIC
    assert policy.effective(private, private, anonymous_read=None).sync_on()         # unknown changes nothing
    assert policy.effective(private, private, anonymous_read=False).sync_on()
    public = policy.parse(PRIVATE.replace("private", "public") + "public_sync: true\n")
    assert policy.effective(public, public, anonymous_read=True).sync_on()


def test_no_probe_from_hooks_or_doctor(state_root, repo, monkeypatch):
    for module in (SOURCE / "capture" / "hook.py", SOURCE / "doctor.py"):
        assert not any(name.startswith("orchestraty.team") for name in _imported_modules(module)), module
    seen, real = [], subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **k):
            seen.append([str(x) for x in args])
            super().__init__(args, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", Spy)
    from orchestraty import doctor
    list(doctor.checks(State(str(state_root)), str(repo), index=False))
    assert seen and not any("ls-remote" in argv for argv in seen)
