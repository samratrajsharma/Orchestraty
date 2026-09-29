"""Tests for the effects module's input record. Every record is synthetic."""
import ast
import json

import pytest

from effects_record import (KEYWORD_ONLY, POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_KEYWORD, VAR_POSITIONAL,
                            CallSite, FunctionChange, Param, Signature, call_site_from_node, from_dict,
                            signature_from_source, to_dict)


def test_a_signature_is_read_from_source_with_every_parameter_kind():
    s = signature_from_source("""
        @app.get("/x")
        async def f(a, /, b: int = 1, *args: str, c, d=None, **kw) -> dict:
            pass
    """)
    assert [(p.name, p.kind, p.has_default, p.default, p.annotation) for p in s.params] == [
        ("a", POSITIONAL_ONLY, False, None, None), ("b", POSITIONAL_OR_KEYWORD, True, "1", "int"),
        ("args", VAR_POSITIONAL, False, None, "str"), ("c", KEYWORD_ONLY, False, None, None),
        ("d", KEYWORD_ONLY, True, "None", None), ("kw", VAR_KEYWORD, False, None, None)]
    assert s.is_async and s.returns == "dict" and s.decorators == ("app.get('/x')",)
    method = signature_from_source("def m(self, a): pass", implicit_first="self")
    assert [p.name for p in method.params] == ["a"] and method.implicit_first == "self"
    with pytest.raises(ValueError):
        signature_from_source("def m(a): pass", implicit_first="self")


@pytest.mark.parametrize("params", [
    [Param("a"), Param("a")],                                                     # repeated
    [Param("a", KEYWORD_ONLY), Param("b", POSITIONAL_OR_KEYWORD)],                # out of order
    [Param("a", has_default=True), Param("b")],                                  # default before non-default
    [Param("a", VAR_POSITIONAL), Param("b", VAR_POSITIONAL)],                    # two *args
    [Param("a", VAR_KEYWORD, has_default=True)],
    [Param("a", "keyword")],                                                     # unknown kind
])
def test_signatures_that_python_would_reject_are_refused(params):
    with pytest.raises(ValueError):
        Signature(tuple(params))


def test_a_keyword_only_parameter_may_be_required_after_an_optional_one():
    Signature((Param("a", KEYWORD_ONLY, has_default=True), Param("b", KEYWORD_ONLY)))


def test_call_sites_count_arguments_and_name_keywords():
    call = ast.parse("f(1, x, *rest, key=2, **opts)").body[0].value
    site = call_site_from_node(call, "pkg/use.py", resolved=True, evidence="import")
    assert site == CallSite("pkg/use.py", 1, positional=2, keywords=("key",), star_args=True, star_kwargs=True,
                            resolved=True, evidence="import")


def test_a_record_round_trips_through_json():
    change = FunctionChange(id="pkg/mod.py::f", name="f", file="pkg/mod.py", line=3,
                            before=signature_from_source("def f(a, b=1): pass"),
                            after=signature_from_source("def f(a, *, b=2, **kw) -> int: pass"),
                            call_sites=(CallSite("x.py", 9, positional=1, keywords=("b",), resolved=True),))
    assert from_dict(json.loads(json.dumps(to_dict(change)))) == change
    with pytest.raises(ValueError):
        FunctionChange(id="x", name="x")                                          # neither side
