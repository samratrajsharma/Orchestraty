"""The session-log reader: what it keeps, and that it never decodes or keeps anything else.
Every log here is synthetic."""
import json
import os

import pytest

from history_logs import EDIT_TOOLS, Unreadable, parse_time, read_sessions, select

NOW = parse_time("2026-10-20T12:00:00Z")
# Planted in every place the reader must not keep: prompts, replies, thinking, tool inputs,
# file contents, results, titles. None may ever come back out.
SECRETS = ["PROMPT-CANARY", "REPLY-CANARY", "THINK-CANARY", "OLD-CANARY", "NEW-CANARY", "FILE-CANARY",
           "PATCH-CANARY", "TITLE-CANARY", "LASTPROMPT-CANARY", "ATTACH-CANARY", "BASH-CANARY"]


def at(hhmm, day="2026-10-20"):
    return f"{day}T{hhmm}:00.000Z"


def user_prompt(sid, when, cwd="/w/repo"):
    return {"parentUuid": None, "isSidechain": False, "userType": "external", "cwd": cwd, "sessionId": sid,
            "version": "2.1.292", "gitBranch": "main", "type": "user",
            "message": {"role": "user", "content": "PROMPT-CANARY please edit"}, "uuid": "u1", "timestamp": when}


def tool_use(sid, when, use_id, name, path, cwd="/w/repo"):
    inp = {"file_path": path, "old_string": "OLD-CANARY", "new_string": "NEW-CANARY"}
    if name == "Write":
        inp = {"file_path": path, "content": "FILE-CANARY"}
    if name == "NotebookEdit":
        inp = {"notebook_path": path, "new_source": "NEW-CANARY"}
    if name == "MultiEdit":
        inp = {"file_path": path, "edits": [{"old_string": "OLD-CANARY", "new_string": "NEW-CANARY"}]}
    return {"parentUuid": "u1", "isSidechain": False, "cwd": cwd, "sessionId": sid, "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "THINK-CANARY"},
                {"type": "text", "text": "REPLY-CANARY"},
                {"type": "tool_use", "id": use_id, "name": name, "input": inp}]},
            "uuid": "a1", "timestamp": when}


def tool_result(sid, when, use_id, hunks=(), error=False, cwd="/w/repo"):
    line = {"parentUuid": "a1", "cwd": cwd, "sessionId": sid, "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": use_id, "content": "PATCH-CANARY done",
                 "is_error": error}]},
            "uuid": "u2", "timestamp": when}
    if not error:
        line["toolUseResult"] = {"filePath": "/w/repo/x.py", "oldString": "OLD-CANARY", "newString": "NEW-CANARY",
                                 "originalFile": "FILE-CANARY\n" * 3,
                                 "structuredPatch": [{"oldStart": s, "oldLines": c, "newStart": s, "newLines": c,
                                                      "lines": ["-PATCH-CANARY", "+PATCH-CANARY"]}
                                                     for s, c in hunks]}
    else:
        line["toolUseResult"] = "Error: PATCH-CANARY"
    return line


def extras(sid):
    return [{"type": "custom-title", "customTitle": "TITLE-CANARY", "sessionId": sid},
            {"type": "last-prompt", "lastPrompt": "LASTPROMPT-CANARY", "sessionId": sid},
            {"type": "attachment", "attachment": {"content": "ATTACH-CANARY"}, "cwd": "/w/repo",
             "sessionId": sid, "timestamp": at("10:01")},
            {"type": "user", "cwd": "/w/repo", "sessionId": sid, "timestamp": at("10:02"),
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "bash1",
                                                       "content": "BASH-CANARY"}]},
             "toolUseResult": {"stdout": "BASH-CANARY", "stderr": ""}}]


def write_log(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write((line if isinstance(line, str) else json.dumps(line)) + "\n")
    os.utime(path, (NOW, NOW))


@pytest.fixture
def projects(tmp_path):
    return tmp_path / "projects"


def one_session(projects):
    sid = "s1"
    write_log(str(projects / "C--w-repo" / "s1.jsonl"), [
        user_prompt(sid, at("10:00")),
        *extras(sid),
        tool_use(sid, at("10:05"), "t1", "Edit", "/w/repo/a.py"),
        tool_result(sid, at("10:05"), "t1", hunks=[(10, 3), (40, 2)]),
        tool_use(sid, at("10:06"), "t2", "Write", "/w/repo/new.py"),
        tool_result(sid, at("10:06"), "t2"),
        tool_use(sid, at("10:07"), "t3", "Edit", "/w/repo/failed.py"),
        tool_result(sid, at("10:07"), "t3", error=True),
        tool_use(sid, at("10:08"), "t4", "Edit", "/w/repo/cut-off.py"),        # no result: the session was cut
        tool_use(sid, at("10:09"), "t5", "NotebookEdit", "/w/repo/n.ipynb"),
        tool_result(sid, at("10:09"), "t5"),
        tool_use(sid, at("10:10"), "t6", "MultiEdit", "/w/repo/m.py"),
        tool_result(sid, at("10:10"), "t6", hunks=[(5, 1)]),
    ])
    return sid


def everything(sessions):
    """Every value the reader returned, as one string."""
    parts = []
    for s in sessions.values():
        parts += [s.id, s.cwd, repr(s.times), repr(s.stretches)] + [repr(e) for e in s.edits]
    return "\n".join(parts)


def test_a_session_keeps_its_folder_times_and_successful_edits(projects):
    one_session(projects)
    sessions, skipped = read_sessions(str(projects), now=NOW)
    [s] = sessions.values()
    assert (s.id, s.cwd, skipped) == ("s1", "/w/repo", {"lines": 0, "logs": 0})
    assert s.start == parse_time(at("10:00")) and s.end == parse_time(at("10:10"))
    got = [(e.tool, e.path, e.lines) for e in s.edits]
    assert got == [("Edit", "/w/repo/a.py", ((10, 3), (40, 2))), ("Write", "/w/repo/new.py", ()),
                   ("NotebookEdit", "/w/repo/n.ipynb", ()), ("MultiEdit", "/w/repo/m.py", ((5, 1),))]
    assert set(EDIT_TOOLS) == {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def test_nothing_from_prompts_replies_contents_or_results_comes_back(projects):
    one_session(projects)
    out = everything(read_sessions(str(projects), now=NOW)[0])
    for secret in SECRETS:
        assert secret not in out, secret


def test_strings_outside_the_wanted_places_are_never_decoded():
    # A broken escape anywhere else would raise if the reader tried to decode it.
    line = (b'{"type":"user","message":{"content":"bad \\q escape","x":[{"y":"\\q"}]},'
            b'"cwd":"/w/r","timestamp":"2026-10-20T10:00:00Z"}')
    assert dict(select(line)) == {("type",): "user", ("cwd",): "/w/r", ("timestamp",): "2026-10-20T10:00:00Z"}
    with pytest.raises(ValueError):                       # the same escape in a wanted place
        select(b'{"cwd":"bad \\q"}')


def test_paths_lists_and_values():
    line = json.dumps({"message": {"content": [{}, {"type": "tool_use", "id": "t", "name": "Edit",
                                                     "input": {"file_path": "C:\\w\\a.py", "n": [1, 2]}},
                                               "plain", {"type": "text", "text": "skip"}]},
                       "toolUseResult": {"structuredPatch": [{"newStart": 3, "newLines": 0}]},
                       "type": "assistant", "timestamp": "t"}).encode()
    got = select(line)
    assert (("message", "content", 1, "input", "file_path"), "C:\\w\\a.py") in got
    assert (("message", "content", 3, "type"), "text") in got              # an empty object and a string before it
    assert (("toolUseResult", "structuredPatch", 0, "newLines"), 0) in got
    assert not any(v in ("skip", "plain") for _, v in got)


def test_lines_it_cannot_follow_are_dropped_and_counted(projects):
    sid = one_session(projects)
    path = str(projects / "C--w-repo" / "s1.jsonl")
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write('{"type":"user","cwd":"/w/repo","timestamp":"' + at("10:30") + '","message":{"content":"PROMPT-CAN\n')
    os.utime(path, (NOW, NOW))
    sessions, skipped = read_sessions(str(projects), now=NOW)
    assert skipped["lines"] == 1 and sessions[sid].end == parse_time(at("10:10"))
    with pytest.raises(Unreadable):
        select(b'{"a":[1,2')


def test_subagent_logs_count_for_their_session_and_idle_gaps_split_stretches(projects):
    sid = one_session(projects)
    write_log(str(projects / "C--w-repo" / sid / "subagents" / "agent-x.jsonl"), [
        tool_use("agent-x", at("13:00", "2026-10-19"), "s9", "Edit", "/w/repo/b.py"),
        tool_result("agent-x", at("13:00", "2026-10-19"), "s9", hunks=[(1, 1)]),
    ])
    sessions, _ = read_sessions(str(projects), now=NOW)
    assert list(sessions) == [sid]
    s = sessions[sid]
    assert [e.path for e in s.edits][0] == "/w/repo/b.py"                  # the subagent's, a day earlier
    assert s.stretches == [(parse_time(at("13:00", "2026-10-19")),) * 2,
                           (parse_time(at("10:00")), parse_time(at("10:10")))]


def test_only_the_last_30_days(projects):
    write_log(str(projects / "C--w-old" / "old.jsonl"), [user_prompt("old", at("10:00", "2026-09-01"))])
    write_log(str(projects / "C--w-mixed" / "mixed.jsonl"), [
        user_prompt("mixed", at("10:00", "2026-09-01")), user_prompt("mixed", at("10:00", "2026-10-19"))])
    sessions, _ = read_sessions(str(projects), now=NOW)
    assert list(sessions) == ["mixed"] and sessions["mixed"].start == parse_time(at("10:00", "2026-10-19"))
    os.utime(str(projects / "C--w-mixed" / "mixed.jsonl"), (NOW - 40 * 86400, NOW - 40 * 86400))
    assert read_sessions(str(projects), now=NOW)[0] == {}                  # not written to in 30 days: not opened
