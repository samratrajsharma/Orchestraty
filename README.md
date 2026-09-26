# Orchestraty

This repository holds Orchestraty's effect engine.

## What is here

| Path | What it is |
| --- | --- |
| `engine/` | The effect engine, Python package `orch_engine`. It ingests a repository, parses it and builds the code graph that the impact, tracking and check analyses read from. [`engine/README.md`](engine/README.md) describes each module. |
| `spikes/` | Throwaway experiments that are not part of the product. They will be deleted when the experiments end. See [`spikes/README.md`](spikes/README.md). |
| `docs/decisions/` | Architecture decision records. Start with the [template](docs/decisions/template.md). |
| `.github/workflows/ci.yml` | For every push and pull request, runs the engine tests on Linux (Python 3.10, 3.11 and 3.12) and Windows (Python 3.12), and the spike tests on Linux (Python 3.9 to 3.12) and Windows (Python 3.12). |

## Run the tests

You need Python 3.10 or later and pytest. The engine's core uses only the standard library.

On Windows (PowerShell):

```
cd engine
python -m pip install "pytest>=8,<10"
python -m pytest
```

On macOS, Linux and WSL2, use `python3`. Install pytest in a virtual environment, because the system Python usually refuses `pip install`. On Debian and Ubuntu, including the default WSL2 distribution, `venv` needs the `python3-venv` package first (`sudo apt install python3-venv`).

```
cd engine
python3 -m venv .venv
. .venv/bin/activate
python -m pip install "pytest>=8,<10"
python -m pytest
```

`engine/pytest.ini` already makes the output quiet. If you add `-q` as well, pytest also hides the final "N passed" line. CI installs the same pytest range.

Optional extras add features but are not needed for the tests:

- `pip install -e ".[treesitter]"` parses more languages;
- `pip install -e ".[mcp]"` adds the MCP server.
