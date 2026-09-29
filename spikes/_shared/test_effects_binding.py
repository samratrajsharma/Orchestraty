"""Tests for the call-binding check, against synthetic callers parsed from Python source."""
import ast

import pytest

from effects_binding import BINDS, BREAKS, NOT_ANALYSED, REBINDS, REVIEW, callers_not_updated, check_call, check_calls
from effects_record import CallSite, FunctionChange, call_site_from_node, signature_from_source as sig


def calls(source, name="f", resolved=True, file="callers.py"):
    """One CallSite per call to `name` (or to .name) in `source`, in line order."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            target = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if target == name:
                passes_first = isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                    and func.value.id[:1].isupper()                              # Cls.m(obj, ...)
                out.append(call_site_from_node(node, file, resolved, "import", passes_first))
    return sorted(out, key=lambda c: c.line)


def change(before, after, callers, implicit=(None, None), language="python", resolved=True):
    return FunctionChange(id="lib.py::f", name="f", file="lib.py", language=language,
                          before=sig(before, implicit[0]) if before else None,
                          after=sig(after, implicit[1]) if after else None,
                          call_sites=tuple(calls(callers, resolved=resolved)))


def results(ch):
    return [(r.call.line, r.result) for r in check_calls(ch)]


# before, after, one caller per line, the expected result per line
CASES = [
    ("reordered", "def f(a, b): pass", "def f(b, a): pass",
     "f(1, 2)\nf(a=1, b=2)\nf(1, b=2)", [REBINDS, BINDS, BREAKS]),
    ("required added", "def f(a): pass", "def f(a, b): pass",
     "f(1)\nf(1, 2)\nf(a=1, b=2)", [BREAKS, BINDS, BINDS]),
    ("optional removed", "def f(a, b=1): pass", "def f(a): pass",
     "f(1)\nf(1, 2)\nf(1, b=2)", [BINDS, BREAKS, BREAKS]),
    ("renamed", "def f(a, b): pass", "def f(a, c): pass",
     "f(1, 2)\nf(1, b=2)\nf(1, c=2)", [REVIEW, BREAKS, BINDS]),
    ("default changed", "def f(a, b=1): pass", "def f(a, b=2): pass",
     "f(1)\nf(1, 2)\nf(1, b=3)", [REVIEW, BINDS, BINDS]),
    ("made keyword-only", "def f(a, b): pass", "def f(a, *, b): pass",
     "f(1, 2)\nf(1, b=2)", [BREAKS, BINDS]),
    ("made positional-only", "def f(a): pass", "def f(a, /): pass",
     "f(1)\nf(a=1)", [BINDS, BREAKS]),
    ("inserted before another", "def f(a, c=0): pass", "def f(a, b=0, c=0): pass",
     "f(1, 2)\nf(1, c=2)\nf(1)", [REBINDS, BINDS, BINDS]),
    ("*args removed", "def f(a, *rest): pass", "def f(a): pass",
     "f(1)\nf(1, 2, 3)", [BINDS, BREAKS]),
    ("keyword now into **kwargs", "def f(a, b=1): pass", "def f(a, **kw): pass",
     "f(1, b=2)\nf(1)", [REVIEW, BINDS]),
    ("unpacking", "def f(a, b): pass", "def f(a, b, c): pass",
     "f(*xs)\nf(1, **opts)\nf(*xs, zz=1)\nf(1, 2, 3, 4, *xs)", [REVIEW, REVIEW, BREAKS, BREAKS]),
    ("given twice", "def f(a, b=1): pass", "def f(a, b=1): pass",
     "f(1, 2, b=3)\nf(1, *xs, a=2)", [BREAKS, BREAKS]),
]


@pytest.mark.parametrize("label,before,after,callers,expected", CASES, ids=[c[0] for c in CASES])
def test_call_sites_against_the_new_signature(label, before, after, callers, expected):
    got = check_calls(change(before, after, callers))
    assert [r.result for r in got] == expected, [r.reason for r in got]
    for r in got:
        assert r.high_confidence == (r.result in (BREAKS, REBINDS, BINDS)), r
        assert r.label == "resolved"


def test_what_cannot_be_decided_is_never_binds():
    got = check_calls(change("def f(a, b): pass", "def f(a, b, *, c): pass", "f(*xs)\nf(**kw)\nf(1, *xs, **kw)"))
    assert {r.result for r in got} == {REVIEW} and not any(r.high_confidence for r in got)


def test_methods_bind_without_the_implicit_first_parameter():
    callers = "obj.f(1)\nThing.f(obj, 1)\nobj.f()"
    got = check_calls(change("def f(self, a): pass", "def f(self, a, b=0): pass", callers, ("self", "self")))
    assert [r.result for r in got] == [BINDS, BINDS, BREAKS]
    got = check_calls(change("def f(self, a): pass", "@staticmethod\ndef f(a): pass", callers, ("self", None)))
    # staticmethod is transparent: obj.f(1) still passes 1 to a. Only the unbound call, which passes
    # obj itself, depends on how the first parameter is bound.
    assert [r.result for r in got] == [BINDS, REVIEW, BREAKS]


def test_a_call_found_by_name_only_is_never_high_confidence():
    got = check_calls(change("def f(a): pass", "def f(a, b): pass", "f(1)", resolved=False))
    assert [(r.result, r.high_confidence, r.label) for r in got] == [(BREAKS, False, "name-only")]


def test_a_wrapping_decorator_leaves_every_call_to_review():
    got = check_calls(change("@retry\ndef f(a): pass", "@retry\ndef f(): pass", "f(1)\nf()"))
    assert [r.result for r in got] == [REVIEW, REVIEW]


def test_removed_and_added_functions():
    gone = FunctionChange(id="lib.py::f", name="f", before=sig("def f(a): pass"), still_resolves=False,
                          call_sites=(CallSite("x.py", 3, positional=1, resolved=True),))
    assert [(r.result, r.high_confidence) for r in check_calls(gone)] == [(BREAKS, True)]
    moved = FunctionChange(id="lib.py::f", name="f", before=sig("def f(a): pass"),
                           call_sites=(CallSite("x.py", 3, positional=1, resolved=True),))
    assert [(r.result, r.high_confidence) for r in check_calls(moved)] == [(REVIEW, False)]
    added = change(None, "def f(a): pass", "f(1)\nf()")
    assert [r.result for r in check_calls(added)] == [BINDS, BREAKS]


def test_other_languages_are_not_analysed():
    got = check_calls(change("def f(a): pass", "def f(): pass", "f(1)", language="typescript"))
    assert [(r.result, r.high_confidence) for r in got] == [(NOT_ANALYSED, False)]


def test_callers_not_updated_lists_breaks_and_rebinds_with_file_and_line():
    ch = change("def f(a, b): pass", "def f(b, a): pass", "f(1, 2)\nf(a=1, b=2)\nf(1)")
    assert [(r.call.file, r.call.line, r.result) for r in callers_not_updated(ch)] == [
        ("callers.py", 1, REBINDS), ("callers.py", 3, BREAKS)]


def test_the_reason_names_the_problem():
    r = check_call(change("def f(a): pass", "def f(a, b): pass", ""), CallSite("x.py", 1, positional=1,
                                                                                 resolved=True))
    assert r.result == BREAKS and "missing required b" in r.reason


def test_an_async_change_leaves_every_call_to_review():
    for before, after in (("def f(a): pass", "async def f(a): pass"), ("async def f(a): pass", "def f(a): pass")):
        got = check_calls(change(before, after, "f(1)\nawait_it = f(1)"))
        assert {r.result for r in got} == {REVIEW} and not any(r.high_confidence for r in got)


def test_a_classmethod_called_through_its_class_still_binds_cls():
    callers = "Thing.f(1)\nobj.f(1)"
    got = check_calls(change("@classmethod\ndef f(cls, a): pass", "@classmethod\ndef f(cls, a, b=0): pass",
                             callers, ("cls", "cls")))
    assert [r.result for r in got] == [BINDS, BINDS]


def test_a_self_passed_by_keyword_is_left_to_review():
    got = check_calls(change("def f(self, a): pass", "def f(self, a, b=0): pass", "Thing.f(self=obj, a=1)",
                             ("self", "self")))
    assert [r.result for r in got] == [REVIEW]


def test_a_static_method_made_a_method_leaves_calls_through_the_class_to_review():
    callers = "Thing.f(1)\nobj.f(1)"
    got = check_calls(change("@staticmethod\ndef f(a): pass", "def f(self, a): pass", callers, (None, "self")))
    assert [r.result for r in got] == [REVIEW, BINDS]
