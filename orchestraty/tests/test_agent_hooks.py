"""Codex's and Antigravity's events through the real hook script: what the spool line keeps,
what the hook prints back, and the notes it hands over. Every payload is synthetic, in the
shape each agent sends (Codex's keys in the order it writes them)."""
import json
import os
import statistics
import subprocess
import sys
import time

import pytest

from helpers import SECRET, ag, ag_tool, codex, patch_event, run_hook, spool_lines
from orchestraty.adapters import antigravity as ag_adapter
from orchestraty.adapters import codex as codex_adapter
from orchestraty.capture import hook
from orchestraty.delivery import slots

ALLOW = b'{"decision":"allow"}'


PATCH = "\n".join([
    "*** Begin Patch",
    "*** Update File: pkg/a.py",
    "@@ def charge",
    "-    return amount",
    "+    return amount * 2",
    "*** Add File: pkg/new file.py",
    '+x = "*** Update File: decoy.py"',            # a line of content, not a header
    '+s = "\\n*** Update File: decoy2.py"',        # a backslash and an n in the content
    "*** Delete File: old.py",
    "*** Update File: b.py",
    "*** Move to: c.py",
    "@@",
    "-y = 0",
    "+y = " + json.dumps(SECRET),
    "*** End Patch",
])


@pytest.fixture
def spool(state_root):
    return state_root / "spool"


def one_line(spool):
    [(name, line)] = spool_lines(spool)
    return name, line


# --------------------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------------------- #
def test_codex_lines_keep_claude_codes_names_and_the_patchs_files(hook_script, spool, tmp_path):
    wt = tmp_path / "wt"
    proc = run_hook(hook_script, ["codex", "PreToolUse"], patch_event("PreToolUse", "019a-codex-1", wt, PATCH))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    name, line = one_line(spool)
    assert name == "019a-codex-1.jsonl"
    assert (line["agent"], line["event"], line["session_id"], line["cwd"]) == ("codex", "PreToolUse", "019a-codex-1",
                                                                              str(wt))
    assert (line["tool_name"], line["tool_use_id"], line["hook_event_name"]) == ("apply_patch", "call_1", "PreToolUse")
    names = ["pkg/a.py", "pkg/new file.py", "old.py", "b.py", "c.py"]
    assert line["file_path"] == os.path.join(str(wt), names[0])
    assert line["other_files"] == [os.path.join(str(wt), n) for n in names[1:]]
    assert SECRET not in json.dumps(line) and "decoy" not in json.dumps(line) and "transcript_path" not in line


def test_codex_session_and_stop_fields_and_a_shell_command(hook_script, spool, tmp_path):
    wt = tmp_path / "wt"
    run_hook(hook_script, ["codex", "SessionStart"], codex("SessionStart", "s1", wt, source="startup"))
    run_hook(hook_script, ["codex", "UserPromptSubmit"], codex("UserPromptSubmit", "s1", wt, prompt=SECRET))
    run_hook(hook_script, ["codex", "PostToolUse"], codex("PostToolUse", "s1", wt, tool_name="Bash",
                                                          tool_input={"command": "sed -i s/a/b/ a.py"},
                                                          tool_response={"output": SECRET}, tool_use_id="call_2"))
    run_hook(hook_script, ["codex", "Stop"], codex("Stop", "s1", wt, stop_hook_active=False,
                                                   last_assistant_message=SECRET))
    run_hook(hook_script, ["codex", "SessionEnd"], codex("SessionEnd", "s1", wt, reason="other"))
    lines = [line for _, line in spool_lines(spool)]
    assert [line["event"] for line in lines] == ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop",
                                                 "SessionEnd"]
    assert lines[0]["source"] == "startup" and lines[4]["reason"] == "other" and lines[3]["stop_hook_active"] is False
    assert lines[2]["tool_name"] == "Bash" and "file_path" not in lines[2]
    assert all(line["cwd"] == str(wt) for line in lines) and SECRET not in json.dumps(lines)


def test_codex_takes_its_notes_in_its_own_form(hook_script, spool, state_root, tmp_path):
    wt = tmp_path / "wt"
    folder = state_root / "notes" / "s1"
    second = os.path.join(str(wt), "pkg/new file.py")              # joined as the hook joins it
    edit = codex_adapter.output("edit", "Orchestraty: before you edit new file.py")
    (folder / "edit").mkdir(parents=True)
    (folder / "edit" / (slots.file_key(second) + ".json")).write_bytes(edit)
    proc = run_hook(hook_script, ["codex", "PreToolUse"], patch_event("PreToolUse", "s1", wt, PATCH))
    assert proc.stdout == edit                                    # the slot of the first file that has one
    _, line = one_line(spool)
    assert line["took"] == "edit" and line["file_path"] == second            # the file whose note was taken
    assert os.path.join(str(wt), "pkg/a.py") in line["other_files"]
    (folder / "next.json").write_bytes(codex_adapter.output("next", "with the result"))
    (folder / "stop.json").write_bytes(codex_adapter.output("stop", "callers left"))
    post = run_hook(hook_script, ["codex", "PostToolUse"], patch_event("PostToolUse", "s1", wt, PATCH))
    assert json.loads(post.stdout)["hookSpecificOutput"]["additionalContext"] == "with the result"
    kept = run_hook(hook_script, ["codex", "Stop"], codex("Stop", "s1", wt, stop_hook_active=True))
    assert kept.stdout == b"" and (folder / "stop.json").exists()             # a stop hook already kept it going
    stop = run_hook(hook_script, ["codex", "Stop"], codex("Stop", "s1", wt, stop_hook_active=False))
    assert json.loads(stop.stdout) == {"decision": "block", "reason": "callers left"}
    assert [line.get("took") for _, line in spool_lines(spool)] == ["edit", "next", None, "stop"]


def test_two_codex_sessions_in_one_shared_process_each_land_on_their_own(hook_script, spool, tmp_path):
    # Codex's shared app-server runs every session's hooks with the environment and the folder
    # of the session that started it: here A's, for both. Each event must still land on its own
    # session, by its payload.
    wt_a, wt_b = tmp_path / "wt-a", tmp_path / "wt-b"
    wt_a.mkdir()
    env = dict(os.environ, PWD=str(wt_a), CODEX_THREAD_ID="session-a", ORCH_NO_COORDINATOR="1")
    for event, session, cwd in [("SessionStart", "session-a", wt_a), ("SessionStart", "session-b", wt_b),
                                ("PreToolUse", "session-b", wt_b), ("PreToolUse", "session-a", wt_a),
                                ("PostToolUse", "session-b", wt_b), ("Stop", "session-a", wt_a)]:
        if event in ("PreToolUse", "PostToolUse"):
            raw = patch_event(event, session, cwd, "*** Begin Patch\n*** Update File: x.py\n*** End Patch")
        else:
            raw = codex(event, session, cwd, **({"source": "startup"} if event == "SessionStart" else {}))
        assert run_hook(hook_script, ["codex", event], raw, cwd=str(wt_a), env=env).returncode == 0
    by_file = {}
    for name, line in spool_lines(spool):
        by_file.setdefault(name, []).append(line)
    assert sorted(by_file) == ["session-a.jsonl", "session-b.jsonl"]
    assert [line["event"] for line in by_file["session-b.jsonl"]] == ["SessionStart", "PreToolUse", "PostToolUse"]
    assert all(line["cwd"] == str(wt_b) and line.get("file_path", os.path.join(str(wt_b), "x.py")) ==
               os.path.join(str(wt_b), "x.py") for line in by_file["session-b.jsonl"])
    assert all(line["cwd"] == str(wt_a) for line in by_file["session-a.jsonl"])


# --------------------------------------------------------------------------------------- #
# Antigravity
# --------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,args,path", [
    ("write_to_file", {"TargetFile": "/w/pkg/a.py", "CodeContent": SECRET, "EmptyFile": False}, "/w/pkg/a.py"),
    ("replace_file_content", {"TargetFile": "file:///w/pkg/my%20file.py", "ReplacementChunks": [
        {"TargetContent": SECRET, "ReplacementContent": SECRET}]}, "/w/pkg/my file.py"),
    ("multi_replace_file_content", {"Instruction": SECRET, "TargetFile": "/w/b.py"}, "/w/b.py"),
    ("write_file", {"file_path": "/w/c.py", "content": SECRET}, "/w/c.py"),
    ("edit_file", {"path": "/w/d.py", "old": SECRET, "new": SECRET}, "/w/d.py"),
])
@pytest.mark.parametrize("args_first", [False, True])
def test_both_sets_of_antigravity_edit_tools_give_the_file(hook_script, spool, name, args, path, args_first):
    raw = ag_tool("PostToolUse", "c0ffee-1", "/w", name, args, args_first)
    proc = run_hook(hook_script, ["antigravity", "PostToolUse"], raw)
    assert (proc.returncode, proc.stdout) == (0, b"{}")
    fname, line = one_line(spool)
    assert fname == "c0ffee-1.jsonl"
    assert (line["session_id"], line["cwd"], line["tool_name"], line["file_path"]) == ("c0ffee-1", "/w", name, path)
    assert line["transcript_path"] == "/t/brain/transcript.jsonl" and SECRET not in json.dumps(line)


def test_a_tool_that_edits_nothing_gives_no_file(hook_script, spool):
    raw = ag_tool("PostToolUse", "c1", "/w", "run_command", {"CommandLine": 'echo "TargetFile"', "Cwd": "/w"})
    run_hook(hook_script, ["antigravity", "PostToolUse"], raw)
    _, line = one_line(spool)
    assert line["tool_name"] == "run_command" and "file_path" not in line


def test_a_windows_file_uri_is_made_a_path():
    assert hook.uri_path("file:///C:/Users/me/a%2Bb.py") == "C:/Users/me/a+b.py"
    assert hook.uri_path("file:///home/me/x.py") == "/home/me/x.py" and hook.uri_path("/plain/p.py") == "/plain/p.py"
    assert hook.uri_path("file:///w/bad%zz.py") == "/w/bad%zz.py"


@pytest.mark.parametrize("event,expected", [
    ("SessionStart", b"{}"), ("PreInvocation", b"{}"), ("PreToolUse", ALLOW), ("PostToolUse", b"{}"),
    ("PostInvocation", b"{}"), ("Stop", b"{}")])
def test_each_antigravity_event_gets_the_answer_it_waits_for(hook_script, spool, event, expected):
    fields = {"PreInvocation": {"invocationNum": 1, "initialNumSteps": 0},
              "Stop": {"executionNum": 1, "terminationReason": "model_stop", "fullyIdle": True}}.get(event, {})
    if event in ("PreToolUse", "PostToolUse"):
        raw = ag_tool(event, "c2", "/w", "write_to_file", {"TargetFile": "/w/a.py", "CodeContent": SECRET})
    else:
        raw = ag(event, "c2", ["/w"], **fields)
    proc = run_hook(hook_script, ["antigravity", event], raw)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, expected, b"")
    _, line = one_line(spool)
    assert (line["event"], line["session_id"], line["cwd"]) == (event, "c2", "/w")


def test_antigravity_takes_its_notes_before_its_next_step_and_at_stop(hook_script, spool, state_root):
    folder = state_root / "notes" / "c3"
    folder.mkdir(parents=True)
    nxt, stop = ag_adapter.output("next", "Orchestraty: a.py changed"), ag_adapter.output("stop", "callers left")
    (folder / "next.json").write_bytes(nxt)
    (folder / "stop.json").write_bytes(stop)
    (folder / "edit").mkdir()
    edit_slot = folder / "edit" / (slots.file_key("/w/a.py") + ".json")
    edit_slot.write_bytes(b"never taken")
    tool = ag_tool("PreToolUse", "c3", "/w", "write_to_file", {"TargetFile": "/w/a.py", "CodeContent": "x"})
    assert run_hook(hook_script, ["antigravity", "PreToolUse"], tool).stdout == ALLOW
    assert edit_slot.exists()                                     # its PreToolUse cannot pass text on
    first = run_hook(hook_script, ["antigravity", "PreInvocation"], ag("PreInvocation", "c3", ["/w"], invocationNum=2))
    assert first.stdout == nxt
    assert json.loads(first.stdout)["injectSteps"][0]["ephemeralMessage"] == "Orchestraty: a.py changed"
    again = run_hook(hook_script, ["antigravity", "PreInvocation"], ag("PreInvocation", "c3", ["/w"], invocationNum=3))
    assert again.stdout == b"{}"                                  # taken exactly once
    end = run_hook(hook_script, ["antigravity", "Stop"], ag("Stop", "c3", ["/w"], fullyIdle=True))
    assert json.loads(end.stdout) == {"decision": "continue", "reason": "callers left"}
    assert [line.get("took") for _, line in spool_lines(spool)] == [None, "next", None, "stop"]


def test_antigravity_gets_its_answer_even_when_the_hook_cannot_write(hook_script, state_root, tmp_path):
    (state_root / "spool").write_text("not a folder", encoding="utf-8")       # every append fails
    raw = ag_tool("PreToolUse", "c4", "/w", "write_to_file", {"TargetFile": "/w/a.py"})
    proc = run_hook(hook_script, ["antigravity", "PreToolUse"], raw)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, ALLOW, b"")
    misplaced = tmp_path / "elsewhere" / "hook.py"                            # outside a hooks folder: writes nothing
    misplaced.parent.mkdir()
    misplaced.write_bytes(hook_script.read_bytes())
    assert run_hook(misplaced, ["antigravity", "PreToolUse"], raw).stdout == ALLOW
    assert run_hook(misplaced, ["codex", "PreToolUse"], raw).stdout == b""


# --------------------------------------------------------------------------------------- #
# The hook stays fast for every agent
# --------------------------------------------------------------------------------------- #
OVERHEAD_RUNS = 20


def _medians_ms(commands, data, runs=OVERHEAD_RUNS, warmup=3):
    """Each command's median time in ms. The commands take turns, run by run, so the load on a
    shared CI machine falls on all of them alike, not on whichever was measured later."""
    times = [[] for _ in commands]
    for i in range(warmup + runs):
        for argv, out in zip(commands, times):
            started = time.perf_counter()
            proc = subprocess.run(argv, input=data, capture_output=True, timeout=60)
            took = (time.perf_counter() - started) * 1000
            assert proc.returncode == 0
            if i >= warmup:
                out.append(took)
    return [statistics.median(t) for t in times]


@pytest.mark.parametrize("agent,event,raw", [
    ("claude", "PostToolUse", json.dumps({"session_id": "t1", "transcript_path": "/t/x.jsonl", "cwd": "/w",
                                          "hook_event_name": "PostToolUse", "tool_name": "Edit",
                                          "tool_input": {"file_path": "/w/a.py", "old_string": "x" * 900,
                                                         "new_string": "y" * 900},
                                          "tool_response": {"filePath": "/w/a.py"},
                                          "tool_use_id": "toolu_1"}).encode()),
    ("codex", "PostToolUse", patch_event("PostToolUse", "t2", "/w", PATCH + "\n" + "+" + "z" * 1500)),
    ("antigravity", "PostToolUse", ag_tool("PostToolUse", "t3", "/w", "replace_file_content",
                                           {"TargetFile": "file:///w/a.py", "ReplacementChunks": "q" * 1800})),
])
def test_the_hooks_overhead_for_each_agent(hook_script, agent, event, raw):
    # The way the latency harness measures it: the hook's time over a Python that does
    # nothing, both started the same way. The hook adds a few milliseconds to the
    # interpreter's start; the bound only catches gross slowness on a shared CI machine.
    baseline, took = _medians_ms([[sys.executable, "-S", "-I", "-c", "pass"],
                                  [sys.executable, "-S", "-I", str(hook_script), agent, event]], raw)
    added = took - baseline
    assert added < max(20.0, baseline), f"{agent}: the hook took {took:.1f} ms, a no-op {baseline:.1f} ms"


# --------------------------------------------------------------------------------------- #
# Only the tool's own name and its own file field: never an argument or the tool's output
# --------------------------------------------------------------------------------------- #
LEAK = "LEAK-OUTPUT-3c9b"


@pytest.mark.parametrize("args_first", [False, True])
def test_the_antigravity_tool_is_toolcalls_own_name_never_an_arguments(hook_script, spool, args_first):
    run_hook(hook_script, ["antigravity", "PostToolUse"],
             ag_tool("PostToolUse", "c5", "/w", "create_memory", {"name": LEAK, "body": SECRET}, args_first))
    args = {"meta": {"name": LEAK, "TargetFile": "/w/" + LEAK}, "TargetFile": "/w/a.py", "CodeContent": SECRET}
    run_hook(hook_script, ["antigravity", "PostToolUse"], ag_tool("PostToolUse", "c5", "/w", "write_to_file", args,
                                                                  args_first))
    lines = [line for _, line in spool_lines(spool)]
    assert [line["tool_name"] for line in lines] == ["create_memory", "write_to_file"]
    assert "file_path" not in lines[0] and lines[1]["file_path"] == "/w/a.py"
    assert LEAK.encode() not in (spool / "antigravity" / "c5.jsonl").read_bytes()


def test_antigravity_reads_its_own_top_level_names_whatever_their_order(hook_script, spool):
    inner = {"conversationId": LEAK, "workspacePaths": [LEAK], "transcriptPath": LEAK, "TargetFile": LEAK}
    body = {"toolCall": {"name": "write_to_file", "args": {"CodeContent": SECRET, "x": inner}}, "extra": inner,
            "conversationId": "c6", "workspacePaths": ["/w"], "transcriptPath": "/t/brain/transcript.jsonl"}
    run_hook(hook_script, ["antigravity", "PostToolUse"], json.dumps(body).encode())
    name, line = one_line(spool)
    assert (name, line["session_id"], line["cwd"], line["transcript_path"]) == ("c6.jsonl", "c6", "/w",
                                                                                "/t/brain/transcript.jsonl")
    assert line["tool_name"] == "write_to_file" and "file_path" not in line and LEAK not in json.dumps(line)


@pytest.mark.parametrize("response", [
    {"output": "Failed:\n*** Update File: " + LEAK + " is what you sent\n*** Add File: " + LEAK + "2"},
    "error\n*** Delete File: " + LEAK + "\n",
], ids=["object", "string"])
def test_a_patchs_files_come_from_its_own_command_never_the_tool_output(hook_script, spool, tmp_path, response):
    wt = tmp_path / "wt"
    raw = codex("PostToolUse", "s5", wt, tool_name="apply_patch",
                tool_input={"command": PATCH, "justification": "\n*** Add File: " + LEAK},
                tool_response=response, tool_use_id="call_1")
    run_hook(hook_script, ["codex", "PostToolUse"], raw)
    _, line = one_line(spool)
    names = ["pkg/a.py", "pkg/new file.py", "old.py", "b.py", "c.py"]
    assert [line["file_path"]] + line["other_files"] == [os.path.join(str(wt), n) for n in names]
    assert LEAK.encode() not in (spool / "codex" / "s5.jsonl").read_bytes()


def test_a_patch_names_no_file_from_outside_its_command_before_the_edit(hook_script, spool, state_root, tmp_path):
    wt = tmp_path / "wt"
    folder = state_root / "notes" / "s6" / "edit"
    folder.mkdir(parents=True)
    bogus = os.path.join(str(wt), LEAK)
    (folder / (slots.file_key(bogus) + ".json")).write_bytes(b"never taken")
    raw = codex("PreToolUse", "s6", wt, tool_name="apply_patch",
                tool_input={"command": "*** Begin Patch\n*** Update File: x.py\n*** End Patch",
                            "justification": "\n*** Update File: " + LEAK}, tool_use_id="call_1")
    proc = run_hook(hook_script, ["codex", "PreToolUse"], raw)
    assert proc.stdout == b"" and (folder / (slots.file_key(bogus) + ".json")).exists()
    _, line = one_line(spool)
    assert line["file_path"] == os.path.join(str(wt), "x.py") and "other_files" not in line


def test_a_patch_given_as_the_whole_tool_input_names_its_files(hook_script, spool, tmp_path):
    wt = tmp_path / "wt"
    raw = codex("PostToolUse", "s7", wt, tool_name="apply_patch", tool_input=PATCH,
                tool_response={"output": "*** Add File: " + LEAK}, tool_use_id="call_1")
    run_hook(hook_script, ["codex", "PostToolUse"], raw)
    _, line = one_line(spool)
    names = ["pkg/a.py", "pkg/new file.py", "old.py", "b.py", "c.py"]
    assert [line["file_path"]] + line["other_files"] == [os.path.join(str(wt), n) for n in names]
    assert LEAK.encode() not in (spool / "codex" / "s7.jsonl").read_bytes()
