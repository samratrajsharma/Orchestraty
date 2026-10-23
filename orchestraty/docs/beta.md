# Trying Orchestraty: a short guide

Orchestraty keeps parallel coding-agent sessions on one repository aware of each other. When one session changes something another session depends on, the affected agent is told, in a short note, before it builds on the old form. This guide covers what you need for a trial: installing it, what you will see, the settings worth knowing, how to tell us what it got right and wrong, and how to take it all out again.

Where it stands: with Claude Code, capture has been tested with the real agent. Codex and Antigravity are built; not yet tested with the real agents. The Claude Code plugin and the VS Code and Cursor extension are built and tested on their own; not yet tried in a real session. Python is analysed in full; TypeScript only when you turn it on (`orch ts on`), and its notes say they are heuristic.

## Install

You need Python 3.10 or later and git. Install the wheel you were given as a command of its own, then run `orch init` in the repository your agents work on:

```bash
pipx install orchestraty-<version>-py3-none-any.whl     # or: uv tool install orchestraty-<version>-py3-none-any.whl
cd path/to/your/repository
orch init          # shows what it will change and asks first
orch doctor        # checks everything it needs, with a fix for each problem
```

`orch init` adds hook entries to Claude Code's user settings (backed up first), watches the repository, and offers the push line, a git pre-push hook chained in front of your own. For the other agents, run `orch init --agent codex` or `orch init --agent antigravity` as well. Codex runs a new hook only once you have trusted it: open Codex and run `/hooks`.

Then work as you always do: start two or more sessions, in separate worktrees or in the same folder. Nothing else needs starting; the first hook starts Orchestraty's background process, which exits after ten minutes with nothing to do.

## What you will see

Mostly nothing. Orchestraty speaks to the agents, not to you, and only when one session's change affects another's work. A note arrives in the agent's context, before an edit, with its next tool result, or at the end of its turn, and you can read it in the session's transcript. Here is one, as the hook hands it to the agent about to edit `c.py`:

```text
Orchestraty: another Claude Code session (worktree refunds, branch feat-refunds) changed the contract
of charge in a.py: parameter currency removed. The change is in that worktree, so your copy does not
show it yet; both meet when merged. c.py uses it: use (line 5). Check these uses before you change
this file: leaving that argument out works with both forms, where it was optional; otherwise agree
with that session if the old form must stay. Static calls only; dynamic calls are not analysed.
```

Every note is filled in from a template with names, files and lines; no model writes it, and nothing another session wrote ever reaches it. A session hears about each finding once. The same file changed in two sessions is a weak overlap: no one is told, and it shows only in `orch status`.

Where you look yourself:

- `orch status`: the live sessions, what each has changed, and the overlaps between them.
- At `git push`, one line if callers of a changed function are still left on the old form, and nothing otherwise. It never blocks the push.
- In Claude Code with the mod (`orch init` installs it), `/orch` opens a pane with the sessions and overlaps. In VS Code or Cursor, the extension shows the same in a status bar item and a panel.

## Steer and wake

Both are off by default.

- `orch config steer on`: a strong finding's note before an edit (a breaking or removed Python function that the file uses) stops that edit with the reason, so the agent re-plans first. With steer off, the note only informs, and your agent's usual permission prompts apply.
- `orch config wake on`: a note for a Claude Code session that has finished its turn and waits for you wakes it, so it can act on the note. Other agents cannot be woken.

`orch config` alone shows both.

## Label what it found

Your verdicts are what tell us whether the notes are worth having. `orch findings` lists what was found in the last 7 days, the latest first, each with a short number that holds for 24 hours. Give a verdict:

```bash
orch findings
orch label 3 real --why "B would have shipped a call with the old argument"
orch label 4 harmless
orch label 5 false --why "the call is in dead code"
```

`real` means it caught something that mattered, `harmless` that it was right but did not matter, `false` that it was wrong. `--why` is optional.

## Report a note that should have come

When one session broke another and no note came, say so, in your own words:

```bash
orch missed "session B kept calling charge() with currency after A removed it"
orch missed "..." --session 3fa2      # the session it should have told: its id or first characters
```

## What is recorded, and where it goes

The hooks record event names, session ids, folders and the paths of edited files; never prompts, file contents, commands or tool output. Orchestraty keeps it all in its state folder on your machine (`~/.local/state/orchestraty`, or `%USERPROFILE%\.orchestraty` on Windows, or wherever `ORCH_HOME` points), with your labels and missed-note reports. There is no account and no telemetry, and nothing leaves your machine. The one network use is opt-in: `orch remote on` fetches cloud agents' branches from your repository's own git remote.

`orch stats` shows how it went, week by week: sessions, findings, notes and how quickly each was ready for its agent, notes the agents acted on (a proxy), notes per session per day, and what was held back. Weak findings, notes a session had heard already and notes withdrawn are what keeps the noise down.

To send us your feedback:

```bash
orch feedback                          # the stats summary and your comment, typed at a prompt
orch feedback --out feedback.json      # ... and saved to that file, once you have read it and said yes
```

It holds counts only, plus your comment as you typed it: no path, name, session id or date. It prints everything first and sends nothing anywhere; you send the file, if you choose to.

## Uninstall

Run `orch feedback` first if you want to send it, since uninstalling removes the record. Then:

```bash
orch uninstall                 # prints what it will take out, then asks
pipx uninstall orchestraty     # or: uv tool uninstall orchestraty
```

`orch uninstall` takes out every hook entry Orchestraty added to each agent's settings, the mod, the plugin (with Claude Code's own command, when it is on the PATH; otherwise it tells you the command to run), the push line in every watched repository and the cloud branches' copies. Where the settings file is as it was before `orch init` apart from Orchestraty's entries, it puts back the copy from then, byte for byte; where it has changed since, it takes out only Orchestraty's entries, unless you say to put the old copy back. Other entries are never touched. Last, it asks before removing the state folder; `--keep-state` keeps it. `--yes` goes ahead without asking: it removes the state folder too (unless `--keep-state`), and keeps the changes made to a settings file since `orch init`. Running it again changes nothing.

An MCP server you registered by hand (`orch mcp --config` only prints how) stays: remove it with `claude mcp remove orchestraty`, `codex mcp remove orchestraty`, or in Antigravity's MCP settings.
