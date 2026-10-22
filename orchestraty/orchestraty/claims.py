"""Claims: what one session reserves, so other sessions hear of it before they edit there.

``orch claim <target>``, or an agent's MCP tool ``claim``, reserves for one session a file, a
folder (an area: every file in it) or a Python function or class, named ``path::name`` or
``module.name`` and resolved through the engine to the file that defines it. It holds for
``claim_minutes`` (120 by default, in config.json) or the time given, until it is released
(``orch release``, the MCP tool ``release``), or until its session ends. A hard claim makes
another session's edit there wait for the person's answer, where its agent can ask.

Neither the command nor the MCP server writes the record: each appends one line to orch's own
spool, and the coordinator stores the claim (record schema 4), so a claim survives a
coordinator restart. Targets are kept in canonical form: a posix path from the top of the
repository for a file or a folder; ``path::Qual.name`` for a symbol, its file beside it.
"""
import os
import re
import time

from orchestraty import feedback, gitio

KINDS = ("file", "area", "symbol")
MAX_TARGET = 300
MAX_SESSION = 200
MAX_MINUTES = 24 * 60
_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m?)?")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+")
_ROOT_FILES = ("pyproject.toml", "setup.cfg", "pytest.ini")


class Refused(Exception):
    """What was asked cannot be claimed or released; the message says why."""


def default_minutes(cfg):
    """How long a claim holds when its maker does not say: ``claim_minutes``, kept within bounds."""
    return min(max(int(cfg["claim_minutes"]), 1), MAX_MINUTES)


def parse_minutes(text):
    """--for: minutes ("90" or "90m"), hours ("2h") or both ("1h30m"), from 1 minute to a day."""
    m = _DURATION.fullmatch(str(text).strip())
    if not m or not (m.group(1) or m.group(2)):
        raise ValueError(f"{text}: a time such as 30m, 2h or 1h30m")
    minutes = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    if not 1 <= minutes <= MAX_MINUTES:
        raise ValueError(f"{text}: from 1 minute to {MAX_MINUTES // 60} hours")
    return minutes


# ------------------------------------------------------------------------------- targets
def inside(path, top):
    """Whether `path` is the folder `top` or inside it."""
    return _rel(top, path) is not None


def _rel(top, full):
    """The posix path of `full` from the worktree's top, or None when it is outside."""
    try:
        rel = os.path.relpath(os.path.realpath(full), os.path.realpath(top))
    except ValueError:                                  # another drive
        return None
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return None
    return "" if rel == os.curdir else rel.replace(os.sep, "/")


def _full(top, cwd, path):
    """The path `path` names: absolute, or relative to `cwd`, else to the worktree's top."""
    if os.path.isabs(path):
        return path
    full = os.path.join(cwd, path)
    return full if os.path.exists(full) else os.path.join(top, path)


def _qualnames(top, rel):
    """The qualified names of the functions and classes in the Python file `rel`, as the engine
    parses it."""
    from orch_engine.parsing import parse_python
    try:
        with open(os.path.join(top, *rel.split("/")), "r", encoding="utf-8", errors="replace") as fh:
            return {s.qualname for s in parse_python(rel, fh.read()).symbols}
    except OSError:
        return set()


def _defined(top, rel, name):
    """`name` when the Python file `rel` defines a function or class of that qualified name, else None."""
    return name if name in _qualnames(top, rel) else None


def _modules(top):
    """{module name: file} for this worktree's Python files, as the engine names modules."""
    from orch_engine.graph import python_modules
    from orch_engine.models import ParsedFile
    try:
        listed = gitio.run(["ls-files", "-co", "--exclude-standard", "-z"], top).split("\0")
    except gitio.GitError:
        return {}
    files = []
    for rel in listed:
        if rel.endswith(".py"):
            files.append(ParsedFile(file=rel, language="python"))
        elif rel.rsplit("/", 1)[-1] in _ROOT_FILES:
            try:
                with open(os.path.join(top, *rel.split("/")), "r", encoding="utf-8", errors="replace") as fh:
                    files.append(ParsedFile(file=rel, language="config", text=fh.read()))
            except OSError:
                pass
    return python_modules(files)[0]


def resolve(top, cwd, target):
    """(kind, canonical target, file) for what `target` names in the worktree whose top is
    `top`: a file or a folder (absolute, or relative to `cwd`, else to `top`), ``path::name``,
    or ``module.name``. Raises Refused when it names nothing claimable."""
    target = (target or "").strip()
    if not target:
        raise Refused("name a file, a folder, or a Python function or class")
    if "::" in target:
        path, name = (x.strip() for x in target.split("::", 1))
        full = _full(top, cwd, path)
        rel = _rel(top, full) if os.path.isfile(full) else None
        if not rel:
            raise Refused(f"{path} is not a file in this worktree")
        quals = _qualnames(top, rel) if rel.endswith(".py") else set()
        if name not in quals:
            methods = sorted(q for q in quals if q.endswith("." + name))
            hint = f" (did you mean {' or '.join(methods[:3])}?)" if methods else ""
            raise Refused(f"{rel} defines no function or class named {name}{hint}")
        return "symbol", f"{rel}::{name}", rel
    full = _full(top, cwd, target)
    if os.path.isfile(full) or os.path.isdir(full):
        rel = _rel(top, full)
        if rel is None:
            raise Refused(f"{target} is not inside this worktree")
        if rel == "" or rel == ".git" or rel.startswith(".git/"):
            raise Refused("claim a file or a folder inside the repository, not all of it")
        return ("file" if os.path.isfile(full) else "area"), rel, ""
    if _DOTTED.fullmatch(target):
        modules, parts = _modules(top), target.split(".")
        for i in range(len(parts) - 1, 0, -1):
            rel = modules.get(".".join(parts[:i]))
            qual = _defined(top, rel, ".".join(parts[i:])) if rel else None
            if qual:
                return "symbol", f"{rel}::{qual}", rel
    raise Refused(f"{target}: no file or folder of that name here, and no Python function or class")


def note_key(claim_id):
    """The key the notes about a claim are kept under, where a finding's notes have its key."""
    return f"claim|{int(claim_id)}"


def from_note_key(key):
    """The number of the claim a note's key names, or None for a finding's key."""
    head, _, number = str(key).partition("|")
    return int(number) if head == "claim" and number.isdigit() else None


def path_of(claim):
    """The path a claim's notes are keyed by: the file, the folder, or the symbol's file."""
    return claim["file"] if claim["kind"] == "symbol" else claim["target"]


def covers(claim, rel):
    """Whether a claim covers the file at posix path `rel` (from the repository's top)."""
    if claim["kind"] == "area":
        return rel == claim["target"] or rel.startswith(claim["target"].rstrip("/") + "/")
    return rel == path_of(claim)


def describe(claim):
    """What a claim reserves, in a few words (identifiers as they are; notes clean them)."""
    if claim["kind"] == "symbol":
        return f"{claim['target'].split('::', 1)[1]} in {claim['file']}"
    if claim["kind"] == "area":
        return f"the folder {claim['target']}/"
    return claim["target"]


# ------------------------------------------------------------------------------- the spool
def append_claim(state, session_id, common_dir, kind, target, file, hard, minutes, now=None):
    """Append one claim for the coordinator to store."""
    feedback.append_line(state, "claim", time.time() if now is None else now, session_id=session_id,
                         repo=common_dir, kind=kind, target=target, file=file, hard=bool(hard), minutes=int(minutes))


def append_release(state, common_dir, target="", claim_id=None, session_id="", now=None):
    """Append the release of one claim (by its id), or of the claims on a target that hold in
    this repository (only `session_id`'s, when given)."""
    fields = {"repo": common_dir, "session_id": session_id or ""}
    if claim_id is not None:
        fields["id"] = int(claim_id)
    else:
        fields["target"] = target
    feedback.append_line(state, "release", time.time() if now is None else now, **fields)


def _text(value, limit, empty=False):
    return isinstance(value, str) and (empty or value) and len(value) <= limit


def parse_line(line):
    """The claim or release one line of orch's spool carries, checked, or None for a line of
    any other shape, whoever wrote it."""
    event, repo = line.get("event"), line.get("repo")
    if event not in ("claim", "release") or not (isinstance(repo, str) and os.path.isabs(repo)):
        return None
    sid = line.get("session_id", "")
    if not _text(sid, MAX_SESSION, empty=event == "release"):
        return None
    if event == "release":
        claim_id, target = line.get("id"), line.get("target")
        if isinstance(claim_id, int) and not isinstance(claim_id, bool) and claim_id > 0:
            return {"event": "release", "repo": repo, "id": claim_id, "session_id": sid}
        if _text(target, MAX_TARGET):
            return {"event": "release", "repo": repo, "target": target, "session_id": sid}
        return None
    kind, target, file, hard, minutes = (line.get(k) for k in ("kind", "target", "file", "hard", "minutes"))
    if kind not in KINDS or not _text(target, MAX_TARGET) or not _text(file, MAX_TARGET, empty=True):
        return None
    if (kind == "symbol") != bool(file and "::" in target) or not isinstance(hard, bool):
        return None
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
        return None
    return {"event": "claim", "repo": repo, "session_id": sid, "kind": kind, "target": target, "file": file,
            "hard": hard, "minutes": minutes}
