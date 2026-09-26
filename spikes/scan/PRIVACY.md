# Coding agent setup scanner: privacy note

Status: throwaway (Phase 0). Not part of the product.

This note goes to everyone who is asked to run the scanner. Please read it before you run anything.

## Please keep this private

The scanner and this note are private, pre-release work, shared with you personally. Please do not pass them on, post them, or share your result with anyone except the person who sent them to you.

## What the scanner is

The scanner is a single Python file (Python 3.9 or later). You can read all of it before you run it. It describes, in counts, how coding agents are set up and used on your machine. It installs nothing, changes nothing, and never contacts another machine itself. If a folder it looks at is on a mapped network drive (for example `H:`), it reads that folder like a local one. Network folders on your PATH are never searched. Repositories in cloud-synced folders (for example OneDrive or iCloud) whose files are not on this computer are skipped, so nothing gets downloaded.

## What it looks at

- **Which coding agents are installed**, for example Claude Code, Codex, Gemini CLI, Copilot CLI and Cursor. It checks whether each agent's program is on your PATH and whether its settings folder exists.
- **How many sessions each agent recorded in the last 14 days.** It lists the session files and looks only at their names, sizes and dates. **It never opens a session, transcript or history file.** If your system records when a file was created, it also works out how many sessions overlapped in time.
- **Git repositories** it can find:
  - in common code folders in your home folder, such as `code`, `src`, `projects` and `repos`;
  - from the project list Claude Code keeps;
  - in any folders you name, or in the folders under them.

  For each repository it notes where the repository is hosted, as a category only: GitHub, GitLab, Bitbucket, Azure or other. It also counts:
  - worktrees;
  - branches with recent work;
  - how many people committed recently;
  - pairs of branches where different people changed the same files;
  - commits made by known agent bot accounts, or credited to an agent in a `Co-authored-by` line, for each agent;
  - recent commits made by coding agents working in the cloud, recent commits that credit an agent working locally, and recent commits made by other automation, such as dependency bots;
  - which hook tools are set up (husky, lefthook, pre-commit), whether the repository sets its own hooks folder, and how many git hooks are active;
  - whether it is a partial or shallow clone.

  It runs only read-only git commands: `log`, `rev-parse`, `for-each-ref`, `merge-base`, `worktree list`, `config --get`, `remote -v` and `symbolic-ref --quiet`. Git's network access, signature checks, trace logs and background fetching are switched off for every command. It never runs `git status`, and it changes nothing in the repository.
- **MCP helpers** (tools that agents start). It opens each app's helper settings file and counts:
  - how many helpers are set up for each app, with helpers that are switched off counted separately;
  - for each helper that is switched on, a short label for the kind of command that starts it: a common launcher such as `npx`, `uvx`, `docker`, `node` or `python`; `remote` for a helper reached by a web address; otherwise `other` (a program name), `other-path` (a full path to a program) or `unknown` (no usable command);
  - whether that command can be found on your machine. A command on a network path is never looked up.
- **Hook settings:** whether Claude Code allows your own hooks or only ones your organisation manages, and how many hook events you have set up. On Windows this includes Claude Code's policy setting in the registry.
- **About the run:** the date, the operating system, whether it runs in WSL, and the Python version.

## What it opens, and what it keeps

- **It never opens:**
  - session, transcript or history files;
  - files with the usual credential file names, such as `auth.json` and `.credentials.json`.
- **Git compares changes, but no file content is kept.** To tell rebased or squash-merged work apart from work still in progress, git compares the changes on active branches in memory. That can include any file committed on those branches, whatever its name.
- **It opens settings files, keeps only counts and labels, and discards the rest:**
  - MCP helper settings for each app;
  - Claude Code's hook settings.

  These files can contain passwords or tokens in helper settings. The scanner reads them in memory and discards everything else. It never keeps, shows or saves:
  - helper names, programs or arguments;
  - environment values or headers;
  - any other setting.
- **It asks first before opening `.claude.json`.** Claude Code keeps its user-level helper list in that file, and the file also holds your sign-in details and can hold older prompt history. If you agree, it keeps only the helper counts and command labels.
- **You might see a macOS prompt.** On macOS, a code folder inside Documents can trigger a system prompt asking whether your terminal may access that folder. You can say no; the scanner just skips the folder.

## How names are protected

Folder paths, repository and branch names, email addresses, helper names and helper programs never appear in the result. Where the scanner needs to tell things apart, it uses a short one-way code. The key for the codes is random for each run and is never saved, so the codes cannot be turned back into names or matched between runs.

## What happens to the result

1. The scanner shows you the full result on screen.
2. Only if you agree, it saves it to one file, `orch-scan-<date>.json`, in the folder where you ran it. It writes no other file.
3. You read that file and email it yourself to the person who sent you the scanner. Nothing is sent automatically.
4. You can delete the file at any time. You can also ask the person you sent it to to delete their copy.

## Optional questions

At the end the scanner asks three optional questions about the last 30 days:
- the time an agent's helper tool failing cost you the most time;
- the time not knowing what an agent had changed cost you the most time;
- the time your work collided with a teammate's cost you the most time.

Each asks for a date and a number of minutes. You can skip any of them.

## Running it

```
python orch-scan.py
```

On macOS and Linux, use `python3`. On Windows, run it from PowerShell or Command Prompt. In Git Bash it also works. The scanner explains itself and asks before it starts. With many repositories the scan can take a few minutes. It shows its progress as it goes, for example "repository 3 of 12". Looking at repositories stops after 5 minutes, and the result then says how many it looked at. To stop at any point, press Ctrl+C: nothing is saved unless you agree at the end.
