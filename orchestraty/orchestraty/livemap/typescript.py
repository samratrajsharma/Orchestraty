"""TypeScript contracts, opt-in for each repository (``orch ts on|off``): what a session's changes
do to the exported API of its TypeScript modules.

Running the project's compiler runs the user's toolchain, so nothing here runs while it is off
(the default). When it is on, for a session that changed .ts or .tsx files in a worktree:

* **The compiler**, where the worktree has a ``tsconfig.json`` at its top and a TypeScript
  compiler (``node_modules/.bin/tsc``, else ``tsc`` on PATH): it writes the declarations of the
  session's working copy, and then of its base commit, with ``--declaration --emitDeclarationOnly``,
  into the state folder: each with an ``--outDir`` and an ``--incremental`` build-info of its
  own, so nothing is written into the repository (a config that sets ``outFile`` is not
  compiled). The base commit's files are checked out into a folder of the state folder that
  borrows the worktree's ``node_modules`` through a link, read only. A run has a time limit; one
  runs at a time, on a thread of its own, never in a hook; a session is compiled again once its
  .ts files changed, at most every ``COOLDOWN`` seconds. It compiled when declarations were
  written and no error is about the project rather than its code (one in no file, as about its
  options or inputs, or in its tsconfig): errors in the code (exit code 2) leave the declarations
  right.
* **The comparison**, module by module, of the declarations (``dts``): an export removed, a
  parameter removed or made required, a required property added to an input, and the rest. A
  file with a syntax error its base commit does not have (TS1xxx: one caught mid-edit, as a
  rule) is not compared: its declarations are what the compiler made of what it could read.
* **The fallback**, where there is no tsconfig.json or compiler, or the project does not compile
  (or takes too long): the exports of the changed files are read from their source, at the base
  commit and in the working copy, and compared the same way; a file left open (a bracket, a
  template literal or a comment) is not compared. What it finds is heuristic, and ``orch status`` says why it was used.

Uses need import evidence (``tsimports``) and calls are not resolved, so an O3 or O4 about
TypeScript is never strong.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

from orchestraty import config, gitio, together, usercommand
from orchestraty.coordinator.record import repo_key
from orchestraty.livemap import dts, tsimports

DEFAULT_TIMEOUT = 300
COOLDOWN = 15                      # seconds between two compiler runs for one session
KEEP_BASES = 4                     # base commits whose declarations are kept in memory
KEEP_RUNS = 100
OUTPUT_LINES = 20000               # lines of the compiler's output read for its errors
EMPTY = {"exports": {}, "imports": {}, "stars": []}
_DIAGNOSTIC = re.compile(r"^(?:(.+?)\(\d+,\d+\): )?error TS(\d+):")       # with --pretty false


# ------------------------------------------------------------------------------ settings
def settings_for_key(cfg, key):
    """{"on", "timeout"} for the repository the record keys `key`: off and DEFAULT_TIMEOUT by default."""
    table = cfg.get("typescript") if isinstance(cfg.get("typescript"), dict) else {}
    entry = table.get(key) if key else None
    entry = entry if isinstance(entry, dict) else {}
    timeout = entry.get("timeout")
    return {"on": entry.get("on") is True,
            "timeout": timeout if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0
            else DEFAULT_TIMEOUT}


def settings(cfg, common_dir):
    return settings_for_key(cfg, repo_key(common_dir))


def save_settings(state, common_dir, **changes):
    """Change one repository's settings in config.json; returns them."""
    cfg = config.load(state)
    entry = settings(cfg, common_dir)
    entry.update(changes)
    table = dict(cfg["typescript"])
    table[repo_key(common_dir)] = entry
    cfg["typescript"] = table
    config.save(state, cfg)
    return entry


def folder(state):
    return os.path.join(state.root, "ts")


def runs(state):
    """The latest run for each session: {session id: run}, as the worker last wrote them."""
    try:
        with open(os.path.join(folder(state), "runs.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    found = data.get("runs") if isinstance(data, dict) else None
    return {k: v for k, v in found.items() if isinstance(v, dict)} if isinstance(found, dict) else {}


def describe(run):
    """What a session's latest run did, in a few words (orch status, orch ts)."""
    if run.get("source") == "compiler":
        line = "compiled" + (" (with errors)" if (run.get("head") or {}).get("exit_code") == 2 else "")
    else:
        line = "exports read from the source, heuristic: " + (run.get("why") or "no compiler run")
    unread = list(run.get("not_compared") or [])
    if unread:
        line += "; not compared, a syntax error: " + ", ".join(unread[:3]) + (
            f" and {len(unread) - 3} more" if len(unread) > 3 else "")
    return line


# ------------------------------------------------------------------------------ the compiler
def compiler(worktree):
    """The TypeScript compiler a worktree uses: its node_modules/.bin/tsc, else tsc on PATH, or None."""
    local = os.path.join(worktree, "node_modules", ".bin", "tsc.cmd" if os.name == "nt" else "tsc")
    if os.path.isfile(local):
        return local
    return shutil.which("tsc")


def project_problem(root):
    """Why the project at `root` is not compiled ("" when it can be): no tsconfig.json at its
    top, or one that sets outFile (its declarations would go there)."""
    if not os.path.isfile(os.path.join(root, "tsconfig.json")):
        return "no tsconfig.json at the top of the worktree"

    def read(path):
        try:
            with open(os.path.join(root, *path.split("/")), "r", encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            return None
    cfg = tsimports.TsConfig("tsconfig.json", read)
    if not cfg.ok:
        return "tsconfig.json could not be read"
    if cfg.options.get("outFile") or cfg.options.get("out"):
        return "tsconfig.json sets outFile"
    return ""


def _command(tsc, root, out, buildinfo):
    argv = [tsc, "-p", os.path.join(root, "tsconfig.json"), "--declaration", "--emitDeclarationOnly",
            "--noEmit", "false", "--noEmitOnError", "false", "--declarationMap", "false", "--sourceMap", "false",
            "--outDir", out, "--declarationDir", out, "--rootDir", root, "--incremental",
            "--tsBuildInfoFile", buildinfo, "--pretty", "false"]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _has_output(out):
    for _top, _dirs, names in os.walk(out):
        if any(n.endswith((".d.ts", ".d.mts", ".d.cts")) for n in names):
            return True
    return False


def _diagnostic(line):
    """(file or None, code) of an error line of the compiler's output, or None."""
    m = _DIAGNOSTIC.match(line)
    return (m.group(1).replace("\\", "/") if m.group(1) else None, int(m.group(2))) if m else None


def _project_error(line):
    """Whether an error line is about the project rather than its code: one with no file (its
    options, its inputs) or in a file that is not code (its tsconfig)."""
    d = _diagnostic(line)
    return d is not None and (d[0] is None or not d[0].endswith(tsimports.CODE_EXTS))


def syntax_errors(lines):
    """The code files (posix paths from the project's top) the compiler's output gives a syntax
    error in: TS1xxx, the parser's errors and a few of the grammar's."""
    out = set()
    for d in map(_diagnostic, lines):
        if d and d[0] and d[0].endswith(tsimports.CODE_EXTS) and 1000 <= d[1] < 2000:
            out.add(d[0])
    return out


def run_compiler(tsc, root, work, timeout):
    """Compile the project at `root`, its declarations into work/out and its build-info in
    work: {"ok", "exit_code", "timed_out", "seconds", "error", "syntax_errors"}. It compiled when
    declarations were written and no error is about the project itself: type errors in its code
    (exit code 2) still leave its declarations right."""
    out, buildinfo = os.path.join(work, "out"), os.path.join(work, "tsbuildinfo")
    if not _has_output(out) and os.path.exists(buildinfo):
        os.remove(buildinfo)                 # an incremental run writes only what changed: start afresh
    os.makedirs(out, exist_ok=True)
    ran = usercommand.run(_command(tsc, root, out, buildinfo), root, timeout, first=OUTPUT_LINES)
    lines = ran.get("first_lines", []) + ran["last_lines"]
    errors = [line for line in lines if _diagnostic(line)]
    project = [line for line in errors if _project_error(line)]
    ok = not ran["timed_out"] and ran["exit_code"] in (0, 2) and not project and _has_output(out)
    error = "" if ran["exit_code"] == 0 and ok else (project or errors or lines[-1:] or ["no declarations written"])[0]
    return {"ok": ok, "exit_code": ran["exit_code"], "timed_out": ran["timed_out"], "seconds": ran["seconds"],
            "error": usercommand.clean_line(error, 300), "syntax_errors": sorted(syntax_errors(lines))}


def declarations(out, root):
    """{source posix path: module} of the declaration files in `out`, each for the source file
    under `root` it was made from (one that is gone, or never was, is left out)."""
    found = {}
    for top, _dirs, names in os.walk(out):
        for name in names:
            for ext, sources in ((".d.mts", (".mts",)), (".d.cts", (".cts",)), (".d.ts", (".ts", ".tsx"))):
                if not name.endswith(ext):
                    continue
                stem = os.path.relpath(os.path.join(top, name), out).replace(os.sep, "/")[:-len(ext)]
                for e in sources:
                    if os.path.isfile(os.path.join(root, *(stem + e).split("/"))):
                        try:
                            with open(os.path.join(top, name), "r", encoding="utf-8") as fh:
                                found[stem + e] = dts.read(fh.read())
                        except (OSError, UnicodeDecodeError):
                            pass
                        break
                break
    return found


def _borrow(target, link):
    """Make `link` point at the folder `target`: a symbolic link, or on Windows a junction (which
    needs no privilege). False when it cannot be made."""
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(target, link)
        else:
            os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, ImportError, AttributeError):
        return False


def _unborrow(link):
    """Remove the link, never what it points at."""
    try:
        if os.name == "nt":
            os.rmdir(link)
        else:
            os.unlink(link)
    except OSError:
        pass


def _discard(path):
    try:
        if os.path.exists(path):
            gitio.remove_tree(path)
    except OSError:
        pass


def base_declarations(state, worktree, base, tsc, timeout):
    """(declarations, run) of a base commit: its files checked out into the state folder, with
    the worktree's node_modules borrowed, compiled there, and removed afterwards."""
    work = os.path.join(folder(state), "base-" + base[:20])
    tree, scratch = os.path.join(work, "tree"), os.path.join(work, "git")
    _discard(work)
    for part in ("info", "pack"):
        os.makedirs(os.path.join(scratch, "objects", part))
    link = os.path.join(tree, "node_modules")
    borrowed = False
    try:
        try:
            common_dir = gitio.worktree_info(worktree)["common_dir"]
            together.unpack(base, worktree, common_dir, scratch, tree)
        except (together.CheckError, gitio.GitError, gitio.UntrustedRepositoryError, KeyError, TypeError) as exc:
            return {}, {"ok": False, "error": f"the base commit could not be checked out: {exc}"[:300],
                        "exit_code": None, "timed_out": False, "seconds": 0}
        why = project_problem(tree)
        if why:
            return {}, {"ok": False, "error": why.replace("worktree", "base commit"), "exit_code": None,
                        "timed_out": False, "seconds": 0}
        modules = os.path.join(worktree, "node_modules")
        if not os.path.lexists(link) and os.path.isdir(modules):
            borrowed = _borrow(modules, link)
        run = run_compiler(tsc, tree, work, timeout)
        found = declarations(os.path.join(work, "out"), tree) if run["ok"] else {}
        for path, module in found.items():                   # where each export is declared, from source
            module["lines"] = _export_lines(_read(os.path.join(tree, *path.split("/"))))
        return found, run
    finally:
        if borrowed:
            _unborrow(link)
        _discard(work)


def head_declarations(state, worktree, tsc, timeout):
    """(declarations, run) of a working copy, compiled where it is; the output and build-info
    stay in the state folder for the next, incremental, run."""
    work = os.path.join(folder(state), "head-" + hashlib.sha1(os.path.normcase(worktree).encode("utf-8")).hexdigest()[:16])
    os.makedirs(work, exist_ok=True)
    run = run_compiler(tsc, worktree, work, timeout)
    return (declarations(os.path.join(work, "out"), worktree) if run["ok"] else {}), run


# ------------------------------------------------------------------------------ comparing
def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _export_lines(text):
    """{export name: the line it is declared on} in a module's source."""
    if text is None:
        return {}
    return {name: d.get("line") or 0 for name, d in dts.read(text, source=True)["exports"].items()}


def _inputs(modules):
    """{(module, name)} of the interfaces and object types an exported function, method or
    constructor of some module takes: in its own module, or imported from another."""
    out = set()
    for path, module in modules.items():
        names = dts.inputs(module)
        out.update((path, n) for n in names)
        for local, (spec, imported) in module.get("imports", {}).items():
            if local in names and spec.startswith("."):
                stem = os.path.normpath(os.path.join(os.path.dirname(path), spec)).replace(os.sep, "/")
                for target in (stem + ".ts", stem + ".tsx", stem + "/index.ts", stem):
                    if target in modules:
                        out.add((target, imported))
                        break
    return out


def compare_modules(base, head, present, heuristic, skip=frozenset()):
    """[change] between two {path: module}: each {"symbol", "kind", "contract", "reasons", "file",
    "line", "heuristic"}. A module the working copy lacks while its source is `present(path)`
    was not compiled this time, and one in `skip` could not be read: nothing is said about them."""
    used = _inputs(base) | _inputs(head)
    out = []
    for path in sorted(set(base) | set(head)):
        old, new = base.get(path), head.get(path)
        if (new is None and present(path)) or path in skip:
            continue
        for name, kind, verdict, reasons in dts.compare(old or EMPTY, new or EMPTY,
                                                        {n for (m, n) in used if m == path}):
            if verdict == dts.NONE or name == "*":
                continue
            lines = ((old or {}) if kind == "removed" else (new or {})).get("lines", {})
            out.append({"symbol": f"{path}::{name}", "kind": kind, "contract": verdict, "reasons": reasons[:6],
                        "file": path, "line": lines.get(name, 0), "heuristic": heuristic})
    return out


def _base_text(worktree, base, path):
    try:
        return gitio.run(["show", f"{base}:{path}"], worktree)
    except (gitio.GitError, gitio.UntrustedRepositoryError):
        return None


def _source_module(text):
    module = dts.read(text, source=True)
    module["lines"] = {n: d.get("line") or 0 for n, d in module["exports"].items()}
    return module


def fallback_changes(worktree, base, ts_files):
    """(changes, files not compared): the changes the exports of the changed files' source show
    (heuristic). `ts_files` is {posix path: "A" | "M" | "D"}. A file of the working copy left open
    (a bracket, a template literal or a comment: dts.balanced) is not compared."""
    base_mods, head_mods, unread = {}, {}, []
    for path, status in sorted(ts_files.items()):
        if status != "D":
            text = _read(os.path.join(worktree, *path.split("/")))
            if text is not None and not dts.balanced(dts.tokens(text, source=True)):
                unread.append(path)
                continue
            if text is not None:
                head_mods[path] = _source_module(text)
        if status != "A":
            text = _base_text(worktree, base, path)
            if text is not None:
                base_mods[path] = _source_module(text)
    return compare_modules(base_mods, head_mods, lambda p: False, heuristic=True), unread


# ------------------------------------------------------------------------------ the sessions
class TypeScript:
    """The TypeScript contracts of the live map's sessions: their settings, the compiler runs
    (on a worker thread) and each session's latest changes. `cfg` is set by the coordinator."""

    def __init__(self, state, log):
        self.state, self.log = state, log
        self.cfg = dict(config.DEFAULTS)
        self._lock = threading.Lock()
        self._queue, self._queued, self._done = [], set(), []
        self._thread = None
        self._bases = {}                  # (repo key, base commit) -> (declarations, run)
        self._results = {}                # session id -> {"key", "changes", "source", "why"}
        self._started = {}                # session id -> when its last run was queued (monotonic)

    def on(self, key):
        return settings_for_key(self.cfg, key)["on"]

    def forget(self, sid):
        with self._lock:
            self._results.pop(sid, None)
            self._started.pop(sid, None)

    def changes(self, sid, worktree, base, key, ts_files, content):
        """The session's latest TypeScript changes, for the live map's refresh: compiled ones
        when the project compiles (a new run is queued once its content changed), else the
        fallback's. `content`: what its .ts files hold now (a key)."""
        entry = settings_for_key(self.cfg, key)
        if not entry["on"] or not ts_files or not worktree or not base:
            self.forget(sid)
            return []
        content = (base, content)
        with self._lock:
            result = self._results.get(sid)
        tsc = compiler(worktree)
        why = "no TypeScript compiler (node_modules/.bin/tsc, or tsc on PATH)" if tsc is None else project_problem(worktree)
        if why:
            if result is None or result["key"] != content or result["source"] != "fallback":
                changes, unread = fallback_changes(worktree, base, ts_files)
                result = {"key": content, "changes": changes, "source": "fallback", "why": why}
                with self._lock:
                    self._results[sid] = result
                self._record(sid, key, {"source": "fallback", "why": why, "not_compared": unread})
            return result["changes"]
        if result is None or result["key"] != content:
            mono = time.monotonic()
            with self._lock:
                due = sid not in self._queued and mono - self._started.get(sid, -COOLDOWN) >= COOLDOWN
                if due:
                    self._started[sid] = mono
                    self._queued.add(sid)
                    self._queue.append({"sid": sid, "worktree": worktree, "base": base, "repo_key": key,
                                        "ts_files": dict(ts_files), "content": content, "tsc": tsc,
                                        "timeout": entry["timeout"]})
                    if self._thread is None or not self._thread.is_alive():
                        self._thread = threading.Thread(target=self._work, name="orch-typescript", daemon=True)
                        self._thread.start()
        return result["changes"] if result is not None else []

    def take(self):
        """The sessions whose run finished since the last call (the live map refreshes them)."""
        with self._lock:
            done, self._done = self._done, []
        return done

    def busy(self):
        with self._lock:
            return bool(self._queue) or (self._thread is not None and self._thread.is_alive())

    def _work(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._thread = None
                    return
                job = self._queue.pop(0)
            try:
                result = self._run(job)
            except Exception as exc:          # noqa: BLE001 - one run's failure must not stop the others
                self.log.warning("TypeScript run for %s failed: %s", job["sid"][:8], exc)
                result = {"key": job["content"], "changes": fallback_changes(job["worktree"], job["base"],
                                                                             job["ts_files"])[0],
                          "source": "fallback", "why": f"the run failed: {type(exc).__name__}"}
            with self._lock:
                self._queued.discard(job["sid"])
                if job["sid"] in self._started:          # not forgotten meanwhile
                    self._results[job["sid"]] = result
                    self._done.append(job["sid"])

    def _run(self, job):
        worktree, base, tsc, timeout = job["worktree"], job["base"], job["tsc"], job["timeout"]
        head, head_run = head_declarations(self.state, worktree, tsc, timeout)
        base_decls, base_run = {}, None
        if head_run["ok"]:                    # else the fallback is used whatever the base does
            with self._lock:
                cached = self._bases.get((job["repo_key"], base))
            if cached is None:
                cached = base_declarations(self.state, worktree, base, tsc, timeout)
                if cached[1]["ok"] or not cached[1]["timed_out"]:    # a commit never changes; a timeout may pass
                    with self._lock:
                        self._bases[(job["repo_key"], base)] = cached
                        while len(self._bases) > KEEP_BASES:
                            self._bases.pop(next(iter(self._bases)))
            base_decls, base_run = cached
        record = {side: {k: v for k, v in run.items() if k != "syntax_errors"} if run else None
                  for side, run in (("head", head_run), ("base", base_run))}
        if head_run["ok"] and base_run["ok"]:
            for path, module in head.items():
                module["lines"] = _export_lines(_read(os.path.join(worktree, *path.split("/"))))
            unread = sorted(set(head_run["syntax_errors"]) - set(base_run.get("syntax_errors", ())))
            changes = compare_modules(base_decls, head, lambda p: os.path.isfile(os.path.join(worktree, *p.split("/"))),
                                      heuristic=False, skip=set(unread))
            result = {"key": job["content"], "changes": changes, "source": "compiler", "why": ""}
        else:
            failed = "the working copy" if not head_run["ok"] else "the base commit"
            run = head_run if not head_run["ok"] else base_run
            why = (f"{failed} took longer than {timeout} s to compile" if run.get("timed_out") else
                   f"{failed} did not compile" + (f": {run['error']}" if run.get("error") else ""))
            changes, unread = fallback_changes(worktree, base, job["ts_files"])
            result = {"key": job["content"], "changes": changes, "source": "fallback", "why": why}
        record.update(source=result["source"], why=result["why"], not_compared=unread)
        self._record(job["sid"], job["repo_key"], record)
        return result

    def _record(self, sid, key, run):
        """Keep a session's latest run in <state>/ts/runs.json, for orch status."""
        run = dict(run, repo=key, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        run["not_compared"] = list(run.get("not_compared", ()))[:20]
        with self._lock:
            kept = runs(self.state)
            kept[sid] = run
            if len(kept) > KEEP_RUNS:
                for old in sorted(kept, key=lambda s: kept[s].get("at", ""))[:len(kept) - KEEP_RUNS]:
                    kept.pop(old, None)
            os.makedirs(folder(self.state), exist_ok=True)
            path = os.path.join(folder(self.state), "runs.json")
            with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"runs": kept}, fh, indent=1, sort_keys=True)
            os.replace(path + ".tmp", path)


def clean(state, keep, now=None):
    """Remove the compiler output of worktrees no longer followed (`keep`: the worktrees that
    are), and what an interrupted base run left, once a day old or an hour old."""
    now = time.time() if now is None else now
    wanted = {"head-" + hashlib.sha1(os.path.normcase(w).encode("utf-8")).hexdigest()[:16] for w in keep}
    try:
        names = os.listdir(folder(state))
    except OSError:
        return
    for name in names:
        path = os.path.join(folder(state), name)
        try:
            age = now - os.stat(path).st_mtime
        except OSError:
            continue
        if (name.startswith("base-") and age > 3600) or (name.startswith("head-") and name not in wanted and age > 86400):
            _discard(path)
