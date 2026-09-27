"""Tests for the capture shim. Every payload is synthetic."""
import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import capture_shim

HERE = Path(__file__).resolve().parent
SHIM = HERE / "capture_shim.py"
SECRET = "SECRET-PROMPT-TEXT-91c2"
FIELDS = {"session_id", "hook_event_name", "tool_name", "tool_use_id", "cwd", "transcript_path"}


def payload(**overrides):
    event = {"session_id": "0f8c1a2b-3c4d-4e5f-8a9b-1c2d3e4f5a6b", "transcript_path": "/tmp/t/s.jsonl",
             "cwd": "/tmp/work", "permission_mode": "default", "hook_event_name": "PostToolUse",
             "tool_name": "Edit", "tool_use_id": "toolu_01ABC", "prompt": SECRET,
             "tool_input": {"file_path": "/tmp/work/a.py", "new_string": SECRET},
             "tool_response": {"output": SECRET * 20}}
    event.update(overrides)
    return json.dumps(event).encode("utf-8")


def run_shim(args, data, cwd=None):
    return subprocess.run([sys.executable, "-S", "-I", str(SHIM)] + [str(a) for a in args], input=data,
                          capture_output=True, timeout=60, cwd=cwd)


def spool_lines(spool):
    out = []
    for path in sorted(p for p in Path(spool).rglob("*.jsonl") if p.is_file()):
        for line in path.read_bytes().splitlines():
            out.append((path.name, json.loads(line)))
    return out


def test_one_valid_line_per_event_with_only_the_named_fields(tmp_path):
    data = payload()
    proc = run_shim([tmp_path, "claude", "PostToolUse"], data)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [(name, line)] = spool_lines(tmp_path)
    assert name == "0f8c1a2b-3c4d-4e5f-8a9b-1c2d3e4f5a6b.jsonl"
    source = json.loads(data)
    assert {k: line[k] for k in FIELDS} == {k: source[k] for k in FIELDS}
    assert set(line) == FIELDS | {"ts", "pid", "agent", "event", "len"}
    assert line["len"] == len(data) and line["agent"] == "claude" and line["event"] == "PostToolUse"
    assert SECRET.encode() not in (tmp_path / "claude" / name).read_bytes(), "payload content was stored"


@pytest.mark.parametrize("data", [
    b"", b"not json at all", b"\xff\xfe\x00garbage", b"[1, 2, 3]", b'{"session_id": 5}',
    b'{"session_id": "a\x01b"}', b'{"session_id": "bad\\q escape"}', b'{"session_id": "unterminated',
    b'{"session_id": "\\u12"}', b'{"cwd": "\xc3\x28"}', b"{" * 100000,
    b'{"hook_event_name": "Stop", "session_id": "' + b"x" * 5000 + b'"}',
], ids=["empty", "text", "binary", "array", "number", "control-char", "bad-escape", "unterminated",
        "short-unicode-escape", "bad-utf8", "deep-nesting", "overlong-field"])
def test_bad_input_is_silent_exits_zero_and_still_gives_one_valid_line(tmp_path, data):
    proc = run_shim([tmp_path, "claude", "Stop"], data)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    lines = spool_lines(tmp_path)                 # json.loads succeeded for every line
    assert len(lines) == 1
    assert lines[0][1]["len"] == len(data)


def test_a_large_payload_is_fine(tmp_path):
    data = payload(tool_response={"output": "y" * 3_000_000})
    proc = run_shim([tmp_path, "claude"], data)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [(_, line)] = spool_lines(tmp_path)
    assert line["len"] == len(data) and line["tool_use_id"] == "toolu_01ABC"


def test_without_arguments_it_writes_nothing_and_stays_silent(tmp_path):
    proc = run_shim([], payload(), cwd=tmp_path)
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    assert list(tmp_path.iterdir()) == []


def test_an_internal_failure_leaves_one_shim_error_line(tmp_path):
    blocked = tmp_path / "claude" / "0f8c1a2b-3c4d-4e5f-8a9b-1c2d3e4f5a6b.jsonl"
    blocked.mkdir(parents=True)                   # a folder where the session file should be
    proc = run_shim([tmp_path, "claude"], payload())
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    [(name, line)] = spool_lines(tmp_path)
    assert name == "shim_errors.jsonl"
    assert set(line) == {"ts", "pid", "agent", "shim_error"} and line["shim_error"].endswith("Error")


def test_names_from_the_payload_cannot_leave_the_spool(tmp_path):
    spool = tmp_path / "spool"
    run_shim([spool, "../../outside", "Stop"], payload(session_id="../../../evil\\..\\name"))
    assert [p.name for p in tmp_path.iterdir()] == ["spool"]
    written = [p.relative_to(spool).as_posix() for p in spool.rglob("*") if p.is_file()]
    assert written == ["outside/evilname.jsonl"]


def test_fields_are_taken_from_keys_not_from_text_that_looks_like_them():
    raw = json.dumps({"note": "session_id", "tool_input": {"content": '{"session_id": "fake", "cwd": "x"}'},
                      "session_id": "real-one", "cwd": "/w"}).encode()
    assert capture_shim.field(raw, b"session_id") == b'"real-one"'
    assert capture_shim.field(raw, b"cwd") == b'"/w"'
    assert capture_shim.field(raw, b"tool_name") is None


def test_unicode_and_escapes_survive_as_valid_json(tmp_path):
    data = payload(cwd="C:\\Users\\Женя\\代码 \"quoted\"", transcript_path="/tmp/\u00e9\t\U0001F600.jsonl")
    run_shim([tmp_path, "claude"], data)
    [(_, line)] = spool_lines(tmp_path)
    assert line["cwd"] == "C:\\Users\\Женя\\代码 \"quoted\"" and line["transcript_path"] == "/tmp/\u00e9\t\U0001F600.jsonl"


def test_built_in_modules_only_and_no_json():
    tree = ast.parse(SHIM.read_text(encoding="utf-8"))
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods <= {"os", "sys", "time", "_winapi"}      # all loaded by the interpreter itself
    # At run time, too: nothing beyond what "python -S -I" loads anyway, and never json.
    probe = textwrap.dedent("""
        import sys, runpy
        before = set(sys.modules)
        sys.argv = ["capture_shim.py"]
        runpy.run_path(%r, run_name="not_main")
        added = sorted(set(sys.modules) - before - {"runpy", "pkgutil", "importlib.machinery", "importlib.util"})
        sys.stdout.write(" ".join(added))
    """ % str(SHIM))
    # -B: -I ignores PYTHONDONTWRITEBYTECODE, and the import must not leave __pycache__ behind.
    out = subprocess.run([sys.executable, "-S", "-I", "-B", "-c", probe], capture_output=True, text=True, timeout=60)
    assert "json" not in out.stdout.split() and out.returncode == 0, out.stdout + out.stderr


def test_five_processes_append_a_thousand_lines_each_without_loss_or_interleaving(tmp_path):
    # Plain O_APPEND through the C runtime lost 30-39 of 1,000 events and broke lines on Windows.
    target = tmp_path / "shared.jsonl"
    worker = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        import capture_shim
        writer = int(sys.argv[1])
        for i in range(1000):
            line = b'{"writer":%%d,"seq":%%d,"pad":"%%s"}\\n' %% (writer, i, b"x" * 400)
            capture_shim.append(%r, line)
    """ % (str(HERE), str(target)))
    procs = [subprocess.Popen([sys.executable, "-S", "-I", "-B", "-c", worker, str(w)]) for w in range(5)]
    assert [p.wait(timeout=300) for p in procs] == [0] * 5
    raw = target.read_bytes().splitlines()
    parsed = [json.loads(line) for line in raw]            # a torn or interleaved line would not parse
    assert len(parsed) == 5000
    assert {(p["writer"], p["seq"]) for p in parsed} == {(w, i) for w in range(5) for i in range(1000)}
    assert all(p["pad"] == "x" * 400 for p in parsed)


def test_parallel_hook_runs_into_one_session_file_lose_nothing(tmp_path):
    # The real shim, 5 at a time, all writing to the same session's spool file.
    import threading
    errors = []

    def launcher(k):
        for i in range(20):
            proc = run_shim([tmp_path, "claude", "PostToolUse"], payload(tool_use_id=f"toolu_{k}_{i}"))
            if proc.returncode or proc.stdout or proc.stderr:
                errors.append(proc)

    threads = [threading.Thread(target=launcher, args=(k,)) for k in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    lines = spool_lines(tmp_path)
    assert sorted(line["tool_use_id"] for _, line in lines) == sorted(f"toolu_{k}_{i}" for k in range(5) for i in range(20))
