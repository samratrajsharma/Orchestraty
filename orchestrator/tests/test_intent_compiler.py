"""Unit tests for the pure intent compiler (no database)."""

import pytest

from app import intent


def test_compile_template_echo():
    c = intent.compile_spec(intent.get_template("echo-demo"))
    assert c["name"] == "Echo demo" and len(c["tasks"]) == 2
    hello = next(t for t in c["tasks"] if t["name"] == "say hello")
    world = next(t for t in c["tasks"] if t["name"] == "say world")
    assert hello["depends_on"] == [] and world["depends_on"] == [hello["id"]]


def test_compile_explicit_spec_defaults():
    spec = {"name": "w", "tasks": [
        {"key": "a", "capability": "x"},
        {"key": "b", "capability": "y", "depends_on": ["a"]},
    ]}
    c = intent.compile_spec(spec)
    by = {t["name"]: t for t in c["tasks"]}
    assert by["b"]["depends_on"] == [by["a"]["id"]] and by["a"]["max_attempts"] == 3


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


def test_all_templates_compile():
    assert len(intent.TEMPLATES) >= 6
    for name in intent.TEMPLATES:
        c = intent.compile_spec(intent.get_template(name))
        assert c["tasks"], name


def test_train_and_serve_chain():
    c = intent.compile_spec(intent.get_template("train-and-serve"))
    by = {t["name"]: t for t in c["tasks"]}
    assert by["deploy model"]["depends_on"] == [by["train model"]["id"]]
    assert by["deploy model"]["payload"]["model_uri"] == "models:/iris-classifier/latest"
