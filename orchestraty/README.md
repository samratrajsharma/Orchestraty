# orchestraty

The main package: the `orch` command, the hook shims, the coordinator, the live map, the overlap rules and the notes that keep parallel coding-agent sessions aware of each other.

**Status: scaffold.** The package installs and `orch --version` works. The subpackages below name the planned components; each holds only a description of its job until that component is built. See the repository [README](../README.md) for the design.

| Subpackage | What it will do |
| --- | --- |
| `capture` | Hook shims and the spool. Agents and git call the shims on events; each appends one line to a per-session spool and exits. Silent and fail-open. |
| `coordinator` | One process per user, started on demand. It reads the spools, owns the local record, runs the engine and the rules, writes notes in advance, and exits when idle. |
| `livemap` | Every live session on a repository, with the files, functions and contract changes it has made since its starting commit. |
| `rules` | Rates how two sessions' changes touch (weak, medium or strong) and decides who must hear. |
| `delivery` | Notes built from templates over structured fields, written in advance for each receiving session, and the one-line push check. |
| `adapters` | Turns each agent's hook events into one common shape, and hands notes back in the form that agent accepts. |

## Rules

- Python 3.10 or later, standard library only in the core.
- May import the engine (`orch_engine`); never imports from `spikes/`. The engine never imports this package. `tests/test_layout.py` checks both.
- Tests leave nothing behind in the working copy.

## Run the tests

```bash
cd orchestraty
python -m pytest
```

## Install for development

```bash
pip install -e ./engine -e ./orchestraty
orch --version
```
