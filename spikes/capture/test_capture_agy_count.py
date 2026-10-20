"""The Antigravity counter, on a synthetic transcript and spool."""
import json
import os
import time

import capture_agy_count


def write_lines(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def test_lost_events_coverage_and_hook_errors(tmp_path):
    brain, spool = str(tmp_path / "brain"), str(tmp_path / "spool")
    steps = [{"type": "USER_INPUT", "content": "SECRET"},
             {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "view_file", "args": {"x": "SECRET"}},
                                                         {"name": "replace_file_content", "args": {}}]},
             {"type": "GENERIC"}, {"type": "GENERIC"},
             {"type": "PLANNER_RESPONSE", "tool_calls": []}]
    write_lines(capture_agy_count.transcript(brain, "c1"), steps)
    write_lines(capture_agy_count.transcript(brain, "c2"), [{"type": "USER_INPUT"}, {"type": "PLANNER_RESPONSE"}])
    now = time.time()
    got = [("SessionStart", 1, None), ("PreInvocation", 2, None), ("PostInvocation", 2, None), ("PreToolUse", 1, 2),
           ("PreToolUse", 1, 3), ("PostToolUse", 1, 2), ("Stop", 1, None)]       # step 3's PostToolUse lost
    write_lines(os.path.join(spool, "agy", "c1.jsonl"),
                [dict({"session_id": "c1", "event": e, "ts": int(now * 1e9)}, **({"step": s} if s else {}))
                 for e, n, s in got for _ in range(n)] + [{"shim_error": "OSError"}])
    # c3: captured, but Antigravity removed its transcript; its events are complete in pairs
    write_lines(os.path.join(spool, "agy", "c3.jsonl"),
                [{"session_id": "c3", "event": e, "ts": int(now * 1e9)}
                 for e in ("SessionStart", "PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop")])
    r = capture_agy_count.count(spool, now - 60, now + 60, brain)
    assert r["conversations"] == 3 and r["captured_conversations"] == 2 and abs(r["coverage"] - 2 / 3) < 1e-9
    assert r["checked_against_transcript"] == 2 and r["transcript_removed"] == 1 and r["transcript_removed_consistent"] == 1
    assert r["lost_by_event"]["PostToolUse"] == 1 and r["rows"]["c1"]["lost"]["PreToolUse"] == 0
    assert r["rows"]["c2"]["lost"] == {"SessionStart": 1, "PreInvocation": 1, "PostInvocation": 1, "PreToolUse": 0,
                                       "PostToolUse": 0, "Stop": 1}
    assert r["events_lost"] == 1 + 4 and r["hook_errors"] == 1
    assert r["conversations_that_edited"] == ["c1"]
    assert "SECRET" not in json.dumps(r)
    assert capture_agy_count.count(spool, now + 3600, now + 7200, brain)["conversations"] == 0   # outside the window


def test_a_tool_call_that_never_finished_is_not_a_lost_event(tmp_path):
    brain, spool = str(tmp_path / "brain"), str(tmp_path / "spool")
    write_lines(capture_agy_count.transcript(brain, "c1"), [
        {"type": "USER_INPUT", "step_index": 0},
        {"type": "PLANNER_RESPONSE", "step_index": 1, "tool_calls": [{"name": "run_command"}]},
        {"type": "GENERIC", "step_index": 2, "status": "RUNNING"}])
    now = time.time()
    write_lines(os.path.join(spool, "agy", "c1.jsonl"),
                [{"session_id": "c1", "event": e, "ts": int(now * 1e9), "step": 2 if "Tool" in e else None}
                 for e in ("SessionStart", "PreInvocation", "PostInvocation", "PreToolUse", "Stop")])
    r = capture_agy_count.count(spool, now - 60, now + 60, brain)
    assert r["events_lost"] == 0 and r["unfinished_tool_calls"] == 1
