"""Smoke tests: every module imports, and the non-importable entry points
(helper scripts) at least compile cleanly."""
from __future__ import annotations

import importlib

import pytest

ENGINE_MODULES = [
    "orch_engine", "orch_engine.config", "orch_engine.models", "orch_engine.ingest",
    "orch_engine.parsing", "orch_engine.treesitter_parser", "orch_engine.graph",
    "orch_engine.impact", "orch_engine.insights", "orch_engine.techdebt",
    "orch_engine.coverage", "orch_engine.config_map", "orch_engine.diagram",
    "orch_engine.track", "orch_engine.check", "orch_engine.context",
    "orch_engine.mcp_server", "orch_engine.pipeline",
]


@pytest.mark.parametrize("mod", ENGINE_MODULES)
def test_module_imports(mod):
    assert importlib.import_module(mod) is not None


@pytest.mark.parametrize("script", [
    "scripts/verify_treesitter.py",
])
def test_entrypoints_compile(project_root, script):
    path = project_root / script
    assert path.exists(), f"missing {script}"
    # Compiled in memory: raises SyntaxError on a syntax error, and writes no bytecode
    # (py_compile would write __pycache__ even with PYTHONDONTWRITEBYTECODE set).
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
