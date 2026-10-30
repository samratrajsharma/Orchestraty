"""Routes in the live map and rule O5: two sessions that change the same HTTP route (its method
and path) both hear, when the engine is sure of the route on both sides; different routes, or a
route under a computed prefix, make no O5."""
import json
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import config, status
from orchestraty.capture.hook import take_slot
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.livemap import routes as live_routes
from orchestraty.paths import State
from orchestraty.rules import overlap

MAIN = ("from fastapi import FastAPI\nfrom app import users, items\nfrom app.config import settings\n\n"
        "app = FastAPI()\napp.include_router(users.router, prefix='/api')\napp.include_router(items.router, prefix='/api')\n"
        "app.include_router(users.legacy, prefix=settings.LEGACY)\n")
USERS = ("from fastapi import APIRouter\nrouter = APIRouter(prefix='/users')\nlegacy = APIRouter()\n\n\n"
         "@router.get('/{uid}')\ndef get_user(uid):\n    return uid\n\n\n"
         "@legacy.get('/old/{uid}')\ndef old_user(uid):\n    return uid\n")
ITEMS = ("from fastapi import APIRouter\nrouter = APIRouter(prefix='/items')\n\n\n"
         "@router.get('/{iid}')\ndef get_item(iid):\n    return iid\n")


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def two(state, tmp_path, git):
    """A FastAPI app in a watched repository, two worktrees with a hooked session in each, and
    a coordinator that has seen both and indexed their base commit."""
    repo = tmp_path / "api"
    (repo / "app").mkdir(parents=True)
    git(repo, "init", "-q")
    for name, text in (("__init__.py", ""), ("main.py", MAIN), ("users.py", USERS), ("items.py", ITEMS),
                       ("config.py", "settings = object()\n")):
        (repo / "app" / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    config.watch(state, str(repo / ".git"))
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    spool_event(state.root, "A", "SessionStart", wa)
    spool_event(state.root, "B", "SessionStart", wb)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    yield c, wa, wb
    c.close()


def edit(state, c, sid, top, rel, old, new):
    path = top / rel
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(path))
    c.step()
    c.livemap.indexes.wait(120)
    c._last_scan = None
    c.step()


def findings(c, rule):
    return [f for f in c.record.findings() if f["rule"] == rule]


def test_two_sessions_changing_one_route_both_hear(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "app/users.py", "@router.get('/{uid}')", "@router.post('/{uid}')")      # its method
    edit(state, c, "B", wb, "app/main.py", "users.router, prefix='/api'", "users.router, prefix='/v2'")  # its path
    [f] = findings(c, "O5")
    assert (f["key"], f["strength"], f["sessions"]) == ("O5|GET /api/users/{uid}|A|B", "strong", ["A", "B"])
    assert (f["symbol"], f["file"]) == ("app/users.py::get_user", "app/users.py")
    assert f["detail"]["changes"]["A"] == {"kind": "changed", "to": "POST /api/users/{uid}", "from": "",
                                           "handler": "app/users.py::get_user", "file": "app/users.py", "line": 6}
    assert f["detail"]["changes"]["B"]["to"] == "GET /v2/users/{uid}"
    assert not findings(c, "O1") and not findings(c, "O2")                 # two files, two functions
    told = {"A": ("that session changed it to GET /v2/users/{uid}, and you changed it to POST /api/users/{uid}", "wt-b"),
            "B": ("that session changed it to POST /api/users/{uid}, and you changed it to GET /v2/users/{uid}", "wt-a")}
    for sid, (what, other) in told.items():
        [note] = [n for n in c.record.notes(session_id=sid) if n["finding_key"] == f["key"]]
        assert note["kind"] == "next" and note["state"] == "written"
        assert note["text"] == (
            f"Orchestraty: another Claude Code session (worktree {other}, branch feat-{other[-1]}) is also changing "
            f"the HTTP route GET /api/users/{{uid}}, declared in app/users.py at line 6 (rule O5): {what}. Agree with "
            "that session on the route's method and path before either of you merges.")
        slot = slots.next_slot(state, sid)
        assert note["text"] in json.loads(Path(slot).read_bytes())["hookSpecificOutput"]["additionalContext"]
        assert take_slot(slot)
        c.step()
        assert c.record.note(note["id"])["state"] == "delivered"
    shown = status.render(status.collect(state))
    assert ("strong  O5  GET /api/users/{uid} (app/users.py): A changed it to POST /api/users/{uid}, "
            "B changed it to GET /v2/users/{uid}") in shown
    # A takes its change back: the route is B's alone again, and the finding is resolved
    edit(state, c, "A", wa, "app/users.py", "@router.post('/{uid}')", "@router.get('/{uid}')")
    assert not findings(c, "O5")


def test_both_changing_the_handler_of_one_route_hear_once(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "app/users.py", "@router.get('/{uid}')", "@router.post('/{uid}')")
    edit(state, c, "B", wb, "app/users.py", "@router.get('/{uid}')", "@router.put('/{uid}')")
    [f] = findings(c, "O5")
    assert f["symbol"] == "app/users.py::get_user"
    assert not findings(c, "O2")                                           # the route's finding says it
    for sid in ("A", "B"):
        assert [n["finding_key"] for n in c.record.notes(session_id=sid)] == [f["key"]]


def test_different_routes_make_no_o5(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "app/users.py", "@router.get('/{uid}')", "@router.post('/{uid}')")
    edit(state, c, "B", wb, "app/items.py", "@router.get('/{iid}')", "@router.delete('/{iid}')")
    assert [(ch.kind, ch.route["path"], ch.to["method"]) for ch in c.livemap.routes("B").changes] == [
        ("changed", "/api/items/{iid}", "DELETE")]
    assert not findings(c, "O5")


def test_a_route_under_a_computed_prefix_makes_no_o5(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "app/users.py", "@legacy.get('/old/{uid}')", "@legacy.put('/old/{uid}')")
    edit(state, c, "B", wb, "app/users.py", "@legacy.get('/old/{uid}')", "@legacy.get('/older/{uid}')")
    [change] = c.livemap.routes("A").changes
    assert (change.route["path"], change.route["confidence"]) == ("{?}/old/{uid}", "heuristic")
    assert change.route["unresolved"] == ["prefix settings.LEGACY (app/main.py line 8)"]
    assert not findings(c, "O5")
    assert findings(c, "O2")                                               # both changed old_user all the same


def test_a_file_that_does_not_parse_keeps_its_routes(state, two):
    c, wa, wb = two
    edit(state, c, "A", wa, "app/users.py", "@router.get('/{uid}')", "@router.post('/{uid}')")
    edit(state, c, "B", wb, "app/users.py", "def old_user(uid):", "def old_user(uid:")       # half an edit
    assert c.livemap.views["B"].head["app/users.py"].error
    assert c.livemap.routes("B").changes == [] and not findings(c, "O5")


def route(method, path, handler="a.py::h", confidence="exact"):
    return {"method": method, "path": path, "handler": handler, "file": "a.py", "line": 3, "framework": "fastapi",
            "confidence": confidence, "unresolved": [] if confidence == "exact" else ["prefix x (a.py line 1)"]}


def test_o5_needs_an_exact_route_on_both_sides():
    views = {"A": live_routes.compare([route("GET", "/x")], []),
             "B": live_routes.compare([route("GET", "/x")], [route("PUT", "/x")]),
             "C": live_routes.compare([route("GET", "/x", confidence="heuristic")], [])}
    found = list(overlap.same_route("A", "B", views.get))
    assert [(f.key, f.detail["changes"]["A"]["kind"], f.detail["changes"]["B"]["to"]) for f in found] == [
        ("O5|GET /x|A|B", "removed", "PUT /x")]
    assert list(overlap.same_route("A", "C", views.get)) == []
    assert list(overlap.same_route("A", "D", views.get)) == []              # D's routes are not known yet
