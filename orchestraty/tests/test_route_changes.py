"""The live map's route changes: each session's HTTP routes against its base commit's, a
changed route counting under its old and its new method and path."""
from orch_engine.parsing import parse_file

from orchestraty.livemap import routes as live_routes

APP = "from fastapi import FastAPI\nfrom api import users\napp = FastAPI()\napp.include_router(users.router, prefix='/api')\n"
USERS = "from fastapi import APIRouter\nrouter = APIRouter()\n\n\n@router.get('/users/{uid}')\ndef get_user(uid):\n    return uid\n"


class Index:
    """The base commit's index, as far as the route changes read it."""

    def __init__(self, files):
        self.parsed_files = [parse_file(path, None, text=text) for path, text in files.items()]
        self.memo = {}


def route(method, path, handler="a.py::h"):
    return {"method": method, "path": path, "handler": handler, "file": "a.py", "line": 3, "framework": "fastapi",
            "confidence": "exact", "unresolved": []}


def test_a_changed_route_counts_under_its_old_and_its_new_method_and_path():
    base = [route("GET", "/users"), route("GET", "/gone", "a.py::gone")]
    head = [route("POST", "/users"), route("GET", "/new", "a.py::new")]
    view = live_routes.compare(base, head)
    assert {k: (c.kind, c.contract) for k, (c, _) in view.touched.items()} == {
        ("GET", "/users"): ("changed", "breaking"), ("POST", "/users"): ("changed", "breaking"),
        ("GET", "/gone"): ("removed", "breaking"), ("GET", "/new"): ("added", "compatible")}
    [change] = [c for c in view.changes if c.kind == "changed"]
    assert change.as_dict()["to"]["method"] == "POST"


def changes(index, head):
    return [(c.kind, c.route["method"], c.route["path"], (c.to or {}).get("method"))
            for c in live_routes.compare(*live_routes.session_routes(index, head)).changes]


def test_a_sessions_routes_are_read_over_its_copy_of_the_code():
    index = Index({"api/__init__.py": "", "api/main.py": APP, "api/users.py": USERS})
    assert changes(index, {}) == []
    post = parse_file("api/users.py", None, text=USERS.replace("router.get", "router.post"))
    assert changes(index, {"api/users.py": post}) == [("changed", "GET", "/api/users/{uid}", "POST")]
    moved = parse_file("api/main.py", None, text=APP.replace("'/api'", "'/v2'"))     # a prefix in another file
    assert changes(index, {"api/main.py": moved}) == [("changed", "GET", "/api/users/{uid}", "GET")]
    assert changes(index, {"api/users.py": None}) == [("removed", "GET", "/api/users/{uid}", None)]
    # a file that does not parse, an edit half done, keeps its routes
    broken = parse_file("api/users.py", None, text=USERS.replace("(uid):", "(uid:"))
    assert broken.error and changes(index, {"api/users.py": broken}) == []
