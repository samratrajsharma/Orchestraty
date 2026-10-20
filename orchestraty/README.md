# orchestraty

The main package: the `orch` command, the hook shims, the coordinator, the live map, the overlap rules and the notes that keep parallel coding-agent sessions aware of each other.

**Status: in development.** The coordination core works end to end in tests: `orch init` installs the hooks and watches the repository it runs in; the coordinator, which the hooks start when needed, keeps a live map of what each session has changed, runs the overlap rules, and writes each note in advance for the session that must hear it; the hooks hand the notes to the agents. A real run with two Claude Code sessions, and the measurements, are next. See the repository [README](../README.md) for the design.

| Subpackage | Job |
| --- | --- |
| `capture` | The hook script, its installer and the git hook chain. Agents call the script on events: it appends one line to a per-session spool, hands the agent any note waiting for that event, and exits. Silent and fail-open; built-in modules only, so it starts fast. In git's pre-push it prints the push line, and never blocks a push. |
| `coordinator` | One process per user and environment, started by a hook when it is not running. It holds a lock (only one runs), reads the spools, owns the local record (SQLite), keeps each session's lifecycle, and exits when idle. Running the engine and the rules, and writing notes in advance, come next. |
| `livemap` | For each live session, the files it has changed since its base commit (from its edit events and git, after each tool call and every few seconds) and, for Python, the functions and classes added, removed, moved or modified, with each signature change rated breaking, review or compatible. Worktrees with changes and no hooked session are followed as plain-git sessions, so agents without hooks, editors and people are seen too. Parses are cached by content and shared by every worktree; the engine's index of each base commit is built in the background and gives each session a code graph of its own copy of the code. |
| `rules` | The overlap rules for Python: O1 the same file (weak), O2 the same function (medium), O3 a breaking change used in a file another session is changing, O4 a removed function still used there, O6 callers left behind that no session has updated (all strong). Uses come from the engine's resolved calls and inheritance, never from names alone. A file in the other session's copy that uses the change but that it is not changing yet gives a latent finding: not listed as an overlap, but the session gets its note before its first edit of that file. Each finding has a stable key; the coordinator opens, refreshes and resolves them. |
| `delivery` | Notes from templates over structured fields only (identifiers are cleaned, so no session's words reach another), written in advance into slot files for each receiving session and taken by the hook exactly once: before an edit of a file that uses a broken or removed function (O3, O4), with the next tool result (O2, and who uses what you changed), and at the end of a turn for callers left behind (O6). One note per finding per session; a note whose finding stops holding before it is taken is withdrawn. Also the push line, and the opt-in steer and wake. |
| `adapters` | Turns each agent's hook events into one common shape, and hands notes back in the form that agent accepts. |

## Commands

| Command | What it does |
| --- | --- |
| `orch init` | Copies the hook script into the state folder and adds hook entries for Claude Code's events to your user settings (`~/.claude/settings.json`, or the folder `CLAUDE_CONFIG_DIR` names), so every session and worktree is seen; then watches the repository it runs in and offers the push line (a pre-push hook chained in front of the repository's own hooks). Shows what will change and asks first; `--yes` skips the questions, `--no-git-hooks` the push line, `--project` uses this folder's `.claude/settings.local.json`, `--claude-config DIR` another config folder, `--print` only prints the entries. The settings file is backed up before every change. |
| `orch restore` | Removes Orchestraty's hook entries and leaves everything else. `--from-backup` puts back the settings file as it was before the last `orch init`. |
| `orch watch [PATH]`, `orch unwatch [PATH]` | Start or stop comparing the sessions of the repository holding PATH (default: here). `orch init` watches the repository it runs in. Sessions in other repositories are dropped unread: nothing about them is recorded. |
| `orch status` | The coordinator and the live sessions on each watched repository: agent, worktree, branch, base commit, last event, working or idle, what each has changed (files, and the functions whose contract changed), and the overlaps between them, strongest first. `--json` for other tools (the `schema` field versions it), `--all` to include ended sessions. |
| `orch config [steer\|wake] [on\|off]` | Off by default. **steer**: a note before an edit stops that edit with the reason, so the agent re-plans (otherwise the note only informs, and the agent's usual permission prompts apply). **wake**: a note for a session that waits for its user wakes it (adds a background hook). |
| `orch coordinator run`, `orch coordinator stop` | For debugging: hooks start the coordinator when it is needed, and it exits after ten minutes with nothing to do. |

The hooks record event names, session ids, folders and the paths of edited files; never prompts, file contents, commands or tool output. Nothing leaves the machine. When a session ends, its waiting notes are taken back. `orch unwatch` also takes the push line out of that repository's git hooks.

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
