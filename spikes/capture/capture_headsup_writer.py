"""Writes the ready-made files the heads-up hook returns, and times the hook's read.

Status: throwaway (Phase 0). Not part of the product.

    python capture_headsup_writer.py put    --path <file path> --text <context> [--root <phase-0 folder>]
    python capture_headsup_writer.py remove --path <file path> [--root <phase-0 folder>]
    python capture_headsup_writer.py bench  [--n 200] [--root <phase-0 folder>]

Files go to <root>/headsup/<key>.json only. Each is the complete stdout the hook returns:
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": <text>}}, with the
text cut to MAX_CONTEXT characters. A file is written to a temporary name, moved into place,
and then read back once: the first read of a freshly written file was slow (about 9 ms,
probably on-access scanning), and the read-back moves that cost off the hook's path.

bench prints numbers only: the read time of the first read after a rewrite, without and with
the read-back, and of later reads (steady state), each in a fresh process as the hook does it.
"""
import argparse
import json
import os
import subprocess
import sys
import time

import capture_hooks
from capture_headsup import key_for
from capture_latency import stats

MAX_CONTEXT = 9500          # characters; the hook output stays under 10,000


def payload_bytes(text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "additionalContext": text[:MAX_CONTEXT]}}).encode("utf-8")


def target(root, path):
    return capture_hooks.checked_target(root, os.path.join("headsup", key_for(path) + ".json"))


def put(root, path, text, read_back=True):
    """Write the ready-made file for `path` and return its location."""
    final = target(root, path)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    tmp = final + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(payload_bytes(text))
    os.replace(tmp, final)
    if read_back:
        with open(final, "rb") as fh:
            fh.read()
    return final


def remove(root, path):
    try:
        os.remove(target(root, path))
        return True
    except FileNotFoundError:
        return False


_READER = ("import sys, time\n"
           "t = time.perf_counter()\n"
           "f = open(sys.argv[1], 'rb'); f.read(); f.close()\n"
           "sys.stdout.write('%.6f' % (time.perf_counter() - t))\n")


def _timed_read(path):
    """Seconds one fresh process takes to open and read the file (process start excluded)."""
    out = subprocess.run([sys.executable, "-S", "-I", "-B", "-c", _READER, path], capture_output=True, timeout=60)
    return float(out.stdout)


def bench(root, n=200):
    """Read times (ms) for the three cases, n samples each."""
    path = os.path.join("bench", "src", "module.py")
    samples = {"first read, no read-back": [], "first read, after read-back": [], "steady state": []}
    for i in range(n):
        file = put(root, path, f"bench context {i} " * 50, read_back=False)
        samples["first read, no read-back"].append(_timed_read(file))
        put(root, path, f"bench context {i}b " * 50, read_back=True)
        samples["first read, after read-back"].append(_timed_read(file))
        samples["steady state"].append(_timed_read(file))
    remove(root, path)
    return {name: {k: (v * 1000 if isinstance(v, float) else v) for k, v in stats(values).items()}
            for name, values in samples.items()}


def main(argv=None):
    p = argparse.ArgumentParser(description="Write heads-up files for the pre-edit hook, or time its read.")
    p.add_argument("action", choices=["put", "remove", "bench"])
    p.add_argument("--root", default=capture_hooks.default_root())
    p.add_argument("--path", help="the file the agent will edit")
    p.add_argument("--text", default="", help="the context to hand the agent")
    p.add_argument("--n", type=int, default=200)
    args = p.parse_args(argv)
    try:
        if args.action == "put":
            print(put(args.root, args.path, args.text))
        elif args.action == "remove":
            print("removed" if remove(args.root, args.path) else "not there")
        else:
            rows = bench(args.root, args.n)
            print("case | n | median ms | p90 ms | p99 ms | max ms")
            for name, s in rows.items():
                print(" | ".join([name, str(s["n"])] + ["%.3f" % s[k] for k in ("median", "p90", "p99", "max")]))
    except capture_hooks.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
