"""Orchestraty installed from its wheel, end to end: what CI and the release workflow run on the
built wheel, on Windows, macOS and Linux, and what first_note_timing.py times.

    python scripts/smoke.py dist/orchestraty-<version>-py3-none-any.whl [--keep]

In a temporary folder it makes a clean virtual environment, installs the wheel there with pip
(--no-index: the wheel has no dependencies, so nothing is downloaded), and runs

1. orch --version;
2. orch init --print;
3. orch init --yes in a small repository with two worktrees, then two scripted Claude Code
   sessions, played through the hook entries orch init installed, run the way the agent runs
   them (exec form, the event's JSON on stdin). Session A makes currency keyword-only in
   charge() in a.py; session B, about to edit b.py, whose pay() still passes currency by
   position, hears of it before it edits. The first hook starts the coordinator;
4. orch doctor, in that repository.

The state folder, the agent's config folder, the home folder and the repository are all in the
temporary folder: nothing outside it is read or written, no real agent runs and nothing goes
over the network. The exit code is 0 when every step passed. Standard library only.
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

A_PY = "def charge(amount, currency):\n    return amount\n"
A_PY_KEYWORD_ONLY = "def charge(amount, *, currency):\n    return amount\n"
B_PY = 'from a import charge\n\n\ndef pay():\n    return charge(1, "EUR")\n'
GIT = ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
       "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "user.name=Smoke test",
       "-c", "user.email=smoke@example.invalid"]
NOTE_SECONDS = 180.0            # how long session B waits for its note at most
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SmokeError(Exception):
    """A step that did not do what it should; the message says which and what came out."""


class Steps:
    """The seconds each step took, in order."""

    def __init__(self, say=print):
        self.taken = []
        self.say = say

    def __call__(self, name):
        return _Step(self, name)


class _Step:
    def __init__(self, steps, name):
        self.steps, self.name = steps, name

    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, kind, value, tb):
        seconds = time.monotonic() - self.started
        self.steps.taken.append((self.name, seconds))
        self.steps.say(f"{'ok  ' if kind is None else 'FAIL'} {self.name} ({seconds:.1f} s)")
        return False


def run(argv, env, cwd=None, data=None, timeout=600, check=True):
    """Run a command (no shell, no console window on Windows); its CompletedProcess."""
    proc = subprocess.run([str(a) for a in argv], input=data, capture_output=True, env=env, cwd=cwd,
                          timeout=timeout, creationflags=NO_WINDOW,
                          stdin=None if data is not None else subprocess.DEVNULL)
    if check and proc.returncode != 0:
        raise SmokeError(f"{' '.join(str(a) for a in argv)} exited with {proc.returncode}:\n"
                         f"{_text(proc.stdout)}{_text(proc.stderr)}")
    return proc


def _text(data):
    return data.decode("utf-8", "replace") if data else ""


def remove_tree(path, attempts=5):
    """Delete a folder, git's read-only object files too (Windows will not delete those as they
    are), trying again for a few seconds: on Windows a process that has just stopped can still
    hold a file. Returns whether the folder is gone."""
    def writable(func, name, _exc):
        os.chmod(name, stat.S_IWRITE)
        func(name)
    for attempt in range(attempts):
        if not os.path.isdir(path):
            return True
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=writable)
            else:
                shutil.rmtree(path, onerror=lambda func, name, info: writable(func, name, info[1]))
        except OSError:
            time.sleep(0.5 * (attempt + 1))
    return not os.path.isdir(path)


# ------------------------------------------------------------------------------ the environment
def isolated_env(folder, pythonpath=None):
    """This environment with Orchestraty's state folder, the agents' config folders and the home
    folder in `folder`, and nothing of an outer git, Python, agent or test run."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("ORCH_", "GIT_", "PYTHON", "CLAUDE", "CODEX_"))}
    home = os.path.join(folder, "home")
    os.makedirs(home, exist_ok=True)
    env.update(ORCH_HOME=os.path.join(folder, "state"), CLAUDE_CONFIG_DIR=os.path.join(folder, "claude"),
               CODEX_HOME=os.path.join(folder, "codex"), HOME=home, USERPROFILE=home, PYTHONDONTWRITEBYTECODE="1",
               PIP_CONFIG_FILE=os.devnull)                       # no pip configuration of the user's
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return env


def venv_file(venv, name):
    """A program of a virtual environment: Scripts\\<name>.exe on Windows, bin/<name> elsewhere."""
    if os.name == "nt":
        return os.path.join(venv, "Scripts", name + ".exe")
    return os.path.join(venv, "bin", name)


def make_venv(venv, env):
    """A clean virtual environment, made by the Python running this script; returns its python."""
    run([sys.executable, "-m", "venv", venv], env)
    return venv_file(venv, "python")


def install_wheel(python, wheel, env):
    """Install the wheel into the environment of `python`, from the file alone (--no-index)."""
    run([python, "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir", "--disable-pip-version-check",
         "--quiet", os.path.abspath(wheel)], env)


# ------------------------------------------------------------------------------ the repository
def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def git(cwd, *args, env=None):
    return run(GIT + list(args), env or os.environ.copy(), cwd=cwd)


def filler_files(top, count):
    """`count` more Python files, in packages of 100 (an __init__.py and 99 modules), each
    module's function calling the one before it."""
    for n in range(count):
        package = os.path.join(top, "pkg%03d" % (n // 100))
        if n % 100 == 0:
            write(os.path.join(package, "__init__.py"), "")
        elif n % 100 == 1:
            write(os.path.join(package, "mod%05d.py" % n), "def work_%d(x):\n    return x + 1\n" % n)
        else:
            write(os.path.join(package, "mod%05d.py" % n),
                  "from pkg%03d.mod%05d import work_%d\n\n\ndef work_%d(x):\n    return work_%d(x) + 1\n"
                  % (n // 100, n - 1, n - 1, n, n - 1))


def make_repository(folder, env, filler=0, source=None):
    """(repo, wt-a, wt-b): a repository in folder/repo with a.py, which defines charge(amount,
    currency), and b.py, whose pay() calls charge(1, "EUR"), committed, with `filler` more Python
    files; and two worktrees of it on branches of their own. With `source`, a clone of that
    repository instead, a.py and b.py committed on top of it."""
    repo = os.path.join(folder, "repo")
    if source:
        run(GIT + ["clone", "--quiet", "--no-hardlinks", os.path.abspath(source), repo], env)
    else:
        os.makedirs(repo)
        git(repo, "init", "-q", env=env)
        filler_files(repo, filler)
    write(os.path.join(repo, "a.py"), A_PY)
    write(os.path.join(repo, "b.py"), B_PY)
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-q", "-m", "charge, and pay that calls it", env=env)
    trees = []
    for name in ("a", "b"):
        tree = os.path.join(folder, "wt-" + name)
        git(repo, "worktree", "add", "-q", "-b", "smoke-" + name, tree, env=env)
        trees.append(tree)
    return repo, trees[0], trees[1]


# ------------------------------------------------------------------------------ the sessions
def hook_commands(settings_path):
    """{event: [command, *args]}: the hook entries orch init installed for Claude Code (exec form)."""
    with open(settings_path, "r", encoding="utf-8") as fh:
        settings = json.load(fh)
    found = {}
    for event, entries in (settings.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks") or []:
                args = [str(a) for a in hook.get("args") or []]
                if "-I" in args and any(os.path.basename(a) == "hook.py" for a in args) and "Wake" not in args:
                    found.setdefault(event, [hook["command"]] + args)
    return found


def note_of(output):
    """The note a hook handed over (Claude Code's additionalContext), or ""."""
    try:
        data = json.loads(output.decode("utf-8")) if output.strip() else {}
    except ValueError as exc:
        raise SmokeError(f"the hook printed something that is not JSON: {output[:300]!r}") from exc
    return str((data.get("hookSpecificOutput") or {}).get("additionalContext") or "")


class Session:
    """One scripted Claude Code session: its events, run through the installed hook entries."""

    def __init__(self, sid, cwd, commands, env):
        self.sid, self.cwd, self.commands, self.env = sid, cwd, commands, env
        self.calls = 0

    def event(self, name, **fields):
        if name not in self.commands:
            raise SmokeError(f"orch init installed no hook for {name}")
        self.calls += 1
        payload = {"session_id": self.sid, "transcript_path": os.path.join(self.cwd, ".transcript.jsonl"),
                   "cwd": self.cwd, "hook_event_name": name}
        payload.update(fields)
        proc = run(self.commands[name], self.env, cwd=self.cwd, data=json.dumps(payload).encode("utf-8"), timeout=60,
                   check=False)
        if proc.returncode != 0 or proc.stderr:
            raise SmokeError(f"the {name} hook exited with {proc.returncode}: {_text(proc.stderr)[:500]}")
        return proc.stdout

    def _edit(self, event, path, **fields):
        return self.event(event, tool_name="Edit", tool_use_id=f"toolu_{self.sid}_{self.calls}",
                          tool_input={"file_path": path, "old_string": "x", "new_string": "y"}, **fields)

    def start(self):
        return self.event("SessionStart", source="startup")

    def before_edit(self, path):
        return note_of(self._edit("PreToolUse", path))

    def after_edit(self, path):
        return self._edit("PostToolUse", path, tool_response={"filePath": path, "success": True})

    def end(self):
        return self.event("SessionEnd", reason="other")


def wait_for_note(session, path, seconds):
    """Session's PreToolUse for `path`, again and again, until it hands over a note ("" if none
    came in time): a real agent goes on working and hears with a later call."""
    deadline = time.monotonic() + seconds
    while True:
        note = session.before_edit(path)
        if note or time.monotonic() > deadline:
            return note
        time.sleep(0.25)


# ------------------------------------------------------------------------------ the smoke test
def smoke(orch, folder, env, steps=None, wait=NOTE_SECONDS):
    """Steps 1 to 4 of the module's docstring, with `orch` the command to run Orchestraty as
    ([path of orch], or [python, "-m", "orchestraty"]) and `env` from isolated_env(folder).
    Raises SmokeError at the first step that fails; returns the note session B heard."""
    steps = steps or Steps()
    with steps("orch --version"):
        out = _text(run(orch + ["--version"], env).stdout).strip()
        if not out.startswith("orch "):
            raise SmokeError(f"orch --version printed {out!r}")
        steps.say("     " + out)
    with steps("orch init --print"):
        printed = json.loads(_text(run(orch + ["init", "--print"], env).stdout))
        if "PreToolUse" not in printed.get("hooks", {}):
            raise SmokeError(f"orch init --print printed no PreToolUse entry: {printed}")
    with steps("a repository with two worktrees"):
        repo, wt_a, wt_b = make_repository(folder, env)
    note = play(orch, repo, wt_a, wt_b, env, steps, wait)
    with steps("orch doctor"):
        proc = run(orch + ["doctor"], env, cwd=repo, check=False)
        for line in _text(proc.stdout).splitlines():
            steps.say("     " + line)
        if proc.returncode != 0:
            raise SmokeError(f"orch doctor found problems:\n{_text(proc.stdout)}{_text(proc.stderr)}")
    return note


def play(orch, repo, wt_a, wt_b, env, steps, wait=NOTE_SECONDS):
    """orch init --yes in the repository, then the two sessions; returns B's note. The
    coordinator the first hook starts is stopped at the end."""
    with steps("orch init --yes"):
        run(orch + ["init", "--yes", "--no-git-hooks"], env, cwd=repo)
        commands = hook_commands(os.path.join(env["CLAUDE_CONFIG_DIR"], "settings.json"))
    a = Session("smoke-a", wt_a, commands, env)
    b = Session("smoke-b", wt_b, commands, env)
    try:
        with steps("two sessions start (the first hook starts the coordinator)"):
            a.start()
            b.start()
        with steps("session A changes the contract of charge in a.py"):
            a_py = os.path.join(wt_a, "a.py")
            a.before_edit(a_py)
            write(a_py, A_PY_KEYWORD_ONLY)
            a.after_edit(a_py)
        with steps("session B hears of it before it edits b.py"):
            note = wait_for_note(b, os.path.join(wt_b, "b.py"), wait)
            if "charge" not in note or "a.py" not in note:
                raise SmokeError(f"session B heard no note about charge in a.py within {wait:.0f} s: {note!r}")
            steps.say("     B heard: " + note[:300])
        a.end()
        b.end()
    finally:
        run(orch + ["coordinator", "stop"], env, check=False, timeout=120)
    return note


def main(argv=None):
    parser = argparse.ArgumentParser(description="The smoke test of a built Orchestraty wheel.")
    parser.add_argument("wheel", help="the wheel to install: dist/orchestraty-<version>-py3-none-any.whl")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder, and say where it is")
    args = parser.parse_args(argv)
    if not os.path.isfile(args.wheel):
        parser.error(f"no such wheel: {args.wheel}")
    folder = os.path.realpath(tempfile.mkdtemp(prefix="orch-smoke-"))
    steps = Steps()
    try:
        env = isolated_env(folder)
        with steps("a clean virtual environment"):
            python = make_venv(os.path.join(folder, "venv"), env)
        with steps("pip install " + os.path.basename(args.wheel)):
            install_wheel(python, args.wheel, env)
        smoke([venv_file(os.path.join(folder, "venv"), "orch")], folder, env, steps)
    except SmokeError as exc:
        print(f"\nThe smoke test failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"Kept: {folder}")
        elif not remove_tree(folder):
            print(f"Could not delete all of {folder}.", file=sys.stderr)
    print(f"The smoke test passed in {sum(s for _, s in steps.taken):.1f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
