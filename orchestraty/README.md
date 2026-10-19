# orchestraty

The main package: the `orch` command, the hook shims, the coordinator, the live map, the overlap rules and the notes that keep parallel coding-agent sessions aware of each other.

**Status: in development.** Capture works: `orch init` installs the hook script and Claude Code's hook entries, and every session then appends one line per event to its own spool file. The other subpackages below hold only a description of their job until that component is built. See the repository [README](../README.md) for the design.

| Subpackage | Job |
| --- | --- |
| `capture` | The hook script and its installer. Agents call the script on events; it appends one line to a per-session spool and exits. Silent and fail-open; built-in modules only, so it starts fast. |
| `coordinator` | One process per user, started on demand. It reads the spools, owns the local record, runs the engine and the rules, writes notes in advance, and exits when idle. |
| `livemap` | Every live session on a repository, with the files, functions and contract changes it has made since its starting commit. |
| `rules` | Rates how two sessions' changes touch (weak, medium or strong) and decides who must hear. |
| `delivery` | Notes built from templates over structured fields, written in advance for each receiving session, and the one-line push check. |
| `adapters` | Turns each agent's hook events into one common shape, and hands notes back in the form that agent accepts. |

## Commands

| Command | What it does |
| --- | --- |
| `orch init` | Copies the hook script into the state folder and adds hook entries for Claude Code's events to your user settings (`~/.claude/settings.json`, or the folder `CLAUDE_CONFIG_DIR` names), so every session and worktree is seen. Shows what will change and asks first; `--yes` skips the question, `--project` uses this folder's `.claude/settings.local.json`, `--claude-config DIR` another config folder, `--print` only prints the entries. The settings file is backed up before every change. |
| `orch restore` | Removes Orchestraty's hook entries and leaves everything else. `--from-backup` puts back the settings file as it was before the last `orch init`. |

The hooks record event names, session ids, folders and the paths of edited files; never prompts, file contents, commands or tool output. Nothing leaves the machine.

## Where state lives

One folder per user and environment, never inside a repository: `%USERPROFILE%\.orchestraty` on Windows, `$XDG_STATE_HOME/orchestraty` (by default `~/.local/state/orchestraty`) elsewhere. `ORCH_HOME` overrides it. Windows and each WSL distribution have their own folder.

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
