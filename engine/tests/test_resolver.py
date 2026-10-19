"""The resolver: a cross-file call edge needs import evidence, every edge says how it was
bound and on which lines, Python binds only to Python and JS/TS only to JS/TS, and module
names resolve against every Python source root. One small synthetic repository per case.
"""
from __future__ import annotations

import pytest

from orch_engine import mcp_server, techdebt
from orch_engine.graph import EVIDENCE_TAGS, NAMESPACE_NOTE


def calls(idx, src):
    """{target: (evidence, lines)} for the calls edges from `src`."""
    g = idx.graph
    return {t: (g.edge_info(src, t)["evidence"], g.edge_info(src, t)["lines"]) for t in g.callees(src)}


def lang_of(idx, sid):
    g = idx.graph
    return g.get(g.get(sid)["data"]["file"])["data"]["language"]


# ----------------------------------------------------------------- one case per evidence tag

def test_same_file(build_repo):
    idx = build_repo({"a.py": "def helper():\n    return 1\n\n\ndef main():\n    return helper()\n"})
    assert calls(idx, "a.py::main") == {"a.py::helper": ("same_file", [6])}


def test_import_and_relative_import(build_repo):
    idx = build_repo({
        "util.py": "def helper():\n    return 1\n",
        "app.py": "from util import helper\n\n\ndef main():\n    return helper()\n",
        "pkg/__init__.py": "",
        "pkg/core.py": "def run():\n    return 2\n",
        "pkg/api.py": "from .core import run\n\n\ndef go():\n    return run()\n",
    })
    assert calls(idx, "app.py::main") == {"util.py::helper": ("import", [5])}
    assert calls(idx, "pkg/api.py::go") == {"pkg/core.py::run": ("import", [5])}


def test_import_alias(build_repo):
    idx = build_repo({
        "billing.py": "def charge(amount):\n    return amount\n",
        "shop.py": "from billing import charge as pay\n\n\ndef buy():\n    return pay(3)\n",
    })
    assert calls(idx, "shop.py::buy") == {"billing.py::charge": ("import_alias", [5])}


def test_module_attribute_calls(build_repo):
    idx = build_repo({
        "util.py": "def helper():\n    return 1\n",
        "pkg/__init__.py": "",
        "pkg/sub.py": "def f():\n    return 2\n",
        "app.py": ("import util\nimport pkg.sub\nimport pkg.sub as s\nfrom pkg import sub\n\n\n"
                   "def a():\n    return util.helper()\n\n\ndef b():\n    return pkg.sub.f()\n\n\n"
                   "def c():\n    return s.f()\n\n\ndef d():\n    return sub.f()\n"),
    })
    assert calls(idx, "app.py::a") == {"util.py::helper": ("module_attr", [8])}
    assert calls(idx, "app.py::b") == {"pkg/sub.py::f": ("module_attr", [12])}
    assert calls(idx, "app.py::c") == {"pkg/sub.py::f": ("module_attr", [16])}
    assert calls(idx, "app.py::d") == {"pkg/sub.py::f": ("module_attr", [20])}


def test_class_attribute_constructor_and_init(build_repo):
    idx = build_repo({
        "models.py": ("class Base:\n    @classmethod\n    def make(cls):\n        return cls()\n\n\n"
                      "class User(Base):\n    def __init__(self, name=''):\n        self.name = name\n\n"
                      "    @staticmethod\n    def build():\n        return User()\n\n\nclass Plain:\n    pass\n"),
        "app.py": ("from models import User, Plain\n\n\ndef run():\n    User.build()\n    User.make()\n"
                   "    u = User('x')\n    return Plain()\n"),
    })
    assert calls(idx, "app.py::run") == {
        "models.py::User.build": ("class_attr", [5]),
        "models.py::Base.make": ("class_attr", [6]),          # inherited from a direct base
        "models.py::User.__init__": ("constructor", [7]),
        "models.py::Plain": ("constructor", [8]),             # no __init__: the class itself
    }


def test_self_method_with_a_direct_base(build_repo):
    idx = build_repo({
        "base.py": "class Base:\n    def save(self):\n        return 1\n",
        "user.py": ("from base import Base\n\n\nclass User(Base):\n    def check(self):\n        return 2\n\n"
                    "    def run(self):\n        self.check()\n        return self.save()\n"),
    })
    assert calls(idx, "user.py::User.run") == {"user.py::User.check": ("self_method", [9]),
                                               "base.py::Base.save": ("self_method", [10])}
    assert idx.graph.edge_info("user.py::User", "base.py::Base", "inherits")["evidence"] == "import"


def test_an_instance_call_reaches_a_method_inherited_from_a_direct_base(build_repo):
    idx = build_repo({
        "log.py": ("class Logger:\n    def error(self, msg):\n        return msg\n\n\n"
                   "class RootLogger(Logger):\n    pass\n\n\nroot = RootLogger()\n\n\n"
                   "def error(msg):\n    return root.error(msg)\n"),
    })
    assert calls(idx, "log.py::error") == {"log.py::Logger.error": ("instance", [14])}


def test_typed_instance_receivers(build_repo):
    idx = build_repo({
        "engine.py": "class Engine:\n    def run(self):\n        return 1\n",
        "app.py": ("from engine import Engine\n\n_DEFAULT = Engine()\n\n\n"
                   "def a():\n    e = Engine()\n    return e.run()\n\n\n"
                   "def b(e: Engine):\n    return e.run()\n\n\ndef c():\n    return _DEFAULT.run()\n\n\n"
                   "def d():\n    _DEFAULT = make()\n    return _DEFAULT.run()\n"),
    })
    assert calls(idx, "app.py::a") == {"engine.py::Engine": ("constructor", [7]),
                                       "engine.py::Engine.run": ("instance", [8])}
    for fn, line in (("b", 12), ("c", 16)):
        assert calls(idx, f"app.py::{fn}") == {"engine.py::Engine.run": ("instance", [line])}, fn
    assert calls(idx, "app.py::d") == {}          # rebound locally: the module-level type no longer holds


def test_reexports_are_followed_up_to_three_levels(build_repo):
    idx = build_repo({
        "pkg/__init__.py": "from .api import compute\nfrom .deep import far\n",
        "pkg/api.py": "from .impl import compute\n",
        "pkg/impl.py": "def compute():\n    return 1\n",
        "pkg/deep.py": "from .d1 import far\n",
        "pkg/d1.py": "from .d2 import far\n",
        "pkg/d2.py": "from .d3 import far\n",
        "pkg/d3.py": "def far():\n    return 2\n",
        "pkg/n1.py": "from .n2 import near\n",
        "pkg/n2.py": "from .n3 import near\n",
        "pkg/n3.py": "def near():\n    return 3\n",
        "pkg/more.py": "from .n1 import near\n",
        "app.py": "import pkg\nfrom pkg import compute, far\n\n\ndef run():\n    compute()\n    pkg.compute()\n    return far()\n",
        "app2.py": "from pkg.more import near\n\n\ndef run():\n    return near()\n",
    })
    assert calls(idx, "app.py::run") == {"pkg/impl.py::compute": ("reexport", [6, 7])}   # 2 hops; far needs 4
    assert calls(idx, "app2.py::run") == {"pkg/n3.py::near": ("reexport", [5])}          # exactly 3 hops


def test_star_import_needs_a_literal_all(build_repo):
    idx = build_repo({
        "lib.py": "__all__ = ['shown']\n\n\ndef shown():\n    return 1\n\n\ndef hidden():\n    return 2\n",
        "loose.py": "def anything():\n    return 3\n",
        "app.py": ("from lib import *\nfrom loose import *\n\n\n"
                   "def run():\n    shown()\n    hidden()\n    return anything()\n"),
    })
    assert calls(idx, "app.py::run") == {"lib.py::shown": ("import", [6])}


def test_every_tag_is_documented():
    import pathlib
    readme = (pathlib.Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for tag in EVIDENCE_TAGS:
        assert f"`{tag}`" in readme, tag


# ----------------------------------------------------------------- what never binds

def test_a_parameter_or_local_never_binds_to_an_import_or_a_module_def(build_repo):
    idx = build_repo({
        "util.py": "def helper():\n    return 1\n\n\ndef cb():\n    return 2\n",
        "app.py": ("from util import helper, cb\n\n\ndef local_def():\n    return 0\n\n\n"
                   "def by_param(helper):\n    return helper()\n\n\n"
                   "def by_local():\n    cb = make()\n    return cb()\n\n\n"
                   "def by_nested():\n    def inner(local_def):\n        return local_def()\n    return inner\n\n\n"
                   "def by_loop(items):\n    for helper in items:\n        helper()\n"),
    })
    for fn in ("by_param", "by_local", "by_nested", "by_loop"):
        assert calls(idx, f"app.py::{fn}") == {}, fn


def test_conditional_imports_of_two_modules_are_ambiguous(build_repo):
    idx = build_repo({
        "fast.py": "def parse(x):\n    return x\n",
        "slow.py": "def parse(x):\n    return x\n",
        "app.py": ("try:\n    from fast import parse\nexcept ImportError:\n    from slow import parse\n\n\n"
                   "def run():\n    return parse(1)\n"),
        "same.py": ("try:\n    from fast import parse\nexcept ImportError:\n    from fast import parse\n\n\n"
                    "def run():\n    return parse(1)\n"),
    })
    assert calls(idx, "app.py::run") == {}
    assert calls(idx, "same.py::run") == {"fast.py::parse": ("import", [8])}      # the same module twice
    assert ("app.py::run", "parse", 8, "ambiguous") in idx.graph.unresolved["app.py"]


def test_no_repo_unique_name_shortcut_and_no_receiver_fan_out(build_repo):
    idx = build_repo({
        "tools.py": "def lonely():\n    return 1\n",
        "store.py": ("class A:\n    def get(self, k):\n        return k\n\n\nclass B:\n    def get(self, k):\n"
                     "        return k\n\n\ndef use(obj):\n    return obj.get('x')\n\n\ndef caller():\n    return lonely()\n"),
    })
    assert calls(idx, "store.py::caller") == {}     # lonely is unique in the repo, but never imported
    assert calls(idx, "store.py::use") == {}        # unknown receiver: no edge, no same-file fan-out


def test_unresolved_calls_are_counted_per_file(build_repo):
    idx = build_repo({"app.py": ("import os\n\n\ndef run(cb, obj):\n    print(len('x'))\n    os.path.join('a')\n"
                                 "    cb()\n    obj.save()\n    return missing()\n")})
    got = {(name, why) for _, name, _, why in idx.graph.unresolved["app.py"]}
    assert got == {("cb", "local"), ("save", "local"), ("missing", "free")}   # builtins, os: not counted
    assert idx.graph.get("app.py")["data"]["unresolved_calls"] == 3


def test_duplicate_call_sites_make_one_edge_with_every_line(build_repo):
    idx = build_repo({"a.py": "def h():\n    return 1\n\n\ndef f():\n    h()\n    h(); h()\n    return h()\n"})
    assert calls(idx, "a.py::f") == {"a.py::h": ("same_file", [6, 7, 8])}
    assert [d for s, d, _ in idx.graph.all_edges("calls") if s == "a.py::f"] == ["a.py::h"]


# ----------------------------------------------------------------- language scope

MIXED = {   # Python and TypeScript names that collide; nothing imports across languages
    "api/client.py": ("import requests\nfrom slugify import slugify\n\n\ndef fetch(url):\n"
                      "    return requests.get(url, timeout=5).json()\n\n\ndef load_config(path):\n"
                      "    with open(path) as fh:\n        return fh.read()\n\n\ndef make_slug(title):\n"
                      "    return slugify(title)\n"),
    "api/report.py": "def format(value):\n    return f'<{value}>'\n",
    "web/api.ts": "export async function getUsers(): Promise<unknown> {\n  const r = await fetch('/api/users');\n  return r.json();\n}\n",
    "web/dates.ts": "import { format } from 'date-fns';\nexport function label(d: Date): string {\n  return format(d, 'yyyy-MM-dd');\n}\n",
    "web/modal.ts": "export class Modal {\n  open(): void { document.body.classList.add('modal-open'); }\n}\n",
    "web/util.ts": "export function slugify(s: string): string {\n  return s.toLowerCase();\n}\n",
    "svc/monitor.py": "def alert(msg):\n    return msg\n\n\ndef debounce(fn):\n    return fn\n",
    "svc/jobs.py": "import os\n\n\ndef run_job(xs):\n    print(sorted(xs))\n\n\ndef data_path():\n    return os.path.join('a', 'b')\n",
    "svc/utils.py": "def helper():\n    return 1\n",
    "ui/widget.ts": ("import { debounce } from 'lodash';\nexport function print(x: string) { return x; }\n"
                     "export function join(a: string, b: string) { return a + b; }\n"
                     "export function onClick() {\n  alert('hi');\n  debounce(() => 1);\n}\n"),
    "ui/uses.ts": "import { helper } from 'utils';\nexport function go() { return helper(); }\n",
}


def test_no_call_edge_crosses_languages(build_repo):
    idx = build_repo(MIXED)
    edges = idx.graph.all_edges("calls")
    assert [(s, d) for s, d, _ in edges if lang_of(idx, s) != lang_of(idx, d)] == []
    # the npm package 'utils' shares its name with svc/utils.py: still no edge
    assert calls(idx, "ui/uses.ts::go") == {}


# ----------------------------------------------------------------- Python source roots

def test_a_src_layout(build_repo):
    idx = build_repo({
        "src/shop/__init__.py": "", "src/shop/cart.py": "def total(xs):\n    return sum(xs)\n",
        "tests/test_cart.py": "from shop.cart import total\n\n\ndef test_total():\n    assert total([1]) == 1\n",
    })
    assert calls(idx, "tests/test_cart.py::test_total") == {"src/shop/cart.py::total": ("import", [5])}
    assert ("tests/test_cart.py", "src/shop/cart.py") in {(s, d) for s, d, _ in idx.graph.all_edges("imports")}


def test_a_package_in_a_subfolder(build_repo):
    idx = build_repo({
        "backend/app/__init__.py": "", "backend/app/db.py": "def get(k):\n    return k\n",
        "backend/app/main.py": "from app.db import get\nfrom app import db\n\n\ndef run():\n    get(1)\n    return db.get(2)\n",
    })
    assert calls(idx, "backend/app/main.py::run") == {"backend/app/db.py::get": ("import", [6, 7])}
    assert "backend" in idx.graph.resolution["roots"]


def test_a_cycle_under_src_is_found(build_repo):
    idx = build_repo({"src/pkg/__init__.py": "", "src/pkg/a.py": "import pkg.b\n", "src/pkg/b.py": "from pkg import a\n"})
    assert techdebt.import_cycles(idx) == [["src/pkg/a.py", "src/pkg/b.py"]]


def test_our_own_layout(build_repo):
    idx = build_repo({
        "engine/orch_engine/__init__.py": "",
        "engine/orch_engine/track.py": "def snapshot(path):\n    return path\n",
        "engine/tests/__init__.py": "",
        "engine/tests/test_track.py": "from orch_engine import track\n\n\ndef test_it():\n    assert track.snapshot('x')\n",
    })
    assert calls(idx, "engine/tests/test_track.py::test_it") == {
        "engine/orch_engine/track.py::snapshot": ("module_attr", [5])}


def test_a_folder_without_init_resolves_its_own_modules_like_a_script_folder(build_repo):
    idx = build_repo({
        "tools/run.py": "from infer import go\nimport model\n\n\ndef main():\n    go()\n    return model.make()\n",
        "tools/infer.py": "def go():\n    return 1\n",
        "tools/model.py": "def make():\n    return 2\n",
        "pkg/__init__.py": "",
        "pkg/run.py": "from infer import go\n\n\ndef main():\n    return go()\n",       # a package: no implicit sibling
        "pkg/infer.py": "def go():\n    return 3\n",
        "clash/run.py": "import util\n\n\ndef main():\n    return util.f()\n",
        "clash/util.py": "def f():\n    return 4\n",
        "util.py": "def f():\n    return 5\n",                                       # a root finds another util
    })
    assert calls(idx, "tools/run.py::main") == {"tools/infer.py::go": ("import", [6]),
                                                "tools/model.py::make": ("module_attr", [7])}
    assert ("tools/run.py", "tools/infer.py") in {(s, d) for s, d, _ in idx.graph.all_edges("imports")}
    assert calls(idx, "pkg/run.py::main") == {}
    assert calls(idx, "clash/run.py::main") == {}         # two candidates: skipped, never guessed


def test_an_ambiguous_module_name_is_skipped_never_guessed(build_repo):
    idx = build_repo({
        "util.py": "def f():\n    return 1\n",
        "src/util.py": "def f():\n    return 2\n",
        "app.py": "import util\n\n\ndef run():\n    return util.f()\n",
    })
    assert calls(idx, "app.py::run") == {}
    assert "util" in idx.graph.resolution["ambiguous_modules"]


@pytest.mark.parametrize("config", [
    {"pyproject.toml": "[tool.pytest.ini_options]\npythonpath = [\n  \"code\",  # tests import from here\n]\n"},
    {"pytest.ini": "[pytest]\npythonpath = code\n"},
    {"setup.cfg": "[options]\npackage_dir =\n    =code\n"},
    {"pyproject.toml": "[tool.setuptools]\npackage-dir = {\"\" = \"code\"}\n"},
    {"pyproject.toml": "[tool.setuptools.packages.find]\nwhere = [\"code\"]\n"},
])
def test_literal_import_path_settings_add_a_root(build_repo, config):
    files = {"code/helpers.py": "def h():\n    return 1\n",
             "app.py": "import helpers\n\n\ndef run():\n    return helpers.h()\n"}
    files.update(config)
    idx = build_repo(files)
    assert calls(idx, "app.py::run") == {"code/helpers.py::h": ("module_attr", [5])}


def test_a_namespace_package_is_noted_when_its_imports_stay_unresolved(build_repo):
    idx = build_repo({"lib/ns/mod.py": "def f():\n    return 1\n", "app.py": "import ns.mod\n\n\ndef run():\n    return ns.mod.f()\n"})
    assert calls(idx, "app.py::run") == {}
    assert {"path": "lib/ns/", "reason": NAMESPACE_NOTE} in idx.not_analysed


# ----------------------------------------------------------------- JavaScript and TypeScript

def test_js_and_ts_import_evidence(build_repo):
    idx = build_repo({
        "web/util/index.ts": "export function fmt(x: number) { return x; }\nexport default function main() { return 0; }\n",
        "web/math.ts": "export function add(a: number, b: number) { return a + b; }\n",
        "web/barrel.ts": "export { add } from './math';\nexport * from './util/index';\n",
        "web/app.ts": ("import main, { fmt } from './util';\nimport { fmt as f2 } from './util/index.ts';\n"
                       "import * as m from './math';\nimport { add } from './barrel';\nimport { x } from 'some-package';\n"
                       "export function run() {\n  main();\n  fmt(1);\n  m.add(1, 2);\n  return x();\n}\n"
                       "export function alias() { return f2(2); }\n"
                       "export function viaBarrel() { return add(3, 4); }\n"),
        "web/old.js": "const util = require('./util/index.ts');\nfunction go() { return util.fmt(3); }\n",
    })
    assert calls(idx, "web/app.ts::run") == {
        "web/util/index.ts::main": ("import", [7]), "web/util/index.ts::fmt": ("import", [8]),
        "web/math.ts::add": ("module_attr", [9])}
    assert calls(idx, "web/app.ts::alias") == {"web/util/index.ts::fmt": ("import_alias", [12])}
    assert calls(idx, "web/app.ts::viaBarrel") == {"web/math.ts::add": ("reexport", [13])}
    assert calls(idx, "web/old.js::go") == {"web/util/index.ts::fmt": ("module_attr", [2])}


def test_ts_aliases_and_workspaces_are_noted(build_repo):
    idx = build_repo({
        "package.json": '{"private": true, "workspaces": ["packages/*"]}',
        "web/tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}',
        "web/src/lib/api.ts": "export function getUsers() { return 1; }\n",
        "web/src/pages/home.ts": "import { getUsers } from '@/lib/api';\nexport function home() { return getUsers(); }\n",
    })
    assert calls(idx, "web/src/pages/home.ts::home") == {}
    reasons = {e["path"]: e["reason"] for e in idx.not_analysed}
    assert reasons["package.json"].startswith("workspace packages")
    assert reasons["web/tsconfig.json"].startswith("TypeScript path aliases")


# ----------------------------------------------------------------- the MCP tools

def test_mcp_tools_return_each_edges_evidence_and_lines(build_repo, monkeypatch):
    idx = build_repo({"util.py": "def helper():\n    return 1\n",
                      "app.py": "import util\n\n\ndef run():\n    util.helper()\n    return util.helper()\n",
                      "cli.py": "from app import run\n\n\ndef main():\n    return run()\n"})
    monkeypatch.setattr(mcp_server, "_IDX", idx)
    monkeypatch.setattr(mcp_server, "_IDX_REPO", idx.meta.path)
    monkeypatch.setenv("ORCH_MCP_REPO", idx.meta.path)
    [caller] = mcp_server.callers_of("util.py::helper")["callers"]
    assert caller["id"] == "app.py::run" and caller["evidence"] == "module_attr" and caller["lines"] == [5, 6]
    [callee] = mcp_server.callees_of("cli.py::main")["callees"]
    assert callee["id"] == "app.py::run" and callee["evidence"] == "import" and callee["lines"] == [5]
    deep = {c["id"]: c for c in mcp_server.callers_of("util.py::helper", transitive=True)["callers"]}
    assert deep["cli.py::main"]["evidence"] == "import" and deep["cli.py::main"]["lines"] == [5]


# ----------------------------------------------------------------- review regressions

def bound(idx, src):
    """{target: evidence} for the calls edges from `src`."""
    return {t: e for t, (e, _) in calls(idx, src).items()}


def test_an_overridden_method_and_a_second_base_are_never_the_ones_called(build_repo):
    idx = build_repo({"m.py": (
        "class Base:\n    def save(self):\n        return 1\n\n    def go(self):\n        return 2\n\n\n"
        "class Other:\n    def go(self):\n        return 3\n\n\n"
        "class Child(Base, Other):\n    def save(self):\n        return 4\n\n"
        "    def run(self):\n        self.save()\n        return self.go()\n\n\n"
        "def use():\n    Child.save(None)\n    c = Child()\n    return c.go()\n")})
    assert bound(idx, "m.py::Child.run") == {"m.py::Child.save": "self_method", "m.py::Base.go": "self_method"}
    assert bound(idx, "m.py::use") == {"m.py::Child.save": "class_attr", "m.py::Child": "constructor",
                                       "m.py::Base.go": "instance"}


def test_a_nested_scope_never_changes_what_a_name_means_outside_it(build_repo):
    idx = build_repo({
        "fast.py": "def parse(x):\n    return x\n",
        "slow.py": "def parse(x):\n    return x\n",
        "a.py": "def f():\n    return 1\n",
        "app.py": ("from fast import parse\n\n\ndef run(data):\n    def fallback():\n        from slow import parse\n"
                   "        return parse(data)\n    return parse(data), fallback\n\n\n"
                   "def outer():\n    from a import f\n\n    def inner():\n        global f\n        return 0\n    return f(), inner\n"),
        "srv.py": ("class Server:\n    def close(self):\n        return 1\n\n    def make_handler(self):\n"
                   "        class Handler:\n            def close(self):\n                return 2\n\n"
                   "            def finish(self):\n                return self.close()\n        return Handler\n"),
    })
    # run's own call goes to fast.parse; the nested fallback's call (counted as run's) to slow.parse
    assert calls(idx, "app.py::run") == {"fast.py::parse": ("import", [8]), "slow.py::parse": ("import", [7])}
    assert calls(idx, "app.py::outer") == {"a.py::f": ("import", [17])}
    assert calls(idx, "srv.py::Server.make_handler") == {}      # that self is the nested class's


def test_cls_is_the_class_only_as_a_methods_own_first_parameter(build_repo):
    idx = build_repo({"reg.py": (
        "class Registry:\n    def validate(self):\n        return 1\n\n    def register(self, cls):\n"
        "        return cls.validate()\n\n    @classmethod\n    def make(cls):\n        return cls.build()\n\n"
        "    @classmethod\n    def build(cls):\n        return 2\n")})
    assert bound(idx, "reg.py::Registry.register") == {}
    assert bound(idx, "reg.py::Registry.make") == {"reg.py::Registry.build": "self_method"}


def test_a_receiver_bound_more_than_once_has_no_known_class(build_repo):
    idx = build_repo({
        "store.py": ("class Engine:\n    def close(self):\n        return 1\n\n\nclass Redis:\n    def get(self):\n"
                     "        return 1\n\n\nclass Memory:\n    def get(self):\n        return 2\n"),
        "app.py": ("from store import Engine, Redis, Memory\n\n\n"
                   "def process(conn):\n    conn.close()\n    conn = Engine()\n    return conn\n\n\n"
                   "def loop(items):\n    e = Engine()\n    for e in items:\n        e.close()\n\n\n"
                   "def pick(fast):\n    if fast:\n        s = Redis()\n    else:\n        s = Memory()\n    return s.get()\n"),
    })
    assert bound(idx, "app.py::process") == {"store.py::Engine": "constructor"}
    assert bound(idx, "app.py::loop") == {"store.py::Engine": "constructor"}
    assert bound(idx, "app.py::pick") == {"store.py::Redis": "constructor", "store.py::Memory": "constructor"}


def test_a_receiver_that_is_one_class_or_none_keeps_its_class(build_repo):
    idx = build_repo({
        "store.py": "class Engine:\n    def index(self, xs):\n        return xs\n",
        "app.py": ("from store import Engine\n\n\ndef build(xs, fast):\n    dense = None\n    if fast:\n"
                   "        try:\n            dense = Engine()\n            dense.index(xs)\n"
                   "        except Exception:\n            dense = None\n    return dense\n"),
    })
    assert bound(idx, "app.py::build") == {"store.py::Engine": "constructor", "store.py::Engine.index": "instance"}


def test_a_package_variable_is_not_its_same_named_submodule(build_repo):
    idx = build_repo({
        "app/__init__.py": "class Settings:\n    def get(self):\n        return 1\n\n\nconfig = Settings()\n",
        "app/config.py": "def get():\n    return 2\n",
        "use.py": "from app import config\n\n\ndef run():\n    return config.get()\n",
    })
    assert calls(idx, "use.py::run") == {}


def test_nothing_resolves_under_a_package_name_that_maps_to_two_folders(build_repo):
    idx = build_repo({
        "svc_a/app/__init__.py": "", "svc_a/app/db.py": "def connect():\n    return 1\n",
        "svc_b/app/__init__.py": "", "svc_b/app/database.py": "def connect():\n    return 2\n",
        "svc_b/main.py": "from app import db\n\n\ndef run():\n    return db.connect()\n",
    })
    assert calls(idx, "svc_b/main.py::run") == {}
    assert "app" in idx.graph.resolution["ambiguous_modules"]


def test_alternatives_inside_the_target_module_are_ambiguous(build_repo):
    idx = build_repo({
        "markup.py": "try:\n    from _speedups import escape\nexcept ImportError:\n    def escape(s):\n        return s\n",
        "use.py": "from markup import escape\n\n\ndef run():\n    return escape('x')\n",
    })
    assert calls(idx, "use.py::run") == {}


def test_a_relative_import_above_the_indexed_folder_binds_nothing(build_repo):
    idx = build_repo({"util.py": "def f():\n    return 1\n",
                      "sub/mod.py": "from ...util import f\n\n\ndef run():\n    return f()\n"})
    assert calls(idx, "sub/mod.py::run") == {}


def test_a_python_method_named_constructor_is_not_a_constructor(build_repo):
    idx = build_repo({"n.py": "class Node:\n    def constructor(self):\n        return 1\n\n\ndef build():\n    return Node()\n"})
    assert calls(idx, "n.py::build") == {"n.py::Node": ("constructor", [7])}


def test_js_chained_and_optional_calls_are_not_plain_calls(build_repo):
    idx = build_repo({"web/app.ts": (
        "function listen(p: number) { return p; }\nfunction save(x: number) { return x; }\n"
        "export function start(server: any, user: any, getUser: any) {\n  server.create().listen(3000);\n"
        "  user?.save(1);\n  getUser()\n    .save(2);\n  return listen(1);\n}\n")})
    assert calls(idx, "web/app.ts::start") == {"web/app.ts::listen": ("same_file", [8])}


def test_js_default_export_of_a_call_result_is_not_the_called_function(build_repo):
    idx = build_repo({"web/h.ts": "function makeHandler(o: any) { return o; }\nexport default makeHandler({});\n",
                      "web/use.ts": "import handler from './h';\nexport function run() { return handler(1); }\n"})
    assert calls(idx, "web/use.ts::run") == {}


def test_a_ts_import_of_the_compiled_js_name_finds_the_ts_file(build_repo):
    idx = build_repo({"web/util.ts": "export function fmt() { return 1; }\n",
                      "web/app.ts": "import { fmt } from './util.js';\nexport function run() { return fmt(); }\n"})
    assert calls(idx, "web/app.ts::run") == {"web/util.ts::fmt": ("import", [2])}


def test_js_globals_are_not_counted_as_unresolved(build_repo):
    idx = build_repo({"web/g.ts": "export function f(x: any) {\n  console.log(1);\n  JSON.parse('1');\n  return x.go();\n}\n"})
    assert [(name, why) for _, name, _, why in idx.graph.unresolved.get("web/g.ts", [])] == [("go", "free")]


def test_tree_sitter_methods_and_receivers_are_not_plain_calls(tmp_path):
    from orch_engine import treesitter_parser as ts
    if not ts.available():
        pytest.skip("tree-sitter is not installed")
    from orch_engine.config import Config
    from orch_engine.pipeline import build_index
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "x.go").write_text("package main\n\ntype Conn struct{}\n\nfunc (c *Conn) Close() {}\n\n"
                                         "func run() {\n\tClose()\n}\n", encoding="utf-8")
    (tmp_path / "r" / "u.rb").write_text("class User\n  def self.find(id)\n    id\n  end\nend\n\ndef load\n"
                                         "  User.find(1)\nend\n", encoding="utf-8")
    idx = build_index(str(tmp_path / "r"), Config(data_dir=str(tmp_path / "d"), use_cache=False), register=False)
    assert idx.graph.callees("x.go::run") == [] and idx.graph.callees("u.rb::load") == []
