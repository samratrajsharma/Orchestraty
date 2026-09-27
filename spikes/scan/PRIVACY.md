# Coding agent setup scanner: privacy note

Status: throwaway (Phase 0). Not part of the product.

This note goes to everyone who is asked to run the scanner. Please read it before you run anything.

## Please keep this private

The scanner and this note are private, pre-release work, shared with you personally. Please do not pass them on, post them, or share your result with anyone except the person who sent them to you.

## Why we ask

We want to learn how developers and teams set up and use coding agents, so that we can decide what to build. The scanner collects counts, not content, so you can take part without sharing your code or your conversations.

## What the scanner is

The scanner is a single Python file (Python 3.9 or later). You can read all of it before you run it. It describes, in counts, how coding agents are set up and used on your machine. It installs nothing, changes nothing, and never contacts another machine itself. If a folder it looks at is on a mapped network drive (for example `H:`), it reads that folder like a local one. Network folders on your PATH are never searched. Repositories in cloud-synced folders (for example OneDrive or iCloud) whose files are not on this computer are skipped, so nothing gets downloaded.

## What it looks at

- **Which coding agents are installed**, for example Claude Code, Codex, Gemini CLI, Copilot CLI and Cursor. It checks whether each agent's program is on your PATH and whether its settings folder exists.
- **How many sessions each agent recorded in the last 14 days.** It lists the session files and looks only at their names, sizes and dates. **It never opens a session, transcript or history file.** If your system records when a file was created, it also works out how many sessions overlapped in time.
- **Git repositories** it can find: in common code folders in your home folder (such as `code`, `src`, `projects` and `repos`), from the project list Claude Code keeps, and in any folders you name or the folders under them. For each repository it notes where it is hosted, as a category only (GitHub, GitLab, Bitbucket, Azure or other), and counts:
  - worktrees, and branches with recent work;
  - how many people committed recently. One person who uses two email addresses, or the same name on two machines, counts once; git's `.mailmap` file in the repository is used for this when it is a plain file. It also keeps the count by email address;
  - pairs of branches where different people changed the same files;
  - commits made or credited by coding agents, in the cloud or locally, and commits made by other automation such as dependency bots;
  - which hook tools are set up (husky, lefthook, pre-commit), whether the repository sets its own hooks folder, how many git hooks are active, and whether it is a partial or shallow clone.

  It runs only read-only git commands: `log`, `rev-parse`, `for-each-ref`, `merge-base`, `worktree list`, `config --get`, `remote -v` and `symbolic-ref --quiet`. Git's network access, signature checks, trace logs and background fetching are switched off for every command. It never runs `git status`, and it changes nothing in the repository.
- **MCP helpers** (tools that agents start): how many are set up for each app (switched-off ones counted separately), a short label for the kind of command that starts each one (a common launcher such as `npx`, `uvx`, `docker`, `node` or `python`; `remote` for a web address; otherwise `other`, `other-path` or `unknown`), and whether that command can be found on your machine. A command on a network path is never looked up.
- **Hook settings:** whether Claude Code allows your own hooks or only ones your organisation manages, and how many hook events you have set up. On Windows this includes Claude Code's policy setting in the registry.
- **About the run:** the date, the operating system, whether it runs in WSL, the Python version and how long the scan took.

## What it opens, and what it keeps

- **It never opens** session, transcript or history files, or files with the usual credential file names, such as `auth.json` and `.credentials.json`.
- **Git compares changes, but no file content is kept.** To tell rebased or squash-merged work apart from work still in progress, git compares the changes on active branches in memory. That can include any file committed on those branches, whatever its name.
- **It opens settings files, keeps only counts and labels, and discards the rest:** each app's MCP helper settings, and Claude Code's settings files (your `settings.json` and any settings your organisation manages). These files can contain passwords or tokens. The scanner reads them in memory only and never keeps, shows or saves helper names, programs, arguments, environment values, headers or any other setting.
- **It asks first before opening `.claude.json`.** Claude Code keeps its user-level helper list there, and the file also holds your sign-in details and can hold older prompt history. The default answer is no. If you agree, it keeps only the helper counts and command labels.
- **You might see a macOS prompt** asking whether your terminal may access a folder inside Documents. You can say no; the scanner just skips the folder.

## How names are protected

Folder paths, repository and branch names, names, email addresses, helper names and helper programs never appear in the result. Where the scanner needs to tell things apart, it uses a short one-way code. The key for the codes is random for each run and is never saved, so the codes cannot be turned back into names or matched between runs.

## What happens to the result

1. The scanner shows you the full result on screen.
2. Only if you agree, it saves it to one file, `orch-scan-<date>.json`, in the folder where you ran it. It writes no other file.
3. You read that file and email it yourself to the person who sent you the scanner. Nothing is sent automatically.
4. **Who sees it:** only the person who sent you the scanner. They save it under a participant code and delete your email. It is analysed under that code, never with your name or email, and only totals across all participants are ever shown to anyone else.
5. **How long it is kept:** until the report on this study is final. Then every result file, and the list linking codes to people, is deleted; only the totals remain.
6. **Deletion on request:** ask the person who sent you the scanner, at any time. Your file and your entry in the code list are deleted within 7 days, and you get an email confirming it. Totals already in a finished report are not recalculated. You can delete your own copy at any time.

Questions about any of this go to the person who sent you the scanner.

## Optional questions

At the end the scanner asks three optional questions about the last 30 days: the time an agent's helper tool failing cost you the most time; the time not knowing what an agent had changed cost you the most time; and the time your work collided with a teammate's cost you the most time. Each asks for a date and a number of minutes. You can skip any of them.

## Running it

First check that the file is the one we sent: compare its SHA-256 with the one in our message.
- Windows (PowerShell): `Get-FileHash orch-scan.py`
- macOS: `shasum -a 256 orch-scan.py`
- Linux: `sha256sum orch-scan.py`

Then run:

```
python orch-scan.py
```

On macOS and Linux, use `python3`. On Windows, run it from PowerShell or Command Prompt; Git Bash also works. The scanner explains itself and asks before it starts. It takes up to about 5 minutes and shows its progress as it goes, for example "repository 3 of 12". To stop at any point, press Ctrl+C. Unless you had already agreed to save, it says "Stopped. Nothing was saved." and nothing is saved.
