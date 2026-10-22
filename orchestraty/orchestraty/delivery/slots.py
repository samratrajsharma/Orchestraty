"""Slots: the ready-made files the hook script hands to an agent.

A slot holds the complete output one hook prints, written in advance by the coordinator:

* ``notes/<session>/edit/<file key>.json``: PreToolUse, before the session edits that file;
* ``notes/<session>/area/<folder key>.json``: PreToolUse too, before it edits any file in that
  folder or below it (a claimed area), when the file has no slot of its own waiting; named
  ``.ask.json`` instead when the edit is to wait for the user's answer, and taken before the
  file's own slot then;
* ``notes/<session>/next.json``: PostToolUse, with the session's next tool result;
* ``notes/<session>/stop.json``: Stop, when the session tries to finish its turn;
* ``notes/<session>/wake.txt``: the text that wakes an idle session (wake is opt-in).

Exactly once, with no locks: the coordinator writes a slot only when its path is free, and
never overwrites one (``write`` returns False when the slot is still waiting); the hook takes a
slot by renaming it to a name of its own, then reads and deletes it. A slot whose notes the
record says were written and which is no longer on disk was taken. ``withdraw`` is a rename
too: when it fails the hook was first, and the note was delivered.

``file_key`` must give the same key as the hook script's copy of it (a test checks).
"""
import os
import sys

TAIL = 24                   # characters of the path kept readable in a key
_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."


def _fnv1a64(text):
    h = 0xCBF29CE484222325
    for byte in text.encode("utf-8", "surrogatepass"):
        h = ((h ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def file_key(path):
    """A short file name for an absolute path: the end of the path, escaped ("_" as "__",
    anything outside letters, digits, "-" and "." as "_<hex>_"), then a 64-bit hash of the
    whole path. The path is normalised and its separators unified, and where the file system
    ignores case (Windows, macOS) so does the key. Short, because a slot's full path must stay
    under Windows' 260-character limit."""
    p = os.path.normpath(path).replace("\\", "/")
    if sys.platform in ("win32", "darwin"):
        p = p.lower()
    tail = "".join(c if c in _SAFE else ("__" if c == "_" else "_%x_" % ord(c)) for c in p[-TAIL:])
    return tail[-TAIL:] + "~" + _fnv1a64(p)


def safe_id(session_id):
    """A session id as the hook script names its folder: letters, digits, "-" and "_" only."""
    kept = "".join(c for c in str(session_id) if c.isascii() and (c.isalnum() or c in "-_"))[:64]
    return kept or "nosession"


def folder(state, session_id):
    return os.path.join(state.root, "notes", safe_id(session_id))


def edit_slot(state, session_id, path):
    return os.path.join(folder(state, session_id), "edit", file_key(path) + ".json")


def area_slot(state, session_id, path, ask=False):
    """The slot taken before the session edits any file in the folder `path` (absolute) or below
    it; `ask`: the one of a note that makes the edit wait for the user's answer."""
    return os.path.join(folder(state, session_id), "area", file_key(path) + (".ask.json" if ask else ".json"))


def next_slot(state, session_id):
    return os.path.join(folder(state, session_id), "next.json")


def stop_slot(state, session_id):
    return os.path.join(folder(state, session_id), "stop.json")


def wake_slot(state, session_id):
    return os.path.join(folder(state, session_id), "wake.txt")


def write(path, data):
    """Put `data` at `path` if no slot is waiting there. True when written."""
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "wb") as fh:
        fh.write(data)
    try:
        if os.name == "nt":
            os.rename(tmp, path)               # fails when the target exists
        else:
            try:
                os.link(tmp, path)             # fails when the target exists
            except FileExistsError:
                raise
            except OSError:                    # a file system without hard links
                if os.path.exists(path):
                    raise FileExistsError(path)
                os.replace(tmp, path)
            else:
                os.remove(tmp)
    except FileExistsError:
        os.remove(tmp)
        return False
    with open(path, "rb") as fh:               # read back once: a scanner's first look is off the hook's path
        fh.read()
    return True


def withdraw(path):
    """Take a waiting slot back. True when withdrawn; False when the hook took it first."""
    gone = "%s.%d.withdrawn" % (path, os.getpid())
    try:
        os.rename(path, gone)
    except (FileNotFoundError, PermissionError):  # taken, or being taken (Windows: held open by the hook)
        return False
    os.remove(gone)
    return True


def waiting(path):
    return os.path.exists(path)
