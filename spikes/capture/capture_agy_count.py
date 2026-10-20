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
(shim_error lines). Only step types, statuses, counts and tool names are read from the
transcripts.

A tool call whose run never finished (its step still RUNNING in the transcript) has no
PostToolUse to capture; such a missing PostToolUse is counted as unfinished, not lost. A
conversation whose transcript Antigravity has since removed is counted apart: its events can
only be checked against each other (every PreToolUse has its PostToolUse, and so on).
"""
import argparse
import json
import os

EVENTS = ("SessionStart", "PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop")
EDIT_TOOLS = ("write_to_file", "replace_file_content", "multi_replace_file_content", "write_file", "edit_file")


def default_brain():
    return os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "brain")


def transcript(brain, cid):
    return os.path.join(brain, cid, ".system_generated", "logs", "transcript_full.jsonl")


def expected(path):
    """({event: count}, edited files?, {step index: status}) from one transcript."""
    out = dict.fromkeys(EVENTS, 0)
    out["SessionStart"] = 1
    edited, status = False, {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                step = json.loads(line)
            except ValueError:
                continue
            kind = step.get("type")
            if isinstance(step.get("step_index"), int):
                status[step["step_index"]] = step.get("status")
            if kind == "PLANNER_RESPONSE":
                out["PreInvocation"] += 1
                out["PostInvocation"] += 1
                for call in step.get("tool_calls") or []:
                    out["PreToolUse"] += 1
                    out["PostToolUse"] += 1
                    edited |= isinstance(call, dict) and call.get("name") in EDIT_TOOLS
            elif kind == "USER_INPUT":
                out["Stop"] += 1
    return out, edited, status


def captured(spool):
    """({conversation id: {event: count}}, hook errors) from the agy spool."""
    detail = captured_detail(spool)
    return {cid: d["counts"] for cid, d in detail["conversations"].items()}, detail["errors"]


def captured_detail(spool):
    """{"conversations": {id: {"counts", "pre_steps", "post_steps", "transcript"}}, "errors"}."""
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
                if not sid:
                    continue
                c = out.setdefault(sid, {"counts": dict.fromkeys(EVENTS, 0), "pre_steps": [], "post_steps": [],
                                         "transcript": None, "first": None})
                if d.get("event") in c["counts"]:
                    c["counts"][d["event"]] += 1
                if d.get("event") == "PreToolUse" and isinstance(d.get("step"), int):
                    c["pre_steps"].append(d["step"])
                if d.get("event") == "PostToolUse" and isinstance(d.get("step"), int):
                    c["post_steps"].append(d["step"])
                if c["transcript"] is None and isinstance(d.get("transcript_path"), str):
                    c["transcript"] = d["transcript_path"]
                if isinstance(d.get("ts"), int):
                    c["first"] = d["ts"] / 1e9 if c["first"] is None else min(c["first"], d["ts"] / 1e9)
    return {"conversations": out, "errors": errors}


def _consistent(counts):
    return (counts["SessionStart"] == 1 and counts["Stop"] >= 1 and counts["PreToolUse"] == counts["PostToolUse"]
            and counts["PreInvocation"] == counts["PostInvocation"])


def count(spool, since, until, brain=None):
    brain = brain or default_brain()
    detail = captured_detail(spool)
    got = {cid: c for cid, c in detail["conversations"].items() if c["first"] is None or since <= c["first"] <= until}
    recorded = set()
    for cid in sorted(os.listdir(brain)) if os.path.isdir(brain) else []:
        path = transcript(brain, cid)
        if os.path.exists(path) and since <= os.path.getmtime(path) <= until:
            recorded.add(cid)
    rows, unchecked = {}, {}
    for cid in sorted(recorded | set(got)):
        c = got.get(cid)
        path = transcript(brain, cid)
        if not os.path.exists(path) and c and c["transcript"] and os.path.exists(c["transcript"]):
            path = c["transcript"]
        have = c["counts"] if c else dict.fromkeys(EVENTS, 0)
        if not os.path.exists(path):
            unchecked[cid] = {"captured": have, "consistent": _consistent(have)}
            continue
        want, edited, status = expected(path)
        lost = {e: max(0, want[e] - have[e]) for e in EVENTS}
        missing_post = list(c["pre_steps"]) if c else []
        for s in (c["post_steps"] if c else []):
            if s in missing_post:
                missing_post.remove(s)
        unfinished = sum(1 for s in missing_post if status.get(s) == "RUNNING")
        lost["PostToolUse"] = max(0, lost["PostToolUse"] - unfinished)
        rows[cid] = {"expected": want, "captured": have, "edited": edited, "lost": lost,
                     "unfinished_tool_calls": unfinished}
    lost = {e: sum(r["lost"][e] for r in rows.values()) for e in EVENTS}
    everyone = set(rows) | set(unchecked)
    captured_n = sum(1 for cid in everyone if cid in got)
    return {"conversations": len(everyone), "captured_conversations": captured_n,
            "coverage": captured_n / len(everyone) if everyone else None,
            "checked_against_transcript": len(rows),
            "events_expected": sum(sum(r["expected"].values()) for r in rows.values()),
            "events_lost": sum(lost.values()), "lost_by_event": lost,
            "unfinished_tool_calls": sum(r["unfinished_tool_calls"] for r in rows.values()),
            "transcript_removed": len(unchecked),
            "transcript_removed_consistent": sum(1 for u in unchecked.values() if u["consistent"]),
            "events_in_removed": sum(sum(u["captured"].values()) for u in unchecked.values()),
            "conversations_that_edited": sorted(cid for cid, r in rows.items() if r["edited"]),
            "hook_errors": detail["errors"], "rows": rows, "unchecked": unchecked}


def main(argv=None):
    p = argparse.ArgumentParser(description="Count the Antigravity capture leg.")
    p.add_argument("--spool", required=True)
    p.add_argument("--since", type=float, required=True)
    p.add_argument("--until", type=float, required=True)
    p.add_argument("--brain", default=None)
    p.add_argument("--json", help="also write the result to this file")
    args = p.parse_args(argv)
    result = count(args.spool, args.since, args.until, args.brain)
    summary = {k: v for k, v in result.items() if k not in ("rows", "unchecked")}
    print(json.dumps(summary, indent=2))
    if args.json:
        with open(args.json, "x", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
