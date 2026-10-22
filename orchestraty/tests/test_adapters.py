"""The adapters and the hook script's own copy of them agree, and each adapter hands notes back
in the exact output its agent accepts."""
import json

import pytest

from orchestraty import adapters
from orchestraty.adapters import antigravity, claude, codex
from orchestraty.capture import hook

COMMON = {"SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
          "Stop", "SubagentStop", "StopFailure"}


def _b(table):
    return {k.encode(): v.encode() for k, v in table.items()}


@pytest.mark.parametrize("adapter", [claude, codex, antigravity])
def test_the_hook_takes_the_slots_and_gives_the_answers_each_adapter_declares(adapter):
    assert hook.SLOTS[adapter.NAME.encode()] == _b(adapter.SLOTS)
    if adapter.QUIET is None:
        assert adapter.NAME.encode() not in hook.QUIET
    else:
        default, answers = hook.QUIET[adapter.NAME.encode()]
        assert (default.decode(), {k.decode(): v.decode() for k, v in answers.items()}) == adapter.QUIET
        for answer in [adapter.QUIET[0], *adapter.QUIET[1].values()]:
            json.loads(answer)                                      # each answer is JSON the agent can read
    assert set(adapter.SLOTS.values()) <= set(adapter.HEARS)
    assert set(adapter.CAPTURED) <= set(adapter.EVENTS)


def test_the_hook_reads_the_names_each_adapter_declares():
    assert hook.EDIT_TOOLS == {b'"%s"' % k.encode(): v[0].encode() for k, v in claude.EDIT_TOOLS.items()}
    assert hook.AG_EDIT_TOOLS == {b'"%s"' % k.encode(): tuple(x.encode() for x in v)
                                  for k, v in antigravity.EDIT_TOOLS.items()}
    assert hook.PATCH_TOOLS == tuple(b'"%s"' % t.encode() for t in codex.PATCH_TOOLS)
    assert hook.PATCH_HEADERS == tuple(h.encode() for h in codex.PATCH_HEADERS)
    assert (hook.AG_SESSION, hook.AG_TRANSCRIPT) == (b"conversationId", b"transcriptPath")
    assert antigravity.READS["session_id"] == "conversationId" and antigravity.READS["cwd"] == "workspacePaths[0]"
    line_fields = ("session_id", "hook_event_name", "tool_name", "tool_use_id", "cwd", "transcript_path")
    assert {k.encode() for k in claude.READS if k in line_fields} == set(hook.FIELDS)
    assert codex.READS["session_id"] == "session_id" and codex.READS["cwd"] == "cwd"


def test_events_map_to_the_common_ones_where_there_is_one():
    assert set(claude.EVENTS) <= COMMON
    for adapter in (codex, antigravity):
        for own, common in adapter.EVENTS.items():
            assert common == own                           # same name, same meaning; PreInvocation is its own
            assert own in COMMON or adapter is antigravity
    assert set(codex.EVENTS) == {"SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
                                 "SubagentStop"}
    assert "PreToolUse" not in antigravity.CAPTURED and "PostToolUse" in antigravity.CAPTURED


def test_each_agent_gets_notes_in_its_own_form():
    t = "Orchestraty: a note"
    assert json.loads(codex.output("edit", t)) == {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                                          "additionalContext": t}}
    assert json.loads(codex.output("edit", t, steer=True)) == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": t}}
    assert json.loads(codex.output("next", t)) == {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                                          "additionalContext": t}}
    assert json.loads(codex.output("stop", t)) == {"decision": "block", "reason": t}
    assert json.loads(antigravity.output("next", t)) == {"injectSteps": [{"ephemeralMessage": t}]}
    assert json.loads(antigravity.output("stop", t)) == {"decision": "continue", "reason": t}
    assert claude.output("stop", t) == claude.stop_output(t) and claude.output("wake", t) == t.encode()
    assert adapters.output("helper-mybot", "next", t) == claude.post_tool_output(t)   # no adapter: Claude Code's form


def test_the_registry():
    assert adapters.hears("claude") == ("edit", "next", "stop", "wake")
    assert adapters.hears("codex") == ("edit", "next", "stop")
    assert adapters.hears("antigravity") == ("next", "stop")
    assert adapters.hears("helper-mybot") == claude.HEARS
    assert (adapters.title("antigravity"), adapters.title("codex"), adapters.title("helper-x")) == (
        "Antigravity", "Codex", "helper-x")
    assert "view_file" in adapters.READ_ONLY_TOOLS and "write_to_file" not in adapters.READ_ONLY_TOOLS
    assert codex.CANNOT and antigravity.CANNOT and not claude.CANNOT
