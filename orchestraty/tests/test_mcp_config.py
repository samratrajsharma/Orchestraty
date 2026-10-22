"""orch mcp --config: how to register the MCP server with an agent, printed; nothing written."""
import json
import os
import subprocess

import pytest

from helpers import tree
from orchestraty.mcp import register
from orchestraty.paths import State

INITIALIZE = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.mark.parametrize("agent", register.AGENTS)
def test_mcp_config_prints_how_to_register_and_writes_nothing(agent, tmp_path, capsys):
    from orchestraty import cli
    before = tree(tmp_path)
    assert cli.main(["mcp", "--config", agent]) == 0
    out = capsys.readouterr().out
    assert tree(tmp_path) == before
    assert register.AGENTS_LINE in out and "check_before_edit" in out and out.endswith("writes nothing.\n")
    assert register.UNTESTED in out
    assert ('"args": ["-m", "orchestraty", "mcp"]' if agent == "antigravity" else " -m orchestraty mcp") in out


def test_the_registration_commands_and_snippets_parse(tmp_path):
    argv, env = ["/opt/py thon/bin/python3", "-m", "orchestraty", "mcp"], {"PYTHONPATH": "/x/orchestraty"}
    claude = register.text("claude", argv, env)
    assert "claude mcp add --scope user orchestraty --env PYTHONPATH=/x/orchestraty -- " in claude
    codex = register.text("codex", argv, env)
    assert "codex mcp add orchestraty --env PYTHONPATH=/x/orchestraty -- " in codex
    table = "\n".join(line[4:] for line in codex.splitlines() if line.startswith("    [mcp_servers")
                      or line.startswith(("    command", "    args", "    env")))
    try:
        import tomllib
    except ImportError:
        tomllib = None
    if tomllib is not None:
        assert tomllib.loads(table)["mcp_servers"]["orchestraty"] == {"command": argv[0], "args": argv[1:], "env": env}
    ag = register.text("antigravity", argv, env)
    snippet = ag[ag.index("    {"):ag.index("\n    }\n") + 6]
    assert json.loads(snippet) == {"mcpServers": {"orchestraty": {"command": argv[0], "args": argv[1:], "env": env}}}


def test_the_printed_command_starts_a_working_server(state, tmp_path):
    argv, env = register.command()
    proc = subprocess.run(argv, input=(json.dumps(INITIALIZE) + "\n").encode(), capture_output=True, timeout=120,
                          cwd=str(tmp_path), env=dict(os.environ, ORCH_HOME=state.root, **env))
    assert json.loads(proc.stdout)["result"]["serverInfo"]["name"] == "orchestraty", proc.stderr
