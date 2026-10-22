"""Notes: what an agent is told, built from templates over structured fields only.

Every value placed in a note is an identifier that came from git or the engine (a symbol, a
path, a line number, a worktree folder, a branch, an agent name) and is cleaned first: only
letters, digits and ``_ . : / < > - [ ]`` survive, and each value is cut to a short length.
Nothing another session wrote (a prompt, a message, a comment, a default value's text) ever
reaches a note, so one session cannot steer another through it. Each note says what its
analysis covers and is cut to ``MAX_NOTE`` characters.
"""
import os
import re
import time

# Claude Code's hook outputs, under the names they had here. Each agent's own are in its adapter.
from orchestraty.adapters.claude import post_tool_output, pre_tool_output, stop_output  # noqa: F401

MAX_NOTE = 1200
MAX_VALUE = 120
MAX_ITEMS = 6
COVERAGE = "Static calls only; dynamic calls are not analysed."
_UNSAFE = re.compile(r"[^A-Za-z0-9_.:/<>\-\[\]]")
_UNSAFE_REASON = re.compile(r"[^A-Za-z0-9_ *./]")      # reasons are our own words around identifiers
AGENTS = {"claude": "Claude Code", "codex": "Codex", "antigravity": "Antigravity", "git": "plain-git"}
CLOUD = "cloud"             # a cloud agent's branch: its agent works elsewhere and is never told


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


def clean_reason(value):
    """One reason a contract changed (our own words around identifiers) made safe to place in a note."""
    return _UNSAFE_REASON.sub("?", str(value))[:80]


def _reasons(reasons):
    kept = [clean_reason(r) for r in reasons[:3]]
    return "; ".join(kept) or "its contract changed"


def describe(session):
    """'another Claude Code session (worktree wt-b, branch feat-b)' from a session row."""
    agent = session.get("agent", "")
    if agent == CLOUD:
        return f"the cloud branch {clean(session.get('branch') or '?', 80)}"
    where = f"worktree {clean(os.path.basename(session.get('worktree', '')) or '?', 60)}"
    if session.get("branch"):
        where += f", branch {clean(session['branch'], 60)}"
    if agent == "git":
        return f"changes made outside any agent session ({where})"
    return f"another {AGENTS.get(agent, clean(agent, 30))} session ({where})"


def _cap(text):
    text = "Orchestraty: " + text
    return text if len(text) <= MAX_NOTE else text[:MAX_NOTE - 1] + "~"


def both_forms(reasons):
    """A way to write a use that works before and after the change, from its first reason that
    has one, or None (a new required argument, for one, has none)."""
    for r in reasons:
        if r == "positional parameters reordered" or "can no longer be passed by position" in r:
            return "passing the arguments by name works with both forms"
        if (r.startswith("parameter ") and " renamed to " in r) or "can no longer be passed by name" in r:
            return "passing it by position works with both forms"
        if r == "*args removed":
            return "passing no extra positional arguments works with both forms"
        if r == "**kwargs removed":
            return "passing only the named parameters works with both forms"
        if r.startswith("parameter ") and r.endswith(" removed"):
            return "leaving that argument out works with both forms, where it was optional"
    return None


def for_user(finding, changer, before_edit=True, elsewhere=True):
    """O3/O4, to the session whose file uses what another session broke or removed; given
    before its next edit of that file, or with its next tool result (before_edit=False).
    `elsewhere`: the change is in another working tree, so this session's copy lacks it."""
    d = finding["detail"]
    uses = _list(d.get("uses", []), lambda u: _name(u["symbol"]) + _lines(u.get("lines", [])))
    target = f"{_name(finding['symbol'])} in {_path(finding['symbol'])}"
    hint = None
    if finding["rule"] == "O4":
        what = f"{describe(changer)} removed {target}."
    else:
        what = f"{describe(changer)} changed the contract of {target}: {_reasons(d.get('reasons', []))}."
        if d.get("moved_to"):
            what += f" It now lives in {_path(d['moved_to'])}."
        hint = both_forms([str(r) for r in d.get("reasons", [])])
    cloud = changer.get("agent") == CLOUD
    if elsewhere:
        what += (f" The change is in that {'branch' if cloud else 'worktree'}, so your copy does not show it yet; "
                 "both meet when merged.")
    when = "before you change this file" if before_edit else "in the code you changed"
    if cloud:
        act = f": {hint}; otherwise tell your user" if hint else ", and tell your user"
        tail = " if the old form must stay: the agent on that branch works elsewhere and is not told."
    else:
        act = f": {hint}; otherwise agree" if hint else ", and agree"
        tail = " with that session if the old form must stay."
    return _cap(f"{what} {clean(finding['file'])} uses it: {uses}. Check these uses {when}{act}{tail} {COVERAGE}")


def for_changer(finding, user, told=None):
    """O3/O4, to the session that broke or removed something another session's file uses.
    `told`: whether that session gets a note of its own before it next edits the file ("next":
    with its next step, for an agent that takes no note before an edit); by default, unless it
    is a plain-git session (no session with Orchestraty works there)."""
    d = finding["detail"]
    uses = _list(d.get("uses", []), lambda u: _name(u["symbol"]) + _lines(u.get("lines", [])))
    how = "removed" if finding["rule"] == "O4" else f"changed ({_reasons(d.get('reasons', []))})"
    if told is None:
        told = user.get("agent") not in ("git", CLOUD)
    if user.get("agent") == CLOUD:
        then = "The agent on that branch works elsewhere and is not told; mention it to your user."
    elif told == "next":
        then = "That session is told with its next step."
    elif told:
        then = "That session is told before it next edits that file."
    else:
        then = "No session with Orchestraty works in that worktree, so nobody there is told; mention it to your user."
    return _cap(f"{_name(finding['symbol'])} in {_path(finding['symbol'])}, which you {how}, is used in "
                f"{clean(finding['file'])} by {describe(user)}: {uses}. {then} {COVERAGE}")


def for_both(finding, other):
    """O2, to each of two sessions changing the same function."""
    settle = ("The agent on that branch works elsewhere and is not told: settle on one version with your user "
              "before either is merged." if other.get("agent") == CLOUD else
              "Agree on one version before either of you merges.")
    return _cap(f"{describe(other)} is also changing {_name(finding['symbol'])} in {_path(finding['symbol'])}. "
                + settle)


def same_folder(other):
    """W1, to each of two sessions with hooks working in one worktree, about the other one.
    It only informs: in steer mode too, nothing is held back."""
    agent = other.get("agent", "")
    where = f"branch {clean(other['branch'], 60)}" if other.get("branch") else "detached HEAD"
    return _cap(f"another {AGENTS.get(agent, clean(agent, 30))} session ({where}) is working in this same folder. "
                "Its edits, branch switches and commits change this folder for you too, so either session can "
                "overwrite or switch the other's uncommitted work. To keep your work apart, use a separate worktree "
                "(`git worktree add <folder> -b <branch>`), or ask your user.")


def left_behind(finding):
    """O6, to the session that broke a contract, at the end of its turn."""
    d = finding["detail"]
    left = _list(d.get("left", []), lambda c: f"{_name(c['symbol'])} in {_path(c['file'])}" + _lines(c.get("lines", [])))
    return _cap(f"you changed the contract of {_name(finding['symbol'])} in {_path(finding['symbol'])} "
                f"({_reasons(d.get('reasons', []))}). These callers still use it and no session has updated "
                f"them: {left}. Update them, or say why they need no change. {COVERAGE}")


def reserved(claim):
    """What a claim reserves, cleaned: a file, "the folder pkg/", or "charge in a.py"."""
    if claim["kind"] == "symbol":
        return f"{_name(claim['target'])} in {_path(claim['file'])}"
    if claim["kind"] == "area":
        return f"the folder {clean(claim['target'])}/"
    return clean(claim["target"])


def _who(session):
    """Another session, as a note names it; a plain-git one is someone outside any agent session."""
    if session.get("agent") == "git":
        return "someone working outside any agent session (" + describe(session).split(" (", 1)[-1]
    return describe(session)


def claimed(claim, claimer, before_edit=True, ask=None):
    """A claim, to another session: before it edits what the claim covers (for a function or
    class, the file that defines it; for a folder, any file in it), or with its next step when
    it has changed that already (before_edit=False). `ask`, for a hard claim: True when this
    edit waits for the user's answer, False when the agent cannot be made to wait."""
    who = _who(claimer)
    until = time.strftime("%H:%M", time.localtime(claim["expires"] / 1e9))
    number = int(claim["id"])
    how = "hard claim" if claim["hard"] else "claim"
    what = f"{who} has claimed {reserved(claim)} until {until} ({how} #{number})."
    if not before_edit:
        then = "You have changed it in your worktree: agree with that session, or ask your user, before you go on."
    elif ask:
        then = "This edit waits for the user's answer: leave it to that session unless the user agrees."
    elif ask is False:
        then = ("This session cannot be made to wait for its user before an edit, so this note only informs: ask your "
                "user before you change it.")
    else:
        then = "Leave it to that session, or agree with it before you change it."
    return _cap(f"{what} {then} The user can end the claim with orch release {number}.")


def planned_overlap(finding, me, other):
    """O7, to each of two sessions whose declared or claimed work overlaps, about the other one.
    It only informs: in steer mode too, nothing is held back."""
    d = finding["detail"]
    how = d.get("how") or {}
    what = (f"{_name(finding['symbol'])} in {_path(finding['symbol'])}" if finding.get("symbol")
            else clean(finding["file"]))
    mine = "claimed" if how.get(me) == "claimed" else "declared"
    theirs = "claimed" if how.get(other["id"]) == "claimed" else "declared"
    return _cap(f"{_who(other)} has {theirs} {what}, which you have {mine} too (rule O7). Agree with that session "
                "on who changes it before either of you goes on; nothing is held back.")


def push_line(findings):
    """The one line shown at push for the O6 findings still open in a worktree, or ""."""
    if not findings:
        return ""
    first = findings[0]
    left = first["detail"].get("left", [])
    more = f" and {len(findings) - 1} more change(s)" if len(findings) > 1 else ""
    return (f"orchestraty: {len(left)} caller(s) of {_name(first['symbol'])} still use its old form "
            f"({_list(left, lambda c: _name(c['symbol']))}){more}. Run: orch status")[:400]
