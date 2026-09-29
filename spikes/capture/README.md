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
| `capture_count.py` | The loss and coverage counter: compares the spool with the agents' own session records. It prints numbers only. |
| `capture_link.py` | The minimal linker: links each session to its commits, and checks the links against a ground truth. It prints ids and numbers only. |
| `capture_refprobe.py` | The custom-ref probe: can a remote keep `refs/orchestraty/<person>/<device>`, and if not, a dedicated branch? |
| `test_capture_*.py` | Tests. Every payload is synthetic. |

## The shim

- **Fields kept:** `session_id`, `hook_event_name`, `tool_name`, `tool_use_id`, `cwd` and `transcript_path`, plus `ts`, `pid`, `agent`, `event` and `len` (the payload's size in bytes). They are found with `bytes.find`, not parsed. A field whose value is not a valid JSON string (a bad escape, a control character, bad UTF-8, over 4 KB) is left out, so every line is valid JSON.
- **Which occurrence.** Set from payloads recorded from Claude Code 2.1.283. `session_id`, `transcript_path`, `cwd`, `hook_event_name` and `tool_name` always come before `tool_input` and `tool_response`, so their first occurrence is the top-level one. `tool_use_id` comes after them, so it is taken from its last occurrence: a nested key of that name in a tool's input or output would come first. `tool_name` and `tool_use_id` are read only for tool events (PreToolUse, PostToolUse, PostToolUseFailure).
- **Atomic append.** On Windows the file is opened with `FILE_APPEND_DATA` only, so the kernel puts every write at the end even with several writers; plain `O_APPEND` through the C runtime lost events with 5 writers. On POSIX it is `O_APPEND` with one `write` per line.
- **Silent and fail-open.** It prints nothing and always exits 0. An internal failure appends one `{"shim_error": "<exception class>"}` line instead (to the session's file, or `shim_errors.jsonl`), so failures show up in the data.
- **Where it writes.** The spool folder comes from the hook's arguments, because Claude Code does not pass `CLAUDE_CONFIG_DIR` to hooks. The agent name and session id are reduced to letters, digits, `-` and `_`, so no payload can move the file outside the spool.

## The hook-config generator

```
python capture_hooks.py [--root <phase-0 folder>] [--python <absolute python>] [--async]
```

- **Default folder:** `%USERPROFILE%\orch-phase0` on Windows, and `~/.local/state/orch-phase0` on WSL2 and macOS. The folder must be named `orch-phase0`. On Windows it stays out of AppData: a packaged app's writes under AppData go to its own private copy, which other programs don't see, so the folder would split in two.
- **What it writes:** `profiles/claude/settings.json` (merged into any settings already there, and idempotent), a copy of the shim in `shim/`, and the folder `spool/claude/`.
- **What it refuses:** any target outside `<root>/profiles`, `<root>/shim` and `<root>/spool`, and any path that goes through a link. So it never writes to the daily profile (`~/.claude`), nor to a repository's `.claude/` or `.codex/`.
- **Hook form.** Every hook is in exec form, with no shell: `command` is the absolute path to Python (a real `.exe` on Windows), and `args` is `["-S", "-I", <shim>, <spool>, "claude", <event>]`.
- **Events:** SessionStart, SessionEnd, UserPromptSubmit, PreToolUse (Edit, Write and MultiEdit only, the calls the heads-up answers), PostToolUse, PostToolUseFailure, Stop, SubagentStop and StopFailure (a turn that ended in an API error fires StopFailure instead of Stop). `--async` marks them all async, because loss is measured with async on and off.
- **`--headsup`** also installs a PreToolUse hook for Edit, Write and MultiEdit that runs `capture_headsup.py`, after the capture hook. It is never async, because its output has to reach the agent.
- **Logging in.** After writing, it prints one line each for PowerShell and bash that start the pinned Claude Code (found under `<root>/tools/claude`, or given with `--claude`) with `CLAUDE_CONFIG_DIR` set, and leave nothing set afterwards. It never uses the daily `claude` on PATH; with no pinned copy it says so and prints no login line.

## Codex

```
python capture_codex.py [--root <phase-0 folder>] [--python <absolute python>] [--async] [--windows-shell powershell|cmd]
```

- **What it writes:** `profiles/codex/hooks.json` (merged into any hooks already there, and idempotent), the shim copy, and `spool/codex/`, with the same checks as `capture_hooks.py`.
- **Events:** SessionStart, SessionEnd, UserPromptSubmit, PostToolUse, Stop and SubagentStop. Codex has no PostToolUseFailure, and allows at most 3 seconds for SessionEnd hooks.
- **Hook form.** Codex runs every hook `command` through a shell; it has no exec form. On Windows that is PowerShell by default (the command starts with PowerShell's call operator `&`), or cmd with `--windows-shell cmd`; elsewhere it is `sh -c`. The payload arrives on stdin either way. cmd has no quoting that is safe for every path, so with `--windows-shell cmd` a path holding any of `& | < > ^ % ! "` is refused.
- **Trust.** Codex runs a new user hook only after the person has reviewed and trusted it (`/hooks`). The generator never writes that trust state, and nothing here uses the flag that skips the review.
- **Logging in.** Like `capture_hooks.py`, it prints the login lines for the pinned Codex (under `<root>/tools/codex`, or `--codex`) with `CODEX_HOME` set.

## Git hooks

```
python capture_githooks.py install   <repo> [--root <phase-0 folder>] [--python <absolute python>]
python capture_githooks.py uninstall <repo> [--root <phase-0 folder>]
```

- **Scratch repositories only:** the repository must sit under `<root>/repos`, with no link on the way.
- **The chain.** Git runs the hooks of one folder: `core.hooksPath` when set (husky sets `.husky/_`), else `.git/hooks` (where lefthook and pre-commit install theirs). The installer points `core.hooksPath` at a new folder inside the repository's git folder, `orch-capture-hooks`, with one small sh script per hook:
  - post-commit, pre-push, post-checkout, post-merge and post-rewrite first run `capture_githook.py`, with its output discarded and its exit code ignored, then run the hook that was there before with the same arguments and stdin, and exit with that hook's code. A failing pre-push hook therefore still stops the push;
  - every other hook the earlier folder had only runs the earlier hook.
- **What is recorded** (`<spool>/git/<date>.jsonl`): the hook, the working tree, the branch HEAD names (or the detached commit id), commit ids and flags from the arguments, and the ref lines of pre-push and post-rewrite. For post-commit and post-merge, which get no commit id as an argument, it also records the commit HEAD now points at, read from the branch's ref file or from `packed-refs` (never by running git). Only a commit id is ever copied from those files. A remote's URL is never kept, because it can carry a user name or a token.
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

## The loss and coverage counter

```
python capture_count.py --spool <root>/spool --manifest <run manifest.json> \
                        [--claude-projects <profile>/projects] [--codex-sessions <CODEX_HOME>/sessions] \
                        [--timeout-minutes 30] [--now <unix seconds>] [--json <out file>]
```

- **What it reads.** From the spool, its own lines. From each session record (a Claude Code transcript, with its subagent files, or a Codex rollout file) only ids, entry types, tool names, timestamps and error flags, and whether a tool result is a permission denial, which it recognises by a fixed phrase. No prompt, tool input or output, or file content reaches its output.
- **The manifest** lists every session of the run with its agent, id, kind (`scripted`, `parallel`, `interactive`, `cockpit`, `killed` or `noop`), environment and async setting. The harness writes it.
- **Expected counts** follow T1 section 6.3: one SessionStart (plus one per compaction) and one SessionEnd; one UserPromptSubmit per prompt; one PreToolUse per Edit, Write or MultiEdit call; one Post event per tool call, less permission-denied calls and calls rejected at input validation; one SubagentStop per subagent run that was not denied.
- **Turns.** A prompt opens a turn. A turn that ends normally expects a Stop; one that ends in an API error expects a StopFailure instead; one the user interrupts expects neither. Interrupt markers, compact summaries and slash-command echoes are user entries in the transcript but not prompts.
- **Rows.** Lost and extra events per environment, session kind, async setting and event. Killed sessions get their own row: missing SessionEnd and Stop, and whether each was closed by a SessionEnd, by the timeout rule, is still open, or was never captured. A kill forgives a missing Stop only for a turn that was still running at the kill. Permission-denied calls, counted only when the record shows the denial, get their own row too. Coverage is records against captured sessions per kind, leaving out the `noop` sessions. The count of `shim_error` lines comes last.

## The linker

```
python capture_link.py --spool <root>/spool [--truth <ground truth.json>] [--pr 12=feature-x ...] \
                       [--grace-seconds 120] [--merge-seconds 10] [--json <out file>]
```

- **Commits** come from the git lines: post-commit (with its commit id), the new commits of a rebase (post-rewrite), and merge commits (post-merge, when the commit has two parents and was made at that moment). A commit that an amend or a rebase replaced is dropped, and the new one keeps the original's session. The replays of an aborted rebase (made on a detached HEAD, kept by nothing) are listed as unreachable. Each git line carries its repository (`repo`), so a commit keeps it after its worktree is removed.
- **Link rule.** A commit goes to the session in the same working tree that was open at that time, or had ended at most `--grace-seconds` before it (a commit the harness makes right after the session), on a branch the session started on or was seen on while open. An open session wins over one in its grace period. Working trees are keyed by the folder git runs the hooks from, so a nested worktree keeps its own sessions after it is removed.
- **Squash merges.** A commit whose subject ends in `(#N)` is linked through pull request N only: to the sessions linked to commits on the PR's branch. The branch is given with `--pr N=branch`, or found by tree (the squash commit's tree equals a linked commit's tree). A tree that matches more than one branch is reported as ambiguous and linked to nothing. Only commits in the squash commit's own repository, made before it, count. A commit ending in `(#N)` with no PR behind it is linked as an ordinary commit.
- **Link rate.** With `--truth`, each session's linked commits are compared with its ground-truth commits: linked right, wrong links and missed commits, split by who made the commits (the agent or the harness). Subjects and trees are read with read-only `git log`; they never reach the output.

## The custom-ref probe

```
python capture_refprobe.py --repo <root>/repos/<clone> --remote <name, path or URL> --person <p> --device <d> \
                           [--allow-network] [--also-fallback] [--json <out file>]
```

- **Steps.** It makes a probe commit with `commit-tree` (one file holding a random nonce, under a probe identity, never the user's). If the ref is already on the remote it reports `exists` and stops: it never overwrites or deletes a ref it did not push. Otherwise it pushes the commit to `refs/orchestraty/<person>/<device>` with `--no-verify` (the clone's own pre-push hooks are not what is probed), checks `ls-remote`, fetches it into a new empty repository as a second device would and checks the nonce, then deletes the remote ref, checks it is gone, and removes the temporary repository. The deletion runs whatever fails after the push. If the remote refuses the ref (or with `--also-fallback`), the dedicated branch `refs/heads/orchestraty/<person>/<device>` is probed the same way.
- **Network.** A local remote must be a bare repository. Any other remote, including `file://host/...` and `\\server\share` paths, is a network remote and is refused unless `--allow-network` is given. Every network step of T1 is run by the owner, with that flag. The tests use a local bare repository, with a `pre-receive` hook that refuses `refs/orchestraty/*` for the fallback.
- **Scratch clones only:** under `<root>/repos`, like the git hooks.

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
| The default folder is outside AppData on Windows; the login line starts the pinned program and leaves nothing set | `test_the_default_root_is_outside_appdata_on_windows`, `test_the_login_line_uses_the_pinned_program_and_leaves_nothing_set`, `test_the_command_line_prints_the_pinned_login_line` |
| cmd is never given a path it would read specially | `test_cmd_refuses_paths_with_characters_it_treats_specially` |
| Only a commit id is copied from a ref file, and never from outside the git folder | `test_the_recorder_reads_the_commit_from_loose_packed_and_worktree_refs`, `test_post_commit_lines_carry_the_new_commit_in_a_checkout_and_a_worktree` |
| The counter's and the linker's output hold numbers and ids only | `test_the_report_holds_numbers_and_ids_only`, `test_the_output_holds_ids_and_numbers_only` |
| Lost, extra, denied and killed are counted exactly | `test_known_gaps_are_counted_exactly`, `test_permission_denied_calls_come_from_the_record_and_are_not_lost`, `test_a_missing_post_event_is_never_read_as_a_denial`, `test_killed_sessions_get_their_own_rows_and_the_timeout_close` |
| A squash merge links through its PR; an ambiguous tree links nothing | `test_a_squash_merge_links_through_its_pr_by_tree`, `test_the_pr_branch_can_be_given_when_the_tree_is_ambiguous` |
| The probe never uses the user's identity, never reaches a network remote without the flag, and leaves no ref or folder behind | `test_an_accepted_custom_ref_is_pushed_listed_fetched_and_removed`, `test_a_network_remote_is_refused_without_the_flag_and_nothing_is_written`, `test_a_refused_custom_ref_falls_back_to_a_branch` |

## Limits

- **Fields are found, not parsed.** A key inside a string value cannot match, because its quotes are escaped there. The first-or-last rule rests on the key order Claude Code 2.1.283 uses; a version that reorders its payloads needs the rule checked again (the L1 recording step does that).
- **POSIX appends** rely on the local file system keeping one `write` of a short line whole under `O_APPEND`. Local file systems do (the test runs on every CI job); network file systems may not, and the spool is never on one.
- **A hook manager that reinstalls itself replaces the chain.** For example, `npm install` running husky, or `lefthook install`, resets `core.hooksPath` or the hooks folder. Install the chain after the managers.
- **Codex on Windows runs hooks through PowerShell,** whose start-up alone costs far more than Python's.
- **The heads-up works for Claude Code's Edit, Write and MultiEdit,** which name the file in `file_path`. Codex edits files with `apply_patch`, whose input carries a patch, not a `file_path`.
- **The recorder reads refs from files.** A repository that stores refs in the newer reftable format gets an empty `commit` field.
- **The counter's reading of the session records** (entry types, denial phrases, Codex call ids) follows the formats seen in the docs and is confirmed against recorded sessions at the start of T1.
- **The linker needs the spool's git lines,** so the git-hook chain must be installed in every scratch repository and worktree before its sessions. Found by tree, a squash merge links only when main did not move between the branch's last push and the merge; `--pr` covers the other case.
