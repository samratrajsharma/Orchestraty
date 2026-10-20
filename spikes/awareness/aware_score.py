"""Scores saved awareness runs: breaks, breaks avoided, false notes, time to awareness, cost.

Status: throwaway (Phase 0). Not part of the product.

    python aware_score.py --out <results folder>       # writes score.json and reactions.md there

* A run is **discarded** when either branch fails its tests alone (the task failed for reasons
  that have nothing to do with awareness).
* A **break**: both branches pass alone, but the merged code fails the tests or the
  combination check. A text conflict is counted apart, not as a break.
* **Breaks avoided** = (breaks without - breaks with) / breaks without, over the related
  pairs, with a bootstrap 95% interval over pairs.
* **False notes**: strong or medium notes delivered in unrelated pairs, per 10 pair runs.
* **Time to awareness**: from the change's event to the note being written, median.
* **Cost**: the sessions' reported cost and input tokens, per condition.
* ``reactions.md`` lists every delivered note with the agent's next words, for labelling by
  hand (used it correctly / ignored it / misread it).
"""
import argparse
import glob
import json
import os
import random
import statistics

STRONG_OR_MEDIUM = ("strong", "medium")


def load(out):
    runs = []
    for path in sorted(glob.glob(os.path.join(out, "*", "*", "result.json"))):
        with open(path, encoding="utf-8") as fh:
            r = json.load(fh)
        r["_folder"] = os.path.dirname(path)
        runs.append(r)
    return runs


def outcome(r):
    """'discarded', 'conflict', 'break' or 'ok' for one run."""
    if not (r["alone_a"]["passed"] and r["alone_b"]["passed"]):
        return "discarded"
    if r["conflict"]:
        return "conflict"
    if not r.get("merged", {}).get("passed") or (r.get("check") is not None and not r["check"]["passed"]):
        return "break"
    return "ok"


def avoided(table, pairs):
    """(breaks without - breaks with) / breaks without over `pairs`, or None with no break."""
    without = sum(table[p]["without"] for p in pairs)
    with_ = sum(table[p]["with"] for p in pairs)
    return (without - with_) / without if without else None


def bootstrap(table, pairs, rounds=2000, seed=7):
    """The 2.5th and 97.5th percentiles of breaks avoided over pairs resampled with replacement."""
    rng = random.Random(seed)
    values = []
    for _ in range(rounds):
        sample = [rng.choice(pairs) for _ in pairs]
        v = avoided(table, sample)
        if v is not None:
            values.append(v)
    if not values:
        return None
    values.sort()
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


def score(runs):
    related = sorted({r["pair"] for r in runs if r["related"]})
    table = {p: {"with": 0, "without": 0, "runs_with": 0, "runs_without": 0} for p in related}
    outcomes = {}
    for r in runs:
        o = outcome(r)
        outcomes.setdefault(o, 0)
        outcomes[o] += 1
        if r["related"] and o in ("break", "ok"):
            table[r["pair"]]["runs_" + r["condition"]] += 1
            table[r["pair"]][r["condition"]] += o == "break"
    unrelated_with = [r for r in runs if not r["related"] and r["condition"] == "with" and outcome(r) != "discarded"]
    false_notes = sum(1 for r in unrelated_with for n in r.get("notes", [])
                      if n.get("state") == "delivered" and n.get("strength") in STRONG_OR_MEDIUM)
    waits = [(n["written"] - n["cause_ts"]) / 1e6 for r in runs for n in r.get("notes", [])
             if n.get("written") and n.get("cause_ts")]
    cost = {}
    for cond in ("with", "without"):
        sessions = [s for r in runs if r["condition"] == cond for s in r["sessions"].values()]
        cost[cond] = {"sessions": len(sessions),
                      "usd": round(sum(s.get("total_cost_usd") or 0 for s in sessions), 2),
                      "input_tokens_mean": round(statistics.mean(
                          [_input_tokens(s) for s in sessions]) if sessions else 0)}
    breaks_without = sum(t["without"] for t in table.values())
    return {
        "runs": len(runs), "outcomes": outcomes, "related_pairs": len(related),
        "breaks": {p: t for p, t in table.items()},
        "breaks_without": breaks_without, "breaks_with": sum(t["with"] for t in table.values()),
        "breaks_avoided": avoided(table, related) if related else None,
        "breaks_avoided_95": bootstrap(table, related) if related else None,
        "too_few_breaks": breaks_without < 6,
        "unrelated_pair_runs_with": len(unrelated_with),
        "false_notes": false_notes,
        "false_notes_per_10": round(10.0 * false_notes / len(unrelated_with), 2) if unrelated_with else None,
        "time_to_awareness_ms_median": round(statistics.median(waits)) if waits else None,
        "notes_delivered": sum(1 for r in runs for n in r.get("notes", []) if n.get("state") == "delivered"),
        "cost": cost,
    }


def _input_tokens(s):
    u = s.get("usage") or {}
    return sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def reactions(runs):
    """Markdown: each delivered note and the agent's next words, from its transcript."""
    lines = ["# Notes and the agents' next words (label each: used / ignored / misread)", ""]
    for r in runs:
        notes = [n for n in r.get("notes", []) if n.get("state") == "delivered"]
        if not notes:
            continue
        lines.append("## %s %s-%s" % (r["pair"], r["condition"], r["repeat"]))
        for n in notes:
            who = "A" if n["session_id"] == r["sessions"].get("A", {}).get("session_id") else "B"
            lines.append("- to %s (%s, %s): %s" % (who, n.get("rule"), n.get("kind"), n.get("text", "")[:600]))
            lines.append("  - next words: %s" % _next_words(r, who, n.get("text", "")))
        lines.append("")
    return "\n".join(lines) + "\n"


def _next_words(r, who, note_text):
    """The first assistant text after the note appears in the session's transcript."""
    path = os.path.join(r["_folder"], "transcripts", who + ".jsonl")
    if not os.path.exists(path):
        return "(no transcript)"
    seen = False
    key = note_text[:60]
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not seen:
                seen = bool(key) and key in line
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            for block in (d.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").strip():
                    return block["text"].strip().replace("\n", " ")[:500]
    return "(none found)"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score the awareness runs.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    runs = load(args.out)
    result = score(runs)
    with open(os.path.join(args.out, "score.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    with open(os.path.join(args.out, "reactions.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(reactions(runs))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
