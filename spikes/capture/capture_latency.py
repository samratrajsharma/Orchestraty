"""Latency harness: the time a hook command adds over a no-op of the same form.

Status: throwaway (Phase 0). Not part of the product.

Replays hook payloads against each command, with several replayers in parallel (5 or more
for the primary number), candidates in a shuffled order every round, warm-up rounds first,
then N measured rounds per replayer. Each sample is the wall time from just before the
process starts to after it has exited with its pipes drained. The first run of each command
in each replayer is reported apart, as a cold run.

    python capture_latency.py --cmd shim='["/abs/python", "-S", "-I", "capture_shim.py", "<spool>", "bench"]' \\
                              --cmd noop='["/usr/bin/true"]' --baseline noop \\
                              [--payloads recorded.jsonl] [--n 1000] [--warmup 20] [--parallel 5] [--seed 1]

It prints numbers only: no pass marks (spike rule 4). Without --payloads it replays a
synthetic 2 KB PreToolUse-shaped payload.
"""
import argparse
import json
import random
import subprocess
import sys
import threading
import time


def percentile(values, q):
    """The q-th percentile (0-100) with linear interpolation between the closest ranks, as
    numpy's default and Excel's PERCENTILE.INC compute it. None for no values."""
    s = sorted(values)
    if not s:
        return None
    pos = (len(s) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def stats(values):
    return {"n": len(values), "median": percentile(values, 50), "p90": percentile(values, 90),
            "p99": percentile(values, 99), "max": max(values) if values else None}


def synthetic_payload(session="bench-session", seq=0, size=2048):
    """A PreToolUse-shaped payload of about `size` bytes. Synthetic: no real session or code."""
    event = {"session_id": session, "transcript_path": "/tmp/bench/%s.jsonl" % session, "cwd": "/tmp/bench",
             "permission_mode": "default", "hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_use_id": "toolu_%06d" % seq,
             "tool_input": {"file_path": "/tmp/bench/src/module.py", "old_string": "", "new_string": ""}}
    pad = max(0, size - len(json.dumps(event)))
    unit = len(json.dumps("x = 1\n")) - 2                 # its size inside the JSON text
    event["tool_input"]["old_string"] = "x = 1\n" * (pad // (2 * unit))
    event["tool_input"]["new_string"] = "x = 2\n" * (pad // (2 * unit))
    return json.dumps(event).encode("utf-8")


def load_payloads(path):
    with open(path, "rb") as fh:
        return [line.rstrip(b"\r\n") for line in fh if line.strip()]


def run_once(argv, payload):
    """(seconds, exit code, printed anything) for one run of argv with payload on stdin."""
    start = time.perf_counter()
    proc = subprocess.run(argv, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    elapsed = time.perf_counter() - start
    return elapsed, proc.returncode, bool(proc.stdout or proc.stderr)


def measure(commands, payloads, n=1000, warmup=20, parallel=5, seed=1, runner=run_once):
    """Samples for every command: {"warm": [...], "cold": [...], "bad_exit": k, "printed": k}.

    `commands` maps a name to an argv list. Each of `parallel` replayers runs warmup + n
    rounds; every round runs every command once, in an order shuffled per round."""
    results = {name: {"warm": [], "cold": [], "bad_exit": 0, "printed": 0} for name in commands}
    lock = threading.Lock()
    names = sorted(commands)

    def replayer(index):
        rng = random.Random(seed * 1000 + index)
        seen = set()
        for rnd in range(warmup + n):
            order = names[:]
            rng.shuffle(order)
            for name in order:
                payload = payloads[(rnd * len(names) + index) % len(payloads)]
                elapsed, code, printed = runner(commands[name], payload)
                with lock:
                    r = results[name]
                    r["bad_exit"] += code != 0
                    r["printed"] += printed
                    if name not in seen:
                        r["cold"].append(elapsed)
                        seen.add(name)
                    elif rnd >= warmup:
                        r["warm"].append(elapsed)

    threads = [threading.Thread(target=replayer, args=(i,)) for i in range(parallel)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def summarize(results, baseline):
    """Rows of numbers, in milliseconds: every command's warm and cold statistics, and for
    each command the time it adds over the baseline at the median, p90, p99 and max."""
    rows = {}
    base = stats(results[baseline]["warm"])
    for name, r in sorted(results.items()):
        warm, cold = stats(r["warm"]), stats(r["cold"])
        row = {"warm": {k: (v * 1000 if isinstance(v, float) else v) for k, v in warm.items()},
               "cold": {k: (v * 1000 if isinstance(v, float) else v) for k, v in cold.items()},
               "bad_exit": r["bad_exit"], "printed": r["printed"]}
        if base["n"] and warm["n"]:
            row["added_over_baseline"] = {k: (warm[k] - base[k]) * 1000 for k in ("median", "p90", "p99", "max")}
        rows[name] = row
    return rows


def format_report(rows, baseline):
    lines = ["command | n | median ms | p90 ms | p99 ms | max ms | cold n | cold median ms | cold max ms | "
             "non-zero exits | printed output"]
    for name, row in rows.items():
        w, c = row["warm"], row["cold"]
        lines.append(" | ".join([name, str(w["n"])] + [_ms(w[k]) for k in ("median", "p90", "p99", "max")]
                                + [str(c["n"]), _ms(c["median"]), _ms(c["max"]), str(row["bad_exit"]),
                                   str(row["printed"])]))
    lines.append("")
    lines.append(f"added over {baseline} | median ms | p90 ms | p99 ms | max ms")
    for name, row in rows.items():
        if name != baseline and "added_over_baseline" in row:
            a = row["added_over_baseline"]
            lines.append(" | ".join([name] + [_ms(a[k], signed=True) for k in ("median", "p90", "p99", "max")]))
    return "\n".join(lines)


def _ms(value, signed=False):
    if value is None:
        return "-"
    return ("%+.2f" if signed else "%.2f") % value


def main(argv=None):
    p = argparse.ArgumentParser(description="Hook latency harness (throwaway, prints numbers only).")
    p.add_argument("--cmd", action="append", required=True, metavar="NAME=JSON_ARGV",
                   help='a command to time, e.g. noop=\'["/usr/bin/true"]\'')
    p.add_argument("--baseline", required=True, help="the name of the same-form no-op")
    p.add_argument("--payloads", help="recorded payloads, one JSON object per line")
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--warmup", type=int, default=20)
    p.add_argument("--parallel", type=int, default=5)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args(argv)
    commands = {}
    for spec in args.cmd:
        name, _, argv_json = spec.partition("=")
        commands[name] = json.loads(argv_json)
    if args.baseline not in commands:
        p.error("--baseline must name one of the --cmd commands")
    payloads = load_payloads(args.payloads) if args.payloads else [synthetic_payload(seq=i) for i in range(50)]
    results = measure(commands, payloads, args.n, args.warmup, args.parallel, args.seed)
    print(f"replayers {args.parallel} | rounds {args.warmup} warm-up + {args.n} measured | payloads {len(payloads)}")
    print(format_report(summarize(results, args.baseline), args.baseline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
