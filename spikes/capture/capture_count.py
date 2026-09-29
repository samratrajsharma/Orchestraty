"""Loss and coverage counter: compares the capture spool with the agents' own session records.

Status: throwaway (Phase 0). Not part of the product.

    python capture_count.py --spool <root>/spool --manifest <run manifest.json>
                            [--claude-projects <isolated profile>/projects] [--codex-sessions <CODEX_HOME>/sessions]
                            [--timeout-minutes 30] [--now <unix seconds>] [--json <out file>]

What it reads. From the spool, only its own lines. From each session record (a Claude Code
transcript, or a Codex rollout file) only ids, entry types, tool names, timestamps, error flags
and whether a tool result is a permission denial, which is recognised by a fixed marker.
No prompt, tool input, tool output or file content is ever copied into its output.

The manifest is written by whoever runs the sessions. It lists every session of the run:
    {"sessions": [{"agent": "claude", "session_id": "...", "kind": "scripted|parallel|interactive|
      cockpit|killed|noop", "env": "win|wsl", "async": false}, ...]}
Sessions of kind "noop" ran with no-op hooks and are left out of loss and coverage by design.

Expected counts (T1 section 6.3), for sessions that end normally:
    SessionStart                 1, plus 1 per compaction (it fires again with source "compact")
    SessionEnd                   exactly 1
    UserPromptSubmit             1 per user prompt (not interrupt markers, compact summaries or
                                 slash-command echoes, which are user entries but no prompt)
    PreToolUse                   1 per Edit, Write or MultiEdit tool call
    PostToolUse(+Failure)        1 per tool call, less permission-denied calls and calls rejected
                                 at input validation (no hook runs for those)
    Stop                         1 per turn that ends normally: not for a turn the user
                                 interrupted, nor for one that ended in an API error
    StopFailure                  1 per turn that ended in an API error
    SubagentStop                 1 per subagent run (Task or Agent call, not denied)
For killed sessions the same holds up to the kill: a call or subagent run with no result
expects no Post or SubagentStop event, and a missing SessionEnd, and a missing Stop for a turn
that was still running at the kill, are not lost but reported in their own row, together with
whether the session was closed by a SessionEnd, by the timeout rule (no event for
--timeout-minutes before --now), is still open, or was never captured. A killed session whose
last turn had already ended still expects that turn's Stop. A permission-denied call is counted
only when the record shows the denial, never inferred from a missing event.

It prints numbers only.
"""
import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict

EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop",
          "StopFailure"]
EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}
SUBAGENT_TOOLS = {"Task", "Agent", "spawn_agent"}
# How a permission denial reads in a tool result (Claude Code) or a call's output (Codex).
DENIAL_MARKERS = ("permission to use", "haven't granted it yet", "requested permissions to use",
                  "doesn't want to proceed with this tool use", "was rejected by the user", "denied by the user",
                  "rejected by user")
# How Codex words a rejected call at the start of its output (checked only when the call failed).
CODEX_DENIAL_MARKERS = ("rejected by user", "was rejected by the user", "denied by the user",
                        "permission denied by the user")


def _ts(value):
    """Unix seconds from an ISO 8601 timestamp, or None."""
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _is_denial(content):
    texts = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        texts += [c.get("text", "") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str)]
    low = " ".join(texts).lower()
    return any(marker in low for marker in DENIAL_MARKERS)


def read_jsonl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if raw:
                try:
                    yield json.loads(raw)
                except ValueError:
                    continue


class Record:
    """What one session's record says should have been captured."""

    def __init__(self, agent, session_id):
        self.agent, self.session_id = agent, session_id
        self.prompts = 0
        self.interrupted = 0        # turns ended by the user (Esc or Ctrl+C): no Stop fires
        self.api_errors = 0         # turns ended by an API error: StopFailure fires, not Stop
        self.turn_open = False      # a turn was still running when the record ends
        self.tool_ids = {}          # tool call id -> tool name
        self.results = set()        # tool call ids with a result
        self.denied = set()
        self.invalid = set()        # calls rejected at input validation: no hook runs for them
        self.compactions = 0        # compact summaries: each one fires SessionStart (source compact)
        self.first = self.last = None

    def seen(self, ts):
        if ts is not None:
            self.first = ts if self.first is None else min(self.first, ts)
            self.last = ts if self.last is None else max(self.last, ts)


def _texts(content):
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [c.get("text", "") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str)]
    return []


def _not_a_prompt(entry, content):
    """User entries that carry no user prompt: interrupt markers, compact summaries, transcript-only
    entries and local slash-command echoes. No UserPromptSubmit fires for them."""
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isVisibleInTranscriptOnly"):
        return True
    texts = [t.lstrip() for t in _texts(content)]
    return bool(texts) and all(t.startswith(("<command-name>", "<command-message>", "<local-command-", "[Request interrupted"))
                               for t in texts)


def read_claude_transcript(path, record=None):
    """A Record from a Claude Code transcript (and, via the caller, its subagent files).

    Turns: a user prompt opens one; it ends normally (an assistant entry with no tool call and
    stop_reason end_turn or stop_sequence: Stop fires), by an API error (an entry marked
    isApiErrorMessage: StopFailure fires), or by the user (an "[Request interrupted by user..."
    entry: neither fires). Subagent (sidechain) entries never change the main turn's state."""
    session_id = os.path.splitext(os.path.basename(path))[0]
    record = record or Record("claude", session_id)
    for entry in read_jsonl(path):
        if not isinstance(entry, dict):
            continue
        record.seen(_ts(entry.get("timestamp")))
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        side = bool(entry.get("isSidechain"))
        if entry.get("type") == "user":
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            for b in results:
                tid = b.get("tool_use_id")
                if isinstance(tid, str):
                    record.results.add(tid)
                    if b.get("is_error") and _is_denial(b.get("content")):
                        record.denied.add(tid)
                    if b.get("is_error") and any("InputValidationError" in t[:200] for t in _texts(b.get("content"))):
                        record.invalid.add(tid)
            if entry.get("isCompactSummary") and not side:
                record.compactions += 1
            interrupt = any(t.lstrip().startswith("[Request interrupted by user") for t in _texts(content))
            if interrupt and not side and record.turn_open:
                record.interrupted += 1
                record.turn_open = False
            elif not results and not side and not _not_a_prompt(entry, content):
                record.prompts += 1
                record.turn_open = True
        elif entry.get("type") == "assistant":
            calls = False
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("id"), str):
                    record.tool_ids[b["id"]] = str(b.get("name", ""))
                    calls = True
            if side:
                continue
            if entry.get("isApiErrorMessage") and record.turn_open:
                record.api_errors += 1
                record.turn_open = False
            elif not calls and message.get("stop_reason") in ("end_turn", "stop_sequence"):
                record.turn_open = False
    return record


def read_codex_rollout(path):
    """A Record from a Codex rollout file (session_meta, user messages, calls and their outputs)."""
    record = None
    for entry in read_jsonl(path):
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        if entry.get("type") == "session_meta" and record is None:
            record = Record("codex", str(payload.get("id", "")))
        if record is None:
            continue
        record.seen(_ts(entry.get("timestamp")))
        kind = payload.get("type")
        if entry.get("type") == "event_msg" and kind == "user_message":
            record.prompts += 1
        elif entry.get("type") == "response_item" and kind in ("function_call", "custom_tool_call", "local_shell_call"):
            cid = payload.get("call_id")
            if isinstance(cid, str):
                record.tool_ids[cid] = str(payload.get("name", kind))
        elif entry.get("type") == "response_item" and kind in ("function_call_output", "custom_tool_call_output"):
            cid = payload.get("call_id")
            if isinstance(cid, str):
                record.results.add(cid)
                output = payload.get("output")
                success = None
                if isinstance(output, dict):
                    success = output.get("success")
                    output = output.get("content")
                head = output.strip()[:200].lower() if isinstance(output, str) else ""
                # Only a failed call, and only Codex's own rejection wording at the start: command
                # output (a licence text, say) can contain the same phrases.
                if success is not True and not head.startswith("exit code:") and \
                        any(m in head for m in CODEX_DENIAL_MARKERS):
                    record.denied.add(cid)
    return record


def find_records(claude_projects=None, codex_sessions=None):
    """{(agent, session_id): Record} for every session record found."""
    records = {}
    if claude_projects and os.path.isdir(claude_projects):
        for folder, _, files in os.walk(claude_projects):
            if os.path.basename(folder) == "subagents":
                continue
            for name in files:
                if name.endswith(".jsonl") and "." not in name[:-6]:
                    path = os.path.join(folder, name)
                    record = read_claude_transcript(path)
                    sub = os.path.join(folder, name[:-6], "subagents")
                    if os.path.isdir(sub):
                        for sname in sorted(os.listdir(sub)):
                            if sname.endswith(".jsonl"):
                                read_claude_transcript(os.path.join(sub, sname), record)
                    records[("claude", record.session_id)] = record
    if codex_sessions and os.path.isdir(codex_sessions):
        for folder, _, files in os.walk(codex_sessions):
            for name in files:
                if name.startswith("rollout-") and name.endswith(".jsonl"):
                    record = read_codex_rollout(os.path.join(folder, name))
                    if record and record.session_id:
                        records[("codex", record.session_id)] = record
    return records


def read_spool(spool):
    """{(agent, session_id): [spool line, ...]} and the number of shim_error lines."""
    found = defaultdict(list)
    errors = 0
    for agent in ("claude", "codex"):
        folder = os.path.join(spool, agent)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.endswith(".jsonl"):
                continue
            for line in read_jsonl(os.path.join(folder, name)):
                if not isinstance(line, dict):
                    continue
                if "shim_error" in line:
                    errors += 1
                    continue
                sid = line.get("session_id")
                if isinstance(sid, str):
                    found[(agent, sid)].append(line)
    return found, errors


def expected_counts(record, killed):
    """(expected counts per event, expected Post ids, denied ids) for one session."""
    finished = set(record.tool_ids) & record.results if killed else set(record.tool_ids)
    post_ids = finished - record.denied - record.invalid
    subagents = sum(1 for tid in post_ids if record.tool_ids[tid] in SUBAGENT_TOOLS)
    # The kill forgives one Stop only if a turn was really still running then: a turn with no
    # normal end, or a tool call with no result.
    open_at_end = record.turn_open or bool(set(record.tool_ids) - record.results)
    stops = record.prompts - record.interrupted - record.api_errors
    counts = {
        "SessionStart": 1 + record.compactions,
        "SessionEnd": 0 if killed else 1,
        "UserPromptSubmit": record.prompts,
        "PreToolUse": sum(1 for tid, name in record.tool_ids.items() if name in EDIT_TOOLS and tid not in record.invalid),
        "PostToolUse": len(post_ids),
        "Stop": max(stops - (1 if killed and open_at_end else 0), 0),
        "SubagentStop": subagents,
        "StopFailure": record.api_errors,
    }
    return counts, post_ids, record.denied & set(record.tool_ids)


def count(spool, manifest, claude_projects=None, codex_sessions=None, timeout_minutes=30.0, now=None):
    """All the rows of the T1 loss and coverage report, as plain numbers."""
    now = now if now is not None else dt.datetime.now().timestamp()
    records = find_records(claude_projects, codex_sessions)
    found, shim_errors = read_spool(spool)
    listed = {(s["agent"], s["session_id"]): s for s in manifest.get("sessions", [])}
    loss = defaultdict(lambda: {"expected": 0, "found": 0, "lost": 0, "extra": 0})
    killed_rows = defaultdict(lambda: {"sessions": 0, "missing_session_end": 0, "missing_stop": 0,
                                       "closed_by_session_end": 0, "closed_by_timeout": 0, "still_open": 0,
                                       "not_captured": 0})
    denied = Counter()
    coverage = defaultdict(lambda: {"records": 0, "captured": 0})
    for key, record in sorted(records.items()):
        info = listed.get(key, {"kind": "unlisted", "env": "?", "async": None})
        kind, env, is_async = info.get("kind", "unlisted"), info.get("env", "?"), info.get("async")
        if kind == "noop":
            continue
        lines = found.get(key, [])
        coverage[(env, kind)]["records"] += 1
        coverage[(env, kind)]["captured"] += bool(lines)
        killed = kind == "killed"
        expected, post_ids, denied_ids = expected_counts(record, killed)
        denied[(env, kind)] += len(denied_ids)
        seen = Counter()
        seen_post = set()
        for line in lines:
            event = line.get("event") or line.get("hook_event_name")
            if event == "PostToolUseFailure":
                event = "PostToolUse"
            if event == "PostToolUse" and isinstance(line.get("tool_use_id"), str):
                seen_post.add(line["tool_use_id"])
            seen[event] += 1
        for event in EVENTS:
            row = loss[(env, kind, is_async, event)]
            got = len(seen_post & post_ids) if event == "PostToolUse" else seen[event]
            row["expected"] += expected[event]
            row["found"] += min(got, expected[event])
            row["lost"] += max(expected[event] - got, 0)
            if event == "PostToolUse":
                row["extra"] += seen[event] - len(seen_post & post_ids)
            elif not (killed and event in ("SessionEnd", "Stop")):     # a kill may or may not let these fire
                row["extra"] += max(got - expected[event], 0)
        if killed:
            k = killed_rows[env]
            k["sessions"] += 1
            k["missing_session_end"] += seen["SessionEnd"] == 0
            k["missing_stop"] += seen["Stop"] < record.prompts - record.interrupted - record.api_errors
            last = max((line.get("ts", 0) / 1e9 for line in lines), default=record.last or 0)
            if not lines:
                k["not_captured"] += 1                           # nothing to close: capture missed it
            elif seen["SessionEnd"]:
                k["closed_by_session_end"] += 1
            elif now - last >= timeout_minutes * 60:
                k["closed_by_timeout"] += 1
            else:
                k["still_open"] += 1
    unlisted_spool = sum(1 for key in found if key not in records)
    return {
        "loss": [dict(env=e, kind=k, async_=a, event=ev, **v) for (e, k, a, ev), v in sorted(loss.items(),
                                                                                        key=lambda x: str(x[0]))],
        "killed": [dict(env=e, **v) for e, v in sorted(killed_rows.items())],
        "permission_denied": [dict(env=e, kind=k, calls=n) for (e, k), n in sorted(denied.items())],
        "coverage": [dict(env=e, kind=k, **v) for (e, k), v in sorted(coverage.items())],
        "shim_errors": shim_errors,
        "spool_sessions_without_a_record": unlisted_spool,
    }


def format_report(result):
    out = ["lost events (sessions that end normally carry the zero-loss bar; killed sessions have their own rows)",
           "env | kind | async | event | expected | found | lost | extra"]
    for r in result["loss"]:
        out.append(" | ".join(str(r[k]) for k in ("env", "kind", "async_", "event", "expected", "found", "lost", "extra")))
    out += ["", "killed sessions | sessions | missing SessionEnd | missing Stop | closed by SessionEnd | "
                "closed by timeout | still open | not captured"]
    for r in result["killed"]:
        out.append(" | ".join(str(r[k]) for k in ("env", "sessions", "missing_session_end", "missing_stop",
                                                  "closed_by_session_end", "closed_by_timeout", "still_open",
                                                  "not_captured")))
    out += ["", "permission-denied calls | env | kind | calls"]
    out += [f"denied | {r['env']} | {r['kind']} | {r['calls']}" for r in result["permission_denied"]]
    out += ["", "coverage | env | kind | records | captured"]
    out += [f"coverage | {r['env']} | {r['kind']} | {r['records']} | {r['captured']}" for r in result["coverage"]]
    out += ["", f"shim_error lines | {result['shim_errors']}",
            f"spool sessions with no record | {result['spool_sessions_without_a_record']}"]
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="T1 loss and coverage counter (prints numbers only).")
    p.add_argument("--spool", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--claude-projects")
    p.add_argument("--codex-sessions")
    p.add_argument("--timeout-minutes", type=float, default=30.0)
    p.add_argument("--now", type=float)
    p.add_argument("--json", help="also write the rows as JSON to this file")
    args = p.parse_args(argv)
    with open(args.manifest, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    result = count(args.spool, manifest, args.claude_projects, args.codex_sessions, args.timeout_minutes, args.now)
    print(format_report(result))
    if args.json:
        with open(args.json, "x", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
