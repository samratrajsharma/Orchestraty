"""orch ui --demo: the sandbox reaches its findings and notes, and leaves nothing behind."""
import http.client
import json
import os
import threading

import pytest

from orchestraty.ui import data, demo as sandbox, server as ui_server


@pytest.fixture
def demo(git):
    d = sandbox.Demo()
    yield d
    d.close()


def keys(d):
    a, b = d.sessions["A"], d.sessions["B"]
    return f"O3|a.py::charge|{a}|{b}|c.py", f"O6|a.py::charge|{a}"


def outputs(payload, n):
    return {o["label"]: o["text"] for o in payload["steps"][n - 1]["outputs"]}


def test_the_scenario_reaches_its_findings_and_notes(demo):
    a, b = demo.sessions["A"], demo.sessions["B"]
    o3, o6 = keys(demo)
    first = demo.next_step()                                                          # 1: both start
    assert first["next"] == 2 and first["steps"][0]["done"] and not first["steps"][1]["done"]
    [repo] = data.status_payload(demo.state)["repositories"]
    assert {s["id"] for s in repo["sessions"]} == {a, b} and {s["turn"] for s in repo["sessions"]} == {"idle"}
    demo.next_step()                                                                  # 2: B calls charge
    assert data.session_payload(demo.state, b)["changes"]["files"] == {"c.py": "A"}
    third = demo.next_step()                                                          # 3: A breaks it
    overlaps = [o["key"] for o in data.status_payload(demo.state)["repositories"][0]["overlaps"]]
    assert o3 in overlaps and o6 in overlaps
    [charge] = data.session_payload(demo.state, a)["changes"]["symbols"]
    assert (charge["symbol"], charge["contract"]) == ("a.py::charge", "breaking")
    assert "to B (wt-b), before its next edit of c.py: written (O3)" in outputs(third, 3)[
        "Notes the coordinator wrote in advance, each waiting in its slot"]

    fourth = demo.next_step()                                                         # 4: B hears
    [printed] = [t for label, t in outputs(fourth, 4).items() if label.startswith("What B’s PostToolUse hook printed")]
    out = json.loads(printed)["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUse"
    text = out["additionalContext"]
    assert text.startswith("Orchestraty: another Claude Code session (worktree wt-a, branch feat-a) changed the "
                           "contract of charge in a.py: parameter currency can no longer be passed by position.")
    assert "c.py uses it: refund (line 5)" in text and "passing the arguments by name works with both forms" in text
    told = {(n["finding_key"], n["kind"]): n for n in data.notes_payload(demo.state, session=b)["notes"]}
    assert told[(o3, "next")]["state"] == "delivered" and told[(o3, "next")]["delivered"] is not None
    assert told[(o3, "edit")]["state"] == "delivered" and told[(o3, "edit")]["heard_with"] == "next"

    fifth = demo.next_step()                                                          # 5: A's Stop
    [printed] = [t for label, t in outputs(fifth, 5).items() if label.startswith("What A’s Stop hook printed")]
    stop = json.loads(printed)["hookSpecificOutput"]
    assert stop["hookEventName"] == "Stop"
    assert "These callers still use it and no session has updated them: pay in b.py (line 5)" in stop["additionalContext"]
    note = {(n["finding_key"], n["kind"]): n for n in data.notes_payload(demo.state, session=a)["notes"]}[(o6, "stop")]
    assert note["state"] == "delivered" and note["time_to_awareness_ms"] is not None

    sixth = demo.next_step()                                                          # 6: the push line
    pushed = outputs(sixth, 6)["git push origin feat-a, in wt-a: what git printed, exactly"]
    assert pushed.startswith("orchestraty: 1 caller(s) of charge still use its old form (pay). Run: orch status")
    [line] = data.push_payload(demo.state)["push"]
    assert line["session"] == a and os.path.normcase(line["worktree"]) == os.path.normcase(demo.worktrees["A"])

    last = demo.next_step()                                                           # 7: B passes it by name
    assert outputs(last, 7)["The overlap between A and B in c.py"] == "resolved"
    assert data.finding_payload(demo.state, o3)["finding"]["state"] == "resolved"
    assert o3 not in [o["key"] for o in data.status_payload(demo.state)["repositories"][0]["overlaps"]]
    told_a = {(n["finding_key"], n["kind"]): n for n in data.notes_payload(demo.state, session=a)["notes"]}
    assert told_a[(o3, "next")]["state"] == "withdrawn"                              # A never needed it after all
    assert last["next"] is None and all(s["done"] for s in last["steps"])
    with pytest.raises(sandbox.Finished):
        demo.next_step()


def test_the_sandbox_is_its_own_and_is_removed(git, state_root, tmp_path):
    d = sandbox.Demo()
    root = d.root
    try:
        assert os.path.isdir(root) and os.path.commonpath([root, d.state.root]) == root
        assert os.path.isfile(d.state.hook_script) and not os.path.exists(d.state.launch)   # never starts one itself
        for _ in sandbox.STEPS:
            d.next_step()
    finally:
        d.close()
    d.close()                                                                          # twice is fine
    assert not os.path.exists(root)
    assert not os.path.exists(state_root)                     # the state folder this user would have: untouched
    assert not (tmp_path / "claude-config").exists()          # no Claude Code settings written


def test_the_page_drives_the_demo(git):
    d = sandbox.Demo()
    s = ui_server.Server(d.state, 0)
    s.demo = d
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()

    def ask(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=120)
        headers = {"Cookie": f"{s.cookie}={s.secret}"}
        if method == "POST":
            headers.update({"Content-Type": "application/json", "X-Orch-UI": "1", "Origin": f"http://127.0.0.1:{s.port}"})
        try:
            conn.request(method, path, body=json.dumps(body or {}).encode() if method == "POST" else None, headers=headers)
            res = conn.getresponse()
            return res.status, json.loads(res.read())
        finally:
            conn.close()
    try:
        code, status = ask("GET", "/api/status")
        assert code == 200 and status["ui"]["demo"] is True and status["coordinator"]["running"] is True
        code, before = ask("GET", "/api/demo")
        assert code == 200 and before["next"] == 1 and before["folder"] == d.root and len(before["steps"]) == 7
        code, after = ask("POST", "/api/demo/next")
        assert code == 200 and after["next"] == 2 and after["steps"][0]["done"]
        code, out = ask("POST", "/api/config", {"wake": "on"})
        assert code == 200 and out["wake"] is True and out["lines"] == ["wake: on", ui_server.DEMO_WAKE]
        for _ in range(6):
            assert ask("POST", "/api/demo/next")[0] == 200
        code, out = ask("POST", "/api/demo/next")
        assert code == 409 and out["error"] == "the scenario is done"
    finally:
        s.shutdown()
        s.server_close()
        t.join(10)
        d.close()
    assert not os.path.exists(d.root)


def test_orch_ui_demo_removes_its_folder_when_it_stops(git, monkeypatch, capsys):
    seen = {}

    def fake_serve(s, browser=True, out=None):
        seen["root"] = s.demo.root
        seen["state"] = s.state.root
        s.demo.next_step()
        s.server_close()
        return 0
    monkeypatch.setattr(ui_server, "serve", fake_serve)
    assert ui_server.main(port=0, browser=False, demo=True) == 0
    assert os.path.commonpath([seen["root"], seen["state"]]) == seen["root"]
    assert not os.path.exists(seen["root"])


def test_no_demo_endpoints_outside_the_demo(state_root):
    from orchestraty.paths import State
    s = ui_server.Server(State(str(state_root)), 0)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        for method in ("GET", "POST"):
            conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=30)
            path = "/api/demo" if method == "GET" else "/api/demo/next"
            conn.request(method, path, body=b"{}" if method == "POST" else None,
                         headers={"Cookie": f"{s.cookie}={s.secret}", "Content-Type": "application/json",
                                  "X-Orch-UI": "1", "Origin": f"http://127.0.0.1:{s.port}"})
            res = conn.getresponse()
            assert res.status == 404 and "orch ui --demo" in json.loads(res.read())["error"]
            conn.close()
    finally:
        s.shutdown()
        s.server_close()
        t.join(10)
