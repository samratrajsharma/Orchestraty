"""``orch resume``: start an agent on a handoff, in a new worktree, in the user's own terminal.

The one place Orchestraty starts an agent, and only when the user runs it: it makes a worktree
on a new branch from the packet's head commit, then runs the agent's own command-line tool
there, in the foreground, with the packet as its first message: the documented way each tool
takes an opening prompt (``claude "<prompt>"``, ``codex "<prompt>"``, ``agy --prompt-interactive
"<prompt>"``); a command of the user's own gets the packet on its standard input. It prints what
will run and asks first. It passes nothing else: the agent keeps its own permissions and model
settings (one agent's permissions translated into another's would be unsafe), and nothing runs
in the background.

On Windows a tool installed as a ``.cmd`` or ``.bat`` file is run through cmd.exe, which cannot
take a line break inside an argument, so its first message is one line naming the packet's file.
A packet longer than ``MAX_ARGUMENT`` characters is cut, ending with where the whole of it is.
"""
import os
import shutil
import subprocess

from orchestraty import gitio

AGENTS = {"claude": ("claude", []), "codex": ("codex", []), "antigravity": ("agy", ["--prompt-interactive"])}
MAX_ARGUMENT = 24000                 # well under Windows' 32,767 characters for a whole command line


class Refused(Exception):
    """resume cannot go ahead; the message says why."""


def which(name):
    """The agent's tool on PATH, or None (tests stand in their own)."""
    return shutil.which(name)


def _pointer(md_path):
    return f"Take over the work described in the handoff packet {md_path}: read that file first."


def opening(packet_text, md_path, exe):
    """The first message the agent is given."""
    if os.name == "nt" and exe and exe.lower().endswith((".cmd", ".bat")):
        return _pointer(md_path)
    if len(packet_text) <= MAX_ARGUMENT:
        return packet_text
    tail = f"\n\n(The packet is cut here; the whole of it is in {md_path}.)\n"
    return packet_text[:MAX_ARGUMENT - len(tail)] + tail


def _branch_free(cwd, branch):
    try:
        gitio.run(["rev-parse", "--verify", "--quiet", "refs/heads/" + branch], cwd)
    except gitio.GitError:
        return True
    return False


def plan(packet, md_path, packet_text, agent, worktree=None):
    """What resume will do: {"cwd", "worktree", "branch", "head", "git": argv, "agent": argv or None,
    "command": a command line or None, "stdin": bool, "missing": the tool not found, or ""}."""
    s, head = packet["session"], packet["head"]
    if not head:
        raise Refused("the packet has no head commit to start from (the worktree had no commit)")
    common = packet["repository"]["common_dir"]
    cwd = s["worktree"] if s["worktree"] and os.path.isdir(s["worktree"]) else os.path.dirname(common)
    if not os.path.isdir(cwd):
        raise Refused(f"the repository is gone: neither {s['worktree']} nor {os.path.dirname(common)} exists")
    branch = f"{s['branch']}-{packet['id']}" if s["branch"] else f"handoff-{packet['id']}"
    if not _branch_free(cwd, branch):
        raise Refused(f"the branch {branch} exists already: delete it, or resume from a new packet")
    if worktree:
        path = os.path.abspath(worktree)
    else:
        base = s["worktree"] or os.path.dirname(common)
        path = os.path.join(os.path.dirname(os.path.abspath(base)), f"{os.path.basename(base)}-{packet['id']}")
    if os.path.exists(path):
        raise Refused(f"{path} exists already: pass --worktree with a folder that does not")
    out = {"cwd": cwd, "worktree": path, "branch": branch, "head": head,
           "git": ["git", "worktree", "add", "-b", branch, path, head], "agent": None, "command": None,
           "stdin": False, "missing": ""}
    if agent in AGENTS:
        name, flags = AGENTS[agent]
        exe = which(name)
        out["missing"] = "" if exe else name
        out["agent"] = [exe or name] + flags + [opening(packet_text, md_path, exe)]
    else:
        out["command"], out["stdin"] = agent, True
    return out


def describe(p, md_path, packet_chars):
    """The lines that say exactly what will run."""
    lines = [f"In {p['cwd']}:", "  " + " ".join(_quote(a) for a in p["git"]), f"Then, in {p['worktree']}:"]
    if p["command"] is not None:
        lines.append(f"  {p['command']}")
        lines.append(f"  with the packet ({packet_chars} characters, {md_path}) on its standard input")
    else:
        message = p["agent"][-1]
        shown = (f"<the packet: {len(message)} characters of {md_path}>" if "\n" in message or len(message) > 200
                 else _quote(message))
        lines.append("  " + " ".join(_quote(a) for a in p["agent"][:-1]) + " " + shown)
        lines.append("  in the foreground, in this terminal. Nothing is passed but the first message: the agent "
                     "keeps its own permissions and model settings.")
        if p["missing"]:
            lines.append(f"  ({p['missing']} is not on PATH here.)")
    return lines


def _quote(arg):
    return arg if arg and all(c.isalnum() or c in "-_./:\\=@+," for c in arg) else '"' + arg.replace('"', '\\"') + '"'


def make_worktree(p):
    """git worktree add, as planned. Raises Refused when git fails."""
    try:
        gitio.run(p["git"][1:], p["cwd"], timeout=300)
    except gitio.GitError as exc:
        raise Refused(f"git could not make the worktree: {exc}") from None


def start(p, packet_text):
    """Run the agent in the foreground in the new worktree; returns its exit code."""
    if p["command"] is not None:
        return subprocess.run(p["command"], shell=True, cwd=p["worktree"],
                              input=packet_text.encode("utf-8")).returncode
    return subprocess.run(p["agent"], cwd=p["worktree"]).returncode
