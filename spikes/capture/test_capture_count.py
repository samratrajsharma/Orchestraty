"""Tests for the loss and coverage counter. Every transcript, rollout and spool is synthetic."""
import json
import os
import subprocess
import sys
from pathlib import Path

import capture_count as cc

SECRET = "SECRET-TASK-TEXT-5e1d"
T0 = 1790000000.0


def iso(seconds):
    import datetime as dt
    return dt.datetime.fromtimestamp(T0 + seconds, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def claude_transcript(projects, sid, turns, t=0):
    """turns: list of lists of (tool_id, tool_name, outcome) with outcome "ok", "error", "denied" or None."""
    rows = []
    for calls in turns:
        rows.append({"type": "user", "sessionId": sid, "timestamp": iso(t), "message": {"role": "user", "content": SECRET}})
        t += 1
        for tid, name, outcome in calls:
            rows.append({"type": "assistant", "timestamp": iso(t), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": name, "input": {"file_path": "/w/" + SECRET}}]}})
            t += 1
            if outcome is not None:
                text = "Permission to use Bash has been denied." if outcome == "denied" else SECRET
                rows.append({"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tid, "is_error": outcome in ("error", "denied"),
                     "content": text}]}})
                t += 1
        rows.append({"type": "assistant", "timestamp": iso(t), "message": {"role": "assistant", "stop_reason": "end_turn",
                                                                          "content": [{"type": "text", "text": SECRET}]}})
        t += 1
    write_jsonl(projects / "-w" / f"{sid}.jsonl", rows)


def spool_lines(spool, agent, sid, events):
    rows = []
    for i, (event, tool_id) in enumerate(events):
        row = {"ts": int((T0 + i) * 1e9), "pid": 1, "agent": agent, "event": event, "len": 10, "session_id": sid,
               "hook_event_name": event}
        if tool_id:
            row["tool_use_id"] = tool_id
        rows.append(row)
    write_jsonl(spool / agent / f"{sid}.jsonl", rows)


def full_events(turns):
    events = [("SessionStart", None)]
    for calls in turns:
        events.append(("UserPromptSubmit", None))
        for tid, name, outcome in calls:
            if name in cc.EDIT_TOOLS:
                events.append(("PreToolUse", tid))
            if outcome in ("ok", "error"):
                events.append(("PostToolUse" if outcome == "ok" else "PostToolUseFailure", tid))
            if name == "Task" and outcome == "ok":
                events.append(("SubagentStop", None))
        events.append(("Stop", None))
    events.append(("SessionEnd", None))
    return events


def row(result, event, kind="scripted"):
    return next(r for r in result["loss"] if r["event"] == event and r["kind"] == kind)


def test_a_complete_session_loses_nothing(tmp_path):
    turns = [[("toolu_1", "Edit", "ok"), ("toolu_2", "Bash", "error")], [("toolu_3", "Task", "ok")]]
    claude_transcript(tmp_path / "projects", "s-full", turns)
    spool_lines(tmp_path / "spool", "claude", "s-full", full_events(turns))
    manifest = {"sessions": [{"agent": "claude", "session_id": "s-full", "kind": "scripted", "env": "win", "async": False}]}
    result = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"))
    for event in cc.EVENTS:
        r = row(result, event)
        assert r["lost"] == 0 and r["extra"] == 0 and r["found"] == r["expected"], r
    assert row(result, "PostToolUse")["expected"] == 3 and row(result, "PreToolUse")["expected"] == 1
    assert row(result, "Stop")["expected"] == 2 and row(result, "SubagentStop")["expected"] == 1
    assert result["coverage"] == [{"env": "win", "kind": "scripted", "records": 1, "captured": 1}]


def test_known_gaps_are_counted_exactly(tmp_path):
    turns = [[("toolu_1", "Edit", "ok"), ("toolu_2", "Read", "ok"), ("toolu_3", "Bash", "ok")]]
    claude_transcript(tmp_path / "projects", "s-gaps", turns)
    events = [e for e in full_events(turns) if e not in (("PostToolUse", "toolu_2"), ("Stop", None))]
    events.append(("PostToolUse", "toolu_3"))                          # a duplicate is extra, not found twice
    spool_lines(tmp_path / "spool", "claude", "s-gaps", events)
    manifest = {"sessions": [{"agent": "claude", "session_id": "s-gaps", "kind": "scripted", "env": "win"}]}
    result = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"))
    assert (row(result, "PostToolUse")["lost"], row(result, "PostToolUse")["extra"]) == (1, 1)
    assert row(result, "Stop")["lost"] == 1
    assert row(result, "SessionStart")["lost"] == 0


def test_permission_denied_calls_come_from_the_record_and_are_not_lost(tmp_path):
    turns = [[("toolu_1", "Bash", "denied"), ("toolu_2", "Bash", "ok")]]
    claude_transcript(tmp_path / "projects", "s-denied", turns)
    spool_lines(tmp_path / "spool", "claude", "s-denied", full_events(turns))
    manifest = {"sessions": [{"agent": "claude", "session_id": "s-denied", "kind": "scripted", "env": "wsl"}]}
    result = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"))
    assert row(result, "PostToolUse")["expected"] == 1 and row(result, "PostToolUse")["lost"] == 0
    assert result["permission_denied"] == [{"env": "wsl", "kind": "scripted", "calls": 1}]


def test_a_missing_post_event_is_never_read_as_a_denial(tmp_path):
    turns = [[("toolu_1", "Bash", "ok")]]
    claude_transcript(tmp_path / "projects", "s-miss", turns)
    spool_lines(tmp_path / "spool", "claude", "s-miss", [e for e in full_events(turns) if e[0] != "PostToolUse"])
    manifest = {"sessions": [{"agent": "claude", "session_id": "s-miss", "kind": "scripted", "env": "win"}]}
    result = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"))
    assert row(result, "PostToolUse")["lost"] == 1
    assert result["permission_denied"] == [{"env": "win", "kind": "scripted", "calls": 0}]


def test_killed_sessions_get_their_own_rows_and_the_timeout_close(tmp_path):
    # Killed in the second turn, while a Bash call was still running: no result, no Post expected.
    turns = [[("toolu_1", "Edit", "ok")], [("toolu_2", "Bash", None)]]
    for sid in ("s-kill-old", "s-kill-new"):
        claude_transcript(tmp_path / "projects", sid, turns)
        events = [e for e in full_events(turns) if e[0] not in ("SessionEnd",)]
        events = events[:-1]                                             # the cut-short turn's Stop never fired
        spool_lines(tmp_path / "spool", "claude", sid, events)
    manifest = {"sessions": [{"agent": "claude", "session_id": sid, "kind": "killed", "env": "win"}
                             for sid in ("s-kill-old", "s-kill-new")]}
    old = T0 + 3600
    result = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"), timeout_minutes=30, now=old)
    for event in cc.EVENTS:
        assert row(result, event, "killed")["lost"] == 0, event
    [killed] = result["killed"]
    assert killed["sessions"] == 2 and killed["missing_session_end"] == 2 and killed["missing_stop"] == 2
    assert killed["closed_by_timeout"] == 2 and killed["still_open"] == 0
    result = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"), timeout_minutes=30, now=T0 + 60)
    assert result["killed"][0]["still_open"] == 2


def test_coverage_counts_every_record_and_leaves_noop_sessions_out(tmp_path):
    for sid in ("s-a", "s-b", "s-noop", "s-extra"):
        claude_transcript(tmp_path / "projects", sid, [[]])
    spool_lines(tmp_path / "spool", "claude", "s-a", full_events([[]]))
    manifest = {"sessions": [{"agent": "claude", "session_id": "s-a", "kind": "interactive", "env": "win"},
                             {"agent": "claude", "session_id": "s-b", "kind": "interactive", "env": "win"},
                             {"agent": "claude", "session_id": "s-noop", "kind": "noop", "env": "win"}]}
    result = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"))
    cov = {(r["env"], r["kind"]): (r["records"], r["captured"]) for r in result["coverage"]}
    assert cov == {("win", "interactive"): (2, 1), ("?", "unlisted"): (1, 0)}


def test_shim_errors_and_subagent_transcripts_are_counted(tmp_path):
    turns = [[("toolu_1", "Task", "ok")]]
    claude_transcript(tmp_path / "projects", "s-sub", turns)
    # The subagent's own tool calls live in <session>/subagents/*.jsonl and fire hooks too.
    write_jsonl(tmp_path / "projects" / "-w" / "s-sub" / "subagents" / "agent-1.jsonl", [
        {"type": "assistant", "isSidechain": True, "timestamp": iso(5), "message": {"content": [
            {"type": "tool_use", "id": "toolu_sub", "name": "Read", "input": {}}]}},
        {"type": "user", "isSidechain": True, "timestamp": iso(6), "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_sub", "content": "x"}]}}])
    spool_lines(tmp_path / "spool", "claude", "s-sub", full_events(turns) + [("PostToolUse", "toolu_sub")])
    write_jsonl(tmp_path / "spool" / "claude" / "shim_errors.jsonl", [{"ts": 1, "pid": 1, "agent": "claude",
                                                                      "shim_error": "OSError"}])
    manifest = {"sessions": [{"agent": "claude", "session_id": "s-sub", "kind": "scripted", "env": "win"}]}
    result = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"))
    assert row(result, "PostToolUse")["expected"] == 2 and row(result, "PostToolUse")["lost"] == 0
    assert row(result, "UserPromptSubmit")["expected"] == 1                 # sidechain prompts are not user prompts
    assert result["shim_errors"] == 1


def test_codex_rollouts_are_read_by_call_id(tmp_path):
    rollout = tmp_path / "codex" / "2026" / "09" / "27" / "rollout-2026-09-27T10-00-00-thread-1.jsonl"
    write_jsonl(rollout, [
        {"timestamp": iso(0), "type": "session_meta", "payload": {"id": "thread-1", "cwd": "/w"}},
        {"timestamp": iso(1), "type": "event_msg", "payload": {"type": "user_message", "message": SECRET}},
        {"timestamp": iso(2), "type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                                                   "call_id": "call_1", "arguments": SECRET}},
        {"timestamp": iso(3), "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call_1",
                                                                   "output": SECRET}},
        {"timestamp": iso(4), "type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                                                   "call_id": "call_2", "arguments": "x"}},
        {"timestamp": iso(5), "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call_2",
                                                                   "output": "command was rejected by the user"}}])
    spool_lines(tmp_path / "spool", "codex", "thread-1", [("SessionStart", None), ("UserPromptSubmit", None),
                                                          ("PostToolUse", "call_1"), ("Stop", None),
                                                          ("SessionEnd", None)])
    manifest = {"sessions": [{"agent": "codex", "session_id": "thread-1", "kind": "scripted", "env": "win"}]}
    result = cc.count(str(tmp_path / "spool"), manifest, codex_sessions=str(tmp_path / "codex"))
    for event in cc.EVENTS:
        assert row(result, event)["lost"] == 0, event
    assert result["permission_denied"][0]["calls"] == 1


def test_the_report_holds_numbers_and_ids_only(tmp_path):
    turns = [[("toolu_1", "Edit", "ok")]]
    claude_transcript(tmp_path / "projects", "s-priv", turns)
    spool_lines(tmp_path / "spool", "claude", "s-priv", full_events(turns))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sessions": [{"agent": "claude", "session_id": "s-priv", "kind": "scripted",
                                                  "env": "win"}]}), encoding="utf-8")
    out_json = tmp_path / "rows.json"
    proc = subprocess.run([sys.executable, str(Path(cc.__file__)), "--spool", str(tmp_path / "spool"), "--manifest",
                           str(manifest), "--claude-projects", str(tmp_path / "projects"), "--json", str(out_json)],
                          capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.returncode == 0, proc.stderr
    assert SECRET not in proc.stdout and SECRET not in out_json.read_text(encoding="utf-8")
    assert "lost events" in proc.stdout and "coverage | win | scripted | 1 | 1" in proc.stdout


# ----------------------------------------------------------------- turn endings (from the T1 tools review)


def user(text, **extra):
    return dict({"type": "user", "timestamp": iso(0), "message": {"role": "user", "content": text}}, **extra)


def done(text=SECRET, **extra):
    return dict({"type": "assistant", "timestamp": iso(1), "message": {"role": "assistant", "stop_reason": "end_turn",
                                                                        "content": [{"type": "text", "text": text}]}},
                **extra)


def call(tid, name):
    return {"type": "assistant", "timestamp": iso(1), "message": {"role": "assistant", "stop_reason": "tool_use",
                                                                   "content": [{"type": "tool_use", "id": tid,
                                                                                "name": name, "input": {}}]}}


def result(tid, text=SECRET, error=False):
    return {"type": "user", "timestamp": iso(2), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": text}]}}


def counted(tmp_path, sid, rows, events, kind="interactive", **kw):
    write_jsonl(tmp_path / "projects" / "-w" / f"{sid}.jsonl", rows)
    spool_lines(tmp_path / "spool", "claude", sid, events)
    manifest = {"sessions": [{"agent": "claude", "session_id": sid, "kind": kind, "env": "win"}]}
    res = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"), **kw)
    return {r["event"]: (r["expected"], r["lost"], r["extra"]) for r in res["loss"] if r["kind"] == kind}, res


INTERRUPT = [{"type": "text", "text": "[Request interrupted by user for tool use]"}]


def test_an_interrupted_turn_expects_no_stop_and_its_marker_is_no_prompt(tmp_path):
    rows = [user("first"), done(), user("second"), call("toolu_1", "Bash"), result("toolu_1", "stopped", True),
            user(INTERRUPT), user("third"), done()]
    events = [("SessionStart", None), ("UserPromptSubmit", None), ("Stop", None), ("UserPromptSubmit", None),
              ("PostToolUseFailure", "toolu_1"), ("UserPromptSubmit", None), ("Stop", None), ("SessionEnd", None)]
    by_event, _ = counted(tmp_path, "s-int", rows, events)
    assert by_event["UserPromptSubmit"] == (3, 0, 0) and by_event["Stop"] == (2, 0, 0)


def test_compact_summaries_and_command_echoes_are_no_prompts(tmp_path):
    rows = [user("first"), done(), {"type": "system", "subtype": "compact_boundary"},
            user("This session is being continued...", isCompactSummary=True, isVisibleInTranscriptOnly=True),
            user("<command-name>/compact</command-name>"), user("second"), done()]
    events = [("SessionStart", None), ("UserPromptSubmit", None), ("Stop", None), ("SessionStart", None),
              ("UserPromptSubmit", None), ("Stop", None), ("SessionEnd", None)]
    by_event, _ = counted(tmp_path, "s-compact", rows, events)
    assert by_event["UserPromptSubmit"] == (2, 0, 0) and by_event["Stop"] == (2, 0, 0)
    assert by_event["SessionStart"] == (2, 0, 0)


def test_a_turn_ending_in_an_api_error_expects_stopfailure_not_stop(tmp_path):
    rows = [user("first"), dict(done("rate limited"), isApiErrorMessage=True, error="rate_limit")]
    events = [("SessionStart", None), ("UserPromptSubmit", None), ("StopFailure", None), ("SessionEnd", None)]
    by_event, _ = counted(tmp_path, "s-api", rows, events, kind="scripted")
    assert by_event["Stop"] == (0, 0, 0) and by_event["StopFailure"] == (1, 0, 0)


def test_a_call_rejected_at_input_validation_expects_no_hook(tmp_path):
    rows = [user("go"), call("toolu_1", "Edit"),
            result("toolu_1", "<tool_use_error>InputValidationError: Edit failed</tool_use_error>", True), done()]
    events = [("SessionStart", None), ("UserPromptSubmit", None), ("Stop", None), ("SessionEnd", None)]
    by_event, _ = counted(tmp_path, "s-invalid", rows, events, kind="scripted")
    assert by_event["PostToolUse"] == (0, 0, 0) and by_event["PreToolUse"] == (0, 0, 0)


def test_a_denied_subagent_call_expects_no_subagentstop(tmp_path):
    rows = [user("go"), call("toolu_1", "Agent"), result("toolu_1", "Permission to use Agent has been denied.", True),
            done()]
    events = [("SessionStart", None), ("UserPromptSubmit", None), ("Stop", None), ("SessionEnd", None)]
    by_event, res = counted(tmp_path, "s-deny-agent", rows, events, kind="scripted")
    assert by_event["SubagentStop"] == (0, 0, 0) and res["permission_denied"][0]["calls"] == 1


def test_a_session_killed_while_idle_still_expects_its_last_stop(tmp_path):
    rows = [user("go"), call("toolu_1", "Bash"), result("toolu_1"), done()]
    events = [("SessionStart", None), ("UserPromptSubmit", None), ("PostToolUse", "toolu_1")]   # its Stop is lost
    by_event, res = counted(tmp_path, "s-idle", rows, events, kind="killed", now=T0 + 7200)
    assert by_event["Stop"] == (1, 1, 0), "the last turn had ended: its Stop is lost, not forgiven"
    assert res["killed"][0]["missing_stop"] == 1


def test_a_ctrl_c_kill_mid_call_forgives_the_cut_short_stop(tmp_path):
    rows = [user("go"), call("toolu_1", "Bash"), result("toolu_1", "stopped", True), user(INTERRUPT)]
    events = [("SessionStart", None), ("UserPromptSubmit", None), ("PostToolUseFailure", "toolu_1"),
              ("SessionEnd", None)]
    by_event, res = counted(tmp_path, "s-ctrlc", rows, events, kind="killed")
    assert all(lost == 0 for _, lost, _ in by_event.values()), by_event
    assert res["killed"][0]["closed_by_session_end"] == 1


def test_a_killed_session_never_captured_is_not_closed_by_timeout(tmp_path):
    write_jsonl(tmp_path / "projects" / "-w" / "s-none.jsonl", [user("go"), call("toolu_1", "Bash")])
    (tmp_path / "spool" / "claude").mkdir(parents=True)
    manifest = {"sessions": [{"agent": "claude", "session_id": "s-none", "kind": "killed", "env": "win"}]}
    res = cc.count(str(tmp_path / "spool"), manifest, str(tmp_path / "projects"), now=T0 + 99999)
    assert res["killed"][0]["not_captured"] == 1 and res["killed"][0]["closed_by_timeout"] == 0


def test_codex_output_that_merely_contains_denial_words_is_no_denial(tmp_path):
    rollout = tmp_path / "codex" / "rollout-2026-09-27T10-00-00-thread-2.jsonl"
    write_jsonl(rollout, [
        {"timestamp": iso(0), "type": "session_meta", "payload": {"id": "thread-2"}},
        {"timestamp": iso(1), "type": "response_item", "payload": {"type": "function_call", "call_id": "c1",
                                                                   "name": "shell", "arguments": "cat LICENSE"}},
        {"timestamp": iso(2), "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "c1",
            "output": {"success": True, "content": "Exit code: 0\nPermission to use, copy, modify... "
                                                   "rejected by user"}}}])
    record = cc.read_codex_rollout(str(rollout))
    assert record.denied == set() and record.results == {"c1"}
