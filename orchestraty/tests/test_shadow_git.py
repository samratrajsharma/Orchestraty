"""The shadow repository's git, for what team sync will need: making the shadow, fetching a list
of refspecs given on stdin, pushing one ref with a lease and reading git's porcelain answer,
pruning the shadow's own objects, and the URL the user's own git push goes to. Every call runs
with the cloud fetch's options and environment, so none of them ever asks for a credential.
Tested against local bare repositories, and an http server on 127.0.0.1 that wants a password."""
import hashlib
import http.server
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from orchestraty import remote
from orchestraty.paths import State

URL = "/srv/remote.git"
DST = "refs/orchestraty/alice/7f3a9c2e41b0"
A, B = "94548ec6feb1da411750ba314bfdd0495e06ec14", "17c6b8c10ebe107ed98abf4958aaa70d995747f8"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def origin(repo, git, tmp_path):
    path = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(path))
    git(repo, "remote", "add", "origin", str(path))
    return path


def porcelain(line):
    return f"To {URL}\n{line}\nDone\n"


@pytest.mark.parametrize("code, out, err, kind, detail", [
    (0, porcelain(f"*\t{A}:{DST}\t[new reference]"), "", "new", ""),
    (0, porcelain(f" \t{B}:{DST}\t94548ec..17c6b8c"), "", "fast-forward", ""),
    (0, porcelain(f"+\t{A}:{DST}\t17c6b8c...94548ec (forced update)"), "", "forced", ""),
    (0, porcelain(f"=\t{A}:{DST}\t[up to date]"), "", "up to date", ""),
    (1, porcelain(f"!\t{B}:{DST}\t[rejected] (stale info)"), f"error: failed to push some refs to '{URL}'\n",
     "stale", "stale info"),
    (1, porcelain(f"!\t{A}:{DST}\t[remote rejected] (deny updating a hidden ref)"),
     f"error: failed to push some refs to '{URL}'\n", "refused", "deny updating a hidden ref"),
    (1, porcelain(f"!\t{A}:{DST}\t[remote rejected] (pre-receive hook declined)"),
     f"remote: no custom refs here        \nerror: failed to push some refs to '{URL}'\n", "refused",
     "pre-receive hook declined"),
    (128, "", "fatal: unable to access 'https://user:secret@example.invalid/r.git/': Could not resolve host: "
              "example.invalid\n", "failed", "fatal: unable to access 'https://example.invalid/r.git/': Could not "
                                             "resolve host: example.invalid"),
])
def test_push_result_parses_porcelain(code, out, err, kind, detail):
    result = remote.parse_push(code, out, err, DST)
    assert (result.kind, result.detail) == (kind, detail)
    assert result.ok == (kind in ("new", "fast-forward", "forced", "up to date"))


def test_ensure_makes_the_shadow_once_and_again_when_broken(state, repo, origin):
    shadow = remote.Shadow(state, str(repo / ".git"))
    assert not shadow.exists()
    shadow.ensure()
    assert shadow.exists()
    marker = Path(shadow.path) / "kept"
    marker.write_text("x", encoding="utf-8")
    shadow.ensure()
    assert marker.exists()                                   # there already: left as it is
    shadow.mark_broken()
    shadow.ensure()
    assert shadow.exists() and not marker.exists() and not shadow.broken()


def test_fetch_stdin_missing_glob_succeeds(state, repo, origin, git):
    shadow = remote.Shadow(state, str(repo / ".git"))
    shadow.fetch_refspecs(str(origin), ["+HEAD:refs/orch/origin/HEAD",
                                        "+refs/heads/no-such-branch*:refs/orch-team/origin/heads/no-such-branch*"])
    assert git(shadow.path, "rev-parse", "refs/orch/origin/HEAD") == git(repo, "rev-parse", "HEAD")
    assert git(shadow.path, "for-each-ref", "refs/orch-team/") == ""


def test_fetch_stdin_explicit_missing_ref_fails(state, repo, origin):
    """Why the team fetch asks for a branch by a glob: one exact name the remote no longer has
    fails the whole fetch."""
    shadow = remote.Shadow(state, str(repo / ".git"))
    with pytest.raises(remote.RemoteError, match="no-such-branch"):
        shadow.fetch_refspecs(str(origin), ["+HEAD:refs/orch/origin/HEAD",
                                            "+refs/heads/no-such-branch:refs/orch-team/origin/heads/no-such-branch"])


def test_push_new_then_with_a_lease(state, repo, origin, git):
    shadow = remote.Shadow(state, str(repo / ".git"))
    shadow.ensure()
    head = git(repo, "rev-parse", "HEAD")
    assert shadow.push(str(origin), head, DST, "").kind == "new"
    assert git(origin, "rev-parse", DST) == head
    assert shadow.push(str(origin), head, DST, head).kind == "up to date"
    assert shadow.push(str(origin), head, DST, "").kind == "up to date"   # the same tip: nothing to check a lease on
    newer = git(shadow.path, "commit-tree", git(repo, "rev-parse", "HEAD^{tree}"), "-p", head, "-m", "record 2")
    stale = shadow.push(str(origin), newer, DST, "")                 # we thought it was not there yet
    assert (stale.kind, stale.detail) == ("stale", "stale info")
    assert git(origin, "rev-parse", DST) == head
    assert shadow.push(str(origin), newer, DST, head).kind == "fast-forward"
    older = git(shadow.path, "commit-tree", git(repo, "rev-parse", "HEAD^{tree}"), "-p", head, "-m", "compacted")
    assert shadow.push(str(origin), older, DST, newer).kind == "forced"
    assert git(repo, "for-each-ref", "refs/orchestraty/") == ""       # the user's repository never has these refs


class _AsksForCredentials(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_GET

    def log_message(self, *args):
        pass


def test_push_never_prompts(state, repo, origin, git):
    shadow = remote.Shadow(state, str(repo / ".git"))
    shadow.ensure()
    server = http.server.HTTPServer(("127.0.0.1", 0), _AsksForCredentials)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        started = time.monotonic()
        result = shadow.push(f"http://127.0.0.1:{server.server_port}/repo.git", git(repo, "rev-parse", "HEAD"), DST, "",
                             timeout=60)
        took = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
    assert result.kind == "failed" and result.detail and took < 30


def _fingerprint(gitdir):
    """A hash of a .git: its refs, packed-refs, index, config, HEAD and the list of its objects."""
    gitdir, h = Path(gitdir), hashlib.sha256()
    for path in sorted(p for p in (gitdir / "refs").rglob("*") if p.is_file()):
        h.update(path.relative_to(gitdir).as_posix().encode() + b"\0" + path.read_bytes())
    for name in ("packed-refs", "index", "config", "HEAD"):
        path = gitdir / name
        h.update(name.encode() + b"\0" + (path.read_bytes() if path.exists() else b"(none)"))
    for path in sorted(p for p in (gitdir / "objects").rglob("*") if p.is_file()):
        h.update(path.relative_to(gitdir).as_posix().encode() + b"\0")
    return h.hexdigest()


def _exists(git_dir, oid):
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    return subprocess.run(["git", "cat-file", "-e", oid], cwd=git_dir, env=env, capture_output=True).returncode == 0


def test_prune_keeps_alternates_objects_and_the_users_repo_fingerprint(state, repo, origin, git):
    shadow = remote.Shadow(state, str(repo / ".git"))
    shadow.fetch_refspecs(str(origin), ["+HEAD:refs/orch/origin/HEAD"])
    users = git(repo, "rev-parse", "HEAD")                            # an object the shadow reads from the user's store
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    kept = git(shadow.path, "commit-tree", tree, "-p", users, "-m", "record")
    git(shadow.path, "update-ref", "refs/orch-out/origin/record", kept)
    gone = git(shadow.path, "commit-tree", tree, "-p", users, "-m", "an older record")     # nothing refers to it
    before = _fingerprint(repo / ".git")
    shadow.prune()
    assert _exists(shadow.path, kept) and _exists(shadow.path, users) and not _exists(shadow.path, gone)
    assert (Path(shadow.path) / "objects" / "info" / "alternates").is_file()
    assert _fingerprint(repo / ".git") == before


def test_push_url_uses_pushurl_and_pushinsteadof(repo, origin, git, tmp_path):
    common = str(repo / ".git")
    assert remote.push_url(common, "origin") == os.path.normpath(str(origin))
    git(repo, "remote", "set-url", "origin", "../origin.git")             # relative to the main worktree
    assert remote.push_url(common, "origin") == os.path.normpath(str(origin))
    git(repo, "config", "remote.origin.pushurl", "https://push.example.invalid/r.git")
    assert remote.push_url(common, "origin") == "https://push.example.invalid/r.git"
    git(repo, "config", "--unset", "remote.origin.pushurl")
    git(repo, "remote", "set-url", "origin", "https://read.example.invalid/r.git")
    git(repo, "config", "url.ssh://git@write.example.invalid/.pushInsteadOf", "https://read.example.invalid/")
    assert remote.push_url(common, "origin") == "ssh://git@write.example.invalid/r.git"
    assert remote.remote_url(common, "origin") == "https://read.example.invalid/r.git"   # fetching is unchanged
    with pytest.raises(remote.RemoteError, match="no remote named upstream"):
        remote.push_url(common, "upstream")
