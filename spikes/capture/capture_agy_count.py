"""Counts the Antigravity capture leg: every conversation's events in the spool against what
Antigravity's own transcript says happened.

Status: throwaway (Phase 0). Not part of the product.

    python capture_agy_count.py --spool <root>/spool --since <epoch> --until <epoch> [--brain <folder>]

Antigravity keeps each conversation under <brain>/<conversation id>/.system_generated/logs/
transcript_full.jsonl, one step per line. Expected events per conversation:
  * SessionStart: 1;
  * PreInvocation and PostInvocation: one per PLANNER_RESPONSE step (each call to the model);
  * PreToolUse and PostToolUse: one per entry of those steps' tool_calls;
  * Stop: one per USER_INPUT step (each turn ends once).
Conversations counted are those whose transcript was written between --since and --until.
It prints numbers and conversation ids only: lost events per type (expected minus captured,
never below zero), coverage (conversations with any captured event / all), and hook errors
(shim_error lines). Only step types, counts and tool names are read from the transcripts.
"""
import argparse
import json
import os

EVENTS = ("SessionStart", "PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop")
EDIT_TOOLS = ("write_to_file", "replace_file_content", "multi_replace_file_content")


def default_brain():
    return os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "brain")


def transcript(brain, cid):
    return os.path.join(brain, cid, ".system_generated", "logs", "transcript_full.jsonl")


def expected(path):
    """({event: count}, edited files?) from one transcript."""
    out = dict.fromkeys(EVENTS, 0)
    out["SessionStart"] = 1
    edited = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                step = json.loads(line)
            except ValueError:
                continue
            kind = step.get("type")
            if kind == "PLANNER_RESPONSE":
                out["PreInvocation"] += 1
                out["PostInvocation"] += 1
                for call in step.get("tool_calls") or []:
                    out["PreToolUse"] += 1
                    out["PostToolUse"] += 1
                    edited |= isinstance(call, dict) and call.get("name") in EDIT_TOOLS
            elif kind == "USER_INPUT":
                out["Stop"] += 1
    return out, edited


def captured(spool):
    """({conversation id: {event: count}}, hook errors) from the agy spool."""
    out, errors = {}, 0
    folder = os.path.join(spool, "agy")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    errors += 1
                    continue
                if "shim_error" in d:
                    errors += 1
                    continue
                sid = d.get("session_id")
                if sid:
                    counts = out.setdefault(sid, dict.fromkeys(EVENTS, 0))
                    if d.get("event") in counts:
                        counts[d["event"]] += 1
    return out, errors


def count(spool, since, until, brain=None):
    brain = brain or default_brain()
    got, errors = captured(spool)
    rows = {}
    for cid in sorted(os.listdir(brain)) if os.path.isdir(brain) else []:
        path = transcript(brain, cid)
        if not os.path.exists(path) or not since <= os.path.getmtime(path) <= until:
            continue
        want, edited = expected(path)
        have = got.get(cid, dict.fromkeys(EVENTS, 0))
        rows[cid] = {"expected": want, "captured": have, "edited": edited,
                     "lost": {e: max(0, want[e] - have[e]) for e in EVENTS}}
    lost = {e: sum(r["lost"][e] for r in rows.values()) for e in EVENTS}
    expected_total = sum(sum(r["expected"].values()) for r in rows.values())
    return {"conversations": len(rows), "captured_conversations": sum(1 for c in rows if c in got),
            "coverage": (sum(1 for c in rows if c in got) / len(rows)) if rows else None,
            "events_expected": expected_total, "events_lost": sum(lost.values()), "lost_by_event": lost,
            "conversations_that_edited": sorted(c for c, r in rows.items() if r["edited"]),
            "hook_errors": errors, "rows": rows}


def main(argv=None):
    p = argparse.ArgumentParser(description="Count the Antigravity capture leg.")
    p.add_argument("--spool", required=True)
    p.add_argument("--since", type=float, required=True)
    p.add_argument("--until", type=float, required=True)
    p.add_argument("--brain", default=None)
    p.add_argument("--json", help="also write the result to this file")
    args = p.parse_args(argv)
    result = count(args.spool, args.since, args.until, args.brain)
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2))
    if args.json:
        with open(args.json, "x", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
