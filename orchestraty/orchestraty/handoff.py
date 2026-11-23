"""Handoffs: a piece of work packaged from the record, for another agent to take over.

``orch handoff`` builds a packet for one session: its task line, branch, base commit and head,
the files it changed, its contract changes with their reasons, the open findings that involve
it, the callers it left behind, and, with ``--tests``, how the user's test command went in its
worktree. Everything comes from the record and from git: the hooks never store prompts, and
nothing here reads a prompt or a transcript. The task line is the user's ``--task``, else the
task line the agent gave when it declared its work (the MCP tool ``declare``), else the branch.

A packet is Markdown, for an agent to read, with the same content as JSON beside it:
``<state>/handoffs/<id>.md`` and ``<id>.json``. Paths, functions and branches in the Markdown
are cleaned as notes clean them; the test output is the user's own command's, shown as such.
``orch resume <id>`` (``resume``) starts an agent on a packet in a new worktree.
"""
import json
import os
import secrets
import time

from orchestraty import gitio, usercommand
from orchestraty.coordinator.record import Record
from orchestraty.delivery import notes as templates

PACKET_SCHEMA = 1
NS = 1_000_000_000
MAX_TASK = 500
TEST_TIMEOUT = 600
CLOUD_AGENT = "cloud"


class Refused(Exception):
    """No packet can be made, or found; the message says why."""


def _iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def folder(state):
    return os.path.join(state.root, "handoffs")


def _task(task, declared, branch):
    """(text, where it came from) for the task line."""
    if task and task.strip():
        return task.strip()[:MAX_TASK], "user"
    said = [d for d in declared if d.get("task")]
    if said:
        return said[-1]["task"][:MAX_TASK], "declared"
    if branch:
        return branch, "branch"
    return "", "none"


def _git(top):
    """(head, [paths changed and not committed]) of a worktree, from git; ("", []) when it is gone."""
    if not top or not os.path.isdir(top):
        return "", []
    try:
        head = gitio.run(["rev-parse", "--verify", "--quiet", "HEAD"], top).strip()
    except gitio.GitError:
        head = ""
    try:
        out = gitio.run(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"], top)
    except gitio.GitError:
        return head, []
    return head, sorted({entry[3:] for entry in out.split("\0") if len(entry) > 3})


def build(state, session, task=None, tests=None, timeout=TEST_TIMEOUT, now=None):
    """The packet (a dict) for one session row, read from the record and git."""
    now = time.time() if now is None else now
    record = Record(state.record, readonly=True)
    try:
        sessions = {s["id"]: s for s in record.sessions()}
        repo = next((r for r in record.repos() if r["id"] == session["repo_id"]), None)
        files, unattributed, symbols = record.changes(session["id"])
        declared = [d for d in record.declarations() if d["session_id"] == session["id"]]
        mine = [f for f in record.findings(repo_id=session["repo_id"])
                if session["id"] in f["sessions"] or session["id"] in (f["changer"], f["receiver"])]
        # a latent finding is for the session whose file uses the change, before it edits that file
        mine = [f for f in mine if not f["detail"].get("latent") or f["receiver"] == session["id"]]
    finally:
        record.close()
    text, source = _task(task, declared, session["branch"])
    head, uncommitted = _git(session["worktree"])
    mine.sort(key=lambda f: ({"strong": 0, "medium": 1, "weak": 2}.get(f["strength"], 9), f["rule"], f["key"]))
    packet = {
        "schema": PACKET_SCHEMA, "id": "", "made": _iso(now),
        "session": {"id": session["id"], "agent": session["agent"], "worktree": session["worktree"],
                    "branch": session["branch"], "state": session["state"]},
        "repository": {"name": repo["name"] if repo else "", "common_dir": repo["common_dir"] if repo else ""},
        "task": {"text": text, "from": source},
        "base_commit": session["base_commit"], "head": head, "uncommitted": uncommitted,
        "files": files, "unattributed": unattributed,
        "contract_changes": [c for c in symbols if _contract(c)],
        "other_changes": [c for c in symbols if not _contract(c)],
        "findings": [{"key": f["key"], "rule": f["rule"], "strength": f["strength"], "symbol": f["symbol"],
                      "file": f["file"], "latent": bool(f["detail"].get("latent")),
                      "with": [x for x in f["sessions"] if x != session["id"]],
                      "note": _bare(templates.about(f, session, sessions))} for f in mine],
        "left_behind": [{"symbol": f["symbol"], "reasons": f["detail"].get("reasons", []),
                         "callers": f["detail"].get("left", [])}
                        for f in mine if f["rule"] == "O6" and f["changer"] == session["id"]],
        "tests": None,
    }
    if tests:
        if not session["worktree"] or not os.path.isdir(session["worktree"]):
            raise Refused(f"the session's worktree {session['worktree'] or '(none)'} is gone: no test can run there")
        packet["tests"] = dict(usercommand.run(tests, session["worktree"], timeout), timeout=timeout)
    return packet


def _contract(change):
    """Whether a function's change is one of its contract: breaking, to review, or a removal."""
    return change.get("contract") in ("breaking", "review") or change["kind"] == "removed"


def _bare(text):
    return text[len("Orchestraty: "):] if text.startswith("Orchestraty: ") else text


def _session_ref(sessions, ref):
    found = [s for s in sessions if s["id"] == ref] or [s for s in sessions if s["id"].startswith(ref)]
    if len(found) > 1:
        raise Refused(f"{ref} is the start of {len(found)} sessions' ids: give more of it")
    if not found:
        raise Refused(f"no session {ref} in the record (orch status --all lists them)")
    return found[0]


def pick_session(state, ref, cwd):
    """The session a handoff is for: the one `ref` names (live or ended), else the live session
    working in the worktree holding `cwd`, one with hooks before a plain-git one, else the last
    one seen there."""
    if not os.path.exists(state.record):
        raise Refused("the record is empty: no session has been seen yet")
    record = Record(state.record, readonly=True)
    try:
        sessions = [s for s in record.sessions() if s["agent"] != CLOUD_AGENT]
    finally:
        record.close()
    if ref:
        return _session_ref(sessions, ref)
    try:
        info = gitio.worktree_info(cwd)
    except (gitio.GitError, gitio.UntrustedRepositoryError) as exc:
        raise Refused(str(exc)) from None
    if info is None:
        raise Refused(f"{cwd} is not inside a git worktree: run it in the session's worktree, or pass --session")
    here = [s for s in sessions if s["worktree"] and _same(s["worktree"], info["top"])]
    live = [s for s in here if s["state"] == "live"]
    chosen = [s for s in live if s["agent"] != "git"] or live or sorted(here, key=lambda s: s["last_seen"])[-1:]
    if not chosen:
        raise Refused(f"no session has worked in {info['top']}: pass --session")
    if len(chosen) > 1:
        raise Refused(f"{len(chosen)} live sessions work in {info['top']}: pass --session, one of "
                      + ", ".join(s["id"][:8] for s in chosen))
    return chosen[0]


def _same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


# ------------------------------------------------------------------------------- the packet's text
def _symbol_line(c):
    name = templates.clean(c["symbol"].split("::", 1)[-1])
    where = templates.clean(c["symbol"].split("::", 1)[0])
    what = c["kind"] + (f", contract {c['contract']}" if c.get("contract") not in ("", "none", None) else "")
    reasons = "; ".join(templates.clean_reason(r) for r in c.get("reasons", [])[:4])
    moved = f" (moved from {templates.clean(c['moved_from'])})" if c.get("moved_from") else ""
    return f"- {name} in {where}{moved}: {what}" + (f": {reasons}" if reasons else "")


def render(p):
    """The packet as Markdown, for the agent that takes the work over."""
    s = p["session"]
    task = p["task"]
    title = task["text"].splitlines()[0][:120] if task["text"] else f"the work on {templates.clean(s['branch'])}"
    where = {"user": "as the user gave it", "declared": "as the agent declared it", "branch": "the branch name",
             "none": ""}[task["from"]]
    lines = [f"# Handoff: {title}", "",
             f"You are taking over work begun by a {templates.AGENTS.get(s['agent'], templates.clean(s['agent'], 30))} "
             f"session (worktree {templates.clean(os.path.basename(s['worktree']) or '?', 60)}). Orchestraty "
             "packaged it from what it recorded and from git; it holds no prompt and no transcript.", ""]
    if task["text"]:
        lines.append(f"- **Task** ({where}): {task['text']}")
    lines += [f"- **Branch:** {templates.clean(s['branch']) if s['branch'] else '(detached HEAD)'}",
              f"- **Base commit:** {p['base_commit'][:12] or '(none)'}; **head:** {p['head'][:12] or '(none)'}"]
    if p["uncommitted"]:
        shown = ", ".join(templates.clean(x) for x in p["uncommitted"][:8])
        more = f" and {len(p['uncommitted']) - 8} more" if len(p["uncommitted"]) > 8 else ""
        lines.append(f"- **Not committed** when this was made, so not in the head: {shown}{more}. They are still "
                     "in the old worktree.")
    lines += ["", "## Files changed since the base commit", ""]
    if p["files"] or p["unattributed"]:
        lines += [f"- {status} {templates.clean(path)}" for path, status in sorted(p["files"].items())]
        lines += [f"- ? {templates.clean(path)} (changed in a folder two sessions share)" for path in p["unattributed"]]
    else:
        lines.append("None.")
    lines += ["", "## Contract changes", ""]
    lines += [_symbol_line(c) for c in p["contract_changes"]] or ["None: no function or class signature changed "
                                                                 "in a way that breaks or needs review."]
    if p["other_changes"]:
        lines.append(f"Other functions and classes changed without a contract change: {len(p['other_changes'])}.")
    lines += ["", "## Open findings with other sessions", ""]
    lines += [f"- {f['strength']} {f['rule']}{' (latent)' if f['latent'] else ''}: {f['note']}"
              for f in p["findings"]] or ["None open."]
    lines += ["", "## Callers left behind", ""]
    callers = [(entry["symbol"], c) for entry in p["left_behind"] for c in entry["callers"]]
    lines += [f"- {templates.clean(c.get('symbol', '').split('::', 1)[-1])} in {templates.clean(c.get('file', ''))}"
              + (f" (lines {', '.join(str(n) for n in c.get('lines', [])[:6])})" if c.get("lines") else "")
              + f" still calls {templates.clean(sym.split('::', 1)[-1])} the old way" for sym, c in callers] or [
              "None: no caller of a changed contract is left un-updated."]
    lines += ["", "## Tests", ""]
    t = p["tests"]
    if t is None:
        lines.append("Not run for this handoff (orch handoff --tests \"<command>\" runs them).")
    else:
        how = (f"did not finish within {t['timeout']} s" if t["timed_out"] else f"exited {t['exit_code']}")
        lines += [f"`{t['command']}`, run in the session's worktree, {how} after {t['seconds']} s. "
                  "Its last lines of output (the command's own words):", "", "```"] + t["last_lines"] + ["```"]
    lines += ["", f"Packet {p['id']}, made {p['made']}, from session {s['id']}."]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------- storing them
def save(state, packet):
    """Give the packet an id and store it: (id, path of the Markdown)."""
    os.makedirs(folder(state), exist_ok=True)
    for _ in range(100):
        packet_id = secrets.token_hex(3)
        if not os.path.exists(os.path.join(folder(state), packet_id + ".json")):
            break
    packet["id"] = packet_id
    md = os.path.join(folder(state), packet_id + ".md")
    for path, text in ((md, render(packet)), (os.path.join(folder(state), packet_id + ".json"),
                                              json.dumps(packet, indent=2) + "\n")):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    return packet_id, md


def load(state, ref):
    """(packet, path of its Markdown) for an id, or the start of one."""
    ref = (ref or "").strip().lower()
    try:
        names = sorted(n[:-5] for n in os.listdir(folder(state)) if n.endswith(".json"))
    except OSError:
        names = []
    found = [n for n in names if n == ref] or [n for n in names if ref and n.startswith(ref)]
    if len(found) > 1:
        raise Refused(f"{ref} is the start of {len(found)} handoffs' ids: give more of it")
    if not found:
        raise Refused(f"no handoff {ref} (orch handoff makes one; they are in {folder(state)})")
    with open(os.path.join(folder(state), found[0] + ".json"), "r", encoding="utf-8") as fh:
        packet = json.load(fh)
    return packet, os.path.join(folder(state), found[0] + ".md")
