"""Tests for the hook script. Every payload is synthetic."""
import ast
import json
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from helpers import run_hook, spool_lines, tree
from orchestraty.capture import hook
from orchestraty.capture.install import HOOK_SOURCE

SECRET = "SECRET-PROMPT-TEXT-91c2"
FIELDS = {"session_id", "hook_event_name", "tool_name", "tool_use_id", "cwd", "transcript_path"}
SESSION = "0f8c1a2b-3c4d-4e5f-8a9b-1c2d3e4f5a6b"


def payload(**overrides):
    event = {"session_id": SESSION, "transcript_path": "/tmp/t/s.jsonl",
             "cwd": "/tmp/work", "permission_mode": "default", "hook_event_name": "PostToolUse",
             "tool_name": "Edit", "tool_use_id": "toolu_01ABC", "prompt": SECRET,
             "tool_input": {"file_path": "/tmp/work/a.py", "old_string": SECRET, "new_string": SECRET},
             "tool_response": {"output": SECRET * 20}}
    event.update(overrides)
    return json.dumps(event).encode("utf-8")


@pytest.fixture
def spool(state_root):
    return state_root / "spool"


def test_one_valid_line_per_event_with_only_the_named_fields(hook_script, spool):
    data = payload()
    proc = run_hook(hook_script, ["claude", "PostToolUse"], data)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [(name, line)] = spool_lines(spool)
    assert name == SESSION + ".jsonl"
    source = json.loads(data)
    assert {k: line[k] for k in FIELDS} == {k: source[k] for k in FIELDS}
    assert set(line) == FIELDS | {"ts", "pid", "agent", "event", "len", "file_path"}
    assert line["file_path"] == "/tmp/work/a.py"
    assert line["len"] == len(data) and line["agent"] == "claude" and line["event"] == "PostToolUse"
    assert SECRET.encode() not in (spool / "claude" / name).read_bytes(), "payload content was stored"


@pytest.mark.parametrize("data", [
    b"", b"not json at all", b"\xff\xfe\x00garbage", b"[1, 2, 3]", b'{"session_id": 5}',
    b'{"session_id": "a\x01b"}', b'{"session_id": "bad\\q escape"}', b'{"session_id": "unterminated',
    b'{"session_id": "\\u12"}', b'{"cwd": "\xc3\x28"}', b"{" * 100000,
    b'{"hook_event_name": "Stop", "session_id": "' + b"x" * 5000 + b'"}',
    b'{"stop_hook_active": tru}', b'{"stop_hook_active": "true"}',
], ids=["empty", "text", "binary", "array", "number", "control-char", "bad-escape", "unterminated",
        "short-unicode-escape", "bad-utf8", "deep-nesting", "overlong-field", "bad-flag", "flag-as-string"])
def test_bad_input_is_silent_exits_zero_and_still_gives_one_valid_line(hook_script, spool, data):
    proc = run_hook(hook_script, ["claude", "Stop"], data)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    lines = spool_lines(spool)                     # json.loads succeeded for every line
    assert len(lines) == 1
    assert lines[0][1]["len"] == len(data)
    assert "stop_hook_active" not in lines[0][1]


def test_a_large_payload_is_fine(hook_script, spool):
    data = payload(tool_response={"output": "y" * 3_000_000})
    proc = run_hook(hook_script, ["claude", "PostToolUse"], data)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [(_, line)] = spool_lines(spool)
    assert line["len"] == len(data) and line["tool_use_id"] == "toolu_01ABC"
    # output dense with quotes, backslashes and brackets, the event's own field after it
    dense = json.dumps({"session_id": "s-dense", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                        "tool_response": {"output": json.dumps([{"k": 'a"b\\c', "v": ["}", "{", '"']}] * 50_000)},
                        "tool_use_id": "toolu_DENSE"}).encode()
    proc = run_hook(hook_script, ["claude", "PostToolUse"], dense)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [line] = [x for _, x in spool_lines(spool) if x.get("session_id") == "s-dense"]
    assert line["tool_use_id"] == "toolu_DENSE" and "shim_error" not in line


def test_without_an_agent_it_writes_nothing_and_stays_silent(hook_script, state_root):
    proc = run_hook(hook_script, [], payload())
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    assert tree(state_root) == ["hooks", "hooks/hook.py"]


def test_outside_a_hooks_folder_it_writes_nothing(tmp_path):
    # The package's own copy, or a copy put anywhere else, must never pick a state folder.
    elsewhere = tmp_path / "somewhere" / "hook.py"
    elsewhere.parent.mkdir()
    shutil.copyfile(HOOK_SOURCE, elsewhere)
    proc = run_hook(elsewhere, ["claude", "PostToolUse"], payload())
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    assert tree(tmp_path / "somewhere") == ["hook.py"]
    assert hook.state_folder(HOOK_SOURCE) is None


def test_an_internal_failure_leaves_one_shim_error_line(hook_script, spool):
    blocked = spool / "claude" / (SESSION + ".jsonl")
    blocked.mkdir(parents=True)                   # a folder where the session file should be
    proc = run_hook(hook_script, ["claude", "PostToolUse"], payload())
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [(name, line)] = spool_lines(spool)
    assert name == "shim_errors.jsonl"
    assert set(line) == {"ts", "pid", "agent", "shim_error"} and line["shim_error"].endswith("Error")


def test_names_from_the_payload_cannot_leave_the_spool(hook_script, state_root, spool):
    run_hook(hook_script, ["../../outside", "Stop"], payload(session_id="../../../evil\\..\\name"))
    assert sorted(p.name for p in state_root.iterdir()) == ["hooks", "spool"]
    written = [p.relative_to(spool).as_posix() for p in spool.rglob("*") if p.is_file()]
    assert written == ["outside/evilname.jsonl"]


def test_fields_are_taken_from_keys_not_from_text_that_looks_like_them():
    raw = json.dumps({"note": "session_id", "tool_input": {"content": '{"session_id": "fake", "cwd": "x"}'},
                      "session_id": "real-one", "cwd": "/w"}).encode()
    assert hook.field(raw, b"session_id") == b'"real-one"'
    assert hook.field(raw, b"cwd") == b'"/w"'
    assert hook.field(raw, b"tool_name") is None


def recorded_order(event, tool_input, tool_response=None, tail=(), tool="mcp__x__y"):
    """A payload with the top-level key order recorded from Claude Code 2.1.283."""
    top = [("session_id", "top-session"), ("transcript_path", "/t/top.jsonl"), ("cwd", "/top"),
           ("prompt_id", "p-1"), ("permission_mode", "acceptEdits"), ("effort", "medium"),
           ("hook_event_name", event), ("tool_name", tool), ("tool_input", tool_input)]
    if tool_response is not None:
        top.append(("tool_response", tool_response))
    top += [("tool_use_id", "toolu_TOP")] + list(tail)
    return json.dumps(dict(top)).encode()


DECOY = {"session_id": "nested-session", "cwd": "/nested", "hook_event_name": "Nested", "tool_name": "nested",
         "tool_use_id": "toolu_NESTED", "transcript_path": "/nested.jsonl"}


@pytest.mark.parametrize("event,raw", [
    ("PreToolUse", recorded_order("PreToolUse", {"args": DECOY})),
    ("PostToolUse", recorded_order("PostToolUse", {"args": DECOY}, {"result": [DECOY, {"deeper": DECOY}]},
                                   [("duration_ms", 12)])),
    ("PostToolUseFailure", recorded_order("PostToolUseFailure", {"args": DECOY}, None,
                                          [("error", "boom"), ("is_interrupt", False), ("duration_ms", 3)])),
], ids=["pre", "post", "post-failure"])
def test_nested_decoy_keys_never_replace_the_top_level_values(hook_script, spool, event, raw):
    run_hook(hook_script, ["claude", event], raw)
    [(name, line)] = spool_lines(spool)
    assert name == "top-session.jsonl"
    assert {k: line[k] for k in FIELDS} == {"session_id": "top-session", "transcript_path": "/t/top.jsonl",
                                            "cwd": "/top", "hook_event_name": event, "tool_name": "mcp__x__y",
                                            "tool_use_id": "toolu_TOP"}
    assert "file_path" not in line


def test_tool_fields_are_read_only_for_tool_events(hook_script, spool):
    stop = json.dumps({"session_id": "s-stop", "transcript_path": "/t.jsonl", "cwd": "/w", "hook_event_name": "Stop",
                       "stop_hook_active": False, "last_assistant_message": "done",
                       "background_tasks": [{"tool_name": "Edit", "tool_use_id": "toolu_BG",
                                             "file_path": "/w/x.py"}]}).encode()
    run_hook(hook_script, ["claude", "Stop"], stop)
    [(_, line)] = spool_lines(spool)
    assert line["session_id"] == "s-stop" and line["stop_hook_active"] is False
    assert not {"tool_name", "tool_use_id", "file_path"} & set(line)


def test_a_key_is_read_only_as_a_member_of_the_payload_itself():
    raw = b'{"tool_use_id": "toolu_1", "note": "tool_use_id", "x": {"tool_use_id": 5}, "y": "\\"tool_use_id\\""}'
    assert hook.field(raw, b"tool_use_id") == b'"toolu_1"'
    raw = b'{"a": {"tool_use_id": "nested"}, "tool_use_id": "toolu_2", "note": "tool_use_id"}'
    assert hook.field(raw, b"tool_use_id") == b'"toolu_2"'
    raw = b'{"tool_use_id": 5, "x": {"tool_use_id": "nested"}}'
    assert hook.field(raw, b"tool_use_id") is None                       # the payload's own is not a string


def test_unicode_and_escapes_survive_as_valid_json(hook_script, spool):
    data = payload(cwd="C:\\Users\\Женя\\代码 \"quoted\"", transcript_path="/tmp/\u00e9\t\U0001F600.jsonl",
                   tool_input={"file_path": "C:\\Users\\Женя\\код.py"})
    run_hook(hook_script, ["claude", "PostToolUse"], data)
    [(_, line)] = spool_lines(spool)
    assert line["cwd"] == "C:\\Users\\Женя\\代码 \"quoted\"" and line["transcript_path"] == "/tmp/\u00e9\t\U0001F600.jsonl"
    assert line["file_path"] == "C:\\Users\\Женя\\код.py"


# --------------------------------------------------------------------------------------- #
# What the live map needs: the edited file, how a session started and ended, the Stop flag
# --------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool,tool_input,expected", [
    ("Edit", {"file_path": "/w/a.py", "old_string": "x", "new_string": "y"}, "/w/a.py"),
    ("Write", {"file_path": "/w/new.py", "content": "print(1)\n"}, "/w/new.py"),
    ("MultiEdit", {"file_path": "/w/m.py", "edits": [{"old_string": "a", "new_string": "b"}]}, "/w/m.py"),
    ("NotebookEdit", {"notebook_path": "/w/n.ipynb", "new_source": "x = 1"}, "/w/n.ipynb"),
    ("Read", {"file_path": "/w/read-only.py"}, None),
    ("Bash", {"command": "sed -i s/a/b/ /w/a.py", "description": "edit"}, None),
], ids=["edit", "write", "multiedit", "notebook", "read-is-not-an-edit", "bash"])
def test_the_edited_file_is_recorded_for_edit_tools_only(hook_script, spool, tool, tool_input, expected):
    for event in ("PreToolUse", "PostToolUse"):
        raw = recorded_order(event, tool_input, {"ok": True} if event == "PostToolUse" else None, tool=tool)
        run_hook(hook_script, ["claude", event], raw)
    lines = [line for _, line in spool_lines(spool)]
    assert len(lines) == 2
    assert [line.get("file_path") for line in lines] == [expected, expected]
    assert all("command" not in line and "sed" not in json.dumps(line) for line in lines)


def test_a_file_path_inside_written_content_cannot_stand_in_for_the_real_one(hook_script, spool):
    content = '{"file_path": "/evil/elsewhere.py"}\n"file_path": "/evil/two.py"'
    raw = recorded_order("PreToolUse", {"content": content, "file_path": "/w/real.py"}, tool="Write")
    run_hook(hook_script, ["claude", "PreToolUse"], raw)
    [(_, line)] = spool_lines(spool)
    assert line["file_path"] == "/w/real.py"


def test_the_file_path_comes_from_the_tool_input_not_the_tool_response(hook_script, spool):
    raw = recorded_order("PostToolUse", {"file_path": "/w/input.py", "old_string": "a", "new_string": "b"},
                         {"file_path": "/w/response.py", "nested": {"file_path": "/w/deeper.py"}}, tool="Edit")
    run_hook(hook_script, ["claude", "PostToolUse"], raw)
    [(_, line)] = spool_lines(spool)
    assert line["file_path"] == "/w/input.py"


def test_the_file_path_is_read_only_when_the_event_is_a_tool_event(hook_script, spool):
    raw = json.dumps({"session_id": "s1", "hook_event_name": "UserPromptSubmit", "tool_name": "Edit",
                      "file_path": "/w/x.py", "prompt": SECRET}).encode()
    run_hook(hook_script, ["claude", "UserPromptSubmit"], raw)
    [(_, line)] = spool_lines(spool)
    assert not {"file_path", "tool_name"} & set(line)
    assert SECRET not in json.dumps(line)


def test_session_start_source_session_end_reason_and_the_stop_flag(hook_script, spool):
    events = [
        ("SessionStart", {"session_id": "s1", "hook_event_name": "SessionStart", "source": "startup", "model": "m"}),
        ("SessionEnd", {"session_id": "s1", "hook_event_name": "SessionEnd", "reason": "clear"}),
        ("Stop", {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": True}),
        ("SubagentStop", {"session_id": "s1", "hook_event_name": "SubagentStop", "stop_hook_active": False}),
        ("UserPromptSubmit", {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "source": "x",
                              "reason": "y", "stop_hook_active": True}),
    ]
    for event, body in events:
        run_hook(hook_script, ["claude", event], json.dumps(body).encode())
    lines = {line["event"]: line for _, line in spool_lines(spool)}
    assert lines["SessionStart"]["source"] == "startup" and "reason" not in lines["SessionStart"]
    assert lines["SessionEnd"]["reason"] == "clear" and "source" not in lines["SessionEnd"]
    assert lines["Stop"]["stop_hook_active"] is True
    assert lines["SubagentStop"]["stop_hook_active"] is False
    assert not {"source", "reason", "stop_hook_active"} & set(lines["UserPromptSubmit"])


@pytest.mark.parametrize("raw,expected", [
    (b'{"stop_hook_active": true}', True), (b'{"stop_hook_active":false,"x":1}', False),
    (b'{"stop_hook_active" :\n true }', True), (b'{"stop_hook_active": "true"}', None),
    (b'{"stop_hook_active": truest}', None), (b'{"stop_hook_active": null}', None),
    (b'{"x": "stop_hook_active", "stop_hook_active": true}', True), (b"{}", None),
])
def test_flag(raw, expected):
    assert hook.flag(raw, b"stop_hook_active") is expected


# --------------------------------------------------------------------------------------- #
# Start-up cost and the append
# --------------------------------------------------------------------------------------- #
def test_built_in_modules_only_and_no_json():
    tree_ = ast.parse(Path(HOOK_SOURCE).read_text(encoding="utf-8"))
    mods = {a.name for n in ast.walk(tree_) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module for n in ast.walk(tree_) if isinstance(n, ast.ImportFrom)}
    assert mods <= {"os", "sys", "time", "_winapi"}      # all loaded by the interpreter itself
    # At run time, too: nothing beyond what "python -S -I" loads anyway, and never json.
    probe = textwrap.dedent("""
        import os, sys, time
        if os.name == "nt":
            import _winapi
        path = %r
        with open(path, "rb") as fh:
            code = compile(fh.read(), path, "exec")
        before = set(sys.modules)
        exec(code, {"__name__": "not_main", "__file__": path})
        sys.stdout.write(" ".join(sorted(set(sys.modules) - before)))
    """ % str(HOOK_SOURCE))
    # -B: -I ignores PYTHONDONTWRITEBYTECODE, and the import must not leave __pycache__ behind.
    out = subprocess.run([sys.executable, "-S", "-I", "-B", "-c", probe], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout == "", "the hook loads more than os, sys, time and _winapi: " + out.stdout


def test_five_processes_append_a_thousand_lines_each_without_loss_or_interleaving(tmp_path):
    # Plain O_APPEND through the C runtime lost 30-39 of 1,000 events and broke lines on Windows.
    target = tmp_path / "shared.jsonl"
    worker = textwrap.dedent("""
        import runpy, sys
        hook = runpy.run_path(%r, run_name="not_main")
        writer = int(sys.argv[1])
        for i in range(1000):
            line = b'{"writer":%%d,"seq":%%d,"pad":"%%s"}\\n' %% (writer, i, b"x" * 400)
            hook["append"](%r, line)
    """ % (str(HOOK_SOURCE), str(target)))
    procs = [subprocess.Popen([sys.executable, "-S", "-I", "-B", "-c", worker, str(w)]) for w in range(5)]
    assert [p.wait(timeout=300) for p in procs] == [0] * 5
    raw = target.read_bytes().splitlines()
    parsed = [json.loads(line) for line in raw]            # a torn or interleaved line would not parse
    assert len(parsed) == 5000
    assert {(p["writer"], p["seq"]) for p in parsed} == {(w, i) for w in range(5) for i in range(1000)}
    assert all(p["pad"] == "x" * 400 for p in parsed)


def test_parallel_hook_runs_into_one_session_file_lose_nothing(hook_script, spool):
    errors = []

    def launcher(k):
        for i in range(20):
            proc = run_hook(hook_script, ["claude", "PostToolUse"], payload(tool_use_id=f"toolu_{k}_{i}"))
            if proc.returncode or proc.stdout or proc.stderr:
                errors.append(proc)

    threads = [threading.Thread(target=launcher, args=(k,)) for k in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    lines = spool_lines(spool)
    assert sorted(line["tool_use_id"] for _, line in lines) == sorted(
        f"toolu_{k}_{i}" for k in range(5) for i in range(20))


# --------------------------------------------------------------------------------------- #
# Only the payload's own members: never a key inside the tool's input or output
# --------------------------------------------------------------------------------------- #
NESTED = "SECRET-NESTED-7f3a"


@pytest.mark.parametrize("body,gone", [
    ({"session_id": "s1", "transcript_path": "/t.jsonl", "hook_event_name": "PostToolUse", "tool_name": "Bash",
      "tool_input": {"command": "ls", "cwd": NESTED}, "tool_response": {}, "tool_use_id": "toolu_1"}, "cwd"),
    ({"transcript_path": "/t.jsonl", "cwd": "/w", "hook_event_name": "PostToolUse", "tool_name": "mcp__x__y",
      "tool_input": {"session_id": NESTED}, "tool_response": {}, "tool_use_id": "toolu_1"}, "session_id"),
    ({"session_id": "s1", "cwd": "/w", "hook_event_name": "PostToolUse", "tool_name": "Edit",
      "tool_input": {"old_string": "a", "new_string": "b"}, "tool_response": {"file_path": NESTED},
      "tool_use_id": "toolu_1"}, "file_path"),
    ({"session_id": "s1", "cwd": "/w", "hook_event_name": "PostToolUse",
      "tool_input": {"tool_name": NESTED, "transcript_path": NESTED}, "tool_use_id": "toolu_1"}, "tool_name"),
], ids=["cwd", "session", "edit-file", "tool-and-transcript"])
def test_a_missing_top_level_field_is_never_taken_from_inside_the_tool(hook_script, spool, body, gone):
    run_hook(hook_script, ["claude", "PostToolUse"], json.dumps(body).encode())
    [(name, line)] = spool_lines(spool)
    assert gone not in line and NESTED not in json.dumps(line) and NESTED not in name


@pytest.mark.parametrize("order", ["use-id-first", "response-first", "nested-edit-file-first"])
def test_any_key_order_gives_the_top_level_values(hook_script, spool, order):
    tool_input = {"file_path": "/w/real.py", "old_string": "a", "new_string": "b"}
    response = {"file_path": "/w/response.py", "tool_use_id": "toolu_NESTED", "structuredContent": [DECOY]}
    if order == "use-id-first":
        body = dict(session_id="s1", cwd="/w", hook_event_name="PostToolUse", tool_name="Edit", tool_use_id="toolu_TOP",
                    tool_input=tool_input, tool_response=response)
    elif order == "response-first":
        body = dict(tool_response=response, session_id="s1", cwd="/w", hook_event_name="PostToolUse", tool_name="Edit",
                    tool_input=tool_input, tool_use_id="toolu_TOP")
    else:
        body = dict(session_id="s1", cwd="/w", hook_event_name="PostToolUse", tool_name="MultiEdit",
                    tool_input={"edits": [{"file_path": "/w/inner.py", "old_string": "a"}], "file_path": "/w/real.py"},
                    tool_use_id="toolu_TOP")
    run_hook(hook_script, ["claude", "PostToolUse"], json.dumps(body).encode())
    [(_, line)] = spool_lines(spool)
    assert (line["session_id"], line["cwd"], line["tool_use_id"], line["file_path"]) == ("s1", "/w", "toolu_TOP",
                                                                                         "/w/real.py")


def test_a_nested_stop_flag_or_source_is_not_the_events_own(hook_script, spool, state_root):
    folder = state_root / "notes" / "s1"
    folder.mkdir(parents=True)
    (folder / "stop.json").write_bytes(b'{"decision":"block","reason":"callers left"}')
    stop = {"session_id": "s1", "background_tasks": [{"stop_hook_active": True}], "stop_hook_active": False}
    proc = run_hook(hook_script, ["claude", "Stop"], json.dumps(stop).encode())
    assert proc.stdout == b'{"decision":"block","reason":"callers left"}'          # the top-level flag is false
    run_hook(hook_script, ["claude", "SessionStart"], json.dumps({"session_id": "s1", "x": {"source": NESTED}}).encode())
    lines = {line["event"]: line for _, line in spool_lines(spool)}
    assert lines["Stop"]["stop_hook_active"] is False and "source" not in lines["SessionStart"]


def test_strings_holding_brackets_quotes_and_backslashes_are_skipped_whole():
    tricky = 'a\\" } ] { [ "cwd": "x" \\\\'
    raw = json.dumps({"tool_input": {"content": tricky, "list": ['"', "\\", "}", "]", {"cwd": "nested"}]},
                      "tool_response": "\\\\\"", "empty": {}, "n": [1, [2, [3]], {"a": []}], "flag": True,
                      "cwd": "/real", "tool_use_id": "toolu_R"}).encode()
    assert hook.field(raw, b"cwd") == b'"/real"' and hook.field(raw, b"tool_use_id") == b'"toolu_R"'
    compact = json.dumps(json.loads(raw), separators=(",", ":")).encode()
    assert hook.field(compact, b"cwd") == b'"/real"' and hook.field(compact, b"tool_use_id") == b'"toolu_R"'


def test_long_runs_of_brackets_cost_no_more_than_their_length():
    # a tool's structured rows of numbers: many ] and no string between them
    raw = json.dumps({"tool_response": {"rows": [[i, i + 1, i + 2] for i in range(200_000)]},
                      "tool_use_id": "toolu_M"}).encode()
    started = time.perf_counter()
    assert hook.field(raw, b"tool_use_id") == b'"toolu_M"'
    assert time.perf_counter() - started < 1.0                  # it took seconds when each ] scanned to the end
    raw = json.dumps({"x": [{} for _ in range(100_000)], "y": [[[[1]]]] * 50_000, "cwd": "/w"}).encode()
    assert hook.field(raw, b"cwd") == b'"/w"'


def test_a_payload_that_starts_with_a_byte_order_mark_is_read(hook_script, spool):
    body = json.dumps({"session_id": "s-bom", "cwd": "/w", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                       "tool_use_id": "toolu_B"}).encode()
    run_hook(hook_script, ["claude", "PostToolUse"], b"\xef\xbb\xbf" + body)
    [(name, line)] = spool_lines(spool)
    assert name == "s-bom.jsonl" and (line["session_id"], line["cwd"], line["tool_use_id"]) == ("s-bom", "/w", "toolu_B")


@pytest.mark.parametrize("name", ["\\ud800", "\\udc00", "\\ud800x", "\\ud800\\u0041", "\\udbff\\ud800"])
def test_a_file_with_half_a_surrogate_pair_is_left_out_and_the_event_kept(hook_script, spool, name):
    data = payload(tool_input={"file_path": "/w/POISON.py"}).replace(b"POISON", name.encode())
    proc = run_hook(hook_script, ["claude", "PostToolUse"], data)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [(_, line)] = spool_lines(spool)
    assert "file_path" not in line and line["session_id"] == SESSION and line["tool_name"] == "Edit"


def test_a_whole_surrogate_pair_is_kept(hook_script, spool):
    data = payload(tool_input={"file_path": "/w/\U0001F600.py"})          # json.dumps writes 😀
    assert b"\\ud83d\\ude00" in data
    run_hook(hook_script, ["claude", "PostToolUse"], data)
    [(_, line)] = spool_lines(spool)
    assert line["file_path"] == "/w/\U0001F600.py"
