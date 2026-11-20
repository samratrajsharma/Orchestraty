# `orch status --json`

What `orch status` shows, as JSON for other tools: the coordinator, every watched repository, its live sessions and what each has changed, declared and claimed, and the overlaps between them. It is read from the record without changing it, so it works whether the coordinator runs or not; it says what the coordinator last saw.

```bash
orch status --json          # live sessions, overlaps that are not latent
orch status --json --all    # also ended sessions and latent overlaps
```

The same object is in `<state>/status.json`, which the coordinator keeps current (at most once a second) for the Claude Code mod. The MCP tool `status` gives one repository of it (see [below](#the-mcp-tool-status)).

## Schema 1

The `schema` field gives the schema number. This page describes schema 1.

**The compatibility promise.** Within one schema number, fields are only added. No field is removed or renamed, and none changes its type or meaning. A change that would do any of that comes with a new schema number. So a reader should check `schema`, and ignore the fields it does not know.

Times are UTC, as `YYYY-MM-DDTHH:MM:SSZ`. Paths of files in a repository are relative to the worktree's top, with `/` on every system. Folders (`worktree`, `common_dir`) are absolute, in the system's own form. A function or class is named `path::Qualified.name`, for example `billing/charge.py::Invoice.total`. The JSON is ASCII: other characters are written as `\uXXXX` escapes, so it reads the same whatever encoding a reader assumes.

### The object

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | integer | `1` |
| `coordinator` | object | Whether the coordinator runs. See [coordinator](#coordinator). |
| `repositories` | array | One object per watched repository, in the order they were watched. See [repository](#repository). |

### `coordinator`

| Field | Type | Meaning |
| --- | --- | --- |
| `running` | boolean | It has shown it is alive in the last 10 seconds. |
| `pid` | integer | Its process id. Only while it runs, and when it has said so. |
| `since` | string (time) | When it started. Same condition as `pid`. |
| `version` | string | Orchestraty's version it runs. Same condition as `pid`. |

### Repository

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | A short name: the folder name of its main worktree. |
| `common_dir` | string (folder) | Its git common directory, as watched: what makes worktrees one repository. |
| `sessions` | array | Its live sessions, oldest first; with `--all`, also the ended ones. See [session](#session). |
| `overlaps` | array | The open findings between its sessions, strongest first. Latent findings only with `--all`. See [overlap](#overlap). |
| `merge_order` | array | Merge-order advice, one entry per pair of sessions that it concerns; `[]` when there is nothing to say. See [merge_order](#merge_order). |
| `together` | object | The together check (`orch together`): its settings, and the latest result for each pair of the sessions listed. See [together](#together). |
| `typescript` | object | TypeScript contracts (`orch ts`): the settings, and how the exports of each listed session were last read. See [typescript](#typescript). |
| `cloud` | object | Its cloud agents' branches (`orch remote`): the settings, the last fetch, and the branches followed. See [cloud](#cloud). |
| `team` | object | Team sync (`orch team`), only while it is on for the repository or teammates' records of it are known: a repository without it has no `team` key. See [team](#team). |

### Session

A session is an agent session with hooks, or a plain-git one: a worktree with changes and no hooked session working in it, which stands for agents without hooks, editors and people.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | The agent's session id; for plain git, `git-` and twelve hex digits from the worktree's path; for a cloud branch, `cloud-` and twelve hex digits from the repository, remote and branch. |
| `agent` | string | `claude` for Claude Code, `codex` for Codex, `antigravity` for Antigravity, `git` for plain git, `cloud` for a cloud agent's branch; other agents by their own short name. |
| `hears` | array of strings | The kinds of note its agent can be handed: `edit` (before it edits a file), `next` (with its next step), `stop` (at the end of a turn), `wake` (woken while it waits for its user, when wake is on). Claude Code takes all four, Codex all but `wake`, Antigravity `next` and `stop`; `[]` for plain git and cloud branches, which are never told. |
| `worktree` | string (folder) | The top of the worktree it works in. |
| `branch` | string | The branch checked out there; `""` for a detached HEAD. |
| `base_commit` | string | The commit its changes are measured from (40 hex digits); `""` before the first commit. |
| `base_guessed` | boolean | It was first seen after it started, so `base_commit` is the commit at that time. |
| `state` | string | `live` or `ended`. |
| `turn` | string | For a live session with hooks: `working` (from a prompt to the end of the turn) or `idle`. Otherwise `""`. |
| `note` | string | Why it is in its state: `""`, `plain git`, `ended`, `ended: <the agent's reason>`, `timed out`, `clean` (a plain-git worktree with nothing changed any more) or `a hooked session works here`; for a cloud branch, `merged into the default branch`, `the branch is gone`, `remote branches off` or `not watched`. |
| `started` | string (time) | When it was first seen. |
| `last_event` | string (time) | Its last event; for plain git, the last time its changes were read. |
| `seconds_since_last_event` | integer | From `last_event` to the moment of the call. |
| `changes` | object | What it has changed since `base_commit`. See [changes](#changes). |
| `declared` | array | What it says it is about to change (the MCP tool `declare`): the declarations that still hold, for live sessions; `[]` otherwise. See [declared](#declared). |
| `claims` | array | What it has reserved (`orch claim`, the MCP tool `claim`): its claims that still hold, oldest first, for live sessions; `[]` otherwise. See [claims](#claims). |

A cloud branch, in [`cloud`](#cloud), has the same fields, read this way: `worktree` is `""` (it has none here), `branch` is `<remote>/<branch>`, `base_commit` is its merge base with the remote's default branch, `last_event` and `seconds_since_last_event` are its last commit's time, `turn` is `""`, and `declared` and `claims` are `[]`.

### `changes`

| Field | Type | Meaning |
| --- | --- | --- |
| `files` | object | `{path: status}`, `status` being `A` (added), `M` (modified) or `D` (deleted). |
| `unattributed` | array of paths | Files changed in a worktree two sessions share that neither session's edits name, so neither is said to have changed them. |
| `symbols` | array | The functions and classes it added, removed, moved or modified, for Python; with TypeScript contracts on (`orch ts on`), also the exports of its TypeScript modules that changed. See [symbol](#symbol). |
| `not_compared` | object | `{path: why}` for its changed Python files whose functions were not compared this time, `why` being `does not parse` (a syntax error its base commit does not have, as in a file caught mid-edit), `too large` (over 2,000,000 bytes) or `not readable`. Until it reads whole again, such a file counts as the session's copy last showed it, else as its base commit has it, so none of its functions looks removed. `{}` when every file was compared. |

### Symbol

| Field | Type | Meaning |
| --- | --- | --- |
| `symbol` | string | The function or class (for a move, where it is now). |
| `kind` | string | `added`, `removed`, `moved` or `modified`. |
| `change` | string | What of it changed: `signature`, `body`, `both`, or `""`. |
| `contract` | string | How its signature change rates: `breaking`, `review`, `compatible` or `none`. |
| `reasons` | array of strings | Why, one reason each, for example `parameter currency removed`. |
| `file` | string (path) | The file that holds it. |
| `lines` | array of 2 integers | Its first and last line in the session's copy; in the base commit for a removal. |
| `moved_from` | string | For a move, where it was; `""` otherwise. |
| `language` | string | `python`, or `typescript` for an export of a TypeScript module (`path::name`). |
| `heuristic` | boolean | TypeScript: `true` when the export was read from source (the fallback), not from the compiler's declarations. `false` for Python. |

### `declared`

| Field | Type | Meaning |
| --- | --- | --- |
| `paths` | array of paths | The files the agent said it is about to change. |
| `symbols` | array of strings | The functions and classes it named: `path::name`, or a bare name when it named several files. |
| `task` | string | The agent's own short line about the task; `""` when it gave none. Kept on this machine, and never put into another session's note. |
| `since` | string (time) | When it declared. |
| `until` | string (time) | When the declaration stops holding: `declare_minutes` later (120 by default, in `config.json`). |

### `claims`

A claim reserves a file, a folder or a Python function or class for one session: another session is told before it edits there, and a hard claim makes that edit wait for its user's answer where the agent can ask. A claim ends when it expires, when it is released (`orch release`, the MCP tool `release`), or when its session ends.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | integer | Its number, which `orch claims` shows and `orch release` takes. |
| `kind` | string | `file`, `area` (a folder: every file in it) or `symbol` (a Python function or class). |
| `target` | string | What it reserves, in canonical form: a file's path, a folder's path (no `/` at the end), or `path::Qualified.name`. |
| `file` | string (path) | For a symbol, the file that defines it; `""` otherwise. |
| `hard` | boolean | Another session's edit there waits for its user's answer, where its agent can ask (Claude Code); other agents' sessions are told only. |
| `since` | string (time) | When it was made. |
| `until` | string (time) | When it expires: the time given (`orch claim --for`), else `claim_minutes` later (120 by default, in `config.json`). |

### Overlap

| Field | Type | Meaning |
| --- | --- | --- |
| `key` | string | A stable key: the same overlap keeps it while it holds, and gets it again if it comes back. |
| `rule` | string | `O1`, `O2`, `O3`, `O4`, `O5`, `O6`, `O7` or `W1` (below). |
| `strength` | string | `strong`, `medium` or `weak`. |
| `symbol` | string | The function or class it is about (for O5, the route's handler, `""` when not known); `""` for O1, W1, and O7 about a path. |
| `file` | string (path) | The file it is about: the file both changed (O1), the function's file (O2, O6, O7), the file that uses the change (O3, O4), the file the route is declared in (O5), the file or folder both declared or claimed (O7); `""` for W1. |
| `changer` | string | O3, O4, O6: the session whose change it is. `""` otherwise. |
| `receiver` | string | O3, O4: the session that must hear first, whose file uses the change. `""` otherwise. |
| `sessions` | array of strings | Every session involved, sorted. |
| `detail` | object | The rule's own fields, below. |

`detail`, by rule:

| Rule | Strength | `detail` |
| --- | --- | --- |
| `O1` the same file | weak | `file` |
| `O2` the same function | medium | `symbol`; `kinds`: `{session id: kind}`, what each did to it |
| `O3` a breaking change used in a file another session is changing | strong | `symbol`; `kind`; `reasons` (those the uses run into, then those that break no call); `moved_to` (where it lives now, for a move, else `""`); `uses`: `[{symbol, lines}]`, the functions in `file` that use it and their lines; `latent`: the receiver is not changing `file` yet, so it hears before its first edit of it. About TypeScript (`orch ts on`) it is `medium`, never strong: each use is a file that imports the export and names it (`symbol` is that file), and the detail adds `language` (`typescript`), `heuristic` (`true`: calls are not resolved), `evidence` (`import`) and `source` (`compiler`, or `source` for the fallback) |
| `O4` a removed function still used there | strong | as O3 |
| `O5` the same HTTP route | strong | `method` and `path`: the route both changed; `framework`: `fastapi`, `flask` or `django`; `changes`: `{session id: {kind, from, to, handler, file, line}}`, what each did to it: `kind` is `added`, `removed` or `changed`; for a change, `to` is the method and path it became, or `from` the one that became it (else `""`); `handler`, `file` and `line` say where the route is declared in that session's copy (in the base commit, for a removal) |
| `O6` callers no session has updated | strong | `symbol`; `kind`; `reasons`; `left`: `[{symbol, file, lines}]`, the callers in the base commit that still use the old form |
| `W1` two sessions with hooks in one worktree | medium | `worktree`: its top |
| `O7` the work two sessions declared or claimed overlaps | medium | `what`: the narrower of the two overlapping items, a path or `path::name`; `kind`: `path` or `symbol`; `how`: `{session id: "declared" or "claimed"}`, how each named it (`claimed` when it did both) |

A latent finding is left out of `overlaps` unless `--all` is given.

### `merge_order`

Worked out from the open findings, the same way every time: when one session is changing a file that uses what another changed in a way that breaks it, or removed (O3, O4, not latent), the other merges first; when each of two sessions uses what the other broke, they merge together, or coordinate.

| Field | Type | Meaning |
| --- | --- | --- |
| `sessions` | array of strings | The two sessions: for a one-way order, the one to merge first, then the other; else sorted. |
| `first` | string | The session to merge first; `""` when the two depend on each other. |
| `symbols` | array of strings | The functions and classes it is about, `path::name`. |
| `line` | string | The advice in a line, as `orch status` and `orch merge-order` print it: sessions named by their branch, else the start of their id. |

### `together`

Opt-in, for each repository. When the rules flag a medium or strong overlap between two sessions (O2 to O5, not latent), the user's command runs on each session's working copy alone and on both merged, in folders of Orchestraty's own outside the repository.

| Field | Type | Meaning |
| --- | --- | --- |
| `on` | boolean | Whether it runs (`orch together on`); `false` by default. |
| `command` | string | The command it runs, as the user set it; `""` when none is set. |
| `timeout` | integer | The seconds each run may take (120 by default). |
| `results` | array | The latest result for each pair of the sessions listed, the latest first. See [together result](#together-result). |

### Together result

| Field | Type | Meaning |
| --- | --- | --- |
| `sessions` | array of strings | The two sessions. |
| `findings` | array of strings | The keys of the findings that asked for the check. |
| `at` | string (time) | When it ran. |
| `command` | string | What ran. |
| `result` | string | `fails together` (each passes alone; together it fails), `passes together`, `fails alone` (at least one fails alone), `conflict` (their changes conflict as text: nothing ran), `timed out` (together, it did not finish in time) or `error` (git could not make a snapshot or the merge). |
| `conflicts` | array of paths | For a conflict, the files in conflict. |
| `alone` | object | `{session id: {"exit_code", "timed_out"}}`, the command on each session's working copy alone; `exit_code` is null for a run that timed out. `{}` for a conflict. |
| `together` | object or null | `{"exit_code", "timed_out"}` on both merged; null for a conflict. |
| `first_failing_line` | string | When they fail together, the line of output that says the most (the last giving an error or an assertion, such as `TypeError: ...`; else the last naming an error or a failure; else the last): the command's own words, cleaned. Shown in `orch status`; a note names only the error and where it happened, never this line. |
| `error` | string | For an error, what git could not do. |

### `typescript`

Opt-in, for each repository. For a session that changes .ts files, the project's TypeScript compiler writes the declarations of its working copy and its base commit into the state folder, and the exported API is compared; without a tsconfig.json or a compiler, or when the project does not compile, the exports are read from the source instead (heuristic).

| Field | Type | Meaning |
| --- | --- | --- |
| `on` | boolean | Whether it runs (`orch ts on`); `false` by default. |
| `timeout` | integer | The seconds each compiler run may take (300 by default). |
| `runs` | array | The latest run for each of the sessions listed. See [TypeScript run](#typescript-run). |

### TypeScript run

| Field | Type | Meaning |
| --- | --- | --- |
| `session` | string | The session. |
| `at` | string (time) | When it ran. |
| `source` | string | `compiler` (both the working copy and the base commit compiled) or `fallback` (the exports were read from the source of the changed files: heuristic). |
| `why` | string | For the fallback, why: no tsconfig.json or compiler, a config with `outFile`, which side took too long, or which side did not compile (no declarations written, or an error about the project itself: its options, its inputs, its tsconfig.json), with that error. `""` otherwise. |
| `head` | object or null | The compiler run on the working copy: `{"exit_code", "timed_out", "seconds", "error"}` (exit code 2: errors in the code, the declarations written all the same); null when the compiler did not run. |
| `base` | object or null | The same for the base commit, checked out into the state folder; null when it did not run. |
| `not_compared` | array of strings | Files of the working copy whose exports were not compared this time (at most 20): a syntax error the base commit does not have, as in a file caught mid-edit. From the compiler, its TS1xxx errors; in the fallback, brackets, template literals or comments left open. |

### `cloud`

| Field | Type | Meaning |
| --- | --- | --- |
| `on` | boolean | Whether the repository's cloud branches are fetched and followed (`orch remote on`). |
| `remote` | string | The remote they are fetched from. |
| `patterns` | array of strings | The branches followed: refspec globs, such as `claude/*`. |
| `agents_told` | boolean | Always `false`: the agents on these branches work elsewhere (level 0), so no note reaches them. |
| `fetch` | object or null | The last fetch, or null before the first. See [fetch](#fetch). |
| `branches` | array | One [session](#session) per branch followed, by name; with `--all`, also the ended ones. |

### `fetch`

| Field | Type | Meaning |
| --- | --- | --- |
| `at` | string (time) | When it ran. |
| `ok` | boolean | Whether it worked. |
| `error` | string | Why not, in git's words without any user name or token; `""` when it worked. |
| `failures` | integer | Failed fetches in a row; each doubles the wait before the next automatic one, up to an hour. |
| `next` | string (time) or null | When the coordinator fetches next. |
| `last_ok` | string (time) or null | The last fetch that worked. |
| `branches` | integer | How many branches matched the patterns at the last fetch that worked. |

### `team`

What team sync does for the repository. It is read locally, from config.json, the record and the shadow repository: no network.

| Field | Type | Meaning |
| --- | --- | --- |
| `on` | boolean | Whether team sync is on for the repository (`orch team on`). |
| `remote` | string | The team remote. |
| `store` | string | Where this machine's records go on it: `ref`, one ref per writer under `refs/orchestraty/`; or `branch`, the branch `orchestraty/records`, once the host refused custom refs. |
| `agents_told` | boolean | Whether teammates' work reaches this machine's agents. `false` in this version. |
| `policy` | object | The policy in force and the effective policy. See [team.policy](#teampolicy). |
| `me` | object or null | Who this machine shares as; null when no git email is set for the repository. See [team.me](#teamme). |
| `sent` | object or null | The last record this machine sent, null before any. See [team.sent](#teamsent). |
| `withheld_24h` | array | What was withheld from this machine's records in the last 24 hours, by rule and field, never a value. See [team.withheld_24h](#teamwithheld_24h). |
| `fetch` | object or null | The last team fetch, null before the first. See [team.fetch](#teamfetch). |
| `writers` | array | Each teammate's record as last read. See [team writer](#team-writer). |
| `sessions` | array | Each teammate's branch followed here, read from its commits. See [team session](#team-session). |

### `team.policy`

| Field | Type | Meaning |
| --- | --- | --- |
| `blob` | string | The first seven hex digits of the policy's blob on the remote's default branch, as last fetched; `""` when none was read. |
| `sync` | boolean | Whether the effective policy lets team sync run. |
| `visibility` | string | `private`, `public`, or `""` when the policy does not say. |
| `share` | object | What the effective policy shares: `claims`, `unpushed` and `activity` (booleans) and `task` (`none`, `ticket` or `confirmed`). |
| `widened` | boolean | Whether the policy in force allows more than the copy the person agreed to; `orch team on` reviews it. |
| `why` | string | Why nothing is shared, or `""`. |
| `fix` | string | What to do about it, or `""`. |

### `team.me`

| Field | Type | Meaning |
| --- | --- | --- |
| `person` | string | The handle this machine shares as: the people file's, or `unlisted-` and eight hex digits. |
| `listed` | boolean | Whether the people file lists the git email. |
| `device` | string | The device id, or `""` before `orch team on` made one. |
| `label` | string | The device's label, or `""`. |

### `team.sent`

| Field | Type | Meaning |
| --- | --- | --- |
| `at` | string (time) | When it was sent, or tried. |
| `ok` | boolean | Whether the remote took it. |
| `error` | string | Why not, or `""`. |
| `seq` | integer | Its sequence number. |
| `sessions` | integer | How many sessions it held. |

### `team.withheld_24h`

| Field | Type | Meaning |
| --- | --- | --- |
| `rule` | string | The rule that withheld it: a secret scanner rule (such as `github-token`), `grammar`, `deny-path` or `deny-word`. |
| `field` | string | The field it was in, such as `sessions[].branch`. |
| `count` | integer | How many times in the last 24 hours. |

### `team.fetch`

| Field | Type | Meaning |
| --- | --- | --- |
| `at` | string (time) | When it ran. |
| `ok` | boolean | Whether it worked. |
| `error` | string | Why not, or `""`. |
| `failures` | integer | Failed fetches in a row; each one doubles the wait, up to an hour. |
| `next` | string (time) or null | When the next is due. |
| `last_ok` | string (time) or null | The last one that worked. |
| `writers` | integer | The writers' records it found. |

### Team session

A teammate's branch, in [`team.sessions`](#team), has the fields of a [session](#session), read this way: `agent` is `team`, `worktree` is `""` (it has none here), `branch` is `<remote>/<branch>`, `base_commit` is its merge base with the remote's default branch, worked out here, `changes` is what its commits changed from there, `started` is when this machine first saw it, `last_event` and `seconds_since_last_event` are when it last saw the branch or the records naming it change, `turn` is `""`, and `declared` and `claims` are `[]`. It adds `team`:

| Field | Type | Meaning |
| --- | --- | --- |
| `people` | array of strings | The teammates whose records name the branch; `[]` for your own other device alone. |
| `own` | boolean | Whether your own work on another device names it. |
| `evidence` | string | `commits`: it is read from the branch's commits. |
| `tip` | string | The branch's commit as last fetched. |
| `last_commit` | string (time) | Its last commit's time, by their clock, never later than now. |
| `as_of` | string (time) | When the newest record naming it was sealed, by their clock, never later than now. |
| `agents` | array of strings | The agent kinds of the sessions on it. |
| `ticket` | string | A ticket their records give it, or `""`. |
| `merged` | string | `""` while it is not merged into the default branch. |
| `contained_in` | array of strings | Your sessions whose copy holds its tip; `[]` in this version. |
| `builds_on` | array of strings | Your sessions whose commits it builds on; `[]` in this version. |
| `claims` | array | Teammates' claims on it; `[]` in this version. |
| `line` | string | The task line its author confirmed. Only in `orch status --json --with-lines`: never in status.json or in the MCP tool's answer. |

A session id in `overlaps` or `merge_order` is one of `sessions`, `cloud.branches` or `team.sessions`.

### Team writer

| Field | Type | Meaning |
| --- | --- | --- |
| `person` | string | The teammate's handle, from their record (from the ref's name when the record was dropped). |
| `device` | string | Their device id. |
| `label` | string | Their device's label, or `""`. |
| `written` | string (time) | When they sealed the record, by their clock. |
| `verdict` | string | `admitted`; `dropped`, with its reason in `why`; or `ignored`, when one read's limits (64 writers, 4 devices a person, 4 MiB) left it unread. |
| `why` | string | For a dropped record, its reason (`writer-mismatch`, `unlisted`, `rollback`, `too-large`, `bad-json`, `duplicate-key`, `bad-structure`, `newer-format`, `bad-grammar:<field>` or `not-a-record`); for an admitted one, what was dropped from it, counted. |
| `entries` | array | One object a session it shares. See [team writer entry](#team-writer-entry). |

### Team writer entry

| Field | Type | Meaning |
| --- | --- | --- |
| `agent` | string | The agent kind. |
| `state` | string | `live` or `ended`. |
| `branch` | string | The branch. |
| `pushed` | boolean | Whether the remote has the branch. |
| `ticket` | string | Its ticket, or `""`. |
| `line` | string | The task line its author confirmed. Only in `orch status --json --with-lines`: never in status.json or in the MCP tool's answer. |

## The MCP tool `status`

The MCP server's `status` tool returns, as its structured content:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | integer | As above. |
| `coordinator` | object | As above. |
| `repository` | object | The caller's repository, as above, with these differences, so that no session's words reach another agent: every name in it (session ids, branches, worktree folders, changed files and functions, claims, declared paths and functions, findings' keys, symbols, files and details, merge-order entries, cloud branches) is cleaned as notes clean them, a folder keeping its separators as `/`; another session's declarations have no `task`; an O5 overlap's path, in its key, its `detail` and each session's `to` and `from`, is shown as notes show it (left out when it is not made of a path's characters and parameters, a parameter in braces); a together result's `first_failing_line` holds only the error's name and where it happened (`TypeError at tests/test_a.py::test_x`), and its `error`, a TypeScript run's `why` and `error` and a cloud fetch's `error` keep only Orchestraty's own words, before the first `: `; in `team`, teammates' handles, devices, branches and tickets are cleaned as notes clean them, an entry never has a `line`, and the policy's `why` and `fix` and the last send's `error` keep only Orchestraty's own words. |
| `you` | string or null | The caller's session id, when the server found it. |

## `orch team preview --json`

The record this machine would send its teammates for the repository now, exactly as it would be sent, and what was withheld from it. Nothing is stored or sent.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | integer | 1. |
| `bytes` | string or null | The record's canonical bytes (ASCII JSON and a newline), or null when it is held back. |
| `record` | object or null | The same record, parsed: format 1 of `record.json`. |
| `withheld` | list | One object a withheld item: its `field` (such as `sessions[].branch`) and the `rule` that withheld it (a scanner rule such as `github-token`, `grammar`, `deny-path` or `deny-word`). Never the value. |
| `held` | string | Why the whole record is held back (`final scan` when the scan of its bytes found what an earlier step should have withheld), or "" when it is not. |
