# Changelog

Notable changes to Orchestraty. Nothing has been released yet; the version in the package is `0.0.1.dev0`.

## Unreleased

Phase 1, on top of the coordination core already in place (the hook script and its installer, the background coordinator and its record, the live map, the overlap rules O1 to O4 and O6, notes written in advance, the push line, steer and wake, `orch status` and the Claude Code mod).

### Added

**Agents and integration levels**

- Codex and Antigravity adapters: `orch init --agent codex|antigravity` and `orch restore --agent`; notes in each agent's own form, only at the events where it can take them; `orch status` says what each session's agent cannot be told. Built; not yet tested with the real agents.
- `orch mcp`, an MCP server over stdio that answers from the record (`who_is_changing`, `check_before_edit`, `my_overlaps`, `status`, `declare`, `claim`, `release`), and `orch mcp --config claude|codex|antigravity`, which prints how to register it.
- The two-call helper for agents you build yourself, in Python (`orchestraty.helper`) and TypeScript (`helpers/typescript`), and an example agent in `examples/homemade-agent`.
- Cloud agents' branches, opt-in with `orch remote on`: fetched read-only into a shadow repository and compared with your sessions as plain-git sessions; `orch status` shows them in a group of their own.
- A Claude Code plugin (`plugins/claude-code`) with the hooks, the mod, the MCP server and a skill, listed in the repository's own marketplace; `orch init`, `orch restore` and `orch doctor` know about it. Built; not yet installed in a real Claude Code session.
- A VS Code and Cursor extension (`editors/vscode`) that shows the sessions and overlaps of the workspace's repository in a status bar item and a panel. Built and unit-tested; not yet tried in the editors.

**Findings and notes**

- Rule W1: two sessions working in the same folder.
- Rule O5: two sessions changing the same HTTP route (FastAPI, Flask and Django routes, read from the syntax tree by the engine).
- Rule O7: the work two sessions declared or claimed overlaps.
- Claims: `orch claim`, `orch claims` and `orch release`, which reach other sessions before they edit what is claimed; a hard claim makes the edit wait for its user's answer, where the agent can ask.
- TypeScript contracts, opt-in for each repository with `orch ts on`: the exported API from the compiler's declarations, or from the source when the project does not compile; their notes say they are heuristic.
- The together check, opt-in: two overlapping sessions' work merged in a scratch copy and run with your own test command.

**Handoffs and merging**

- `orch handoff` packages a session's work; `orch resume` starts an agent on it in a new worktree.
- Merge-order advice, in `orch status` and `orch merge-order`.

**Measuring and feedback**

- `orch findings`, `orch label` (real, harmless or false) and `orch missed`.
- `orch stats`: usage week by week, with an acted-on proxy, notes per session per day and what was held back; `--summary` gives counts only.
- `orch feedback`: the stats summary and your own comment, shown first and sent nowhere.
- `orch events`: the record's changes as a stream.

**Installing and removing**

- One distribution, `orchestraty`: the `orch` command and the engine in one pure-Python wheel with no dependencies.
- `orch doctor` checks what Orchestraty needs, with a fix for each problem.
- `orch uninstall` takes out everything Orchestraty added, puts back the settings files `orch init` backed up where nothing is lost, and asks before removing its state folder.

**The record**

- Schema 2 keeps labels and missed-note reports; schema 3 logs every change and keeps declarations; schema 4 keeps claims. An older record is brought up to date when it is opened.

**Repository**

- CI builds the sdist and the wheel and smoke-tests the wheel on Windows and Linux (and macOS in the weekly run); it tests the TypeScript helper, the TypeScript contracts and the editor extension with Node.
- A release workflow that builds, smoke-tests and drafts a GitHub release. Publishing to PyPI is off until the owner turns it on.
- `scripts/first_note_timing.py` times install to first note on a large repository.
- The rules page (`docs/rules.md`), the agents' test results (`docs/agents.json`) and the README table generated from them, a guide for trying Orchestraty (`orchestraty/docs/beta.md`), `SECURITY.md` and `CONTRIBUTING.md`.

### Changed

- Steer stops an edit only for a strong finding's note; every other note only informs, in steer mode too.
