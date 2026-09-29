"""Tests for the session-to-commit linker. Every spool and repository is synthetic, in a temp folder."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import capture_githooks as gh
import capture_link as cl

SWITCHES = ["-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "maintenance.auto=false",
            "-c", "gc.auto=0", "-c", "user.name=Test", "-c", "user.email=t@example.com"]
SECRET = "SECRET-SUBJECT-77c1"


def ids(n):
    return (format(n, "x") * 40)[:40]                     # distinct from the first character on


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def agent_line(spool, agent, sid, seconds, cwd, event="PostToolUse"):
    write_jsonl(spool / agent / f"{sid}.jsonl", [{"ts": int(seconds * 1e9), "pid": 1, "agent": agent, "event": event,
                                                   "session_id": sid, "cwd": str(cwd)}])


def git_line(spool, seconds, cwd, event, head="refs/heads/main", commit=None, args=(), refs=None):
    row = {"ts": int(seconds * 1e9), "pid": 1, "agent": "git", "event": event, "cwd": str(cwd), "head": head,
           "args": list(args)}
    if commit is not None:
        row["commit"] = commit
    if refs is not None:
        row["refs"] = refs
    write_jsonl(spool / "git" / "2026-09-27.jsonl", [row])


def worktree(tmp_path, name):
    wt = tmp_path / name
    (wt / ".git").mkdir(parents=True)
    (wt / "sub").mkdir()
    return wt


def linked(result, agent, sid):
    return [(c["id"], c["rule"]) for c in result["links"].get((agent, sid), [])]


def test_commits_link_by_worktree_time_and_branch(tmp_path):
    spool = tmp_path / "spool"
    wt1, wt2 = worktree(tmp_path, "wt1"), worktree(tmp_path, "wt2")
    for t in (100, 200):
        agent_line(spool, "claude", "s1", t, wt1 / "sub")                    # a subfolder still maps to wt1
    for t in (150, 250):
        agent_line(spool, "codex", "s2", t, wt2)
    git_line(spool, 50, wt1, "post-checkout", head="refs/heads/a")          # s1 starts on branch a
    git_line(spool, 150, wt1, "post-commit", head="refs/heads/a", commit=ids(1))
    git_line(spool, 200, wt2, "post-commit", commit=ids(2))
    git_line(spool, 260, wt1, "post-commit", head="refs/heads/a", commit=ids(3))    # harness, 60 s after
    git_line(spool, 400, wt1, "post-commit", head="refs/heads/a", commit=ids(4))    # past the grace period
    git_line(spool, 270, wt1, "post-checkout", head="refs/heads/b")
    git_line(spool, 280, wt1, "post-commit", head="refs/heads/b", commit=ids(5))    # in grace, another branch
    result = cl.link(str(spool), grace=120)
    assert linked(result, "claude", "s1") == [(ids(1), "open"), (ids(3), "grace")]
    assert linked(result, "codex", "s2") == [(ids(2), "open")]
    assert sorted(result["unlinked"]) == [ids(4), ids(5)]


def test_rewritten_commits_are_dropped_and_rebased_ones_linked(tmp_path):
    spool = tmp_path / "spool"
    wt = worktree(tmp_path, "wt")
    for t in (100, 300):
        agent_line(spool, "claude", "s1", t, wt)
    git_line(spool, 150, wt, "post-commit", commit=ids(1))
    git_line(spool, 160, wt, "post-commit", commit=ids(2))                   # git commit --amend
    git_line(spool, 160, wt, "post-rewrite", args=["amend"], refs=[[ids(1), ids(2)]])
    git_line(spool, 170, wt, "post-commit", commit=ids(3))
    git_line(spool, 200, wt, "post-rewrite", args=["rebase"], refs=[[ids(3), ids(4)], [ids(2), ids(5)]])
    result = cl.link(str(spool))
    assert sorted(linked(result, "claude", "s1")) == [(ids(4), "open"), (ids(5), "open")]
    assert result["unlinked"] == []


def test_an_open_session_wins_then_the_latest_start(tmp_path):
    spool = tmp_path / "spool"
    wt = worktree(tmp_path, "wt")
    for sid, (a, b) in {"ended": (10, 90), "early": (20, 300), "late": (50, 300)}.items():
        agent_line(spool, "claude", sid, a, wt)
        agent_line(spool, "claude", sid, b, wt)
    git_line(spool, 100, wt, "post-commit", commit=ids(1))
    result = cl.link(str(spool))
    assert linked(result, "claude", "late") == [(ids(1), "open")]
    assert "ended" not in {k[1] for k in result["links"]} and "early" not in {k[1] for k in result["links"]}


# ----------------------------------------------------------------- real synthetic repositories


def git(repo, *args, env=None, check=True):
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    e.update(env or {})
    proc = subprocess.run(["git"] + SWITCHES + list(args), cwd=repo, capture_output=True, env=e, timeout=120)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr.decode(errors='replace')}")
    return proc.stdout.decode().strip()


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    root = tmp_path / "orch-phase0"
    repo = root / "repos" / "scratch"
    repo.mkdir(parents=True)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("1\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "base")
    gh.install(str(root), str(repo), sys.executable)
    return root, repo


def session_on_branch(root, repo, sid, branch, content):
    """A session in its own worktree that commits one file, bracketed by agent spool lines."""
    wt = root / "repos" / f"scratch-{branch}"
    git(repo, "worktree", "add", "-q", "-b", branch, str(wt))
    agent_line(root / "spool", "claude", sid, time.time(), wt, "SessionStart")
    (wt / "b.txt").write_text(content, encoding="utf-8")
    git(wt, "add", "b.txt")
    git(wt, "commit", "-q", "-m", f"{SECRET} on {branch}")
    commit = git(wt, "rev-parse", "HEAD")
    agent_line(root / "spool", "claude", sid, time.time(), wt, "SessionEnd")
    return commit


def squash(repo, branch, pr):
    git(repo, "merge", "-q", "--squash", branch)
    git(repo, "commit", "-q", "-m", f"{SECRET} (#{pr})")
    return git(repo, "rev-parse", "HEAD")


def test_a_squash_merge_links_through_its_pr_by_tree(scratch):
    root, repo = scratch
    commit = session_on_branch(root, repo, "s-feat", "feat", "2\n")
    merged = squash(repo, "feat", 12)
    result = cl.link(str(root / "spool"), grace=0)
    assert linked(result, "claude", "s-feat") == [(commit, "open"), (merged, "squash #12 by tree")]
    assert result["squash"] == [{"pr": 12, "commit": merged, "found": "by tree", "sessions": 1}]
    assert merged not in result["unlinked"], "a squash commit is linked through its PR only"
    assert result["unlinked"] == []                  # the merge --squash post-merge made no commit


def test_the_pr_branch_can_be_given_when_the_tree_is_ambiguous(scratch):
    root, repo = scratch
    first = session_on_branch(root, repo, "s-one", "one", "same\n")
    second = session_on_branch(root, repo, "s-two", "two", "same\n")        # the same tree on two branches
    merged = squash(repo, "one", 3)
    result = cl.link(str(root / "spool"), grace=0)
    assert result["squash"][0]["found"] == "ambiguous" and result["squash"][0]["sessions"] == 0
    assert linked(result, "claude", "s-one") == [(first, "open")]
    result = cl.link(str(root / "spool"), prs={"3": "one"}, grace=0)
    assert linked(result, "claude", "s-one") == [(first, "open"), (merged, "squash #3 by --pr")]
    assert linked(result, "claude", "s-two") == [(second, "open")]


def test_a_real_rebase_links_each_commit_once_and_its_squash_by_tree(scratch):
    # Found by T1: git rebase fires post-commit for each replayed commit while HEAD is detached,
    # then post-rewrite with the same ids; they were linked twice, and the squash of the rebased
    # branch read as ambiguous (one "branch" per sighting).
    root, repo = scratch
    wt = root / "repos" / "scratch-refactor"
    git(repo, "worktree", "add", "-q", "-b", "refactor", str(wt))
    (repo / "m.txt").write_text("main moves\n", encoding="utf-8")                 # main moves on
    git(repo, "add", "m.txt")
    git(repo, "commit", "-q", "-m", "main moves")
    moved = git(repo, "rev-parse", "HEAD")                                          # made outside any session
    agent_line(root / "spool", "claude", "s-rebase", time.time(), wt, "SessionStart")
    for i in (1, 2):
        (wt / "r.txt").write_text(f"{i}\n", encoding="utf-8")
        git(wt, "add", "r.txt")
        git(wt, "commit", "-q", "-m", f"refactor {i}")
    git(wt, "rebase", "-q", "main")
    rebased = git(wt, "rev-list", "--reverse", "main..HEAD").split()
    agent_line(root / "spool", "claude", "s-rebase", time.time(), wt, "SessionEnd")
    merged = squash(repo, "refactor", 21)
    result = cl.link(str(root / "spool"), grace=0)
    assert linked(result, "claude", "s-rebase") == [(rebased[0], "open"), (rebased[1], "open"),
                                                     (merged, "squash #21 by tree")]
    assert result["unlinked"] == [moved]


def test_a_merge_commit_counts_but_a_fast_forward_does_not(scratch):
    root, repo = scratch
    git(repo, "branch", "old")
    git(repo, "branch", "side")
    git(repo, "checkout", "-q", "old")
    (repo / "o.txt").write_text("o\n", encoding="utf-8")
    git(repo, "add", "o.txt")
    past = {"GIT_COMMITTER_DATE": "2026-09-20T10:00:00+00:00", "GIT_AUTHOR_DATE": "2026-09-20T10:00:00+00:00"}
    git(repo, "commit", "-q", "-m", "made days ago", env=past)
    git(repo, "checkout", "-q", "side")
    (repo / "s.txt").write_text("s\n", encoding="utf-8")
    git(repo, "add", "s.txt")
    git(repo, "commit", "-q", "-m", "side", env=past)
    git(repo, "checkout", "-q", "main")
    shutil_spool = root / "spool"
    agent_line(shutil_spool, "claude", "s-main", time.time(), repo, "SessionStart")
    git(repo, "merge", "-q", "--ff-only", "old")                                 # fast-forward: no new commit
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = git(repo, "rev-parse", "HEAD")
    agent_line(shutil_spool, "claude", "s-main", time.time(), repo, "SessionEnd")
    result = cl.link(str(shutil_spool), grace=0)
    assert linked(result, "claude", "s-main") == [(merge, "open")]


# ----------------------------------------------------------------- the ground truth and the output


def test_the_link_rate_counts_right_wrong_and_missed():
    result = {"links": {("claude", "right"): [{"id": ids(1), "rule": "open"}],
                        ("claude", "wrong"): [{"id": ids(2), "rule": "open"}, {"id": ids(9), "rule": "grace"}],
                        ("codex", "harness"): [{"id": ids(3), "rule": "grace"}],
                        ("claude", "nochange"): [{"id": ids(8), "rule": "grace"}],
                        ("claude", "unlisted"): [{"id": ids(7), "rule": "open"}]},
              "unlinked": [], "squash": [], "sessions": 6}
    truth = {"sessions": [
        {"agent": "claude", "session_id": "right", "commits": [ids(1)[:7]], "by": "agent"},     # a short id
        {"agent": "claude", "session_id": "wrong", "commits": [ids(2)], "by": "agent"},
        {"agent": "claude", "session_id": "missed", "commits": [ids(4)], "by": "agent"},
        {"agent": "codex", "session_id": "harness", "commits": [ids(3)], "by": "harness"},
        {"agent": "claude", "session_id": "nochange", "commits": []}]}
    checked = cl.compare(result, truth)
    rows = {r["by"]: r for r in checked["rows"]}
    assert rows["agent"] == {"by": "agent", "sessions_with_changes": 3, "linked_right": 1, "wrong_links": 1,
                             "missed_commits": 1}
    assert rows["harness"]["linked_right"] == 1
    assert rows["no changes"]["wrong_links"] == 1 and rows["no changes"]["sessions_with_changes"] == 0
    assert checked["sessions_linked_but_not_in_truth"] == 1
    assert not cl._same("abc", "abcdef0")                                        # too short to match


def test_the_output_holds_ids_and_numbers_only(scratch, tmp_path):
    root, repo = scratch
    commit = session_on_branch(root, repo, "s-feat", "feat-SECRETBRANCH", "2\n")
    squash(repo, "feat-SECRETBRANCH", 5)
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"sessions": [{"agent": "claude", "session_id": "s-feat", "commits": [commit]}]}),
                     encoding="utf-8")
    out = tmp_path / "links.json"
    proc = subprocess.run([sys.executable, str(Path(cl.__file__)), "--spool", str(root / "spool"), "--truth",
                           str(truth), "--grace-seconds", "0", "--json", str(out)], capture_output=True, text=True,
                          timeout=120, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.returncode == 0, proc.stderr
    for text in (proc.stdout, out.read_text(encoding="utf-8")):
        assert SECRET not in text and "SECRETBRANCH" not in text and str(tmp_path) not in text
    assert f"link | claude | s-feat | {commit} | open" in proc.stdout
    assert "link rate | agent | 1 | 0 | 1 | 0" in proc.stdout        # the squash is not in this truth: a wrong link


# ----------------------------------------------------------------- fixes from the T1 tools review


def open_session(root, sid, cwd):
    agent_line(root / "spool", "claude", sid, time.time(), cwd, "SessionStart")


def close_session(root, sid, cwd):
    agent_line(root / "spool", "claude", sid, time.time(), cwd, "SessionEnd")


def commit_file(wt, name, text, message):
    (wt / name).write_text(text, encoding="utf-8")
    git(wt, "add", name)
    git(wt, "commit", "-q", "-m", message)
    return git(wt, "rev-parse", "HEAD")


def test_a_squash_links_only_inside_its_own_repository(scratch):
    root, repo = scratch
    other = root / "repos" / "other"
    other.mkdir()
    git(other, "init", "-q")
    commit_file(other, "o.txt", "o\n", "base")
    gh.install(str(root), str(other), sys.executable)
    session_on_branch(root, repo, "s-here", "feat", "2\n")
    wt = root / "repos" / "other-feat"
    git(other, "worktree", "add", "-q", "-b", "feat", str(wt))
    open_session(root, "s-there", wt)
    commit_file(wt, "b.txt", "3\n", "work there")
    close_session(root, "s-there", wt)
    merged = squash(repo, "feat", 1)
    result = cl.link(str(root / "spool"), prs={"1": "feat"}, grace=0)
    assert (merged, "squash #1 by --pr") in linked(result, "claude", "s-here")
    assert merged not in [c for c, _ in linked(result, "claude", "s-there")]


def test_a_squash_takes_no_commit_made_after_it(scratch):
    root, repo = scratch
    session_on_branch(root, repo, "s-round1", "task", "1\n")
    merged = squash(repo, "task", 5)
    time.sleep(2.5)
    git(repo, "worktree", "remove", "--force", str(root / "repos" / "scratch-task"))
    git(repo, "branch", "-D", "task")
    session_on_branch(root, repo, "s-round2", "task", "2\n")                          # the name is reused later
    result = cl.link(str(root / "spool"), prs={"5": "task"}, grace=0, squash_slack=1)
    assert (merged, "squash #5 by --pr") in linked(result, "claude", "s-round1")
    assert merged not in [c for c, _ in linked(result, "claude", "s-round2")]


def test_a_commit_rewritten_later_stays_with_its_session(scratch):
    root, repo = scratch
    wt = root / "repos" / "scratch-late"
    git(repo, "worktree", "add", "-q", "-b", "late", str(wt))
    open_session(root, "s-orig", wt)
    commit_file(wt, "l.txt", "1\n", "original")
    close_session(root, "s-orig", wt)
    time.sleep(1.0)
    git(wt, "commit", "-q", "--amend", "-m", "amended long after the session")   # outside the grace period
    amended = git(wt, "rev-parse", "HEAD")
    result = cl.link(str(root / "spool"), grace=0)
    assert linked(result, "claude", "s-orig") == [(amended, "open")]
    assert result["unlinked"] == []


def test_commits_left_by_an_aborted_rebase_are_unreachable_not_linked(scratch):
    root, repo = scratch
    wt = root / "repos" / "scratch-abort"
    git(repo, "worktree", "add", "-q", "-b", "abort", str(wt))
    open_session(root, "s-abort", wt)
    c1 = commit_file(wt, "x.txt", "x\n", "x")
    c2 = commit_file(wt, "y.txt", "mine\n", "y")
    commit_file(repo, "y.txt", "theirs\n", "main changes y")
    git(wt, "rebase", "main", check=False)                                          # c1 replays, c2 conflicts
    git(wt, "rebase", "--abort")
    close_session(root, "s-abort", wt)
    result = cl.link(str(root / "spool"), grace=0)
    assert linked(result, "claude", "s-abort") == [(c1, "open"), (c2, "open")]
    assert len(result["unreachable"]) == 1 and result["unreachable"][0] not in (c1, c2)


def test_an_ordinary_commit_ending_in_a_pr_number_is_still_linked(scratch):
    root, repo = scratch
    wt = root / "repos" / "scratch-issue"
    git(repo, "worktree", "add", "-q", "-b", "issue", str(wt))
    open_session(root, "s-issue", wt)
    commit = commit_file(wt, "i.txt", "i\n", "Fix the bug (#3)")
    close_session(root, "s-issue", wt)
    result = cl.link(str(root / "spool"), grace=0)
    assert linked(result, "claude", "s-issue") == [(commit, "open")]


def test_a_branch_renamed_during_the_session_still_links(scratch):
    root, repo = scratch
    wt = root / "repos" / "scratch-tmp"
    git(repo, "worktree", "add", "-q", "-b", "tmp", str(wt))
    open_session(root, "s-rename", wt)
    git(wt, "branch", "-m", "tmp", "final")                                          # fires no hook
    commit = commit_file(wt, "r.txt", "r\n", "on the renamed branch")
    close_session(root, "s-rename", wt)
    result = cl.link(str(root / "spool"), grace=0)
    assert linked(result, "claude", "s-rename") == [(commit, "open")]


def test_a_removed_nested_worktree_keeps_its_own_sessions(scratch):
    root, repo = scratch
    nested = repo / ".wt" / "a"
    git(repo, "worktree", "add", "-q", "-b", "nested", str(nested))
    (nested / "sub").mkdir()
    open_session(root, "s-main", repo)
    open_session(root, "s-nested", nested / "sub")
    commit = commit_file(nested, "n.txt", "n\n", "nested work")
    close_session(root, "s-nested", nested / "sub")
    close_session(root, "s-main", repo)
    git(repo, "worktree", "remove", "--force", str(nested))
    result = cl.link(str(root / "spool"), grace=0)
    assert linked(result, "claude", "s-nested") == [(commit, "open")]
    assert linked(result, "claude", "s-main") == []


def test_a_commit_taken_by_a_session_missing_from_the_truth_is_a_wrong_link():
    result = {"links": {("claude", "unlisted"): [{"id": ids(2), "rule": "open"}]}, "unlinked": [], "squash": [],
              "sessions": 2}
    truth = {"sessions": [{"agent": "claude", "session_id": "listed", "commits": [ids(2)], "by": "agent"}]}
    rows = {r["by"]: r for r in cl.compare(result, truth)["rows"]}
    assert rows["agent"]["wrong_links"] == 1 and rows["agent"]["missed_commits"] == 1
