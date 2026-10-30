# Trying Orchestraty: a guide for testers

Orchestraty keeps parallel coding-agent sessions on one repository aware of each other. When one session changes something another session depends on, the affected agent is told, in a short note, before it builds on the old form. This is Phase 1, in early access; we're working on the next phases and will open them soon.

Where it stands: Claude Code is tested with the real agent, and two real sessions reach their notes end to end. Codex and Antigravity are built; not yet tested with the real agents. Python is analysed in full; TypeScript only when you turn it on (`orch ts on`), and its notes say they are heuristic.

## Install

You need Python 3.10 or later, git 2.38 or later, and a recent Claude Code (`claude update`; the status line and pane need 2.1.287 or later). Install, then run `orch init` from a terminal in the repository your agents work on:

```bash
python -m pip install orchestraty
cd path/to/your/repository
orch init          # shows what it will change and asks first
orch doctor        # checks everything it needs, with a fix for each problem
```

`pipx install orchestraty` or `uv tool install orchestraty` work too, and so does a wheel you were given (`pipx install orchestraty-<version>-py3-none-any.whl`). For the newest work on `main` before it is released: `python -m pip install "git+https://github.com/samratrajsharma/Orchestraty.git"`. If `orch` isn't found, type `python -m orchestraty` in its place. To see the whole loop before your own sessions, run `orch ui --demo`: two pretend sessions in a sandbox, in your browser.

`orch init` adds hook entries to Claude Code's user settings (backed up first), with the status line and pane, watches the repository, and offers the push line: a git pre-push hook, chained in front of your own, that adds one line to `git push` when callers of a function you changed are still on the old form. For the other agents, run `orch init --agent codex` or `orch init --agent antigravity` as well. Codex runs a new hook only once you have trusted it: open Codex and run `/hooks`.

Then restart any Claude Code sessions that were already open, and work as you always do, with each session in its own worktree. Two sessions in one folder work too, but each is told to use a worktree, and what each changed can't be told apart. Nothing else needs starting; the first hook starts Orchestraty's background process, which exits after ten minutes with nothing to do.

## What you will see

Mostly nothing. Orchestraty speaks to the agents, not to you, and only when one session's change affects another's work. A note arrives in the agent's context, before an edit, with its next tool result, or at the end of its turn, and you can read it in the session's transcript. Here is one, as the hook hands it to the agent about to edit `shop/checkout.py`:

```text
Orchestraty: another Claude Code session (worktree refunds, branch feat-refunds) changed the contract
of charge in shop/payments.py: parameter currency can no longer be passed by position. The change is
in that worktree, so your copy does not show it yet; both meet when merged. shop/checkout.py uses it:
checkout (line 11). Check these uses before you change this file: passing the arguments by name works
with both forms; otherwise agree with that session if the old form must stay. Static calls only;
dynamic calls are not analysed.
```

Every note is filled in from a template with names, files and lines; no model writes it, and nothing another session wrote ever reaches it. A session hears about each finding once. The same file changed in two sessions is a weak overlap: no one is told, and it shows only in `orch status`.

Where you look yourself:

- `orch status`: the live sessions, what each has changed, and the overlaps between them.
- At `git push`, one line if callers of a changed function are still left on the old form, and nothing otherwise. It never blocks the push.
- `orch ui`: the same in your browser, on this computer only.
- In Claude Code 2.1.287 or later, a status line, a highlighted band when an overlap is strong, and `/orch` to open a pane of every session. (A VS Code and Cursor extension is built, but not yet part of early access.)

## A step for each feature

Two sessions, A and B, each in its own worktree of a Python repository. Each row says what to ask or run, and what should happen; when a row doesn't do what it says, run `orch missed "..."` or open an issue.

| Feature | Try | What should happen |
| --- | --- | --- |
| A broken contract | A makes a parameter of a function keyword-only. B works on a file that calls it the old way | B hears before it edits that file. A, if it ends its turn with callers left on the old form, hears which |
| The push line | In A's worktree, while callers are still on the old form: `git push --dry-run origin <A's branch>` | One line names them, and nothing is pushed. A real push prints the same line and still goes through |
| A removed function | A removes a function that B's files still call | B hears before it edits such a file |
| The same function | Both change the same function | Each hears once |
| The same folder | Two sessions in one folder instead of worktrees | Each is told once to use a worktree |
| Claims | In A's worktree, while A works: `orch claim path/to/file.py --for 1h`; then ask B to edit that file | B hears before it edits. `orch claims` lists it; `orch release path/to/file.py` ends it. With `--hard`, B's edit waits until you allow it in B's session |
| Merge order | While B's changed file still uses A's breaking change: `orch merge-order` | Which branch to merge first |
| Handoff | In A's worktree: `orch handoff`; then `orch resume <id> --agent claude --dry-run`, and the same without `--dry-run` | A packet of A's work, with its id. The dry run says what resume will do; the real one makes a worktree and starts Claude Code there with the packet |
| Together | `orch together set "python -m pytest -q"`, `orch together on`; give A and B changes that pass alone and fail merged | Both hear that their work fails together, with the failing test |
| Routes | Both change the same FastAPI, Flask or Django route | A strong note to both |
| TypeScript | `orch ts on`, then A changes an exported function's parameters and B edits a file that imports it | B hears, in a note marked heuristic |
| Ask before editing | `orch mcp --config claude`, run the command it prints, then ask an agent who else is changing a file | The agent asks Orchestraty and answers |
| Cloud agents | `orch remote on` in a repository your cloud agents push branches to | Their branches show in `orch status`; breaks they cause reach your sessions |

## Steer and wake

Both are off by default.

- `orch config steer on`: a strong finding's note before an edit (a breaking or removed Python function that the file uses) stops that edit with the reason, so the agent re-plans first. With steer off, the note only informs, and your agent's usual permission prompts apply.
- `orch config wake on`: when a Claude Code session has finished its turn and is waiting for you, a note that another session changed something its own changes use wakes it up to act on it. Other agents can't be woken.

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

`orch stats` shows how it went, week by week: sessions, findings, notes and how quickly each was ready for its agent, roughly how many notes the agents acted on, notes per session per day, and how many it held back to keep the noise down.

To send us your feedback:

```bash
orch feedback                          # the stats summary and your comment, typed at a prompt
orch feedback --out feedback.json      # ... and saved to that file, once you have read it and said yes
```

It holds counts only, plus your comment as you typed it: no path, name, session id or date. It prints everything first and sends nothing anywhere: attach the file to an issue on the repository, or send it to whoever invited you.

For anything else, a bug, a confusing note or an idea, open an issue on the repository: what you ran, what you expected, and the note the agent got, if any. A strong note that was wrong is the most serious kind of bug: please tell us the same day. `orch findings` shows how strong each finding was.

## Upgrade

To take a newer release:

```bash
python -m pip install --upgrade orchestraty    # or: pipx upgrade orchestraty, uv tool upgrade orchestraty
orch init                   # puts the new hook script in place
orch coordinator stop       # the next session starts the new version
```

`orch --version` and the orchestraty line of `python -m pip freeze` say which build you have; put them in any bug report.

## Uninstall

Run `orch feedback` first if you want to send it, since uninstalling removes the record. Then:

```bash
orch uninstall                          # prints what it will take out, then asks
python -m pip uninstall orchestraty     # or: pipx uninstall orchestraty, uv tool uninstall orchestraty
```

`orch uninstall` takes out everything Orchestraty added: its hooks in each agent's settings, the status line and pane, the Claude Code plugin if you installed it, and the push line in every watched repository. Other entries in those files are never touched, and a settings file you haven't changed since `orch init` gets its backup back. Last, it asks before deleting the record; `--keep-state` keeps it. Running it again changes nothing.

An MCP server you registered by hand (`orch mcp --config` only prints how) stays: remove it with `claude mcp remove orchestraty`, `codex mcp remove orchestraty`, or in Antigravity's MCP settings.
