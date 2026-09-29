"""The call-binding check: does each call site still bind to the new signature?

Status: throwaway (Phase 0). Not part of the product.

    check_calls(change) -> [CallResult(call, result, high_confidence, label, reason)]
    callers_not_updated(change) -> the results that break or rebind

It binds each call's argument shape (how many positional arguments, which keywords, any
*expr or **expr) to the signature the way Python does, before and after the change:

  breaks        the call does not bind to the new signature (Python would raise TypeError):
                too many positional arguments, an unexpected keyword, a value given twice,
                a positional-only parameter passed by keyword, or a required one missing;
  rebinds       it binds, but a positional argument now reaches a different parameter (for
                example after two parameters swapped places);
  binds         it binds, every argument reaches the same parameter, and nothing it leaves
                out got a new default;
  needs review  anything the check cannot decide: argument unpacking that may or may not fill
                the gap, a parameter renamed in place, a keyword now taken by **kwargs, a
                default the call relies on that changed or wasn't recorded, a self passed by
                keyword, a function made async or no longer async (whether the call awaits is
                not recorded), a removed function whose name may still resolve, or a decorator
                that may change how the function is called. Never "binds".
A call through the class (passes_first, as in Cls.m(obj, x)) passes self itself; a classmethod
still binds cls implicitly.
  not analysed  the function is not Python.

A result is high-confidence only when it comes from a resolved edge and a decided binding.
A call found by name only keeps its result but is labelled "name-only" and low confidence.
Python 3.9 or later.
"""
from dataclasses import dataclass

from effects_classify import PYTHON, decorator_name, wrapping_decorators
from effects_record import POSITIONAL_ONLY, CallSite, FunctionChange

BREAKS, REBINDS, BINDS, REVIEW, NOT_ANALYSED = "breaks", "rebinds", "binds", "needs review", "not analysed"
BOUND, FAILS, UNDECIDED = "bound", "fails", "undecided"


@dataclass(frozen=True)
class Bound:
    status: str               # bound | fails | undecided
    reason: str
    positions: tuple = ()      # the parameter each positional argument reaches ("*" for *args)
    keywords: tuple = ()       # (keyword, parameter it reaches, "**" for **kwargs)
    omitted: tuple = ()        # parameters with defaults that the call leaves out


@dataclass(frozen=True)
class CallResult:
    call: CallSite
    result: str
    high_confidence: bool
    label: str                 # "resolved" or "name-only"
    reason: str


def bind(sig, call):
    """How `call` binds to `sig`, following Python's rules for the argument shape."""
    count = call.positional
    if sig.implicit_first and sig.implicit_first in call.keywords:
        return Bound(UNDECIDED, f"the call names {sig.implicit_first} by keyword")
    classmethod_ = any(decorator_name(d)[1] == "classmethod" for d in sig.decorators)
    if call.passes_first and sig.implicit_first and not classmethod_:   # Cls.m(obj, ...): self is not bound
        if count == 0 and not call.star_args:
            return Bound(FAILS, f"missing {sig.implicit_first}")
        count = max(count - 1, 0)
    slots = sig.positional
    star, kwargs = sig.var_positional, sig.var_keyword
    if count > len(slots) and star is None:
        return Bound(FAILS, f"takes {len(slots)} positional arguments but {count} were given")
    by_position = [p.name for p in slots[:count]] + ["*"] * max(count - len(slots), 0)
    keywords = []
    for k in call.keywords:
        p = sig.named(k)
        if p is None or p.kind == POSITIONAL_ONLY:
            if kwargs is None:
                return Bound(FAILS, f"unexpected keyword {k}" if p is None
                             else f"positional-only parameter {k} passed by keyword")
            keywords.append((k, "**"))
        elif k in by_position:
            return Bound(FAILS, f"{k} given by position and by keyword")
        else:
            keywords.append((k, k))
    if len(set(call.keywords)) != len(call.keywords):
        return Bound(FAILS, "a keyword is given twice")
    if call.star_args or call.star_kwargs:
        return Bound(UNDECIDED, "argument unpacking (*expr or **expr) may fill or clash with any parameter")
    filled = set(by_position) | {target for _, target in keywords}
    missing = [p.name for p in sig.params if p.required and p.name not in filled]
    if missing:
        return Bound(FAILS, "missing required " + ", ".join(missing))
    omitted = tuple(p.name for p in sig.params if not p.variadic and p.name not in filled)
    return Bound(BOUND, "binds", tuple(by_position), tuple(keywords), omitted)


def _compare(change, old, new):
    """(result, reason) for a call that binds to the new signature."""
    if old.status != BOUND:
        return BINDS, "binds to the new signature"           # written for the new signature
    before, after = change.before, change.after
    for i, (a, b) in enumerate(zip(old.positions, new.positions)):
        if a == b:
            continue
        if before.named(b) is None and after.named(a) is None and a != "*" and b != "*":
            return REVIEW, f"argument {i + 1} now reaches {b}, which replaced {a} at that position"
        return REBINDS, f"argument {i + 1} now reaches {b}, not {a}"
    for (k, a), (_, b) in zip(old.keywords, new.keywords):
        if a != b:
            return REVIEW, f"keyword {k} now reaches {b if b != '**' else '**kwargs'}"
    for name in new.omitted:
        bp, ap = before.named(name), after.named(name)
        if bp is None or not bp.has_default:
            continue                                         # a new optional parameter left out
        if bp.default is None or ap.default is None:
            return REVIEW, f"the call relies on the default of {name}, which was not recorded"
        if bp.default != ap.default:
            return REVIEW, f"the call relies on the default of {name}, which changed"
    return BINDS, "binds the same way"


def check_call(change: FunctionChange, call: CallSite) -> CallResult:
    label = "resolved" if call.resolved else "name-only"
    if change.language != PYTHON:
        return CallResult(call, NOT_ANALYSED, False, label, f"{change.language} is not analysed")
    if change.after is None:
        if change.still_resolves is False:
            return CallResult(call, BREAKS, call.resolved, label, "the function was removed and its name no "
                              "longer resolves")
        return CallResult(call, REVIEW, False, label, "the function was removed here; the name may still resolve")
    if change.before is not None and change.before.is_async != change.after.is_async:
        return CallResult(call, REVIEW, False, label, "it was made async or no longer async; whether the call "
                          "awaits is not recorded")
    new = bind(change.after, call)
    if call.passes_first and change.before is not None \
            and change.before.implicit_first != change.after.implicit_first:
        return CallResult(call, REVIEW, False, label, "the call passes the first parameter itself, and how "
                          "that parameter is bound changed")
    if new.status == FAILS:
        result, reason = BREAKS, new.reason
    elif new.status == UNDECIDED:
        result, reason = REVIEW, new.reason
    elif change.before is None:
        result, reason = BINDS, "binds to the new function"
    else:
        result, reason = _compare(change, bind(change.before, call), new)
    wrapping = wrapping_decorators(change.before) + wrapping_decorators(change.after)
    if wrapping and result != REVIEW:
        result, reason = REVIEW, f"decorator {wrapping[0]} may change how it is called ({reason})"
    high = call.resolved and result in (BREAKS, REBINDS, BINDS)
    return CallResult(call, result, high, label, reason)


def check_calls(change: FunctionChange):
    return [check_call(change, c) for c in change.call_sites]


def callers_not_updated(change: FunctionChange):
    """Calls that break or rebind under the new signature, resolved and name-only alike;
    each keeps its label and confidence."""
    return [r for r in check_calls(change) if r.result in (BREAKS, REBINDS)]
