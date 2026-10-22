"""orch mcp: the MCP server, driven as an agent drives it, a subprocess speaking JSON-RPC over
stdin and stdout, on two sessions the coordinator has seen."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import config, status
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.mcp import tools
from orchestraty.paths import State

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = PACKAGE_ROOT.parent / "engine"
BROKEN = "def charge(amount):\n    return amount\n"
USES = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"
REASON = "parameter currency removed"
A_IS = "another Claude Code session (worktree wt-a, branch feat-a)"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def settle(c):
    for _ in range(3):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def edit(state, c, sid, top, rel, text):
    path = top / rel
    path.write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(path))
    settle(c)


@pytest.fixture
def two(state, repo, git, tmp_path):
    """A breaks charge in wt-a; B, in wt-b, writes c.py, which calls it the old way. b.py's pay
    calls it too, and nobody has touched it."""
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    for sid, top in (("A", wa), ("B", wb)):
        spool_event(state.root, sid, "SessionStart", top)
        spool_event(state.root, sid, "UserPromptSubmit", top)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    edit(state, c, "A", wa, "a.py", BROKEN)
    edit(state, c, "B", wb, "c.py", USES)
    yield c, wa, wb
    c.close()


def request(rid, method, params=None):
    m = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        m["params"] = params
    return m


def initialize(version="2025-06-18"):
    return request(0, "initialize", {"protocolVersion": version, "capabilities": {},
                                     "clientInfo": {"name": "test-client", "version": "1.0"}})


INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def call(rid, name, **arguments):
    return request(rid, "tools/call", {"name": name, "arguments": arguments})


def mcp(state, folder, *messages, args=(), env=None):
    """Run orch mcp in `folder` as an agent does, send it the messages (dicts, or raw lines as
    bytes) and close its stdin. Every line it printed must be a JSON-RPC message: returns them
    by id (those with a null id in a list under None), with its stderr."""
    env = dict(os.environ if env is None else env, ORCH_HOME=state.root,
               PYTHONPATH=os.pathsep.join([str(PACKAGE_ROOT), str(ENGINE_ROOT)]))
    env.pop("CLAUDE_PROJECT_DIR", None)
    data = b"".join((m if isinstance(m, bytes) else json.dumps(m).encode("utf-8")) + b"\n" for m in messages)
    proc = subprocess.run([sys.executable, "-m", "orchestraty", "mcp"] + list(args), input=data, capture_output=True,
                          cwd=str(folder), env=env, timeout=120)
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, err
    answers = {None: []}
    for line in proc.stdout.decode("utf-8").splitlines():
        message = json.loads(line)                      # nothing but JSON on stdout...
        for m in message if isinstance(message, list) else [message]:
            assert m["jsonrpc"] == "2.0" and ("result" in m) != ("error" in m), line    # ...and only answers
            if m["id"] is None:
                answers[None].append(m)
            else:
                assert m["id"] not in answers
                answers[m["id"]] = m
    return answers, err


def result(answers, rid):
    assert "result" in answers[rid], answers[rid]
    return answers[rid]["result"]


def text_of(r):
    return r["content"][0]["text"]


def test_initialize_then_list_then_each_tool_on_two_sessions(state, two):
    c, wa, wb = two
    answers, err = mcp(state, wb, initialize(), INITIALIZED, request(1, "tools/list"),
                       call(2, "who_is_changing", path="a.py"),
                       call(3, "who_is_changing", path="a.py", symbol="charge"),
                       call(4, "check_before_edit", path="c.py"),
                       call(5, "check_before_edit", path="b.py"),
                       call(6, "check_before_edit", path="a.py"),
                       call(7, "my_overlaps"),
                       call(8, "status"),
                       request(9, "ping"))
    assert sorted(k for k in answers if k is not None) == list(range(10)) and answers[None] == []
    init = result(answers, 0)
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "orchestraty"
    assert init["capabilities"] == {"tools": {"listChanged": False}} and "check_before_edit" in init["instructions"]
    listed = result(answers, 1)["tools"]
    assert [t["name"] for t in listed] == ["who_is_changing", "check_before_edit", "my_overlaps", "status", "declare"]
    assert all(t["inputSchema"]["type"] == "object" and t["description"] and "handler" not in t for t in listed)
    assert result(answers, 9) == {}
    assert "initialize: test-client 1.0" in err and "tools/call check_before_edit" in err

    # who_is_changing: A changes a.py, with charge's contract broken, and why
    who = result(answers, 2)
    [row] = who["structuredContent"]["sessions"]
    assert (row["session"], row["agent"], row["worktree"], row["branch"], row["you"]) == ("A", "claude", "wt-a",
                                                                                          "feat-a", False)
    assert row["file"] == "M" and row["changes"] == [{"symbol": "a.py::charge", "kind": "modified", "change": "signature",
                                                      "contract": "breaking", "reasons": [REASON]}]
    assert f"- {A_IS}, last event" in text_of(who)
    assert f"charge modified, contract breaking ({REASON})" in text_of(who)
    assert "Static calls only; dynamic calls are not analysed." in text_of(who) and not who.get("isError")
    by_symbol = result(answers, 3)["structuredContent"]
    assert by_symbol["symbol"] == "a.py::charge" and [r["session"] for r in by_symbol["sessions"]] == ["A"]

    # check_before_edit: the note B gets before it edits c.py, word for word
    before = result(answers, 4)
    note = json.loads(Path(slots.edit_slot(state, "B", str(wb / "c.py"))).read_bytes())
    [used] = before["structuredContent"]["uses_broken"]
    assert used["rule"] == "O3" and used["note"] == note["hookSpecificOutput"]["additionalContext"]
    assert f"{A_IS} changed the contract of charge in a.py: {REASON}." in text_of(before)
    assert "c.py uses it: use (line 5)" in text_of(before)
    latent = result(answers, 5)["structuredContent"]["uses_broken"]
    assert [(u["latent"], "b.py uses it: pay (line 5)" in u["note"]) for u in latent] == [(True, True)]
    also = result(answers, 6)
    assert [a["symbol"] for a in also["structuredContent"]["also_changing"]] == ["a.py::charge"]
    assert f"{A_IS} is also changing charge in a.py. Agree on one version" in text_of(also)

    # my_overlaps, for B: the strong one, and the latent one before it edits b.py
    mine = result(answers, 7)["structuredContent"]
    assert mine["session"] == "B"
    assert sorted((o["rule"], o["file"], o["latent"]) for o in mine["overlaps"]) == [("O3", "b.py", True),
                                                                                    ("O3", "c.py", False)]
    assert all(o["with"][0]["session"] == "A" for o in mine["overlaps"])

    # status: the repository as orch status --json has it
    st = result(answers, 8)["structuredContent"]
    assert st["schema"] == status.STATUS_SCHEMA and st["you"] == "B"
    assert [s["id"] for s in st["repository"]["sessions"]] == ["A", "B"]
    assert st["repository"]["overlaps"] == status.collect(state)["repositories"][0]["overlaps"]


def test_the_changer_hears_what_it_broke_and_who_is_left_behind(state, two):
    c, wa, wb = two
    answers, _ = mcp(state, wa, initialize(), call(1, "my_overlaps"), call(2, "who_is_changing", path="a.py"))
    mine = result(answers, 1)["structuredContent"]
    assert [(o["rule"], o["latent"]) for o in mine["overlaps"]] == [("O3", False), ("O6", False)]
    o3, o6 = mine["overlaps"]
    assert o3["note"].startswith(f"charge in a.py, which you changed ({REASON}), is used in c.py by another Claude Code "
                                 "session (worktree wt-b, branch feat-b): use (line 5).")
    assert "These callers still use it and no session has updated them: pay in b.py (line 5)." in o6["note"]
    [row] = result(answers, 2)["structuredContent"]["sessions"]
    assert row["you"] is True and text_of(result(answers, 2)).splitlines()[1].startswith("- you, last event")


def test_declare_reaches_the_record_through_the_coordinator_and_expires(state, two):
    c, wa, wb = two
    answers, _ = mcp(state, wa, initialize(), call(1, "declare", paths=["a.py", str(wa / "b.py")],
                                                   symbols=["charge", "b.py::pay"], task="drop the currency"))
    d = result(answers, 1)["structuredContent"]
    assert (d["session"], d["paths"], d["symbols"], d["holds_minutes"]) == ("A", ["a.py", "b.py"],
                                                                           ["charge", "b.py::pay"], 120)
    assert c.record.declarations() == []                          # the server wrote no record...
    c.step()                                                       # ...the coordinator stores it
    [stored] = c.record.declarations()
    assert (stored["session_id"], stored["paths"], stored["task"]) == ("A", ["a.py", "b.py"], "drop the currency")
    # orch status shows it, with A's own words, to the user
    assert 'until' in status.render(status.collect(state)) and '"drop the currency"' in status.render(status.collect(state))
    # another session asking sees the paths and functions, never the task line
    answers, _ = mcp(state, wb, initialize(), call(1, "who_is_changing", path="b.py"), call(2, "status"))
    [row] = result(answers, 1)["structuredContent"]["sessions"]
    assert row["session"] == "A" and row["declared"][0]["paths"] == ["a.py", "b.py"] and "task" not in row["declared"][0]
    assert "declared it until" in text_of(result(answers, 1))
    st = result(answers, 2)
    assert "drop the currency" not in json.dumps(st)
    answers, _ = mcp(state, wa, initialize(), call(1, "status"))
    assert result(answers, 1)["structuredContent"]["repository"]["sessions"][0]["declared"][0]["task"] == "drop the currency"
    # two hours later it no longer holds
    later = time.time() + 121 * 60
    data, _ = tools.call(state, str(wb), "who_is_changing", {"path": "b.py"}, now=later)
    assert data["sessions"] == []
    assert all(s["declared"] == [] for s in status.collect(state, now=later)["repositories"][0]["sessions"])


def test_unknown_methods_and_bad_arguments_get_json_rpc_errors(state, two):
    c, wa, wb = two
    answers, _ = mcp(state, wb, initialize(),
                     request(1, "resources/list"),
                     call(2, "who_is_changing"),
                     call(3, "who_is_changing", path=3),
                     call(4, "who_is_changing", path="a.py", colour="red"),
                     call(5, "nothing_like_it", path="a.py"),
                     call(6, "declare", paths=[]),
                     request(7, "tools/call", ["who_is_changing"]),
                     request(8, "tools/call", {"arguments": {}}),
                     b"{not json",
                     json.dumps({"id": 9, "method": "ping"}).encode(),
                     json.dumps({"jsonrpc": "2.0", "id": None, "method": "ping"}).encode(),
                     {"jsonrpc": "2.0", "method": "notifications/whatever"},
                     {"jsonrpc": "2.0", "id": 10, "result": {}},
                     json.dumps([request(11, "ping"), {"jsonrpc": "2.0", "method": "notifications/cancelled"},
                                 request(12, "nope")]).encode(),
                     request(13, "initialize", {"capabilities": {}}),
                     call(14, "check_before_edit", path="../outside.py"))
    codes = {rid: a["error"]["code"] for rid, a in answers.items() if rid is not None and "error" in a}
    assert codes == {1: -32601, 2: -32602, 3: -32602, 4: -32602, 5: -32602, 6: -32602, 7: -32602, 8: -32602,
                     9: -32600, 12: -32601, 13: -32602}
    # the line that is not JSON and the request with a null id are answered with a null id
    assert sorted(a["error"]["code"] for a in answers[None]) == [-32700, -32600]
    assert "unknown tool: nothing_like_it" in answers[5]["error"]["message"]
    assert "missing argument(s): path" in answers[2]["error"]["message"]
    assert result(answers, 11) == {}                       # a batch gets its answers, the notification none
    assert 10 not in answers                               # a response to nothing is dropped
    assert result(answers, 14)["isError"] is True and "not inside this repository" in text_of(result(answers, 14))


def test_a_folder_two_sessions_share_needs_the_session_argument(state, two):
    c, wa, wb = two
    spool_event(state.root, "C", "SessionStart", wa)
    spool_event(state.root, "C", "UserPromptSubmit", wa)
    settle(c)
    answers, _ = mcp(state, wa, initialize(), call(1, "my_overlaps"), call(2, "my_overlaps", session="C"),
                     call(3, "check_before_edit", path="a.py", session="A"), call(4, "who_is_changing", path="a.py"),
                     call(5, "my_overlaps", session="Z"))
    refused = result(answers, 1)
    assert refused["isError"] is True and "2 live sessions work in" in text_of(refused) and "pass `session`" in text_of(refused)
    assert "A, C" in text_of(refused)
    w1 = [o for o in result(answers, 2)["structuredContent"]["overlaps"] if o["rule"] == "W1"]
    assert len(w1) == 1 and "is working in this same folder" in w1[0]["note"]
    same = result(answers, 3)["structuredContent"]["same_folder"]
    assert len(same) == 1 and "another Claude Code session (branch feat-a) is working in this same folder" in same[0]["note"]
    assert not result(answers, 4).get("isError")           # who is changing it needs no caller
    assert "No live session has the id Z" in text_of(result(answers, 5))


def test_a_worktree_with_no_session_yet_still_hears_what_its_files_use(state, two, repo, git, tmp_path):
    c, wa, wb = two
    wc = tmp_path / "wt-c"
    git(repo, "worktree", "add", "-q", "-b", "feat-c", str(wc))
    settle(c)
    answers, _ = mcp(state, wc, initialize(), call(1, "check_before_edit", path="b.py"), call(2, "my_overlaps"),
                     call(3, "declare", paths=["b.py"]), call(4, "who_is_changing", path="c.py"))
    before = result(answers, 1)
    [used] = before["structuredContent"]["uses_broken"]
    assert used["rule"] == "O6" and f"{A_IS} changed the contract of charge in a.py: {REASON}." in used["note"]
    assert "b.py uses it: pay (line 5)" in used["note"] and before["structuredContent"]["session"] is None
    assert "No session works in this worktree yet" in text_of(before)
    for rid in (2, 3):
        assert result(answers, rid)["isError"] is True and "No live session works in" in text_of(result(answers, rid))
    assert [r["session"] for r in result(answers, 4)["structuredContent"]["sessions"]] == ["B"]


def test_a_server_started_outside_the_workspace_takes_the_worktree_from_an_absolute_path(state, two, tmp_path):
    c, wa, wb = two
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    answers, _ = mcp(state, outside, initialize(), call(1, "check_before_edit", path=str(wb / "c.py")),
                     call(2, "check_before_edit", path="c.py"), call(3, "my_overlaps", session="B"))
    assert result(answers, 1)["structuredContent"]["session"] == "B"
    assert len(result(answers, 1)["structuredContent"]["uses_broken"]) == 1
    assert result(answers, 2)["isError"] is True and "pass the file's absolute path" in text_of(result(answers, 2))
    assert result(answers, 3)["structuredContent"]["session"] == "B"
    answers, _ = mcp(state, outside, initialize(), call(1, "my_overlaps"), args=["--folder", str(wb)])
    assert result(answers, 1)["structuredContent"]["session"] == "B"


def test_older_clients_get_the_json_as_text_and_unknown_versions_the_newest(state, two):
    c, wa, wb = two
    answers, _ = mcp(state, wb, initialize("2024-11-05"), call(1, "who_is_changing", path="a.py"))
    assert result(answers, 0)["protocolVersion"] == "2024-11-05"
    r = result(answers, 1)
    assert "structuredContent" not in r and len(r["content"]) == 2
    assert json.loads(r["content"][1]["text"])["sessions"][0]["session"] == "A"
    answers, _ = mcp(state, wb, initialize("1999-01-01"))
    assert result(answers, 0)["protocolVersion"] == "2025-11-25"


def test_without_a_record_or_a_repository_it_still_answers_cleanly(state, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    answers, _ = mcp(state, folder, initialize(), call(1, "who_is_changing", path="x.py"), call(2, "status"),
                     request(3, "tools/list"))
    assert result(answers, 1)["isError"] is True and result(answers, 2)["isError"] is True
    assert len(result(answers, 3)["tools"]) == 5
    assert not Path(state.record).exists()                 # the server made no record
