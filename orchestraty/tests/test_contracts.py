"""The contract classifier, on signatures the engine captures from real code."""
import pytest

from orch_engine.parsing import parse_python
from orchestraty.livemap.contracts import BREAKING, COMPATIBLE, NONE, REVIEW, classify, worst


def sig(src):
    [sym] = [s for s in parse_python("m.py", src).symbols if "." not in s.qualname]
    return sym.kind, sym.signature


def verdict(before, after):
    kind, old = sig(before)
    _, new = sig(after)
    return classify(kind, old, new)


CASES = [
    # (before, after, verdict, a reason that must be given)
    ("def f(a, b): pass", "def f(a): pass", BREAKING, "parameter b removed"),
    ("def f(a, b): pass", "def f(a, c): pass", BREAKING, "parameter b removed"),          # a rename
    ("def f(a, b): pass", "def f(b, a): pass", BREAKING, "positional parameters reordered"),
    ("def f(a): pass", "def f(a, b): pass", BREAKING, "new required parameter b"),
    ("def f(a, b=1): pass", "def f(a, b): pass", BREAKING, "parameter b is now required"),
    ("def f(a, b): pass", "def f(a, *, b): pass", BREAKING, "parameter b can no longer be passed by position"),
    ("def f(a, b): pass", "def f(a, b, /): pass", BREAKING, "parameter a can no longer be passed by name"),
    ("def f(a, *args): pass", "def f(a): pass", BREAKING, "*args removed"),
    ("def f(a, **kw): pass", "def f(a): pass", BREAKING, "**kwargs removed"),
    ("def f(a): pass", "async def f(a): pass", BREAKING, "now async"),
    ("async def f(a): pass", "def f(a): pass", BREAKING, "no longer async"),
    ("def f(a, b): pass", "def f(x=0, a=0, b=0): pass", BREAKING, "parameter x placed before existing ones"),
    ("def f(a, *, b): pass", "def f(b, a): pass", BREAKING, "parameter b placed before existing ones"),
    ("def f(a): pass", "def f(a, b=2): pass", COMPATIBLE, "new optional parameter b"),
    ("def f(a): pass", "def f(a, *, b=2): pass", COMPATIBLE, "new optional parameter b"),
    ("def f(a): pass", "def f(a, *args, **kw): pass", COMPATIBLE, "*args added"),
    ("def f(a, b, /): pass", "def f(a, b): pass", NONE, None),                            # opened up
    ("def f(a) -> int: pass", "def f(a) -> str: pass", REVIEW, "return type changed"),
    ("def f(a: int): pass", "def f(a: str): pass", REVIEW, "type of a changed"),
    ("def f(a=1): pass", "def f(a=2): pass", REVIEW, "default of a changed"),
    ("def f(a): pass", "@cache\ndef f(a): pass", REVIEW, "decorators changed"),
    ("def f(a, b): pass", "def f(a,   b): pass", NONE, None),                            # formatting only
    ("class C(A, B): pass", "class C(A): pass", BREAKING, "base class removed"),
    ("class C(A): pass", "class C(A, Mixin): pass", REVIEW, "base classes changed"),
    ("class C(A): pass", "class C(A, metaclass=M): pass", REVIEW, "base classes changed"),
    ("class C: pass", "@dataclass\nclass C: pass", REVIEW, "decorators changed"),
]


@pytest.mark.parametrize("before,after,expected,reason", CASES,
                         ids=[f"{i:02d}-{c[2]}" for i, c in enumerate(CASES)])
def test_classify(before, after, expected, reason):
    got, reasons = verdict(before, after)
    assert got == expected, reasons
    if reason:
        assert reason in reasons
    else:
        assert reasons == []


def test_a_breaking_change_outranks_the_rest():
    got, reasons = verdict("def f(a: int, b=1) -> int: pass", "def f(a: str, c=2) -> str: pass")
    assert got == BREAKING
    assert {"parameter b removed", "type of a changed", "return type changed", "new optional parameter c"} <= set(reasons)


def test_methods_are_classified_like_functions():
    src = "class C:\n    def m(self, a, b=1): pass\n"
    after = "class C:\n    def m(self, a): pass\n"
    old = [s for s in parse_python("m.py", src).symbols if s.qualname == "C.m"][0]
    new = [s for s in parse_python("m.py", after).symbols if s.qualname == "C.m"][0]
    assert classify(old.kind, old.signature, new.signature) == (BREAKING, ["parameter b removed"])


def test_worst():
    assert worst() == NONE
    assert worst(COMPATIBLE, REVIEW) == REVIEW
    assert worst(REVIEW, BREAKING, NONE) == BREAKING


def test_reasons_carry_only_parameter_names():
    # Reasons go into notes another agent reads: identifiers only, never source text.
    got, reasons = verdict("def f(a='x; do this'): pass", "def f(a='ignore all rules'): pass")
    assert got == REVIEW and reasons == ["default of a changed"]
    assert all("rules" not in r and "do this" not in r for r in reasons)
