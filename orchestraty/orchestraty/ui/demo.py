"""orch ui --demo: the whole loop in a sandbox, step by step, with no agent running.

Everything happens in a temporary folder made for this process, removed on close:

* a state folder of its own, never the user's, with the hook script copied in as orch init
  copies it; no Claude Code settings are written anywhere;
* a small git repository like the tests' (``a.py`` defines ``charge(amount, currency)``,
  ``b.py`` calls it) with two worktrees, ``wt-a`` and ``wt-b``, watched on that state folder
  as orch watch would, the push line chained into its git hooks, and a bare remote to push to.
  git runs with a global config of its own, so the user's settings and hooks stay out.

Two pretend Claude Code sessions, A in wt-a and B in wt-b, are driven through the real hook
script, run as a subprocess and fed the JSON Claude Code sends, and through real file edits.
After each step the coordinator is stepped in this process (``Coordinator(state).step()``), as
the tests do; the hooks never start one. On Windows the subprocesses get no console window, as
the coordinator's do.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from orchestraty import __version__, config, gitio
from orchestraty.capture import githooks, install
from orchestraty.coordinator import main as cmain
from orchestraty.coordinator.lock import Heartbeat, Lock
from orchestraty.paths import State

NO_WINDOW = 0x08000000 if os.name == "nt" else 0         # CREATE_NO_WINDOW
TIMEOUT = 60
INDEX_WAIT = 120
SESSION_TIMEOUT = 7 * 24 * 3600                           # the pretend sessions never time out while it is open

A_PY = b"def charge(amount, currency):\n    return amount\n"
B_PY = b"from a import charge\n\n\ndef pay():\n    return charge(1, 'EUR')\n"
C_PY = b"from a import charge\n\n\ndef refund():\n    return charge(2, 'USD')\n"
A_OLD, A_NEW = "def charge(amount, currency):", "def charge(amount, *, currency):"
C_OLD = "    return charge(2, 'USD')"
C_NEW = "    # charge() takes currency by name in wt-a\n    return charge(2, currency='USD')"

STEPS = (
    "Sessions A (wt-a) and B (wt-b) start.",
    "B adds a call to charge in c.py.",
    "A makes currency keyword-only in a.py: a breaking change.",
    "B’s next tool result: the hook hands B its note.",
    "A tries to finish: its Stop hook gets the note about callers left behind.",
    "The push line for wt-a appears: A’s worktree is pushed.",
    "B passes currency by name, and the overlap resolves.",
)
KINDS = {"edit": "before its next edit of the file", "next": "with its next tool result",
         "stop": "when it tries to finish its turn", "wake": "to wake it"}


class DemoError(Exception):
    """The sandbox could not be made, or a step failed."""


class Busy(DemoError):
    """A step is running."""


class Finished(DemoError):
    """Every step has run."""


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def _remove_tree(path):
    """Remove a folder, read-only files (git's objects on Windows) included; True when gone."""
    def again(func, target, _info):
        try:
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
            func(target)
        except OSError:
            pass                                         # retried below, once handles are let go
    for attempt in range(6):
        if not os.path.exists(path):
            return True
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=again)
        else:
            shutil.rmtree(path, onerror=again)
        if not os.path.exists(path):
            return True
        time.sleep(0.2 * (attempt + 1))
    return not os.path.exists(path)


class Demo:
    """The sandbox and its scenario. ``next_step`` runs one step; ``close`` removes it all."""

    def __init__(self):
        if shutil.which("git") is None:
            raise DemoError("the demo needs git on PATH")
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="orch-demo-"))
        self.coordinator = None
        self._heartbeat = None
        self._lock = None
        self._closed = False
        try:
            self._build()
        except BaseException:
            self.close()
            raise
        self.done = []                                   # [{"n", "outputs"}]
        self.error = None
        self._busy = threading.Lock()

    # ------------------------------------------------------------------ the sandbox
    def _build(self):
        root = self.root
        self.state = State(os.path.join(root, "state"))
        os.makedirs(self.state.root)
        self._gitconfig = os.path.join(root, "gitconfig")
        _write(self._gitconfig, b"")
        self.python = install.hook_python()
        self.repo = os.path.join(root, "repo")
        self.worktrees = {"A": os.path.join(root, "wt-a"), "B": os.path.join(root, "wt-b")}
        self.remote = os.path.join(root, "origin.git")
        os.makedirs(self.repo)
        self._git(self.repo, "init", "-q")
        self._git(self.repo, "config", "core.autocrlf", "false")
        _write(os.path.join(self.repo, "a.py"), A_PY)
        _write(os.path.join(self.repo, "b.py"), B_PY)
        self._git(self.repo, "add", "a.py", "b.py")
        self._git(self.repo, "commit", "-q", "-m", "charge() and its first caller")
        self._git(self.repo, "worktree", "add", "-q", "-b", "feat-a", self.worktrees["A"])
        self._git(self.repo, "worktree", "add", "-q", "-b", "feat-b", self.worktrees["B"])
        self._git(root, "init", "-q", "--bare", self.remote)
        info = gitio.worktree_info(self.repo)            # the folder git names, as orch watch takes it
        if info is None:
            raise DemoError("git did not see the sandbox repository")
        config.watch(self.state, info["common_dir"])
        cfg = config.load(self.state)
        cfg["session_timeout"] = SESSION_TIMEOUT
        config.save(self.state, cfg)
        install.install_script(self.state)              # as orch init copies it; no Claude Code settings
        githooks.install(self.worktrees["A"], self.python, self.state.hook_script)
        self.sessions = {"A": str(uuid.uuid4()), "B": str(uuid.uuid4())}
        self._hook_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self._hook_env["ORCH_NO_COORDINATOR"] = "1"      # the coordinator runs here, stepped
        self._lock = Lock(self.state.lock)
        self._lock.acquire()
        self._heartbeat = Heartbeat(self.state.alive)
        self._heartbeat.start()
        with open(self.state.info, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": time.time(), "version": __version__, "python": sys.executable}, fh)
        self.coordinator = cmain.Coordinator(self.state)
        self._spool_read = {}
        self._notes_seen = 0

    def _git(self, cwd, *args):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=self._gitconfig, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                   GIT_AUTHOR_NAME="Orchestraty demo", GIT_AUTHOR_EMAIL="demo@example.invalid",
                   GIT_COMMITTER_NAME="Orchestraty demo", GIT_COMMITTER_EMAIL="demo@example.invalid")
        argv = ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                "-c", "protocol.file.allow=always"] + list(args)
        try:
            proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, stdin=subprocess.DEVNULL,
                                  timeout=TIMEOUT, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DemoError(f"git {args[0]}: {type(exc).__name__}") from exc
        if proc.returncode != 0:
            raise DemoError(f"git {args[0]} failed: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
        return proc

    def _hook(self, who, event, **fields):
        """Run the hook script as Claude Code runs it, for one event of session A or B, with
        the JSON Claude Code sends on stdin. Returns what it printed, exactly."""
        sid, top = self.sessions[who], self.worktrees[who]
        payload = {"session_id": sid, "transcript_path": os.path.join(self.root, "transcripts", sid + ".jsonl"),
                   "cwd": top, "permission_mode": "default", "hook_event_name": event}
        payload.update(fields)
        try:
            proc = subprocess.run([self.python, "-S", "-I", self.state.hook_script, "claude", event],
                                  input=json.dumps(payload).encode("utf-8"), capture_output=True, cwd=top,
                                  env=self._hook_env, timeout=TIMEOUT, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DemoError(f"the hook script did not run: {type(exc).__name__}") from exc
        return proc.stdout.decode("utf-8", "replace")

    def _edit(self, who, rel, tool, data, tool_input):
        """One edit through Claude Code's hooks: PreToolUse, the change on disk, PostToolUse."""
        path = os.path.join(self.worktrees[who], rel)
        tool_input = dict(tool_input, file_path=path)
        use = "toolu_" + uuid.uuid4().hex[:24]
        before = self._hook(who, "PreToolUse", tool_name=tool, tool_input=tool_input, tool_use_id=use)
        _write(path, data)
        after = self._hook(who, "PostToolUse", tool_name=tool, tool_input=tool_input,
                           tool_response={"filePath": path, "success": True}, tool_use_id=use)
        return before + after

    def _settle(self):
        """Step the coordinator until the live map, the code graphs and the rules caught up."""
        c = self.coordinator
        c.reload_config()
        for _ in range(3):
            c.step()
            c.livemap.indexes.wait(INDEX_WAIT)
        c.step()

    # ------------------------------------------------------------------ what each step shows
    def _recorded(self):
        """The spool lines the hooks wrote since the last look, exactly."""
        lines = []
        for folder, _dirs, files in sorted(os.walk(self.state.spool)):
            for name in sorted(files):
                path = os.path.join(folder, name)
                start = self._spool_read.get(path, 0)
                try:
                    with open(path, "rb") as fh:
                        fh.seek(start)
                        chunk = fh.read()
                except OSError:
                    continue
                end = chunk.rfind(b"\n") + 1
                self._spool_read[path] = start + end
                lines += chunk[:end].decode("utf-8", "replace").splitlines()
        return "\n".join(lines) or "(nothing)"

    def _who(self, sid):
        for who, mine in self.sessions.items():
            if mine == sid:
                return f"{who} ({os.path.basename(self.worktrees[who])})"
        return sid[:8]

    def _new_notes(self):
        record = self.coordinator.record
        notes = [n for n in record.notes() if n["id"] > self._notes_seen]
        if notes:
            self._notes_seen = notes[-1]["id"]
        lines = []
        for n in notes:
            f = record.finding(n["finding_key"]) or {"rule": "?", "file": "?", "detail": {}}
            when = f"before its next edit of {f['file']}" if n["kind"] == "edit" else KINDS.get(n["kind"], n["kind"])
            latent = ", latent: it is not changing that file yet" if f["detail"].get("latent") else ""
            lines.append(f"to {self._who(n['session_id'])}, {when}: {n['state']} ({f['rule']}{latent})")
        return "\n".join(lines) or "(none)"

    def _file(self, who, rel):
        with open(os.path.join(self.worktrees[who], rel), "rb") as fh:
            return fh.read().decode("utf-8", "replace")

    def _printed(self, text):
        return text if text else "(nothing)"

    # ------------------------------------------------------------------ the scenario
    def _step_1(self):
        printed = self._hook("A", "SessionStart", source="startup") + self._hook("B", "SessionStart", source="startup")
        self._settle()
        return [("What the hooks printed", self._printed(printed)),
                ("What the hooks recorded (one spool line per event)", self._recorded(), True)]

    def _step_2(self):
        printed = self._hook("B", "UserPromptSubmit", prompt="Add a refund() that charges 2 USD, in c.py.")
        printed += self._edit("B", "c.py", "Write", C_PY, {"content": C_PY.decode()})
        self._settle()
        return [("c.py in wt-b", self._file("B", "c.py")), ("What the hooks printed", self._printed(printed)),
                ("What the hooks recorded: never the prompt or the code", self._recorded(), True)]

    def _step_3(self):
        printed = self._hook("A", "UserPromptSubmit", prompt="Make currency a keyword-only argument of charge().")
        new = A_PY.replace(A_OLD.encode(), A_NEW.encode())
        printed += self._edit("A", "a.py", "Edit", new, {"old_string": A_OLD, "new_string": A_NEW})
        self._settle()
        return [("a.py in wt-a", self._file("A", "a.py")), ("What the hooks printed", self._printed(printed)),
                ("Notes the coordinator wrote in advance, each waiting in its slot", self._new_notes()),
                ("What the hooks recorded", self._recorded(), True)]

    def _step_4(self):
        path = os.path.join(self.worktrees["B"], "c.py")
        printed = self._hook("B", "PostToolUse", tool_name="Read", tool_input={"file_path": path},
                             tool_response={"type": "text", "file": {"filePath": path}},
                             tool_use_id="toolu_" + uuid.uuid4().hex[:24])
        self._settle()
        return [("What B’s PostToolUse hook printed, exactly: what Claude reads", self._printed(printed)),
                ("What the hook recorded: it took the note", self._recorded(), True)]

    def _step_5(self):
        printed = self._hook("A", "Stop", stop_hook_active=False)
        self._settle()
        return [("What A’s Stop hook printed, exactly: what Claude reads", self._printed(printed)),
                ("What the hook recorded", self._recorded(), True)]

    def _step_6(self):
        push = self._git(self.worktrees["A"], "push", self.remote, "feat-a")
        self._settle()
        return [("git push origin feat-a, in wt-a: what git printed, exactly",
                 push.stderr.decode("utf-8", "replace").strip() or "(nothing)"),
                ("What the git hook recorded", self._recorded(), True)]

    def _step_7(self):
        new = C_PY.replace(C_OLD.encode(), C_NEW.encode())
        printed = self._edit("B", "c.py", "Edit", new, {"old_string": C_OLD, "new_string": C_NEW})
        self._settle()
        found = self.coordinator.record.finding(f"O3|a.py::charge|{self.sessions['A']}|{self.sessions['B']}|c.py")
        state = found["state"] if found else "not found"
        return [("c.py in wt-b", self._file("B", "c.py")), ("What the hooks printed", self._printed(printed)),
                ("The overlap between A and B in c.py", state),
                ("What the hooks recorded", self._recorded(), True)]

    # ------------------------------------------------------------------ for the server
    def catch_up(self):
        """Let the coordinator record what the page just wrote to the spool (a label, a claim,
        a release): in the demo nothing else steps it. While a step runs it does nothing; the
        step records it anyway."""
        if not self._busy.acquire(blocking=False):
            return
        try:
            self.coordinator.step()
        finally:
            self._busy.release()

    def next_step(self):
        """Run the next step and return the payload. Raises Busy while a step runs and Finished
        after the last; DemoError (or another exception) when the step failed."""
        if not self._busy.acquire(blocking=False):
            raise Busy("a step is running")
        try:
            n = len(self.done) + 1
            if n > len(STEPS):
                raise Finished("the scenario is done")
            try:
                outputs = getattr(self, f"_step_{n}")()
            except Exception as exc:
                self.error = f"Step {n} failed: {type(exc).__name__}: {exc}"
                raise
            self.done.append({"n": n, "outputs": [{"label": o[0], "text": o[1], "fold": len(o) > 2 and o[2]}
                                                  for o in outputs]})
            self.error = None
            return self.payload()
        finally:
            self._busy.release()

    def payload(self):
        done = {d["n"]: d for d in self.done}
        return {"schema": 1, "folder": self.root, "error": self.error,
                "next": len(self.done) + 1 if len(self.done) < len(STEPS) else None,
                "sessions": {who: {"id": sid, "worktree": self.worktrees[who]} for who, sid in self.sessions.items()},
                "steps": [{"n": i, "text": text, "done": i in done, "outputs": done[i]["outputs"] if i in done else []}
                          for i, text in enumerate(STEPS, 1)]}

    def close(self):
        """Stop the coordinator and remove the temporary folder. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        if self.coordinator is not None:
            self.coordinator.close()
        if self._heartbeat is not None:
            self._heartbeat.stop(remove=True)
        if self._lock is not None:
            self._lock.release()
        _remove_tree(self.root)
