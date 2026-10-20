"""Notes: what an agent is told, built from templates over structured fields only.

Every value placed in a note is an identifier that came from git or the engine (a symbol, a
path, a line number, a worktree folder, a branch, an agent name) and is cleaned first: only
letters, digits and ``_ . : / < > - [ ]`` survive, and each value is cut to a short length.
Nothing another session wrote (a prompt, a message, a comment, a default value's text) ever
reaches a note, so one session cannot steer another through it. Each note says what its
analysis covers and is cut to ``MAX_NOTE`` characters.
"""
import json
import os
import re

MAX_NOTE = 1200
MAX_VALUE = 120
MAX_ITEMS = 6
COVERAGE = "Static calls only; dynamic calls are not analysed."
_UNSAFE = re.compile(r"[^A-Za-z0-9_.:/<>\-\[\]]")
_UNSAFE_REASON = re.compile(r"[^A-Za-z0-9_ *./]")      # reasons are our own words around identifiers
AGENTS = {"claude": "Claude Code", "codex": "Codex", "git": "plain-git"}


def clean(value, limit=MAX_VALUE):
    """An identifier made safe to place in a note."""
    text = _UNSAFE.sub("?", str(value))
    return text if len(text) <= limit else text[:limit - 1] + "~"


def _name(symbol):
    """'path::Qual.name' -> 'Qual.name'."""
    return clean(symbol.split("::", 1)[-1])


def _path(symbol_or_file):
    return clean(symbol_or_file.split("::", 1)[0])


def _lines(lines):
    lines = [int(n) for n in lines if isinstance(n, int) and not isinstance(n, bool)][:MAX_ITEMS]
    if not lines:
        return ""
    return (" (line " if len(lines) == 1 else " (lines ") + ", ".join(str(n) for n in lines) + ")"


def _list(items, render):
    shown = [render(i) for i in items[:MAX_ITEMS]]
    more = len(items) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def _reasons(reasons):
    kept = [_UNSAFE_REASON.sub("?", str(r))[:80] for r in reasons[:3]]
    return "; ".join(kept) or "its contract changed"


def describe(session):
    """'another Claude Code session (worktree wt-b, branch feat-b)' from a session row."""
    agent = session.get("agent", "")
    where = f"worktree {clean(os.path.basename(session.get('worktree', '')) or '?', 60)}"
    if session.get("branch"):
        where += f", branch {clean(session['branch'], 60)}"
    if agent == "git":
        return f"changes made outside any agent session ({where})"
    return f"another {AGENTS.get(agent, clean(agent, 30))} session ({where})"


def _cap(text):
    text = "Orchestraty: " + text
    return text if len(text) <= MAX_NOTE else text[:MAX_NOTE - 1] + "~"


def for_user(finding, changer):
    """O3/O4, to the session whose file uses what another session broke or removed; given
    before its next edit of that file."""
    d = finding["detail"]
    uses = _list(d.get("uses", []), lambda u: _name(u["symbol"]) + _lines(u.get("lines", [])))
    target = f"{_name(finding['symbol'])} in {_path(finding['symbol'])}"
    if finding["rule"] == "O4":
        what = f"{describe(changer)} removed {target}."
    else:
        what = f"{describe(changer)} changed the contract of {target}: {_reasons(d.get('reasons', []))}."
        if d.get("moved_to"):
            what += f" It now lives in {_path(d['moved_to'])}."
    return _cap(f"{what} {clean(finding['file'])} uses it: {uses}. Check these uses before you change this "
                f"file, and agree with that session if the old form must stay. {COVERAGE}")


def for_changer(finding, user):
    """O3/O4, to the session that broke or removed something another session's file uses."""
    d = finding["detail"]
    uses = _list(d.get("uses", []), lambda u: _name(u["symbol"]) + _lines(u.get("lines", [])))
    how = "removed" if finding["rule"] == "O4" else f"changed ({_reasons(d.get('reasons', []))})"
    return _cap(f"{_name(finding['symbol'])} in {_path(finding['symbol'])}, which you {how}, is used in "
                f"{clean(finding['file'])} by {describe(user)}: {uses}. That session is told before it next "
                f"edits that file. {COVERAGE}")


def for_both(finding, other):
    """O2, to each of two sessions changing the same function."""
    return _cap(f"{describe(other)} is also changing {_name(finding['symbol'])} in {_path(finding['symbol'])}. "
                "Agree on one version before either of you merges.")


def left_behind(finding):
    """O6, to the session that broke a contract, at the end of its turn."""
    d = finding["detail"]
    left = _list(d.get("left", []), lambda c: f"{_name(c['symbol'])} in {_path(c['file'])}" + _lines(c.get("lines", [])))
    return _cap(f"you changed the contract of {_name(finding['symbol'])} in {_path(finding['symbol'])} "
                f"({_reasons(d.get('reasons', []))}). These callers still use it and no session has updated "
                f"them: {left}. Update them, or say why they need no change. {COVERAGE}")


def push_line(findings):
    """The one line shown at push for the O6 findings still open in a worktree, or ""."""
    if not findings:
        return ""
    first = findings[0]
    left = first["detail"].get("left", [])
    more = f" and {len(findings) - 1} more change(s)" if len(findings) > 1 else ""
    return (f"orchestraty: {len(left)} caller(s) of {_name(first['symbol'])} still use its old form "
            f"({_list(left, lambda c: _name(c['symbol']))}){more}. Run: orch status")[:400]


# The output each hook prints, as bytes ready to write into a slot.
def pre_tool_output(text, steer=False):
    if steer:
        out = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": text}
    else:
        out = {"hookEventName": "PreToolUse", "additionalContext": text}
    return json.dumps({"hookSpecificOutput": out}).encode("utf-8")


def post_tool_output(text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}).encode("utf-8")


def stop_output(text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": text}}).encode("utf-8")
