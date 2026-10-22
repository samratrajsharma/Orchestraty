"""Cloud agents' branches, at level 0: an agent that works elsewhere (in the cloud, or a
personal agent running coding tasks) pushes a branch, and Orchestraty compares that branch with
the live sessions here, through git alone.

Opt-in per watched repository, off by default (``orch remote on``). Its settings live in
config.json under ``remote``, keyed by the repository: the remote to fetch from (``origin``
unless named) and the branch patterns to follow (by default the prefixes agents push to:
``claude/*``, ``codex/*``, ``copilot/*``). A pattern is a refspec glob: one ``*`` at most.

The user's ``.git`` is never written. Each repository with the feature on gets a shadow: a
bare repository of Orchestraty's own, in the state folder (``shadows/<key>.git``), whose
``objects/info/alternates`` points at the user's object store, so a fetch downloads only what
the user's repository lacks. Fetches go into ``refs/orch/<remote>/<branch>`` there, with the
remote's default branch as ``refs/orch/<remote>/HEAD``, from the URL the user's repository has
for that remote. This is the one place Orchestraty uses the network, and only once the user has
turned it on: git runs with no terminal prompt and no askpass, ssh in batch mode, and a
timeout, so it never asks for credentials and never hangs.

The result of each fetch is kept beside the shadow (``shadows/<key>.json``), for ``orch
status``: when, whether it worked, and why not. A failure doubles the wait before the next
automatic try, up to an hour. If the user's garbage collection removes an object the shadow
relied on (an object it found in the user's store at fetch time), reading the shadow fails; the
shadow is then marked broken and fetched afresh.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

from orch_engine.ingest import git_command

from orchestraty import config, gitio
from orchestraty.coordinator.lock import Lock
from orchestraty.coordinator.record import repo_key

CLOUD_AGENT = "cloud"
DEFAULT_REMOTE = "origin"
DEFAULT_PATTERNS = ("claude/*", "codex/*", "copilot/*")
REF_ROOT = "refs/orch"
FETCH_TIMEOUT = 120              # seconds one fetch may take
MAX_BACKOFF_SECONDS = 3600
PROTOCOLS = "file:git:http:https:ssh"
_BROKEN = re.compile(r"could not read|bad object|missing (?:blob|tree|commit)|unable to read|not a valid object|"
                     r"invalid object|corrupt", re.I)
_USERINFO = re.compile(r"(?<=://)[^/@\s]+@")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RemoteError(Exception):
    """A fetch, or a setting, that cannot be done; the message says why."""


class ShadowBroken(Exception):
    """The shadow lacks an object it relied on: it must be fetched afresh."""


# ------------------------------------------------------------------------------- settings
def settings(cfg, common_dir):
    """{"on", "remote", "patterns"} for one repository, with the defaults."""
    table = cfg.get("remote") if isinstance(cfg.get("remote"), dict) else {}
    entry = table.get(repo_key(common_dir))
    entry = entry if isinstance(entry, dict) else {}
    name = entry.get("remote")
    patterns = entry.get("patterns")
    if not (isinstance(patterns, list) and all(isinstance(p, str) and p for p in patterns)):
        patterns = list(DEFAULT_PATTERNS)
    return {"on": entry.get("on") is True, "remote": name if isinstance(name, str) and name else DEFAULT_REMOTE,
            "patterns": patterns}


def save_settings(state, common_dir, **changes):
    """Change one repository's settings in config.json; returns them."""
    cfg = config.load(state)
    entry = settings(cfg, common_dir)
    entry.update(changes)
    table = dict(cfg["remote"])
    table[repo_key(common_dir)] = entry
    cfg["remote"] = table
    config.save(state, cfg)
    return entry


def valid_pattern(pattern):
    """Whether `pattern` can follow branches: a refspec glob under refs/heads/ (one '*' at most)."""
    if not pattern or pattern.startswith("-") or pattern.count("*") > 1:
        return False
    argv, env = git_command(["check-ref-format", "--refspec-pattern", "refs/heads/" + pattern])
    try:
        proc = subprocess.run(argv, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def matches(branch, patterns):
    """Whether a branch name matches one of the patterns (a '*' spans '/', as in a refspec)."""
    for p in patterns:
        head, star, tail = p.partition("*")
        if (branch == p) if not star else (branch.startswith(head) and branch.endswith(tail)
                                           and len(branch) >= len(head) + len(tail)):
            return True
    return False


def broken_error(text):
    """Whether git's message says an object is missing (the shadow must be fetched afresh)."""
    return bool(text) and bool(_BROKEN.search(text))


def safe_url(url):
    """A URL fit to show: without a user name or token."""
    return _USERINFO.sub("", url)


def _clean_error(text):
    lines = [line.strip() for line in safe_url(text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "git failed")[:300]


# ------------------------------------------------------------------------------- the remote
def remote_url(common_dir, name):
    """The URL the user's repository fetches `name` from (its insteadOf rules applied), as an
    absolute path for a local remote. Raises RemoteError when there is no such remote."""
    try:
        urls = gitio.run(["config", "--get-all", f"remote.{name}.url"], common_dir).splitlines()
    except gitio.GitError:
        urls = []
    if not urls or not urls[0].strip():
        raise RemoteError(f"the repository has no remote named {name}")
    try:
        url = gitio.run(["ls-remote", "--get-url", name], common_dir).strip() or urls[0].strip()
    except gitio.GitError:
        url = urls[0].strip()
    if url.startswith("-"):
        raise RemoteError(f"the URL of {name} starts with '-'")
    first = url.replace("\\", "/").split("/", 1)[0]
    local = not _SCHEME.match(url) and (_DRIVE.match(url) or ":" not in first)
    if local and not os.path.isabs(url):
        top = os.path.dirname(common_dir) if os.path.basename(common_dir) == ".git" else common_dir
        url = os.path.normpath(os.path.join(top, url))
    return url


def fetch_env(environ=None, ssh_configured=False):
    """The environment a fetch runs in: git never prompts, no askpass program runs, ssh runs
    in batch mode (unless the user set their own ssh command), and only the usual transports
    are allowed."""
    env = dict(os.environ if environ is None else environ)
    for name in gitio.INHERITED:
        env.pop(name, None)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", SSH_ASKPASS="", SSH_ASKPASS_REQUIRE="never",
               GCM_INTERACTIVE="never", GIT_ALLOW_PROTOCOL=PROTOCOLS, GIT_OPTIONAL_LOCKS="0")
    if not (ssh_configured or env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH")):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=20"
    return env


# ------------------------------------------------------------------------------- the shadow
class Shadow:
    """The shadow repository of one watched repository, and the files beside it."""

    def __init__(self, state, common_dir):
        self.common_dir = os.path.normpath(common_dir)
        key = hashlib.sha1(repo_key(common_dir).encode("utf-8", "surrogateescape")).hexdigest()[:16]
        self.folder = os.path.join(state.root, "shadows")
        self.path = os.path.join(self.folder, key + ".git")
        self.status_path = os.path.join(self.folder, key + ".json")
        self.lock_path = os.path.join(self.folder, key + ".lock")
        self.broken_path = os.path.join(self.folder, key + ".broken")

    def exists(self):
        return os.path.isfile(os.path.join(self.path, "HEAD"))

    def create(self):
        """A new, empty shadow: a bare repository that reads the user's objects through
        objects/info/alternates. Our own settings keep it quiet (no reflogs, no automatic gc)."""
        os.makedirs(self.folder, exist_ok=True)
        if os.path.exists(self.path):
            shutil.rmtree(self.path)
        gitio.run(["init", "--bare", "--quiet", "--template=", self.path], self.folder)
        alternates = os.path.join(self.path, "objects", "info", "alternates")
        os.makedirs(os.path.dirname(alternates), exist_ok=True)
        with open(alternates, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(os.path.join(self.common_dir, "objects").replace("\\", "/") + "\n")
        for key, value in (("core.logAllRefUpdates", "false"), ("gc.auto", "0"), ("maintenance.auto", "false"),
                           ("fetch.writeCommitGraph", "false")):
            gitio.run(["config", key, value], self.path)

    def mark_broken(self, now=None):
        """The shadow lacks an object it relied on: the next fetch makes it afresh, and is due now."""
        with open(self.broken_path, "w", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n")
        kept = self.status()
        if kept:
            kept["next"] = time.time() if now is None else now
            self._write_status(kept)

    def broken(self):
        return os.path.exists(self.broken_path)

    def fetch(self, url, remote, patterns, ssh_configured=False, timeout=FETCH_TIMEOUT):
        """Fetch the matching branches and the default branch into refs/orch/<remote>/ (made
        afresh first when missing or broken, and once more when git finds an object missing
        on the way); prunes the branches the remote no longer has. Raises RemoteError with
        git's reason."""
        try:
            self._fetch(url, remote, patterns, ssh_configured, timeout)
        except ShadowBroken:
            self.mark_broken()
            self._fetch(url, remote, patterns, ssh_configured, timeout)

    def _fetch(self, url, remote, patterns, ssh_configured, timeout):
        if not self.exists() or self.broken():
            self.create()
            try:
                os.remove(self.broken_path)
            except FileNotFoundError:
                pass
        refspecs = [f"+HEAD:{REF_ROOT}/{remote}/HEAD"] + [f"+refs/heads/{p}:{REF_ROOT}/{remote}/{p}" for p in patterns]
        argv = ["git", "-c", "log.showSignature=false", "-c", "core.fsmonitor=false", "-c", "core.longpaths=true",
                "-c", "core.hooksPath=" + os.path.join(self.path, "no-hooks"), "-c", "core.askPass=",
                "-c", "credential.interactive=never", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30",
                "fetch", "--no-tags", "--prune", "--no-recurse-submodules", "--quiet", url] + refspecs
        try:
            proc = subprocess.run(argv, cwd=self.path, env=fetch_env(ssh_configured=ssh_configured),
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                                  creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RemoteError(f"no answer from {remote} within {timeout} seconds") from None
        except OSError as exc:
            raise RemoteError(f"git could not start: {type(exc).__name__}") from None
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")
            if broken_error(err) and not self.broken():
                raise ShadowBroken(_clean_error(err))
            raise RemoteError(_clean_error(err))

    # reading: local git only
    def _git(self, args):
        """(returncode, stdout) of a read-only git command in the shadow. Raises ShadowBroken
        when git says an object is missing."""
        argv, env = git_command(args, self.path)
        env["GIT_OPTIONAL_LOCKS"] = "0"
        try:
            proc = subprocess.run(argv, cwd=self.path, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=gitio.TIMEOUT, creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise gitio.GitError(f"git {args[0]}: {type(exc).__name__}") from None
        err = proc.stderr.decode("utf-8", "replace")
        if proc.returncode != 0 and broken_error(err):
            raise ShadowBroken(_clean_error(err))
        return proc.returncode, proc.stdout.decode("utf-8", "replace")

    def refs(self, remote):
        """({branch: tip}, the default branch's tip or "") as last fetched."""
        if not self.exists():
            return {}, ""
        rc, out = self._git(["for-each-ref", "--format=%(objectname) %(refname)", f"{REF_ROOT}/{remote}/"])
        if rc != 0:
            raise gitio.GitError("for-each-ref failed in the shadow")
        prefix, branches, default = f"{REF_ROOT}/{remote}/", {}, ""
        for line in out.splitlines():
            sha, _, ref = line.partition(" ")
            if ref.startswith(prefix):
                name = ref[len(prefix):]
                if name == "HEAD":
                    default = sha
                else:
                    branches[name] = sha
        return branches, default

    def branch(self, tip, default):
        """{"base", "merged", "time"} for a branch tip: its merge base with the default
        branch, whether the default branch contains it, and its commit's time (seconds)."""
        rc, _ = self._git(["merge-base", "--is-ancestor", tip, default])
        if rc not in (0, 1):
            raise gitio.GitError("merge-base --is-ancestor failed in the shadow")
        rc_base, base = self._git(["merge-base", tip, default])
        rc_time, when = self._git(["show", "-s", "--format=%ct", tip])
        if rc_base != 0 or rc_time != 0:
            raise gitio.GitError("the branch's merge base or time cannot be read in the shadow")
        return {"base": base.strip(), "merged": rc == 0, "time": int(when.strip() or 0)}

    # the fetch status
    def status(self):
        try:
            with open(self.status_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def record(self, ok, error, now, interval_seconds, remote, url, branches=0):
        """Keep the result of one fetch; a failure doubles the wait before the next try."""
        old = self.status()
        failures = 0 if ok else int(old.get("failures") or 0) + 1
        wait = interval_seconds if ok else min(interval_seconds * 2 ** failures, MAX_BACKOFF_SECONDS)
        data = {"remote": remote, "url": safe_url(url), "at": now, "ok": ok, "error": "" if ok else error,
                "failures": failures, "next": now + wait, "last_ok": now if ok else old.get("last_ok"),
                "branches": branches if ok else old.get("branches", 0)}
        self._write_status(data)
        return data

    def _write_status(self, data):
        os.makedirs(self.folder, exist_ok=True)
        tmp = self.status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.status_path)

    def due(self, now):
        """Whether the next automatic fetch is due (never fetched, or its wait is over)."""
        nxt = self.status().get("next")
        return not isinstance(nxt, (int, float)) or now >= nxt


def fetch_now(state, common_dir, chosen, interval_seconds, now=None, wait=0.0):
    """Fetch one repository's cloud branches now (holding the shadow's lock, waiting up to
    `wait` seconds for it) and keep the result. Returns the result kept, or None when another
    fetch holds the lock."""
    shadow = Shadow(state, common_dir)
    os.makedirs(shadow.folder, exist_ok=True)
    lock = Lock(shadow.lock_path)
    deadline = time.monotonic() + wait
    while not lock.acquire():
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)
    try:
        url = ""
        try:
            url = remote_url(common_dir, chosen["remote"])
            ssh = _ssh_configured(common_dir)
            shadow.fetch(url, chosen["remote"], chosen["patterns"], ssh_configured=ssh)
            branches, _ = shadow.refs(chosen["remote"])
            n = sum(1 for b in branches if matches(b, chosen["patterns"]))
        except (RemoteError, gitio.GitError, gitio.UntrustedRepositoryError, ShadowBroken, OSError) as exc:
            return shadow.record(False, _clean_error(str(exc)), time.time() if now is None else now,
                                 interval_seconds, chosen["remote"], url)
        return shadow.record(True, "", time.time() if now is None else now, interval_seconds, chosen["remote"],
                             url, branches=n)
    finally:
        lock.release()


def _ssh_configured(common_dir):
    try:
        return bool(gitio.run(["config", "--get", "core.sshCommand"], common_dir).strip())
    except gitio.GitError:
        return False
