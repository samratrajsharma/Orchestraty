"""Folder projects: Orchestraty's own snapshots of a folder that is not a git repository.

A session in such a folder (with ``watch_all`` on) needs a base to measure its changes against,
as a git session has its base commit. Its base is a snapshot of the folder's files taken when
the session is first seen, named ``snap:<id>``. Everything lives in the state folder, under
``folders/``; nothing is ever written into the project's folder.

- ``objects/<blob id>``: each file's bytes once, under git's blob id for them, so the live map's
  parse cache and blob logic work as they do for commits.
- ``projects/<key>/snap-<id>.json``: one manifest per snapshot, ``{path: blob id}``; a snapshot
  equal to an earlier one has the same id and is written once.
- ``projects/<key>/stat.json``: size and time of each file last read, so unchanged files are not
  read again.
- ``projects/<key>/tree-<id>/``: the snapshot's files written out, for the engine to index (the
  base graph), made on the index thread when first needed; the latest few are kept.

Files: every file under the folder up to MAX_FILE_BYTES, skipping version-control and tool
folders, dependencies, virtual environments and build output; a folder with more than MAX_FILES
such files is too large to follow this way (``TooLarge``). Symbolic links are not followed.
"""
import hashlib
import json
import os
import shutil
import time

from orchestraty.livemap.index import blob_id

PREFIX = "snap:"
MAX_FILE_BYTES = 2_000_000
MAX_FILES = 20_000
KEEP_TREES = 3
SKIP_DIRS = {".git", ".hg", ".svn", ".bzr", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".nox",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", "dist", "build", ".next", ".nuxt",
             ".svelte-kit", "target", ".gradle", ".idea", ".vscode", ".terraform", ".eggs", "site-packages",
             ".orchestraty", ".claude", ".codex", ".gemini", ".agents"}


class TooLarge(Exception):
    """The folder holds more than MAX_FILES files to follow."""


def is_snapshot(base):
    return isinstance(base, str) and base.startswith(PREFIX)


def _root(state_root):
    return os.path.join(state_root, "folders")


def _project(state_root, folder):
    key = hashlib.sha1(os.path.normcase(os.path.normpath(folder)).encode("utf-8", "surrogateescape")).hexdigest()
    return os.path.join(_root(state_root), "projects", key[:16])


def _object(state_root, blob):
    return os.path.join(_root(state_root), "objects", blob[:2], blob[2:])


def _skip_dir(name, path):
    if name in SKIP_DIRS or name.endswith(".egg-info"):
        return True
    return (os.path.exists(os.path.join(path, "pyvenv.cfg"))          # a virtual environment, by any name
            or os.path.lexists(os.path.join(path, ".git")))           # a nested checkout is its own project


def walk(folder):
    """[(posix path, absolute path)] of the files followed in `folder`, sorted. Raises TooLarge."""
    out = []
    for top, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not _skip_dir(d, os.path.join(top, d))
                         and not os.path.islink(os.path.join(top, d)))
        for name in files:
            path = os.path.join(top, name)
            if os.path.islink(path):
                continue
            out.append((os.path.relpath(path, folder).replace(os.sep, "/"), path))
            if len(out) > MAX_FILES:
                raise TooLarge(f"more than {MAX_FILES} files")
    out.sort()
    return out


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def current(state_root, folder, store=False):
    """{posix path: blob id} for the files in `folder` now, each read only when its size or
    time changed since it was last read. With `store`, each file's bytes go into the object
    store too."""
    project = _project(state_root, folder)
    stat_path = os.path.join(project, "stat.json")
    known = _load(stat_path, {})
    seen, files = {}, {}
    for rel, path in walk(folder):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size > MAX_FILE_BYTES:
            continue
        mark = [st.st_size, st.st_mtime_ns]
        old = known.get(rel)
        blob = old[2] if isinstance(old, list) and len(old) == 3 and old[:2] == mark else None
        if blob is None or (store and not os.path.exists(_object(state_root, blob))):
            try:
                with open(path, "rb") as fh:
                    data = fh.read(MAX_FILE_BYTES + 1)
            except OSError:
                continue
            if len(data) > MAX_FILE_BYTES:
                continue
            blob = blob_id(data)
            if store:
                target = _object(state_root, blob)
                if not os.path.exists(target):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    tmp = target + ".tmp"
                    with open(tmp, "wb") as fh:
                        fh.write(data)
                    os.replace(tmp, target)
        files[rel] = blob
        seen[rel] = mark + [blob]
    if seen != known:
        _write(stat_path, seen)
    return files


def take(state_root, folder):
    """Take a snapshot of `folder` now and return its base name, ``snap:<id>``."""
    files = current(state_root, folder, store=True)
    sid = hashlib.sha1(json.dumps(sorted(files.items())).encode("utf-8", "surrogateescape")).hexdigest()[:16]
    path = os.path.join(_project(state_root, folder), f"snap-{sid}.json")
    if not os.path.exists(path):
        _write(path, {"folder": os.path.normpath(folder), "taken": time.time(), "files": files})
    return PREFIX + sid


def manifest(state_root, folder, base):
    """{posix path: blob id} of a snapshot; {} when it is gone."""
    kept = _load(os.path.join(_project(state_root, folder), f"snap-{base[len(PREFIX):]}.json"), {})
    files = kept.get("files") if isinstance(kept, dict) else None
    return files if isinstance(files, dict) else {}


def changed_files(state_root, folder, base):
    """{posix path: "A" | "M" | "D"} for the files of `folder` that differ from snapshot `base`,
    as git's changed_files gives them for a commit."""
    then, now = manifest(state_root, folder, base), current(state_root, folder)
    out = {p: "A" if p not in then else "M" for p, b in now.items() if then.get(p) != b}
    nested = {"": False}
    for path in then.keys() - now.keys():
        parent = path.rpartition("/")[0]
        unchecked = []
        while parent not in nested:
            unchecked.append(parent)
            parent = parent.rpartition("/")[0]
        in_checkout = nested[parent]
        for parent in reversed(unchecked):
            in_checkout = in_checkout or os.path.lexists(os.path.join(folder, *parent.split("/"), ".git"))
            nested[parent] = in_checkout
        if not in_checkout:
            out[path] = "D"
    return out


def blob_ids(state_root, folder, base, paths):
    """{posix path: blob id} for those of `paths` that the snapshot holds."""
    files = manifest(state_root, folder, base)
    return {p: files[p] for p in paths if p in files}


def read_blobs(state_root, blobs):
    """{blob id: bytes} for those of `blobs` the object store holds."""
    out = {}
    for blob in blobs:
        try:
            with open(_object(state_root, blob), "rb") as fh:
                out[blob] = fh.read()
        except OSError:
            pass
    return out


def tree(state_root, folder, base):
    """A folder holding the snapshot's files, for the engine to index; written once, the latest
    KEEP_TREES kept."""
    project = _project(state_root, folder)
    target = os.path.join(project, "tree-" + base[len(PREFIX):])
    if os.path.isdir(target):
        os.utime(target)
        return target
    files = manifest(state_root, folder, base)
    tmp = target + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    for rel, blob in files.items():
        dest = os.path.join(tmp, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            shutil.copyfile(_object(state_root, blob), dest)
        except OSError:
            continue
    os.makedirs(tmp, exist_ok=True)
    os.replace(tmp, target)
    trees = sorted((os.path.getmtime(os.path.join(project, d)), d) for d in os.listdir(project)
                   if d.startswith("tree-") and not d.endswith(".tmp"))
    for _, old in trees[:-KEEP_TREES]:
        shutil.rmtree(os.path.join(project, old), ignore_errors=True)
    return target


def can_follow(folder, state_root):
    """(True, "") when `folder` can be a folder project; else (False, why). Never a drive's top
    folder, the home folder or a folder above it, or Orchestraty's own state folder."""
    f = os.path.normcase(os.path.normpath(os.path.abspath(folder)))
    if not os.path.isdir(folder):
        return False, "not a folder"
    if os.path.dirname(f) == f:
        return False, "a drive's top folder"
    home = os.path.normcase(os.path.normpath(os.path.expanduser("~")))
    if f == home or home.startswith(f.rstrip(os.sep) + os.sep):
        return False, "the home folder"
    state = os.path.normcase(os.path.normpath(os.path.abspath(state_root)))
    if f == state or f.startswith(state.rstrip(os.sep) + os.sep):
        return False, "Orchestraty's own folder"
    return True, ""
