"""The run order and the scoring, on synthetic results."""
import json
import os

import aware_run
import aware_score


def test_the_order_is_random_per_repeat_but_fixed_by_the_seed():
    pairs = {"p1": {}, "p2": {}, "p3": {}}
    one = aware_run.plan(pairs, 2, ["without", "with"], seed=1)
    assert one == aware_run.plan(pairs, 2, ["without", "with"], seed=1)
    assert len(one) == 12 and sorted(set(one)) == sorted(one)
    assert {r for _, _, r in one[:6]} == {1} and {r for _, _, r in one[6:]} == {2}


def result(pair, condition, repeat, related=True, alone=(True, True), conflict=False, merged=True, check=True,
           notes=()):
    r = {"pair": pair, "condition": condition, "repeat": repeat, "related": related,
         "alone_a": {"passed": alone[0]}, "alone_b": {"passed": alone[1]}, "conflict": conflict,
         "merged": {"passed": merged}, "notes": list(notes),
         "sessions": {"A": {"total_cost_usd": 0.5, "usage": {"input_tokens": 10}, "session_id": "sa"},
                      "B": {"total_cost_usd": 0.5, "usage": {"input_tokens": 30}, "session_id": "sb"}}}
    if related:
        r["check"] = {"passed": check}
    return r


def write_runs(tmp_path, runs):
    for r in runs:
        folder = tmp_path / r["pair"] / ("%s-%d" % (r["condition"], r["repeat"]))
        folder.mkdir(parents=True)
        (folder / "result.json").write_text(json.dumps(r), encoding="utf-8")


def test_breaks_avoided_false_notes_and_what_is_left_out(tmp_path):
    note = {"state": "delivered", "strength": "strong", "written": 3_000_000_000, "cause_ts": 2_500_000_000,
            "session_id": "sb", "rule": "O3", "kind": "edit", "text": "Orchestraty: x"}
    runs = [
        result("r1", "without", 1, check=False), result("r1", "with", 1, notes=[note]),       # a break avoided
        result("r2", "without", 1, check=False), result("r2", "with", 1, check=False),        # not avoided
        result("r3", "without", 1, alone=(False, True)),                                      # discarded
        result("r3", "with", 1, conflict=True),                                               # a conflict
        result("u1", "with", 1, related=False, notes=[note]),                                 # a false note
        result("u1", "without", 1, related=False),
    ]
    write_runs(tmp_path, runs)
    s = aware_score.score(aware_score.load(str(tmp_path)))
    assert s["outcomes"] == {"break": 3, "ok": 3, "discarded": 1, "conflict": 1}
    assert (s["breaks_without"], s["breaks_with"]) == (2, 1) and s["breaks_avoided"] == 0.5
    assert s["too_few_breaks"] is True
    assert s["false_notes"] == 1 and s["false_notes_per_10"] == 10.0
    assert s["time_to_awareness_ms_median"] == 500
    assert s["cost"]["with"]["usd"] == 4.0 and s["cost"]["with"]["input_tokens_mean"] == 20
    low, high = s["breaks_avoided_95"]
    assert 0.0 <= low <= 0.5 <= high <= 1.0
    text = aware_score.reactions(aware_score.load(str(tmp_path)))
    assert "## r1 with-1" in text and "to B (O3, edit)" in text
    assert s["peer_messages"] == {"with": 0, "without": 0}
    folder = tmp_path / "r2" / "without-1" / "transcripts"
    folder.mkdir()
    (folder / "A.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "SendMessage", "input": {"to": "x"}}]}}) + "\n", encoding="utf-8")
    assert aware_score.score(aware_score.load(str(tmp_path)))["peer_messages"] == {"with": 0, "without": 1}
