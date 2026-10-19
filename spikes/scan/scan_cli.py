"""Read-only scanner: consent screen, optional questions, preview and output.

Status: throwaway (Phase 0). Not part of the product.

This is the part a participant runs. It explains what the scanner does, asks
before scanning, asks a few optional questions, shows the whole result, and
writes one file only after the participant agrees. The participant then sends
that file themselves. Nothing is ever sent over the network.

scan_bundle.py joins this file and scan_collect.py into the single file that
participants receive.

Runs on Python 3.9 or later, standard library only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

import scan_collect as sc  # bundle: replaced by the collectors themselves

CONSENT_TEXT = """\
Coding agent setup scanner (read-only)
======================================

This is private, pre-release work shared with you personally. Please do not
pass the scanner, this note or your result on to anyone else.

What it does:
  * Checks which coding agents are installed (for example Claude Code, Codex,
    Gemini CLI, Copilot CLI, Cursor, OpenClaw, Cline, Aider).
  * Counts each agent's sessions over the last {days} days, using only file
    names, dates and sizes. It never opens a session, transcript or history
    file. For Claude Code it also groups the sessions by the repository they
    were started in, using only the names of Claude Code's project folders,
    and counts how often several sessions ran on one repository at the same
    time.
  * Looks at git repositories it can find (in common code folders, from
    Claude Code's project list, or ones you name). It counts worktrees, active
    branches, commits made or credited by coding agents, and whether different
    people changed the same files. Where a repository is hosted is kept only as
    a category (GitHub, GitLab, Bitbucket, Azure or other). It uses read-only
    git commands with all network access switched off. To tell rebased or
    squash-merged work apart, git compares the changes on active branches in
    memory; no file content is kept. It also notes whether Aider's history file
    in a repository changed recently, from the file's date only.
  * Opens the MCP helper settings of each app, and Claude Code's settings files
    (settings.json and any settings your organisation manages), to count the
    helpers, note what kind of command starts them (npx, docker and so on) and
    whether it is missing, and count hook settings. These files can contain
    tokens or passwords. They are read in memory only, and only counts and
    labels are kept: helper names, arguments, secrets and program names are not.
  * Notes whether hooks are allowed or restricted (on Windows this includes
    Claude Code's policy setting in the registry), whether a repository sets
    its own hooks folder, and how many git hooks are active.
  * Also records the date, the operating system, whether it runs in WSL, and
    the Python version. Repositories in cloud-synced folders whose files are
    not on this computer are skipped, so nothing gets downloaded.

Names are protected: folder paths, repository and branch names, email
addresses and helper names never appear in the result. Where something has to
be told apart, it becomes a one-way code whose key is random for each run and
never saved.

Nothing is sent anywhere. At the end you will see the full result, and only
if you agree is it saved to one file. You then email that file yourself to
the person who sent you the scanner.
"""

CLAUDE_JSON_TEXT = """\
Claude Code keeps its MCP helper list in a file (.claude.json) that also holds
your sign-in details and project settings, and can hold older prompt history.
May the scanner open that file to count the helpers? It keeps only the number
of helpers and a short label for how each one starts. Nothing else from the
file is used or saved."""

# Three optional questions. Each asks about the costliest case in a fixed window, as a
# date and a number of minutes. Answers are stored exactly as given.
QUESTION_WINDOW_DAYS = 30
QUESTIONS = [
    ("helper_incident",
     "In the last {window} days, think of the time an agent's helper tool (an MCP\n"
     "server) failing to start or misbehaving cost you the most time."),
    ("change_incident",
     "In the last {window} days, think of the time not knowing what an agent had\n"
     "changed cost you the most time."),
    ("parallel_incident",
     "In the last {window} days, think of the time two of your own agent sessions,\n"
     "running at the same time, broke each other's work or made you redo work, and\n"
     "cost you the most time."),
    ("collision_incident",
     "In the last {window} days, think of the time your work, or your agent's,\n"
     "colliding with a teammate's work on the same code cost you the most time."),
]

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _ask(prompt, input_fn):
    try:
        return input_fn(prompt).strip()
    except EOFError:
        return ""


def _yes(answer, default=False):
    if not answer:
        return default
    return answer.lower() in ("y", "yes")


def _ask_date(input_fn, out):
    for _ in range(2):
        ans = _ask("  When was it? (YYYY-MM-DD, or press Enter to skip) ", input_fn)
        if not ans:
            return None
        if _DATE.match(ans):
            try:
                _dt.date.fromisoformat(ans)
                return ans
            except ValueError:
                pass
        out("  Please use the form YYYY-MM-DD, for example 2026-09-01.")
    return None


def _ask_minutes(input_fn, out):
    for _ in range(2):
        ans = _ask("  About how many minutes did it cost? (a number, or press Enter to skip) ", input_fn)
        if not ans:
            return None
        if ans.isascii() and ans.isdecimal():
            return int(ans)
        out("  Please enter a whole number of minutes.")
    return None


def ask_questions(input_fn, out):
    out("")
    out("Four optional questions. Press Enter to skip any of them.")
    answers = {}
    for key, text in QUESTIONS:
        out("")
        out(text.format(window=QUESTION_WINDOW_DAYS))
        answers[key] = {"date": _ask_date(input_fn, out), "minutes": _ask_minutes(input_fn, out)}
    return answers


def _default_name(now):
    return f"orch-scan-{_dt.date.fromtimestamp(now).isoformat()}.json"


def _free_path(path):
    if not path.exists():
        return path
    for i in range(2, 100):
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(str(path))


def _terminal_available():
    """True if a person can answer prompts. Git Bash's default terminal (mintty) gives
    Python a pipe instead of a console, so it is recognised by its MSYSTEM variable."""
    try:
        if sys.stdin.isatty():
            return True
    except (AttributeError, ValueError):
        return False
    return os.name == "nt" and bool(os.environ.get("MSYSTEM"))


def run(argv=None, input_fn=input, out=print, is_tty=None):
    p = argparse.ArgumentParser(
        prog="orch-scan",
        description="Read-only scanner of coding agent setup. Shows everything before saving.")
    p.add_argument("--out", help="where to save the result: a file, or a folder (default: this folder)")
    p.add_argument("--days", type=int, default=14, help="how many days of sessions to count (default 14)")
    p.add_argument("--repo", action="append", default=[], help="also look at this git repository")
    p.add_argument("--yes", action="store_true",
                   help="agree to the note without being asked, and save without asking")
    p.add_argument("--no-questions", action="store_true", help="skip the optional questions")
    p.add_argument("--claude-json", choices=["ask", "yes", "no"], default="ask",
                   help="whether to count helpers listed in Claude Code's .claude.json (default: ask)")
    # Testing only: scan a synthetic home folder, hermetically (no inherited settings).
    p.add_argument("--home", help=argparse.SUPPRESS)
    p.add_argument("--system", choices=["Windows", "Darwin", "Linux"], help=argparse.SUPPRESS)
    p.add_argument("--root", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    synthetic = args.home is not None or args.system is not None or args.root is not None
    if synthetic and not args.home:
        # Otherwise a test run would silently scan the real machine.
        p.error("--system and --root are for synthetic scans and need a non-empty --home")

    if is_tty is None:
        is_tty = _terminal_available()
    interactive = is_tty and not args.yes

    out(CONSENT_TEXT.format(days=args.days))
    if not args.yes:
        if not is_tty:
            out("There is no terminal to ask you questions in, and --yes was not given, so nothing was")
            out("scanned. Run it from PowerShell, Command Prompt or a normal terminal window.")
            return 2
        if not _yes(_ask("Continue? [y/N] ", input_fn)):
            out("Stopped. Nothing was scanned or saved.")
            return 0

    if synthetic:
        env = sc.Env.synthetic(args.home, args.system or "Linux", root=args.root)
    else:
        env = sc.Env()

    repos = list(args.repo)
    if interactive:
        extra = _ask("\nOptional: paths of git repositories you work in, separated by ';'\n"
                     "(press Enter to skip) ", input_fn)
        repos.extend(x.strip().strip('"') for x in extra.split(";") if x.strip())

    claude_home = sc.agent_home(env, sc.agent_spec("claude-code"))
    read_claude_json = args.claude_json == "yes"
    if args.claude_json == "ask" and interactive and os.path.isdir(claude_home):
        out("")
        out(CLAUDE_JSON_TEXT)
        read_claude_json = _yes(_ask("Count them? [y/N] ", input_fn))

    out("\nScanning. With many repositories this can take a few minutes...")

    def progress(i, n):
        out(f"  looking at repository {i} of {n}")

    result = sc.collect(env, days=args.days, extra_repos=repos, read_claude_json=read_claude_json,
                        progress=progress)

    if interactive and not args.no_questions:
        result["self_report"] = ask_questions(input_fn, out)
        result["self_report"]["window_days"] = QUESTION_WINDOW_DAYS
    else:
        result["self_report"] = None

    text = json.dumps(result, indent=2, sort_keys=True)
    out("\nHere is everything the scanner found. Nothing else is saved.\n")
    out(text)

    target = Path(args.out) if args.out else Path.cwd()
    if target.is_dir():
        target = target / _default_name(env.now)
    if not args.yes:
        if not _yes(_ask(f"\nSave this to {target}? [Y/n] ", input_fn), default=True):
            out("Not saved. Nothing was written.")
            return 0
    target = _save(target, text)
    out(f"\nSaved to {target}")
    out("Please look it over, then email it to the person who sent you the scanner.")
    out("You can delete the file at any time.")
    return 0


class _StoppedWhileSaving(Exception):
    """Ctrl+C landed while the result file was being written."""


_SAVED = []      # the result file, once it is completely written


def _write_all(fh, text):
    fh.write(text)


def _save(target, text):
    """Write the result to a new file. If Ctrl+C lands during the write, the partly written
    file is removed, and the message says exactly what happened."""
    target = _free_path(target)
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as fh:
            _write_all(fh, text + "\n")
    except KeyboardInterrupt:
        try:
            os.remove(target)
        except OSError:
            raise _StoppedWhileSaving(f"Stopped while saving. A partly written file may be left at "
                                      f"{target}; please delete it.") from None
        raise _StoppedWhileSaving("Stopped while saving. The partly written file was removed, "
                                  "so nothing was saved.") from None
    _SAVED.append(target)
    return target


def _configure_streams():
    # Line buffering: in Git Bash stdout is a pipe, and without it the progress lines would
    # appear only at the end. backslashreplace: a console that cannot show a character (for
    # example a user name outside the console's code page) must not crash the scanner.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace", line_buffering=True)
        except (AttributeError, ValueError):
            pass


def main(argv=None, **run_options):
    """Run the scanner. Ctrl+C at any point stops it with exit code 130 and a plain message."""
    _configure_streams()
    del _SAVED[:]
    out = run_options.get("out", print)
    try:
        return run(argv, **run_options)
    except _StoppedWhileSaving as exc:
        out("\n" + str(exc))
    except KeyboardInterrupt:
        if _SAVED:
            out(f"\nStopped. The result had already been saved to {_SAVED[-1]}.")
        else:
            out("\nStopped. Nothing was saved.")
    return 130


if __name__ == "__main__":
    raise SystemExit(main())
