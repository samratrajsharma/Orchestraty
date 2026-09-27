# Capture

Status: throwaway (Phase 0). Not part of the product.

Records coding-agent hook events and git hook firings as one short line each, so that capture can be measured: how much time a hook adds, and whether any event is lost. It stores no prompt text, no tool input and no file content, only a few named fields and the payload's size. It also hands an agent ready-made context before it edits a file (the pre-edit heads-up).

## Files

| File | What it is |
| --- | --- |
| `capture_shim.py` | The hook shim. It reads one hook payload from stdin and appends one line to `<spool>/<agent>/<session_id>.jsonl`. It runs as `python -S -I` and uses built-in modules only (no `json`). |
| `capture_hooks.py` | Puts the capture hooks (and, with `--headsup`, the heads-up hook) into the isolated Claude Code profile under the phase-0 folder, and nowhere else. |
| `capture_codex.py` | The same for the isolated Codex profile (`CODEX_HOME`): writes its `hooks.json`. |
| `capture_githook.py` | The git-hook recorder: one line per git hook firing, appended with the shim's atomic append. |
| `capture_githooks.py` | Installs the git-hook chain into a scratch repository, so existing hooks (husky, lefthook, pre-commit) still run, and removes it again. |
| `capture_headsup.py` | The pre-edit heads-up hook: returns the ready-made context for the file about to be edited, or nothing. |
| `capture_headsup_writer.py` | Writes the ready-made heads-up files, and times the hook's read (`bench`). |
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
- **`--headsup`** also installs a PreToolUse hook for Edit, Write and MultiEdit that runs `capture_headsup.py`. It is never async, because its output has to reach the agent.
- **Logging in.** After writing, it prints the one-line command to log in to the isolated profile.

## Codex

```
python capture_codex.py [--root <phase-0 folder>] [--python <absolute python>] [--async] [--windows-shell powershell|cmd]
```

- **What it writes:** `profiles/codex/hooks.json` (merged into any hooks already there, and idempotent), the shim copy, and `spool/codex/`, with the same checks as `capture_hooks.py`.
- **Events:** SessionStart, SessionEnd, UserPromptSubmit, PostToolUse, Stop and SubagentStop. Codex has no PostToolUseFailure, and allows at most 3 seconds for SessionEnd hooks.
- **Hook form.** Codex runs every hook `command` through a shell; it has no exec form. On Windows that is PowerShell by default (the command starts with PowerShell's call operator `&`), or cmd with `--windows-shell cmd`; elsewhere it is `sh -c`. The payload arrives on stdin either way.
- **Trust.** Codex runs a new user hook only after the person has reviewed and trusted it (`/hooks`). The generator never writes that trust state.

## Git hooks

```
python capture_githooks.py install   <repo> [--root <phase-0 folder>] [--python <absolute python>]
python capture_githooks.py uninstall <repo> [--root <phase-0 folder>]
```

- **Scratch repositories only:** the repository must sit under `<root>/repos`, with no link on the way.
- **The chain.** Git runs the hooks of one folder: `core.hooksPath` when set (husky sets `.husky/_`), else `.git/hooks` (where lefthook and pre-commit install theirs). The installer points `core.hooksPath` at a new folder inside the repository's git folder, `orch-capture-hooks`, with one small sh script per hook:
  - post-commit, pre-push, post-checkout, post-merge and post-rewrite first run `capture_githook.py`, with its output discarded and its exit code ignored, then run the hook that was there before with the same arguments and stdin, and exit with that hook's code. A failing pre-push hook therefore still stops the push;
  - every other hook the earlier folder had only runs the earlier hook.
- **What is recorded** (`<spool>/git/<date>.jsonl`): the hook, the working tree, the branch HEAD names (or the detached commit id), commit ids and flags from the arguments, and the ref lines of pre-push and post-rewrite. A remote's URL is never kept, because it can carry a user name or a token.
- **Uninstall** puts the earlier `core.hooksPath` back and removes the chain folder.

## The pre-edit heads-up

- **The hook** (`capture_headsup.py`, run as `python -S -I`) takes the payload's `file_path`, and if `<root>/headsup/<key>.json` exists it copies that file's bytes to stdout unchanged: a ready-made `hookSpecificOutput` with `additionalContext`. It builds and parses no JSON. On a miss or any failure it prints nothing, writes nothing and exits 0.
- **Keys.** Separators are unified, and on Windows and macOS letter case is ignored. Other characters are escaped reversibly, so two different paths never share a key. Long keys keep their start and end plus a 64-bit hash of the whole.
- **The writer** (`capture_headsup_writer.py put`) writes the complete output to a temporary name, moves it into place, and reads it back once. The first read of a freshly written file is slow (probably on-access scanning), and the read-back moves that cost off the hook's path. The text is cut to 9,500 characters.
- **`bench`** times the hook's read in a fresh process in three cases: the first read after a rewrite without the read-back, the first read after the read-back, and steady state. It prints numbers only.

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
| Codex's `hooks.json` uses only what Codex accepts, and the generated command runs in Codex's shell | `test_hooks_json_uses_only_what_codex_accepts`, `test_the_generated_command_runs_in_codexs_shell_and_writes_one_spool_line` |
| The git-hook chain records all five hooks, prints nothing, never blocks, and leaves the earlier hook's exit code in charge | `test_all_five_hooks_are_recorded_and_ours_prints_nothing`, `test_a_failing_recorder_never_blocks_and_prints_nothing`, `test_the_earlier_hooks_still_run_and_their_exit_code_decides` (simulated husky, lefthook and pre-commit), and `test_real_husky_...`, `test_real_lefthook_...`, `test_real_pre_commit_...` when those tools are installed |
| A remote's URL never reaches the spool | `test_the_recorder_keeps_ids_and_flags_but_never_a_remote_url` |
| Git hooks go only into scratch repositories under the phase-0 folder | `test_only_scratch_repositories_under_the_phase0_folder_are_accepted` |
| The heads-up hook returns the ready-made bytes on a hit, and nothing on a miss or bad input | `test_a_hit_returns_the_ready_made_bytes_unchanged`, `test_a_miss_prints_nothing_and_writes_nothing` |
| Different paths never share a heads-up | `test_different_paths_never_share_a_key` |
| The writer replaces atomically and reads each file back once | `test_the_writer_replaces_atomically_reads_back_once_and_cuts_long_text` |

## Limits

- **Fields are found, not parsed.** The first `"key": "..."` in the payload is used. A key inside a string value cannot match, because its quotes are escaped there. A nested object with a key of the same name, placed before the top-level one, would be taken instead.
- **POSIX appends** rely on the local file system keeping one `write` of a short line whole under `O_APPEND`. Local file systems do (the test runs on every CI job); network file systems may not, and the spool is never on one.
- **A hook manager that reinstalls itself replaces the chain.** For example, `npm install` running husky, or `lefthook install`, resets `core.hooksPath` or the hooks folder. Install the chain after the managers.
- **Codex on Windows runs hooks through PowerShell,** whose start-up alone costs far more than Python's.
- **The heads-up works for Claude Code's Edit, Write and MultiEdit,** which name the file in `file_path`. Codex edits files with `apply_patch`, whose input carries a patch, not a `file_path`.
- **The recorder reads HEAD from the HEAD file only.** It records the branch name, not the commit a branch points to, so linking to commits uses the times and the ref lines.
