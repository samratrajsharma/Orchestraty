"""POSIX ids at ingest and path normalisation at the MCP boundary (#12).

Ids are repo-relative POSIX paths on every OS, so they match git's paths, PR paths and the
same repository indexed on another machine. Every file or symbol an agent passes to an MCP
tool is normalised the same way ('api\\routes.py', './api/routes.py', an absolute path).
"""
from __future__ import annotations

import os

import pytest

from orch_engine import mcp_server, techdebt
from orch_engine.ingest import list_files

NESTED = {
    "pkg/__init__.py": "",
    "pkg/sub/__init__.py": "",
    "pkg/sub/mod.py": "def compute(xs):\n    return sum(xs)\n\n\ndef legacy():\n    return 0\n",
    "api/__init__.py": "",
    "api/routes.py": ("from fastapi import FastAPI\nfrom pkg.sub.mod import compute\n\napp = FastAPI()\n\n\n"
                      "@app.get(\"/users\")\ndef list_users():\n    return compute([1])\n\n\n"
                      "@app.delete(\"/users/{uid}\")\ndef remove_user(uid):\n    return uid\n"),
    "api/models.py": ("from sqlalchemy import Column, Integer\nfrom sqlalchemy.orm import declarative_base\n\n"
                      "Base = declarative_base()\n\n\nclass Account(Base):\n    __tablename__ = \"accounts\"\n"
                      "    id = Column(Integer, primary_key=True)\n"),
    "tests/test_mod.py": "from pkg.sub.mod import compute\n\n\ndef test_compute():\n    assert compute([1, 2]) == 3\n",
    "env/vendored.py": "def hidden():\n    return 1\n",
}


@pytest.fixture
def nested(build_repo, monkeypatch):
    """The MCP tools over a nested synthetic repository (the index is built once, offline)."""
    idx = build_repo(NESTED, name="nested")
    monkeypatch.setenv("ORCH_MCP_REPO", idx.meta.path)
    monkeypatch.setattr(mcp_server, "_IDX", idx)
    monkeypatch.setattr(mcp_server, "_IDX_REPO", idx.meta.path)
    return idx


def test_file_and_symbol_ids_are_posix(nested):
    ids = list(nested.graph.nodes)
    assert ids and not [i for i in ids if "\\" in i]
    assert "pkg/sub/mod.py::compute" in nested.graph.nodes
    assert ("api/routes.py", "pkg/sub/mod.py") in {(s, d) for s, d, _ in nested.graph.all_edges("imports")}
    assert all("\\" not in p.file for p in nested.parsed_files)


def test_list_files_gives_posix_paths(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.py").write_text("x = 1\n", encoding="utf-8")
    code, _ = list_files(str(tmp_path))
    assert [rel for rel, _ in code] == ["a/b/c.py"]


def _variants(idx, rel):
    return [rel, rel.replace("/", "\\"), "./" + rel, rel.replace("/", "//"),
            os.path.join(idx.meta.path, *rel.split("/"))]


def test_routes_touched_finds_the_routes_of_a_nested_file_however_it_is_written(nested):
    for f in _variants(nested, "api/routes.py"):
        res = mcp_server.routes_touched(files=[f])
        assert res["count"] == 2, (f, res)
        assert res["changed_files"] == ["api/routes.py"]


def test_models_touched_and_impact_of_change_accept_any_form_of_the_path(nested):
    for f in _variants(nested, "api/models.py"):
        assert mcp_server.models_touched(files=[f])["count"] == 1, f
    for f in _variants(nested, "pkg/sub/mod.py"):
        res = mcp_server.impact_of_change(f)
        assert res.get("defines") == 2 and res["file"] == "pkg/sub/mod.py", (f, res)
        assert "error" not in mcp_server.public_surface(file=f), f


def test_symbol_tools_accept_a_full_id_with_any_separator(nested):
    for sym in ("pkg/sub/mod.py::compute", "pkg\\sub\\mod.py::compute", "./pkg/sub/mod.py::compute"):
        assert [m["id"] for m in mcp_server.find_symbol(sym)["matches"]] == ["pkg/sub/mod.py::compute"], sym
        callers = {c["id"] for c in mcp_server.callers_of(sym)["callers"]}
        assert {"api/routes.py::list_users", "tests/test_mod.py::test_compute"} <= callers, sym
        assert mcp_server.callees_of("api\\routes.py::list_users")["count"] == 1
        assert mcp_server.blast_radius(sym)["counts"]["symbols"] >= 2, sym


def test_a_path_outside_the_repository_matches_nothing(nested, tmp_path):
    outside = str(tmp_path / "elsewhere" / "routes.py")
    assert mcp_server.routes_touched(files=[outside])["count"] == 0
    assert "error" in mcp_server.impact_of_change("../api/routes.py")


def test_the_path_normaliser(nested):
    norm = lambda p: mcp_server._norm_path(nested, p)    # noqa: E731
    assert norm("api\\routes.py") == "api/routes.py"
    assert norm(" ./api//routes.py ") == "api/routes.py"
    assert norm("api/./sub/../routes.py") == "api/routes.py"
    assert norm(os.path.join(nested.meta.path, "api", "routes.py")) == "api/routes.py"
    # a name inside the repository that starts with ".." is not a path outside it
    assert norm(os.path.join(nested.meta.path, "..data", "x.py")) == "..data/x.py"
    outside = os.path.join(os.path.dirname(nested.meta.path), "x.py")
    assert norm(outside) == outside.replace("\\", "/")
    assert norm("") == ""
    assert mcp_server._norm_symbol(nested, "api\\routes.py::list_users") == "api/routes.py::list_users"
    assert mcp_server._norm_symbol(nested, "Detector.predict") == "Detector.predict"


def test_import_cycles_do_not_depend_on_the_path_separator(build_repo, monkeypatch):
    # 'a/x.py' sorts before 'a0.py' with '/', and after it with '\'. With OS-native ids the
    # same code gave 2 cycles on Windows and 1 on Linux.
    idx = build_repo({"a0.py": "import a.x\n", "a/x.py": "import a0\nimport y\n", "y.py": "import a0\n"},
                     name="order")
    assert techdebt.import_cycles(idx) == [["a/x.py", "a0.py"]]
    monkeypatch.setenv("ORCH_MCP_REPO", idx.meta.path)
    monkeypatch.setattr(mcp_server, "_IDX", idx)
    monkeypatch.setattr(mcp_server, "_IDX_REPO", idx.meta.path)
    assert mcp_server.cycles()["cycles"] == [["a/x.py", "a0.py"]]


def test_skipped_folders_are_listed_as_not_analysed(nested):
    assert {"path": "env/", "reason": "skipped folder"} in nested.not_analysed
    assert not [p for p in nested.parsed_files if p.file.startswith("env/")]
    overview = mcp_server.overview()
    assert {"path": "env/", "reason": "skipped folder"} in overview["not_analysed"]
    assert overview["not_analysed_count"] == len(nested.not_analysed)
