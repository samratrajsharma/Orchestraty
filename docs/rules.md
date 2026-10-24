# Orchestraty's rules

These are the rules Orchestraty is built by: what it does, what it will not become, how it stays quiet, and what would make us change course. Each one says how the code keeps it today.

## Principles

**Agents first, people last.** When one session's change affects another session's work, the affected agent is told first, in a note it reads before its next edit, with its next step or at the end of its turn, so it can adapt while it is still working. A person hears only what the agents left unresolved, in one line: the push line, printed at `git push` when callers of a changed function are still on the old form.

**Never slow the agent down.** An agent's hook runs a small script that appends one line to a file and exits; the one thing it ever waits for is a note already written in advance. The tests measure it at about 10 ms over a bare Python start on Linux. Everything heavy runs in a separate background process. Where a setup misses that budget (the Claude Code plugin's hooks on Windows, which have to start Git Bash), the docs say so, and `orch init` says which setup is faster.

**Every agent, at the level it supports.** Plain git works for any agent, editor or person (level 0); agents with MCP can ask before they edit (level 1); agents with hooks are told without asking (level 2), and so are agents you build yourself, through a two-call helper; Claude Code also gets a pane, and VS Code and Cursor a panel (level 3). No agent has to change.

**Facts first, no model on the hot path.** Nothing in Orchestraty calls a language model. Every note is filled in from a template with facts from git and the code graph, and cites the file, function, line and other session it is about. The same changes always give the same note.

**Precision over recall.** A wrong note costs more than a missed one. A use counts only when the call resolves to the changed function, never by its name alone, and only when the change can break it, judged by how each call passes its arguments. A note about a changed or removed function says what its analysis does not cover ("Static calls only; dynamic calls are not analysed"). Where the evidence is weaker, as for TypeScript, where calls are not resolved, a finding is never strong, and its note says it is heuristic.

**Thin surfaces, thick core.** All the detecting happens in one place: one background process per user, the only writer of the local record. `orch status`, the Claude Code mod, the editor extension and the MCP tools all show what it found; none of them detects anything itself.

**Local-first.** No account, no server and no telemetry. The record stays in a folder on your machine. The one network access is opt-in: `orch remote on` fetches cloud agents' branches from your own repository's remote. Commands that export something (`orch stats --summary`, `orch feedback`) print all of it first and save it only where you say; they never send it.

**Standards, not new formats.** Orchestraty plugs into what agents and tools already read: each agent's own hooks, MCP over stdio, a line in AGENTS.md, and git's own hooks, chained in front of the ones a repository already has. What it offers other tools is plain JSON with a schema number, documented field by field.

**Never used to rate people.** It records sessions, worktrees and branches, not who wrote what, and `orch stats` counts how the notes worked, never how a person did. There is no score for anyone.

**The maker uses it every day, or it doesn't ship.** A release is published only after the people who build Orchestraty have used it on their own parallel sessions every day, and `orch stats` is how that use is measured.

**Claims match the evidence.** An agent is called supported only after its capture test with the real agent passed, and the agents table is generated from those results. Everything else is "built; not yet tested with the real agent". A note about a contract says what its analysis left out, and the docs say what has not been tried.

## What we will not build

| Not this | Why |
| --- | --- |
| Our own coding agent or model | Orchestraty makes the agents you already use aware of each other; an agent of our own would be one more to coordinate, and a model would break "facts, no model". |
| A cockpit that launches, runs or displays sessions | Your agents and your editor are where you work. The one exception, `orch resume`, starts an agent only when you run it. |
| A task planner or a multi-agent framework | Deciding who does what is yours and your agents'. Orchestraty only says what their changes do to each other. |
| Warnings judged by a model | A note must be reproducible and cite its evidence. A model's judgement cannot be checked, and it costs tokens and time. |
| An MCP gateway, aggregator or router | Orchestraty is one MCP server beside the agent's others. Standing between an agent and its tools would add a point of failure, and see far more than it needs to. |
| Cloud execution of agents | Orchestraty runs nothing of yours anywhere but on your machine. |
| A standalone code-graph MCP server | The code graph exists to work out what one session's change does to another, not as a general code-search service. (The engine keeps a read-only graph server for development; it is not an `orch` command, and needs a package Orchestraty does not install.) |
| Codebase chat or RAG | Orchestraty answers one question, what a change does to the other sessions, from facts, not by searching text. |
| A replacement for pull requests or review bots | It works before the merge, while the agents are still working. Review stays review. |
| A desktop app, or a server other machines can reach | The agent, the terminal and the editor are where you already look. The one page Orchestraty serves, `orch ui`, is for trying it out: it listens only on 127.0.0.1, for this computer, behind a link that works once, and it shows without deciding anything. |
| Individual performance scoring | Counting who collided invites blame and gaming, and makes people turn the tool off. |
| Telemetry by default, or uploading prompts or transcripts | Local-first: what you share, you export yourself, and you see it first. |

## The silence rule

A note that isn't needed costs the agent tokens and teaches everyone to ignore notes. So:

- **One note per finding per session.** A session hears about each finding once. When it takes one of its notes about a finding, whether before an edit, with its next step or by being woken, the others are taken back. A finding that comes back is not told again, and the same change found again under another session is not told twice. A note whose finding stops holding before the agent takes it is withdrawn.
- **A length limit.** A note is at most 1,200 characters; each name in it is cut to 120 characters, and each list to 6 items.
- **Nothing for weak overlaps.** Two sessions changing the same file is a weak overlap: nobody is told, and it shows only in `orch status`. A use of a changed function in a file the other session is not changing waits until that session first edits the file.

`orch stats` shows how this works out: notes per session per day, and how many were held back.

## What would change our course

We watch for these, and say in advance what we would do:

- **If agents don't use the notes,** we stop building delivery into agents.
- **If collisions turn out to be rare,** we become a smaller check at push time, or aim at teams first.
- **If the maker doesn't act on the findings,** we stop building and find out why.
- **If an agent's vendor ships contract-level awareness between its own sessions,** we lead with every agent and with teams, and integrate with their feature.
- **If a code host ships contract-level warnings across people, including local work,** we integrate with it and extend it.
- **If a false high-confidence note leads to a bad merge,** that is a top-priority bug, and a second one freezes feature work until precision is fixed.
