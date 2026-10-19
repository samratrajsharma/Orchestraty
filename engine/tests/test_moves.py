"""Moves and test paths in the structural check: a symbol moved to another file with the same
name and body is reported as moved, never as removed; "is it a test file?" goes by path
pattern, never by substring. Eight cases run on one synthetic repository.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orch_engine import check

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available on PATH")

CALC = '''__all__ = ["tax", "Cart"]


def round2(v):
    return round(v, 2)


def tax(x):
    return x * 0.2


class Cart:
    def total(self, items):
        return round2(sum(items) + tax(sum(items)))
'''
APP = '''from shop.calc import tax, Cart
from shop.greatest import pick
from shop.util import slugify


def main():
    return tax(10), Cart().total([1, 2]), pick([3, 1]), slugify("A")
'''
API_TS = '''function fmt(n: number): string {
  return n.toFixed(2);
}

export function price(n: number): string {
  return "$" + fmt(n);
}

export function label(s: string): string {
  return s.trim();
}
'''
BASE = {
    "shop/__init__.py": "",
    "shop/calc.py": CALC,
    "shop/app.py": APP,
    "shop/greatest.py": "def pick(xs):\n    return max(xs)\n\n\ndef keep():\n    return 0\n",
    "shop/util.py": "def slugify(s):\n    return s.lower()\n\n\ndef other():\n    return 1\n",
    "web/api.ts": API_TS,
    "web/main.ts": 'import { price, label } from "./api";\nconsole.log(price(3), label(" x "));\n',
}

CASES = {   # branch -> (files to write, (old, new) git mv or None)
    # move tax() unchanged to shop/tax.py; calc imports it back; the caller is updated
    "move": ({"shop/tax.py": "def tax(x):\n    return x * 0.2\n",
                 "shop/calc.py": CALC.replace("def tax(x):\n    return x * 0.2\n\n\n", "").replace(
                     "__all__", "from shop.tax import tax\n\n__all__"),
                 "shop/app.py": APP.replace("from shop.calc import tax, Cart",
                                            "from shop.tax import tax\nfrom shop.calc import Cart")}, None),
    # rename tax -> vat, every caller updated
    "rename": ({"shop/calc.py": CALC.replace("tax", "vat"), "shop/app.py": APP.replace("tax", "vat")}, None),
    # git mv calc.py -> pricing.py, the import updated
    "git-mv": ({"shop/app.py": APP.replace("shop.calc", "shop.pricing")}, ("shop/calc.py", "shop/pricing.py")),
    # inline round2 (not in __all__, used only in its own file)
    "inline-internal": ({"shop/calc.py": CALC.replace("def round2(v):\n    return round(v, 2)\n\n\n", "")
                            .replace("round2(", "round(").replace("tax(sum(items)))", "tax(sum(items)), 2)")}, None),
    # a real break: pick() deleted while app.py still calls it (the path contains "test")
    "real-break-under-greatest": ({"shop/greatest.py": "def keep():\n    return 0\n"}, None),
    # the control: slugify() deleted while app.py still calls it
    "real-break-control": ({"shop/util.py": "def other():\n    return 1\n"}, None),
    # TS: move exported label() to web/label.ts and re-export it; callers unchanged
    "ts-move-reexport": ({"web/label.ts": "export function label(s: string): string {\n  return s.trim();\n}\n",
                             "web/api.ts": API_TS.replace("export function label(s: string): string {\n  return s.trim();\n}\n",
                                                          'export { label } from "./label";\n')}, None),
    # TS: inline the non-exported fmt()
    "ts-inline-internal": ({"web/api.ts": API_TS.replace(
        "function fmt(n: number): string {\n  return n.toFixed(2);\n}\n\n", "").replace("fmt(n)", "n.toFixed(2)")}, None),
}

EXPECTED = {   # branch -> (exit code, public symbols reported removed, moves as (from, to))
    "move": (0, [], [("shop/calc.py::tax", "shop/tax.py::tax")]),
    "rename": (1, ["shop/calc.py::tax"], []),                     # renames are not paired yet
    "git-mv": (0, [], [("shop/calc.py::Cart", "shop/pricing.py::Cart"),
                         ("shop/calc.py::Cart.total", "shop/pricing.py::Cart.total"),
                         ("shop/calc.py::round2", "shop/pricing.py::round2"),
                         ("shop/calc.py::tax", "shop/pricing.py::tax")]),
    "inline-internal": (1, ["shop/calc.py::round2"], []),         # looks public: conservative
    "real-break-under-greatest": (1, ["shop/greatest.py::pick"], []),    # no longer hidden by "test"
    "real-break-control": (1, ["shop/util.py::slugify"], []),
    "ts-move-reexport": (0, [], [("web/api.ts::label", "web/label.ts::label")]),
    "ts-inline-internal": (1, ["web/api.ts::fmt"], []),           # looks public: conservative
}

SWITCHES = ["-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
            "-c", "maintenance.auto=false", "-c", "gc.auto=0"]


def write(root, files):
    for rel, content in files.items():
        p = Path(root, *rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8"))


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    """The base commit on main, and one branch per case. No user or system git config."""
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("", encoding="utf-8")
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    os.environ.update(GIT_CONFIG_GLOBAL=str(empty), GIT_CONFIG_NOSYSTEM="1")
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com", GIT_TERMINAL_PROMPT="0")
    repo = tmp_path_factory.mktemp("store") / "shop-repo"

    def git(*args):
        r = subprocess.run(["git"] + SWITCHES + list(args), cwd=repo, capture_output=True, env=env, timeout=120)
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")

    write(repo, BASE)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    for branch, (files, mv) in CASES.items():
        git("checkout", "-q", "-b", branch, "main")
        if mv:
            git("mv", *mv)
        write(repo, files)
        git("add", "-A")
        git("commit", "-q", "-m", branch)
        git("checkout", "-q", "main")
    yield repo, git
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.mark.parametrize("branch", list(CASES))
def test_moves_pass_and_real_breaks_fail(shop, branch, tmp_path, capsys):
    repo, git = shop
    git("checkout", "-q", branch)
    try:
        idx = check.build_index(str(repo), check.Config(data_dir=str(tmp_path / "data"), use_cache=False),
                                register=False)
        res = check.run_checks(idx, "main")
        rc = check.main(["--repo", str(repo), "--base", "main", "--data-dir", str(tmp_path / "cli")])
    finally:
        git("checkout", "-q", "main")
    code, removed, moves = EXPECTED[branch]
    assert rc == code
    assert res["public_removed"] == removed
    assert [(m["from"], m["to"]) for m in res["moved"]] == moves
    assert not any(m["signature_changed"] for m in res["moved"])
    out = capsys.readouterr().out
    assert ("Result: FAIL" if code else "Result: PASS") in out
    if moves:
        assert "Symbols moved, not removed" in out


def test_a_moved_symbol_whose_signature_changed_is_still_reported(shop, tmp_path):
    from orch_engine import track
    repo, git = shop
    base = track.snapshot(str(repo), "main", check.Config(data_dir=str(tmp_path / "d"), use_cache=False))
    head = dict(base, symbols=dict(base["symbols"]))
    moved = dict(head["symbols"].pop("shop/calc.py::tax"), file="shop/tax.py", sig_hash="another")
    head["symbols"]["shop/tax.py::tax"] = moved
    d = track.diff(base, head)
    assert d["symbols_moved"] == [{"from": "shop/calc.py::tax", "to": "shop/tax.py::tax", "signature_changed": True}]
    assert d["symbol_changes"] == {"shop/tax.py::tax": "signature"} and d["symbols_removed"] == []


# ----------------------------------------------------------------- test files by path pattern

TEST_FILES = ["tests/test_api.py", "test_api.py", "pkg/api_test.py", "conftest.py", "tests/conftest.py",
              "app/tests.py", "src/api.test.ts", "src/api.test.js", "src/Button.test.tsx", "src/api.spec.ts",
              "src/api.spec.jsx", "lib/x.spec.mjs", "src/__tests__/api.ts", "src/__tests__/util.js",
              "tests/helpers.py", "test/helper.js", "tests\\test_api.py", "pkg\\tests\\fixtures.py"]
NOT_TEST_FILES = ["attestation.py", "pkg/contest.py", "latest.py", "testing_utils.py", "src/api.ts",
                  "protest/x.py", "src/tests.ts", "tests.pyc", "docs/testing.md", "src/testify.spec", "tests", "",
                  "shop/greatest.py", "src/latest/api.py", "pkg/protester.py"]


@pytest.mark.parametrize("path", TEST_FILES)
def test_every_test_file_form_is_a_test_path(path):
    assert check.is_test_path(path)


@pytest.mark.parametrize("path", NOT_TEST_FILES)
def test_a_name_that_only_contains_test_is_not_a_test_path(path):
    assert not check.is_test_path(path)


def test_the_engine_keeps_the_same_patterns_as_the_effects_module():
    # The list mirrors spikes/_shared/effects_testfiles.py (the engine never imports spikes/).
    assert [label for label, _ in check.TEST_FILE_PATTERNS] == [
        "test_*.py", "*_test.py", "conftest.py", "tests.py", "*.test.<js>", "*.spec.<js>"]
    assert [label for label, _ in check.TEST_FOLDER_PATTERNS] == ["__tests__/", "tests/", "test/"]


@pytest.mark.parametrize("key,public", [
    ("pkg/attestation.py::verify", True), ("src/latest/api.py::get_user", True), ("pkg/contest.py::submit", True),
    ("shop/greatest.py::pick", True), ("pkg/core.py::helper_internal", True), ("pkg/core.py::f#2", True),
    ("pkg/core.py::_helper", False), ("pkg/core.py::C._x.setter", False), ("pkg/core.py::C.x.setter", True),
    ("tests/test_x.py::helper", False), ("tests/conftest.py::fixture", False), ("src/a.spec.ts::x", False),
    ("app/tests.py::T", False), ("pkg/mod.py::test_thing", False),
    ("m.py::Impl#2._compat", False), ("m.py::_Impl#2.run", True), ("m.py::Cls.setter", True),
    ("m.py::C.x.setter#2", True), ("m.py::C._x.deleter", False)])
def test_public_is_decided_by_name_and_test_path(key, public):
    assert check._is_public(key) is public


DUPLICATES = '''from typing import overload


@overload
def conv(a: int) -> int: ...
@overload
def conv(a: str) -> str: ...
def conv(a):
    return a


if True:
    def pick():
        return 1
else:
    def pick():
        return 2


def gone():
    return 3
'''


def test_deleting_one_duplicate_definition_is_not_a_removal_of_the_name():
    from orch_engine import track

    def snap(text):
        f = track.fingerprint_text("m.py", text)
        return {"files": {"m.py": f["loc"]}, "modules": {"m.py": f["module_hash"]}, "symbols": f["symbols"],
                "imports": [], "calls": []}

    after = (DUPLICATES.replace("@overload\ndef conv(a: str) -> str: ...\n", "")
             .replace("if True:\n    def pick():\n        return 1\nelse:\n    def pick():\n        return 2\n",
                      "def pick():\n    return 1\n")
             .replace("def gone():\n    return 3\n", ""))
    base, head = snap(DUPLICATES), snap(after)
    delta = track.diff(base, head)
    assert sorted(delta["symbols_removed"]) == ["m.py::conv#3", "m.py::gone", "m.py::pick#2"]
    assert check.public_removals(delta, head) == ["m.py::gone"]
