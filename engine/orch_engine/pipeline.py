from __future__ import annotations
import hashlib
import hmac
import json
import os
import pickle

from . import models
from .config import CONFIG
from .ingest import MAX_BLOB_BYTES, blob_text, ingest, ingest_commit, read_blobs
from .parsing import parse_file
from .graph import build_graph


class RepoIndex:
    """Everything the engine knows about one repository at one commit: the parsed files
    and the code graph built from them. `not_analysed` lists what the index left out
    (skipped folders, symlinks, submodules, oversized files) and what binding could not
    cover (namespace packages, TypeScript path aliases, ...) as {"path", "reason"}, so a
    gap is stated rather than read as "nothing there"."""

    def __init__(self, meta, parsed_files, graph, config, not_analysed=None):
        self.meta = meta
        self.parsed_files = parsed_files
        self.graph = graph
        self.config = config
        self.not_analysed = list(not_analysed or []) + list(getattr(graph, "not_analysed", []))
        self.memo = {}          # per-index cache for pure analyses (insights, overview, ...)

    def stats(self):
        s = self.graph.stats()
        return {
            "repo": self.meta.name, "commit": self.meta.commit[:12],
            "is_git": self.meta.is_git, "files_parsed": len(self.parsed_files),
            "python_files": sum(1 for p in self.parsed_files
                                if p.language in ("python", "javascript", "typescript")),
            "parse_errors": sum(1 for p in self.parsed_files if p.error),
            "symbols": s["node_types"].get("symbol", 0),
            "graph_nodes": s["nodes"], "graph_edges": s["edges"],
            "edge_types": s["edge_types"],
        }

# ---------------- cache + multi-repo registry ----------------
def _signature(meta, files):
    # Fingerprint = commit sha + the ACTUAL working-tree state. The working-tree
    # fingerprint used to be computed only for non-git folders (commit=="working-tree");
    # for a git repo the signature was the HEAD sha alone, so uncommitted edits did NOT
    # bust the cache. That silently broke Timeline/Track on git repos: track/dirty saw
    # the edit, but capture reused the stale HEAD-keyed parse and reported "no change"
    # (QA finding G-05). Always hashing the tree makes any uncommitted edit invalidate
    # the cache, which is the whole point of over-time tracking. The schema version is in
    # it too, so an entry another engine version wrote is never served.
    h = hashlib.sha1()
    h.update(("schema-%d|" % models.ENGINE_SCHEMA).encode())
    h.update((meta.commit or "").encode())
    for rel, ab in sorted(files):
        try:
            st = os.stat(ab)
            h.update(f"{rel}:{int(st.st_mtime)}:{st.st_size}".encode())
        except OSError:
            pass
    return h.hexdigest()[:16]


def _cache_file(data_dir, meta, sig):
    d = os.path.join(data_dir, "cache")
    os.makedirs(d, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in meta.name)[:40] or "repo"
    return os.path.join(d, f"{safe}_v{models.ENGINE_SCHEMA}_{sig}.pkl")


def _cache_secret(data_dir):
    """A per-install secret used to HMAC-sign cache blobs (created once, 0600)."""
    d = os.path.join(data_dir, "cache")
    os.makedirs(d, exist_ok=True)
    key_path = os.path.join(d, ".hmac_key")
    try:
        with open(key_path, "rb") as fh:
            k = fh.read()
        if len(k) >= 32:
            return k
    except OSError:
        pass
    k = os.urandom(32)
    try:
        with open(key_path, "wb") as fh:
            fh.write(k)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return k


def _cache_load(path, secret):
    """Load a cache blob only if its HMAC matches — never unpickle unverified bytes. A missing,
    truncated or tampered file (e.g. a planted .pkl on a shared/synced data dir) is treated as a
    cache miss and rebuilt, closing the pickle-RCE vector."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    if len(raw) < 32:
        return None
    sig, blob = raw[:32], raw[32:]
    if not hmac.compare_digest(sig, hmac.new(secret, blob, hashlib.sha256).digest()):
        return None
    try:
        return pickle.loads(blob)
    except Exception:
        return None


def _load_index_parts(path, secret):
    """(parsed, graph) from a cache file, or None unless it is a verified entry that this
    engine version wrote."""
    loaded = _cache_load(path, secret)
    if isinstance(loaded, dict) and loaded.get("schema") == models.ENGINE_SCHEMA:
        return loaded["parsed"], loaded["graph"]
    return None


def _dump_index_parts(path, parsed, graph, secret):
    try:
        _cache_dump(path, {"schema": models.ENGINE_SCHEMA, "parsed": parsed, "graph": graph}, secret)
    except Exception:
        pass


def _cache_dump(path, obj, secret):
    blob = pickle.dumps(obj)
    sig = hmac.new(secret, blob, hashlib.sha256).digest()
    with open(path, "wb") as fh:
        fh.write(sig + blob)


def record_repo(data_dir, source, meta):
    try:
        os.makedirs(data_dir, exist_ok=True)
        f = os.path.join(data_dir, "repos.json")
        reg = json.load(open(f)) if os.path.exists(f) else []
        reg = [e for e in reg if e.get("source") != source]
        reg.insert(0, {"source": source, "name": meta.name, "commit": meta.commit[:12]})
        json.dump(reg[:20], open(f, "w"), indent=2)
    except Exception:
        pass


def recent_repos(data_dir):
    try:
        f = os.path.join(data_dir, "repos.json")
        return json.load(open(f)) if os.path.exists(f) else []
    except Exception:
        return []


def build_index(source, config=CONFIG, progress=None, use_cache=None, register=True):
    def log(m):
        if progress:
            progress(m)

    if use_cache is None:
        use_cache = config.use_cache
    log("Ingesting repository ...")
    not_analysed = []
    meta, code_files, doc_files = ingest(source, config.data_dir, not_analysed=not_analysed)
    files = code_files + doc_files

    parsed = graph = None
    if use_cache:                                 # with use_cache off, nothing is written to data_dir
        cache = _cache_file(config.data_dir, meta, _signature(meta, files))
        secret = _cache_secret(config.data_dir)
        if os.path.exists(cache):
            log("Loading cached parse/graph ...")
            parts = _load_index_parts(cache, secret)     # HMAC-verified; None on miss/tamper
            if parts is not None:
                parsed, graph = parts

    if parsed is None:
        log(f"Parsing {len(code_files)} code + {len(doc_files)} doc files ...")
        parsed = [parse_file(rel, ab) for rel, ab in files]
        log("Building code graph ...")
        graph = build_graph(parsed)
        if use_cache:
            _dump_index_parts(cache, parsed, graph, secret)

    if register:
        record_repo(config.data_dir, source, meta)
    log("Done.")
    return RepoIndex(meta, parsed, graph, config, not_analysed)


def build_index_at(source, ref="HEAD", config=CONFIG, progress=None, use_cache=None, register=False,
                   max_bytes=MAX_BLOB_BYTES):
    """Index commit `ref` of a git repository straight from its object store, at the
    repository's top folder. Nothing is checked out and nothing is written into the
    repository: no worktree, no archive. The commit is resolved to its full SHA first, and
    that SHA (never a ref name or a prefix) keys the cache, so a moved branch or another
    clone can never be served a stale entry. `use_cache` is honoured both ways."""
    def log(m):
        if progress:
            progress(m)

    if use_cache is None:
        use_cache = config.use_cache
    log("Reading commit %s from git objects ..." % ref)
    meta, code_files, doc_files, not_analysed = ingest_commit(source, ref, config.data_dir, max_bytes=max_bytes)
    files = code_files + doc_files
    cache = os.path.join(config.data_dir, "cache",
                         "commit-%s-%d-v%d.pkl" % (meta.commit, max_bytes, models.ENGINE_SCHEMA))

    parsed = graph = None
    if use_cache:
        secret = _cache_secret(config.data_dir)
        if os.path.exists(cache):
            log("Loading cached parse/graph ...")
            parts = _load_index_parts(cache, secret)     # HMAC-verified; None on miss/tamper
            if parts is not None:
                parsed, graph = parts

    if parsed is None:
        log(f"Parsing {len(code_files)} code + {len(doc_files)} doc files ...")
        blobs = read_blobs(meta.path, [sha for _, sha in files])
        parsed = [parse_file(rel, None, text=blob_text(blobs[sha])) for rel, sha in files]
        log("Building code graph ...")
        graph = build_graph(parsed)
        if use_cache:
            _dump_index_parts(cache, parsed, graph, secret)

    if register:
        record_repo(config.data_dir, source, meta)
    log("Done.")
    return RepoIndex(meta, parsed, graph, config, not_analysed)
