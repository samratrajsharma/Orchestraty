"""What a change does to a Python function's or class's contract.

``classify`` compares the two signatures the engine captured for one symbol (parameters with
their kind, default and annotation; the return annotation; decorators; async) and says
whether callers can break:

* breaking: a parameter removed or renamed, positional parameters reordered, a new parameter
  callers must pass, a default removed, a parameter that can no longer be passed by position
  or by name, ``*args`` or ``**kwargs`` removed, a function made async or no longer async;
  for a class, a base class removed;
* review: a return or parameter annotation changed, a default value changed, decorators
  changed; for a class, its decorators, its other bases or a metaclass changed;
* compatible: an optional parameter added, ``*args`` or ``**kwargs`` added, a parameter
  opened up (positional-only to positional-or-keyword, keyword-only to positional at the end).

Each verdict comes with short reasons for the notes, made only of parameter names.
"""
POSITIONAL_ONLY = "positional_only"
POSITIONAL_OR_KEYWORD = "positional_or_keyword"
VAR_POSITIONAL = "var_positional"
KEYWORD_ONLY = "keyword_only"
VAR_KEYWORD = "var_keyword"
POSITIONAL = (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)
BY_NAME = (POSITIONAL_OR_KEYWORD, KEYWORD_ONLY)

BREAKING, REVIEW, COMPATIBLE, NONE = "breaking", "review", "compatible", "none"
_RANK = {NONE: 0, COMPATIBLE: 1, REVIEW: 2, BREAKING: 3}


def worst(*verdicts):
    return max(verdicts, key=_RANK.__getitem__) if verdicts else NONE


def _params(sig):
    return [p for p in (sig or {}).get("params", []) if isinstance(p, dict) and p.get("name")]


def classify_function(old, new):
    """(verdict, reasons) for a function's or method's two signatures."""
    reasons, verdicts = [], []

    def say(verdict, reason):
        verdicts.append(verdict)
        reasons.append(reason)

    before, after = _params(old), _params(new)
    old_by_name = {p["name"]: p for p in before if p["kind"] not in (VAR_POSITIONAL, VAR_KEYWORD)}
    new_by_name = {p["name"]: p for p in after if p["kind"] not in (VAR_POSITIONAL, VAR_KEYWORD)}
    old_pos = [p["name"] for p in before if p["kind"] in POSITIONAL]
    new_pos = [p["name"] for p in after if p["kind"] in POSITIONAL]
    has = lambda params, kind: any(p["kind"] == kind for p in params)          # noqa: E731

    for name, p in old_by_name.items():
        q = new_by_name.get(name)
        if q is None:
            say(BREAKING, f"parameter {name} removed")
            continue
        if p["has_default"] and not q["has_default"]:
            say(BREAKING, f"parameter {name} is now required")
        elif p["has_default"] and p.get("default") != q.get("default"):
            say(REVIEW, f"default of {name} changed")
        if p["kind"] in POSITIONAL and q["kind"] not in POSITIONAL:
            say(BREAKING, f"parameter {name} can no longer be passed by position")
        if p["kind"] in BY_NAME and q["kind"] not in BY_NAME:
            say(BREAKING, f"parameter {name} can no longer be passed by name")
        if p.get("annotation") != q.get("annotation"):
            say(REVIEW, f"type of {name} changed")
    kept = [n for n in old_pos if n in new_pos]
    inserted = set()
    if [n for n in new_pos if n in kept] != kept:
        say(BREAKING, "positional parameters reordered")
    elif kept:
        # Anything new placed before an existing positional parameter shifts the arguments
        # every positional call passes.
        last = max(new_pos.index(n) for n in kept)
        for name in new_pos[:last]:
            if name not in old_pos:
                inserted.add(name)
                say(BREAKING, f"parameter {name} placed before existing ones")
    for name, q in new_by_name.items():
        if name in old_by_name or name in inserted:
            continue
        if not q["has_default"]:
            say(BREAKING, f"new required parameter {name}")
        else:
            say(COMPATIBLE, f"new optional parameter {name}")
    for kind, label in ((VAR_POSITIONAL, "*args"), (VAR_KEYWORD, "**kwargs")):
        if has(before, kind) and not has(after, kind):
            say(BREAKING, f"{label} removed")
        elif has(after, kind) and not has(before, kind):
            say(COMPATIBLE, f"{label} added")
    old, new = old or {}, new or {}
    if bool(old.get("is_async")) != bool(new.get("is_async")):
        say(BREAKING, "now async" if new.get("is_async") else "no longer async")
    if old.get("returns") != new.get("returns"):
        say(REVIEW, "return type changed")
    if old.get("decorators", []) != new.get("decorators", []):
        say(REVIEW, "decorators changed")
    if old.get("type_params") != new.get("type_params"):
        say(REVIEW, "type parameters changed")
    return worst(*verdicts), reasons


def classify_class(old, new):
    """(verdict, reasons) for a class's two signatures (decorators and bases)."""
    old, new = old or {}, new or {}
    before, after = old.get("bases", []), new.get("bases", [])
    reasons, verdicts = [], []
    removed = [b for b in before if b not in after and "=" not in b]
    if removed:
        verdicts.append(BREAKING)
        reasons.append("base class removed")
    if [b for b in after if b not in before] or [b for b in before if b not in after and "=" in b]:
        verdicts.append(REVIEW)
        reasons.append("base classes changed")
    if old.get("decorators", []) != new.get("decorators", []):
        verdicts.append(REVIEW)
        reasons.append("decorators changed")
    if old.get("type_params") != new.get("type_params"):
        verdicts.append(REVIEW)
        reasons.append("type parameters changed")
    return worst(*verdicts), reasons


def classify(kind, old, new):
    """(verdict, reasons) for one symbol whose signature changed; kind is the engine's
    symbol kind (function, method or class)."""
    if kind == "class":
        return classify_class(old, new)
    return classify_function(old, new)
