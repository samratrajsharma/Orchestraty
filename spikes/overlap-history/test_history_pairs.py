"""The overlap history end to end on a real (tiny) repository: two sessions in two worktrees at
the same time, one breaking a function the other's file calls, then a merge and a fix."""
import json
import os
import subprocess

import pytest

from history_cli import run
from history_logs import parse_time
from history_report import counts, counts_json, report_html

NOW = parse_time("2026-10-20T12:00:00Z")
SWITCHES = ["-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
            "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "user.name=T", "-c", "user.email=t@example.com"]

A_PY = "def charge(amount, currency):\n    return amount\n"
B_PY = "from a import charge\n\n\ndef pay():\n    return charge(1, 'EUR')\n"
BROKEN = "def charge(amount, *, currency):\n    return amount\n"
B_NEW = B_PY + "\n\ndef refund():\n    return charge(2, 'USD')\n"
FIXED = "from a import charge\n\n\ndef pay():\n    return charge(1, currency='EUR')\n" + B_NEW[len(B_PY):]


def git(cwd, when, *args):
    stamp = "%s +0000" % when.replace("T", " ").rstrip("Z")
    env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    env = {k: v for k, v in env.items() if not k.startswith("GIT_") or k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")}
    subprocess.run(["git"] + SWITCHES + list(args), cwd=str(cwd), env=env, check=True, capture_output=True)


def write(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def log_lines(sid, cwd, events):
    """Session-log lines: (when, kind, path) with kind "prompt" or "edit"."""
    out = []
    for n, (when, kind, path) in enumerate(events):
        if kind == "prompt":
            out.append({"type": "user", "cwd": str(cwd), "sessionId": sid, "timestamp": when,
                        "message": {"role": "user", "content": "SECRET PROMPT"}})
        else:
            use = "t%d" % n
            out.append({"type": "assistant", "cwd": str(cwd), "sessionId": sid, "timestamp": when,
                        "message": {"content": [{"type": "tool_use", "id": use, "name": "Edit",
                                                 "input": {"file_path": str(path), "old_string": "x",
                                                           "new_string": "y"}}]}})
            out.append({"type": "user", "cwd": str(cwd), "sessionId": sid, "timestamp": when,
                        "message": {"content": [{"type": "tool_result", "tool_use_id": use, "content": "ok"}]},
                        "toolUseResult": {"filePath": str(path), "structuredPatch": [{"newStart": 1, "newLines": 2}]}})
    return out


def write_log(projects, sid, lines):
    folder = projects / "C--x"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (sid + ".jsonl")
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    os.utime(str(path), (NOW, NOW))


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "2026-10-15T09:00:00Z", "init", "-q")
    write(repo / "a.py", A_PY)
    write(repo / "b.py", B_PY)
    git(repo, "2026-10-15T09:00:00Z", "add", "-A")
    git(repo, "2026-10-15T09:00:00Z", "commit", "-q", "-m", "base")
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "2026-10-15T09:30:00Z", "worktree", "add", "-q", "-b", "a", str(wa))
    git(repo, "2026-10-15T09:30:00Z", "worktree", "add", "-q", "-b", "b", str(wb))
    # A: 10:00-10:30 makes currency keyword-only and commits at 10:20.
    write(wa / "a.py", BROKEN)
    git(wa, "2026-10-15T10:20:00Z", "commit", "-q", "-am", "keyword-only currency")
    # B: 10:10-10:40 adds a caller in b.py and commits at 10:35.
    write(wb / "b.py", B_NEW)
    git(wb, "2026-10-15T10:35:00Z", "commit", "-q", "-am", "refund")
    # Later: both merged into main; a day after, a fix to pay.
    git(repo, "2026-10-16T09:00:00Z", "merge", "-q", "--no-edit", "a")
    git(repo, "2026-10-16T09:05:00Z", "merge", "-q", "--no-edit", "b")
    write(repo / "b.py", FIXED)
    git(repo, "2026-10-17T09:00:00Z", "commit", "-q", "-am", "pass currency by name")
    projects = tmp_path / "projects"
    write_log(projects, "sa", log_lines("sa", wa, [("2026-10-15T10:00:00Z", "prompt", None),
                                                   ("2026-10-15T10:15:00Z", "edit", wa / "a.py"),
                                                   ("2026-10-15T10:30:00Z", "prompt", None)]))
    write_log(projects, "sb", log_lines("sb", wb, [("2026-10-15T10:10:00Z", "prompt", None),
                                                   ("2026-10-15T10:30:00Z", "edit", wb / "b.py"),
                                                   ("2026-10-15T10:40:00Z", "prompt", None)]))
    write_log(projects, "sc", log_lines("sc", wb, [("2026-10-15T14:00:00Z", "edit", wb / "b.py")]))  # later
    return run(str(projects), days=30, now=NOW)


def test_a_parallel_pair_with_a_strong_overlap_and_what_followed(history):
    [pair] = history["pairs"]
    assert {pair["a"]["session"], pair["b"]["session"]} == {"sa", "sb"} and pair["active"]
    assert pair["a"]["committed"] and pair["b"]["committed"]
    rules = {f["rule"]: f for f in pair["findings"]}
    assert set(rules) == {"O3", "O6"}
    o3 = rules["O3"]
    assert o3["symbol"] == "a.py::charge" and o3["file"] == "b.py"
    assert {u["symbol"] for u in o3["detail"]["uses"]} == {"b.py::pay", "b.py::refund"}
    assert o3["detail"]["reasons"][0] == "parameter currency can no longer be passed by position"
    assert [c["symbol"] for c in rules["O6"]["detail"]["left"]] == ["b.py::pay"]
    assert pair["followed"]["conflict"] is False                 # merged, no text conflict
    assert pair["followed"]["fix"] is True and pair["followed"]["revert"] is False


def test_the_counts_file_holds_numbers_only(history):
    c = counts(history)
    assert c["parallel_pairs"] == 1 and c["strong_overlaps"] == 2 and c["overlaps"]["O1"] == 0
    assert c["strong_pairs_followed_by"]["fix"] == 1
    text = counts_json(history)
    for name in ("sa", "sb", "a.py", "b.py", "charge", "pay", "repo", "wt-a", "SECRET"):
        assert '"%s"' % name not in text and name + "." not in text.replace("overlap-history", "")

    def leaves(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield from leaves(v)
        else:
            yield value
    assert all(isinstance(v, (int, float)) or v == "overlap-history" for v in leaves(c))


def test_the_local_report_names_what_overlapped_and_escapes_it(history):
    history["pairs"][0]["findings"][0]["symbol"] = "a.py::<script>alert(1)</script>"
    page = report_html(history)
    assert "b.py::pay" in page and "<script>alert" not in page and "&lt;script&gt;" in page
    assert "SECRET" not in page
