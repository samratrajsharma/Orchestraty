"""An ordinary day with Orchestraty, from its built wheel: what a person does in a day, not one
feature at a time. CI and the release workflow run it beside the smoke test, on the same wheel.

    python scripts/real_day.py dist/orchestraty-<version>-py3-none-any.whl [--keep]

In a temporary folder, with its own home folder, agents' config folders and state folder:

1. a clean virtual environment, with the wheel installed (no network);
2. orch init --yes, run from a folder that is no project: one init for the computer, every project
   followed;
3. a git repository with many worktrees (as the Claude desktop app keeps them), and a project
   folder without git;
4. four sessions start: in the repository, in two of its worktrees and in the folder. Each shows
   in orch status within SHOW_SECONDS;
5. in the folder, a session breaks a function that another file calls: the caller left behind is
   found within FIND_SECONDS, and nothing is written into the folder;
6. orch ui serves; a second orch ui, from another folder, opens the one already running;
7. an upgrade while the sessions run: the installed package's version changes, and the running
   coordinator gives way to the new one within UPGRADE_SECONDS, with no command run.

Nothing outside the temporary folder is read or written, and no real agent runs. The exit code
is 0 when every step passed. Standard library only.
"""
import argparse
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke  # noqa: E402  (the smoke test's helpers: the same sandbox, the same scripted sessions)
from smoke import (A_PY, A_PY_KEYWORD_ONLY, B_PY, Session, SmokeError, Steps, git, hook_commands,  # noqa: E402
                   install_wheel, isolated_env, make_repository, make_venv, remove_tree, run, venv_file, write)

WORKTREES = 12                   # worktrees of the repository, the two the smoke test makes included
SHOW_SECONDS = 15.0              # from a session's first event to orch status showing it
FIND_SECONDS = 90.0              # from the breaking edit to the caller left behind
UPGRADE_SECONDS = 60.0           # from the upgrade to the new coordinator running


def status(orch, env):
    return json.loads(run(orch + ["status", "--json"], env, timeout=60).stdout.decode("utf-8"))


def wait(what, seconds, check):
    """Call check() until it returns something true; that, or SmokeError after `seconds`."""
    deadline = time.monotonic() + seconds
    while True:
        found = check()
        if found:
            return found
        if time.monotonic() > deadline:
            raise SmokeError(f"{what}: not within {seconds:.0f} s")
        time.sleep(0.25)


def sessions_shown(data):
    return {s["id"]: (r["name"], r.get("kind")) for r in data["repositories"] for s in r["sessions"]}


def listing(folder):
    return sorted(os.path.relpath(os.path.join(top, name), folder)
                  for top, _, names in os.walk(folder) for name in names)


def read_link(proc, seconds=60.0):
    """The link orch ui prints first, read from its output without blocking past `seconds`."""
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in iter(proc.stdout.readline, b"")], daemon=True).start()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.5).decode("utf-8", "replace")
        except queue.Empty:
            continue
        found = re.search(r"http://127\.0\.0\.1:(\d+)/\?token=\S+", line)
        if found:
            return int(found.group(1))
    raise SmokeError("orch ui printed no link")


def day(orch, python, folder, env, steps):
    desk = os.path.join(folder, "desk")
    os.makedirs(desk)
    with steps("orch init --yes, from a folder that is no project"):
        out = run(orch + ["init", "--yes", "--no-git-hooks"], env, cwd=desk).stdout.decode("utf-8")
        if "Following every project" not in out:
            raise SmokeError(f"orch init did not turn on following every project:\n{out}")
        commands = hook_commands(os.path.join(env["CLAUDE_CONFIG_DIR"], "settings.json"))
    with steps(f"a git repository with {WORKTREES} worktrees, and a folder without git"):
        repo, wt_a, wt_b = make_repository(folder, env)
        for n in range(WORKTREES - 2):
            git(repo, "worktree", "add", "-q", "-b", f"day-{n}", os.path.join(folder, f"wt-{n}"), env=env)
        notes = os.path.join(folder, "projects", "notes")
        write(os.path.join(notes, "a.py"), A_PY)
        write(os.path.join(notes, "b.py"), B_PY)
        before = listing(notes)
    people = [Session("day-repo", repo, commands, env), Session("day-a", wt_a, commands, env),
              Session("day-b", wt_b, commands, env), Session("day-notes", notes, commands, env)]
    try:
        with steps(f"four sessions start, each shown within {SHOW_SECONDS:.0f} s"):
            for s in people:
                started = time.monotonic()
                s.start()
                wait(f"session {s.sid} in orch status", SHOW_SECONDS, lambda: s.sid in sessions_shown(status(orch, env)))
                steps.say(f"     {s.sid} shown after {time.monotonic() - started:.1f} s")
            kinds = {name: kind for name, kind in sessions_shown(status(orch, env)).values()}
            if kinds != {"repo": "git", "notes": "folder"}:
                raise SmokeError(f"the projects shown are {kinds}")
        with steps("the folder's caller left behind is found, and the folder is left as it was"):
            a_py = os.path.join(notes, "a.py")
            people[3].before_edit(a_py)
            write(a_py, A_PY_KEYWORD_ONLY)
            people[3].after_edit(a_py)

            def left_behind():
                for r in status(orch, env)["repositories"]:
                    if r["name"] == "notes":
                        return [o for o in r["overlaps"] if o.get("rule") == "O6"]
                return []
            found = wait("the caller left behind in the folder", FIND_SECONDS, left_behind)
            steps.say("     " + found[0].get("summary", found[0].get("rule", "")))
            if listing(notes) != before:
                raise SmokeError(f"files were added to the folder: {sorted(set(listing(notes)) - set(before))}")
        with steps("orch ui serves, and a second orch ui opens the one running"):
            ui = subprocess.Popen([str(a) for a in orch + ["ui", "--no-browser"]], env=env, cwd=desk,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  creationflags=smoke.NO_WINDOW)
            try:
                port = read_link(ui)
                steps.say(f"     on port {port}")
                second = run(orch + ["ui", "--no-browser"], env, cwd=notes, timeout=60).stdout.decode("utf-8")
                if "already running" not in second or f":{port}/" not in second:
                    raise SmokeError(f"the second orch ui did not open the running one:\n{second}")
            finally:
                ui.terminate()
                ui.wait(30)
        with steps(f"an upgrade: the new coordinator runs within {UPGRADE_SECONDS:.0f} s, with no command run"):
            init = run([python, "-c", "import orchestraty; print(orchestraty.__file__)"], env).stdout.decode().strip()
            with open(init, "r", encoding="utf-8") as fh:
                text = fh.read()
            old = re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)
            new = old + ".post1"
            with open(init, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text.replace(f'__version__ = "{old}"', f'__version__ = "{new}"', 1))

            def upgraded():
                people[0].event("UserPromptSubmit")               # the person goes on working
                return status(orch, env)["coordinator"].get("version") == new
            wait(f"the coordinator running {new}", UPGRADE_SECONDS, upgraded)
            steps.say(f"     {old} gave way to {new}")
        for s in people:
            s.end()
    finally:
        run(orch + ["coordinator", "stop"], env, check=False, timeout=120)


def main(argv=None):
    parser = argparse.ArgumentParser(description="An ordinary day with a built Orchestraty wheel.")
    parser.add_argument("wheel", help="the wheel to install: dist/orchestraty-<version>-py3-none-any.whl")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder, and say where it is")
    args = parser.parse_args(argv)
    if not os.path.isfile(args.wheel):
        parser.error(f"no such wheel: {args.wheel}")
    folder = os.path.realpath(tempfile.mkdtemp(prefix="orch-day-"))
    steps = Steps()
    try:
        env = isolated_env(folder)
        with steps("a clean virtual environment, with the wheel"):
            venv = os.path.join(folder, "venv")
            python = make_venv(venv, env)
            install_wheel(python, args.wheel, env)
        day([venv_file(venv, "orch")], python, folder, env, steps)
    except SmokeError as exc:
        print(f"\nThe day did not go as it should: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"Kept: {folder}")
        elif not remove_tree(folder):
            print(f"Could not delete all of {folder}.", file=sys.stderr)
    print(f"The day went as it should, in {sum(s for _, s in steps.taken):.1f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
