# Security

## Reporting a problem

Please report a security problem privately, not in a public issue: on this repository's **Security** tab, choose **Report a vulnerability** (GitHub's private vulnerability reporting). Say what you ran, what happened, and which version (`orch --version`). The maintainers answer in that report, and agree with you when the fix and the problem are made public.

Examples of what we treat as a security problem:

- one session's own words (a prompt, a comment, a string in its code) reaching a note for another session;
- anything leaving the machine without the user running a command that exports it;
- a write outside the state folder that the user did not agree to, or into a repository's tracked files;
- a hook that blocks, fails or slows an agent beyond what the docs say;
- prompts, file contents, commands or tool output stored anywhere.

## What Orchestraty reads

- **Agents' hook events.** The hook script copies a few named fields from each event and nothing else: the session id, the event name, the working folder, the tool's name and call id, the path of the file an edit tool changes, the transcript's path (the transcript itself is never opened), and, for a few events, why the session started or ended. It finds them in the raw bytes without parsing the payload. Prompts, file contents, commands and tool output are never stored.
- **The repositories you watch.** Through git, read-only: each session's changes against its base commit, and the code at that commit, which the engine parses to compare contracts. Sessions in repositories you don't watch are dropped unread.
- **Agents' settings files,** when you run `orch init`, `orch restore`, `orch uninstall`, `orch config wake` or `orch doctor`.
- **Its own state folder:** `%USERPROFILE%\.orchestraty` on Windows, `$XDG_STATE_HOME/orchestraty` (by default `~/.local/state/orchestraty`) elsewhere, or the folder `ORCH_HOME` names.

## What Orchestraty writes

- **The state folder:** the hooks' spool lines, the record (one SQLite file, written only by Orchestraty's background process), the notes waiting for each session, the status other tools read, the settings, backups of every agent file it changed, a copy of the hook script and of the Claude Code mod, handoff packets, and, when you turn them on, the cloud branches' shadow repositories and the together check's scratch copies (removed when each check ends).
- **Agents' settings files,** only when you run a command that changes them. It shows the change and asks first (`--yes` skips the question), backs the file up first, and leaves every entry that isn't its own as it was.
- **Inside a repository, only after showing and asking:** the push line, a pre-push hook chained in front of the repository's own (`core.hooksPath` set to a folder inside `.git`); and, with `--project`, the agent's settings file in the workspace, with a line in `.git/info/exclude` to keep it out of git. `orch resume` makes the worktree and branch you ask it for. Orchestraty never commits and never pushes.

`orch uninstall` takes all of it out again: see the [README](README.md).

## The network

- **The hooks, the background process, the Claude Code mod and the editor extension make no network calls.** The hook script imports no networking module, and the mod reads only Orchestraty's state folder and starts only Orchestraty's own Python.
- **The only network access is opt-in:** the cloud-branch fetch. After `orch remote on` (it says what it will do and asks first), Orchestraty fetches the branches of cloud agents (`claude/*` and the other patterns you keep) from your repository's own remote, read-only, into a shadow repository in the state folder, every few minutes. `orch remote off` stops it.
- **No telemetry.** `orch stats --summary` and `orch feedback` print what they hold, save it only where you say, and send nothing.
- Commands you set up yourself run as you set them: the together check runs the test command you give it (`orch together set`), `orch ts on` runs your project's TypeScript compiler, and `orch resume` starts the agent you name. The engine's own development tools (`python -m orch_engine.check`, its MCP server) can clone a repository you name by URL; `orch` never does.

## Notes cannot carry another session's words

Every note is filled in from a template with identifiers from git and the code graph (function, file and branch names, line numbers), cleaned to a short list of safe characters and cut to a set length. Nothing another session wrote reaches a note, so one session cannot give another instructions through it. The one exception is the together check's note, which quotes one line of your own test command's output, cleaned and cut short.

Orchestraty never answers an agent's permission question with "allow". With steer on (off by default), a strong finding's note stops the edit it is about; a hard claim makes another session's edit wait for its user's answer.
