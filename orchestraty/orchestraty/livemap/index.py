"""Parsed files and code graphs for the live map.

* ``ParseCache``: the engine's parse of one file, keyed by (path, blob id), so every worktree
  and session of a repository shares one parse of the same content.
* ``BaseIndexes``: the engine's index of a whole commit (``build_index_at``, read from git's
  objects), built once per (repository, commit) on a thread of its own, since a cold build can
  take minutes on a large repository; until it is ready only file- and function-level rules run.
* ``view``: a session's code graph, the base commit's parsed files with the session's changed
  files put in, for finding who calls what in that session's copy of the code.
"""
import hashlib
import logging
import os
import queue
import threading
from collections import OrderedDict

from orch_engine.config import Config
from orch_engine.graph import build_graph
from orch_engine.ingest import blob_text
from orch_engine.parsing import parse_file
from orch_engine.pipeline import build_index_at


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


def view(base_index, changed):
    """A session's code graph: the base commit's parsed files, with each changed path
    replaced by its ParsedFile (or removed, for None)."""
    files = {pf.file: pf for pf in base_index.parsed_files}
    for path, pf in changed.items():
        if pf is None:
            files.pop(path, None)
        else:
            files[path] = pf
    return build_graph(list(files.values()))
