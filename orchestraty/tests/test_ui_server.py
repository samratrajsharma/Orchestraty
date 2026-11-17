"""orch ui's server: on port 0, in a thread, with a temporary state folder."""
import hashlib
import html.parser
import http.client
import json
import os
import re
import socket
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

from orchestraty import cli, config
from orchestraty.coordinator.record import Record
from orchestraty.paths import State
from orchestraty.rules.overlap import Finding
from orchestraty.ui import data, server as ui_server

S = 1_000_000_000
STATIC = Path(ui_server.STATIC_DIR)


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def srv(state):
    s = ui_server.Server(state, 0)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()
    t.join(10)


def ask(s, method, path, headers=None, body=None, cookie=True):
    """(status, {header: value}, body bytes) for one request on a fresh connection."""
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=30)
    sent = dict(headers or {})
    if cookie:
        sent.setdefault("Cookie", f"{s.cookie}={s.secret}")
    try:
        conn.request(method, path, body=body, headers=sent)
        res = conn.getresponse()
        return res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
    finally:
        conn.close()


def change(s, payload, **headers):
    sent = {"Content-Type": "application/json", "X-Orch-UI": "1", "Origin": f"http://127.0.0.1:{s.port}"}
    sent.update({k.replace("_", "-"): v for k, v in headers.items()})
    for k in [k for k, v in sent.items() if v is None]:
        del sent[k]
    return ask(s, "POST", "/api/config", sent, json.dumps(payload).encode())


def fill(state, tmp_path):
    common = str(tmp_path / "repo" / ".git")
    config.watch(state, common)
    rec = Record(state.record)
    with rec.transaction():
        rid = rec.repo_id(common)
        for sid in ("A", "B"):
            wt = str(tmp_path / ("wt-" + sid.lower()))
            rec.put_session({"id": sid, "agent": "claude", "repo_id": rid, "cwd": wt, "worktree": wt, "branch": "b",
                             "base_commit": "abc", "base_guessed": 0, "started": 1 * S, "last_seen": 2 * S,
                             "state": "live", "turn": "working", "note": ""})
            rec.add_event({"ts": 2 * S, "agent": "claude", "event": "Stop", "session_id": sid})
        f = Finding("O3|a.py::charge|A|B|c.py", "O3", "strong", ("A", "B"), changer="A", receiver="B",
                    symbol="a.py::charge", file="c.py", detail={"uses": [{"symbol": "c.py::use", "lines": [5]}]})
        rec.put_finding(rid, f, 3 * S)
        rec.add_note(f.key, "B", "edit", "notes/B/edit/x.json", "a note", 3 * S, cause_ts=2 * S)
    rec.close()


# --------------------------------------------------------------------------------------- #
# Who may ask
# --------------------------------------------------------------------------------------- #
def test_it_listens_on_127_0_0_1_only(srv):
    assert srv.server_address[0] == "127.0.0.1" and srv.port > 0
    assert srv.url == f"http://127.0.0.1:{srv.port}/?token={srv.token}" and len(srv.token) >= 40


@pytest.mark.parametrize("path", ["/", "/app.js", "/app.css", "/favicon.svg", "/api/status", "/api/health", "/nope"])
def test_every_request_needs_the_cookie(srv, path):
    code, headers, body = ask(srv, "GET", path, cookie=False)
    assert code == 403 and b"open the link" in body
    code, _, _ = ask(srv, "GET", path, {"Cookie": f"{srv.cookie}=not-the-secret"})
    assert code == 403
    code, _, _ = ask(srv, "GET", path, {"Cookie": f"orch_ui_1={srv.secret}"})          # another server's name
    assert code == 403


OTHER_APPS = ['prefs={"theme":"dark"}', "cart=a b", "p=C:\\x", "n=caf\u00e9", "path=/", "expires=1", 'x="abc',
              "novalue", "=v", "orch_ui_1=another-server-s"]


@pytest.mark.parametrize("other", OTHER_APPS)
def test_another_app_s_cookie_on_127_0_0_1_does_not_hide_ours(srv, other):
    ours = f"{srv.cookie}={srv.secret}"
    for cookie in (f"{other}; {ours}", f"{ours}; {other}"):
        for path in ("/", "/app.js", "/api/health"):
            assert ask(srv, "GET", path, {"Cookie": cookie}, cookie=False)[0] == 200, (cookie, path)
    assert ask(srv, "GET", "/api/status", {"Cookie": f"{other}; {srv.cookie}=nope"}, cookie=False)[0] == 403


def test_ours_counts_in_any_cookie_header_and_beside_a_stale_one(srv):
    ours = f"{srv.cookie}={srv.secret}"
    raw = (f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\nCookie: prefs={{\"a\":1}}\r\n"
           f"Cookie: {ours}\r\nConnection: close\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", srv.port), timeout=30) as sock:
        sock.sendall(raw)
        reply = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            reply += chunk
    assert reply.startswith(b"HTTP/1.1 200")
    assert ask(srv, "GET", "/api/health", {"Cookie": f"{ours}; {srv.cookie}=stale"}, cookie=False)[0] == 200
    for cookie in (f"x{srv.cookie}={srv.secret}", f"{srv.cookie}x={srv.secret}", f"other={srv.secret}",
                   f"{srv.cookie}={srv.secret}x", f"{srv.cookie}", f"{srv.cookie}=", f"{srv.cookie}={srv.secret[:-1]}"):
        assert ask(srv, "GET", "/api/health", {"Cookie": cookie}, cookie=False)[0] == 403, cookie


def test_the_link_still_redirects_a_browser_that_has_ours_beside_another_app_s(srv):
    assert ask(srv, "GET", "/?token=" + srv.token, cookie=False)[0] == 303
    code, headers, _ = ask(srv, "GET", "/?token=" + srv.token, {"Cookie": f'prefs={{"a":1}}; {srv.cookie}={srv.secret}'},
                           cookie=False)
    assert code == 303 and "set-cookie" not in headers


def test_the_link_sets_the_cookie_and_works_once(srv):
    code, headers, _ = ask(srv, "GET", "/?token=" + srv.token, cookie=False)
    assert code == 303 and headers["location"] == "/" and headers["cache-control"] == "no-store"
    cookie = headers["set-cookie"]
    assert cookie.startswith(f"{srv.cookie}={srv.secret};")
    assert {p.strip() for p in cookie.split(";")[1:]} == {"HttpOnly", "SameSite=Strict", "Path=/"}
    value = cookie.split(";")[0]
    code, headers, body = ask(srv, "GET", "/", {"Cookie": value}, cookie=False)
    assert code == 200 and headers["content-type"].startswith("text/html") and b"<title>Orchestraty</title>" in body
    code, _, body = ask(srv, "GET", "/?token=" + srv.token, cookie=False)                # used: no second browser
    assert code == 403 and b"used already" in body
    code, headers, _ = ask(srv, "GET", "/?token=" + srv.token, {"Cookie": value}, cookie=False)
    assert code == 303 and "set-cookie" not in headers                                   # the same browser again


def test_a_wrong_token_sets_nothing(srv):
    for token in ("", "x", srv.token[:-1], srv.token + "x", "é" * 43):
        code, headers, body = ask(srv, "GET", "/?token=" + quote(token), cookie=False)
        assert code == 403 and "set-cookie" not in headers and b"not the link" in body
    code, headers, _ = ask(srv, "GET", "/?token=" + srv.token, cookie=False)
    assert code == 303 and "set-cookie" in headers                                       # still good once


@pytest.mark.parametrize("host", ["evil.example:{port}", "127.0.0.1:1", "localhost", "127.0.0.1", "127.0.0.2:{port}",
                                  "[::1]:{port}", "localhost.:{port}", "0.0.0.0:{port}"])
def test_any_other_host_is_refused(srv, host):
    code, _, body = ask(srv, "GET", "/api/status", {"Host": host.format(port=srv.port)})
    assert code == 403 and b"answers only to" in body
    code, _, _ = ask(srv, "GET", "/?token=" + srv.token, {"Host": host.format(port=srv.port)}, cookie=False)
    assert code == 403 and not srv._redeemed


def test_localhost_with_the_port_is_this_server(srv):
    assert ask(srv, "GET", "/api/status", {"Host": f"localhost:{srv.port}"})[0] == 200
    assert ask(srv, "GET", "/api/status", {"Host": f"LOCALHOST:{srv.port}"})[0] == 200


def test_two_host_headers_are_refused(srv):
    raw = (f"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\nHost: evil.example\r\n"
           f"Cookie: {srv.cookie}={srv.secret}\r\nConnection: close\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", srv.port), timeout=30) as sock:
        sock.sendall(raw)
        reply = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            reply += chunk
    assert reply.startswith(b"HTTP/1.1 403")


def test_a_change_needs_the_cookie_the_header_and_the_origin(srv, state):
    assert change(srv, {"steer": "on"}, X_Orch_UI=None)[0] == 403
    assert change(srv, {"steer": "on"}, X_Orch_UI="yes")[0] == 403
    assert change(srv, {"steer": "on"}, Origin=None)[0] == 403
    assert change(srv, {"steer": "on"}, Origin="http://evil.example")[0] == 403
    assert change(srv, {"steer": "on"}, Origin=f"http://127.0.0.1:{srv.port + 1}")[0] == 403
    assert change(srv, {"steer": "on"}, Origin="null")[0] == 403
    assert change(srv, {"steer": "on"}, Cookie=f"{srv.cookie}=nope")[0] == 403
    assert config.load(state)["steer"] is False                                          # none got through
    code, headers, body = change(srv, {"steer": "on"})
    assert code == 200 and headers["cache-control"] == "no-store"
    out = json.loads(body)
    assert out["steer"] is True and out["lines"] == ["steer: on"] and out["error"] is None
    assert config.load(state)["steer"] is True
    code, _, _ = change(srv, {"steer": "off"}, Host=f"localhost:{srv.port}", Origin=f"http://localhost:{srv.port}")
    assert code == 200 and config.load(state)["steer"] is False


def test_a_change_is_checked_as_orch_config_checks_it(srv, state):
    for bad in ({}, {"steer": "maybe"}, {"steer": True}, {"steer": "on", "wake": "on"}, {"idle_seconds": "on"}, []):
        code, _, body = change(srv, bad)
        assert code == 400 and json.loads(body)["schema"] == data.SCHEMA, bad
    assert ask(srv, "POST", "/api/config", {"X-Orch-UI": "1", "Origin": f"http://127.0.0.1:{srv.port}",
                                           "Content-Type": "text/plain"}, b'{"steer": "on"}')[0] == 415
    assert ask(srv, "POST", "/api/config", {"X-Orch-UI": "1", "Origin": f"http://127.0.0.1:{srv.port}",
                                           "Content-Type": "application/json"}, b"{nope")[0] == 400
    big = json.dumps({"steer": "on", "x": "y" * 5000}).encode()
    assert ask(srv, "POST", "/api/config", {"X-Orch-UI": "1", "Origin": f"http://127.0.0.1:{srv.port}",
                                           "Content-Type": "application/json"}, big)[0] == 413
    assert config.load(state) == config.DEFAULTS
    assert ask(srv, "GET", "/api/config?steer=on")[0] == 200 and config.load(state)["steer"] is False   # GET changes nothing


def test_wake_goes_through_orch_config_and_its_hooks(srv, state):
    code, _, body = change(srv, {"wake": "on"})
    assert code == 200 and json.loads(body)["lines"] == ["wake: on", "Run orch init to install the hooks; the wake "
                                                         "hook comes with them."]
    assert cli.main(["init", "--yes", "--no-git-hooks"]) == 0          # in the test's own empty folder
    settings = Path(config.load(state)["settings"])
    code, _, body = change(srv, {"wake": "off"})
    out = json.loads(body)
    assert code == 200 and out["wake"] is False and out["lines"][1].startswith("Updated the hooks in")
    stop = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["Stop"]
    assert [e["hooks"][0]["args"][-1] for e in stop] == ["Stop"]       # the wake hook came out


def act(s, path, payload):
    """(status, decoded body) for one change the page sends to `path`."""
    code, _, body = ask(s, "POST", path, {"Content-Type": "application/json", "X-Orch-UI": "1",
                                          "Origin": f"http://127.0.0.1:{s.port}"}, json.dumps(payload).encode())
    return code, json.loads(body)


def spooled(state, event):
    """The lines of orch's own spool with this event, oldest first."""
    out = []
    for path in sorted((Path(state.spool) / "orch").glob("*.jsonl")):
        out += [line for line in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if line["event"] == event]
    return out


def live_session(state, repo, git, tmp_path, sid="A"):
    """Session `sid` live in a worktree of the watched `repo`, as the coordinator records it."""
    common = str(repo / ".git")
    config.watch(state, common)
    wt = tmp_path / ("wt-" + sid.lower())
    git(repo, "worktree", "add", "-q", "-b", "feat-" + sid.lower(), str(wt))
    rec = Record(state.record)
    with rec.transaction():
        rid = rec.repo_id(common)
        rec.put_session({"id": sid, "agent": "claude", "repo_id": rid, "cwd": str(wt), "worktree": str(wt),
                         "branch": "feat-" + sid.lower(), "base_commit": "abc", "base_guessed": 0, "started": 1 * S,
                         "last_seen": 2 * S, "state": "live", "turn": "working", "note": ""})
    rec.close()
    return rid, common


def test_a_label_from_the_page_is_spooled_as_orch_label_spools_it(srv, state, tmp_path):
    fill(state, tmp_path)
    key = "O3|a.py::charge|A|B|c.py"
    code, out = act(srv, "/api/label", {"finding": key, "verdict": "harmless", "why": "tests cover it"})
    assert code == 200 and out["verdict"] == "harmless" and out["lines"][0] == "Labelled harmless."
    [line] = spooled(state, "label")
    assert (line["finding"], line["verdict"], line["why"]) == (key, "harmless", "tests cover it")
    for bad in ({"finding": key, "verdict": "maybe"}, {"finding": key}, {"finding": "", "verdict": "real"},
                {"finding": key, "verdict": "real", "why": 3}, {"finding": key, "verdict": "real", "x": 1}):
        assert act(srv, "/api/label", bad)[0] == 400, bad
    assert act(srv, "/api/label", {"finding": "O1|nope|A|B", "verdict": "real"})[0] == 404
    assert len(spooled(state, "label")) == 1                           # nothing else got through
    rec = Record(state.record)
    with rec.transaction():
        rec.add_label(key, "harmless", "tests cover it", 4 * S)
    rec.close()
    code, _, body = ask(srv, "GET", "/api/findings")
    [f] = json.loads(body)["findings"]
    assert f["label"] == {"verdict": "harmless", "why": "tests cover it", "at": data.iso(4 * S)}


def test_a_claim_from_the_page_resolves_its_target_in_the_session_s_worktree(srv, state, repo, git, tmp_path):
    live_session(state, repo, git, tmp_path)
    code, out = act(srv, "/api/claims", {"session": "A", "target": "a.py::charge", "minutes": 30})
    assert code == 200 and out["claimed"]["what"] == "charge in a.py" and out["lines"][0] == (
        "Claimed charge in a.py for 30 minutes.")
    code, out = act(srv, "/api/claims", {"session": "A", "target": "b.py", "hard": True})
    assert code == 200 and out["claimed"]["minutes"] == config.DEFAULTS["claim_minutes"]
    lines = spooled(state, "claim")
    assert [(x["session_id"], x["kind"], x["target"], x["file"], x["hard"], x["minutes"]) for x in lines] == [
        ("A", "symbol", "a.py::charge", "a.py", False, 30),
        ("A", "file", "b.py", "", True, config.DEFAULTS["claim_minutes"])]
    assert all(x["repo"] == str(repo / ".git") for x in lines)
    code, out = act(srv, "/api/claims", {"session": "A", "target": "missing.py"})
    assert code == 400 and out["error"]
    for bad in ({"session": "A"}, {"session": "A", "target": "a.py", "minutes": 0},
                {"session": "A", "target": "a.py", "minutes": True}, {"session": "A", "target": "a.py", "hard": "yes"}):
        assert act(srv, "/api/claims", bad)[0] == 400, bad
    assert act(srv, "/api/claims", {"session": "Z", "target": "a.py"})[0] == 404
    assert len(spooled(state, "claim")) == 2


def test_a_release_from_the_page_ends_the_claim_with_that_number(srv, state, repo, git, tmp_path):
    rid, common = live_session(state, repo, git, tmp_path)
    rec = Record(state.record)
    with rec.transaction():
        now = int(__import__("time").time() * S)
        cid = rec.add_claim(rid, "A", "file", "a.py", "", False, now, now + 3600 * S)
    rec.close()
    code, out = act(srv, "/api/release", {"id": cid})
    assert code == 200 and out["released"] == cid and out["lines"][0] == "Released a.py."
    [line] = spooled(state, "release")
    assert (line["id"], line["repo"]) == (cid, common)
    assert act(srv, "/api/release", {"id": cid + 1})[0] == 404
    for bad in ({}, {"id": "1"}, {"id": True}, {"id": 0}, {"id": 1, "x": 2}):
        assert act(srv, "/api/release", bad)[0] == 400, bad


def test_the_page_runs_doctor_s_checks_but_not_in_the_demo(srv, monkeypatch):
    from orchestraty import doctor
    seen = []

    def checks(state, cwd, index=True, say=None):
        seen.append(index)
        yield doctor.ok("Python 3.13")
        yield doctor.problem("coordinator: exited with 3", "see the log")
    monkeypatch.setattr(doctor, "checks", checks)
    code, out = act(srv, "/api/doctor", {})
    assert code == 200 and seen == [False] and out["problems"] == 1
    assert out["checks"] == [{"kind": "ok", "text": "Python 3.13", "fix": ""},
                             {"kind": "fix", "text": "coordinator: exited with 3", "fix": "see the log"}]
    srv.demo = object()
    code, out = act(srv, "/api/doctor", {})
    assert code == 200 and out["checks"] == [] and out["lines"] == [ui_server.DEMO_DOCTOR] and seen == [False]


# --------------------------------------------------------------------------------------- #
# What it answers
# --------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("path,ctype", [("/", "text/html"), ("/app.css", "text/css"), ("/app.js", "text/javascript"),
                                        ("/mark.svg", "image/svg+xml"), ("/favicon.svg", "image/svg+xml"),
                                        ("/DoppioOne-Regular.woff2", "font/woff2"), ("/DoppioOne-OFL.txt", "text/plain")])
def test_static_files_come_with_the_security_headers(srv, path, ctype):
    code, headers, body = ask(srv, "GET", path)
    assert code == 200 and headers["content-type"].startswith(ctype)
    assert headers["content-security-policy"] == ui_server.CSP
    assert headers["x-content-type-options"] == "nosniff" and headers["referrer-policy"] == "no-referrer"
    name = ui_server.STATIC[path][0]
    assert body == (STATIC / name).read_bytes()


def test_every_answer_has_the_security_headers_and_the_api_is_never_stored(srv):
    for method, path, cookie in (("GET", "/api/status", True), ("GET", "/api/nope", True), ("GET", "/nope", True),
                                 ("GET", "/api/status", False), ("PUT", "/api/status", True), ("POST", "/api/nope", True)):
        code, headers, _ = ask(srv, method, path, {"X-Orch-UI": "1", "Origin": f"http://127.0.0.1:{srv.port}",
                                                   "Content-Type": "application/json"} if method == "POST" else None,
                               b"{}" if method == "POST" else None, cookie=cookie)
        assert headers["content-security-policy"] == ui_server.CSP, (method, path)
        assert headers["x-content-type-options"] == "nosniff" and headers["referrer-policy"] == "no-referrer"
        if path.startswith("/api/"):
            assert headers["cache-control"] == "no-store", (method, path, code)


@pytest.mark.parametrize("path", ["/../server.py", "/static/../server.py", "/..%2fserver.py", "/%2e%2e/%2e%2e/etc/passwd",
                                  "/app.js/../server.py", "//etc/passwd", "/static/app.js", "/static/", "/server.py",
                                  "/data.py", "/ui/server.py", "/index.html/", "/app.js%00.html", "/.%2e/server.py",
                                  "/..\\server.py", "/app.js/", "/APP.JS"])
def test_only_the_listed_files_are_served(srv, path):
    code, _, body = ask(srv, "GET", path)
    assert code == 404 and b"import" not in body and b"<html" not in body


def test_the_api_answers_with_a_schema(srv, state, tmp_path):
    fill(state, tmp_path)
    for path in ("/api/status", "/api/status?ended=1", "/api/sessions/A", "/api/findings", "/api/findings?state=all",
                 "/api/findings/" + "O3%7Ca.py%3A%3Acharge%7CA%7CB%7Cc.py", "/api/notes?session=B&kind=edit&state=pending",
                 "/api/push", "/api/health", "/api/config"):
        code, headers, body = ask(srv, "GET", path)
        out = json.loads(body)
        assert code == 200 and headers["content-type"] == "application/json; charset=utf-8", path
        assert out["schema"] == 1, path
    status = json.loads(ask(srv, "GET", "/api/status")[2])
    assert status["ui"] == {"version": cli.__version__, "demo": False}
    assert [s["id"] for s in status["repositories"][0]["sessions"]] == ["A", "B"]
    assert json.loads(ask(srv, "GET", "/api/sessions/B")[2])["notes_received"][0]["text"] == "a note"
    assert json.loads(ask(srv, "GET", "/api/findings/O3%7Ca.py%3A%3Acharge%7CA%7CB%7Cc.py")[2])["finding"]["rule"] == "O3"
    for path in ("/api/sessions/nobody", "/api/findings/nothing", "/api/other"):
        code, _, body = ask(srv, "GET", path)
        assert code == 404 and json.loads(body)["schema"] == 1
    for path in ("/api/status?ended=2", "/api/findings?state=gone", "/api/notes?kind=email", "/api/notes?state=lost"):
        code, _, body = ask(srv, "GET", path)
        assert code == 400 and "must be one of" in json.loads(body)["error"]


def test_an_unchanged_answer_gets_a_304(srv, state, tmp_path):
    fill(state, tmp_path)
    for path in ("/api/status", "/api/sessions/A", "/api/notes", "/app.js"):
        code, headers, _ = ask(srv, "GET", path)
        tag = headers["etag"]
        code, again, body = ask(srv, "GET", path, {"If-None-Match": tag})
        assert code == 304 and body == b"" and again["etag"] == tag, path
        assert again["content-security-policy"] == ui_server.CSP
        assert ask(srv, "GET", path, {"If-None-Match": '"other", ' + tag})[0] == 304
        assert ask(srv, "GET", path, {"If-None-Match": '"other"'})[0] == 200
    tag = ask(srv, "GET", "/api/config")[1]["etag"]
    assert change(srv, {"steer": "on"})[0] == 200
    assert ask(srv, "GET", "/api/config", {"If-None-Match": tag})[0] == 200                # it changed


def test_a_status_that_only_grew_older_keeps_its_etag():
    payload = {"schema": 1, "repositories": [{"sessions": [{"id": "A", "last_event": "x", "seconds_since_last_event": 3}]}]}
    older = json.loads(json.dumps(payload))
    older["repositories"][0]["sessions"][0]["seconds_since_last_event"] = 4
    newer = json.loads(json.dumps(payload))
    newer["repositories"][0]["sessions"][0]["last_event"] = "y"

    def tag(p):
        return ui_server._etag(ui_server._dump(ui_server._without(p, ui_server.VOLATILE)), weak=True)
    assert tag(payload) == tag(older) and tag(payload) != tag(newer)


def test_the_record_is_never_written(srv, state, tmp_path):
    fill(state, tmp_path)
    before = Path(state.record).read_bytes()
    stamp = os.stat(state.record).st_mtime_ns
    rec = Record(state.record, readonly=True)
    rows = [list(map(tuple, rec.db.execute(f"SELECT * FROM {t}"))) for t in ("sessions", "findings", "notes", "events")]
    rec.close()
    for _ in range(3):
        for path in ("/api/status", "/api/status?ended=1", "/api/sessions/A", "/api/findings?state=all",
                     "/api/findings/O3%7Ca.py%3A%3Acharge%7CA%7CB%7Cc.py", "/api/notes", "/api/push", "/api/health",
                     "/api/config"):
            assert ask(srv, "GET", path)[0] == 200
        assert change(srv, {"steer": "on"})[0] == 200
    assert Path(state.record).read_bytes() == before and os.stat(state.record).st_mtime_ns == stamp
    rec = Record(state.record, readonly=True)
    assert [list(map(tuple, rec.db.execute(f"SELECT * FROM {t}"))) for t in ("sessions", "findings", "notes", "events")] == rows
    rec.close()
    assert sorted(p.name for p in Path(state.root).iterdir() if p.name.startswith("record")) == ["record.sqlite"]


def test_without_a_record_none_is_made(srv, state):
    for path in ("/api/status", "/api/sessions/A", "/api/findings", "/api/notes", "/api/push", "/api/health"):
        assert ask(srv, "GET", path)[0] in (200, 404)
    assert not os.path.exists(state.record)


# --------------------------------------------------------------------------------------- #
# The page's files
# --------------------------------------------------------------------------------------- #
class _Refs(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("src", "href", "action", "srcset", "data", "poster", "xlink:href") and value:
                self.refs.append((tag, value))


def test_the_page_refers_only_to_its_own_files():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    parser = _Refs()
    parser.feed(page)
    files = [v for tag, v in parser.refs if not v.startswith("#")]
    assert sorted(files) == ["/app.css", "/app.js", "/favicon.svg"]
    for ref in files:
        assert ref in ui_server.STATIC and (STATIC / ui_server.STATIC[ref][0]).is_file()
    assert "<style" not in page and " style=" not in page and "<script>" not in page        # nothing inline
    assert sorted(p.name for p in STATIC.iterdir()) == sorted(name for name, _ in ui_server.STATIC.values())


def test_the_page_makes_no_request_elsewhere():
    for name in ("index.html", "app.css", "app.js", "favicon.svg", "mark.svg"):
        text = (STATIC / name).read_text(encoding="utf-8")
        urls = re.findall(r"(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s'\")]*", text)
        assert set(urls) <= {"http://www.w3.org/2000/svg"}, (name, urls)        # an XML namespace, never fetched
        assert "@import" not in text
        assert "url(" not in text.replace("data:", "").replace('url("/DoppioOne-Regular.woff2")', "")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js.split("\n", 3)[3] and "insertAdjacentHTML" not in js and "eval(" not in js
    assert set(re.findall(r"""['"](/api/[a-z/]+)""", js)) <= {"/api/status?ended=", "/api/demo", "/api/push",
                                                            "/api/sessions/", "/api/findings/", "/api/notes?",
                                                            "/api/config", "/api/health", "/api/demo/next"} | {
        "/api/status", "/api/sessions", "/api/findings", "/api/notes", "/api/label", "/api/claims", "/api/release",
        "/api/doctor"}


def test_the_mark_and_the_favicon_are_the_brand_s():
    mark = (STATIC / "mark.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 96 96"' in mark and 'd="M21.5 24.01 L29 37 L14 37"' in mark and 'd="M74.5 71.99 L67 59 L82 59"' in mark
    assert mark.count('stroke="#38000A"') == 4 and 'fill="#CD1C18" cx="48" cy="48" r="9"' in mark
    favicon = (STATIC / "favicon.svg").read_text(encoding="utf-8")
    assert favicon.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="14 18 68 60">')
    assert "@media (prefers-color-scheme:dark){.i{stroke:#FFF3F0}.a{fill:#FFA896}}" in favicon
    assert hashlib.sha256(mark.encode()).hexdigest() != hashlib.sha256(favicon.encode()).hexdigest()
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "M19 18 L50.2 36" not in mark + favicon + page            # the earlier mark never sits beside the new name
    assert page.count('class="go ') == 4 and 'class="logo logo-mark"' in page


def test_the_page_is_set_in_its_own_typeface_in_one_weight():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "@font-face" in css and 'font-family: "Doppio One"' in css and "font-synthesis: none" in css
    assert "b, strong, h1, h2, h3, th, dt { font-weight: 400; }" in css
    assert set(re.findall(r"font-weight: *(\w+)", css)) <= {"400", "500"}   # 500 is the command type, in the mono face
    assert (STATIC / "DoppioOne-Regular.woff2").read_bytes()[:4] == b"wOF2"
    assert "SIL OPEN FONT LICENSE" in (STATIC / "DoppioOne-OFL.txt").read_text(encoding="utf-8").upper()


# --------------------------------------------------------------------------------------- #
# orch ui
# --------------------------------------------------------------------------------------- #
def test_orch_ui_serves_the_state_folder_until_stopped(state, monkeypatch, capsys):
    seen = {}

    def fake_serve(s, browser=True, out=None):
        seen.update(address=s.server_address, browser=browser, root=s.state.root)
        s.server_close()
        return 0
    monkeypatch.setattr(ui_server, "serve", fake_serve)
    assert cli.main(["ui", "--no-browser"]) == 0
    assert seen["address"][0] == "127.0.0.1" and seen["browser"] is False and seen["root"] == state.root


def test_orch_ui_refuses_a_bad_port(capsys):
    for bad in ("70000", "-1", "http"):
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["ui", "--port", bad])
        assert exit_info.value.code == 2
    assert "not a port number" in capsys.readouterr().err


def test_a_port_in_use_is_reported(state, capsys):
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    try:
        assert ui_server.main(port=taken.getsockname()[1], browser=False) == 2
        assert "cannot listen on 127.0.0.1" in capsys.readouterr().err
    finally:
        taken.close()


def test_serve_prints_the_link_and_stops_on_ctrl_c(state, monkeypatch):
    s = ui_server.Server(state, 0)

    def interrupted(poll_interval=0.5):
        raise KeyboardInterrupt
    monkeypatch.setattr(s, "serve_forever", interrupted)
    lines = []

    class Out:
        def write(self, text):
            lines.append(text)

        def flush(self):
            pass
    assert ui_server.serve(s, browser=False, out=Out()) == 0
    text = "".join(lines)
    assert s.url in text and "works once" in text and "stopped" in text
