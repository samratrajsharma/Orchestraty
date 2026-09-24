"""Smoke tests: every module imports, and the non-importable entry points
(the Streamlit app, helper scripts) at least compile cleanly."""
from __future__ import annotations

import importlib
import py_compile

import pytest

LEGEND_MODULES = [
    "orch_engine", "orch_engine.config", "orch_engine.models", "orch_engine.providers",
    "orch_engine.ingest", "orch_engine.parsing", "orch_engine.graph", "orch_engine.chunking",
    "orch_engine.index", "orch_engine.retrieval", "orch_engine.llm", "orch_engine.insights",
    "orch_engine.diagram", "orch_engine.teach", "orch_engine.track", "orch_engine.techdebt",
    "orch_engine.engmemory", "orch_engine.media", "orch_engine.pipeline", "orch_engine.eval_harness",
]


@pytest.mark.parametrize("mod", LEGEND_MODULES)
def test_module_imports(mod):
    assert importlib.import_module(mod) is not None


@pytest.mark.parametrize("script", [
    "app.py",
    "scripts/run_eval.py",
    "scripts/make_demo_history.py",
])
def test_entrypoints_compile(project_root, script):
    path = project_root / script
    assert path.exists(), f"missing {script}"
    # raises py_compile.PyCompileError on a syntax error
    py_compile.compile(str(path), doraise=True)
