"""HTTP routes from the syntax tree (orch_engine.routes): FastAPI, Flask and Django, their
prefixes, routers, blueprints and includes across files, and how sure each route is."""
from __future__ import annotations

from orch_engine import routes
from orch_engine.insights import api_map


def table(idx):
    """{(method, path): (handler, "file:line", confidence)} of an index's routes."""
    return {(r["method"], r["path"]): (r["handler"], f"{r['file']}:{r['line']}", r["confidence"])
            for r in routes.for_index(idx)}


FASTAPI = {
    "app/__init__.py": "",
    "app/main.py": (
        "from fastapi import FastAPI\n"
        "from app.routers import users\n"
        "from app.routers.items import router as items_router\n"
        "from app import admin\n"
        "API = '/api'\n"
        "app = FastAPI()\n"
        "app.include_router(users.router, prefix=API + '/v1')\n"
        "app.include_router(items_router, prefix=f'{API}/shop')\n"
        "app.include_router(admin.panel)\n"
        "app.mount('/legacy', admin.old)\n"
        "\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return 'ok'\n"),
    "app/routers/__init__.py": "",
    "app/routers/users.py": (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/users', tags=['users'])\n"
        "\n"
        "@router.get('/{uid}')\n"
        "def get_user(uid: int):\n"
        "    return uid\n"
        "\n"
        "@router.api_route('', methods=['POST', 'put'])\n"
        "async def save():\n"
        "    return None\n"),
    "app/routers/items.py": (
        "import fastapi\n"
        "router = fastapi.APIRouter()\n"
        "\n"
        "@router.delete('/items/{iid}')\n"
        "def drop(iid):\n"
        "    return iid\n"),
    "app/admin.py": (
        "from fastapi import APIRouter, FastAPI\n"
        "panel = APIRouter(prefix='/admin')\n"
        "reports = APIRouter(prefix='/reports')\n"
        "panel.include_router(reports, prefix='/r')\n"
        "old = FastAPI()\n"
        "\n"
        "@reports.get('/daily')\n"
        "def daily():\n"
        "    return []\n"
        "\n"
        "@old.websocket('/feed')\n"
        "async def feed(ws):\n"
        "    return None\n"),
}


def test_fastapi_routers_prefixes_and_includes_across_files(build_repo):
    assert table(build_repo(FASTAPI)) == {
        ("GET", "/health"): ("app/main.py::health", "app/main.py:12", "exact"),
        ("GET", "/api/v1/users/{uid}"): ("app/routers/users.py::get_user", "app/routers/users.py:4", "exact"),
        ("POST", "/api/v1/users"): ("app/routers/users.py::save", "app/routers/users.py:8", "exact"),
        ("PUT", "/api/v1/users"): ("app/routers/users.py::save", "app/routers/users.py:8", "exact"),
        ("DELETE", "/api/shop/items/{iid}"): ("app/routers/items.py::drop", "app/routers/items.py:4", "exact"),
        ("GET", "/admin/r/reports/daily"): ("app/admin.py::daily", "app/admin.py:7", "exact"),
        ("WEBSOCKET", "/legacy/feed"): ("app/admin.py::feed", "app/admin.py:11", "exact"),
    }


FLASK = {
    "web/__init__.py": (
        "from flask import Flask\n"
        "\n"
        "def create_app():\n"
        "    app = Flask(__name__)\n"
        "    from .auth import bp as auth_bp\n"
        "    from .api import api\n"
        "    app.register_blueprint(auth_bp, url_prefix='/auth')\n"
        "    app.register_blueprint(api)\n"
        "\n"
        "    @app.route('/')\n"
        "    def index():\n"
        "        return 'hi'\n"
        "    return app\n"),
    "web/auth.py": (
        "from flask import Blueprint\n"
        "bp = Blueprint('auth', __name__, url_prefix='/replaced')\n"
        "\n"
        "@bp.route('/login', methods=['GET', 'POST'])\n"
        "def login():\n"
        "    return ''\n"
        "\n"
        "@bp.post('/logout')\n"
        "def logout():\n"
        "    return ''\n"),
    "web/api.py": (
        "from flask import Blueprint\n"
        "from .v2 import v2\n"
        "api = Blueprint('api', __name__, url_prefix='/api/')\n"
        "api.register_blueprint(v2, url_prefix='/v2')\n"
        "\n"
        "@api.get('')\n"
        "def root():\n"
        "    return ''\n"),
    "web/v2.py": (
        "import flask\n"
        "v2 = flask.Blueprint('v2', __name__)\n"
        "\n"
        "@v2.route('/things/<int:tid>')\n"
        "def thing(tid):\n"
        "    return ''\n"),
}


def test_flask_blueprints_registered_in_an_app_factory(build_repo):
    assert table(build_repo(FLASK)) == {
        ("GET", "/"): ("web/__init__.py::create_app.<locals>.index", "web/__init__.py:10", "exact"),
        ("GET", "/auth/login"): ("web/auth.py::login", "web/auth.py:4", "exact"),    # the registration's prefix wins
        ("POST", "/auth/login"): ("web/auth.py::login", "web/auth.py:4", "exact"),
        ("POST", "/auth/logout"): ("web/auth.py::logout", "web/auth.py:8", "exact"),
        ("GET", "/api/"): ("web/api.py::root", "web/api.py:6", "exact"),             # the blueprint's own prefix
        ("GET", "/api/v2/things/<int:tid>"): ("web/v2.py::thing", "web/v2.py:4", "exact"),   # nested
    }


DJANGO = {
    "mysite/__init__.py": "",
    "mysite/settings.py": "ROOT_URLCONF = 'mysite.urls'\nDEBUG = False\n",
    "mysite/urls.py": (
        "from django.urls import include, path, re_path\n"
        "from blog import views as blog_views\n"
        "import shop.urls\n"
        "extra = [path('reports/', blog_views.report)]\n"
        "urlpatterns = [\n"
        "    path('', blog_views.index),\n"
        "    path('blog/', include('blog.urls')),\n"
        "    path('credit/', include(extra)),\n"
        "    re_path(r'^api/', include([path('ping/', blog_views.ping)])),\n"
        "    path('shop/', include((shop.urls, 'shop'))),\n"
        "]\n"
        "urlpatterns += [path('about/', blog_views.About.as_view())]\n"),
    "blog/__init__.py": "",
    "blog/views.py": ("def index(request):\n    return None\n\n\ndef report(request):\n    return None\n\n\n"
                      "def ping(request):\n    return None\n\n\ndef detail(request, pk):\n    return None\n\n\n"
                      "class About:\n    pass\n"),
    "blog/urls.py": (
        "from django.urls import path, re_path\n"
        "from . import views\n"
        "urlpatterns = [\n"
        "    path('<int:pk>/', views.detail, name='detail'),\n"
        "    re_path(r'^archive/(?P<year>[0-9]{4})/$', views.index),\n"
        "]\n"),
    "shop/__init__.py": "",
    "shop/urls.py": "from django.urls import path\n\n\ndef cart(request):\n    return None\n\n\n"
                    "urlpatterns = [path('cart/', cart)]\n",
}


def test_django_urlpatterns_and_includes_from_the_root_urlconf(build_repo):
    assert table(build_repo(DJANGO)) == {
        ("ANY", "/"): ("blog/views.py::index", "mysite/urls.py:6", "exact"),
        ("ANY", "/blog/<int:pk>/"): ("blog/views.py::detail", "blog/urls.py:4", "exact"),
        ("ANY", "/blog/archive/(?P<year>[0-9]{4})/$"): ("blog/views.py::index", "blog/urls.py:5", "exact"),
        ("ANY", "/credit/reports/"): ("blog/views.py::report", "mysite/urls.py:4", "exact"),
        ("ANY", "/api/ping/"): ("blog/views.py::ping", "mysite/urls.py:9", "exact"),
        ("ANY", "/shop/cart/"): ("shop/urls.py::cart", "shop/urls.py:8", "exact"),
        ("ANY", "/about/"): ("blog/views.py::About", "mysite/urls.py:12", "exact"),    # a class-based view
    }


def test_django_with_no_root_urlconf_is_heuristic(build_repo):
    files = {k: v for k, v in DJANGO.items() if k != "mysite/settings.py"}
    files["lost/urls.py"] = "from django.urls import path\nurlpatterns = [path('found/', print)]\n"
    found = {(r["method"], r["path"]): r for r in routes.for_index(build_repo(files))}
    assert found[("ANY", "/blog/<int:pk>/")]["confidence"] == "heuristic"         # under a root taken as one
    assert found[("ANY", "/blog/<int:pk>/")]["unresolved"] == ["no literal ROOT_URLCONF names the root URLconf"]
    assert found[("ANY", "/found/")]["confidence"] == "heuristic"                 # a root of its own, too


def unreached(root):
    """A Django project whose root URLconf is `root`, and two URLconfs it does not include, the
    one included by the other sorting first."""
    return {
        "site/__init__.py": "",
        "site/settings.py": "ROOT_URLCONF = 'site.urls'\n",
        "site/urls.py": "from django.urls import path\n" + root,
        "a_inner/__init__.py": "",
        "a_inner/urls.py": "from django.urls import path\nurlpatterns = [path('inner/', print)]\n",
        "z_outer/__init__.py": "",
        "z_outer/urls.py": "from django.urls import include, path\nurlpatterns = [path('outer/', include('a_inner.urls'))]\n",
    }


def test_a_root_urlconf_that_cannot_be_read_leaves_every_prefix_unknown(build_repo):
    idx = build_repo(unreached("urlpatterns = build_them()\n"))
    assert [(r["path"], r["unresolved"]) for r in routes.for_index(idx)] == [
        ("/{?}/outer/inner/", ["the root URLconf site.urls has no urlpatterns that could be read"])]


def test_a_urlconf_no_root_reaches_is_listed_once_under_its_includer(build_repo):
    idx = build_repo(unreached("urlpatterns = [path('', print)]\n"))
    assert [(r["path"], r["unresolved"]) for r in routes.for_index(idx)] == [
        ("/", []), ("/{?}/outer/inner/", ["z_outer/urls.py is included from no root URLconf that is known"])]


def test_confidence_says_what_could_not_be_worked_out(build_repo):
    idx = build_repo({
        "svc/__init__.py": "",
        "svc/main.py": (
            "from fastapi import FastAPI\n"
            "from svc.config import settings\n"
            "from svc import parts\n"
            "PREFIX = '/once'\n"
            "TWICE = '/a'\n"
            "TWICE = '/b'\n"
            "app = FastAPI()\n"
            "app.include_router(parts.computed, prefix=settings.API_PREFIX)\n"
            "app.include_router(parts.constant, prefix=PREFIX)\n"
            "app.include_router(parts.twice, prefix=TWICE)\n"
            "\n"
            "def register(target):\n"
            "    target.include_router(parts.passed)\n"
            "\n"
            "@app.api_route('/any', methods=parts.METHODS)\n"
            "def anything():\n"
            "    return None\n"),
        "svc/config.py": "settings = object()\n",
        "svc/parts.py": (
            "from fastapi import APIRouter\n"
            "METHODS = ['GET']\n"
            "computed = APIRouter()\n"
            "constant = APIRouter()\n"
            "twice = APIRouter()\n"
            "passed = APIRouter()\n"
            "lonely = APIRouter(prefix='/lonely')\n"
            "\n"
            "@computed.get('/c')\n"
            "def c():\n"
            "    return None\n"
            "\n"
            "@constant.get('/k')\n"
            "def k():\n"
            "    return None\n"
            "\n"
            "@twice.get('/t')\n"
            "def t():\n"
            "    return None\n"
            "\n"
            "@passed.get('/p')\n"
            "def p():\n"
            "    return None\n"
            "\n"
            "@lonely.get('/l')\n"
            "def lone():\n"
            "    return None\n"),
        "svc/other.py": (
            "from somewhere import app\n"
            "\n"
            "@app.get('/unknown')\n"
            "def unknown():\n"
            "    return None\n"
            "\n"
            "@cache.get('a-cache-key')\n"
            "def cached():\n"
            "    return None\n"),
    })
    found = {(r["method"], r["path"]): (r["confidence"], r["unresolved"]) for r in routes.for_index(idx)}
    assert found == {
        ("GET", "/once/k"): ("exact", []),                    # a name the module binds once to a string
        ("GET", "{?}/c"): ("heuristic", ["prefix settings.API_PREFIX (svc/main.py line 8)"]),
        ("GET", "{?}/t"): ("heuristic", ["prefix TWICE (svc/main.py line 10)"]),     # bound twice: computed
        ("GET", "{?}/p"): ("heuristic", ["include_router on target (svc/main.py line 13), which is not known "
                                         "to be an app or a router"]),
        ("GET", "{?}/lonely/l"): ("heuristic", ["router lonely in svc/parts.py is included in no app that is known"]),
        ("?", "/any"): ("heuristic", ["methods are computed (svc/main.py line 15)"]),
        ("GET", "{?}/unknown"): ("heuristic", ["app (svc/other.py line 3) is not known to be an app or a router"]),
    }                                                         # and @cache.get("a-cache-key") is no route


def test_nothing_is_imported_or_run(build_repo, tmp_path):
    marker = tmp_path / "ran"
    idx = build_repo({
        "boom.py": (f"open({str(marker)!r}, 'w').write('ran')\n"
                    "raise SystemExit('never imported')\n"
                    "from flask import Flask\n"
                    "app = Flask(__name__)\n"
                    "\n"
                    "@app.get('/boom')\n"
                    "def boom():\n"
                    "    return ''\n"),
    })
    assert table(idx) == {("GET", "/boom"): ("boom.py::boom", "boom.py:6", "exact")}
    assert not marker.exists()


def test_diff_tells_a_changed_method_or_path_from_an_added_and_a_removed_route():
    def r(method, path, handler):
        return {"method": method, "path": path, "handler": handler, "file": "a.py", "line": 1,
                "framework": "fastapi", "confidence": "exact", "unresolved": []}
    base = [r("GET", "/users", "a.py::users"), r("GET", "/items", "a.py::items"), r("GET", "/old", "a.py::old"),
            r("GET", "/same", "a.py::same")]
    head = [r("POST", "/users", "a.py::users"), r("GET", "/goods", "a.py::items"), r("GET", "/new", "a.py::new"),
            r("GET", "/same", "a.py::same")]
    d = routes.diff(base, head)
    assert [(x["method"], x["path"]) for x in d["added"]] == [("GET", "/new")]
    assert [(x["method"], x["path"]) for x in d["removed"]] == [("GET", "/old")]
    assert sorted(((c["from"]["method"], c["from"]["path"]), (c["to"]["method"], c["to"]["path"]))
                  for c in d["changed"]) == [(("GET", "/items"), ("GET", "/goods")),
                                             (("GET", "/users"), ("POST", "/users"))]
    assert routes.diff(base, base) == {"added": [], "removed": [], "changed": []}


def test_api_map_lists_the_routes_with_their_prefixes(build_repo):
    by_path = {r["path"]: r for r in api_map(build_repo(FASTAPI))}
    assert by_path["/api/v1/users/{uid}"]["method"] == "GET"
    assert by_path["/api/v1/users/{uid}"]["file"] == "app/routers/users.py"
    assert {by_path["/api/v1/users/{uid}"][k] for k in ("confidence", "framework")} == {"exact", "fastapi"}
