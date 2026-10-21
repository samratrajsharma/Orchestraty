"""The public-history scan on a local stand-in for GitHub: a repository with pull-request heads
under refs/pull/, cloned partially over file://, with agent pull requests merged by squash and
by merge commit, a person's, a fork's, then a fix and a revert. gh is never called."""
import json
import os
import subprocess

import pytest

import history_public as hp
from test_history_pairs import A_PY, B_NEW, B_PY, BROKEN, FIXED, git, write

NOW = hp._time("2026-10-20T12:00:00Z")
FOOTER = "\n\n\U0001f916 Generated with [an agent](https://claude.com/claude-code)"


def out_of(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git"] + list(args), cwd=str(cwd), env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def raws(sha):
    """The pull requests as `gh pr list --json` gives them."""
    def pr(n, login, body, created, merged, branch, head, base_oid, merge, bot=False, fork=False):
        return {"number": n, "author": {"login": login, "is_bot": bot}, "body": body, "createdAt": created,
                "mergedAt": merged, "baseRefName": "main", "baseRefOid": base_oid, "headRefName": branch,
                "headRefOid": head, "isCrossRepository": fork, "mergeCommit": {"oid": merge}}
    return [
        pr(1, "dev-one", "Keyword-only currency." + FOOTER, "2026-10-02T10:00:00Z", "2026-10-03T09:00:00Z",
           "feature/kw", sha["pr1"], sha["base"], sha["s1"]),
        pr(2, "app/copilot-swe-agent", "Adds refund.", "2026-10-02T11:00:00Z", "2026-10-03T10:00:00Z",
           "copilot/add-refund", sha["pr2"], sha["s1"], sha["m2"], bot=True),
        pr(3, "dev-two", "Tidy.", "2026-10-03T09:30:00Z", "2026-10-03T12:00:00Z",
           "codex/tidy", sha["pr3"], sha["m2"], sha["s3"]),
        pr(4, "dev-two", "A person's change.", "2026-10-02T10:30:00Z", "2026-10-02T18:00:00Z",
           "fix-docs", "4" * 40, sha["base"], "5" * 40),
        pr(5, "outsider", "From a fork." + FOOTER, "2026-10-02T12:00:00Z", "2026-10-02T20:00:00Z",
           "patch-1", "6" * 40, sha["base"], "7" * 40, fork=True),
        pr(6, "app/copilot-swe-agent", "Long ago.", "2026-03-01T10:00:00Z", "2026-03-02T10:00:00Z",
           "copilot/old", "8" * 40, sha["base"], "9" * 40, bot=True),
    ]


def fake_gh(pulls, calls):
    def run(args):
        calls.append(list(args))
        if args[:4] == ["api", "-X", "GET", "search/issues"]:
            query = next(a for a in args if a.startswith("q="))
            numbers = [n for key, found in (("Claude Code", [1, 5]), ("copilot-swe-agent", [2])) if key in query
                       for n in found]
            return {"total_count": len(numbers),
                    "items": [{"repository_url": "https://api.github.com/repos/o/r", "number": n} for n in numbers]}
        if args[:2] == ["pr", "list"]:
            return pulls
        if args[0] == "api":
            return {"size": 2048, "stargazers_count": 5, "fork": False, "archived": False, "language": "Python"}
        raise AssertionError(args)
    return run


def github(tmp_path):
    """The stand-in repository, and the shas its pull requests name."""
    src = tmp_path / "src"
    src.mkdir()
    git(src, "2026-10-01T09:00:00Z", "init", "-q")
    write(src / "a.py", A_PY)
    write(src / "b.py", B_PY)
    write(src / "c.py", "def tidy():\n    return 1\n")
    write(src / "notes.txt", "first\n" * 200)
    git(src, "2026-10-01T09:00:00Z", "add", "-A")
    git(src, "2026-10-01T09:00:00Z", "commit", "-q", "-m", "start")
    sha = {"old_notes": out_of(src, "rev-parse", "HEAD:notes.txt")}
    write(src / "notes.txt", "second\n" * 200)               # the first notes are history only
    git(src, "2026-10-01T09:30:00Z", "commit", "-q", "-am", "notes")
    sha["base"] = out_of(src, "rev-parse", "HEAD")
    git(src, "2026-10-02T10:00:00Z", "checkout", "-q", "-b", "pr1")           # 1: currency keyword-only
    write(src / "a.py", BROKEN)
    git(src, "2026-10-02T10:20:00Z", "commit", "-q", "-am", "keyword-only currency")
    git(src, "2026-10-02T11:00:00Z", "checkout", "-q", "-b", "pr2", "main")   # 2: a new caller
    write(src / "b.py", B_NEW)
    git(src, "2026-10-02T11:30:00Z", "commit", "-q", "-am", "refund")
    git(src, "2026-10-03T09:00:00Z", "checkout", "-q", "main")
    git(src, "2026-10-03T09:00:00Z", "merge", "-q", "--squash", "pr1")
    git(src, "2026-10-03T09:00:00Z", "commit", "-q", "-m", "Keyword-only currency (#1)")
    sha["s1"] = out_of(src, "rev-parse", "HEAD")
    git(src, "2026-10-03T09:30:00Z", "checkout", "-q", "-b", "codex/tidy")    # 3: from main after 1
    write(src / "c.py", "def tidy():\n    return 2\n")
    git(src, "2026-10-03T09:40:00Z", "commit", "-q", "-am", "tidy")
    git(src, "2026-10-03T10:00:00Z", "checkout", "-q", "main")
    git(src, "2026-10-03T10:00:00Z", "merge", "-q", "--no-ff", "-m", "Merge pull request #2", "pr2")
    sha["m2"] = out_of(src, "rev-parse", "HEAD")
    git(src, "2026-10-03T12:00:00Z", "merge", "-q", "--squash", "codex/tidy")
    git(src, "2026-10-03T12:00:00Z", "commit", "-q", "-m", "Tidy (#3)")
    sha["s3"] = out_of(src, "rev-parse", "HEAD")
    write(src / "b.py", FIXED)
    git(src, "2026-10-05T09:00:00Z", "commit", "-q", "-am", "pass currency by name")
    git(src, "2026-10-06T09:00:00Z", "revert", "--no-edit", sha["s3"])
    for n, branch in ((1, "pr1"), (2, "pr2"), (3, "codex/tidy")):
        sha["pr%d" % n] = out_of(src, "rev-parse", branch)
        git(src, "2026-10-06T09:00:00Z", "update-ref", "refs/pull/%d/head" % n, branch)
    git(src, "2026-10-06T09:00:00Z", "config", "uploadpack.allowFilter", "true")
    git(src, "2026-10-06T09:00:00Z", "config", "uploadpack.allowAnySHA1InWant", "true")
    return src, sha


@pytest.fixture
def scanned(tmp_path):
    src, sha = github(tmp_path)
    out = tmp_path / "out"
    result = hp.scan(["o/r"], str(out), days=183, now=NOW, run=fake_gh(raws(sha), []), url=lambda name: src.as_uri())
    return result, out, sha


def test_parallel_agent_pairs_their_overlaps_and_what_followed(scanned):
    result, _, _ = scanned
    [repo] = result["repos"]
    assert (repo["merged_prs"], repo["agent_prs"], repo["pairs_total"]) == (5, 4, 2)   # 6 merged before the window
    assert repo["markers"] == {"claude-code": 2, "app": 1, "branch": 1}
    assert repo["left_out"] == {"fork": 1, "no_head": 0, "no_base": 0, "pairs": 0}
    pairs = {(p["a"]["number"], p["b"]["number"]): p for p in repo["pairs"]}
    assert set(pairs) == {(1, 2), (2, 3)}                                   # 1 merged before 3 opened; 4 is a person's
    rules = {f["rule"]: f for f in pairs[(1, 2)]["findings"]}
    assert set(rules) == {"O3", "O6"}
    assert rules["O3"]["symbol"] == "a.py::charge" and rules["O3"]["file"] == "b.py"
    assert {u["symbol"] for u in rules["O3"]["detail"]["uses"]} == {"b.py::pay", "b.py::refund"}
    assert [c["symbol"] for c in rules["O6"]["detail"]["left"]] == ["b.py::pay"]
    assert pairs[(1, 2)]["followed"] == {"conflict": False, "revert": False, "fix": True, "window_days": 14.0}
    assert pairs[(2, 3)]["findings"] == []
    assert pairs[(2, 3)]["followed"]["revert"] is True and pairs[(2, 3)]["followed"]["fix"] is False


def test_the_clone_is_partial_and_holds_only_what_was_read(scanned):
    _, out, sha = scanned
    top = out / "repos" / "o__r"
    assert out_of(top, "config", "remote.origin.promisor") == "true"
    assert out_of(top, "for-each-ref", "--format=%(refname)", "refs/pr/").split() == ["refs/pr/1", "refs/pr/2",
                                                                                     "refs/pr/3"]
    missing = out_of(top, "rev-list", "--objects", "--all", "--missing=print").split()
    assert "?" + sha["old_notes"] in missing                               # history only: never fetched
    assert not (top / "a.py").exists()                                     # nothing checked out


def test_the_counts_the_report_and_no_descriptions_kept(scanned):
    result, out, _ = scanned
    c = hp.public_counts(result)
    assert (c["parallel_pairs"], c["strong_pairs"], c["strong_rate"], c["reading"]) == (2, 1, 0.5, "pass")
    assert c["strong_rate_95"] == [0.5, 0.5]
    assert (c["parallel_pairs_found"], c["max_pairs_per_repository"], c["repository_rate_median"]) == (2, 300, 0.5)
    assert (c["pairs_with"]["O3"], c["pairs_with"]["O6"], c["pairs_with"]["O1"]) == (1, 1, 0)
    assert c["strong_pairs_followed_by"]["fix"] == 1 and c["overlap_pairs"] == 1
    assert c["left_out"]["fork"] == 1 and c["agent_prs_by_marker"]["app"] == 1
    day = out / "2026-10-20"
    assert json.loads((day / "public-counts.json").read_text(encoding="utf-8")) == c
    page = (day / "public-report.html").read_text(encoding="utf-8")
    assert "https://github.com/o/r/pull/1" in page and "a.py::charge" in page and "<b>pass</b>" in page
    kept = (out / "prs" / "o__r.json").read_text(encoding="utf-8")
    assert "Generated with" not in kept and "Keyword-only currency" not in kept


def test_thousands_of_paths_do_not_overflow_a_command_line(scanned):
    from history_changes import files_at
    _, out, sha = scanned
    top = str(out / "repos" / "o__r")
    many = ["deep/" * 25 + "f%d.py" % i for i in range(3000)]        # far past Windows' 32,767 characters
    assert hp.prefetch(top, [(sha["s3"], many + ["c.py"])]) >= 0
    found = files_at(top, sha["s3"], many + ["a.py"])
    assert found["a.py"] is not None and all(found[p] is None for p in many)


def test_a_rerun_the_same_day_reuses_each_repository_scanned(scanned, monkeypatch):
    result, out, _ = scanned

    def again(*args, **kwargs):
        raise AssertionError("scanned a second time")
    monkeypatch.setattr(hp, "scan_repo", again)
    rerun = hp.scan(["o/r"], str(out), days=183, now=NOW, run=fake_gh([], []), url=lambda name: "unused")
    assert rerun["repos"] == result["repos"]


def test_sampling_caps_the_pairs_and_draws_the_same_ones_again():
    pairs = [(i, i + 1) for i in range(50)]
    first = hp.sample_pairs(pairs, 10, "o/r")
    assert len(first) == 10 and first == hp.sample_pairs(pairs, 10, "o/r") and first == sorted(first)
    assert first != hp.sample_pairs(pairs, 10, "o/other")
    assert hp.sample_pairs(pairs, 0, "o/r") == pairs and hp.sample_pairs(pairs[:5], 10, "o/r") == pairs[:5]


def test_only_a_few_indexes_are_kept_in_memory(monkeypatch):
    class Built:
        def __init__(self):
            self._built, self.asked = {}, []

        def get(self, top, commit):
            self.asked.append(commit)
            self._built[(top, commit)] = None if commit == "gone" else object()
            return self._built[(top, commit)]
    monkeypatch.setattr(hp, "prefetch", lambda top, wants: 0)
    inner = Built()
    fetching = hp._Fetching(inner)
    first = fetching.get("t", "c0")
    assert fetching.get("t", "c0") is first and inner.asked == ["c0"]
    for n in range(1, 10):
        fetching.get("t", "c%d" % n)
    assert len(fetching.kept) == hp._Fetching.KEEP and not inner._built
    assert fetching.get("t", "gone") is None and fetching.get("t", "gone") is None and inner.asked.count("gone") == 1


def test_select_lists_candidates_and_clones_nothing(tmp_path):
    sha = {k: "%040d" % i for i, k in enumerate(("base", "s1", "m2", "s3", "pr1", "pr2", "pr3"))}
    calls = []
    doc = hp.select(days=183, now=NOW, out_dir=str(tmp_path), run=fake_gh(raws(sha), calls), pace=0)
    [row] = doc["candidates"]
    assert (row["repo"], row["size_mb"], row["agent_prs"], row["agent_prs_from_forks"], row["parallel_pairs"]) == \
        ("o/r", 2.0, 4, 1, 2)
    assert (doc["repositories_found"], doc["searches_cut_short"]) == (1, 0)
    assert {c[0] for c in calls} == {"pr", "api"} and all(c[1] == "list" for c in calls if c[0] == "pr")
    assert not (tmp_path / "repos").exists()
    assert "o/r" in hp.candidates_table(doc)


@pytest.mark.parametrize("raw, marker", [
    ({"body": "Fixes it." + FOOTER}, "claude-code"),
    ({"body": "generated with an agent"}, "claude-code"),
    ({"body": "https://chatgpt.com/codex/tasks/task_e_1"}, "codex"),
    ({"author": {"login": "app/copilot-swe-agent", "is_bot": True}}, "app"),
    ({"author": {"login": "Copilot", "type": "Bot"}}, "app"),
    ({"author": {"login": "devin-ai-integration[bot]", "is_bot": True}}, "app"),
    ({"author": {"login": "copilot", "is_bot": False}}, None),             # a person who took the name
    ({"author": {"login": "dependabot[bot]", "is_bot": True}}, None),      # a bot, not a coding agent
    ({"headRefName": "codex/fix-typo"}, "branch"),
    ({"headRefName": "feature/codex"}, None),
    ({"body": "Fixes the parser.", "author": {"login": "dev"}}, None),
])
def test_what_makes_a_pull_request_an_agents(raw, marker):
    assert hp.agent_marker(raw) == marker


def test_parallel_pairs_share_a_base_branch_and_an_open_period():
    def pr(n, created, merged, base="main", agent="app", fork=False):
        return {"number": n, "agent": agent, "created": created, "merged": merged, "base": base, "fork": fork}
    prs = [pr(1, 0, 10), pr(2, 10, 20), pr(3, 5, 8, base="dev"), pr(4, 21, 30), pr(5, 1, 2, agent=None),
           pr(6, 1, 30, fork=True), pr(7, 2, None)]
    assert [(a["number"], b["number"]) for a, b in hp.parallel_prs(prs)] == [(1, 2)]


def test_a_conflict_counts_only_in_a_file_both_changed(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    when = "2026-10-01T09:00:00Z"
    git(repo, when, "init", "-q")
    write(repo / "f.py", "a\nb\nc\nd\ne\n")
    write(repo / "g.py", "x\n")
    git(repo, when, "add", "-A")
    git(repo, when, "commit", "-q", "-m", "start")
    start = out_of(repo, "rev-parse", "HEAD")
    for branch, files, at in (("x", {"f.py": "A\nb\nc\nd\ne\n", "g.py": "X\n"}, "main"),
                              ("main", {"g.py": "M\n"}, None),
                              ("y", {"f.py": "Z\nb\nc\nd\ne\n"}, "main"),
                              ("v", {"f.py": "a\nb\nc\nd\nE\n"}, "main")):
        git(repo, when, "checkout", "-q", *(["-b", branch, at] if at else [branch]))
        for name, text in files.items():
            write(repo / name, text)
        git(repo, when, "commit", "-q", "-am", branch)
        if branch == "main":
            moved = out_of(repo, "rev-parse", "HEAD")
        git(repo, when, "checkout", "-q", "main")

    def change(n, head, base):
        pr = {"number": n, "head": out_of(repo, "rev-parse", head), "base_oid": base, "merge_commit": "",
              "created": 0, "merged": 1}
        return hp.pr_change(str(repo), "o/r", pr)
    x, y, v = change(1, "x", start), change(2, "y", moved), change(3, "v", moved)
    assert (x.work.files, y.work.files, v.work.files) == (["f.py", "g.py"], ["f.py"], ["f.py"])
    assert hp.pair_conflict(str(repo), x, y) is True                       # both rewrote the first line of f.py
    assert hp.pair_conflict(str(repo), x, v) is False                      # g.py clashes with main, not with v
