"""Indexing any commit from git's object store (build_index_at), and snapshots built on it.

A commit is read with `ls-tree` and `cat-file --batch`: no worktree, no archive, nothing
written into the repository. Ids are POSIX, commits are full SHAs, git's output is read as
UTF-8, and what is left out (skipped folders, symlinks, submodules, oversized files) is
listed as not analysed. Every repository here is synthetic, in a temp folder.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from orch_engine import mcp_server, track
from orch_engine.config import Config
from orch_engine.graph import JS_TEXT_NOTE
from orch_engine.ingest import repo_meta, resolve_commit
from orch_engine.models import ENGINE_SCHEMA
from orch_engine.pipeline import build_index, build_index_at

ROOT = Path(__file__).resolve().parents[1]
AUTHOR = "Zoë Ångström"
SUBJECT = "Añadir módulo Á → ü"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available on PATH")


@pytest.fixture(scope="module", autouse=True)
def isolated_git(tmp_path_factory):
    """No user or system git config (autocrlf, hooks, signing) reaches these repositories."""
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("", encoding="utf-8")
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    os.environ["GIT_CONFIG_GLOBAL"] = str(empty)
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def git(repo, *args, env=None, stdin=None):
    e = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
             GIT_AUTHOR_NAME="Engine Test", GIT_AUTHOR_EMAIL="t@example.com",
             GIT_COMMITTER_NAME="Engine Test", GIT_COMMITTER_EMAIL="t@example.com")
    e.update(env or {})
    r = subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c",
                        "core.autocrlf=false", "-c", "maintenance.auto=false", "-c", "gc.auto=0"] + list(args),
                       cwd=repo, capture_output=True, env=e, input=stdin, timeout=120)
    if r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr.decode('utf-8', 'replace')}")
    return r.stdout.decode("utf-8").strip()


def write(repo, files):
    for rel, content in files.items():
        p = Path(repo, *rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def add_entry(repo, mode, sha, path):
    """Put an entry in the index without a file on disk: a symlink or a submodule."""
    git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}")


BASE_FILES = {
    ".gitattributes": "tests/ export-ignore\n",
    "pkg/__init__.py": "",
    "pkg/sub/__init__.py": "",
    "pkg/sub/mod.py": "def compute(xs):\n    return sum(xs)\n",
    "tests/test_mod.py": "from pkg.sub.mod import compute\n\n\ndef test_compute():\n    assert compute([1, 2]) == 3\n",
    "env/vendored.py": "def hidden():\n    return 1\n",
    "app/build/generated.py": "def generated():\n    return 2\n",
    "big.py": "# padding\n" * 80 + "def big():\n    return 3\n",
    "README.md": "# Synthetic\n",
}


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    """Two commits. The base has a symlink and a submodule entry and a non-ASCII author and
    subject; the head adds a route file. The working tree is left at the head."""
    repo = tmp_path_factory.mktemp("history")
    git(repo, "init", "-q")
    write(repo, BASE_FILES)
    git(repo, "add", "-A")
    link_blob = git(repo, "hash-object", "-w", "--stdin", stdin=b"pkg/sub/mod.py")
    add_entry(repo, "120000", link_blob, "link.py")
    add_entry(repo, "160000", "1" * 40, "vendor/lib")
    names = {"GIT_AUTHOR_NAME": AUTHOR, "GIT_COMMITTER_NAME": AUTHOR}
    git(repo, "commit", "-q", "-m", SUBJECT, env=names)
    base = git(repo, "rev-parse", "HEAD")
    write(repo, {"api/__init__.py": "",
                 "api/routes.py": ("from fastapi import FastAPI\nfrom pkg.sub.mod import compute\n\n"
                                   "app = FastAPI()\n\n\n@app.get(\"/total\")\ndef total():\n"
                                   "    return compute([1])\n")})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add routes")
    head = git(repo, "rev-parse", "HEAD")
    return {"path": str(repo), "base": base, "head": head}


def _cfg(tmp_path, use_cache=False):
    return Config(data_dir=str(tmp_path / "data"), use_cache=use_cache)


def _git_dir_state(repo):
    """Every path under .git with its size and modification time."""
    out = []
    for folder, _, files in os.walk(os.path.join(repo, ".git")):
        for name in files:
            st = os.stat(os.path.join(folder, name))
            out.append((os.path.relpath(os.path.join(folder, name), repo), st.st_size, st.st_mtime_ns))
    return sorted(out)


# ----------------------------------------------------------------- reading a commit


def test_nothing_is_written_into_the_repository_and_no_worktree_is_made(history, tmp_path):
    repo = history["path"]
    status = git(repo, "status", "--porcelain")          # (status itself refreshes .git/index)
    before = _git_dir_state(repo)
    cfg = _cfg(tmp_path, use_cache=True)
    build_index_at(repo, history["base"], cfg)
    build_index_at(repo, "HEAD", cfg)
    track.snapshot(repo, history["base"], cfg)
    track.snapshot(repo, "main", cfg)
    assert not os.path.exists(os.path.join(repo, ".git", "worktrees"))
    assert _git_dir_state(repo) == before, "the repository's .git must be left exactly as it was"
    assert git(repo, "status", "--porcelain") == status
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_the_commit_is_named_by_its_full_sha_and_read_at_the_top_folder(history, tmp_path):
    idx = build_index_at(os.path.join(history["path"], "pkg"), history["base"], _cfg(tmp_path))
    assert idx.meta.commit == history["base"] and re.fullmatch(r"[0-9a-f]{40}", idx.meta.commit)
    assert idx.meta.is_git is True and idx.meta.n_commits == 1
    assert os.path.normcase(os.path.realpath(idx.meta.path)) == os.path.normcase(os.path.realpath(history["path"]))
    assert "pkg/sub/mod.py::compute" in idx.graph.nodes
    head = build_index_at(history["path"], "main", _cfg(tmp_path))
    assert head.meta.commit == history["head"] and head.meta.branch == "main"


def test_an_older_commit_is_indexed_as_it_was(history, tmp_path):
    base = build_index_at(history["path"], history["base"], _cfg(tmp_path))
    head = build_index_at(history["path"], history["head"], _cfg(tmp_path))
    assert "api/routes.py::total" in head.graph.nodes and "api/routes.py::total" not in base.graph.nodes
    assert ("api/routes.py", "pkg/sub/mod.py") in {(s, d) for s, d, _ in head.graph.all_edges("imports")}


def test_export_ignore_does_not_hide_files(history, tmp_path):
    idx = build_index_at(history["path"], history["base"], _cfg(tmp_path))
    assert "tests/test_mod.py::test_compute" in idx.graph.nodes


def test_what_is_left_out_is_listed_as_not_analysed(history, tmp_path):
    idx = build_index_at(history["path"], history["base"], _cfg(tmp_path), max_bytes=500)
    reasons = {e["path"]: e["reason"] for e in idx.not_analysed}
    assert reasons == {"app/build/": "skipped folder", "env/": "skipped folder", "link.py": "symlink",
                       "vendor/lib": "submodule", "big.py": "oversized (%d bytes)" % len(BASE_FILES["big.py"])}
    parsed = {p.file for p in idx.parsed_files}
    assert not parsed & {"link.py", "vendor/lib", "big.py", "env/vendored.py", "app/build/generated.py"}
    assert "big.py" in {p.file for p in build_index_at(history["path"], history["base"], _cfg(tmp_path)).parsed_files}


def test_ids_are_posix(history, tmp_path):
    idx = build_index_at(history["path"], history["head"], _cfg(tmp_path))
    assert not [n for n in idx.graph.nodes if "\\" in n]


def test_refs_that_are_not_commits_are_refused(history, tmp_path):
    for bad in ("", "-rf", "--output=x", "no-such-branch", "1" * 40):
        with pytest.raises(ValueError):
            resolve_commit(history["path"], bad)
    with pytest.raises(ValueError):
        track.snapshot(history["path"], "--all", _cfg(tmp_path))
    with pytest.raises(ValueError):
        build_index_at(str(tmp_path), "HEAD", _cfg(tmp_path))          # not a git repository


# ----------------------------------------------------------------- the same results as build_index


def _same(a, b):
    assert [p.file for p in a.parsed_files] == [p.file for p in b.parsed_files]
    assert a.parsed_files == b.parsed_files
    assert list(a.graph.nodes.items()) == list(b.graph.nodes.items())
    assert a.graph.all_edges() == b.graph.all_edges()


def _commit_all(repo):
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "all")


def test_it_matches_build_index_on_a_plain_checkout_of_the_sample_repo(tmp_path):
    repo = tmp_path / "sample"
    shutil.copytree(ROOT / "sample_repo", repo)
    _commit_all(repo)
    cfg = _cfg(tmp_path)
    _same(build_index(str(repo), cfg, register=False), build_index_at(str(repo), "HEAD", cfg))


def test_it_matches_build_index_on_a_nested_checkout_with_odd_bytes_and_line_ends(tmp_path):
    repo = tmp_path / "nested"
    files = {k: v for k, v in BASE_FILES.items()}
    files.update({
        "api/__init__.py": "",
        "api/routes.py": "from flask import Flask\napp = Flask(__name__)\n\n@app.route('/a')\ndef a():\n    return 1\n",
        "crlf.py": b"def crlf():\r\n    return 1\r\n\r\nx = 'a\rb'\r\n",
        "latin1.py": b"# caf\xe9\ndef latin():\n    return '\xe9'\n",
        "web/app.ts": "import { compute } from './util';\nexport function run() { return compute(); }\n",
        "web/util.ts": "export function compute() { return 1; }\n",
        "docs/guide.md": "# Guide\n",
        "config/settings.yaml": "a: 1\n",
    })
    write(repo, files)
    _commit_all(repo)
    cfg = _cfg(tmp_path)
    work, commit = build_index(str(repo), cfg, register=False), build_index_at(str(repo), "HEAD", cfg)
    _same(work, commit)
    assert work.not_analysed == commit.not_analysed
    assert {(e["path"], e["reason"]) for e in work.not_analysed} == {
        ("app/build/", "skipped folder"), ("env/", "skipped folder"), ("", JS_TEXT_NOTE)}


# ----------------------------------------------------------------- caches


def test_use_cache_is_honoured_and_keyed_by_the_full_sha(history, tmp_path):
    off = _cfg(tmp_path / "off", use_cache=False)
    build_index_at(history["path"], history["base"], off)
    track.snapshot(history["path"], history["base"], off)
    build_index(history["path"], off, register=False)
    assert not os.path.exists(off.data_dir), "use_cache off writes nothing"
    on = _cfg(tmp_path / "on", use_cache=True)
    first = build_index_at(history["path"], history["base"], on)
    names = os.listdir(os.path.join(on.data_dir, "cache"))
    assert f"commit-{history['base']}-2000000-v{ENGINE_SCHEMA}.pkl" in names
    logs = []
    again = build_index_at(history["path"], history["base"], on, progress=logs.append)
    assert "Loading cached parse/graph ..." in logs
    _same(first, again)


def test_a_snapshot_follows_a_moved_branch_and_names_its_commit_in_full(history, tmp_path):
    repo = history["path"]
    cfg = _cfg(tmp_path, use_cache=True)
    git(repo, "branch", "-f", "feature/moving", history["base"])
    first = track.snapshot(repo, "feature/moving", cfg)
    git(repo, "branch", "-f", "feature/moving", history["head"])
    second = track.snapshot(repo, "feature/moving", cfg)
    git(repo, "branch", "-D", "feature/moving")
    assert first["commit"] == history["base"] and second["commit"] == history["head"]
    assert "api/routes.py" in second["files"] and "api/routes.py" not in first["files"]
    assert sorted(os.listdir(os.path.join(cfg.data_dir, "snapshots"))) == sorted(
        [f"{history['base']}-v{ENGINE_SCHEMA}.json", f"{history['head']}-v{ENGINE_SCHEMA}.json"])


def test_structural_diff_through_mcp_uses_no_worktree(history, tmp_path, monkeypatch):
    repo = history["path"]
    idx = build_index(repo, _cfg(tmp_path), register=False)
    monkeypatch.setenv("ORCH_MCP_REPO", repo)
    monkeypatch.setattr(mcp_server, "_IDX", idx)
    monkeypatch.setattr(mcp_server, "_IDX_REPO", repo)
    res = mcp_server.structural_diff(history["base"])
    assert "api/routes.py" in res["delta"]["files_added"]
    assert mcp_server.routes_touched(base=history["base"])["count"] == 1
    assert not os.path.exists(os.path.join(repo, ".git", "worktrees"))


# ----------------------------------------------------------------- git detection and encoding


def test_a_linked_worktree_is_recognised_as_git(tmp_path):
    repo = tmp_path / "main-checkout"
    write(repo, {"m.py": "def m():\n    return 1\n"})
    _commit_all(repo)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))       # made on purpose, in a temp repo
    assert os.path.isfile(linked / ".git")
    assert repo_meta(str(linked)).is_git is True
    assert repo_meta(str(repo)).is_git is True
    (repo / "sub").mkdir()
    assert repo_meta(str(repo / "sub")).is_git is False                  # a subfolder is not a repo top
    assert build_index_at(str(linked), "HEAD", _cfg(tmp_path)).meta.branch == "side"


def test_git_log_reads_a_non_ascii_author_and_subject(history):
    base = [c for c in track.git_log(history["path"]) if c["sha"] == history["base"]]
    assert base and base[0]["author"] == AUTHOR and base[0]["subject"] == SUBJECT


CHILD = """
import json, sys
sys.path.insert(0, sys.argv[1])
from orch_engine import track
from orch_engine.config import Config
from orch_engine.pipeline import build_index_at
repo, base, data = sys.argv[2], sys.argv[3], sys.argv[4]
log = track.git_log(repo)
idx = build_index_at(repo, base, Config(data_dir=data, use_cache=False))
print(json.dumps({"utf8_mode": sys.flags.utf8_mode, "log": log, "commit": idx.meta.commit,
                  "ids": sorted(idx.graph.nodes)}))
"""


def _without_utf8_mode():
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(PYTHONUTF8="0", PYTHONDONTWRITEBYTECODE="1")
    return env


def test_it_works_on_windows_without_utf8_mode(history, tmp_path):
    r = subprocess.run([sys.executable, "-c", CHILD, str(ROOT), history["path"], history["base"],
                        str(tmp_path / "data")], capture_output=True, env=_without_utf8_mode(), timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = json.loads(r.stdout.decode("ascii"))
    if os.name == "nt":
        assert out["utf8_mode"] == 0
    base = [c for c in out["log"] if c["sha"] == history["base"]]
    assert base[0]["author"] == AUTHOR and base[0]["subject"] == SUBJECT
    assert out["commit"] == history["base"] and "pkg/sub/mod.py::compute" in out["ids"]


CHILD_FOLDER = """
import json, sys
sys.path.insert(0, sys.argv[1])
from orch_engine.config import Config
from orch_engine.ingest import repo_meta
from orch_engine.pipeline import build_index_at
repo, data = sys.argv[2], sys.argv[3]
idx = build_index_at(repo, "HEAD", Config(data_dir=data, use_cache=False))
print(json.dumps({"is_git": repo_meta(repo).is_git, "ids": sorted(idx.graph.nodes)}))
"""


def test_a_repository_in_a_non_ascii_folder_without_utf8_mode(tmp_path):
    repo = tmp_path / "repö-Á"
    write(repo, {"m.py": "def m():\n    return 1\n"})
    _commit_all(repo)
    r = subprocess.run([sys.executable, "-c", CHILD_FOLDER, str(ROOT), str(repo), str(tmp_path / "data")],
                       capture_output=True, env=_without_utf8_mode(), timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = json.loads(r.stdout.decode("ascii"))
    assert out["is_git"] is True and "m.py::m" in out["ids"]
