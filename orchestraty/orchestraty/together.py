"""The together check (opt-in): whether two sessions' changes, each fine alone, fail together.

Static rules are precise only where the engine understands the language well, so the together
check confirms an overlap by running code. It is off by default, for each repository; ``orch
together on`` and ``orch together set "<command>"`` turn it on, and the settings live in
Orchestraty's config, never in the repository. When the rules flag a medium or strong finding
between two live sessions in worktrees of their own (O2, O3, O4, not latent), the coordinator
queues a check of the pair: once per content state of the two (the findings that asked are
named in the result), at most once a minute for a pair, one at a time, in a worker thread,
never in a hook, and never holding up the coordinator's loop. The check:

1. snapshots each session's working copy, its tracked files and the untracked files git does
   not ignore, as a commit whose parent is the session's base commit, written into a scratch
   object store in the state folder: git runs with ``GIT_OBJECT_DIRECTORY`` set to the scratch
   store, ``GIT_ALTERNATE_OBJECT_DIRECTORIES`` to the repository's objects, and
   ``GIT_INDEX_FILE`` to a temporary index, so the repository's index, refs and objects stay as
   they were;
2. merges the two snapshots with ``git merge-tree --write-tree`` in the same environment: a
   text conflict is reported as a conflict, and nothing runs;
3. checks the merged tree, and each snapshot alone, out into temporary folders outside every
   repository, and runs the user's command in each (``usercommand``: with the time limit, and
   no ``GIT_`` variable), so that "fails together" is told apart from "already fails alone";
4. records the exit codes and the last lines of output.

When each passes alone and they fail together, both sessions get one note. The scratch store
and the folders are removed when a check ends; what an interrupted one left is removed by the
coordinator's housekeeping. The results are kept in ``<state>/together/results.json``, which
``orch status`` reads, with what ran.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

from orch_engine.ingest import git_command

from orchestraty import config, gitio, usercommand
from orchestraty.coordinator.record import repo_key

DEFAULT_TIMEOUT = 120
RULES = ("O2", "O3", "O4")
KEEP_RESULTS = 200                      # the latest results kept in results.json
STALE_SECONDS = 3600                    # scratch folders older than this are an interrupted check's
IDENTITY = {"GIT_AUTHOR_NAME": "Orchestraty", "GIT_AUTHOR_EMAIL": "orchestraty@localhost",
            "GIT_COMMITTER_NAME": "Orchestraty", "GIT_COMMITTER_EMAIL": "orchestraty@localhost",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}


class CheckError(Exception):
    """git could not do a step of the check; the message says which."""


# ------------------------------------------------------------------------------- settings
def settings(cfg, common_dir):
    """{"on", "command", "timeout"} for one repository: off, no command and 120 seconds by default."""
    return settings_for_key(cfg, repo_key(common_dir))


def settings_for_key(cfg, key):
    """settings() for the repository the record keys `key`."""
    table = cfg.get("together") if isinstance(cfg.get("together"), dict) else {}
    entry = table.get(key) if key else None
    entry = entry if isinstance(entry, dict) else {}
    command, timeout = entry.get("command"), entry.get("timeout")
    return {"on": entry.get("on") is True, "command": command if isinstance(command, str) else "",
            "timeout": timeout if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0
            else DEFAULT_TIMEOUT}


def save_settings(state, common_dir, **changes):
    """Change one repository's settings in config.json; returns them."""
    cfg = config.load(state)
    entry = settings(cfg, common_dir)
    entry.update(changes)
    table = dict(cfg["together"])
    table[repo_key(common_dir)] = entry
    cfg["together"] = table
    config.save(state, cfg)
    return entry


def folder(state):
    return os.path.join(state.root, "together")


# ------------------------------------------------------------------------------- git, in a scratch store
def _env(scratch, common_dir, index=None):
    """git's environment for a check: objects written to the scratch store, the repository's read
    through it, and an index of the check's own."""
    _, env = git_command(["version"])
    for name in gitio.INHERITED:
        env.pop(name, None)
    env.update(IDENTITY)
    env["GIT_OBJECT_DIRECTORY"] = os.path.join(scratch, "objects")
    env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = os.path.join(common_dir, "objects")
    env["GIT_OPTIONAL_LOCKS"] = "0"
    if index:
        env["GIT_INDEX_FILE"] = index
    return env


def _git(args, cwd, env, ok=(0,), timeout=300):
    """(exit code, stdout) of one git command of the check."""
    argv, _ = git_command(list(args), cwd)
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError(f"git {args[0]}: {type(exc).__name__}") from None
    if proc.returncode not in ok:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CheckError(f"git {args[0]} exited {proc.returncode}" + (f": {err[-1][:200]}" if err else ""))
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def snapshot(worktree, base, common_dir, scratch):
    """The commit (in the scratch store) of a worktree's working copy: tracked files and the
    untracked files git does not ignore, with the session's base commit as its parent."""
    index = os.path.join(scratch, "index-" + hashlib.sha1(worktree.encode("utf-8")).hexdigest()[:12])
    env = _env(scratch, common_dir, index)
    head = _git(["rev-parse", "--verify", "--quiet", "HEAD"], worktree, env, ok=(0, 1))[1].strip()
    _git(["read-tree", head] if head else ["read-tree", "--empty"], worktree, env)
    _git(["add", "--all", "--", "."], worktree, env)
    tree = _git(["write-tree"], worktree, env)[1].strip()
    parents = ["-p", base] if base else []
    commit = _git(["commit-tree", tree, *parents, "-m", "Orchestraty together check: a snapshot"], worktree, env)[1]
    return commit.strip(), tree


def merge(a, b, cwd, common_dir, scratch):
    """(merged tree or "", [files in conflict]) of two snapshot commits."""
    env = _env(scratch, common_dir)
    code, out = _git(["merge-tree", "--write-tree", "--name-only", "--no-messages", a, b], cwd, env, ok=(0, 1))
    lines = [x for x in out.splitlines() if x.strip()]
    if code == 0:
        return lines[0], []
    return "", sorted(set(lines[1:])) or ["?"]


def unpack(tree, cwd, common_dir, scratch, dest):
    """Check a tree out into the folder `dest`, outside every repository."""
    index = os.path.join(scratch, "index-unpack")
    env = _env(scratch, common_dir, index)
    _git(["read-tree", tree], cwd, env)
    os.makedirs(dest, exist_ok=True)
    _git(["checkout-index", "--all", "--force", "--prefix=" + dest.rstrip("/\\") + os.sep], cwd, env)
    os.remove(index)


# ------------------------------------------------------------------------------- one check
def state_key(pair, tree_a, tree_b):
    """The pair of sessions and content state a check is about: the same for the same trees."""
    return hashlib.sha1("\0".join([pair, tree_a, tree_b]).encode("utf-8")).hexdigest()[:16]


def run_check(state, job, done=None):
    """Run one check; returns its result (a dict). `job`: {"findings": [keys], "sessions": [a, b],
    "worktrees": {sid: path}, "bases": {sid: commit}, "common_dir", "command", "timeout"}.
    `done`: state keys already checked, which are not run again (the result says "unchanged")."""
    os.makedirs(folder(state), exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="check-", dir=folder(state))
    for part in ("info", "pack"):                       # git takes an object store only once it exists
        os.makedirs(os.path.join(scratch, "objects", part))
    a, b = job["sessions"]
    out = {"repo": job["common_dir"], "findings": list(job["findings"]), "sessions": [a, b],
           "command": job["command"], "timeout": job["timeout"],
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "result": "", "state": "", "conflicts": [],
           "alone": {}, "together": None, "first_failing_line": "", "error": ""}
    try:
        snaps = {sid: snapshot(job["worktrees"][sid], job["bases"][sid], job["common_dir"], scratch) for sid in (a, b)}
        out["state"] = state_key(f"{a}|{b}", snaps[a][1], snaps[b][1])
        if done is not None and out["state"] in done:
            out["result"] = "unchanged"
            return out
        tree, conflicts = merge(snaps[a][0], snaps[b][0], job["worktrees"][a], job["common_dir"], scratch)
        if conflicts:
            out.update(result="conflict", conflicts=conflicts)
            return out
        for name, which in ((a, snaps[a][1]), (b, snaps[b][1]), ("together", tree)):
            dest = os.path.join(scratch, "run-" + ("together" if name == "together" else hashlib.sha1(
                name.encode("utf-8")).hexdigest()[:8]))
            unpack(which, job["worktrees"][a], job["common_dir"], scratch, dest)
            ran = usercommand.run(job["command"], dest, job["timeout"])
            ran.pop("command", None)
            if name == "together":
                out["together"] = ran
            else:
                out["alone"][name] = ran
        passed = {sid: r["exit_code"] == 0 for sid, r in out["alone"].items()}
        together = out["together"]
        if together["timed_out"]:
            out["result"] = "timed out"
        elif together["exit_code"] == 0:
            out["result"] = "passes together"
        elif not all(passed.values()):
            out["result"] = "fails alone"
        else:
            out["result"] = "fails together"
            out["first_failing_line"] = first_failing_line(together["last_lines"])
        return out
    except CheckError as exc:
        out.update(result="error", error=str(exc))
        return out
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def first_failing_line(lines):
    """The line of a failing run's output that says the most: the last one naming an error or a
    failure, else the last line."""
    for line in reversed(lines):
        low = line.lower()
        if any(w in low for w in ("error", "fail", "exception", "assert", "missing", "not found")):
            return line
    return lines[-1] if lines else ""


# ------------------------------------------------------------------------------- results
def results(state):
    """The kept results, oldest first."""
    try:
        with open(os.path.join(folder(state), "results.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return [r for r in data.get("results", []) if isinstance(r, dict)] if isinstance(data, dict) else []


def save_result(state, result):
    kept = (results(state) + [result])[-KEEP_RESULTS:]
    os.makedirs(folder(state), exist_ok=True)
    path = os.path.join(folder(state), "results.json")
    with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"results": kept}, fh, indent=1)
    os.replace(path + ".tmp", path)


def clean_scratch(state, now=None):
    """Remove what interrupted checks left: scratch folders older than an hour."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(folder(state))
    except OSError:
        return
    for name in names:
        path = os.path.join(folder(state), name)
        try:
            if name.startswith("check-") and now - os.stat(path).st_mtime > STALE_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


class Worker:
    """One check at a time, on a thread of its own. The coordinator queues jobs and collects
    results; nothing here touches the record."""

    def __init__(self, state):
        self.state = state
        self._lock = threading.Lock()
        self._queue, self._done, self._queued = [], [], set()
        self._thread = None
        self.checked = {r["state"] for r in results(state) if r.get("state")}

    def queue(self, job):
        """Queue a check, unless one for the same pair is queued or running."""
        pair = tuple(job["sessions"])
        with self._lock:
            if pair in self._queued:
                return False
            self._queued.add(pair)
            self._queue.append(job)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="orch-together", daemon=True)
                self._thread.start()
        return True

    def _run(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._thread = None
                    return
                job = self._queue.pop(0)
            result = run_check(self.state, job, done=self.checked)
            with self._lock:
                self._queued.discard(tuple(job["sessions"]))
                self._done.append(result)

    def take(self):
        """The results finished since the last call."""
        with self._lock:
            done, self._done = self._done, []
        for r in done:
            if r.get("state") and r["result"] not in ("unchanged", "error"):
                self.checked.add(r["state"])
        return done

    def busy(self):
        with self._lock:
            return bool(self._queue) or (self._thread is not None and self._thread.is_alive())
