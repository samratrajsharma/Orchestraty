"""Overlap history: finds the times two of your Claude Code sessions changed related code in one
repository at once, over the last 30 days, on this computer only.

    python overlap-history.pyz [--days 30] [--out FOLDER] [--yes]
    python history_cli.py ...            (from this folder, with the repository's engine and package)

It shows what it reads and asks before reading anything (--yes skips the question), then
writes two files into --out (default: the current folder): overlap-report.html and
overlap-counts.json. It needs Python 3.10 or later.
"""
import argparse
import os
import shutil
import sys
import time

CONSENT = """Overlap history (reads your local session logs)

This tool looks at the last 30 days of your Claude Code sessions on this computer, and at your git history, to find times when two of your sessions changed related code at the same time.

What it reads: from each session log, only the folder it ran in, when it started and ended, and which files and lines were edited. It never reads your prompts, the agent's replies or the contents of your files from the logs. From git, it reads your commits and the code at those commits, on this computer only.

What it writes: two files in this folder. overlap-report.html is a full report for you, with file and function names. overlap-counts.json holds counts only, with no names or paths.

What leaves your computer: nothing, unless you choose to send overlap-counts.json to the person who gave you this tool. Read it first; it is plain text.

Type yes to continue, or anything else to stop."""

REPORT, COUNTS = "overlap-report.html", "overlap-counts.json"


def _find_packages():
    """Run from the repository: put its engine and product package on the path."""
    try:
        import orch_engine  # noqa: F401
        import orchestraty.rules  # noqa: F401
        return
    except ImportError:
        pass
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for sub in ("orchestraty", "engine"):
        path = os.path.join(repo, sub)
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def run(projects=None, days=30, now=None, progress=None):
    """Everything the two files are made from."""
    from history_changes import change_of
    from history_logs import read_sessions
    from history_overlaps import Indexes, findings_for, followed, same_worktree
    from history_repos import parallel_pairs, place_sessions

    say = progress or (lambda message: None)
    now = time.time() if now is None else now
    sessions, skipped = read_sessions(projects, days, now)
    say("%d sessions read" % len(sessions))
    repos, left_out = place_sessions(sessions)
    pairs = parallel_pairs(repos)
    say("%d repositories, %d parallel pairs" % (len(repos), len(pairs)))
    indexes, changes, out = Indexes(), {}, []
    try:
        for n, (repo, wa, wb, active) in enumerate(pairs, 1):
            for w in (wa, wb):
                if (repo, w.session.id) not in changes:
                    changes[(repo, w.session.id)] = change_of(w)
            ca, cb = changes[(repo, wa.session.id)], changes[(repo, wb.session.id)]
            found = findings_for(ca, cb, indexes)
            out.append({
                "repo": os.path.basename(wa.top), "active": active, "same_worktree": same_worktree(ca, cb),
                "a": _side(ca), "b": _side(cb),
                "findings": [f.as_dict() for f in found],
                "followed": followed(ca, cb, found, now),
            })
            if n % 10 == 0:
                say("%d of %d pairs done" % (n, len(pairs)))
    finally:
        shutil.rmtree(indexes._data, ignore_errors=True)
    return {"days": days, "now": now, "sessions": len(sessions),
            "sessions_with_edits": sum(1 for s in sessions.values() if s.edits),
            "repositories": sorted(repos), "pairs": out, "unreadable_lines": skipped["lines"],
            "left_out": left_out, "index_failures": indexes.failed}


def _side(change):
    s = change.work.session
    return {"session": s.id, "start": s.start, "end": s.end, "committed": change.committed,
            "files": change.work.files}


def main(argv=None):
    if sys.version_info < (3, 10):
        print("The overlap history needs Python 3.10 or later.", file=sys.stderr)
        return 2
    p = argparse.ArgumentParser(description="Overlaps among your own Claude Code sessions, last 30 days.")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--out", default=".", help="where the two files go (default: this folder)")
    p.add_argument("--projects", help="the session-log folder (default: ~/.claude/projects)")
    p.add_argument("--yes", action="store_true", help="agree without being asked")
    args = p.parse_args(argv)
    print(CONSENT)
    if not args.yes:
        try:
            answer = input("> ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "yes":
            print("Stopped. Nothing was read.")
            return 1
    _find_packages()
    from history_report import counts_json, report_html
    result = run(args.projects, args.days, progress=print)
    os.makedirs(args.out, exist_ok=True)
    for name, text in ((REPORT, report_html(result)), (COUNTS, counts_json(result))):
        with open(os.path.join(args.out, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print("Wrote %s and %s in %s" % (REPORT, COUNTS, os.path.abspath(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
