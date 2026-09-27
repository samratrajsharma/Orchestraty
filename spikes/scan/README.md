# Scanner

Status: throwaway (Phase 0). Not part of the product.

A read-only scanner that describes, in counts, how coding agents are set up and used on one developer's machine:
- which agents are installed;
- how many sessions each agent recorded recently, from file names, dates and sizes only;
- whether git repositories show work in flight by different people on the same files;
- which MCP helpers are configured;
- whether hooks are allowed.

It never opens session, history or sign-in files. It writes nothing except the one result file and never contacts another machine itself. Folders on a mapped network drive are read like local ones, but network paths in helper settings are never looked up. It keeps no names: identifiers become salted one-way codes, and helper commands become a label from a fixed list.

## Files

| File | What it is |
| --- | --- |
| `scan_collect.py` | The collectors. Standard library only, Python 3.9 or later. |
| `test_scan_collect.py`, `conftest.py` | Tests on synthetic home folders laid out as Windows, macOS and Linux homes, and on synthetic git repositories. |
| `scan_cli.py`, `test_scan_cli.py` | The consent screen, optional questions, preview and output file (added in the change after the collectors). |
| `scan_bundle.py` | Builds the single file that participants receive (same change as `scan_cli.py`). |
| `PRIVACY.md`, `RELEASE.md` | The participant-facing privacy note, and the steps before any build is sent out (same change). |

## Safety properties, and the tests that check them

| Property | Test |
| --- | --- |
| No session, transcript or history file is ever opened (by `open`, `io.FileIO` or `os.open`) or handed to git | `test_private_files_are_never_opened_or_passed_to_git` |
| Nothing changes anywhere in the synthetic machine, including its temp folder | `test_nothing_is_written_anywhere` |
| No path, repository or branch name, email, remote, helper name, helper program, host name, user name, secret or salt reaches the output | `test_output_holds_no_identifiers` |
| Git runs only read-only command shapes, with exact output formats (none that checks signatures) | `test_git_commands_outside_the_allowlist_are_refused`, `test_option_like_origin_head_is_ignored` |
| Git is started by its absolute path, never from the current folder, and every command carries the safety switches | `test_git_is_never_started_from_the_current_folder`, `test_every_git_command_carries_the_safety_switches` |
| Git never fetches (partial clones, even with `protocol.file.allow=always` set), never runs gpg or other config-chosen programs, and never writes trace logs | `test_treeless_partial_clone_fetches_nothing_and_writes_nothing`, `test_blobless_partial_clone_still_counts_work_in_flight`, `test_signature_checks_and_fsmonitor_never_run_programs`, `test_trace2_targets_in_the_users_git_config_write_nothing` |
| Inherited `GIT_*` variables cannot redirect git; an empty `.git` never falls through to a parent repository | `test_inherited_git_variables_cannot_redirect_git`, `test_an_empty_git_folder_never_falls_through_to_a_parent_repo` |
| Config files that lead to session, history or sign-in files, or to `.claude.json` without consent, are skipped, through a link, a hard link or another letter case | `test_config_links_to_private_files_are_not_followed`, `test_user_level_links_to_history_sign_in_and_claude_json_are_not_followed`, `test_hard_links_and_other_letter_cases_do_not_reach_private_files` |
| Links inside a repository (config files, folders, hook-manager markers) are never followed | `test_links_inside_a_repository_are_never_followed`, `test_a_junction_inside_a_repository_is_not_followed` (Windows) |
| Files not stored on this computer (cloud placeholders, on Windows and macOS) are not opened, and repositories whose git folder holds such files are not inspected | `test_files_not_stored_on_this_computer_are_not_opened`, `test_repositories_in_cloud_synced_folders_are_not_inspected` |
| `.claude.json`, which also holds sign-in details and possibly prompt history, is opened only with consent | `test_claude_json_is_not_opened_without_consent` |
| Helper commands on network paths, in any spelling (including `/./net` and `/NET`), are never looked up; network folders on PATH are never searched | `test_network_paths_in_helper_commands_are_never_looked_up`, `test_network_and_driveless_path_entries_are_never_searched` |
| Links in the git folder's hooks are counted by name, never followed | `test_a_linked_hook_is_counted_but_never_followed` |
| One person with two addresses is not a pair with themselves; two people still are | `test_one_person_with_two_addresses_is_not_a_pair`, `test_two_people_are_still_a_pair`, `test_mailmap_groups_identities_only_when_safe_to_read` |
| One malformed config file, or a session file dated in the future, never stops the scan | `test_malformed_configs_never_stop_the_scan`, `test_future_dated_session_files_never_stop_the_scan` |
| No network: every test fails if a socket is opened | `no_network` (automatic, in `conftest.py`) |
| Standard library only, no network modules, valid Python 3.9 | `test_standard_library_only_and_no_network_modules`, `test_source_parses_as_python_3_9` |
| The command-line tool writes nothing except the one result file, exactly as previewed | `test_full_interactive_run_saves_exactly_what_it_showed_and_nothing_else` (in `test_scan_cli.py`, next change) |
| Refusing consent scans and writes nothing; an existing file is never overwritten | `test_refusing_consent_scans_and_writes_nothing`, `test_existing_file_is_never_overwritten_and_a_folder_gets_a_file` (same) |
| Ctrl+C at any point stops with exit code 130 and a plain message, and never claims nothing was saved when something was | `test_ctrl_c_at_a_prompt_stops_cleanly`, `test_ctrl_c_during_the_scan_stops_cleanly`, `test_ctrl_c_while_saving_says_what_happened`, `test_ctrl_c_after_saving_does_not_claim_nothing_was_saved` (same) |
| Test-only flags never fall back to scanning the real machine | `test_synthetic_flags_never_fall_back_to_the_real_machine` (same) |

Each property was checked by removing its defences in a copy of the code and confirming that a test fails. Some properties have more than one defence on purpose (for example, network fetches are blocked by both `GIT_NO_LAZY_FETCH` and `GIT_ALLOW_PROTOCOL`); removing only one of them leaves the tests passing. `core.fsmonitor=false` cannot be observed through the read-only commands used, because none of them reads the index, so its presence on every command line is checked instead.

## Developer run

The collectors alone, printed as JSON, against a synthetic home folder. This skips the consent screen. Only PATH and the variables Windows needs to start programs are taken from the real machine; every agent location and machine-wide file comes from the synthetic home and `--root`, and git ignores the machine's system config.

```
python scan_collect.py --home <folder> --system Linux --root <folder>
```

## How "work in flight by different people" is counted

- **In flight** means a branch, not merged into the default branch, with commits in the window that the default branch does not already have. Commits that are already there, whether identical or rebased, are not counted. A branch whose whole change sits in one commit on the default branch (a squash merge, including one after the default branch was merged into the branch) is not in flight either. Files whose changes cancel out on the branch are ignored.
- **Probably merged:** when the default branch changed the same files before a squash or rebase merge, neither the files nor the patches match exactly. A branch is then counted as `probably_merged`, not in flight, if its commits reappear on the default branch with the same author, author date and files (a rebase), or if a later default-side commit by one of its authors changes all its files, and the files that differ were also changed on the default side before it (a squash).
- **One stream of work counts once:** a local branch and its pushed copy (after a rebase, the newer copy is kept), a local branch a few commits ahead of it, and stacked branches. Branches merged together into an integration branch count as that one branch, but collisions between them are still counted.
- **Different people on the same files** means that, for at least one file, someone on one side and someone else on the other side changed it. The sides are two in-flight branches, or a branch and what the default branch gained after the branch forked. Only commits unique to each side are compared. Commits by coding agents count as their own person; commits by other automation (dependency bots and the like) are left out, and a branch made only by them is not in flight.
- **Who is one person.** Within a repository, two commit identities are one person when they share an email address or a name (compared without case, spaces or punctuation). A GitHub noreply address (`<id>+<login>@users.noreply.github.com`, which the web UI uses for squash merges when email privacy is on) also counts its login as a name, because an account with no display name commits under its login. The repository's `.mailmap` is applied too (`%aN`/`%aE`), but only when it is absent or a plain local file: a linked `.mailmap` is never handed to git, since git before 2.32 follows the link. `cross_author_overlap_*` and `people_in_window` use these people; `cross_email_overlap_*` and `authors_in_window` keep the strict count by email, so the data shows how much the grouping changes.
- **Not counted:** branches with no history in common with the default branch, such as a `gh-pages` branch. Linked worktrees of one repository count as one repository.
- **Partial and shallow clones:** git never downloads missing data. In a blobless clone the patch comparison is not possible, so branches are compared by commit only (`branches_checked_by_commit_only`); a treeless clone's branches cannot be read. In a shallow clone, branches that fork below the cut-off cannot be compared and count as unreadable.

## Limits

- **Session start times.** A session's start is the file's creation time where the OS records one: Windows and macOS do, Linux does not. On Linux, `max_concurrent` is therefore empty.
- **Session layouts.** These come from each agent's public documentation or, where the vendor documents none, from third-party sources. The `AGENTS` table marks which, and the output repeats it as `session_layout_source`. Sessions kept in a database (OpenCode, Cursor's editor chats) are not counted.
- **Codex repositories.** Codex session folders do not include the working folder in their names. Repositories are found only from paths the person gives, Claude Code's project list, and a bounded search of common code folders.
- **Two different people who share a name count as one person** (for example two people who both commit as "root", or two namesakes). The strict counts by email (`cross_email_overlap_*`) show where this matters.
- **One person with two addresses and two unrelated names,** and no `.mailmap` entry, still counts as two people.
- **Squash merges that carry extra edits.** A squash commit that also changes the branch's files further is not recognised, so that branch still counts as in flight.
- **Helper commands are checked against the scanner's own PATH,** which can differ from the PATH an app sees.
- **Time.** A scan takes up to about 5 minutes: after 5 minutes it looks at no further repository or branch (noted in `limits`). It shows progress, and `run.duration_seconds` records how long it took.
- **Probably merged is a heuristic.** A branch whose author later commits to the default branch a change covering all the branch's files can be counted as merged although it is not.
- **Aliases that `realpath` does not resolve** (a bind mount, or a macOS firmlink spelling of the home folder) can let a config file that the person deliberately linked through one reach a private file. Private files are compared by path, not by file identity.
- **Cloud-synced repositories** are detected from the git folder itself and its pack folder. A `.git` file that points elsewhere (a linked worktree) is not checked this way.
