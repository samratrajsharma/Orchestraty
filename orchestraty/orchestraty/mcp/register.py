"""``orch mcp --config <agent>``: how to register the MCP server with an agent. It prints, and
writes nothing: writing an agent's own config is the plugin's job.

The command is this interpreter running this package (``python -m orchestraty mcp``), with the
import path ``orch init`` gives the coordinator when the package is not installed where that
interpreter finds it on its own. A server registered once for the user serves every repository:
each session starts its own, in its workspace folder.
"""
import json
import os
import shlex
import subprocess

from orchestraty.capture import install

AGENTS = ("claude", "codex", "antigravity")
NAME = "orchestraty"
AGENTS_LINE = ("Before you edit a file that other sessions may also be changing, call the orchestraty MCP tool "
               "check_before_edit with its path, and follow what it says.")
UNTESTED = "Built; not yet tested with the real agent."


def _importable(python):
    """Whether `python` imports this package and the engine without help from PYTHONPATH. -I
    ignores PYTHONDONTWRITEBYTECODE too, so -B: the check leaves no bytecode beside the code."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    try:
        proc = subprocess.run([python, "-I", "-B", "-c", "import orchestraty, orch_engine"], env=env, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def command():
    """(argv, env) that start the server."""
    python, path = install.launch_lines()
    return [python, "-m", "orchestraty", "mcp"], ({} if _importable(python) else {"PYTHONPATH": path})


def _shell(argv):
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _indent(text):
    return "\n".join("    " + line if line else "" for line in text.splitlines())


def text(agent, argv=None, env=None):
    """What ``orch mcp --config <agent>`` prints."""
    if argv is None:
        argv, env = command()
    env = env or {}
    python, args = argv[0], argv[1:]
    if agent == "claude":
        add = ["claude", "mcp", "add", "--scope", "user", NAME]
        for k, v in env.items():
            add += ["--env", f"{k}={v}"]
        parts = ["Claude Code: run this once. It registers the server for every project of yours (--scope user); "
                 "each session starts its own, in its project folder.", "", _indent(_shell(add + ["--"] + argv))]
        where = ["", "Claude Code reads CLAUDE.md rather than AGENTS.md: put the line there, or a line @AGENTS.md that "
                     "imports the file."]
    elif agent == "codex":
        add = ["codex", "mcp", "add", NAME]
        for k, v in env.items():
            add += ["--env", f"{k}={v}"]
        table = [f"[mcp_servers.{NAME}]", f"command = {json.dumps(python)}",
                 "args = [" + ", ".join(json.dumps(a) for a in args) + "]"]
        if env:
            table.append("env = { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + " }")
        parts = ["Codex: run this once,", "", _indent(_shell(add + ["--"] + argv)), "",
                 "or add this table to ~/.codex/config.toml (the command line and the IDE extension share it):", "",
                 _indent("\n".join(table))]
        where = []
    elif agent == "antigravity":
        entry = [f'      "command": {json.dumps(python)},',
                 '      "args": [' + ", ".join(json.dumps(a) for a in args) + "]"]
        if env:
            entry[-1] += ","
            entry.append('      "env": {' + ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in env.items()) + "}")
        snippet = ["{", '  "mcpServers": {', f'    "{NAME}": {{'] + entry + ["    }", "  }", "}"]
        parts = ["Antigravity: in the agent panel, open ... > MCP Servers > Manage MCP Servers > View raw config, add "
                 "the server to mcp_config.json under mcpServers, and refresh the list in that panel:", "",
                 _indent("\n".join(snippet))]
        where = []
    else:
        raise ValueError(agent)
    parts += ["", "If the agent does not start the server in your workspace folder, the tools that read take the "
                  "worktree from the absolute paths you pass them, or from their session argument; declare, claim "
                  "and release need the server started there (--folder).",
              "", "Then, to have the agent check before it edits, add this line to your repository's AGENTS.md (or "
                  "to the file your agent reads its project instructions from):", "", _indent(AGENTS_LINE)]
    parts += where + ["", UNTESTED, "orch mcp --config writes nothing."]
    return "\n".join(parts) + "\n"
