"""Sets up the Antigravity capture leg: a scratch repository with two worktrees whose workspace
hooks (.agents/hooks.json) record every hook event into the spool, and the prompts to run.

Status: throwaway (Phase 0). Not part of the product.

    python capture_agy.py [--root <phase-0 folder>] [--python <absolute python>] [--fresh]

Antigravity reads hooks from <workspace>/.agents/hooks.json (and from ~/.gemini/config, which
this tool never touches), so the hooks apply only to the two scratch worktrees. It writes:
  * repos/agy-capture/repo: a git repository with a small Python project, its first commit,
    and the git-hook chain that records commits (capture_githooks);
  * repos/agy-capture/wt-a and wt-b: worktrees on branches agy-a and agy-b, each with
    .agents/hooks.json (kept out of git through .git/info/exclude);
  * repos/agy-capture/PROMPTS.md: what to run in Antigravity;
  * shim/capture_shim.py and the folder spool/agy/.
Each hook runs `<python> -S -I <shim> <spool> agy <event>`; the shim prints the answer
Antigravity waits for (see capture_shim.py). Paths with spaces or quotes are refused, so the
command line means the same in any shell.
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys

import capture_githooks
import capture_hooks

EVENTS = ["SessionStart", "PreInvocation", "PreToolUse", "PostToolUse", "PostInvocation", "Stop"]
TOOL_EVENTS = {"PreToolUse", "PostToolUse"}
TIMEOUT_SECONDS = capture_hooks.TIMEOUT_SECONDS
HOOK_NAME = "orch-capture"
LEG = os.path.join("repos", "agy-capture")
GIT = ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
       "-c", "maintenance.auto=false", "-c", "user.name=Capture", "-c", "user.email=capture@example.com"]

PROJECT = {
    "README.md": "# Detector\n\nA small example project: data loading, losses, a model and an API.\n",
    "data.py": ('"""Data loading and preprocessing."""\n\n\n'
                'def load_image(path):\n    """Read an image file from disk into a raw array."""\n'
                '    return {"path": path, "pixels": [0, 1, 2]}\n\n\n'
                'def preprocess(image):\n    """Normalize and resize an image into a model-ready tensor."""\n'
                '    return {"tensor": image["pixels"], "shape": (3, 224, 224)}\n'),
    "losses.py": ('"""Loss functions for training."""\n\n\n'
                  'def cross_entropy(preds, targets):\n    """Standard cross-entropy loss."""\n    return 0.0\n\n\n'
                  'def focal_loss(preds, targets, gamma=2.0):\n    """Focal loss, for class imbalance."""\n'
                  '    return 0.0\n'),
    "model.py": ('"""The model."""\nfrom data import preprocess\n\n\n'
                 'class Detector:\n    def __init__(self, threshold=0.5):\n        self.threshold = threshold\n\n'
                 '    def predict(self, image):\n        tensor = preprocess(image)\n'
                 '        return {"boxes": [], "shape": tensor["shape"]}\n'),
    "api.py": ('"""A tiny API around the detector."""\nfrom data import load_image\nfrom model import Detector\n\n\n'
               'def detect(path):\n    return Detector().predict(load_image(path))\n'),
    "test_project.py": ('from api import detect\nfrom losses import cross_entropy\n\n\n'
                        'def test_detect_returns_boxes():\n    assert detect("x.png")["boxes"] == []\n\n\n'
                        'def test_cross_entropy_is_a_number():\n    assert cross_entropy([], []) == 0.0\n'),
}

PROMPTS = {
    "wt-a": [
        "In data.py, add a function normalize(values) that scales a list of numbers to the range 0 to 1. "
        "Then commit the change with a short message.",
        "Add a test for normalize in test_project.py, run the tests with python -m pytest -q, and commit.",
        "In losses.py, make cross_entropy compute the mean of -log(p) for the target classes. Commit it.",
        "Give Detector in model.py a method describe() that returns its threshold as text. Commit.",
        "Rename the parameter path of load_image in data.py to image_path, update its callers, and commit.",
        "Add a short usage section to README.md and commit it.",
        "Run the tests. If any fail, fix them, then commit.",
        "Add type hints to the functions in data.py and commit.",
        "Create a file config.py with a DEFAULT_THRESHOLD constant set to 0.5 and use it in model.py. Commit.",
        "List the files in the project and summarise in two sentences what each does. Change nothing.",
    ],
    "wt-b": [
        "In api.py, add a function detect_many(paths) that runs detect on each path. Commit the change.",
        "Add a test for detect_many in test_project.py, run the tests, and commit.",
        "In losses.py, add a function mse(preds, targets) for the mean squared error. Commit it.",
        "Give focal_loss in losses.py a real implementation for lists of probabilities. Commit.",
        "Add a docstring to every function in api.py that lacks one and commit.",
        "Make Detector.predict in model.py keep only boxes above the threshold (boxes are empty for now). Commit.",
        "Add a .gitignore for Python and commit it.",
        "Run the tests. If any fail, fix them, then commit.",
        "Add a function load_images(paths) to data.py that calls load_image for each path. Commit.",
        "Read model.py and explain in two sentences how predict works. Change nothing.",
    ],
}


class Refused(capture_hooks.Refused):
    pass


def _plain(path):
    if any(c in path for c in " \"'`$%&|<>^!"):
        raise Refused("a path holds a space or a character a shell treats specially: " + path)
    return path


def hook_command(python, shim, spool, event):
    return " ".join([_plain(python), "-S", "-I", _plain(shim), _plain(spool), "agy", event])


def hooks_json(python, shim, spool):
    """The workspace hooks.json: one named hook set with every event."""
    hooks = {}
    for event in EVENTS:
        handler = {"type": "command", "command": hook_command(python, shim, spool, event), "timeout": TIMEOUT_SECONDS}
        hooks[event] = [{"matcher": "*", "hooks": [handler]}] if event in TOOL_EVENTS else [handler]
    return {HOOK_NAME: dict({"enabled": True}, **hooks)}


def prompts_text(leg):
    lines = ["# Antigravity capture leg: what to run", "",
             "Open each folder below in Antigravity (File > Open Folder), signed in as usual. Start a NEW",
             "conversation for every prompt, paste the prompt, let it finish, and approve what it asks",
             "(commands such as git commit or python -m pytest). Any model is fine; keep the same one.",
             "", "Run the first prompt of wt-a alone first, then tell the build session, which checks that",
             "the hooks fired before you do the rest. Afterwards close Antigravity's windows for both folders.", ""]
    for wt, items in PROMPTS.items():
        lines.append("## " + os.path.join(leg, wt))
        lines.append("")
        lines += ["%d. %s" % (i, p) for i, p in enumerate(items, 1)]
        lines.append("")
    return "\n".join(lines)


def _git(cwd, *args):
    proc = subprocess.run(GIT + list(args), cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if proc.returncode:
        raise Refused("git %s failed: %s" % (args[0], proc.stderr.strip()[:300]))
    return proc.stdout.strip()


def _remove(path):
    def unlock(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if os.path.exists(path):
        shutil.rmtree(path, onerror=unlock)


def setup(root, python, fresh=False):
    if not os.path.isabs(python) or not os.path.isfile(python):
        raise Refused("--python must be the absolute path of an existing interpreter")
    root = os.path.abspath(root)
    if os.path.basename(root) != capture_hooks.ROOT_NAME:
        raise Refused("the phase-0 folder must be named " + capture_hooks.ROOT_NAME)
    leg = os.path.join(root, LEG)
    if os.path.exists(leg):
        if not fresh:
            raise Refused(leg + " exists: pass --fresh to start again")
        _remove(leg)
    shim = capture_hooks.checked_target(root, os.path.join("shim", "capture_shim.py"))
    spool = capture_hooks.checked_target(root, "spool")
    for path in (python, shim, spool, leg):
        _plain(path)
    repo = os.path.join(leg, "repo")
    os.makedirs(repo)
    for name, text in PROJECT.items():
        with open(os.path.join(repo, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    with open(os.path.join(repo, ".git", "info", "exclude"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(".agents/\n")
    os.makedirs(os.path.join(spool, "agy"), exist_ok=True)
    os.makedirs(os.path.dirname(shim), exist_ok=True)
    with open(capture_hooks.SHIM, "rb") as fh:
        capture_hooks._write_atomic(shim, fh.read())
    data = (json.dumps(hooks_json(python, shim, spool), indent=2) + "\n").encode("utf-8")
    for wt in PROMPTS:
        path = os.path.join(leg, wt)
        _git(repo, "worktree", "add", "-q", "-b", "agy-" + wt[-1], path)
        os.makedirs(os.path.join(path, ".agents"))
        capture_hooks._write_atomic(os.path.join(path, ".agents", "hooks.json"), data)
    capture_githooks.install(root, repo, python)
    with open(os.path.join(leg, "PROMPTS.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(prompts_text(leg) + "\n")
    return leg


def main(argv=None):
    p = argparse.ArgumentParser(description="Set up the Antigravity capture leg.")
    p.add_argument("--root", default=capture_hooks.default_root())
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--fresh", action="store_true", help="remove an earlier set-up first")
    args = p.parse_args(argv)
    try:
        leg = setup(args.root, os.path.abspath(args.python), args.fresh)
    except capture_hooks.Refused as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 2
    print("set up " + leg)
    print("next: follow " + os.path.join(leg, "PROMPTS.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
