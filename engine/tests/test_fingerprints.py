"""Symbol fingerprints: a signature hash, a body hash over the normalised syntax tree, a module
hash for code outside any definition, unique ids for duplicate definitions, and the schema
version in every cache key. Every repository here is synthetic, in a temp folder.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from orch_engine import models, track
from orch_engine.config import Config
from orch_engine.graph import JS_TEXT_NOTE
from orch_engine.parsing import parse_python
from orch_engine.pipeline import build_index, build_index_at


def write(root, files, crlf=False):
    for rel, content in files.items():
        p = Path(root, *rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((content.replace("\n", "\r\n") if crlf else content).encode("utf-8"))


def snap(files):
    """A fingerprint shaped like track.fingerprint's, from {path: text}, via the per-file API."""
    out = {"files": {}, "modules": {}, "symbols": {}, "imports": [], "calls": []}
    for rel, text in files.items():
        f = track.fingerprint_text(rel, text)
        out["files"][rel] = f["loc"]
        out["modules"][rel] = f["module_hash"]
        out["symbols"].update(f["symbols"])
    return out


def delta(before, after):
    return track.diff(snap(before), snap(after))


def changed(d):
    """Everything a diff reports, without the empty parts."""
    keys = ("symbol_changes", "modules_changed", "symbols_added", "symbols_removed", "symbols_moved")
    return {k: d[k] for k in keys if d[k]}


# ----------------------------------------------------------------- the 17-commit history

APP0 = '''from fastapi import FastAPI

app = FastAPI()

DEBUG = False

if DEBUG:
    LIMIT = 10
else:
    LIMIT = 100


@app.get("/items/{item_id}")
def read_item(item_id: int):
    return {"id": item_id}


@app.get("/x")
def handler_x():
    return {"ok": True}


def is_allowed(user, resource):
    if user.role == "admin":
        return True
    return resource.owner == user.id


def formatted(a, b):
    # add two numbers
    result = a + b
    return {'sum': result}


def sig_change(a, b):
    return a + b


def caller():
    return sig_change(1, 2)


class ItemView:
    @staticmethod
    def get(x):
        return x
'''

TS0 = '''import { Controller, Get, Delete } from '@nestjs/common';

@Controller('users')
export class UsersController {
  @Get(':id')
  findOne(id: string) {
    return this.lookup(id);
  }

  lookup(id: string) {
    if (id === 'admin') {
      return 'root';
    }
    return id;
  }
}
'''

JS0 = """const express = require('express');
const app = express();

function handler(req, res) {
  res.send('ok');
}

app.get('/y', handler);

module.exports = app;
"""

URLS0 = """from django.urls import path
from . import views

urlpatterns = [
    path("items/", views.list_items),
]
"""

APP_STEPS = [   # (label, old text, new text) applied to app.py, one commit each
    ("logic_flip_in_function", 'if user.role == "admin":', 'if user.role != "admin":'),
    ("logic_flip_module_level", "if DEBUG:\n", "if not DEBUG:\n"),
    ("decorator_GET_to_DELETE", '@app.get("/x")', '@app.delete("/x")'),
    ("decorator_path_change", '@app.get("/items/{item_id}")', '@app.get("/items/{item_id}/v2")'),
    ("method_decorator_static_to_classmethod", "    @staticmethod\n    def get(x):", "    @classmethod\n    def get(x):"),
    ("fmt_whitespace_only", "    result = a + b\n", "    result = a+b\n"),
    ("fmt_comment_only", "    # add two numbers\n", "    # add the two numbers together\n"),
    ("fmt_quote_style_only", "return {'sum': result}", 'return {"sum": result}'),
    ("fmt_blank_line_in_body", "    result = a+b\n", "    result = a+b\n\n"),
    ("fmt_reflow_call_black_style", "def caller():\n    return sig_change(1, 2)\n",
     "def caller():\n    return sig_change(\n        1,\n        2,\n    )\n"),
    ("signature_add_required_param", "def sig_change(a, b):\n    return a + b\n",
     "def sig_change(a, b, c):\n    return a + b + c\n"),
]

EXPECTED = {
    "logic_flip_in_function": {"symbol_changes": {"app.py::is_allowed": "body"}},
    "logic_flip_module_level": {"modules_changed": ["app.py"]},
    "decorator_GET_to_DELETE": {"symbol_changes": {"app.py::handler_x": "signature"}},
    "decorator_path_change": {"symbol_changes": {"app.py::read_item": "signature"}},
    "method_decorator_static_to_classmethod": {"symbol_changes": {"app.py::ItemView.get": "signature"}},
    "fmt_whitespace_only": {},
    "fmt_comment_only": {},
    "fmt_quote_style_only": {},
    "fmt_blank_line_in_body": {},
    "fmt_reflow_call_black_style": {},
    # a new required parameter, and the body that uses it
    "signature_add_required_param": {"symbol_changes": {"app.py::sig_change": "both"}},
    "fmt_crlf_line_endings_whole_file": {},
    "back_to_lf": {},
    # the method decorator sits between the methods, so it is class-level text in TS
    "ts_decorator_Get_to_Delete": {"symbol_changes": {"web/users.controller.ts::UsersController": "body"}},
    "ts_logic_flip": {"symbol_changes": {"web/users.controller.ts::UsersController.lookup": "body"}},
    "express_route_GET_to_DELETE_module_level": {"modules_changed": ["web/server.js"]},
    "django_urlpatterns_view_swap": {"modules_changed": ["urls.py"]},
}


def test_the_17_commit_history_gives_the_expected_change_for_every_commit(tmp_path, git_cmd):
    repo = tmp_path / "repo1"
    steps = []

    def commit(label):
        git_cmd(repo, "add", "-A")
        git_cmd(repo, "commit", "-q", "-m", label)
        steps.append((label, git_cmd(repo, "rev-parse", "HEAD")))

    app = APP0
    write(repo, {"app.py": app, "web/users.controller.ts": TS0, "web/server.js": JS0, "urls.py": URLS0})
    git_cmd(repo, "init", "-q")
    commit("base")
    for label, old, new in APP_STEPS:
        assert app.count(old) == 1, label
        app = app.replace(old, new)
        write(repo, {"app.py": app})
        commit(label)
    write(repo, {"app.py": app}, crlf=True)
    commit("fmt_crlf_line_endings_whole_file")
    write(repo, {"app.py": app})
    commit("back_to_lf")
    ts = TS0.replace("  @Get(':id')\n", "  @Delete(':id')\n")
    write(repo, {"web/users.controller.ts": ts})
    commit("ts_decorator_Get_to_Delete")
    write(repo, {"web/users.controller.ts": ts.replace("if (id === 'admin')", "if (id !== 'admin')")})
    commit("ts_logic_flip")
    write(repo, {"web/server.js": JS0.replace("app.get('/y', handler);", "app.delete('/y', handler);")})
    commit("express_route_GET_to_DELETE_module_level")
    write(repo, {"urls.py": URLS0.replace("views.list_items", "views.delete_items")})
    commit("django_urlpatterns_view_swap")

    cfg = Config(data_dir=str(tmp_path / "data"), use_cache=False)
    snaps = [track.snapshot(str(repo), sha, cfg) for _, sha in steps]
    got = {label: changed(track.diff(snaps[i - 1], snaps[i])) for i, (label, _) in enumerate(steps) if i}
    assert got == EXPECTED


# ----------------------------------------------------------------- formatting never shows

UNFORMATTED = '''import os
def f0(a,b):
    return {'k':a+b}
def f1(x):
    if x>0: return 'pos'
    return 'neg'
def f2(items):
    return [ i*2 for i in items if i ]
def f3(a, b = 1):
    return a+b
def f4(s):
    return s.replace('a','b')
def f5(path):
    return os.path.join(path,'x')
def f6(d):
    return d.get('k',None)
def f7(n):
    total=0
    for i in range(n): total+=i
    return total
def f8(a,b,c):
    return (a,
        b,c)
def f9():
    return f0(1,2)+f3(4)
class K:
    def m1(self,x):
        return f1( x )
    def m2(self):
        return 'm2'
'''

FORMATTED = '''import os


def f0(a, b):
    return {"k": a + b}


def f1(x):
    if x > 0:
        return "pos"
    return "neg"


def f2(items):
    return [i * 2 for i in items if i]


def f3(a, b=1):
    return a + b


def f4(s):
    return s.replace("a", "b")


def f5(path):
    return os.path.join(path, "x")


def f6(d):
    return d.get("k", None)


def f7(n):
    total = 0
    for i in range(n):
        total += i
    return total


def f8(a, b, c):
    return (a, b, c)


def f9():
    return f0(1, 2) + f3(4)


class K:
    def m1(self, x):
        return f1(x)

    def m2(self):
        return "m2"
'''


def test_a_formatter_pass_changes_no_fingerprint():
    before, after = parse_python("mod.py", UNFORMATTED), parse_python("mod.py", FORMATTED)
    texts = {s.id: s.code for s in before.symbols}
    # the old fingerprint hashed each symbol's text: all 13 symbols read as changed
    assert len(texts) == 13 and sum(texts[s.id] != s.code for s in after.symbols) == 13
    assert changed(delta({"mod.py": UNFORMATTED}, {"mod.py": FORMATTED})) == {}


# ----------------------------------------------------------------- what each hash covers

def test_a_docstring_or_comment_edit_changes_nothing():
    before = 'def f(x):\n    """Old words."""\n    return x  # note\n'
    after = 'def f(x):\n    """New words, longer."""\n    # a new comment\n    return x\n'
    assert changed(delta({"m.py": before}, {"m.py": after})) == {}


def test_a_logic_change_is_a_body_change_and_a_new_parameter_a_signature_change():
    base = "def f(a, b=1):\n    return a + b\n"
    assert delta({"m.py": base}, {"m.py": base.replace("a + b", "a - b")})["symbol_changes"] == {"m.py::f": "body"}
    for new_sig in ("def f(a, b=1, *, c):", "def f(a, b=2):", "def f(a: int, b=1):", "async def f(a, b=1):",
                    "def f(a, b=1) -> int:", "def f(a, /, b=1):", "def f(a, b=1, **kw):"):
        d = delta({"m.py": base}, {"m.py": base.replace("def f(a, b=1):", new_sig)})
        assert d["symbol_changes"] == {"m.py::f": "signature"}, new_sig


def test_the_signature_is_captured_as_normalised_source_text():
    src = ('import functools\n\n@functools.lru_cache( maxsize = 2 )\n'
           'async def f(a, /, b: "int"=1, *args, c, d=[1,2], **kw) -> dict[str,int]:\n    return {}\n')
    sig = track.fingerprint_text("m.py", src)["symbols"]["m.py::f"]["signature"]
    assert sig == {
        "params": [
            {"name": "a", "kind": "positional_only", "has_default": False, "default": None, "annotation": None},
            {"name": "b", "kind": "positional_or_keyword", "has_default": True, "default": "1", "annotation": "'int'"},
            {"name": "args", "kind": "var_positional", "has_default": False, "default": None, "annotation": None},
            {"name": "c", "kind": "keyword_only", "has_default": False, "default": None, "annotation": None},
            {"name": "d", "kind": "keyword_only", "has_default": True, "default": "[1, 2]", "annotation": None},
            {"name": "kw", "kind": "var_keyword", "has_default": False, "default": None, "annotation": None}],
        "returns": "dict[str, int]", "decorators": ["functools.lru_cache(maxsize=2)"], "is_async": True}


def test_a_class_decorator_or_base_change_is_a_signature_change():
    base = "import dataclasses\n\n@dataclasses.dataclass\nclass P(object):\n    x: int = 0\n"
    frozen = base.replace("@dataclasses.dataclass\n", "@dataclasses.dataclass(frozen=True)\n")
    assert delta({"m.py": base}, {"m.py": frozen})["symbol_changes"] == {"m.py::P": "signature"}
    rebased = base.replace("class P(object):", "class P(dict):")
    assert delta({"m.py": base}, {"m.py": rebased})["symbol_changes"] == {"m.py::P": "signature"}
    assert delta({"m.py": base}, {"m.py": base.replace("x: int = 0", "x: int = 1")})["symbol_changes"] == {"m.py::P": "body"}


def test_a_method_change_does_not_change_its_class():
    base = ("class Box:\n    size = 1\n\n    @property\n    def width(self):\n        return self.size\n\n"
            "    def grow(self):\n        self.size += 1\n")
    d = delta({"m.py": base}, {"m.py": base.replace("return self.size\n", "return self.size * 2\n")})
    assert d["symbol_changes"] == {"m.py::Box.width": "body"}


def test_module_level_code_is_its_own_fact():
    base = 'import os\n\nLIMIT = 10\n\n\ndef f():\n    return LIMIT\n'
    d = delta({"m.py": base}, {"m.py": base.replace("LIMIT = 10", "LIMIT = 11")})
    assert changed(d) == {"modules_changed": ["m.py"]}
    d = delta({"m.py": base}, {"m.py": base.replace("import os", "import sys")})
    assert changed(d) == {"modules_changed": ["m.py"]}
    d = delta({"m.py": '"""Doc."""\n' + base}, {"m.py": '"""Another doc."""\n' + base})
    assert changed(d) == {}


# ----------------------------------------------------------------- unique ids

def test_property_accessors_and_duplicate_definitions_get_their_own_ids():
    src = '''from typing import overload


class C:
    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        self._x = value

    @x.deleter
    def x(self):
        del self._x


try:
    def load():
        return "fast"
except ImportError:
    def load():
        return "slow"

if True:
    class Impl:
        pass
else:
    class Impl:
        def run(self):
            return 2


@overload
def conv(a: int) -> int: ...
@overload
def conv(a: str) -> str: ...
def conv(a):
    return a
'''
    syms = track.fingerprint_text("m.py", src)["symbols"]
    assert list(syms) == ["m.py::C", "m.py::C.x", "m.py::C.x.setter", "m.py::C.x.deleter",
                          "m.py::load", "m.py::load#2", "m.py::Impl", "m.py::Impl#2", "m.py::Impl#2.run",
                          "m.py::conv", "m.py::conv#2", "m.py::conv#3"]
    assert [k for k, v in syms.items() if v.get("overloaded")] == ["m.py::conv", "m.py::conv#2", "m.py::conv#3"]
    # a setter-only change is a change to the setter, nothing else
    d = delta({"m.py": src}, {"m.py": src.replace("self._x = value", "self._x = int(value)")})
    assert changed(d) == {"symbol_changes": {"m.py::C.x.setter": "body"}}


# ----------------------------------------------------------------- JavaScript and TypeScript

def test_js_and_ts_compare_text_without_comments_and_whitespace(tmp_path):
    base = "export function total(a, b) {\n  // add\n  return a + b;\n}\n"
    reflowed = "export function total(a, b) { /* sum */ return a + b; }\n"
    assert changed(delta({"m.ts": base}, {"m.ts": reflowed})) == {}
    strings = base.replace("return a + b;", "return 'a  b';")
    assert delta({"m.ts": base}, {"m.ts": strings})["symbol_changes"] == {"m.ts::total": "body"}
    assert delta({"m.ts": strings}, {"m.ts": strings.replace("'a  b'", "'a b'")})["symbol_changes"] == {"m.ts::total": "body"}
    write(tmp_path / "r", {"m.ts": base, "p.py": "x = 1\n"})
    idx = build_index(str(tmp_path / "r"), Config(data_dir=str(tmp_path / "d"), use_cache=False), register=False)
    assert {"path": "", "reason": JS_TEXT_NOTE} in idx.not_analysed


# ----------------------------------------------------------------- the per-file API

def test_a_files_fingerprint_needs_no_graph_and_matches_the_full_one(build_repo):
    files = {"a.py": "def f():\n    return 1\n\n\nclass K:\n    def m(self):\n        return f()\n",
             "b.ts": "export function g() { return 2; }\n"}
    idx = build_repo(files)
    full = track.fingerprint(idx, "x")
    for rel, text in files.items():
        one = track.fingerprint_text(rel, text)
        assert one["module_hash"] == full["modules"][rel]
        assert one["symbols"] == {k: v for k, v in full["symbols"].items() if k.startswith(rel + "::")}
    assert full["schema"] == models.ENGINE_SCHEMA


# ----------------------------------------------------------------- the schema version

def test_an_entry_another_engine_version_wrote_is_never_served(tmp_path, git_cmd, monkeypatch):
    repo = tmp_path / "repo"
    write(repo, {"m.py": "def m():\n    return 1\n"})
    git_cmd(repo, "init", "-q")
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-q", "-m", "c")
    cfg = Config(data_dir=str(tmp_path / "data"), use_cache=True)

    def served(build):
        logs = []
        build(logs.append)
        return "Loading cached parse/graph ..." in logs

    def by_commit(log):
        build_index_at(str(repo), "HEAD", cfg, progress=log)

    def working_tree(log):
        build_index(str(repo), cfg, progress=log, register=False)

    monkeypatch.setattr(models, "ENGINE_SCHEMA", 1)
    for build in (by_commit, working_tree):
        assert not served(build) and served(build)          # written, then served by the same version
    old = track.snapshot(str(repo), "HEAD", cfg)
    snap_dir = Path(cfg.data_dir, "snapshots")
    [old_file] = list(snap_dir.iterdir())
    old_file.write_text(json.dumps(dict(old, marker="written by version 1")), encoding="utf-8")
    assert track.snapshot(str(repo), "HEAD", cfg).get("marker") == "written by version 1"

    monkeypatch.setattr(models, "ENGINE_SCHEMA", 2)
    for build in (by_commit, working_tree):
        assert not served(build), "a cache entry from another engine version was served"
    fresh = track.snapshot(str(repo), "HEAD", cfg)
    assert "marker" not in fresh and fresh["schema"] == 2
    assert len(list(snap_dir.iterdir())) == 2
    # a file that claims another version inside is not served either
    [new_file] = [p for p in snap_dir.iterdir() if p != old_file]
    new_file.write_text(json.dumps(dict(fresh, schema=1, marker="tampered")), encoding="utf-8")
    assert "marker" not in track.snapshot(str(repo), "HEAD", cfg)
    names = os.listdir(os.path.join(cfg.data_dir, "cache"))
    assert any(n.endswith("-v1.pkl") for n in names) and any(n.endswith("-v2.pkl") for n in names)
