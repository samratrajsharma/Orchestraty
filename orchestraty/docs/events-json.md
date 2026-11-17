# `orch events --json`

What changes in the record, as a stream of JSON lines for other tools: sessions started and ended, findings opened and resolved, notes written, delivered and withdrawn, declarations made, and claims made and ended. It reads the record without changing it; there is no server and no socket.

```bash
orch events --json                  # every change the record has logged, then end (the same as --once)
orch events --json --since 2h       # those from two hours ago on
orch events --follow --json         # the changes from now on, as they come (polls about once a second)
orch events --follow --since 10m    # the last ten minutes, then the new ones, as text
```

`--since` takes an ISO 8601 time (`2026-10-22T09:30:00Z` in UTC, or without `Z` in local time, or just a date), seconds since the epoch, or a time ago: `30s`, `10m`, `2h`, `1d`. Without `--json`, each event is one line of text: its local time, its kind and what it is about. The JSON is ASCII: other characters are written as `\uXXXX` escapes, so it reads the same whatever encoding a reader assumes.

The record's own write methods log each change as it happens (record schema 3; claims from schema 4), so the stream is in the order the record learned of the changes. A record brought up from an older schema logged the history it already had, in time order, when it was upgraded; withdrawals from before then had no time and are not in it.

## Schema 1

Each line is one JSON object, and its `schema` field gives the schema number. This page describes schema 1. The promise is the one [`orch status --json`](status-json.md) makes: within one schema number, fields are only added, never removed, renamed or changed in type or meaning, so a reader should ignore the fields it does not know.

Every event has:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | integer | `1` |
| `id` | integer | The change's place in the log; it only grows. A reader that keeps the last `id` it saw misses nothing. |
| `event` | string | What happened, one of the kinds below. |
| `time` | string | When, in UTC, as `YYYY-MM-DDTHH:MM:SSZ`. |
| `ts` | integer | The same, in nanoseconds since the epoch. |
| `session` | string or null | The session it is about: the one that started, ended, declared or claimed, or the one a note is for. Null for findings. |

What each kind adds. The session, finding or note is described as the record has it when the event is read; a field is left out when the record no longer has what it describes.

| `event` | Its time | Fields it adds |
| --- | --- | --- |
| `session_started` | The session's first event (or its first after it had ended) | `agent`, `worktree`, `branch`, as in [`orch status --json`](status-json.md#session) |
| `session_ended` | Its last event | `agent`, `worktree`, `branch`; `ended`: why, as the session's `note` says it, while the session has not come back |
| `finding_opened` | When the coordinator opened it, or opened it again | `finding` (its key), `rule`, `strength`, `symbol`, `file`, `sessions`, `changer`, `receiver`, as in [an overlap](status-json.md#overlap); `latent` (boolean) |
| `finding_resolved` | When it stopped holding | as `finding_opened` |
| `note_written` | When the note was put in its slot for the agent's hook | `finding` (the key it is about), `note` (its id), `note_kind` (`edit`: before an edit of a file, or of any file in a claimed folder; `next`: with the next tool result; `stop`: at the end of a turn; `wake`: wakes a waiting session), `rule`; for a note about a claim, `claim` (its number) instead of `rule`, and `finding` is `claim|<number>` |
| `note_delivered` | When the hook handed it to the agent, or another note on the same finding was | as `note_written` |
| `note_withdrawn` | When it was taken back, before the agent saw it | as `note_written` |
| `declared` | When the agent declared | `paths`, `symbols`, `until`, as in [declared](status-json.md#declared) |
| `claimed` | When the claim was made | `claim` (its number); `claim_kind`, `target`, `file`, `hard`, `until`, as `kind`, `target`, `file`, `hard` and `until` in [claims](status-json.md#claims) |
| `claim_ended` | When it was released, expired or its session ended | as `claimed`; `ended`: why, `released`, `expired` or `session ended` |

A note's text is never part of an event, and neither is a declaration's task line.
