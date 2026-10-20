"""The hook script's side of delivery: slots taken and printed, the wake waiter, git's pre-push."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from helpers import run_hook, spool_lines
from orchestraty.capture import hook
from orchestraty.delivery import notes, slots
from orchestraty.paths import State

PATHS = [r"C:\Users\prave\Orchestraty\a.py", "/w/x_y/z.py", "C:\\Very" + "\\d" * 100 + "\\m.py",
         "/w/\u00e9t\u00e9 \u4ee3\u7801.py", "C:\\a\\.\\b\\..\\c.py", "/w/a b/(1)/[x]{y}.py", "/w/emoji\U0001F600.py"]


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.mark.parametrize("path", PATHS)
def test_the_hook_and_the_coordinator_agree_on_slot_keys(path):
    assert hook.file_key(path) == slots.file_key(path)


@pytest.mark.parametrize("text", ["plain", 'q"uote', "back\\slash", "new\nline\ttab", "\u00e9\u4ee3\U0001F600",
                                  "\x01\x1f", "/a/b"])
def test_json_helpers_round_trip(text):
    assert json.loads(hook.json_string(text)) == text
    assert hook.json_unescape(json.dumps(text).encode("utf-8")) == text
    assert hook.json_unescape(json.dumps(text, ensure_ascii=False).encode("utf-8")) == text


def edit_payload(session, path, event="PreToolUse"):
    return json.dumps({"session_id": session, "transcript_path": "/t.jsonl", "cwd": os.path.dirname(path),
                       "hook_event_name": event, "tool_name": "Edit",
                       "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"},
                       "tool_use_id": "toolu_1"}).encode("utf-8")


def test_pre_tool_use_prints_the_note_for_that_file_once(state, hook_script, tmp_path):
    target = str(tmp_path / "w" / "billing" / "annual.py")
    slots.write(slots.edit_slot(state, "S1", target), notes.pre_tool_output("watch out"))
    proc = run_hook(hook_script, ["claude", "PreToolUse"], edit_payload("S1", target))
    assert proc.returncode == 0 and proc.stderr == b""
    assert json.loads(proc.stdout) == {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                              "additionalContext": "watch out"}}
    [(_, line)] = spool_lines(state.spool)
    assert line["took"] == "edit" and line["file_path"] == target
    again = run_hook(hook_script, ["claude", "PreToolUse"], edit_payload("S1", target))
    assert again.stdout == b""                                          # once only
    other = str(tmp_path / "w" / "other.py")
    slots.write(slots.edit_slot(state, "S1", target), b"{}")
    assert run_hook(hook_script, ["claude", "PreToolUse"], edit_payload("S1", other)).stdout == b""
    assert run_hook(hook_script, ["claude", "PreToolUse"], edit_payload("S2", target)).stdout == b""


def test_the_same_path_written_another_way_finds_its_slot(state, hook_script, tmp_path):
    target = str(tmp_path / "w" / "pkg" / "m.py")
    slots.write(slots.edit_slot(state, "S1", target), b'{"x": 1}')
    variant = str(tmp_path / "w" / "pkg" / "." / "m.py")
    if os.name == "nt":
        variant = variant.upper().replace("\\", "/")
    assert run_hook(hook_script, ["claude", "PreToolUse"], edit_payload("S1", variant)).stdout == b'{"x": 1}'


def test_post_tool_use_and_stop_take_their_slots(state, hook_script):
    slots.write(slots.next_slot(state, "S1"), notes.post_tool_output("next"))
    slots.write(slots.stop_slot(state, "S1"), notes.stop_output("finish the job"))
    post = json.dumps({"session_id": "S1", "hook_event_name": "PostToolUse", "tool_name": "Read"}).encode()
    proc = run_hook(hook_script, ["claude", "PostToolUse"], post)
    assert json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] == "next"
    active = json.dumps({"session_id": "S1", "hook_event_name": "Stop", "stop_hook_active": True}).encode()
    assert run_hook(hook_script, ["claude", "Stop"], active).stdout == b""      # a stop hook already ran
    assert slots.waiting(slots.stop_slot(state, "S1"))
    stop = json.dumps({"session_id": "S1", "hook_event_name": "Stop", "stop_hook_active": False}).encode()
    proc = run_hook(hook_script, ["claude", "Stop"], stop)
    assert json.loads(proc.stdout) == {"hookSpecificOutput": {"hookEventName": "Stop",
                                                              "additionalContext": "finish the job"}}
    took = [line.get("took") for _, line in spool_lines(state.spool)]
    assert took == ["next", None, "stop"]


def test_a_session_id_cannot_reach_another_folder(state, hook_script, tmp_path):
    slots.write(os.path.join(state.root, "notes", "evil", "next.json"), b"stolen")
    post = json.dumps({"session_id": "../ev/il", "hook_event_name": "PostToolUse"}).encode()
    assert run_hook(hook_script, ["claude", "PostToolUse"], post).stdout == b"stolen"    # "evil", cleaned
    nosession = json.dumps({"hook_event_name": "PostToolUse"}).encode()
    slots.write(os.path.join(state.root, "notes", "nosession", "next.json"), b"x")
    assert run_hook(hook_script, ["claude", "PostToolUse"], nosession).stdout == b""


def test_parallel_hooks_deliver_a_note_once(state, hook_script):
    for round_ in range(5):
        sid = f"P{round_}"
        slots.write(slots.next_slot(state, sid), b"the note")
        post = json.dumps({"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Read"}).encode()
        outs, lock = [], threading.Lock()

        def one():
            out = run_hook(hook_script, ["claude", "PostToolUse"], post).stdout
            with lock:
                outs.append(out)

        threads = [threading.Thread(target=one) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(outs) == [b""] * 5 + [b"the note"], round_


# --------------------------------------------------------------------------------------- #
# Wake
# --------------------------------------------------------------------------------------- #
def start_waiter(hook_script, session):
    payload = json.dumps({"session_id": session, "hook_event_name": "Stop", "stop_hook_active": False}).encode()
    proc = subprocess.Popen([sys.executable, "-S", "-I", str(hook_script), "claude", "Wake"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.stdin.write(payload)
    proc.stdin.close()
    return proc


def test_the_waiter_wakes_its_session_with_the_note(state, hook_script):
    proc = start_waiter(hook_script, "W1")
    try:
        time.sleep(0.5)
        slots.write(slots.wake_slot(state, "W1"), b"another session changed charge(); you call it")
        assert proc.wait(timeout=30) == 2
        assert proc.stderr.read() == b"another session changed charge(); you call it"
        assert not slots.waiting(slots.wake_slot(state, "W1"))
        [(_, line)] = spool_lines(state.spool)
        assert line["event"] == "Wake" and line["took"] == "wake" and line["session_id"] == "W1"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_the_waiter_leaves_quietly_when_the_session_is_back(state, hook_script):
    spool = Path(state.spool) / "claude" / "W2.jsonl"
    spool.parent.mkdir(parents=True)
    spool.write_bytes(b'{"event":"Stop"}\n')
    proc = start_waiter(hook_script, "W2")
    try:
        time.sleep(hook.WAKE_GRACE + 0.6)
        with open(spool, "ab") as fh:
            fh.write(b'{"event":"UserPromptSubmit"}\n')        # the person typed again
        assert proc.wait(timeout=30) == 0
        assert proc.stderr.read() == b"" and proc.stdout.read() == b""
    finally:
        if proc.poll() is None:
            proc.kill()


# --------------------------------------------------------------------------------------- #
# git's pre-push
# --------------------------------------------------------------------------------------- #
def test_pre_push_prints_the_line_for_its_worktree(state, hook_script, tmp_path):
    work = tmp_path / "wt-a"
    work.mkdir()
    push = Path(state.root) / "push"
    push.mkdir(parents=True)
    (push / (slots.file_key(str(work)) + ".txt")).write_text("orchestraty: 1 caller(s) of charge ...\n",
                                                             encoding="utf-8")
    proc = run_hook(hook_script, ["git", "pre-push", "origin", "https://x"], b"refs/heads/a 1 refs/heads/a 2\n",
                    cwd=work)
    assert proc.returncode == 0 and proc.stdout == b""
    assert proc.stderr.decode().replace("\r\n", "\n") == "orchestraty: 1 caller(s) of charge ...\n"
    elsewhere = tmp_path / "wt-b"
    elsewhere.mkdir()
    quiet = run_hook(hook_script, ["git", "pre-push", "origin"], b"", cwd=elsewhere)
    assert quiet.returncode == 0 and quiet.stderr == b""
    lines = [line for _, line in spool_lines(state.spool)]
    assert [line["event"] for line in lines] == ["pre-push", "pre-push"]
    assert os.path.normcase(lines[0]["cwd"]) == os.path.normcase(str(work))
    assert "https" not in json.dumps(lines)                         # a remote's URL is never kept
