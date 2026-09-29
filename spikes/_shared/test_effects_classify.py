"""Table-driven tests for the compatibility classes. Every signature is synthetic Python source."""
import pytest

from effects_classify import BREAKING, COMPATIBLE, NOT_ANALYSED, REVIEW, classify, classify_signatures
from effects_record import FunctionChange, Param, POSITIONAL_OR_KEYWORD, Signature, signature_from_source as sig

# label, before, after, class, a reason code that must be among the reasons
CASES = [
    ("unchanged", "def f(a, b=1): pass", "def f(a, b=1): pass", COMPATIBLE, "unchanged"),
    ("optional added at the end", "def f(a): pass", "def f(a, b=1): pass", COMPATIBLE, "optional_added"),
    ("required added", "def f(a): pass", "def f(a, b): pass", BREAKING, "required_added"),
    ("optional inserted before another", "def f(a, c=2): pass", "def f(a, b=1, c=2): pass", BREAKING,
     "position_changed"),
    ("required removed", "def f(a, b): pass", "def f(a): pass", BREAKING, "param_removed"),
    ("optional removed", "def f(a, b=1): pass", "def f(a): pass", BREAKING, "param_removed"),
    ("removed but taken by *args/**kwargs", "def f(a, b=1): pass", "def f(a, *args, **kwargs): pass", REVIEW,
     "param_absorbed"),
    ("keyword-only required added", "def f(a): pass", "def f(a, *, b): pass", BREAKING, "required_added"),
    ("keyword-only optional added", "def f(a): pass", "def f(a, *, b=1): pass", COMPATIBLE, "optional_added"),
    ("keyword-only removed", "def f(a, *, b=1): pass", "def f(a): pass", BREAKING, "param_removed"),
    ("keyword-only removed into **kwargs", "def f(a, *, b=1): pass", "def f(a, **kw): pass", REVIEW,
     "param_absorbed"),
    ("keyword-only order changed", "def f(*, a, b): pass", "def f(*, b, a): pass", COMPATIBLE, "unchanged"),
    ("made keyword-only", "def f(a, b): pass", "def f(a, *, b): pass", BREAKING, "not_positional"),
    ("keyword-only made positional", "def f(a, *, b): pass", "def f(a, b): pass", COMPATIBLE, "unchanged"),
    ("made positional-only", "def f(a, b): pass", "def f(a, b, /): pass", BREAKING, "not_keyword"),
    ("made positional-only, required, with **kwargs", "def f(a, b): pass", "def f(a, b, /, **kw): pass", BREAKING,
     "not_keyword"),
    ("made positional-only, optional, with **kwargs", "def f(a, /, b=1): pass", "def f(a, b=1, /, **kw): pass",
     REVIEW, "not_keyword"),
    ("positional-only widened", "def f(a, /): pass", "def f(a): pass", COMPATIBLE, "unchanged"),
    ("*args added", "def f(a): pass", "def f(a, *args): pass", COMPATIBLE, "var_added"),
    ("*args removed", "def f(a, *args): pass", "def f(a): pass", BREAKING, "var_removed"),
    ("**kwargs added", "def f(a): pass", "def f(a, **kw): pass", COMPATIBLE, "var_added"),
    ("**kwargs removed", "def f(a, **kw): pass", "def f(a): pass", BREAKING, "var_removed"),
    ("*args placed before a positional", "def f(a, b): pass", "def f(a, *args, b): pass", BREAKING,
     "not_positional"),
    ("default added", "def f(a, b): pass", "def f(a, b=1): pass", COMPATIBLE, "default_added"),
    ("default removed", "def f(a, b=1): pass", "def f(a, b): pass", BREAKING, "default_removed"),
    ("default changed", "def f(a, b=1): pass", "def f(a, b=2): pass", REVIEW, "default_changed"),
    ("keyword-only default changed", "def f(*, b=None): pass", "def f(*, b=[]): pass", REVIEW, "default_changed"),
    ("renamed", "def f(a, b): pass", "def f(a, c): pass", BREAKING, "param_renamed"),
    ("renamed, old name now into **kwargs", "def f(a, b=1): pass", "def f(a, c=1, **kw): pass", REVIEW,
     "param_renamed"),
    ("positional-only renamed in place", "def f(a, b, /): pass", "def f(a, c, /): pass", REVIEW, "param_renamed"),
    ("keyword-only renamed", "def f(*, b): pass", "def f(*, c): pass", BREAKING, "param_removed"),
    ("reordered", "def f(a, b): pass", "def f(b, a): pass", BREAKING, "position_changed"),
    ("annotation changed", "def f(a: int): pass", "def f(a: str): pass", REVIEW, "annotation_changed"),
    ("annotation added", "def f(a): pass", "def f(a: int): pass", REVIEW, "annotation_changed"),
    ("*args annotation changed", "def f(*a: int): pass", "def f(*a: str): pass", REVIEW, "annotation_changed"),
    ("return annotation changed", "def f(a) -> int: pass", "def f(a) -> str: pass", REVIEW, "return_changed"),
    ("made async", "def f(a): pass", "async def f(a): pass", REVIEW, "async_changed"),        # who awaits?
    ("no longer async", "async def f(a): pass", "def f(a): pass", REVIEW, "async_changed"),
    ("*args took the new positional", "def f(a, *args): pass", "def f(a, b=1, *args): pass", BREAKING,
     "args_taken"),
    ("keyword-only made positional where *args took values", "def f(a, *args, b=1): pass",
     "def f(a, b=1, *args): pass", BREAKING, "args_taken"),
    ("**kwargs took the new keyword", "def f(a, **kw): pass", "def f(a, b=1, **kw): pass", REVIEW,
     "kwargs_taken"),
    ("positional-only made keyword where **kwargs took it", "def f(a, /, **kw): pass", "def f(a, **kw): pass",
     REVIEW, "kwargs_taken"),
    ("mock.patch is no route", "@mock.patch('pkg.f')\ndef f(a): pass", "@mock.patch('pkg.f')\ndef f(a, b=1): pass",
     REVIEW, "wrapped"),
    ("route decorator kept", "@app.get('/u')\ndef h(a): pass", "@app.get('/u')\ndef h(a, b=1): pass",
     COMPATIBLE, "optional_added"),
    ("route verb changed", "@app.get('/u')\ndef h(a): pass", "@app.delete('/u')\ndef h(a): pass", REVIEW,
     "decorators_changed"),
    ("wrapping decorator, param removed", "@click.command()\ndef main(name): pass",
     "@click.command()\ndef main(): pass", REVIEW, "wrapped"),
    ("wrapping decorator, nothing changed", "@retry\ndef f(a): pass", "@retry\ndef f(a): pass", REVIEW, "wrapped"),
    ("overloads", "@overload\ndef f(a: int) -> int: ...", "@overload\ndef f(a: int) -> int: ...", REVIEW, "wrapped"),
    ("transparent decorators", "@functools.lru_cache(maxsize=2)\ndef f(a): pass",
     "@functools.lru_cache(maxsize=2)\ndef f(a, b=0): pass", COMPATIBLE, "optional_added"),
]


@pytest.mark.parametrize("label,before,after,expected,code", CASES, ids=[c[0] for c in CASES])
def test_python_contract_changes(label, before, after, expected, code):
    result = classify_signatures(sig(before), sig(after))
    assert result.cls == expected, [r.text for r in result.reasons]
    assert code in {r.code for r in result.reasons}
    assert result.high_confidence == (expected in (BREAKING, COMPATIBLE))


@pytest.mark.parametrize("before,after,implicit,expected", [
    ("def m(self, a): pass", "def m(self, a, b=1): pass", ("self", "self"), COMPATIBLE),
    ("def m(self, a): pass", "def m(self, b): pass", ("self", "self"), BREAKING),
    ("@classmethod\ndef m(cls, a): pass", "@classmethod\ndef m(cls, a, *, b): pass", ("cls", "cls"), BREAKING),
    ("def m(self, a): pass", "@staticmethod\ndef m(a): pass", ("self", None), REVIEW),
])
def test_methods_leave_the_implicit_first_parameter_out(before, after, implicit, expected):
    result = classify_signatures(sig(before, implicit[0]), sig(after, implicit[1]))
    assert result.cls == expected, [r.text for r in result.reasons]


def test_added_and_removed_functions():
    added = classify_signatures(None, sig("def f(a): pass"))
    assert (added.cls, added.high_confidence) == (COMPATIBLE, True)
    removed = FunctionChange(id="x", name="f", before=sig("@click.command()\ndef f(a): pass"), after=None,
                             still_resolves=False)
    assert (classify(removed).cls, classify(removed).high_confidence) == (BREAKING, True), \
        "a wrapper can't bring a removed name back"
    for still in (None, True):                    # moved, re-exported or inherited, or simply not recorded
        maybe = FunctionChange(id="x", name="f", before=sig("def f(a): pass"), after=None, still_resolves=still)
        assert (classify(maybe).cls, classify(maybe).high_confidence) == (REVIEW, False)


def test_a_default_that_was_not_recorded_is_never_read_as_unchanged():
    unknown = Signature((Param("a"), Param("b", POSITIONAL_OR_KEYWORD, has_default=True)))
    result = classify_signatures(unknown, unknown)
    assert result.cls == REVIEW and "default_unknown" in {r.code for r in result.reasons}


@pytest.mark.parametrize("language", ["typescript", "javascript", "go", "java", "rust", "ruby"])
def test_every_other_language_is_not_analysed(language):
    change = FunctionChange(id="x", name="f", language=language, before=sig("def f(a): pass"),
                            after=sig("def f(): pass"))
    result = classify(change)
    assert result.cls == NOT_ANALYSED and result.high_confidence is False
