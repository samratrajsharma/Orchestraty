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

Times are UTC, as `YYYY-MM-DDTHH:MM:SSZ`. Paths of files in a repository are relative to the worktree's top, with `/` on every system. Folders (`worktree`, `common_dir`) are absolute, in the system's own form. A function or class is named `path::Qualified.name`, for example `billing/charge.py::Invoice.total`.

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
| `cloud` | object | Its cloud agents' branches (`orch remote`): the settings, the last fetch, and the branches followed. See [cloud](#cloud). |

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
| `symbols` | array | The functions and classes it added, removed, moved or modified, for Python. See [symbol](#symbol). |

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
| `rule` | string | `O1`, `O2`, `O3`, `O4`, `O6`, `O7` or `W1` (below). |
| `strength` | string | `strong`, `medium` or `weak`. |
| `symbol` | string | The function or class it is about; `""` for O1, W1, and O7 about a path. |
| `file` | string (path) | The file it is about: the file both changed (O1), the function's file (O2, O6, O7), the file that uses the change (O3, O4), the file or folder both declared or claimed (O7); `""` for W1. |
| `changer` | string | O3, O4, O6: the session whose change it is. `""` otherwise. |
| `receiver` | string | O3, O4: the session that must hear first, whose file uses the change. `""` otherwise. |
| `sessions` | array of strings | Every session involved, sorted. |
| `detail` | object | The rule's own fields, below. |

`detail`, by rule:

| Rule | Strength | `detail` |
| --- | --- | --- |
| `O1` the same file | weak | `file` |
| `O2` the same function | medium | `symbol`; `kinds`: `{session id: kind}`, what each did to it |
| `O3` a breaking change used in a file another session is changing | strong | `symbol`; `kind`; `reasons` (those the uses run into, then those that break no call); `moved_to` (where it lives now, for a move, else `""`); `uses`: `[{symbol, lines}]`, the functions in `file` that use it and their lines; `latent`: the receiver is not changing `file` yet, so it hears before its first edit of it |
| `O4` a removed function still used there | strong | as O3 |
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

Opt-in, for each repository. When the rules flag a medium or strong overlap between two sessions (O2, O3, O4, not latent), the user's command runs on each session's working copy alone and on both merged, in folders of Orchestraty's own outside the repository.

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
| `first_failing_line` | string | When they fail together, the line of output that says the most (the last naming an error or a failure, else the last): the command's own words, cleaned. |
| `error` | string | For an error, what git could not do. |

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

## The MCP tool `status`

The MCP server's `status` tool returns, as its structured content:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | integer | As above. |
| `coordinator` | object | As above. |
| `repository` | object | The caller's repository, as above, with one difference: another session's declarations have no `task`, so no session's words reach another agent. |
| `you` | string or null | The caller's session id, when the server found it. |
