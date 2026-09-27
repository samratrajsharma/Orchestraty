# Capture

Status: throwaway (Phase 0). Not part of the product.

Records coding-agent hook events as one short line each, so that capture can be measured: how much time a hook adds, and whether any event is lost. It stores no prompt text, no tool input and no file content, only a few named fields and the payload's size.

## Files

| File | What it is |
| --- | --- |
| `capture_shim.py` | The hook shim. It reads one hook payload from stdin and appends one line to `<spool>/<agent>/<session_id>.jsonl`. It runs as `python -S -I` and uses built-in modules only (no `json`). |
| `capture_hooks.py` | Puts the capture hooks into the isolated Claude Code profile under the phase-0 folder, and nowhere else. |
| `capture_latency.py` | The latency harness: the time a hook command adds over a no-op of the same form. It prints numbers only. |
| `test_capture_*.py` | Tests. Every payload is synthetic. |

## The shim

- **Fields kept:** `session_id`, `hook_event_name`, `tool_name`, `tool_use_id`, `cwd` and `transcript_path`, plus `ts`, `pid`, `agent`, `event` and `len` (the payload's size in bytes). They are found with `bytes.find`, not parsed. A field whose value is not a valid JSON string (a bad escape, a control character, bad UTF-8, over 4 KB) is left out, so every line is valid JSON.
- **Atomic append.** On Windows the file is opened with `FILE_APPEND_DATA` only, so the kernel puts every write at the end even with several writers; plain `O_APPEND` through the C runtime lost events with 5 writers. On POSIX it is `O_APPEND` with one `write` per line.
- **Silent and fail-open.** It prints nothing and always exits 0. An internal failure appends one `{"shim_error": "<exception class>"}` line instead (to the session's file, or `shim_errors.jsonl`), so failures show up in the data.
- **Where it writes.** The spool folder comes from the hook's arguments, because Claude Code does not pass `CLAUDE_CONFIG_DIR` to hooks. The agent name and session id are reduced to letters, digits, `-` and `_`, so no payload can move the file outside the spool.

## The hook-config generator

```
python capture_hooks.py [--root <phase-0 folder>] [--python <absolute python>] [--async]
```

- **Default folder:** `%LOCALAPPDATA%\orch-phase0` on Windows, and `~/.local/state/orch-phase0` on WSL2 and macOS. The folder must be named `orch-phase0`.
- **What it writes:** `profiles/claude/settings.json` (merged into any settings already there, and idempotent), a copy of the shim in `shim/`, and the folder `spool/claude/`.
- **What it refuses:** any target outside `<root>/profiles`, `<root>/shim` and `<root>/spool`, and any path that goes through a link. So it never writes to the daily profile (`~/.claude`), nor to a repository's `.claude/` or `.codex/`.
- **Hook form.** Every hook is in exec form, with no shell: `command` is the absolute path to Python (a real `.exe` on Windows), and `args` is `["-S", "-I", <shim>, <spool>, "claude", <event>]`.
- **Events:** SessionStart, SessionEnd, UserPromptSubmit, PostToolUse, PostToolUseFailure, Stop and SubagentStop. `--async` marks them all async, because loss is measured with async on and off.
- **Logging in.** After writing, it prints the one-line command to log in to the isolated profile.

## The latency harness

```
python capture_latency.py --cmd shim='["<abs python>", "-S", "-I", "<shim>", "<spool>", "bench"]' \
                          --cmd noop='["/usr/bin/true"]' --baseline noop [--payloads recorded.jsonl]
```

- **Rounds:** 5 replayers in parallel by default, with each round's order shuffled per replayer. There are 20 warm-up rounds, then N=1,000 measured rounds per replayer.
- **Payloads:** without `--payloads`, it replays a synthetic 2 KB PreToolUse-shaped payload. Recorded payloads come from the scripted capture runs and are kept outside the repository.
- **Output:** the median, p90, p99 and max for every command, and the time each adds over the baseline. Percentiles use linear interpolation between the closest ranks.
- **Cold runs:** the first run of each command in each replayer is reported apart.
- **Checks:** non-zero exits and any printed output are counted.
- **No pass marks:** it prints numbers only.

## Safety properties, and the tests that check them

| Property | Test |
| --- | --- |
| Exit 0, empty stdout and stderr, and exactly one valid JSON line, for any input | `test_bad_input_is_silent_exits_zero_and_still_gives_one_valid_line`, `test_a_large_payload_is_fine`, `test_one_valid_line_per_event_with_only_the_named_fields` |
| No payload content (prompt, tool input or output) is stored | `test_one_valid_line_per_event_with_only_the_named_fields` |
| An internal failure leaves a `shim_error` line and stays silent | `test_an_internal_failure_leaves_one_shim_error_line` |
| Payload names cannot move the file outside the spool | `test_names_from_the_payload_cannot_leave_the_spool` |
| 5 processes × 1,000 appends to one file lose nothing and interleave nothing | `test_five_processes_append_a_thousand_lines_each_without_loss_or_interleaving`, `test_parallel_hook_runs_into_one_session_file_lose_nothing` |
| Built-in modules only, never `json` | `test_built_in_modules_only_and_no_json` |
| The generator writes only inside the phase-0 folder's profiles, shim and spool | `test_it_writes_only_the_profile_settings_the_shim_and_the_spool_folder`, `test_targets_outside_the_profiles_shim_and_spool_folders_are_refused`, `test_a_link_inside_the_phase0_folder_is_refused`, `test_a_root_with_another_name_is_refused_and_nothing_is_written` |
| The harness's percentiles are right, and a no-op against itself adds about 0 ms | `test_percentiles_match_hand_computed_values`, `test_a_no_op_measured_against_itself_adds_about_nothing` |

## Limits

- **Fields are found, not parsed.** The first `"key": "..."` in the payload is used. A key inside a string value cannot match, because its quotes are escaped there. A nested object with a key of the same name, placed before the top-level one, would be taken instead.
- **POSIX appends** rely on the local file system keeping one `write` of a short line whole under `O_APPEND`. Local file systems do (the test runs on every CI job); network file systems may not, and the spool is never on one.
- **Git hooks, Codex hooks and the pre-edit heads-up read** are not part of this change.
