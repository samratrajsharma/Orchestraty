"""Git-hook recorder: one git hook firing in, one spool line out.

Status: throwaway (Phase 0). Not part of the product.

The chain scripts that capture_githooks.py installs run it as

    python -S -I capture_githook.py <spool_dir> <hook> [<hook arguments>...]

with the hook's own stdin (pre-push and post-rewrite get ref lines there). It appends one
line to <spool_dir>/git/<YYYY-MM-DD>.jsonl:

    {"ts": <ns>, "pid": <pid>, "agent": "git", "event": "<hook>", "cwd": "...", "head": "...",
     "args": [...], "refs": [[...], ...]}

  * cwd: the working tree the hook ran in (git runs hooks from its top folder);
  * head: the branch HEAD names (refs/heads/...), or the commit id when HEAD is detached,
    read from the HEAD file only;
  * args: only commit ids, the flags git passes (0 or 1, amend or rebase) and a remote's
    name. A remote's URL is never kept, since it can carry a user name or a token;
  * refs: for pre-push, [local ref, local id, remote ref, remote id] per line; for
    post-rewrite, [old id, new id] per line. Ref names outside a plain character set
    become "?".

Silent and fail-open, like capture_shim.py, whose atomic append it uses: it prints nothing,
always exits 0, and writes a shim_error line on an internal failure. Built-in modules only.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # -I leaves the script's folder out
import capture_shim  # noqa: E402

HOOKS = ("post-commit", "pre-push", "post-checkout", "post-merge", "post-rewrite")
MAX_REF_LINES = 1000
_HEX = b"0123456789abcdef"
_REF_CHARS = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-"


def json_string(value):
    """A JSON string literal (bytes) for any str, without the json module."""
    out = [b'"']
    for ch in value:
        o = ord(ch)
        if ch == '"':
            out.append(b'\\"')
        elif ch == "\\":
            out.append(b"\\\\")
        elif o < 32 or 0xD800 <= o <= 0xDFFF:     # control characters; lone surrogates from odd paths
            out.append(b"\\u%04x" % o)
        else:
            out.append(ch.encode("utf-8"))
    out.append(b'"')
    return b"".join(out)


def is_commit_id(value):
    return 4 <= len(value) <= 64 and all(c in _HEX for c in value)


def safe_ref(value):
    return value if 0 < len(value) <= 250 and all(c in _REF_CHARS for c in value) else b"?"


def kept_args(hook, args):
    """The hook arguments worth keeping, as bytes: commit ids and flags, never a URL."""
    kept = []
    for i, arg in enumerate(args):
        raw = arg.encode("utf-8", "replace")
        if is_commit_id(raw) or raw in (b"0", b"1", b"amend", b"rebase"):
            kept.append(raw)
        elif hook == "pre-push" and i == 0:          # the remote's name (its URL is argument 2)
            kept.append(safe_ref(raw))
    return kept


def ref_lines(hook, data):
    rows = []
    for line in data.splitlines()[:MAX_REF_LINES]:
        parts = line.split()
        if hook == "pre-push" and len(parts) == 4:
            rows.append([safe_ref(parts[0]), parts[1] if is_commit_id(parts[1]) else b"?",
                         safe_ref(parts[2]), parts[3] if is_commit_id(parts[3]) else b"?"])
        elif hook == "post-rewrite" and len(parts) >= 2:
            rows.append([p if is_commit_id(p) else b"?" for p in parts[:2]])
    return rows


def head_of(cwd):
    """The branch HEAD names, or the detached commit id, from the HEAD file; b"" if unknown."""
    git_dir = os.environ.get("GIT_DIR") or os.path.join(cwd, ".git")
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(cwd, git_dir)
    if os.path.isfile(git_dir):                       # a linked worktree: "gitdir: <path>"
        with open(git_dir, "rb") as fh:
            target = fh.read(4096).strip()
        if not target.startswith(b"gitdir:"):
            return b""
        git_dir = os.path.join(cwd, target[7:].strip().decode("utf-8", "replace"))
    with open(os.path.join(git_dir, "HEAD"), "rb") as fh:
        head = fh.read(512).strip()
    if head.startswith(b"ref:"):
        return safe_ref(head[4:].strip())
    return head if is_commit_id(head) else b""


def event_line(hook, args, stdin_data, cwd, now_ns, pid):
    hook_b = hook.encode("ascii")
    parts = [b'{"ts":%d,"pid":%d,"agent":"git","event":"%s","cwd":%s' % (now_ns, pid, hook_b, json_string(cwd))]
    try:
        head = head_of(cwd)
    except (OSError, ValueError):
        head = b""
    parts.append(b',"head":"%s"' % head)
    parts.append(b',"args":[%s]' % b",".join(b'"%s"' % a for a in kept_args(hook, args)))
    rows = ref_lines(hook, stdin_data)
    if rows:
        parts.append(b',"refs":[%s]' % b",".join(b"[" + b",".join(b'"%s"' % v for v in row) + b"]"
                                                 for row in rows))
    parts.append(b"}\n")
    return b"".join(parts)


def main(argv):
    if len(argv) < 2:
        return
    folder = os.path.join(argv[0], "git")
    path = os.path.join(folder, time.strftime("%Y-%m-%d") + ".jsonl")
    try:
        hook = argv[1]
        if hook not in HOOKS:
            raise ValueError("not a captured hook")
        data = sys.stdin.buffer.read() if hook in ("pre-push", "post-rewrite") and sys.stdin else b""
        capture_shim.append(path, event_line(hook, argv[2:], data, os.getcwd(), time.time_ns(), os.getpid()))
    except BaseException as exc:
        try:
            capture_shim.append(path, capture_shim.error_line(b"git", exc, time.time_ns(), os.getpid()))
        except BaseException:
            pass


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BaseException:
        pass
