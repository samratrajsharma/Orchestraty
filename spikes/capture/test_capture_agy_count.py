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
    got = [("SessionStart", 1), ("PreInvocation", 2), ("PostInvocation", 2), ("PreToolUse", 2), ("PostToolUse", 1),
           ("Stop", 1)]                                   # one PostToolUse lost
    write_lines(os.path.join(spool, "agy", "c1.jsonl"),
                [{"session_id": "c1", "event": e} for e, n in got for _ in range(n)] + [{"shim_error": "OSError"}])
    now = time.time()
    r = capture_agy_count.count(spool, now - 60, now + 60, brain)
    assert r["conversations"] == 2 and r["captured_conversations"] == 1 and r["coverage"] == 0.5
    assert r["lost_by_event"]["PostToolUse"] == 1 and r["rows"]["c1"]["lost"]["PreToolUse"] == 0
    assert r["rows"]["c2"]["lost"] == {"SessionStart": 1, "PreInvocation": 1, "PostInvocation": 1, "PreToolUse": 0,
                                       "PostToolUse": 0, "Stop": 1}
    assert r["events_lost"] == 1 + 4 and r["hook_errors"] == 1
    assert r["conversations_that_edited"] == ["c1"]
    assert "SECRET" not in json.dumps(r)
    assert capture_agy_count.count(spool, now + 3600, now + 7200, brain)["conversations"] == 0   # outside the window
