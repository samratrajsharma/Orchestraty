"""Parsed files and code graphs for the live map.

* ``ParseCache``: the engine's parse of one file, keyed by (path, blob id), so every worktree
  and session of a repository shares one parse of the same content.
* ``BaseIndexes``: the engine's index of a whole commit (``build_index_at``, read from git's
  objects), built once per (repository, commit) on a thread of its own, since a cold build can
  take minutes on a large repository; until it is ready only file- and function-level rules run.
  A commit read from a bare repository (the shadow of cloud branches) goes through the same
  engine readers, ``index_from_objects``.
* ``view``: a session's code graph, the base commit's parsed files with the session's changed
  files put in (``overlay``), for finding who calls what in that session's copy of the code.
"""
import hashlib
import logging
import os
import queue
import threading
from collections import OrderedDict

from orch_engine.config import Config
from orch_engine.graph import build_graph
from orch_engine.ingest import blob_text, commit_files, read_blobs, resolve_commit
from orch_engine.models import RepoMeta
from orch_engine.parsing import parse_file
from orch_engine.pipeline import RepoIndex, build_index_at


def is_bare(path):
    """Whether `path` is a bare repository (a git directory with no worktree)."""
    return (os.path.isfile(os.path.join(path, "HEAD")) and os.path.isdir(os.path.join(path, "objects"))
            and not os.path.exists(os.path.join(path, ".git")))


def index_from_objects(git_dir, commit, config):
    """The engine's index of `commit` in the bare repository `git_dir`, read the way
    build_index_at reads a commit (its files listed, then read from the object store), which
    needs a worktree's top folder; nothing is cached on disk."""
    sha = resolve_commit(git_dir, commit)
    code, docs, not_analysed = commit_files(git_dir, sha)
    files = code + docs
    blobs = read_blobs(git_dir, [blob for _, blob in files])
    parsed = [parse_file(rel, None, text=blob_text(blobs[blob])) for rel, blob in files]
    meta = RepoMeta(name=os.path.basename(git_dir.rstrip("/\\")), path=git_dir, commit=sha, is_git=True)
    return RepoIndex(meta, parsed, build_graph(parsed), config, not_analysed)


def blob_id(data):
    """git's blob id for these bytes (as git hash-object computes it, without filters)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class ParseCache:
    """ParsedFile per (posix path, blob id), least recently used first out."""

    def __init__(self, limit=4000):
        self.limit = limit
        self._items = OrderedDict()
        self.hits = self.misses = 0

    def get(self, path, data, blob=None):
        key = (path, blob or blob_id(data))
        pf = self._items.get(key)
        if pf is not None:
            self._items.move_to_end(key)
            self.hits += 1
            return pf
        self.misses += 1
        pf = parse_file(path, None, text=blob_text(data))
        self._items[key] = pf
        if len(self._items) > self.limit:
            self._items.popitem(last=False)
        return pf

    def known(self, path, blob):
        return (path, blob) in self._items

    def put(self, path, blob, pf):
        self._items[(path, blob)] = pf


class BaseIndexes:
    """Indexes of whole commits, built on one background thread and kept for the most
    recently used commits."""

    def __init__(self, data_dir, log=None, keep=6):
        self.config = Config(data_dir=data_dir, use_cache=True)
        self.log = log or logging.getLogger("orchestraty.coordinator")
        self.keep = keep
        self._ready = OrderedDict()            # (repo key, commit) -> RepoIndex
        self._failed = {}                      # (repo key, commit) -> reason
        self._queued = set()
        self._fresh = []                       # keys finished since take_fresh()
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._thread = None

    def get(self, repo_key, commit, worktree):
        """The index, or None while it is being built (the build is started if needed)."""
        key = (repo_key, commit)
        with self._lock:
            idx = self._ready.get(key)
            if idx is not None:
                self._ready.move_to_end(key)
                return idx
            if key in self._failed or key in self._queued or not commit:
                return None
            self._queued.add(key)
        self._queue.put((key, worktree))
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, name="base-index", daemon=True)
            self._thread.start()
        return None

    def failed(self, repo_key, commit):
        with self._lock:
            return self._failed.get((repo_key, commit))

    def forget_failures(self, repo_key):
        """Let the failed builds of a repository be tried again (its shadow was fetched afresh)."""
        with self._lock:
            for key in [k for k in self._failed if k[0] == repo_key]:
                del self._failed[key]

    def take_fresh(self):
        """Keys of the indexes finished since the last call."""
        with self._lock:
            fresh, self._fresh = self._fresh, []
        return fresh

    def wait(self, timeout=None):
        """Block until every queued build is done (for tests)."""
        done = threading.Event()
        self._queue.put((None, done))
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, name="base-index", daemon=True)
            self._thread.start()
        return done.wait(timeout)

    def _work(self):
        while True:
            key, worktree = self._queue.get()
            if key is None:
                worktree.set()                 # a wait() marker
                continue
            try:
                if is_bare(worktree):
                    idx = index_from_objects(worktree, key[1], self.config)
                else:
                    idx = build_index_at(worktree, key[1], self.config, use_cache=True)
            except Exception as exc:          # untrusted folder, a commit gone, ...
                with self._lock:
                    self._failed[key] = f"{type(exc).__name__}: {exc}"[:300]
                    self._queued.discard(key)
                self.log.warning("no index of %s at %s: %s", worktree, key[1][:12], exc)
                continue
            with self._lock:
                self._ready[key] = idx
                self._queued.discard(key)
                self._fresh.append(key)
                while len(self._ready) > self.keep:
                    self._ready.popitem(last=False)
            self.log.info("index of %s at %s ready (%d files)", os.path.basename(worktree), key[1][:12],
                          len(idx.parsed_files))


def overlay(base_index, changed):
    """A session's copy of the code: the base commit's parsed files, with each changed path
    replaced by its ParsedFile (or removed, for None)."""
    files = {pf.file: pf for pf in base_index.parsed_files}
    for path, pf in changed.items():
        if pf is None:
            files.pop(path, None)
        else:
            files[path] = pf
    return list(files.values())


def view(base_index, changed):
    """A session's code graph, built over its copy of the code (overlay)."""
    return build_graph(overlay(base_index, changed))
