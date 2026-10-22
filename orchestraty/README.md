# orchestraty

The main package: the `orch` command, the hook shims, the coordinator, the live map, the overlap rules and the notes that keep parallel coding-agent sessions aware of each other.

**Status: in development.** The coordination core works end to end in tests: `orch init` installs the hooks and watches the repository it runs in; the coordinator, which the hooks start when needed, keeps a live map of what each session has changed, runs the overlap rules, and writes each note in advance for the session that must hear it; the hooks hand the notes to the agents. A real run with two Claude Code sessions, and the measurements, are next. See the repository [README](../README.md) for the design.

| Subpackage | Job |
| --- | --- |
| `capture` | The hook script, its installer and the git hook chain. Agents call the script on events: it appends one line to a per-session spool, hands the agent any note waiting for that event, and exits. Silent and fail-open; built-in modules only, so it starts fast. In git's pre-push it prints the push line, and never blocks a push. |
| `coordinator` | One process per user and environment, started by a hook when it is not running. It holds a lock (only one runs), reads the spools, owns the local record (SQLite), keeps each session's lifecycle, and exits when idle. It also stores the lines `orch label` and `orch missed` append to its own spool, and brings a record of an older schema up to date when it opens it. A hooked session whose edits land in another worktree of its repository moves there with them. Running the engine and the rules, and writing notes in advance, come next. |
| `livemap` | For each live session, the files it has changed since its base commit (from its edit events and git, after each tool call and every few seconds) and, for Python, the functions and classes added, removed, moved or modified, with each signature change rated breaking, review or compatible, and for a breaking change, which calls it breaks (a renamed parameter breaks only the calls that pass it by name). Worktrees with changes and no hooked session are followed as plain-git sessions, so agents without hooks, editors and people are seen too. Parses are cached by content and shared by every worktree; the engine's index of each base commit is built in the background and gives each session a code graph of its own copy of the code. |
| `rules` | The overlap rules for Python: O1 the same file (weak), O2 the same function (medium), O3 a breaking change used in a file another session is changing, O4 a removed function still used there, O6 callers left behind that no session has updated (all strong). Uses come from the engine's resolved calls and inheritance, never from names alone, and count only when the change can break them, judged by how each call passes its arguments. A file in the other session's copy that uses the change but that it is not changing yet gives a latent finding: not listed as an overlap, but the session gets its note before its first edit of that file. W1 (medium): two sessions with hooks work in the same worktree, whichever subfolder each started in. Between two sessions in one folder, O1 runs on the files each edited, and O2, O3 and O4 do not, since what each did to a function cannot be told apart there. Each finding has a stable key; the coordinator opens, refreshes and resolves them. |
| `delivery` | Notes from templates over structured fields only (identifiers are cleaned, so no session's words reach another), written in advance into slot files for each receiving session and taken by the hook exactly once: before an edit of a file that uses a broken or removed function (O3, O4), with the next tool result (O2, and who uses what you changed), at the end of a turn for callers left behind (O6), and with the next tool result for another session working in this same folder (W1: one note per session per other session, which only informs, in steer mode too). One note per finding per session; a note whose finding stops holding before it is taken is withdrawn. Also the push line, which stays for a worktree an agent has left (its changes are then followed as plain git), and the opt-in steer and wake. |
| `adapters` | Turns each agent's hook events into one common shape, and hands notes back in the form that agent accepts. |

## Commands

| Command | What it does |
| --- | --- |
| `orch init` | Copies the hook script into the state folder and adds hook entries for Claude Code's events to your user settings (`~/.claude/settings.json`, or the folder `CLAUDE_CONFIG_DIR` names), so every session and worktree is seen; then watches the repository it runs in and offers the push line (a pre-push hook chained in front of the repository's own hooks). Shows what will change and asks first; `--yes` skips the questions, `--no-git-hooks` the push line, `--project` uses this folder's `.claude/settings.local.json`, `--claude-config DIR` another config folder, `--print` only prints the entries. The settings file is backed up before every change. |
| `orch restore` | Removes Orchestraty's hook entries and leaves everything else. `--from-backup` puts back the settings file as it was before the last `orch init`. |
| `orch watch [PATH]`, `orch unwatch [PATH]` | Start or stop comparing the sessions of the repository holding PATH (default: here). `orch init` watches the repository it runs in. Sessions in other repositories are dropped unread: nothing about them is recorded. |
| `orch status` | The coordinator and the live sessions on each watched repository: agent, worktree, branch, base commit, last event, working or idle, what each has changed (files, and the functions whose contract changed), and the overlaps between them, strongest first. `--json` for other tools (the `schema` field versions it), `--all` to include ended sessions. |
| `orch findings` | The findings seen in the last days, the latest first: rule, strength, what it is about, the sessions involved, first and last seen, open or resolved, and your label. Each has a short number that `orch label` takes for 24 hours (kept in the state folder's `finding-numbers.json`). `--all` adds the resolved and latent ones, `--days N` (default 7), `--json` for other tools. Reads the record without changing it. |
| `orch label <number\|key> real\|harmless\|false [--why "..."]` | Your verdict on a finding: real, harmless or false, with your reason if you give one. |
| `orch missed "<what should have been noted>" [--session ID]` | Reports a note that should have come; `--session` takes a session's id or its first characters. |
| `orch stats [--days 7] [--json]` | How Orchestraty was used, week by week, from the record (see [Measuring daily use](#measuring-daily-use)). `--json` carries a schema number. `--summary [--out FILE]` gives an anonymous summary: counts only, shown before it is saved. |
| `orch config [steer\|wake] [on\|off]` | Off by default. **steer**: a note before an edit stops that edit with the reason, so the agent re-plans (otherwise the note only informs, and the agent's usual permission prompts apply). **wake**: a note for a session that waits for its user wakes it (adds a background hook). |
| `orch coordinator run`, `orch coordinator stop` | For debugging: hooks start the coordinator when it is needed, and it exits after ten minutes with nothing to do. |

The hooks record event names, session ids, folders and the paths of edited files; never prompts, file contents, commands or tool output. Nothing leaves the machine. `orch label` and `orch missed` never write the record themselves: each appends one line to Orchestraty's own spool (`<state>/spool/orch/`), and the coordinator stores it. Your words (`--why`, the missed text) stay in the record on this machine and are never put into a note. When a session ends, its waiting notes are taken back. `orch unwatch` also takes the push line out of that repository's git hooks.

## Measuring daily use

`orch stats` counts, for each week of the last `--days` days (the latest 7 days, the 7 before them, and so on):

- the sessions live in it (agent sessions with hooks, and plain-git ones), and the pairs of agent sessions on one repository working at the same time;
- the findings first seen in it, by rule and by strength (latent ones apart);
- the notes made in it, by kind (`edit`, `next`, `stop`, `wake`) and by state (`pending`, `written`, `delivered`, `withdrawn`), how many were delivered, and the median and 90th percentile of their time to awareness: from the event that caused a note to the note written, ready for its agent;
- the notes acted on, and the findings they were about;
- the labels given in it, by verdict (a finding's latest label), and the reports of missed notes;
- an estimate of the tokens the notes handed to agents cost: their characters / 4.

**Acted on is a proxy**, worked out from the record only. A note handed to its agent counts as acted on when, within `acted_minutes` of its delivery (30 by default; set it in `config.json`), either the receiving session edited a file the note cites (the file its finding names, or one holding a function it names), or the finding stopped holding while that session was live. It says the agent did something the note may have caused, not that the note caused it.

`orch stats --summary` keeps counts only: no path, name, symbol, session id, date or text of yours. It prints first, so you read all of it; `--out FILE` then saves it once you say yes (`--yes` skips the question). Orchestraty never sends it anywhere.

## The Claude Code mod

`orch init` also loads Orchestraty's [mod](https://code.claude.com/docs/en/plugins/mods/overview) (Claude Code 2.1.287 or later): it copies it into the state folder and adds that folder to `CLAUDE_CODE_PLUGIN_DIRS` in the same settings file. The mod shows; it detects nothing. Every few seconds it reads the status the coordinator keeps (`status.json`, the same as `orch status --json`) and draws:

- a line under the prompt: how many other sessions work on this repository, and the overlaps that concern this session;
- a band above the prompt, only for strong overlaps that concern this session, with **Show all** and **Dismiss**;
- a pane with every session on the repository and every overlap: `/orch`. Also `/orch status`, `/orch steer on|off`, `/orch wake on|off`.

Notes still reach Claude through the hooks. The one thing the mod hands over itself is a wake note for its own session while that session waits for its user: it asks the hook script to take the note (exactly once) and submits it as a prompt. The mod reads only Orchestraty's state folder, starts only Orchestraty's own Python, and makes no network request; `claude plugin validate` lists exactly that. `--no-mod` leaves it out. In the desktop app, WSL sessions load no plugins, so the mod is absent there while the hooks still work.

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
