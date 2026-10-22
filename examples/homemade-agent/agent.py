"""A homemade agent that uses Orchestraty's two-call helper. It has no model: it plays two
sessions of one agent in two worktrees of a small repository, so the notes it gets can be read,
and checked.

    python agent.py FOLDER

FOLDER must be new or empty. The agent makes a repository there (``repo``), with a.py, which
defines charge(amount, currency), and b.py, whose pay() calls charge(1, "EUR"), and two
worktrees of it, ``wt-a`` and ``wt-b``. It watches the repository, as ``orch watch`` does, and
stops watching it at the end. Orchestraty's hook script must be installed: run ``orch init``
once. The hook starts the coordinator when it is not running.

Session A, in wt-a, makes charge's currency keyword-only. Session B, in wt-b, is about to edit
pay(), which still passes currency by position: before B writes b.py, the helper hands it
Orchestraty's note about A's change. A, at the end of its turn, hears that pay() is left
behind. Then B passes currency by name. The exit code is 0 when B heard before it wrote.
"""
import os
import subprocess
import sys
import time

from orchestraty import config
from orchestraty.helper import Orchestraty
from orchestraty.paths import State

A_PY = "def charge(amount, currency):\n    return amount\n"
A_PY_KEYWORD_ONLY = "def charge(amount, *, currency):\n    return amount\n"
B_PY = 'from a import charge\n\n\ndef pay():\n    return charge(1, "EUR")\n'
B_PY_BY_NAME = 'from a import charge\n\n\ndef pay():\n    return charge(1, currency="EUR")\n'
GIT = ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
       "-c", "user.name=Homemade agent", "-c", "user.email=agent@example.invalid"]
WAIT_SECONDS = 60.0


def git(cwd, *args):
    subprocess.run(GIT + list(args), cwd=cwd, check=True, capture_output=True, stdin=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def write(folder, name, text):
    with open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_repository(folder):
    """The repository and its two worktrees: (repo, wt-a, wt-b)."""
    repo = os.path.join(folder, "repo")
    os.makedirs(repo)
    write(repo, "a.py", A_PY)
    write(repo, "b.py", B_PY)
    git(repo, "init", "-q")
    git(repo, "add", "a.py", "b.py")
    git(repo, "commit", "-q", "-m", "charge, and pay that calls it")
    trees = []
    for name in ("a", "b"):
        tree = os.path.join(folder, "wt-" + name)
        git(repo, "worktree", "add", "-q", "-b", "feat-" + name, tree)
        trees.append(tree)
    return repo, trees[0], trees[1]


def wait_for(call):
    """Repeat `call` until it hands over a note. A real agent would carry on with its work and
    hear later; this one waits for the coordinator to catch up."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        note = call()
        if note:
            return note
        time.sleep(0.25)
    return None


def say(session, when, note):
    print(f"{session}, {when}, " + (f"hears:\n    {note}" if note else "hears nothing."), flush=True)


def play(wt_a, wt_b):
    a = Orchestraty(agent="homemade", cwd=wt_a)
    b = Orchestraty(agent="homemade", cwd=wt_b)
    a.start()
    b.start()
    say("A", "before writing a.py", a.before_write("a.py"))
    write(wt_a, "a.py", A_PY_KEYWORD_ONLY)
    say("A", "after making currency keyword-only in a.py", a.after_write("a.py"))
    heard = wait_for(lambda: b.before_write("b.py"))
    say("B", "before writing b.py", heard)
    say("A", "at the end of its turn", wait_for(a.end_turn))
    write(wt_b, "b.py", B_PY_BY_NAME)
    say("B", "after passing currency by name in b.py", b.after_write("b.py"))
    a.end()
    b.end()
    return 0 if heard else 1


def main(argv):
    if len(argv) != 1:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    folder = os.path.abspath(argv[0])
    if os.path.exists(folder) and os.listdir(folder):
        print(f"{folder} is not empty.", file=sys.stderr)
        return 2
    state = State()
    if not os.path.isfile(state.hook_script):
        print(f"Orchestraty's hook script is not in {state.hook_script}: run orch init once.", file=sys.stderr)
        return 2
    repo, wt_a, wt_b = make_repository(folder)
    common_dir = os.path.join(repo, ".git")
    config.watch(state, common_dir)
    try:
        return play(wt_a, wt_b)
    finally:
        config.unwatch(state, common_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
