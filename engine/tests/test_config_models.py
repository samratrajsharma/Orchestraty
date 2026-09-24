"""Tests for legend.config.Config and legend.models dataclasses."""
from __future__ import annotations

from orch_engine.config import Config
from orch_engine.models import ParsedFile, RepoMeta, Symbol


def test_config_overrides_apply():
    cfg = Config(data_dir="x", use_cache=False)
    assert cfg.data_dir == "x"
    assert cfg.use_cache is False


def test_symbol_defaults_are_independent():
    s1 = Symbol(id="a", name="a", qualname="a", kind="function",
                file="f.py", start_line=1, end_line=2)
    s2 = Symbol(id="b", name="b", qualname="b", kind="function",
                file="f.py", start_line=1, end_line=2)
    assert s1.docstring == "" and s1.code == ""
    assert s1.calls == [] and s1.bases == []
    assert s1.complexity == 0 and s1.parent is None
    # mutable defaults must not be shared between instances
    s1.calls.append("x")
    assert s2.calls == []


def test_parsedfile_defaults():
    pf = ParsedFile(file="f.py", language="python")
    assert pf.imports == [] and pf.symbols == []
    assert pf.loc == 0 and pf.text == "" and pf.error == ""


def test_repometa_defaults():
    m = RepoMeta(name="r", path="/p", commit="abc")
    assert m.branch == "" and m.is_git is False and m.n_commits == 0

