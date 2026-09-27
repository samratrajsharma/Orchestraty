"""Read-only scanner: the collectors.

Status: throwaway (Phase 0). Not part of the product.

Describes, in counts, how coding agents are set up and used on this machine:
which agents are installed, how many sessions they recorded recently (from file
dates and sizes only), which git repositories show work in flight by different
people on the same files, which MCP helpers are configured, and how hooks are
allowed.

Safety properties (each one is covered by a test):
  * Session, transcript, history and sign-in files are never opened. Only their
    names, sizes and dates are read (os.scandir / os.stat). Config files that
    lead to one through a link or a hard link are skipped, and links inside a
    repository are never followed.
  * Nothing is written anywhere. Writing the result is the caller's job.
  * Git runs only the read-only command shapes in _git_args_allowed, started
    by its absolute path (never from the current folder), with every inherited
    GIT_* variable dropped, lazy fetching and all network protocols off,
    signature checks off, trace logs off and optional locks off. To tell
    rebased or squash-merged work apart, git compares file changes in memory;
    no file content is kept.
  * Network paths in helper settings are never looked up.
  * Paths, repository and branch names, emails, helper names and helper
    commands never appear in the output. Identifiers become salted hashes (the
    salt is random for each run and never written out); helper commands become
    a label from a fixed list.
  * Config files that are read (MCP and hook settings) are parsed in memory for
    counts and labels only. Values such as env, headers and args are never kept.

Runs on Python 3.9 or later, standard library only.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform
import posixpath
import re
import secrets
import subprocess
import time
from pathlib import Path

SCHEMA = "orch-scan/1"
SCANNER_VERSION = "0.3.1"

MAX_REPOS = 50          # repositories inspected per run
MAX_REFS = 100          # unmerged branches inspected per repository, newest first
MAX_WALK_DIRS = 20000   # directories visited while looking for repositories
GIT_TIMEOUT = 20        # seconds per git command
REPO_TIME_BUDGET = 300  # seconds for inspecting repositories, in total

# ------------------------------------------------------------------ environment


class Env:
    """Where things live on this machine.

    Everything the scanner looks at is derived from here. Env() describes the real
    machine. Env.synthetic() describes a made-up home folder laid out like Windows,
    macOS or Linux, and shares nothing with the real machine except PATH and the
    variables Windows needs to start programs.
    """

    def __init__(self, home=None, system=None, environ=None, root=None, now=None):
        # Machine-wide sources outside the file system (the Windows registry, git's system
        # config) are read only when scanning the real machine.
        self.real_machine = home is None and root is None and environ is None
        self.environ = dict(os.environ if environ is None else environ)
        self.home = Path(home) if home is not None else Path.home()
        self.system = system or platform.system()      # "Windows", "Darwin" or "Linux"
        self.root = Path(root) if root is not None else Path(os.path.abspath(os.sep))
        self.now = now if now is not None else time.time()
        self._git = None

    @classmethod
    def synthetic(cls, home, system, root=None, path=None, now=None, **extra_environ):
        """A hermetic Env for a synthetic home: no inherited variables, a private root."""
        home = Path(home)
        root = Path(root) if root is not None else home / ".synthetic-root"
        environ = {"PATH": os.environ.get("PATH", "") if path is None else path}
        # Variables Windows needs to start any program at all. They identify nobody.
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            if os.environ.get(name):
                environ[name] = os.environ[name]
        # A temp folder of its own, as every real machine has one (git writes temp files there).
        for name in ("TEMP", "TMP", "TMPDIR"):
            environ[name] = str(root / "tmp")
        if system == "Windows":
            environ["APPDATA"] = str(home / "AppData" / "Roaming")
            environ["LOCALAPPDATA"] = str(home / "AppData" / "Local")
            environ["ProgramFiles"] = str(root / "Program Files")
        environ.update(extra_environ)
        return cls(home=home, system=system, environ=environ, root=root, now=now)

    @property
    def is_windows(self):
        return self.system == "Windows"

    @property
    def is_macos(self):
        return self.system == "Darwin"

    @property
    def case_insensitive(self):
        return self.system in ("Windows", "Darwin")

    def appdata(self):
        v = self.environ.get("APPDATA")
        return Path(v) if v else self.home / "AppData" / "Roaming"

    def localappdata(self):
        v = self.environ.get("LOCALAPPDATA")
        return Path(v) if v else self.home / "AppData" / "Local"

    def xdg_config(self):
        v = self.environ.get("XDG_CONFIG_HOME")
        return Path(v) if v else self.home / ".config"

    def user_config_dir(self):
        """Per-user application config folder for desktop apps."""
        if self.is_windows:
            return self.appdata()
        if self.is_macos:
            return self.home / "Library" / "Application Support"
        return self.xdg_config()

    def path_dirs(self):
        """The absolute folders on PATH. Empty and relative entries (the current folder) are
        dropped: a program planted where the scanner is run from must never be found."""
        dirs = [d for d in self.environ.get("PATH", "").split(os.pathsep) if d and os.path.isabs(d)]
        # Folders on other machines are never searched, nor Windows folders without a drive.
        return [d for d in dirs if not _is_network_path(d)
                and (os.name != "nt" or os.path.splitdrive(d)[0])]

    def which(self, name):
        """A program's absolute path, or None. Looks only in the absolute PATH folders;
        shutil.which cannot be used, as on Windows it also searches the current folder."""
        if os.name == "nt":
            exts = [e for e in self.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
            if os.path.splitext(name)[1].upper() in {e.upper() for e in exts}:
                names = [name]
            else:
                names = [name + e for e in exts]
        else:
            names = [name]
        for folder in self.path_dirs():
            for n in names:
                p = os.path.join(folder, n)
                if os.path.isfile(p) and (os.name == "nt" or os.access(p, os.X_OK)):
                    return p
        return None

    def git_program(self):
        """git's absolute path (git.exe on Windows, never a script), or None. Looked up once."""
        if self._git is None:
            name = "git.exe" if os.name == "nt" else "git"
            self._git = next((p for p in (os.path.join(d, name) for d in self.path_dirs())
                              if os.path.isfile(p) and (os.name == "nt" or os.access(p, os.X_OK))), "")
        return self._git or None

    def program_files(self):
        v = self.environ.get("ProgramFiles") or self.environ.get("PROGRAMFILES")
        return Path(v) if v else self.root / "Program Files"


class Hasher:
    """Salted, one-way labels. The salt is random per run and never leaves this object."""

    def __init__(self, salt=None):
        self._salt = salt if salt is not None else secrets.token_bytes(16)

    def __call__(self, value):
        data = self._salt + str(value).encode("utf-8", "replace")
        return hashlib.sha256(data).hexdigest()[:12]


class Counters:
    """Things that went wrong or were capped during a run. Text is fixed; never a path."""

    def __init__(self):
        self.unreadable_folders = 0
        self.skipped_links = 0
        self.undecodable_projects = 0
        self.future_files = 0
        self.errors = []
        self.limits = []


def _isdir(p):
    return os.path.isdir(p)          # os.path swallows permission errors; pathlib does not


def _isfile(p):
    return os.path.isfile(p)


def _exists(p):
    return os.path.exists(p)


def _norm(p):
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(p)))


def _is_absolute(s):
    return s.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", s))


def _is_network_path(s):
    """True for a path that names another machine. Looking such a path up (even os.stat)
    makes Windows connect to that machine, and autofs on macOS and Linux do the same for
    /net and /Network. Windows reads '/' as '\\' and passes '\\??\\' paths straight through."""
    w = s.replace("/", "\\")
    if w.startswith("\\\\") or w.startswith("\\??\\"):
        return True
    if s.startswith("/"):
        # "/./net", "/usr/../net" and "/NET" reach the same place ("." and ".." parts depend on
        # links, and macOS ignores letter case), so any such spelling counts as a network path.
        n = posixpath.normpath(s)
        return n != s or n.split("/")[1].casefold() in ("net", "network")
    return False


# lstat facts that mean "this is a link", without following it: a symlink, or on Windows any
# reparse point that redirects to another name (junctions, symlinks, WSL links).
_S_IFLNK = 0o120000
_NAME_SURROGATE = 0x20000000
# Windows file attributes of a file whose data is not on this computer (a cloud placeholder):
# opening it would download it. OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS.
_NOT_LOCAL = 0x1000 | 0x40000 | 0x400000


_SF_DATALESS = 0x40000000     # macOS: the file's data is in iCloud, not on this computer


def _not_local(st):
    return bool(getattr(st, "st_file_attributes", 0) & _NOT_LOCAL or getattr(st, "st_flags", 0) & _SF_DATALESS)


def _git_folder_not_local(repo):
    """True if the repository's git folder holds files whose data is not on this computer
    (a repository in a cloud-synced folder). Git would read them and the sync app would
    download them, so such a repository is not inspected. Only names and lstat are used."""
    git_dir = os.path.join(str(repo), ".git")
    try:
        if _not_local(os.lstat(git_dir)):
            return True
        for folder in (git_dir, os.path.join(git_dir, "objects", "pack")):
            with os.scandir(folder) as it:
                if any(_not_local(e.stat(follow_symlinks=False)) for e in it):
                    return True
    except OSError:
        pass                    # a .git file (worktree) or an unreadable folder: git decides
    return False


def _lstat_is_link(st):
    return (st.st_mode & 0o170000) == _S_IFLNK or bool(getattr(st, "st_reparse_tag", 0) & _NAME_SURROGATE)



# ------------------------------------------------------------------ agents
#
# One row per agent, from each agent's public documentation (checked 2026-09-24).
#   "home"      folder relative to a base: "home" (the home folder) or "xdg_config" (~/.config)
#   "home_env"  an environment variable that relocates the agent's folder, and whether it
#               names the folder itself ("dir") or a replacement home folder ("home")
#   "sessions"  where session files live: (subfolder, depth, file-name pattern). Only file
#               names, sizes and dates are ever read there.
#   "history"   files in the agent's folder that hold prompt history; never opened
#   "credentials"  files in the agent's folder that hold sign-in tokens; never opened
#   "generic_binary"  the program name is too common to prove the agent is installed on its
#               own (e.g. "pi"), so it counts only together with the agent's folder
#   "source"    "docs" when the session layout is documented by the vendor, "third-party"
#               when only third-party sources describe it

AGENTS = [
    {
        "name": "claude-code",
        "binaries": ["claude"],
        "home": ("home", ".claude"),
        "home_env": ("CLAUDE_CONFIG_DIR", "dir"),
        # <home>/projects/<encoded working directory>/<session uuid>.jsonl. Set-aside copies
        # ("<id>.orphaned-<time>-<x>.jsonl") have dots in the name and are not counted.
        "sessions": [("projects", 2, r"^[^.]+\.jsonl$")],
        "history": ["history.jsonl"],
        "credentials": [".credentials.json"],
        "source": "docs",
    },
    {
        "name": "codex",
        "binaries": ["codex"],
        "home": ("home", ".codex"),
        "home_env": ("CODEX_HOME", "dir"),
        # <home>/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl, compressed to .jsonl.zst after
        # 7 days; archived sessions keep the same layout.
        "sessions": [("sessions", 4, r"^rollout-.+\.jsonl(\.zst)?$"),
                     ("archived_sessions", 4, r"^rollout-.+\.jsonl(\.zst)?$")],
        "history": ["history.jsonl", "session_index.jsonl"],
        "credentials": ["auth.json"],
        "source": "docs",
    },
    {
        "name": "gemini-cli",
        "binaries": ["gemini"],
        "home": ("home", ".gemini"),
        "home_env": ("GEMINI_CLI_HOME", "home"),      # replaces the home folder: <it>/.gemini
        # <home>/tmp/<project slug>/chats/session-<time>-<id>.jsonl (older builds: .json)
        "sessions": [("tmp", 3, r"^session-.+\.jsonl?$")],
        "credentials": ["oauth_creds.json"],
        "source": "docs",
    },
    {
        "name": "copilot-cli",
        "binaries": ["copilot"],
        "home": ("home", ".copilot"),
        "home_env": ("COPILOT_HOME", "dir"),
        # <home>/session-state/<session id>/events.jsonl
        "sessions": [("session-state", 2, r"^events\.jsonl$")],
        "source": "third-party",
    },
    {
        "name": "cursor",
        "binaries": ["cursor", "cursor-agent"],
        "home": ("home", ".cursor"),
        "home_env": None,
        # CLI transcripts: <home>/projects/<encoded cwd>/agent-transcripts/<uuid>/<uuid>.jsonl.
        # Editor chats live in a database and are not counted.
        "sessions": [("projects", 4, r"^[0-9a-fA-F-]{8,}\.jsonl$")],
        "source": "third-party",
    },
    {
        "name": "factory-droid",
        "binaries": ["droid"],
        "home": ("home", ".factory"),
        "home_env": None,
        # <home>/sessions/<encoded cwd>/<uuid>.jsonl (next to <uuid>.settings.json)
        "sessions": [("sessions", 2, r"^[^.]+\.jsonl$")],
        "source": "third-party",
    },
    {
        "name": "pi",
        "binaries": ["pi"],
        "generic_binary": True,
        "home": ("home", ".pi/agent"),
        "home_env": ("PI_CODING_AGENT_DIR", "dir"),
        # <home>/sessions/--<encoded cwd>--/<time>_<id>.jsonl
        "sessions": [("sessions", 2, r"^.+\.jsonl$")],
        "credentials": ["auth.json"],
        "source": "docs",
    },
    {
        "name": "opencode",
        "binaries": ["opencode"],
        "home": ("xdg_config", "opencode"),
        "home_env": None,
        "sessions": None,   # one SQLite database; not counted
        "source": "docs",
    },
]


def agent_spec(name):
    return next(a for a in AGENTS if a["name"] == name)


def agent_home(env, spec):
    """The agent's own folder, following its relocation variable when set."""
    reloc = spec.get("home_env")
    if reloc and env.environ.get(reloc[0]):
        target = Path(env.environ[reloc[0]])
        if reloc[1] == "home":
            return target / spec["home"][1]
        return target
    kind, rel = spec["home"]
    base = env.home if kind == "home" else env.xdg_config()
    return base / rel


# File names that usually hold sign-in tokens, wherever they are. Never opened.
CREDENTIAL_NAMES = {"auth.json", ".credentials.json", "oauth_creds.json", "credentials.json"}


def private_places(env):
    """Folders and files that hold sessions, history or sign-in tokens. Nothing under these
    is ever opened."""
    roots, files = [], []
    for spec in AGENTS:
        home = agent_home(env, spec)
        for subdir, _, _ in spec.get("sessions") or []:
            roots.append(_norm(home / subdir))
        for name in (spec.get("history") or []) + (spec.get("credentials") or []):
            files.append(_norm(home / name))
    return roots, files


# ------------------------------------------------------------------ file stats only


def _birth_time(st, env):
    """Creation time if the OS records one, else None. Never reads file contents."""
    t = getattr(st, "st_birthtime", None)
    if t is not None:
        return float(t)
    if env.is_windows and os.name == "nt":
        return float(st.st_ctime)      # on Windows, st_ctime is the creation time
    return None


def _scan_files(root, depth, pattern, counters):
    """Yield (path, os.stat_result) for files `depth` levels below root whose name matches.

    Uses os.scandir and DirEntry.stat only: file contents are never opened, and links
    are not followed.
    """
    rx = re.compile(pattern)

    def walk(folder, level):
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except PermissionError:
            counters.unreadable_folders += 1
            return
        except OSError:
            return
        for entry in entries:
            try:
                if level < depth - 1:
                    if entry.is_dir(follow_symlinks=False):
                        yield from walk(entry.path, level + 1)
                elif entry.is_file(follow_symlinks=False) and rx.match(entry.name):
                    yield Path(entry.path), entry.stat(follow_symlinks=False)
            except OSError:
                continue

    if _isdir(root):
        yield from walk(str(root), 0)


def _max_overlap(intervals):
    events = []
    for start, end in intervals:
        if start is None or end is None:
            continue
        events.append((start, 1))
        events.append((max(end, start), -1))
    events.sort(key=lambda e: (e[0], e[1]))
    best = cur = 0
    for _, delta in events:
        cur += delta
        best = max(best, cur)
    return best


def session_stats(env, spec, days, counters):
    """Counts for one agent's session files in the last `days` days, from file stats only."""
    layouts = spec.get("sessions")
    if not layouts:
        return None
    home = agent_home(env, spec)
    since = env.now - days * 86400
    count = 0
    total_bytes = 0
    active_days = set()
    folders = set()
    intervals = []
    births_known = True
    for subdir, depth, pattern in layouts:
        for path, st in _scan_files(home / subdir, depth, pattern, counters):
            if st.st_mtime < since:
                continue
            if st.st_mtime > env.now + 86400:       # a clock or copy error; its date means nothing
                counters.future_files += 1
                continue
            count += 1
            total_bytes += st.st_size
            active_days.add(_dt.date.fromtimestamp(st.st_mtime).isoformat())
            folders.add(str(path.parent))
            born = _birth_time(st, env)
            if born is None:
                births_known = False
            intervals.append((born, st.st_mtime))
    return {
        "sessions": count,
        "bytes": total_bytes,
        "active_days": len(active_days),
        "folders": len(folders),
        "max_concurrent": _max_overlap(intervals) if (births_known and count) else None,
        "intervals": intervals,   # internal; removed before output
    }


# ------------------------------------------------------------------ repository discovery


def _encode_claude_segment(name):
    """Encode a folder name the way Claude Code does: every character other than an ASCII
    letter or digit becomes "-", counted per UTF-16 code unit (as JavaScript does), so a
    character outside the Basic Multilingual Plane becomes "--"."""
    out = []
    for ch in name:
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append("--" if ord(ch) > 0xFFFF else "-")
    return "".join(out)


def decode_claude_project(env, encoded, max_steps=2000):
    """Turn a Claude Code project folder name back into a real folder, or None.

    The folder name is the working directory with every character other than letters
    and digits replaced by "-", so it cannot be decoded on its own. We walk down from
    the filesystem root, keeping only children whose encoded name matches the next part
    of the string. Only folder names are listed. Names longer than 200 characters are
    cut and given a hash by Claude Code; for those the deepest folder reached is used,
    but only if the rest of the name is the start of a folder name there (the cut fell
    inside it), so a deleted folder is never credited to a sibling with a shorter name.
    """
    steps = [0]
    deepest = [None, 0, ""]         # folder, depth, the part of the name still to match there
    fold = (lambda s: s.casefold()) if env.case_insensitive else (lambda s: s)

    if env.is_windows:
        m = re.match(r"^([A-Za-z])--(.*)$", encoded)
        if not m:
            return None
        drive = m.group(1).upper()
        start = Path(drive + ":\\") if env.real_machine else env.root / drive
        rest = m.group(2)
    else:
        if not encoded.startswith("-"):
            return None
        start = env.root
        rest = encoded[1:]

    def search(folder, remaining, depth):
        steps[0] += 1
        if steps[0] > max_steps:
            return None
        if depth > deepest[1]:
            deepest[0], deepest[1], deepest[2] = folder, depth, remaining
        if remaining == "":
            return folder
        children = _child_names(folder)
        if children is None:
            return None
        want = fold(remaining)
        for child in sorted(children, key=len, reverse=True):
            enc = fold(_encode_claude_segment(child))
            if want == enc:
                return folder / child
            if want.startswith(enc + "-"):
                found = search(folder / child, remaining[len(enc) + 1:], depth + 1)
                if found is not None:
                    return found
        return None

    found = search(start, rest, 0)
    if found is None and len(encoded) > 200 and deepest[1] >= 2:
        # Claude Code keeps the first 200 characters and adds "-<hash>".
        cut = fold(deepest[2].rpartition("-")[0])
        children = _child_names(deepest[0]) or []
        if cut == "" or any(fold(_encode_claude_segment(c)).startswith(cut) for c in children):
            return deepest[0]
    return found


def _child_names(folder):
    try:
        with os.scandir(folder) as it:
            return [e.name for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return None


def _has_git_entry(path):
    return _exists(os.path.join(str(path), ".git"))


def _enclosing_repo(path, home):
    """The folder holding .git at or above `path`, stopping below the home folder and the root.

    A home folder that is itself under git is never treated as a repository.
    """
    home_key = _norm(home)
    for cand in [path] + list(path.parents)[:20]:
        if _norm(cand) == home_key or cand == cand.parent:
            return None
        if _has_git_entry(cand):
            return cand
    return None


# Folders under the home folder where people usually keep code. Matched case-insensitively.
COMMON_CODE_FOLDERS = ["code", "src", "projects", "repos", "dev", "git", "work", "workspace",
                       "developer", "documents/github", "documents/projects", "source/repos"]


def _child_dirs(folder, counters, follow=False):
    """Sub-folders of `folder`. Links to folders are included only when `follow` is set."""
    try:
        with os.scandir(folder) as it:
            entries = list(it)
    except PermissionError:
        counters.unreadable_folders += 1
        return []
    except OSError:
        return []
    out = []
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=follow):
                out.append(e)
        except OSError:
            continue
    return out


def _common_code_roots(env, counters):
    """Existing folders matching COMMON_CODE_FOLDERS, whatever their letter case. A root that
    is a link (e.g. ~/code pointing at another disk) is followed."""
    roots = []
    for rel in COMMON_CODE_FOLDERS:
        level = [env.home]
        for part in rel.split("/"):
            nxt = []
            for folder in level:
                nxt.extend(Path(e.path) for e in _child_dirs(folder, counters, follow=True)
                           if e.name.casefold() == part)
            level = nxt
        roots.extend(level)
    return roots


SOURCE_ORDER = {"given": 0, "claude_projects": 1, "common_folders": 2}


def discover_repos(env, extra_paths=(), walk_depth=3, counters=None):
    """Find git working copies: paths given by the person, Claude Code project folders
    that decode to a real folder, and a bounded walk of common code folders.

    Returns (candidates sorted with given paths first, counts by source before any cap)."""
    counters = counters or Counters()
    found = {}          # normalised real path -> {"path", "sources"}
    home_key = _norm(env.home)

    def add(path, source):
        key = _norm(path)
        found.setdefault(key, {"path": Path(os.path.realpath(str(path))), "sources": set()})["sources"].add(source)

    visited = [0]

    def walk(folder, level, source):
        """Repositories at or below `folder`, bounded in depth and in folders visited. Links
        directly inside the starting folder are followed; deeper links are not."""
        if visited[0] >= MAX_WALK_DIRS:
            return 0
        visited[0] += 1
        if _has_git_entry(folder) and _norm(folder) != home_key:
            add(folder, source)
            return 1
        if level >= walk_depth:
            return 0
        hits = 0
        for e in _child_dirs(folder, counters, follow=(level == 1)):
            if not e.name.startswith(".") and e.name not in ("node_modules", "venv", "__pycache__"):
                hits += walk(Path(e.path), level + 1, source)
        return hits

    for p in extra_paths:
        s = str(p)
        if s == "~" or s.startswith(("~/", "~\\")):
            s = str(env.home) + s[1:]         # the scanned home, not whoever runs the scanner
        p = Path(os.path.abspath(s))
        if not _isdir(p):
            counters.errors.append("a folder you named does not exist")
            continue
        repo = _enclosing_repo(p, env.home)
        if repo is not None:
            add(repo, "given")
        elif not walk(p, 1, "given"):         # a folder that holds repositories, e.g. C:\src
            counters.errors.append("a folder you named has no git repository in or under it")

    projects = agent_home(env, agent_spec("claude-code")) / "projects"
    for entry in _child_dirs(projects, counters):
        real = decode_claude_project(env, entry.name)
        if real is None:
            counters.undecodable_projects += 1
            continue
        repo = _enclosing_repo(real, env.home)
        if repo is not None:          # sessions started in a subfolder map to their repository
            add(repo, "claude_projects")

    for base in _common_code_roots(env, counters):
        walk(base, 1, "common_folders")
    if visited[0] >= MAX_WALK_DIRS:
        counters.limits.append(f"repository search stopped after visiting {MAX_WALK_DIRS} folders")

    items = sorted(found.values(),
                   key=lambda r: (min(SOURCE_ORDER[s] for s in r["sources"]), str(r["path"])))
    by_source = {s: sum(1 for r in items if s in r["sources"]) for s in SOURCE_ORDER}
    return items, by_source


# ------------------------------------------------------------------ git (read-only)

_REF = re.compile(r"^refs/(?!.*\.\.)[^\s~^:?*\[\\\x00-\x1f\x7f]+$")
# The only output formats git is ever asked for. A format is a program of its own (%G? or
# %(signature) would run gpg), so only these exact strings are accepted.
# The window log gives each author twice: as recorded, and as the repository's .mailmap maps
# them (%aN/%aE, used only when .mailmap is safe to let git read; see _mailmap_safe).
_FMT_WINDOW = "--format=%x01%an <%ae>%x00%an%x00%ae%x00%(trailers:key=Co-authored-by,valueonly,separator=%x02)"
_FMT_WINDOW_MAILMAP = "--format=%x01%an <%ae>%x00%aN%x00%aE%x00%(trailers:key=Co-authored-by,valueonly,separator=%x02)"
_FMT_STREAM = "--format=%x01%m%H%x00%P%x00%an%x00%ae%x00%ct%x00%at"
_FMT_FIRST_PARENT = "--format=%x01%H"
_FMT_REFS = "--format=%(refname)%00%(committerdate:unix)"
_LOG_FLAGS = {"--all", "--exclude=refs/stash", "--no-merges", "--no-renames", "--raw", "--no-abbrev", "--cherry-pick",
              "--left-right", "--first-parent", "-m", "--no-show-signature", "--no-ext-diff",
              "--no-textconv", _FMT_WINDOW, _FMT_WINDOW_MAILMAP, _FMT_STREAM, _FMT_FIRST_PARENT}
_SINCE = re.compile(r"^--since=\d+\.days\.ago$")
_CONFIG_KEYS = {"core.hooksPath"}


def _is_ref(arg):
    return bool(_REF.match(arg))


def _is_rev(arg):
    """A ref, or a range of two refs (A..B or A...B)."""
    for sep in ("...", ".."):
        if sep in arg:
            left, _, right = arg.partition(sep)
            return _is_ref(left) and _is_ref(right)
    return _is_ref(arg)


def _git_args_allowed(args):
    """True only for the read-only command shapes this scanner uses. Anything else is refused."""
    if not args:
        return False
    cmd, rest = args[0], list(args[1:])
    if cmd == "rev-parse":
        if rest in (["--is-inside-work-tree"], ["--show-toplevel"], ["--git-common-dir"]):
            return True
        return (len(rest) == 3 and rest[:2] == ["--verify", "--quiet"]
                and _is_ref(rest[2][:-len("^{commit}")] if rest[2].endswith("^{commit}") else rest[2]))
    if cmd == "worktree":
        return rest == ["list", "--porcelain"]
    if cmd == "remote":
        return rest == ["-v"]
    if cmd == "config":
        return len(rest) == 2 and rest[0] == "--get" and rest[1] in _CONFIG_KEYS
    if cmd == "symbolic-ref":
        return rest == ["--quiet", "refs/remotes/origin/HEAD"]
    if cmd == "for-each-ref":
        return (len(rest) == 5 and rest[0] == _FMT_REFS and rest[1] == "--sort=-committerdate"
                and rest[2].startswith("--no-merged=") and _is_ref(rest[2][len("--no-merged="):])
                and rest[3:] == ["refs/heads", "refs/remotes"])
    if cmd == "merge-base":
        return len(rest) == 2 and all(_is_ref(r) for r in rest)
    if cmd == "log":
        if "--end-of-options" in rest:
            i = rest.index("--end-of-options")
            flags, revs = rest[:i], rest[i + 1:]
            if not revs or not all(_is_rev(r) for r in revs):
                return False
        else:
            flags, revs = rest, []
            if "--all" not in flags:
                return False
        return all(f in _LOG_FLAGS or _SINCE.match(f) for f in flags)
    return False


def _git(repo, args, env):
    if not _git_args_allowed(args):
        raise ValueError(f"git command not allowed: {args[:3]}")
    program = env.git_program()
    if program is None:
        raise OSError("git was not found in the absolute PATH folders")
    # Start from the scanner's environment minus every GIT_* variable (GIT_DIR, GIT_WORK_TREE,
    # GIT_CONFIG_* ...), then switch off everything that could reach the network, run a
    # program chosen by config, or write a file.
    genv = {k: v for k, v in env.environ.items() if not k.upper().startswith("GIT_")}
    genv.update({
        "GIT_OPTIONAL_LOCKS": "0",           # never refresh the index as a side effect
        "GIT_TERMINAL_PROMPT": "0",          # never block on credentials
        "GIT_NO_LAZY_FETCH": "1",            # partial clones: never fetch missing objects (git >= 2.44)
        # Every transport refused. Unlike -c protocol.allow=never, this also overrides any
        # protocol.<name>.allow in the repository's config, so it holds on older git too.
        "GIT_ALLOW_PROTOCOL": "none",
        "GIT_TRACE2": "0", "GIT_TRACE2_EVENT": "0", "GIT_TRACE2_PERF": "0",   # beat trace2.* config
        "GIT_CEILING_DIRECTORIES": str(Path(repo).parent),   # never fall through to a parent repo
        "GCM_INTERACTIVE": "never",
        "LC_ALL": "C",
        "NoDefaultCurrentDirectoryInExePath": "1",   # Windows: programs git starts, never from "."
    })
    if not env.real_machine:
        genv["GIT_CONFIG_NOSYSTEM"] = "1"    # a synthetic scan never depends on this machine's git setup
    cmd = [program,
           "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
           "-c", "log.showSignature=false",       # no gpg.program / ssh-keygen runs
           "-c", "log.diffMerges=first-parent",   # -m shows a merge against its first parent only
           "-c", "log.mailmap=false",             # .mailmap is read only for %aN/%aE (_mailmap_safe)
           "-c", "protocol.allow=never",          # no transport (see GIT_ALLOW_PROTOCOL)
           "-C", str(repo)] + list(args)
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=genv, timeout=GIT_TIMEOUT)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


# Author name or email fragments that identify commits made by cloud agents, and
# trailer values that local agents add. Matching is case-insensitive substring.
BOT_AUTHORS = {        # commit author strings used by cloud agents
    "copilot": ["copilot-swe-agent", "198982749+copilot@users.noreply.github.com"],
    "claude": ["claude[bot]", "agent@example.invalid"],     # the GitHub Action, and Claude Code on the web
    "codex": ["chatgpt-codex-connector"],
    "cursor": ["cursoragent@cursor.com"],
    "devin": ["devin-ai-integration"],
    "jules": ["google-labs-jules"],
    "factory-droid": ["factory-droid[bot]"],
}
AGENT_TRAILERS = {     # Co-authored-by values that local agents add
    "claude": ["agent@example.invalid"],
    "codex": ["noreply@openai.com"],
    "copilot": ["copilot@users.noreply.github.com", "copilot@github.com"],
    "cursor": ["cursoragent@cursor.com"],
    "factory-droid": ["factory-droid[bot]"],
    "devin": ["devin-ai-integration"],
    "aider": ["aider@aider.chat"],
    "opencode": ["noreply@opencode.ai"],
}
# Automation that is not a coding agent (dependency and CI bots). Its commits are counted,
# but never treated as a person whose work can collide with someone else's.
OTHER_BOTS = ["[bot]", "dependabot", "renovate", "github-actions", "snyk-bot", "greenkeeper",
              "pre-commit-ci", "mergify", "imgbot", "semantic-release-bot"]


def _match_table(text, table):
    low = text.lower()
    return sorted({key for key, needles in table.items() if any(n in low for n in needles)})


def _is_other_bot(who):
    """True for an author ("name <email>") that is automation but not a coding agent."""
    low = who.lower()
    return any(n in low for n in OTHER_BOTS) and not _match_table(who, BOT_AUTHORS)


def _default_ref(repo, env):
    """The default branch as a verified ref name, or None."""
    code, out = _git(repo, ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], env)
    candidates = []
    target = out.strip()
    if code == 0 and target.startswith("refs/remotes/") and _is_ref(target):
        candidates.append(target)
    candidates += ["refs/remotes/origin/main", "refs/remotes/origin/master",
                   "refs/heads/main", "refs/heads/master"]
    for cand in candidates:
        code, _ = _git(repo, ["rev-parse", "--verify", "--quiet", cand + "^{commit}"], env)
        if code == 0:
            return cand
    return None


_RAW = re.compile(r"^:\d+ \d+ ([0-9a-f]+) ([0-9a-f]+) [A-Z]\d*\t(.+)$")


class _Commit:
    """One commit from `git log --raw`: which side it is on, who made it, and for each file
    changed the blob before and after (blob ids only; file contents are never read here)."""
    __slots__ = ("sha", "right", "merge", "author", "name", "who", "person", "time", "atime", "files")

    def __init__(self, sha, right, merge, author, name, ctime, atime, files):
        self.sha, self.right, self.merge = sha, right, merge
        self.author, self.name, self.who = author, name, f"{name} <{author}>"
        self.person = author            # replaced by the grouped identity (_People) once known
        self.time, self.atime, self.files = ctime, atime, files


def _raw_files(body):
    files = {}
    for line in body.splitlines():
        m = _RAW.match(line)
        if m:
            files[m.group(3)] = (m.group(1), m.group(2))
    return files


def _log_stream(repo, default, ref, since, env, cherry):
    """Commits in the window on either side of default...ref, or None if git failed.

    One command covers both sides (--left-right). With `cherry`, commits whose change is on
    the other side too (a rebase or cherry-pick) are left out; git works that out by
    comparing the changes, which needs the file contents (it keeps none of them)."""
    flags = ["--left-right"] + (["--cherry-pick"] if cherry else []) + [
        "--no-renames", "--raw", "--no-abbrev", "--no-show-signature", f"--since={since}", _FMT_STREAM]
    code, out = _git(repo, ["log"] + flags + ["--end-of-options", f"{default}...{ref}"], env)
    if code != 0:
        return None
    commits = []
    for chunk in out.split("\x01")[1:]:
        head, _, body = chunk.partition("\n")
        parts = head.split("\x00")
        if len(parts) != 6 or parts[0][:1] not in ("<", ">"):
            continue
        mark_sha, parents, name, email, ct, at = parts
        try:
            ctime, atime = float(ct), float(at)
        except ValueError:
            ctime = atime = 0.0
        email = email.strip().lower()
        commits.append(_Commit(mark_sha[1:], mark_sha[0] == ">", len(parents.split()) > 1,
                               email, name, ctime, atime, _raw_files(body)))
    return commits


def _first_parent_tips(repo, default, ref, since, env):
    """{file: blob} as the branch's first-parent history leaves it, merges included (a merge
    of the default branch into the branch, as GitHub's "Update branch" makes, can change a
    file). None if git failed."""
    code, out = _git(repo, ["log", "-m", "--first-parent", "--no-renames", "--raw", "--no-abbrev",
                            "--no-show-signature", f"--since={since}", _FMT_FIRST_PARENT,
                            "--end-of-options", f"{default}..{ref}"], env)
    if code != 0:
        return None
    tips = {}
    for chunk in out.split("\x01")[1:]:          # newest first
        for f, (_, after) in _raw_files(chunk.partition("\n")[2]).items():
            tips.setdefault(f, after)
    return tips


def _net_change(own, tips=None):
    """{file: final blob} for a branch's own commits (newest first), leaving out files whose
    changes cancel out (a scratch file added then deleted, an edit later reverted)."""
    new, old = {}, {}
    for c in own:
        for f, (before, after) in c.files.items():
            new.setdefault(f, after)
            old[f] = before                # ends as the oldest commit's "before"
    for f in new:
        if tips and f in tips:
            new[f] = tips[f]
    return {f: blob for f, blob in new.items() if blob != old[f]}


def _squash_merged(net, theirs):
    """True if one commit on the default branch carries the branch's whole net change,
    which is what a squash merge looks like."""
    return bool(net) and any(all(c.files.get(f, (None, None))[1] == blob for f, blob in net.items())
                             for c in theirs)


_NOREPLY = re.compile(r"^(?:\d+\+)?([^@+]+)@users\.noreply\.github\.com$")


def _name_node(name):
    """A name compared without case, spaces or punctuation ("Alice Smith" = "alice.smith")."""
    key = re.sub(r"[\W_]+", "", name.casefold())
    return "n:" + key if key else None


class _People:
    """Groups the commit identities of one repository into people.

    Two identities are one person when they share an email address or a name (ignoring case,
    spaces and punctuation). A GitHub noreply address (<id>+<login>@users.noreply.github.com,
    used for squash merges made on the web with email privacy on) also counts its login as a
    name, because an account with no display name commits under its login. Two different
    people who share a name are therefore counted as one. Nothing here is ever output."""

    def __init__(self):
        self._parent = {}

    def _find(self, node):
        self._parent.setdefault(node, node)
        while self._parent[node] != node:
            self._parent[node] = self._parent[self._parent[node]]
            node = self._parent[node]
        return node

    @staticmethod
    def _nodes(name, email):
        email = email.strip().casefold()
        nodes = ["e:" + email] if email else []
        for n in (_name_node(name.strip()),):
            if n:
                nodes.append(n)
        m = _NOREPLY.match(email)
        if m and "[bot]" not in email:
            login = _name_node(m.group(1))
            if login:
                nodes.append(login)
        return nodes

    def add(self, *identities):
        """Record that these (name, email) identities belong to one person."""
        nodes = [n for name, email in identities for n in self._nodes(name, email)]
        for n in nodes[1:]:
            a, b = self._find(nodes[0]), self._find(n)
            if a != b:
                self._parent[b] = a

    def key(self, name, email):
        nodes = self._nodes(name, email)
        return self._find(nodes[0]) if nodes else "?"

    def count(self, identities):
        return len({self.key(name, email) for name, email in identities})


def _mailmap_safe(repo):
    """True if git may read the repository's .mailmap (for %aN/%aE): it is absent, or a plain
    file stored on this computer. Git since 2.32 refuses a linked .mailmap by itself; older git
    would follow the link, possibly to another machine, so a link is never offered to it."""
    try:
        st = os.lstat(os.path.join(str(repo), ".mailmap"))
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return not _lstat_is_link(st) and (st.st_mode & 0o170000) == 0o100000 and not _not_local(st)


def _rebased_copies(own, theirs):
    """Own commits the default branch already holds as a rebased copy that git could not match
    by patch (lines nearby had changed): same author, same author date, same files. Rebasing
    keeps the author and the author date."""
    landed = {(c.author, c.atime, frozenset(c.files)) for c in theirs}
    return [c for c in own if (c.author, c.atime, frozenset(c.files)) in landed]


def _squash_after_base_moved(net, own, theirs):
    """A squash merge made after the default branch had changed some of the same files, so
    no blob matches exactly: a default-side commit newer than the branch's work, by one of the
    branch's authors (squash merges keep the pull request's author), that changes every file
    the branch changed, where each file it leaves different was also changed on the default
    side before it. Reported apart from exact squash merges, as probably merged."""
    newest = max(c.time for c in own)
    authors = {c.person for c in own}
    for s in theirs:
        if s.time <= newest or s.person not in authors or not set(net) <= set(s.files):
            continue
        moved = {f for c in theirs if c is not s and c.time <= s.time for f in c.files}
        if all(s.files[f][1] == blob or f in moved for f, blob in net.items()):
            return True
    return False


def _authors_by_file(commits, key):
    out = {}
    for c in commits:
        for f in c.files:
            out.setdefault(f, set()).add(getattr(c, key))
    return out


def _shared_by_different_people(side_a, side_b, key="person"):
    """Files changed on both sides where someone on one side differs from someone on the other.

    Only commits unique to each side are compared, so shared history never counts. Commits
    by automation that is not a coding agent (dependency bots) are left out.
    """
    sha_a = {c.sha for c in side_a}
    sha_b = {c.sha for c in side_b}
    by_a = _authors_by_file([c for c in side_a if c.sha not in sha_b and not _is_other_bot(c.who)], key)
    by_b = _authors_by_file([c for c in side_b if c.sha not in sha_a and not _is_other_bot(c.who)], key)
    return {f for f in set(by_a) & set(by_b)
            if any(x != y for x in by_a[f] for y in by_b[f])}


def _host_category(url):
    low = url.lower()
    for key in ("github", "gitlab", "bitbucket"):
        if key in low:
            return key
    if "dev.azure.com" in low or "visualstudio.com" in low:
        return "azure"
    return "other"


def repo_key(repo, env):
    """The repository's shared git folder, so linked worktrees of one repository count once."""
    code, out = _git(repo, ["rev-parse", "--git-common-dir"], env)
    if code != 0 or not out.strip():
        return None
    common = Path(out.strip())
    if not common.is_absolute():
        common = Path(repo) / common
    return _norm(common)


def _checkouts(porcelain):
    """Paths of the real checkouts in `git worktree list --porcelain` output: the bare entry
    of a bare repository and "prunable" entries (folder deleted by hand) are left out."""
    paths = []
    for block in re.split(r"\n\s*\n", porcelain.strip()):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree "):
            continue
        if any(line == "bare" or line.startswith("prunable") for line in lines[1:]):
            continue
        paths.append(lines[0][len("worktree "):])
    return paths


def inspect_repo(repo, env, days, hasher, deadline=None, _via_container=False):
    """Counts for one repository. Returns (item, errors); item is None if it was skipped.
    No new branch is looked at once time.monotonic() passes `deadline`."""
    errors = []
    since = f"{days}.days.ago"

    code, out = _git(repo, ["rev-parse", "--is-inside-work-tree"], env)
    if code != 0 or out.strip() != "true":
        # A bare repository kept next to its worktrees (proj/.bare, proj/.git -> .bare,
        # proj/main, proj/feature): inspect it through its first checkout.
        if code == 0 and not _via_container:
            code, out = _git(repo, ["worktree", "list", "--porcelain"], env)
            for path in _checkouts(out) if code == 0 else []:
                if not _is_network_path(path) and _isdir(path):
                    return inspect_repo(Path(path), env, days, hasher, deadline, _via_container=True)
        return None, ["a folder was not a readable git working copy"]
    code, out = _git(repo, ["rev-parse", "--show-toplevel"], env)
    if code != 0 or _norm(out.strip()) != _norm(repo):
        return None, ["a folder was inside a repository but not at its top"]
    item = {"id": hasher(_norm(repo)), "_path": Path(repo)}

    code, out = _git(repo, ["worktree", "list", "--porcelain"], env)
    item["worktrees"] = len(_checkouts(out)) if code == 0 else None

    code, out = _git(repo, ["remote", "-v"], env)
    item["remote_hosts"] = sorted({_host_category(line.split()[1]) for line in out.splitlines()
                                   if len(line.split()) >= 2}) if code == 0 else []

    # Hook setup: managers present, a custom hooks path, and active hooks in the git dir.
    # lexists: a marker that is a link is noted, but the link is never followed.
    managers = []
    for marker, label in ((".husky", "husky"), ("lefthook.yml", "lefthook"), (".lefthook.yml", "lefthook"),
                          ("lefthook.yaml", "lefthook"), (".pre-commit-config.yaml", "pre-commit")):
        if os.path.lexists(os.path.join(str(repo), marker)) and label not in managers:
            managers.append(label)
    item["hook_managers"] = managers
    code, out = _git(repo, ["config", "--get", "core.hooksPath"], env)
    item["hooks_path_set"] = code == 0 and bool(out.strip())
    code, out = _git(repo, ["rev-parse", "--git-common-dir"], env)
    active = None
    partial = shallow = False
    if code == 0 and out.strip():
        common = Path(out.strip())
        if not common.is_absolute():
            common = Path(repo) / common
        # Hooks are counted by name; a hooks folder or hook that is a link is never followed.
        try:
            st = os.lstat(str(common / "hooks"))
            if not _lstat_is_link(st) and (st.st_mode & 0o170000) == 0o040000:
                with os.scandir(common / "hooks") as it:
                    active = sum(1 for e in it if not e.name.endswith(".sample")
                                 and (e.is_symlink() or e.is_file(follow_symlinks=False)))
        except OSError:
            active = None
        # A shallow clone keeps a "shallow" file in its git folder (checked by name only).
        shallow = os.path.lexists(os.path.join(str(common), "shallow"))
        # A partial clone keeps "*.promisor" files next to its packs (names only are read).
        try:
            partial = any(e.name.endswith(".promisor") for e in os.scandir(common / "objects" / "pack"))
        except OSError:
            partial = False
    item["active_git_hooks"] = active
    item["partial_clone"] = partial

    # Commits in the window on every ref: who made them (agents and other bots) and which
    # agents are credited. Per-agent tallies can count one commit twice (author and trailer);
    # the cloud/local counts never do.
    mailmap = _mailmap_safe(repo)
    code, out = _git(repo, ["log", "--exclude=refs/stash", "--all", "--no-merges", "--no-show-signature",
                            f"--since={since}", _FMT_WINDOW_MAILMAP if mailmap else _FMT_WINDOW], env)
    commits = cloud = local = other_bots = 0
    bots, trailers, authors, identities = {}, {}, set(), set()
    people = _People()
    if code == 0:
        for chunk in out.split("\x01")[1:]:
            commits += 1
            author, _, rest = chunk.partition("\x00")
            mapped_name, _, rest = rest.partition("\x00")
            mapped_email, _, tr = rest.partition("\x00")
            name, _, email = author.rpartition(" <")
            email = email[:-1] if email.endswith(">") else email
            authors.add(email.strip().lower())
            identities.add((name, email.strip().lower()))
            people.add((name, email), (mapped_name, mapped_email))
            by_agent = _match_table(author, BOT_AUTHORS)
            credited = _match_table(tr, AGENT_TRAILERS)
            for key in by_agent:
                bots[key] = bots.get(key, 0) + 1
            for key in credited:
                trailers[key] = trailers.get(key, 0) + 1
            if by_agent or (credited and _is_other_bot(author)):
                cloud += 1                       # an agent in the cloud, or run by a CI bot
            elif credited:
                local += 1
            elif _is_other_bot(author):
                other_bots += 1
    else:
        errors.append("could not read the commit log")
    item["commits_in_window"] = commits
    item["authors_in_window"] = len(authors)
    item["people_in_window"] = people.count(identities)
    item["mailmap_read"] = mailmap
    item["bot_authored_commits"] = bots
    item["agent_trailer_commits"] = trailers
    item["cloud_agent_commits"] = cloud
    item["local_agent_commits"] = local
    item["other_bot_commits"] = other_bots

    # Work in flight: unmerged branches with commits in the window that the default branch
    # does not already have (by commit, by patch, or as one squashed commit).
    default = _default_ref(repo, env)
    item["default_branch_found"] = default is not None
    candidates = []
    skipped = {"unrelated_history": 0, "squash_merged": 0, "probably_merged": 0, "no_net_change": 0,
               "bot_only": 0, "unreadable": 0}
    by_commit_only = 0
    capped = timed_out = False
    if default is not None:
        # Branches already merged by commit are left out by git; the rest come newest first,
        # so the cap never spends itself on old merged branches.
        code, out = _git(repo, ["for-each-ref", _FMT_REFS, "--sort=-committerdate",
                                f"--no-merged={default}", "refs/heads", "refs/remotes"], env)
        refs = []
        if code == 0:
            cutoff = env.now - days * 86400
            for line in out.splitlines():
                name, _, ts = line.partition("\x00")
                if name == default or name.endswith("/HEAD") or not _is_ref(name):
                    continue
                try:
                    if float(ts) >= cutoff:
                        refs.append(name)
                except ValueError:
                    continue
        else:
            errors.append("could not list the branches")
        if len(refs) > MAX_REFS:
            capped = True
            refs = refs[:MAX_REFS]
        for ref in refs:
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True                            # the scan's time budget ran out
                break
            try:
                # In a partial clone the file contents needed to compare patches are missing
                # (and are never downloaded), so commits are compared by id only. The same
                # happens when the comparison fails or takes too long.
                commits_seen = None
                if not partial:
                    try:
                        commits_seen = _log_stream(repo, default, ref, since, env, cherry=True)
                    except subprocess.TimeoutExpired:
                        commits_seen = None
                commit_only = commits_seen is None
                if commits_seen is None:
                    commits_seen = _log_stream(repo, default, ref, since, env, cherry=False)
                if commits_seen is None:
                    skipped["unreadable"] += 1
                    continue
                for c in commits_seen:
                    c.person = people.key(c.name, c.author)
                on_branch = [c for c in commits_seen if c.right]
                own = [c for c in on_branch if not c.merge]
                theirs = [c for c in commits_seen if not c.right and not c.merge]
                if not own:
                    continue
                code, _ = _git(repo, ["merge-base", default, ref], env)
                if code != 0:
                    # e.g. an orphan gh-pages branch; in a shallow clone, a fork below the cut
                    skipped["unreadable" if shallow else "unrelated_history"] += 1
                    continue
                if all(_is_other_bot(c.who) for c in own):
                    skipped["bot_only"] += 1                # e.g. an open Dependabot branch
                    continue
                landed = _rebased_copies(own, theirs)
                if landed and len(landed) == len(own):
                    skipped["probably_merged"] += 1         # rebase-merged after lines nearby changed
                    continue
                own = [c for c in own if c not in landed]
                tips = None
                if any(c.merge for c in on_branch):
                    tips = _first_parent_tips(repo, default, ref, since, env)
                net = _net_change(own, tips)
                if not net:
                    skipped["no_net_change"] += 1
                    continue
                if _squash_merged(net, theirs):
                    skipped["squash_merged"] += 1
                    continue
                if _squash_after_base_moved(net, own, theirs):
                    skipped["probably_merged"] += 1
                    continue
                by_commit_only += commit_only
                candidates.append((ref, frozenset(c.sha for c in own), own, theirs))
            except (subprocess.SubprocessError, OSError, ValueError):
                skipped["unreadable"] += 1                  # e.g. a timeout on one old branch
    # One stream of work counts once: a local branch and its pushed copy (even after one of
    # them was rebased: the newer copy is kept), a local branch a commit or two ahead of it,
    # and stacked branches (each holding the one below).
    newest_by_name = {}
    for cand in candidates:
        name = re.sub(r"^refs/(heads|remotes/[^/]+)/", "", cand[0])
        best = newest_by_name.get(name)
        if best is None or max(c.time for c in cand[2]) > max(c.time for c in best[2]):
            newest_by_name[name] = cand
    candidates = [c for c in candidates if newest_by_name.get(
        re.sub(r"^refs/(heads|remotes/[^/]+)/", "", c[0])) is c]
    streams = []
    for cand in sorted(candidates, key=lambda c: -len(c[1])):
        if not any(cand[1] <= kept[1] for kept in streams):
            streams.append(cand)
    item["in_flight_branches"] = len(streams)
    item["branches_skipped"] = skipped
    item["branches_checked_by_commit_only"] = by_commit_only
    item["shallow_clone"] = shallow
    item["branch_cap_hit"] = capped
    item["branch_time_limit_hit"] = timed_out

    # Two counts: by person (identities grouped, the measure to use) and strictly by email,
    # so the data shows how much the grouping changes.
    counts = {"person": [0, set()], "author": [0, set()]}

    def note(side_a, side_b):
        for key, tally in counts.items():
            common = _shared_by_different_people(side_a, side_b, key)
            if common:
                tally[0] += 1
                tally[1] |= common

    for i, (_, _, own_a, theirs_a) in enumerate(streams):
        note(own_a, theirs_a)                    # the default branch moved under it
        for _, _, own_b, _ in streams[i + 1:]:
            note(own_a, own_b)
    # Branches merged together into one integration branch (a staging or test-merge branch)
    # are hidden inside it, but their authors' work can still collide with each other.
    inner = set()
    for _, shas_m, _, _ in streams:
        members = [c for c in candidates if c[1] < shas_m]
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                key = frozenset((a[0], b[0]))
                if a[1] <= b[1] or b[1] <= a[1] or key in inner:
                    continue
                inner.add(key)
                note(a[2], b[2])
    item["cross_author_overlap_pairs"] = counts["person"][0]
    item["cross_author_overlap_files"] = len(counts["person"][1])
    item["cross_email_overlap_pairs"] = counts["author"][0]
    item["cross_email_overlap_files"] = len(counts["author"][1])
    return item, errors


# ------------------------------------------------------------------ MCP helpers (config files only)

# Commands that start helpers, kept as labels. Anything else becomes "other" (or "other-path"
# for an absolute path), because a helper's own program name can identify a person or company.
LAUNCHERS = {"npx", "npm", "pnpm", "pnpx", "yarn", "bun", "bunx", "node", "deno", "uvx", "uv", "pipx",
             "python", "py", "docker", "podman", "java", "dotnet", "go", "cargo", "ruby", "php",
             "cmd", "powershell", "pwsh", "sh", "bash"}


def _strip_jsonc(text):
    """Remove // and /* */ comments and trailing commas outside strings (JSON with comments)."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(ch)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _read_text(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def _read_json(path):
    text = _read_text(path)
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_strip_jsonc(text))


def _servers_from(obj, key):
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, dict) else {}


def _is_disabled(server):
    """A helper switched off in its config: "disabled": true, or "enabled": false."""
    return isinstance(server, dict) and (server.get("disabled") is True or server.get("enabled") is False)


def _command_parts(server):
    """(label, command) for how a helper starts. The label is from LAUNCHERS or generic."""
    if not isinstance(server, dict):
        return "unknown", None
    if server.get("url") or server.get("httpUrl") or server.get("serverUrl") or \
            str(server.get("type", "")).lower() in ("http", "sse", "streamable-http", "remote"):
        return "remote", None
    cmd = server.get("command")
    if isinstance(cmd, list):
        cmd = cmd[0] if cmd else None
    if not isinstance(cmd, str) or not cmd.strip() or "\x00" in cmd:
        return "unknown", None
    cmd = cmd.strip()
    if _is_network_path(cmd):
        first = cmd                       # another machine: never looked up (that would contact it)
    elif _is_absolute(cmd):
        first = cmd if _exists(cmd) else cmd.split()[0]      # a path with spaces, or a path and arguments
    else:
        first = cmd.split()[0]
    name = re.split(r"[\\/]", first)[-1].lower()
    name = re.sub(r"\.(exe|cmd|bat|ps1|sh)$", "", name)
    name = "python" if re.match(r"^python\d*(\.\d+)?$", name) else name
    if name in LAUNCHERS:
        return name, first
    return ("other-path" if _is_absolute(first) else "other"), first


def _command_check(first, env):
    """"unchecked", "missing" or "found" for a helper's command, looked up locally only.
    Network paths are never looked up, and nor are relative paths with a folder part, which
    mean nothing without the folder the app starts the helper in."""
    if _is_network_path(first) or (not _is_absolute(first) and re.search(r"[\\/]", first)):
        return "unchecked"
    if _is_absolute(first):
        return "found" if _exists(first) else "missing"
    return "found" if env.which(first) else "missing"


def _codex_servers(path):
    """Server names and commands from a Codex config.toml, without a TOML library.

    Understands [mcp_servers.<name>] tables, a [mcp_servers] table of inline tables, and
    dotted keys. Skips multi-line strings. Only `command`, `url` and `enabled` are read.
    """
    bool_rx = r"enabled\s*=\s*(true|false)\b"
    servers = {}
    current = None        # name of the server table we are in
    in_parent = False     # inside [mcp_servers]
    in_multiline = None
    name_rx = r"(\"[^\"]+\"|'[^']+'|[A-Za-z0-9_-]+)"
    for raw in _read_text(path).splitlines():
        line = raw.strip()
        if in_multiline:
            if in_multiline in line:
                in_multiline = None
            continue
        for q in ('"""', "'''"):
            if line.count(q) % 2 == 1:
                in_multiline = q
        if in_multiline:
            continue
        m = re.match(r"^\[\s*mcp_servers\s*\.\s*" + name_rx + r"\s*\]\s*(#.*)?$", line)
        if m:
            current, in_parent = m.group(1).strip("\"'"), False
            servers.setdefault(current, {})
            continue
        if re.match(r"^\[\s*mcp_servers\s*\]\s*(#.*)?$", line):
            current, in_parent = None, True
            continue
        if line.startswith("["):
            current, in_parent = None, False
            continue
        value_rx = r"=\s*(\"([^\"]*)\"|'([^']*)'|\[\s*\"([^\"]*)\")"
        if current is not None:
            m = re.match(r"^(command|url)\s*" + value_rx, line)
            if m:
                servers[current][m.group(1)] = m.group(3) or m.group(4) or m.group(5) or ""
            m = re.match(r"^" + bool_rx, line)
            if m:
                servers[current]["enabled"] = m.group(1) == "true"
            continue
        prefix = "" if in_parent else r"mcp_servers\s*\.\s*"
        m = re.match(r"^" + prefix + name_rx + r"\s*=\s*\{(.*)\}\s*(#.*)?$", line)
        if m and (in_parent or line.startswith("mcp_servers")):
            entry = servers.setdefault(m.group(1).strip("\"'"), {})
            for key in ("command", "url"):
                km = re.search(key + r"\s*" + value_rx, m.group(2))
                if km:
                    entry[key] = km.group(2) or km.group(3) or km.group(4) or ""
            km = re.search(r"(?:^|[{,\s])" + bool_rx, m.group(2))
            if km:
                entry["enabled"] = km.group(1) == "true"
            continue
        m = re.match(r"^" + prefix + name_rx + r"\s*\.\s*(command|url)\s*" + value_rx, line)
        if m and (in_parent or line.startswith("mcp_servers")):
            servers.setdefault(m.group(1).strip("\"'"), {})[m.group(2)] = \
                m.group(4) or m.group(5) or m.group(6) or ""
            continue
        m = re.match(r"^" + prefix + name_rx + r"\s*\.\s*" + bool_rx, line)
        if m and (in_parent or line.startswith("mcp_servers")):
            servers.setdefault(m.group(1).strip("\"'"), {})["enabled"] = m.group(2) == "true"
    return servers


def mcp_sources(env, repos, read_claude_json):
    """(app, path, kind, repo) for every MCP config file the scanner may read."""
    cfg = env.user_config_dir()
    claude_home = agent_home(env, agent_spec("claude-code"))
    devin_cfg = (env.appdata() if env.is_windows else env.xdg_config()) / "devin"
    user = [
        ("claude-desktop", cfg / "Claude" / "claude_desktop_config.json", "mcpServers"),
        ("cursor", env.home / ".cursor" / "mcp.json", "mcpServers"),
        # Windsurf, renamed Devin Desktop in 2026; its docs name three locations.
        ("windsurf", env.home / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers"),
        ("windsurf", env.home / ".codeium" / "mcp_config.json", "mcpServers"),
        ("windsurf", devin_cfg / "mcp_config.json", "mcpServers"),
        ("gemini-cli", agent_home(env, agent_spec("gemini-cli")) / "settings.json", "mcpServers"),
        ("copilot-cli", agent_home(env, agent_spec("copilot-cli")) / "mcp-config.json", "mcpServers"),
        ("vscode", cfg / "Code" / "User" / "mcp.json", "servers"),
        ("vscode", cfg / "Code - Insiders" / "User" / "mcp.json", "servers"),
        ("opencode", agent_home(env, agent_spec("opencode")) / "opencode.json", "opencode"),
        ("opencode", agent_home(env, agent_spec("opencode")) / "opencode.jsonc", "opencode"),
        ("codex", agent_home(env, agent_spec("codex")) / "config.toml", "codex"),
        ("factory-droid", agent_home(env, agent_spec("factory-droid")) / "mcp.json", "mcpServers"),
    ]
    if env.is_windows:
        # Claude Desktop installed from the Microsoft Store keeps its config in a package folder.
        user.append(("claude-desktop", env.localappdata() / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache"
                     / "Roaming" / "Claude" / "claude_desktop_config.json", "mcpServers"))
    for folder in _managed_folders(env):
        user.append(("claude-code-managed", folder / "managed-mcp.json", "mcpServers"))
    if read_claude_json:
        # CLAUDE_CONFIG_DIR moves .claude.json inside that folder; otherwise it sits in the home folder.
        base = claude_home if env.environ.get("CLAUDE_CONFIG_DIR") else env.home
        user.append(("claude-code", base / ".claude.json", "claude.json"))
    sources = [(app, path, kind, None) for app, path, kind in user]
    for repo in repos:
        # .mcp.json at a repository root is read by several apps (Claude Code, Copilot CLI, VS Code).
        for app, rel, kind in (("project-mcp-json", ".mcp.json", "mcpServers"),
                               ("cursor", ".cursor/mcp.json", "mcpServers"),
                               ("vscode", ".vscode/mcp.json", "servers"),
                               ("gemini-cli", ".gemini/settings.json", "mcpServers"),
                               ("codex", ".codex/config.toml", "codex"),
                               ("copilot-cli", ".github/mcp.json", "mcpServers"),
                               ("factory-droid", ".factory/mcp.json", "mcpServers")):
            sources.append((app, Path(repo) / rel, kind, Path(repo)))
    return sources


def _repo_file_state(path, repo):
    """"absent", "link" or "ok" for a config file inside a repository. Each step down from the
    repository's top is checked with lstat before the next, so a link that the repository
    itself contains (to another machine, or out of the repository) is never followed."""
    try:
        parts = Path(path).relative_to(repo).parts
    except ValueError:
        return "link"
    cur = Path(repo)
    for part in parts:
        cur = cur / part
        try:
            st = os.lstat(str(cur))
        except (OSError, ValueError):
            return "absent"
        if _lstat_is_link(st):
            return "link"
    return "ok"


def _safe_to_read(path, repo, env, read_claude_json, private):
    """Refuse a config path that leads to a session, history or sign-in file (or, without
    consent, .claude.json), is a hard link, is not stored on this computer, or leaves its
    repository. Letter case is ignored where the file system ignores it."""
    fold = (lambda s: s.casefold()) if env.case_insensitive else (lambda s: s)
    real = fold(_norm(path))
    roots, files = private
    if any(real == fold(r) or real.startswith(fold(r) + os.sep) for r in roots) or \
            real in {fold(f) for f in files}:
        return False
    base = os.path.basename(real)
    if base in {fold(n) for n in CREDENTIAL_NAMES}:
        return False
    if not read_claude_json and base == fold(os.path.normcase(".claude.json")):
        return False
    try:
        st = os.stat(str(path))
    except (OSError, ValueError):
        return False
    # A hard link cannot be told from its target by its path; real config files have none.
    if st.st_nlink > 1 or _not_local(st):
        return False
    if repo is not None:
        top = fold(_norm(repo))
        if not real.startswith(top + os.sep):
            return False
    return True


# Helper apps that are really one app, or files shared by several apps.
REAL_APP = {"claude-code-managed": "claude-code", "project-mcp-json": None}


def collect_mcp(env, repos, read_claude_json, hasher, counters):
    by_app = {}
    seen, seen_disabled = set(), set()
    private = private_places(env)
    for app, path, kind, repo in mcp_sources(env, repos, read_claude_json):
        if repo is not None:
            state = _repo_file_state(path, repo)
            if state == "absent":
                continue
            if state == "link":
                counters.skipped_links += 1
                continue
        if not _isfile(path):
            continue
        if not _safe_to_read(path, repo, env, read_claude_json, private):
            counters.skipped_links += 1
            continue
        try:
            if kind == "codex":
                servers = _codex_servers(path)
            else:
                data = _read_json(path)
                if kind == "claude.json":
                    servers = dict(_servers_from(data, "mcpServers"))
                    projects = data.get("projects") if isinstance(data, dict) else None
                    if isinstance(projects, dict):
                        for proj in projects.values():
                            for n, s in _servers_from(proj, "mcpServers").items():
                                servers.setdefault(n, s)
                elif kind == "opencode":
                    servers = _servers_from(data, "mcp")
                else:
                    servers = _servers_from(data, kind)
                del data
        except Exception:        # one bad file (even JSON nested too deep to parse) never stops the scan
            counters.errors.append(f"{app}: a config file could not be parsed")
            continue
        entry = by_app.setdefault(app, {"servers": 0, "commands": {}, "missing_commands": 0,
                                        "unchecked_commands": 0, "remote": 0, "disabled": 0})
        for name, server in servers.items():
            key = (app, hasher(name))
            if _is_disabled(server):              # switched off: counted here only
                if key not in seen_disabled:
                    seen_disabled.add(key)
                    entry["disabled"] += 1
                continue
            if key in seen:
                continue
            seen.add(key)
            try:
                label, first = _command_parts(server)
                check = _command_check(first, env) if label != "remote" and first is not None else None
            except Exception:
                label, check = "unknown", None
            entry["servers"] += 1
            entry["commands"][label] = entry["commands"].get(label, 0) + 1
            if label == "remote":
                entry["remote"] += 1
            elif check == "unchecked":
                entry["unchecked_commands"] += 1
            elif check == "missing":
                entry["missing_commands"] += 1
    real_apps = {REAL_APP.get(app, app) for app, v in by_app.items()
                 if v["servers"] - v["remote"] > 0}
    real_apps.discard(None)
    total = sum(v["servers"] for v in by_app.values())
    remote = sum(v["remote"] for v in by_app.values())
    return {
        "by_app": by_app,
        "total_servers": total,
        "local_servers": total - remote,
        "apps_with_servers": sum(1 for v in by_app.values() if v["servers"]),
        "real_apps_with_local_servers": len(real_apps),
        "distinct_server_names": len({h for _, h in seen}),
        "claude_json_read": bool(read_claude_json),
    }


# ------------------------------------------------------------------ hook policy


def _managed_folders(env):
    """Claude Code's machine-wide managed settings folder for this OS."""
    if env.is_windows:
        return [env.program_files() / "ClaudeCode"]
    if env.is_macos:
        return [env.root / "Library" / "Application Support" / "ClaudeCode"]
    return [env.root / "etc" / "claude-code"]


def _managed_settings_files(env):
    files = []
    for folder in _managed_folders(env):
        files.append(folder / "managed-settings.json")
        drop_in = folder / "managed-settings.d"
        if _isdir(drop_in):
            try:
                files.extend(sorted(Path(e.path) for e in os.scandir(drop_in)
                                    if e.is_file() and e.name.endswith(".json")))
            except OSError:
                pass
    # Settings pushed from a server are cached in the user's Claude folder.
    files.append(agent_home(env, agent_spec("claude-code")) / "remote-settings.json")
    return [f for f in files if _isfile(f)]


def _registry_policy(env):
    """Claude Code policy JSON from the Windows registry, if set. Read-only; None elsewhere."""
    if not env.is_windows or os.name != "nt" or not env.real_machine:
        return None
    try:
        import winreg
    except ImportError:
        return None
    found = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Policies\ClaudeCode", 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, "Settings")
            if isinstance(value, str):
                found.append(json.loads(value))
        except Exception:        # a missing key, a value that is not JSON, JSON nested too deep
            continue
    return found


def _apply_managed(cc, data):
    if isinstance(data, dict):
        cc["managed_hooks_only"] = cc["managed_hooks_only"] or bool(data.get("allowManagedHooksOnly"))
        cc["managed_hooks_disabled"] = cc["managed_hooks_disabled"] or bool(data.get("disableAllHooks"))
        hooks = data.get("hooks")
        cc["managed_hook_events"] += len(hooks) if isinstance(hooks, dict) else 0


def collect_hook_policy(env, counters):
    cc = {}
    private = private_places(env)

    def safe(path):
        # The same checks as MCP config files: never a way into a session, history or sign-in file.
        if _safe_to_read(path, None, env, False, private):
            return True
        counters.skipped_links += 1
        return False

    settings = agent_home(env, agent_spec("claude-code")) / "settings.json"
    cc["user_settings_found"] = _isfile(settings)
    cc["user_hook_events"] = 0
    cc["user_hooks_disabled"] = False
    if cc["user_settings_found"] and safe(settings):
        try:
            data = _read_json(settings)
            hooks = data.get("hooks") if isinstance(data, dict) else None
            cc["user_hook_events"] = len(hooks) if isinstance(hooks, dict) else 0
            cc["user_hooks_disabled"] = bool(isinstance(data, dict) and data.get("disableAllHooks"))
            del data
        except Exception:
            counters.errors.append("claude-code: user settings could not be parsed")
    managed = [p for p in _managed_settings_files(env) if safe(p)]
    registry = _registry_policy(env) or []
    cc["managed_settings_found"] = bool(managed or registry)
    cc["managed_hooks_only"] = False
    cc["managed_hooks_disabled"] = False
    cc["managed_hook_events"] = 0
    for p in managed:
        try:
            _apply_managed(cc, _read_json(p))
        except Exception:
            counters.errors.append("claude-code: managed settings could not be parsed")
    for data in registry:
        _apply_managed(cc, data)
    return {"claude_code": cc}


# ------------------------------------------------------------------ the whole scan


def _is_wsl(env):
    if env.system != "Linux":
        return False
    if not env.real_machine:            # a synthetic machine says so itself, or is not WSL
        return bool(env.environ.get("WSL_DISTRO_NAME"))
    return "microsoft" in platform.release().lower() or bool(env.environ.get("WSL_DISTRO_NAME"))


def collect(env=None, days=14, extra_repos=(), read_claude_json=False, hasher=None, progress=None):
    """Run every collector and return the result as a dict ready for json.dumps.

    `progress`, if given, is called as progress(i, n) before the i-th of n repositories."""
    started_scan = time.monotonic()
    env = env or Env()
    hasher = hasher or Hasher()
    counters = Counters()

    agents = {}
    all_intervals = []
    for spec in AGENTS:
        home = agent_home(env, spec)
        home_found = _isdir(home)
        on_path = any(env.which(b) for b in spec["binaries"])
        # A common program name (e.g. "pi") proves nothing unless the agent's folder exists too.
        installed = home_found or (on_path and not spec.get("generic_binary"))
        try:
            stats = session_stats(env, spec, days, counters)
        except Exception:        # one agent's folder never stops the scan
            stats = None
            counters.errors.append(f"{spec['name']}: its session files could not be counted")
        if stats is not None:
            all_intervals.extend(stats.pop("intervals"))
        reloc = spec.get("home_env")
        agents[spec["name"]] = {
            "installed": bool(installed),
            "on_path": bool(on_path),
            "home_found": home_found,
            "home_relocated": bool(reloc and env.environ.get(reloc[0])),
            "session_layout_source": spec.get("source") if spec.get("sessions") else None,
            "sessions": stats,
        }

    active = [n for n, a in agents.items() if a["sessions"] and a["sessions"]["sessions"] > 0]
    births_known = all(s is not None and e is not None for s, e in all_intervals)
    sessions_summary = {
        "agents_installed": sum(1 for a in agents.values() if a["installed"]),
        "agents_with_sessions": len(active),
        "sessions_total": sum(a["sessions"]["sessions"] for a in agents.values() if a["sessions"]),
        "max_concurrent_all_agents": _max_overlap(all_intervals) if (births_known and all_intervals) else None,
    }

    candidates, found_by = discover_repos(env, extra_repos, counters=counters)
    repos_out = []
    inspected_paths = []
    git_available = env.git_program() is not None
    worktree_checkouts = 0
    caps_hit = time_limited = 0
    partial_clones = shallow_clones = 0
    if git_available:
        by_key = {}
        cloud_repos = 0
        for r in candidates:
            if _git_folder_not_local(r["path"]):
                cloud_repos += 1            # git would make the sync app download it
                continue
            try:
                key = repo_key(r["path"], env)
            except (OSError, subprocess.SubprocessError, ValueError):
                key = None
            if key is None:
                counters.errors.append("a folder was not a readable git working copy")
                continue
            if key in by_key:                 # another checkout (linked worktree) of the same repository
                by_key[key]["sources"] |= r["sources"]
                worktree_checkouts += 1
                continue
            by_key[key] = {"path": r["path"], "sources": set(r["sources"])}
        unique = list(by_key.values())
        if cloud_repos:
            counters.limits.append(f"{cloud_repos} repositories are in cloud-synced folders with files not "
                                   "on this computer; they were not inspected")
        if len(unique) > MAX_REPOS:
            counters.limits.append(f"{len(unique)} repositories found; only {MAX_REPOS} were inspected "
                                   "(named ones first)")
            unique = unique[:MAX_REPOS]
        started = time.monotonic()
        deadline = started + REPO_TIME_BUDGET
        for i, r in enumerate(unique):
            if time.monotonic() - started > REPO_TIME_BUDGET:
                counters.limits.append(f"repository inspection stopped after {REPO_TIME_BUDGET // 60} minutes; "
                                       f"{i} of {len(unique)} repositories were looked at")
                break
            if progress is not None:
                progress(i + 1, len(unique))
            try:
                item, errs = inspect_repo(r["path"], env, days, hasher, deadline)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                item, errs = None, [f"a repository was skipped ({type(exc).__name__})"]
            if item is None:
                counters.errors.extend(errs)
                continue
            item["found_by"] = sorted(r["sources"])
            if item.pop("branch_cap_hit"):
                caps_hit += 1
            time_limited += item.pop("branch_time_limit_hit")
            partial_clones += item["partial_clone"]
            shallow_clones += item["shallow_clone"]
            if errs:
                item["errors"] = errs
            inspected_paths.append(item.pop("_path"))
            repos_out.append(item)
    else:
        counters.limits.append("git is not on PATH, so repositories were counted but not inspected")
    if caps_hit:
        counters.limits.append(f"{caps_hit} repositories had more than {MAX_REFS} unmerged branches; "
                               f"only the {MAX_REFS} newest were inspected in each")
    if time_limited:
        counters.limits.append(f"in {time_limited} repositories not every branch was looked at, because the "
                               "time budget ran out")
    if shallow_clones:
        counters.limits.append(f"{shallow_clones} repositories are shallow clones; branches that fork below "
                               "the cut-off could not be compared and count as unreadable")
    if partial_clones:
        counters.limits.append(f"{partial_clones} repositories are partial clones; missing data is never "
                               "downloaded, so their branches were compared by commit only, and some "
                               "branches may not have been readable")

    # A collector that fails outright costs its own part of the result, never the whole scan.
    try:
        mcp = collect_mcp(env, inspected_paths, read_claude_json, hasher, counters)
    except Exception:
        mcp = None
        counters.errors.append("the MCP helper settings could not be counted")
    try:
        hooks = collect_hook_policy(env, counters)
    except Exception:
        hooks = None
        counters.errors.append("the hook settings could not be read")

    if counters.unreadable_folders:
        counters.limits.append(f"{counters.unreadable_folders} folders could not be read (permission denied)")
    if counters.future_files:
        counters.limits.append(f"{counters.future_files} session files dated in the future were not counted")
    if counters.undecodable_projects:
        counters.limits.append(f"{counters.undecodable_projects} Claude Code project folders could not be "
                               "matched to a folder on disk")
    if counters.skipped_links:
        counters.limits.append(f"{counters.skipped_links} config files were skipped because they are links "
                               "inside a repository, hard links or files not stored on this computer, or "
                               "lead to session, history or sign-in files")

    not_collected = [
        "the contents of any session, transcript, history or sign-in file (only file names, sizes and "
        "dates are read)",
        "prompt text, agent replies and tool output",
        "file contents from repositories (git compares the changes on active branches in memory; "
        "no content is kept)",
        "MCP helper arguments, environment values, headers and program names (config files are read, "
        "but only counts and a launcher label are kept)",
    ]
    if not read_claude_json:
        not_collected.append("Claude Code's .claude.json (it also holds sign-in details and can hold prompt "
                             "history), so its user-level MCP helpers are not counted")
    if env.is_macos:
        not_collected.append("Claude Code policies set through a macOS configuration profile")
    for spec in AGENTS:
        if not spec.get("sessions"):
            not_collected.append(f"{spec['name']} sessions (they are kept in a database, which is not read)")

    return {
        "schema": SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "run": {
            "date": _dt.date.fromtimestamp(env.now).isoformat(),
            "window_days": days,
            "os": env.system,
            "wsl": _is_wsl(env),
            "python": platform.python_version(),
            "duration_seconds": int(round(time.monotonic() - started_scan)),
        },
        "agents": agents,
        "sessions_summary": sessions_summary,
        "repos": {
            "found": len(candidates),
            "found_by": found_by,
            "other_checkouts_of_same_repo": worktree_checkouts,
            "inspected": len(repos_out),
            "items": repos_out,
        },
        "mcp": mcp,
        "hook_policy": hooks,
        "not_collected": not_collected,
        "limits": counters.limits,
        "errors": counters.errors,
    }


def main(argv=None):
    """Developer entry point: prints the raw result for a synthetic home folder only."""
    import argparse
    p = argparse.ArgumentParser(description="Read-only scanner, collectors only (developer use).")
    p.add_argument("--home", required=True, help="the synthetic home folder to scan")
    p.add_argument("--system", required=True, choices=["Windows", "Darwin", "Linux"],
                   help="treat the home folder as this OS")
    p.add_argument("--root", help="synthetic filesystem root for machine-wide files")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--repo", action="append", default=[], help="also inspect this repository")
    args = p.parse_args(argv)
    env = Env.synthetic(args.home, args.system, root=args.root)
    print(json.dumps(collect(env, days=args.days, extra_repos=args.repo), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
