"""Unit tests for the pure intent compiler (no database)."""

import pytest

from app import intent


def test_compile_template_echo():
    c = intent.compile_spec(intent.get_template("echo-demo"))
    assert c["name"] == "Echo demo" and len(c["tasks"]) == 2
    hello = next(t for t in c["tasks"] if t["name"] == "say hello")
    world = next(t for t in c["tasks"] if t["name"] == "say world")
    assert hello["depends_on"] == []
    assert world["depends_on"] == [hello["id"]]


def test_compile_explicit_spec_defaults():
    spec = {"name": "w", "tasks": [
        {"key": "a", "capability": "x"},
        {"key": "b", "capability": "y", "depends_on": ["a"]},
    ]}
    c = intent.compile_spec(spec)
    by = {t["name"]: t for t in c["tasks"]}
    assert by["a"]["depends_on"] == [] and by["b"]["depends_on"] == [by["a"]["id"]]
    assert by["a"]["max_attempts"] == 3 and by["a"]["timeout_seconds"] == 300


def test_errors():
    bads = [
        {"tasks": [{"key": "a", "capability": "x"}]},
        {"name": "w", "tasks": []},
        {"name": "w", "tasks": [{"key": "a", "capability": "x"}, {"key": "a", "capability": "y"}]},
        {"name": "w", "tasks": [{"key": "a"}]},
        {"name": "w", "tasks": [{"key": "a", "capability": "x", "depends_on": ["z"]}]},
        {"name": "w", "tasks": [
            {"key": "a", "capability": "x", "depends_on": ["b"]},
            {"key": "b", "capability": "y", "depends_on": ["a"]},
        ]},
    ]
    for bad in bads:
        with pytest.raises(intent.CompileError):
            intent.compile_spec(bad)


def test_unknown_template():
    with pytest.raises(intent.CompileError):
        intent.get_template("nope")
