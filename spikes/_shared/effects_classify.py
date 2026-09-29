"""Compatibility classes for a function's contract change: breaking, needs review, or compatible.

Status: throwaway (Phase 0). Not part of the product.

    classify(change) -> Classification(cls, reasons, high_confidence)

The rules are Python's calling rules, so they apply to Python only. A change in any other
language is classed "not analysed". From the callers' side, for Python:

  breaking     some call that was valid before now fails or passes a value to a different
               parameter: a required parameter added; a parameter removed or renamed where
               callers can name it; a default removed; *args or **kwargs removed; a parameter
               no longer accepted by position or by keyword; a positional parameter moved; a
               new positional slot that takes values *args used to take; the function removed
               and its name recorded as no longer resolving.
  needs review nothing certainly breaks, but a caller may behave differently or the effect
               can't be decided from the signature: a default value changed or not recorded; an
               annotation or the return annotation changed; a removed parameter now taken by
               *args/**kwargs; a new named parameter that takes a keyword **kwargs used to
               take; a positional-only parameter renamed in place; how the first parameter is
               bound changed; made async or no longer async (whether callers await is not
               recorded); removed where the name may still resolve (moved, re-exported,
               inherited); a decorator that may change how the function is called, or a
               change in the decorators.
  compatible   every call that was valid before binds the same way: optional parameters,
               *args or **kwargs added at the end, a default added, or no change at all.

Only "breaking" and "compatible" are high-confidence, and only when they come from the parsed
signatures alone: any decorator that may wrap the function lowers them to "needs review".
Decorators that leave the call signature alone (staticmethod, classmethod, abstractmethod,
override, final, lru_cache, cache, contextmanager, asynccontextmanager, and route decorators
of the form app.get("/path")) don't; mock.patch(...) is not a route. Python 3.9 or later.
"""
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from effects_record import FunctionChange, Signature

BREAKING, REVIEW, COMPATIBLE, NOT_ANALYSED = "breaking", "needs review", "compatible", "not analysed"
RANK = {COMPATIBLE: 0, REVIEW: 1, BREAKING: 2}
PYTHON = "python"
TRANSPARENT = {"staticmethod", "classmethod", "abstractmethod", "override", "final", "lru_cache", "cache",
               "contextmanager", "asynccontextmanager"}
ROUTE_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "route", "websocket", "api_route"}


@dataclass(frozen=True)
class Reason:
    code: str
    cls: str
    param: Optional[str]
    text: str


@dataclass(frozen=True)
class Classification:
    cls: str
    reasons: Tuple[Reason, ...]
    high_confidence: bool


def decorator_name(text):
    """'app.get("/x")' -> ('app.get', 'get'); 'functools.lru_cache(maxsize=2)' -> (..., 'lru_cache')."""
    head = text.split("(", 1)[0].strip().lstrip("@").strip()
    return head, head.rsplit(".", 1)[-1]


_ROUTE_PATH = re.compile(r"""^\(\s*[rRbBuU]{0,2}["']/""")


def is_transparent(text):
    """True for a decorator that returns the function with the same call signature. A route
    decorator counts only in the form obj.verb("/path", ...): mock.patch("pkg.f") shares the
    name 'patch' but injects an argument."""
    head, last = decorator_name(text)
    if last in TRANSPARENT:
        return True
    rest = text.strip().lstrip("@").strip()[len(head):].lstrip()
    return "." in head and last in ROUTE_VERBS and bool(_ROUTE_PATH.match(rest))


def wrapping_decorators(sig):
    return [d for d in (sig.decorators if sig else ()) if not is_transparent(d)]


def compare(before, after):
    """The reasons one signature differs from another, each with its class."""
    reasons = []

    def add(code, cls, param, text):
        reasons.append(Reason(code, cls, param, text))

    b_pos, a_pos = [p.name for p in before.positional], [p.name for p in after.positional]
    b_named = {p.name: p for p in before.params if not p.variadic}
    a_named = {p.name: p for p in after.params if not p.variadic}
    a_star, a_kw = after.var_positional is not None, after.var_keyword is not None

    if before.is_async != after.is_async:
        # Callers that don't await break when it becomes async; callers that awaited an awaitable
        # it already returned keep working. Which callers await is not in the record.
        add("async_changed", REVIEW, None, "is now async" if after.is_async else "is no longer async")
    if before.implicit_first != after.implicit_first:
        add("binding_changed", REVIEW, None,
            f"its first parameter is bound as {after.implicit_first or 'nothing'}, "
            f"not {before.implicit_first or 'nothing'}")

    # A parameter removed and one added at the same position, of the same kind, is a rename.
    renamed = {}
    for i, name in enumerate(b_pos):
        if name not in a_named and i < len(a_pos) and a_pos[i] not in b_named \
                and before.named(name).kind == after.named(a_pos[i]).kind:
            renamed[name] = a_pos[i]

    for name, p in b_named.items():
        if name in a_named or name in renamed:
            continue
        keyword_breaks = p.keyword and not a_kw
        positional_breaks = p.positional and not a_star
        if keyword_breaks or positional_breaks:
            add("param_removed", BREAKING, name, f"parameter {name} was removed")
        else:
            add("param_absorbed", REVIEW, name, f"parameter {name} was removed; *args/**kwargs now take it")

    for old, new in renamed.items():
        p = before.named(old)
        if p.keyword:
            cls = REVIEW if a_kw else BREAKING
            add("param_renamed", cls, old, f"parameter {old} was renamed to {new}; calls naming {old} "
                + ("now pass it into **kwargs" if a_kw else "fail"))
        else:
            add("param_renamed", REVIEW, old, f"positional-only parameter {old} became {new} at the same "
                "position; it may be a different parameter")

    pairs = [(before.named(n), after.named(n)) for n in b_named if n in a_named]
    pairs += [(before.named(o), after.named(n)) for o, n in renamed.items()]
    added = [p for n, p in a_named.items() if n not in b_named and n not in renamed.values()]

    for p in added:
        if p.required:
            add("required_added", BREAKING, p.name, f"required parameter {p.name} was added")
        else:
            add("optional_added", COMPATIBLE, p.name, f"optional parameter {p.name} was added")

    # A positional slot beyond the old ones takes values that *args used to take; a new named
    # parameter takes a keyword that **kwargs used to take.
    for name in a_pos[len(b_pos):]:
        if before.var_positional is not None and not (name in b_named and before.named(name).positional):
            add("args_taken", BREAKING, name, f"positional values that *args took now go to {name}")
    for p in added:
        if before.var_keyword is not None and p.keyword and not p.required:
            add("kwargs_taken", REVIEW, p.name, f"a keyword {p.name} that **kwargs took now binds to the parameter")
    for bp, ap in pairs:
        if before.var_keyword is not None and not bp.keyword and ap.keyword:
            add("kwargs_taken", REVIEW, ap.name, f"a keyword {ap.name} that **kwargs took now binds to the parameter")

    for bp, ap in pairs:
        name = ap.name
        if bp.positional and not ap.positional:
            add("not_positional", BREAKING, name, f"{name} can no longer be passed by position")
        if bp.keyword and not ap.keyword and bp.name == ap.name:
            add("not_keyword", REVIEW if (a_kw and not ap.required) else BREAKING, name,
                f"{name} can no longer be passed by keyword")
        if bp.positional and ap.positional and b_pos.index(bp.name) != a_pos.index(ap.name):
            add("position_changed", BREAKING, name, f"{name} moved from position {b_pos.index(bp.name) + 1} "
                f"to {a_pos.index(ap.name) + 1}")
        if bp.has_default and not ap.has_default:
            add("default_removed", BREAKING, name, f"{name} lost its default and is now required")
        elif ap.has_default and not bp.has_default:
            add("default_added", COMPATIBLE, name, f"{name} gained a default")
        elif bp.has_default and ap.has_default:
            if bp.default is None or ap.default is None:
                add("default_unknown", REVIEW, name, f"the default of {name} was not recorded on both sides")
            elif bp.default != ap.default:
                add("default_changed", REVIEW, name, f"the default of {name} changed from {bp.default} to {ap.default}")
        if bp.annotation != ap.annotation:
            add("annotation_changed", REVIEW, name, f"the annotation of {name} changed")

    for label, b_var, a_var in (("*args", before.var_positional, after.var_positional),
                                ("**kwargs", before.var_keyword, after.var_keyword)):
        if b_var and not a_var:
            add("var_removed", BREAKING, b_var.name, f"{label} was removed")
        elif a_var and not b_var:
            add("var_added", COMPATIBLE, a_var.name, f"{label} was added")
        elif b_var and a_var and b_var.annotation != a_var.annotation:
            add("annotation_changed", REVIEW, a_var.name, f"the annotation of {label} changed")

    if before.returns != after.returns:
        add("return_changed", REVIEW, None, "the return annotation changed")
    if set(before.decorators) != set(after.decorators):
        add("decorators_changed", REVIEW, None, "its decorators changed")
    return reasons


def classify(change: FunctionChange) -> Classification:
    if change.language != PYTHON:
        return Classification(NOT_ANALYSED, (Reason("language", NOT_ANALYSED, None,
                                                    f"{change.language} is not analysed"),), False)
    if change.before is None:
        reasons = [Reason("added", COMPATIBLE, None, "new function")]
    elif change.after is None:
        if change.still_resolves is False:
            reasons = [Reason("removed", BREAKING, None, "function removed, and its name no longer resolves")]
        else:                                   # moved, re-exported or inherited: callers may still work
            reasons = [Reason("removed", REVIEW, None, "function removed here; whether its name still resolves "
                              "was not recorded" if change.still_resolves is None else
                              "function removed here, but its name still resolves")]
    else:
        reasons = compare(change.before, change.after) or [Reason("unchanged", COMPATIBLE, None,
                                                                  "signature unchanged")]
    cls = max((r.cls for r in reasons), key=RANK.__getitem__)
    # A wrapping decorator can change the call signature, but not whether the name exists.
    wrapping = [] if change.before is None or change.after is None else \
        wrapping_decorators(change.before) + wrapping_decorators(change.after)
    if wrapping and cls != REVIEW:
        reasons.append(Reason("wrapped", REVIEW, None, f"decorator {wrapping[0]} may change how it is called"))
        cls = REVIEW
    return Classification(cls, tuple(reasons), cls in (BREAKING, COMPATIBLE))


def classify_signatures(before: Optional[Signature], after: Optional[Signature], language=PYTHON):
    """classify() for two signatures without a full record."""
    return classify(FunctionChange(id="", name="", language=language, before=before, after=after))
