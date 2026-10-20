"""Awareness runner: for each task pair, two headless Claude Code sessions in two worktrees at
once, with or without Orchestraty; then each branch alone, the merge, and the combination check.

Status: throwaway (Phase 0). Not part of the product.

    python aware_run.py --pairs <folder> --out <folder> [--repeats 2] [--only ID,...]
                        [--conditions with,without] [--limit N] [--wait-on-limit SECONDS]

A pair file (JSON, one per pair, written before any run):
    {"id": "...", "repo": "<path of a git repository>", "base": "<commit>", "related": true,
     "prompt_a": "...", "prompt_b": "...",
     "tests": {"cwd": "<folder in the repository>", "args": ["python", "-m", "pytest", "-q"]},
     "check": {"path": "<file to add after the merge>", "code": "<a test>"},      (related pairs)
     "pythonpath": ["src", "<absolute folder>"]}                               (optional)

"pythonpath" lists folders put first on the import path of both sessions and of every test run,
relative to that run's own worktree (or absolute), so a repository with a src layout tests its
own code even where an installed copy of the same package exists.

Each run goes into <out>/<pair>/<condition>-<repeat>/ and is skipped when its result.json
exists, so a stopped series resumes where it stopped. Per run: a clone of the repository at
the base commit, worktrees wt-a and wt-b, both sessions started within seconds of each other
(each with a time limit), each worktree committed, the tests on each branch alone, B merged
onto A, and the tests and the combination check on the merged code. Kept: both sessions'
results and transcripts, the patches, the test output, the notes Orchestraty delivered (with
condition), and timings; the clone itself is removed. A session stopped by a usage limit stops
the series (or waits and retries with --wait-on-limit); the run is not recorded.
"""
import argparse
import glob
import json
import os
import random
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time

PHASE0 = os.path.join(os.path.expanduser("~"), "orch-phase0")
PROFILE = os.path.join(PHASE0, "profiles", "claude")
CLAUDE = os.path.join(PHASE0, "tools", "claude-2.1.292", "node_modules", "@anthropic-ai", "claude-code-win32-x64",
                      "claude.exe")
ORCH_SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository
MODEL = "claude-sonnet-5-5"
SESSION_SECONDS = 15 * 60
TEST_SECONDS = 600
GIT = ["git", "-c", "user.name=Run", "-c", "user.email=run@example.com", "-c", "commit.gpgsign=false",
       "-c", "core.autocrlf=false", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "core.hooksPath="]
LIMIT_WORDS = ("usage limit", "rate limit", "limit reached", "limit will reset", "resets at", "out of extra usage")


class Limited(Exception):
    """A session hit a usage limit."""


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH" and not k.startswith("GIT_")
           and not k.startswith("ORCH_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def git(cwd, *args, check=True):
    proc = subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True, env=clean_env(), timeout=600)
    if check and proc.returncode:
        raise RuntimeError("git %s: %s" % (args[0], proc.stderr.strip()[:300]))
    return proc


def remove(path):
    def unlock(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if os.path.exists(path):
        shutil.rmtree(path, onerror=unlock)


def orch(state, cwd, *args):
    env = clean_env(ORCH_HOME=state, PYTHONPATH=os.pathsep.join(
        [os.path.join(ORCH_SRC, "orchestraty"), os.path.join(ORCH_SRC, "engine")]))
    return subprocess.run([sys.executable, "-m", "orchestraty"] + list(args), cwd=cwd, capture_output=True,
                          text=True, env=env, timeout=300)


def stop_coordinator(state):
    alive = os.path.join(state, "coordinator.alive")
    if os.path.exists(alive):
        open(os.path.join(state, "coordinator.stop"), "w").close()
        deadline = time.time() + 30
        while os.path.exists(alive) and time.time() < deadline:
            time.sleep(0.5)


def plan(pairs, repeats, conditions, seed):
    """[(pair id, condition, repeat)] in a random order per repeat, so the conditions do not run
    in a fixed sequence."""
    rng = random.Random(seed)
    out = []
    for r in range(1, repeats + 1):
        batch = [(p, c, r) for p in sorted(pairs) for c in conditions]
        rng.shuffle(batch)
        out += batch
    return out


def import_path(top, pair):
    """{"PYTHONPATH": ...} for a run's worktree, or {} when the pair names no folders."""
    folders = [p if os.path.isabs(p) else os.path.join(top, *p.split("/")) for p in pair.get("pythonpath", [])]
    return {"PYTHONPATH": os.pathsep.join(folders)} if folders else {}


def session(prompt, cwd, results, name, extra_env=None):
    argv = [CLAUDE, "-p", prompt, "--model", MODEL, "--max-turns", "60", "--permission-mode", "acceptEdits",
            "--allowedTools", "Bash(python -m pytest:*)", "--output-format", "json"]
    start = time.time()
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=SESSION_SECONDS,
                              env=clean_env(CLAUDE_CONFIG_DIR=PROFILE, **(extra_env or {})),
                              encoding="utf-8", errors="replace")
        out, err, code, timed_out = proc.stdout, proc.stderr, proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        out, err, code, timed_out = (exc.stdout or ""), (exc.stderr or ""), None, True
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
    info = {"exit": code, "timed_out": timed_out, "seconds": round(time.time() - start, 1), "started": start}
    try:
        data = json.loads(out)
    except ValueError:
        data = {}
    for key in ("session_id", "is_error", "num_turns", "total_cost_usd", "usage", "modelUsage", "subtype"):
        if key in data:
            info[key] = data[key]
    info["result"] = str(data.get("result", ""))[:4000]
    info["stderr"] = err[-2000:]
    text = (info["result"] + " " + info["stderr"]).lower()
    info["limited"] = bool(data.get("is_error") or code) and any(w in text for w in LIMIT_WORDS)
    results[name] = info


def run_tests(cwd, tests, extra=(), extra_env=None):
    args = list(tests["args"]) + list(extra)
    try:
        proc = subprocess.run(args, cwd=os.path.join(cwd, tests.get("cwd", ".")), capture_output=True, text=True,
                              timeout=TEST_SECONDS, env=clean_env(**(extra_env or {})), encoding="utf-8",
                              errors="replace")
        return {"passed": proc.returncode == 0, "exit": proc.returncode, "tail": (proc.stdout + proc.stderr)[-3000:]}
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit": None, "tail": "timed out"}


def transcripts(results, dest):
    os.makedirs(dest, exist_ok=True)
    for name, info in results.items():
        sid = info.get("session_id")
        if not sid:
            continue
        for path in glob.glob(os.path.join(PROFILE, "projects", "*", sid + ".jsonl")):
            shutil.copy2(path, os.path.join(dest, name + ".jsonl"))


def delivered_notes(state):
    path = os.path.join(state, "record.sqlite")
    if not os.path.exists(path):
        return []
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in db.execute("SELECT * FROM notes ORDER BY id")]
        findings = {r["key"]: dict(r) for r in db.execute("SELECT key, rule, strength, changer, receiver, file FROM findings")}
    finally:
        db.close()
    for r in rows:
        f = findings.get(r.get("finding_key"), {})
        r["rule"], r["strength"] = f.get("rule"), f.get("strength")
    return rows


def run_one(pair, condition, repeat, out_root):
    folder = os.path.join(out_root, pair["id"], "%s-%d" % (condition, repeat))
    done = os.path.join(folder, "result.json")
    if os.path.exists(done):
        with open(done, encoding="utf-8") as fh:
            return json.load(fh)
    work, state = os.path.join(folder, "work"), os.path.join(folder, "state")
    remove(work)
    remove(state)
    os.makedirs(work)
    repo, wa, wb = (os.path.join(work, n) for n in ("repo", "wt-a", "wt-b"))
    git(work, "clone", "-q", "--no-hardlinks", pair["repo"], repo)
    git(repo, "checkout", "-q", "--detach", pair["base"])
    git(repo, "worktree", "add", "-q", "-b", "a", wa, pair["base"])
    git(repo, "worktree", "add", "-q", "-b", "b", wb, pair["base"])
    result = {"pair": pair["id"], "condition": condition, "repeat": repeat, "related": pair["related"],
              "model": MODEL, "started": time.time()}
    if condition == "with":
        init = orch(state, repo, "init", "--yes", "--no-mod", "--claude-config", PROFILE)
        result["orch_init"] = init.returncode
        if init.returncode:
            raise RuntimeError("orch init failed: " + init.stderr[-500:])
    sessions = {}
    try:
        threads = [threading.Thread(target=session, args=(pair["prompt_a"], wa, sessions, "A", import_path(wa, pair))),
                   threading.Thread(target=session, args=(pair["prompt_b"], wb, sessions, "B", import_path(wb, pair)))]
        threads[0].start()
        time.sleep(2)
        threads[1].start()
        for t in threads:
            t.join()
    finally:
        if condition == "with":
            result["notes"] = delivered_notes(state)
            orch(state, repo, "restore", "--yes", "--claude-config", PROFILE)
            orch(state, repo, "unwatch")
            stop_coordinator(state)
    result["sessions"] = sessions
    if any(s.get("limited") for s in sessions.values()):
        raise Limited(pair["id"])
    for name, wt in (("A", wa), ("B", wb)):
        git(wt, "add", "-A")
        result["changed_" + name] = git(wt, "commit", "-q", "-m", name, check=False).returncode == 0
    result["alone_a"] = run_tests(wa, pair["tests"], extra_env=import_path(wa, pair))
    result["alone_b"] = run_tests(wb, pair["tests"], extra_env=import_path(wb, pair))
    git(repo, "checkout", "-q", "-B", "merged", "a")
    merge = git(repo, "merge", "--no-edit", "b", check=False)
    result["conflict"] = merge.returncode != 0
    if result["conflict"]:
        git(repo, "merge", "--abort", check=False)
    else:
        extra = []
        if pair.get("check"):
            path = os.path.join(repo, *pair["check"]["path"].split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(pair["check"]["code"])
            extra = [os.path.relpath(path, os.path.join(repo, pair["tests"].get("cwd", ".")))]
            result["check"] = run_tests(repo, {"cwd": pair["tests"].get("cwd", "."),
                                               "args": list(pair["tests"]["args"][:3]) + ["-q"] + extra},
                                        extra_env=import_path(repo, pair))
        result["merged"] = run_tests(repo, pair["tests"], extra_env=import_path(repo, pair))
    for name, branch in (("a", "a"), ("b", "b")):
        with open(os.path.join(folder, name + ".patch"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(git(repo, "diff", pair["base"], branch, check=False).stdout)
    transcripts(sessions, os.path.join(folder, "transcripts"))
    result["finished"] = time.time()
    remove(work)
    with open(done, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    return result


def load_pairs(folder):
    pairs = {}
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.json"), recursive=True)):
        with open(path, encoding="utf-8") as fh:
            p = json.load(fh)
        pairs[p["id"]] = p
    return pairs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the awareness task pairs.")
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--conditions", default="without,with")
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many new runs")
    ap.add_argument("--seed", type=int, default=20261020)
    ap.add_argument("--wait-on-limit", type=int, default=0, help="seconds to wait and retry after a usage limit")
    args = ap.parse_args(argv)
    pairs = load_pairs(args.pairs)
    if args.only:
        pairs = {k: v for k, v in pairs.items() if k in args.only.split(",")}
    order = plan(pairs, args.repeats, args.conditions.split(","), args.seed)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump({"seed": args.seed, "model": MODEL, "order": order}, fh, indent=1)
    new = 0
    for pid, condition, repeat in order:
        if args.limit and new >= args.limit:
            break
        folder = os.path.join(args.out, pid, "%s-%d" % (condition, repeat))
        fresh = not os.path.exists(os.path.join(folder, "result.json"))
        while True:
            try:
                r = run_one(pairs[pid], condition, repeat, args.out)
                break
            except Limited:
                print("usage limit at %s %s-%d" % (pid, condition, repeat), flush=True)
                if not args.wait_on_limit:
                    return 3
                time.sleep(args.wait_on_limit)
        if fresh:
            new += 1
            print("%s %s-%d: alone %s/%s, conflict %s, merged %s, check %s, cost %.2f" % (
                pid, condition, repeat, r["alone_a"]["passed"], r["alone_b"]["passed"], r["conflict"],
                r.get("merged", {}).get("passed"), r.get("check", {}).get("passed"),
                sum(s.get("total_cost_usd") or 0 for s in r["sessions"].values())), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
