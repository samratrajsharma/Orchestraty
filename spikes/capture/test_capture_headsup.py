"""Tests for the pre-edit heads-up hook and its writer. Every path and text is synthetic."""
import builtins
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import capture_headsup as hu
import capture_headsup_writer as w
import capture_hooks

HERE = Path(__file__).resolve().parent
HOOK = HERE / "capture_headsup.py"


def run_hook(headsup_dir, payload):
    # -B: the hook imports capture_shim, and -I ignores PYTHONDONTWRITEBYTECODE.
    return subprocess.run([sys.executable, "-S", "-I", "-B", str(HOOK), str(headsup_dir)], input=payload,
                          capture_output=True, timeout=60)


def pre_tool_use(file_path, tool="Edit"):
    return json.dumps({"session_id": "s-1", "hook_event_name": "PreToolUse", "tool_name": tool,
                       "tool_input": {"file_path": file_path, "old_string": "a", "new_string": "b"}}).encode()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "orch-phase0"


def test_a_hit_returns_the_ready_made_bytes_unchanged(root):
    target = "/work/app/src/billing.py" if os.name != "nt" else "C:\\work\\app\\src\\billing.py"
    stored = Path(w.put(str(root), target, "billing.py: total() is called by 3 routes"))
    proc = run_hook(root / "headsup", pre_tool_use(target))
    assert proc.returncode == 0 and proc.stderr == b""
    assert proc.stdout == stored.read_bytes()
    out = json.loads(proc.stdout)
    assert out == {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "additionalContext": "billing.py: total() is called by 3 routes"}}


def test_a_miss_prints_nothing_and_writes_nothing(root, tmp_path):
    w.put(str(root), "/work/app/a.py", "context for a")
    before = sorted(p for p in tmp_path.rglob("*"))
    for payload in (pre_tool_use("/work/app/b.py"), b"", b"not json", b'{"tool_input": {}}', b'{"file_path": 5}'):
        proc = run_hook(root / "headsup", payload)
        assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""
    assert sorted(p for p in tmp_path.rglob("*")) == before


def test_windows_paths_escaped_in_json_find_their_heads_up(root):
    w.put(str(root), "C:\\Users\\dev\\app\\main.py", "ctx")
    proc = run_hook(root / "headsup", pre_tool_use("C:\\Users\\dev\\app\\main.py"))
    same_case = "C:/Users/dev/app/main.py"
    assert json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] == "ctx"
    assert hu.key_for("C:\\Users\\dev\\app\\main.py") == hu.key_for(same_case)
    if sys.platform in ("win32", "darwin"):                     # file systems that ignore case
        assert hu.key_for(same_case.upper()) == hu.key_for(same_case)


def test_different_paths_never_share_a_key():
    paths = ["/w/a b.py", "/w/a_b.py", "/w/a__b.py", "/w/a%b.py", "/w/a-b.py", "/w/\u00e9.py", "/w/e.py",
             "/w/" + "x" * 300 + "1.py", "/w/" + "x" * 300 + "2.py"]
    keys = [hu.key_for(p) for p in paths]
    assert len(set(keys)) == len(keys)
    for k in keys:
        assert len(k) <= 200 and all(c.isalnum() or c in "-._~" for c in k)


def test_json_strings_are_unescaped_like_json_does():
    for text in ('C:\\a\\b "q" /x', "tab\tnew\nline", "\u00e9\u4ee3\U0001F600", "\\u0041 literal"):
        token = json.dumps(text).encode()
        assert hu.json_unescape(token) == text
    assert hu.json_unescape(json.dumps("\U0001F600", ensure_ascii=True).encode()) == "\U0001F600"


def test_the_writer_replaces_atomically_reads_back_once_and_cuts_long_text(root, monkeypatch):
    opened = []
    real_open = builtins.open

    def spy(path, mode="r", *a, **k):
        opened.append((os.path.basename(str(path)), mode))
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    final = Path(w.put(str(root), "/w/big.py", "y" * 20000))
    name = final.name
    assert opened == [(name + ".tmp", "wb"), (name, "rb")], "write to a temporary name, then read back once"
    assert not (final.parent / (name + ".tmp")).exists()
    text = json.loads(final.read_bytes())["hookSpecificOutput"]["additionalContext"]
    assert len(text) == w.MAX_CONTEXT
    assert w.remove(str(root), "/w/big.py") is True and not final.exists()


def test_the_writer_writes_only_inside_the_phase0_headsup_folder(tmp_path):
    with pytest.raises(capture_hooks.Refused):
        w.put(str(tmp_path / "not-phase0"), "/w/a.py", "x")
    assert list(tmp_path.iterdir()) == []


def test_the_installed_headsup_hook_runs_end_to_end(root):
    settings = json.loads(Path(capture_hooks.install(str(root), sys.executable, headsup=True)).read_text(encoding="utf-8"))
    [entry] = settings["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Edit|Write|MultiEdit" and "async" not in entry["hooks"][0]
    hook = entry["hooks"][0]
    w.put(str(root), "/w/src/x.py", "x.py is imported by y.py")
    proc = subprocess.run([hook["command"], "-B"] + hook["args"], input=pre_tool_use("/w/src/x.py"),
                          capture_output=True, timeout=60)
    assert json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] == "x.py is imported by y.py"
    # Installing again, with or without the heads-up, never duplicates it.
    settings = json.loads(Path(capture_hooks.install(str(root), sys.executable, headsup=True)).read_text(encoding="utf-8"))
    assert len(settings["hooks"]["PreToolUse"]) == 1


def test_the_bench_prints_numbers_only(root):
    proc = subprocess.run([sys.executable, str(HERE / "capture_headsup_writer.py"), "bench", "--n", "3",
                           "--root", str(root)], capture_output=True, text=True, timeout=300,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0].startswith("case | n | median ms") and len(lines) == 4
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("|")]
        assert cells[1] == "3" and all(float(c) >= 0 for c in cells[2:])
    assert not list((root / "headsup").glob("*.json")), "the bench cleans up after itself"
