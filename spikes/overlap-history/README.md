# Overlap history

Status: throwaway (Phase 0). Not part of the product.

Looks back over the last 30 days of one person's Claude Code sessions and their git history, and finds the times two sessions changed related code in one repository at once. It runs on that person's own machine and writes two files: a full local report, and a file of counts only, with no names or paths.

## Run it

```
python overlap-history.pyz            # built by: python history_build.py
python history_cli.py                 # or from this folder, with the repository's engine and package
```

It needs Python 3.10 or later (as the engine does). It shows what it reads and asks before reading anything (`--yes` skips the question), then writes `overlap-report.html` and `overlap-counts.json` into `--out` (default: the current folder).

## What it reads

- **Session logs** under `~/.claude/projects/`. From each session: the folder it ran in, the times of its events, and for each Edit, Write, MultiEdit and NotebookEdit that succeeded, the file's path and the line numbers the edit covered. It never decodes or keeps prompts, replies, or file contents. Each log line is walked token by token, and only the values at a few named places are decoded (`history_logs.WANTED`); every other string is stepped over unread. Subagent logs count for the session that started them.
- **git**, in the repositories those sessions edited: each working tree's HEAD reflog, the commits, and the code at those commits, read from git's object store. Nothing is checked out or written.

## What it works out

1. Each session's repositories, placed by the folders of the files it edited, so all worktrees of a repository count together. Files in folders that no longer exist are counted as unmapped.
2. Parallel pairs: two sessions that edited one repository with overlapping time ranges (first to last event). Whether their active stretches also overlapped is kept beside it.
3. What each session changed: its starting commit (its working tree's HEAD when it started), the commits it made (reflog entries during the session and up to 10 minutes after), and the function and class changes between them, rated by the product's contract classifier. A session that committed nothing has its files but no function changes.
4. Overlaps by rule, with the product's own rule code (`orchestraty.rules.overlap`): O1 the same file, O2 the same function, O3 a breaking change used in a file the other changed, O4 a removed function still used there, O6 callers neither session updated.
5. What followed within 14 days: a text conflict when both were merged, a revert, or a later fix to a function the overlap involves.

## Public history

`history_public.py` runs the same rules on agent pull requests in public GitHub repositories: Python, merged in the last 183 days.

```
python history_public.py select                      # GitHub's API only, through gh: candidates.json
python history_public.py scan OWNER/NAME [...]        # partial clones of the named repositories, then the rules
```

- **Agent pull requests:** Claude Code's footer or a Codex task link in the description, a coding agent's GitHub app as the author, or an agent's branch prefix (`claude/`, `codex/`, `copilot/`).
- **Parallel pairs:** two agent pull requests into one base branch whose open periods (created to merged) overlap.
- **Each change:** the head against its merge base with the base branch just before it merged. Each pull request is a place of its own, so the function-level rules apply.
- **What followed, within 14 days of the later merge:** a text conflict between the two heads in a file both changed, and a revert or a later fix on the base branch.
- **What it downloads:** `select` calls the API only and lists each candidate's size. `scan` clones the repositories it is named, and only those: partial clones (commits and trees) under `--out` (default `~/orch-phase0/agent-history`). A file's contents are fetched in batches when a commit that needs them is read. Nothing is checked out or run.
- **Sampling:** at most 300 parallel pairs per repository (`--max-pairs`), drawn at random with the repository's name as the seed. So no one repository decides the rate, and a rerun draws the same pairs. The median of the repositories' own rates is reported beside the pooled rate.
- **What it writes:** `public-result.json`, `public-counts.json` and `public-report.html`, in a folder named for the day. Each repository's result is saved there as it finishes, and a rerun the same day reuses it. Pull-request titles and descriptions are not kept.
- **Limits:** merged pull requests only, so rework done before merging is not seen and the rate is a lower bound. Python only. Pull requests from forks are left out and counted.

## Files

| File | What it is |
| --- | --- |
| `history_logs.py` | The session-log reader. |
| `history_repos.py` | Files to repositories; parallel pairs. |
| `history_changes.py` | Starting commit, own commits, and function changes per session. |
| `history_overlaps.py` | The rules per pair, and what followed. |
| `history_report.py` | The local report and the counts file. |
| `history_cli.py` | The command, with the consent text. |
| `history_build.py` | Builds the one-file zipapp (this folder, the engine and the product package). |
| `history_public.py` | The public history: candidates through gh, partial clones, and the rules on pairs of agent pull requests. |
| `test_history_*.py` | Tests: synthetic logs with planted strings that must never come back out, a real tiny repository with two parallel sessions, and a local stand-in for GitHub with pull-request refs. |

## Run the tests

From `spikes/`: `python -m pytest overlap-history`. Below Python 3.10 only the log reader's tests run.
