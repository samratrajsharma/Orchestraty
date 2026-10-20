"""Minimal session-to-commit linker, for the T1 link rate and wrong links.

Status: throwaway (Phase 0). Not part of the product.

    python capture_link.py --spool <root>/spool [--truth <ground truth.json>] [--pr 12=feature-x ...]
                           [--grace-seconds 120] [--merge-seconds 10] [--json <out file>]

How a commit is found. Every commit made in a scratch repository fires post-commit, whose spool
line (capture_githook.py) holds the time, the working tree, the branch and the new commit id.
Commits a rebase makes come from its post-rewrite line; a commit that post-rewrite replaces
(amend or rebase) is dropped, since it is no longer on the branch. A merge commit fires
post-merge only; it counts when it has two or more parents and its committer time is within
--merge-seconds of the hook. A fast-forward or a merge --squash makes no new commit.

How it is linked. A commit is linked to the agent session that
  * ran in the same working tree: the nearest folder with a .git entry above the session's cwd;
  * was open at that time (first to last spool line), or had ended at most --grace-seconds
    before it, for a commit the harness makes right after the session; and
  * was on that branch: the branch the working tree was on when the session started (the last
    git line there before it), or one the session checked out while it was open. When no git
    line shows the start branch, this check is skipped.
If several sessions qualify, an open one wins over one in its grace period; then the latest
start (open) or the latest end (grace).

Squash merges. A commit whose subject ends in "(#N)" is the squash of pull request N and is
linked only this way: to every session linked to a commit on the PR's branch. The branch is
given with --pr N=branch (read off the PR page), or else found by tree: the squash commit's
tree equals the tree of a linked commit, the branch tip when main did not move before the
merge. A tree that matches commits on more than one branch is reported as ambiguous and not
linked. Subjects and trees come from read-only git log in each working tree seen in the spool.

The ground truth, written by the harness after each session:
    {"sessions": [{"agent": "claude", "session_id": "...", "commits": ["<id>", ...],
                   "by": "agent|harness"}, ...]}
Once a PR is merged, its squash commit is added to the session's commits. A session with
commits "changed files"; it is linked right when its linked commits are exactly its commits
(ids match on a shared prefix of at least 7 characters). A linked commit not in its session's
list is a wrong link: reported separately, and the session does not count as linked right.

It prints ids and numbers only: no commit subject, branch name, path or file content.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

from capture_count import read_jsonl

SQUASH = re.compile(r"\(#(\d+)\)\s*$")
HEX = set("0123456789abcdef")

# Variables that would point git at another repository than the one named on its command line.
REPO_VARIABLES = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                  "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX"}

def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def worktree_of(cwd, known=()):
    """The working tree a cwd belongs to, normalised: the deepest known hook top folder (git runs
    hooks from the working tree's top) that contains it, else the nearest folder with a .git
    entry, else cwd itself. Known top folders come first, so a nested worktree that has since
    been removed still maps to itself, not to the checkout around it."""
    start = _norm(cwd)
    inside = [k for k in known if start == k or start.startswith(k.rstrip(os.sep) + os.sep)]
    if inside:
        return max(inside, key=len)
    path = start
    while True:
        if os.path.lexists(os.path.join(path, ".git")):
            return os.path.normcase(path)
        parent = os.path.dirname(path)
        if parent == path:
            return start
        path = parent


def _branch(value):
    """A branch name (refs/heads/...), or "" for a detached HEAD or anything else."""
    return value if isinstance(value, str) and value.startswith("refs/heads/") else ""


def _is_id(value):
    return isinstance(value, str) and 4 <= len(value) <= 64 and set(value) <= HEX


def _git(worktree, *args):
    env = {k: v for k, v in os.environ.items() if k not in REPO_VARIABLES}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    try:
        proc = subprocess.run(["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", worktree] + list(args),
                              capture_output=True, env=env, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None


class Session:
    def __init__(self, agent, session_id):
        self.agent, self.session_id = agent, session_id
        self.start = self.end = None
        self.cwds = set()
        self.worktrees = set()                     # filled in by link(), once the hook top folders are known

    def seen(self, ts, cwd):
        self.start = ts if self.start is None else min(self.start, ts)
        self.end = ts if self.end is None else max(self.end, ts)
        if isinstance(cwd, str) and cwd:
            self.cwds.add(cwd)


def read_sessions(spool):
    sessions = {}
    for agent in ("claude", "codex", "agy"):
        folder = os.path.join(spool, agent)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".jsonl"):
                continue
            for line in read_jsonl(os.path.join(folder, name)):
                if isinstance(line, dict) and isinstance(line.get("session_id"), str) \
                        and isinstance(line.get("ts"), int):
                    key = (agent, line["session_id"])
                    sessions.setdefault(key, Session(*key)).seen(line["ts"] / 1e9, line.get("cwd"))
    return sessions


def read_git_events(spool):
    events = []
    folder = os.path.join(spool, "git")
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.endswith(".jsonl"):
                events += [e for e in read_jsonl(os.path.join(folder, name))
                           if isinstance(e, dict) and isinstance(e.get("ts"), int) and isinstance(e.get("cwd"), str)]
    for e in events:
        e["ts"] = e["ts"] / 1e9
        e["worktree"] = _norm(e["cwd"])                   # git runs hooks from the working tree's top
        e["head"] = _branch(e.get("head"))
        e["repo"] = _norm(e["repo"]) if isinstance(e.get("repo"), str) and e["repo"] else None
    return sorted(events, key=lambda e: e["ts"])


def repo_of(worktree, cache):
    """The repository a working tree belongs to (its shared git folder), or None if unreadable."""
    if worktree not in cache:
        out = _git(worktree, "rev-parse", "--git-common-dir") if os.path.isdir(worktree) else None
        cache[worktree] = _norm(os.path.join(worktree, out.strip())) if out else None
    return cache[worktree]


def read_log(worktrees, repos):
    """({commit id: (tree id, subject, committer time, parent count)}, {commit id: {repo ids}},
    {repo ids read}) from read-only git log in each repository that still exists."""
    commits, where, done = {}, defaultdict(set), set()
    for wt in sorted(worktrees):
        repo = repo_of(wt, repos)
        if repo is None or repo in done:
            continue
        done.add(repo)
        out = _git(wt, "log", "--all", "--format=%H%x1f%T%x1f%ct%x1f%P%x1f%s")
        for row in (out or "").splitlines():
            parts = row.split("\x1f", 4)
            if len(parts) == 5:
                commits[parts[0]] = (parts[1], parts[4], int(parts[2]) if parts[2].isdigit() else None,
                                     len(parts[3].split()))
                where[parts[0]].add(repo)
    return commits, where, done


def find_commits(events, log, merge_seconds):
    """[{"id", "ts", "worktree", "branch"}] for commits made in the scratch repositories, one entry
    per commit id. A rebase reports its new commits twice: through post-commit while HEAD is
    detached (no branch), and again through post-rewrite once the branch is back. The first
    sighting keeps its time; a later one fills in the branch if the first had none."""
    rewritten, commits, known, origin = set(), [], {}, {}
    for e in events:
        if e.get("event") == "post-rewrite":
            for row in e.get("refs", []):
                if len(row) == 2 and row[0] != row[1]:
                    origin.setdefault(row[1], row[0])
        new = []
        if e.get("event") == "post-commit" and _is_id(e.get("commit")):
            new = [e["commit"]]
        elif e.get("event") == "post-rewrite" and e.get("args", [None])[:1] == ["rebase"]:
            new = [row[1] for row in e.get("refs", []) if len(row) == 2 and _is_id(row[1])]
        elif e.get("event") == "post-merge" and _is_id(e.get("commit")) and e["commit"] not in known \
                and e.get("args", [None])[:1] != ["1"]:                  # "1": a merge --squash
            _, _, committed, parents = log.get(e["commit"]) or (None, None, None, 0)
            if parents >= 2 and committed is not None and abs(committed - e["ts"]) <= merge_seconds:
                new = [e["commit"]]
        if e.get("event") == "post-rewrite":
            rewritten.update(row[0] for row in e.get("refs", []) if len(row) == 2 and row[0] != row[1])
        for cid in new:
            if cid in known:
                if not known[cid]["branch"]:
                    known[cid]["branch"] = e["head"]
                continue
            known[cid] = {"id": cid, "ts": e["ts"], "worktree": e["worktree"], "branch": e["head"]}
            commits.append(known[cid])
    # A commit that replaced another (amend, rebase) keeps the first sighting's time and place, so
    # it goes to the session that made the original, whenever the rewrite happened.
    for c in commits:
        root, hops = c["id"], set()
        while root in origin and root not in hops:
            hops.add(root)
            root = origin[root]
        if root != c["id"] and root in known:
            c["ts"], c["worktree"] = known[root]["ts"], known[root]["worktree"]
            c["branch"] = c["branch"] or known[root]["branch"]
    return [c for c in commits if c["id"] not in rewritten]


def session_branches(session, worktree, events):
    """The branches a session worked on in a working tree, or None when unknown: the branch the
    tree was on when the session started, and every branch a git line shows there while it was
    open (a checkout, or a commit on a branch renamed without any hook)."""
    before = [e["head"] for e in events if e["worktree"] == worktree and e["ts"] <= session.start and e["head"]]
    if not before:
        return None
    during = {e["head"] for e in events if e["worktree"] == worktree
              and session.start < e["ts"] <= session.end and e["head"]}
    return {before[-1]} | during


def link_commit(commit, sessions, events, grace):
    """(session key, "open"|"grace") for one commit, or None."""
    open_, late = [], []
    for key, s in sessions.items():
        if commit["worktree"] not in s.worktrees:
            continue
        branches = session_branches(s, commit["worktree"], events)
        if branches is not None and commit["branch"] and commit["branch"] not in branches:
            continue
        if s.start <= commit["ts"] <= s.end:
            open_.append((s.start, key))
        elif s.end < commit["ts"] <= s.end + grace:
            late.append((s.end, key))
    if open_:
        return max(open_)[1], "open"
    if late:
        return max(late)[1], "grace"
    return None


def _tree_of(cid, log, worktrees):
    if cid in log:
        return log[cid][0]
    for wt in sorted(worktrees):
        out = _git(wt, "rev-parse", "--verify", "-q", cid + "^{tree}") if os.path.isdir(wt) else None
        if out:
            return out.strip()
    return None


SQUASH_SLACK = 60.0                 # seconds: clock difference allowed between a commit and a later squash


def link(spool, prs=None, grace=120.0, merge_seconds=10.0, squash_slack=SQUASH_SLACK):
    """{"links": {(agent, session): [{"id", "rule"}]}, "unlinked": [...], "unreachable": [...],
    "squash": [...]}. A commit made on a detached HEAD that no post-rewrite kept and no ref
    reaches (the replays of an aborted rebase) is listed as unreachable, not linked; a commit made
    on a branch stays linked even after that branch is deleted. Squash links stay inside the squash commit's repository
    and take only commits made before it."""
    sessions = read_sessions(spool)
    events = read_git_events(spool)
    tops = {e["worktree"] for e in events}
    for s in sessions.values():
        s.worktrees = {worktree_of(c, tops) for c in s.cwds}
    worktrees = tops | {wt for s in sessions.values() for wt in s.worktrees}
    repos = {e["worktree"]: e["repo"] for e in events if e["repo"]}      # recorded by the hook itself
    log, where, read = read_log(worktrees, repos)
    links = defaultdict(list)
    unlinked, unreachable, on_branch, held = [], [], [], []
    for commit in find_commits(events, log, merge_seconds):
        if not commit["branch"] and commit["id"] not in log and repo_of(commit["worktree"], repos) in read:
            unreachable.append(commit["id"])            # made on a detached HEAD, never kept: an aborted rebase
            continue
        if SQUASH.search((log.get(commit["id"]) or ("", "", None, 0))[1]):
            held.append(commit)                                 # a squash is linked through its PR first
            continue
        hit = link_commit(commit, sessions, events, grace)
        if hit is None:
            unlinked.append(commit["id"])
        else:
            links[hit[0]].append({"id": commit["id"], "rule": hit[1]})
            on_branch.append((repo_of(commit["worktree"], repos), commit["branch"], commit["id"], hit[0],
                              commit["ts"]))
    squash_rows, by_pr = [], set()
    wanted = {int(n): ("refs/heads/" + b if not b.startswith("refs/") else b) for n, b in (prs or {}).items()}
    for cid, (tree, subject, committed, _) in sorted(log.items()):
        match = SQUASH.search(subject)
        if not match:
            continue
        pr = int(match.group(1))
        limit = (committed or 0) + squash_slack
        rows = [r for r in on_branch if r[0] in where.get(cid, ()) and r[4] <= limit]
        if pr in wanted:
            branch, how = wanted[pr], "by --pr"
        else:
            branches = {b for _, b, c, _, _ in rows if b and _tree_of(c, log, worktrees) == tree}
            branch = next(iter(branches)) if len(branches) == 1 else None
            how = "by tree" if branch else ("ambiguous" if branches else "no branch found")
        keys = sorted({key for _, b, _, key, _ in rows if branch and b == branch})
        for key in keys:
            links[key].append({"id": cid, "rule": f"squash #{pr} {how}"})
        if keys:
            by_pr.add(cid)
        squash_rows.append({"pr": pr, "commit": cid, "found": how, "sessions": len(keys)})
    for commit in held:                        # "(#N)" with no PR branch behind it: an ordinary commit
        if commit["id"] in by_pr:
            continue
        hit = link_commit(commit, sessions, events, grace)
        if hit is None:
            unlinked.append(commit["id"])
        else:
            links[hit[0]].append({"id": commit["id"], "rule": hit[1]})
    return {"links": dict(links), "unlinked": unlinked, "unreachable": unreachable, "squash": squash_rows,
            "sessions": len(sessions)}


def _same(a, b):
    a, b = a.lower(), b.lower()
    return min(len(a), len(b)) >= 7 and (a.startswith(b) or b.startswith(a))


def compare(result, truth):
    """Link rate rows per "by", from the ground truth."""
    rows = defaultdict(lambda: {"sessions_with_changes": 0, "linked_right": 0, "wrong_links": 0,
                                "missed_commits": 0})
    listed = set()
    for s in truth.get("sessions", []):
        key = (s.get("agent"), s.get("session_id"))
        listed.add(key)
        expected = [str(c) for c in s.get("commits", [])]
        linked = [c["id"] for c in result["links"].get(key, [])]
        row = rows[s.get("by", "agent") if expected else "no changes"]
        wrong = [c for c in linked if not any(_same(c, e) for e in expected)]
        missed = [e for e in expected if not any(_same(c, e) for c in linked)]
        row["wrong_links"] += len(wrong)
        if expected:
            row["sessions_with_changes"] += 1
            row["missed_commits"] += len(missed)
            row["linked_right"] += not wrong and not missed
    # A commit the truth gives to a listed session but the linker gave to an unlisted one is a
    # wrong link too, charged to the owner's row.
    owners = [(str(e), s.get("by", "agent")) for s in truth.get("sessions", []) for e in s.get("commits", [])]
    for key, commits in result["links"].items():
        if key in listed:
            continue
        for c in commits:
            by = next((b for e, b in owners if _same(c["id"], e)), None)
            if by is not None:
                rows[by]["wrong_links"] += 1
    unchecked = sum(1 for key in result["links"] if key not in listed)
    return {"rows": [dict(by=k, **v) for k, v in sorted(rows.items())], "sessions_linked_but_not_in_truth": unchecked}


def format_report(result, checked=None):
    out = [f"sessions in the spool | {result['sessions']}",
           f"sessions with a linked commit | {len(result['links'])}",
           f"commits linked to no session | {len(result['unlinked'])}",
           f"commits no ref reaches (aborted rebase, reset) | {len(result.get('unreachable', []))}", "",
           "squash | PR | commit | branch found | sessions linked"]
    out += [f"squash | {r['pr']} | {r['commit']} | {r['found']} | {r['sessions']}" for r in result["squash"]]
    out += ["", "link | agent | session | commit | rule"]
    for (agent, sid), commits in sorted(result["links"].items()):
        out += [f"link | {agent} | {sid} | {c['id']} | {c['rule']}" for c in commits]
    if checked is not None:
        out += ["", "link rate | by | sessions with changes | linked right | wrong links | missed commits"]
        out += [" | ".join(["link rate"] + [str(r[k]) for k in ("by", "sessions_with_changes", "linked_right",
                                                                 "wrong_links", "missed_commits")])
                for r in checked["rows"]]
        out.append(f"sessions linked but not in the ground truth | {checked['sessions_linked_but_not_in_truth']}")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="T1 session-to-commit linker (prints ids and numbers only).")
    p.add_argument("--spool", required=True)
    p.add_argument("--truth")
    p.add_argument("--pr", action="append", default=[], metavar="N=BRANCH")
    p.add_argument("--grace-seconds", type=float, default=120.0)
    p.add_argument("--merge-seconds", type=float, default=10.0)
    p.add_argument("--json", help="also write the result as JSON to this file")
    args = p.parse_args(argv)
    prs = {}
    for item in args.pr:
        number, _, branch = item.partition("=")
        if not number.isdigit() or not branch:
            p.error(f"--pr takes N=branch, got {item!r}")
        prs[number] = branch
    result = link(args.spool, prs, args.grace_seconds, args.merge_seconds)
    checked = None
    if args.truth:
        with open(args.truth, "r", encoding="utf-8") as fh:
            checked = compare(result, json.load(fh))
    print(format_report(result, checked))
    if args.json:
        data = {"links": [{"agent": a, "session_id": s, "commits": c} for (a, s), c in sorted(result["links"].items())],
                "unlinked": result["unlinked"], "unreachable": result["unreachable"], "squash": result["squash"],
                "checked": checked}
        with open(args.json, "x", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
