# Overlap history

Status: throwaway (Phase 0). Not part of the product.

Looks back over the last 30 days of one person's Claude Code sessions and their git history, and finds the times two sessions changed related code in one repository at once. It runs on that person's own machine and writes two files: a full local report, and a file of counts only, with no names or paths.

## What it reads

- **Session logs** under `~/.claude/projects/`. From each session: the folder it ran in, the times of its events, and for each Edit, Write, MultiEdit and NotebookEdit that succeeded, the file's path and the line numbers the edit covered. It never decodes or keeps prompts, replies, or file contents. Each log line is walked token by token, and only the values at a few named places are decoded (`history_logs.WANTED`); every other string is stepped over unread.
- **git history** of the repositories those sessions worked in (coming next).

## Files

| File | What it is |
| --- | --- |
| `history_logs.py` | The session-log reader. |
| `test_history_logs.py` | Tests, on synthetic logs with planted strings that must never come back out. |

## Run the tests

From `spikes/`: `python -m pytest overlap-history`.
