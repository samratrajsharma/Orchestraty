"""What a change does to a Python function's or class's contract, and which calls it breaks.

``classify`` compares the two signatures the engine captured for one symbol (parameters with
their kind, default and annotation; the return annotation; decorators; async) and says
whether callers can break:

* breaking: a parameter removed or renamed, positional parameters reordered, a new parameter
  callers must pass, a default removed, a parameter that can no longer be passed by position
  or by name, ``*args`` or ``**kwargs`` removed, a function made async or no longer async, a
  plain method made a staticmethod or a classmethod or back (it breaks only the calls made on
  the class), a method made a function or back; for a class, a base class removed;
* review: a return or parameter annotation changed, a default value changed, decorators
  changed; for a class, its decorators, its other bases or a metaclass changed;
* compatible: an optional parameter added, ``*args`` or ``**kwargs`` added, a parameter
  opened up (positional-only to positional-or-keyword, keyword-only to positional at the end),
  a positional-only parameter renamed, a staticmethod made a classmethod or back.

Each verdict comes with short reasons for the notes, made only of parameter names. For each
breaking reason, ``breaks`` says which calls it breaks, as conditions on how a call passes
its arguments (the engine records that for every Python call):

* ``["any"]``: every call;
* ``["name", p]``: a call that passes ``p`` by name;
* ``["positional", n]``: a call that passes ``n`` or more arguments by position;
* ``["missing", p, i]``: a call that passes ``p`` neither by name nor at position ``i``
  (``i`` is None for a keyword-only parameter);
* ``["keywords", names]``: a call that passes a keyword not in ``names``;
* ``["on_class"]``: a call made on the class (``Cls.m(...)``, or ``cls.m(...)`` in a
  classmethod), not on an instance.

Positions count the arguments a call writes, so for a method they start after ``self`` or
``cls``. So a parameter renamed in place breaks only the calls that pass it by name, and a
parameter removed from the end only the calls that pass that many arguments by position.
``breaking_reasons`` matches one use against them, cautiously: a call that unpacks ``*x`` or
``**x`` can break, and so can a use with no recorded calls (an inheritance, a call from JS).
"""
import re

POSITIONAL_ONLY = "positional_only"
POSITIONAL_OR_KEYWORD = "positional_or_keyword"
VAR_POSITIONAL = "var_positional"
KEYWORD_ONLY = "keyword_only"
VAR_KEYWORD = "var_keyword"
POSITIONAL = (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)
BY_NAME = (POSITIONAL_OR_KEYWORD, KEYWORD_ONLY)

BREAKING, REVIEW, COMPATIBLE, NONE = "breaking", "review", "compatible", "none"
_RANK = {NONE: 0, COMPATIBLE: 1, REVIEW: 2, BREAKING: 3}
ANY = ["any"]
ON_CLASS = ["on_class"]
_BINDINGS = {"staticmethod": "static", "classmethod": "class"}
_AS = {"static": "a staticmethod", "class": "a classmethod", "instance": "a plain method", "": "a function"}


# Every reason classify and the live map give, as a template: {id} is a parameter's name and
# {path} a repository path. A reason shared with a teammate must match one of these forms
# (REASON_FORMS), so a reason can never carry other words.
REASON_TEMPLATES = (
    "now a staticmethod", "now a classmethod", "now a plain method", "now a function", "now a class",
    "now async", "no longer async",
    "parameter {id} renamed to {id}", "type of {id} changed", "default of {id} changed",
    "parameter {id} removed", "parameter {id} is now required",
    "parameter {id} can no longer be passed by position", "parameter {id} can no longer be passed by name",
    "positional parameters reordered", "parameter {id} placed before existing ones",
    "new required parameter {id}", "new optional parameter {id}",
    "*args removed", "*args added", "**kwargs removed", "**kwargs added",
    "return type changed", "decorators changed", "type parameters changed",
    "base class removed", "base classes changed",
    "removed", "moved from {path}",
)
_PLACES = {"{id}": "[A-Za-z_][A-Za-z0-9_]{0,63}", "{path}": "(?P<path>.+)"}


def _form(template):
    """A template as a regular expression: its words literal, its places as their patterns."""
    return re.compile("".join(_PLACES.get(part) or re.escape(part)
                              for part in re.split(r"(\{id\}|\{path\})", template)))


REASON_FORMS = tuple(_form(t) for t in REASON_TEMPLATES)


def worst(*verdicts):
    return max(verdicts, key=_RANK.__getitem__) if verdicts else NONE


def _params(sig):
    return [p for p in (sig or {}).get("params", []) if isinstance(p, dict) and p.get("name")]


def _binding(sig, kind):
    """How a function gets its first argument: "instance" or "class" (a method's self or cls,
    which calls do not write), "static" for a staticmethod, "" for a plain function."""
    if kind != "method":
        return ""
    for d in (sig or {}).get("decorators", []):
        name = str(d).split("(", 1)[0].rsplit(".", 1)[-1].strip()
        if name in _BINDINGS:
            return _BINDINGS[name]
    return "instance"


def _binding_change(was, now):
    """(verdict, conditions) for a method whose binding changed. A call on an instance gets the
    same arguments whether the method is a plain method, a staticmethod or a classmethod; only a
    call made on the class can break, and only where one side is a plain method. A method made a
    function, or back, breaks every call."""
    if "" in (was, now):
        return BREAKING, [ANY]
    if "instance" in (was, now):
        return BREAKING, [ON_CLASS]
    return COMPATIBLE, []                         # a staticmethod made a classmethod, or back


def _after_receiver(params, binding):
    """The parameters calls write: for a method, those after self or cls."""
    if binding in ("instance", "class") and params and params[0]["kind"] in POSITIONAL:
        return params[1:]
    return params


def classify_function(old, new, kind="function", old_kind=None):
    """(verdict, reasons, breaks) for a function's or method's two signatures; breaks is
    {breaking reason: [condition]}."""
    reasons, verdicts, breaks = [], [], {}

    def say(verdict, reason, *conditions):
        verdicts.append(verdict)
        reasons.append(reason)
        if verdict == BREAKING:
            known = breaks.setdefault(reason, [])
            for c in conditions or (ANY,):
                if c not in known:
                    known.append(c)

    was, now = _binding(old, old_kind or kind), _binding(new, kind)
    if was != now:
        verdict, conditions = _binding_change(was, now)
        say(verdict, f"now {_AS[now]}", *conditions)
    before, after = _after_receiver(_params(old), was), _after_receiver(_params(new), now)
    old_by_name = {p["name"]: p for p in before if p["kind"] not in (VAR_POSITIONAL, VAR_KEYWORD)}
    new_by_name = {p["name"]: p for p in after if p["kind"] not in (VAR_POSITIONAL, VAR_KEYWORD)}
    old_pos = [p["name"] for p in before if p["kind"] in POSITIONAL]
    new_pos = [p["name"] for p in after if p["kind"] in POSITIONAL]
    has = lambda params, k: any(p["kind"] == k for p in params)                 # noqa: E731
    missing = lambda n: ["missing", n, new_pos.index(n) if n in new_pos else None]   # noqa: E731

    # A parameter renamed in place: the same position, kind and default, another name.
    renamed = {}
    for o, n in zip(old_pos, new_pos):
        if (o != n and o not in new_by_name and n not in old_by_name and
                old_by_name[o]["kind"] == new_by_name[n]["kind"] and
                old_by_name[o]["has_default"] == new_by_name[n]["has_default"]):
            renamed[o] = n
    for o, n in renamed.items():
        p, q = old_by_name[o], new_by_name[n]
        if p["kind"] == POSITIONAL_ONLY:
            say(COMPATIBLE, f"parameter {o} renamed to {n}")             # no call passes it by name
        else:
            say(BREAKING, f"parameter {o} renamed to {n}", ["name", o])
        if p.get("annotation") != q.get("annotation"):
            say(REVIEW, f"type of {n} changed")
        if p["has_default"] and p.get("default") != q.get("default"):
            say(REVIEW, f"default of {n} changed")

    for name, p in old_by_name.items():
        if name in renamed:
            continue
        q = new_by_name.get(name)
        if q is None:
            conditions = [["name", name]] if p["kind"] in BY_NAME else []
            if p["kind"] in POSITIONAL:
                conditions.append(["positional", old_pos.index(name) + 1])
            say(BREAKING, f"parameter {name} removed", *conditions)
            continue
        if p["has_default"] and not q["has_default"]:
            say(BREAKING, f"parameter {name} is now required", missing(name))
        elif p["has_default"] and p.get("default") != q.get("default"):
            say(REVIEW, f"default of {name} changed")
        if p["kind"] in POSITIONAL and q["kind"] not in POSITIONAL:
            say(BREAKING, f"parameter {name} can no longer be passed by position",
                ["positional", old_pos.index(name) + 1])
        if p["kind"] in BY_NAME and q["kind"] not in BY_NAME:
            say(BREAKING, f"parameter {name} can no longer be passed by name", ["name", name])
        if p.get("annotation") != q.get("annotation"):
            say(REVIEW, f"type of {name} changed")

    kept = [n for n in old_pos if n in new_pos]
    order = [n for n in new_pos if n in kept]
    if order != kept:
        first = min(old_pos.index(n) for n, m in zip(kept, order) if n != m)
        say(BREAKING, "positional parameters reordered", ["positional", first + 1])
    inserted = set()
    if kept:
        # Anything new placed before an existing positional parameter shifts the arguments
        # every positional call passes from there on.
        last = max(new_pos.index(n) for n in kept)
        for name in new_pos[:last]:
            if name not in old_pos and name not in renamed.values():
                inserted.add(name)
                conditions = [["positional", new_pos.index(name) + 1]]
                if not new_by_name[name]["has_default"]:
                    conditions.append(missing(name))
                say(BREAKING, f"parameter {name} placed before existing ones", *conditions)
    for name, q in new_by_name.items():
        if name in old_by_name or name in inserted or name in renamed.values():
            continue
        if not q["has_default"]:
            say(BREAKING, f"new required parameter {name}", missing(name))
        else:
            say(COMPATIBLE, f"new optional parameter {name}")
    if has(before, VAR_POSITIONAL) and not has(after, VAR_POSITIONAL):
        # breaks the calls that passed more arguments by position than the named parameters took
        say(BREAKING, "*args removed", ["positional", len(old_pos) + 1])
    elif has(after, VAR_POSITIONAL) and not has(before, VAR_POSITIONAL):
        say(COMPATIBLE, "*args added")
    if has(before, VAR_KEYWORD) and not has(after, VAR_KEYWORD):
        # breaks the calls that passed a keyword no named parameter took
        say(BREAKING, "**kwargs removed", ["keywords", sorted(n for n, p in old_by_name.items() if p["kind"] in BY_NAME)])
    elif has(after, VAR_KEYWORD) and not has(before, VAR_KEYWORD):
        say(COMPATIBLE, "**kwargs added")
    old, new = old or {}, new or {}
    if bool(old.get("is_async")) != bool(new.get("is_async")):
        say(BREAKING, "now async" if new.get("is_async") else "no longer async", ANY)
    if old.get("returns") != new.get("returns"):
        say(REVIEW, "return type changed")
    if old.get("decorators", []) != new.get("decorators", []):
        say(REVIEW, "decorators changed")
    if old.get("type_params") != new.get("type_params"):
        say(REVIEW, "type parameters changed")
    return worst(*verdicts), reasons, breaks


def classify_class(old, new):
    """(verdict, reasons, breaks) for a class's two signatures (decorators and bases)."""
    old, new = old or {}, new or {}
    before, after = old.get("bases", []), new.get("bases", [])
    reasons, verdicts, breaks = [], [], {}
    removed = [b for b in before if b not in after and "=" not in b]
    if removed:
        verdicts.append(BREAKING)
        reasons.append("base class removed")
        breaks["base class removed"] = [ANY]
    if [b for b in after if b not in before] or [b for b in before if b not in after and "=" in b]:
        verdicts.append(REVIEW)
        reasons.append("base classes changed")
    if old.get("decorators", []) != new.get("decorators", []):
        verdicts.append(REVIEW)
        reasons.append("decorators changed")
    if old.get("type_params") != new.get("type_params"):
        verdicts.append(REVIEW)
        reasons.append("type parameters changed")
    return worst(*verdicts), reasons, breaks


def classify(kind, old, new, old_kind=None):
    """(verdict, reasons, breaks) for one symbol whose signature changed; kind is the
    engine's symbol kind (function, method or class), old_kind the one it had before when
    that differs (a move)."""
    old_kind = old_kind or kind
    if (old_kind == "class") != (kind == "class"):
        reason = "now a class" if kind == "class" else f"now {_AS[_binding(new, kind)]}"
        return BREAKING, [reason], {reason: [ANY]}
    if kind == "class":
        return classify_class(old, new)
    return classify_function(old, new, kind, old_kind)


def positional_room(sig, kind):
    """How many arguments a call can pass by position to a function or method with this
    signature (after self or cls); None when it takes any number (``*args``) or is not known."""
    if not sig or kind == "class":
        return None
    params = _after_receiver(_params(sig), _binding(sig, kind))
    if any(p["kind"] == VAR_POSITIONAL for p in params):
        return None
    return sum(1 for p in params if p["kind"] in POSITIONAL)


def call_breaks(shape, condition):
    """Whether a call passing its arguments as `shape` (the engine's {"positional", "star",
    "keywords", "double_star", "on_class"}) meets one condition. A call that unpacks *x or **x may pass
    anything there, so it meets every condition its unpacking could touch."""
    positional, keywords = shape.get("positional", 0), set(shape.get("keywords", ()))
    star, double_star = bool(shape.get("star")), bool(shape.get("double_star"))
    what = condition[0]
    if what == "name":
        return condition[1] in keywords or double_star
    if what == "positional":
        return positional >= condition[1] or star
    if what == "missing":
        name, index = condition[1], condition[2]
        return not (name in keywords or (index is not None and positional > index))
    if what == "keywords":
        return double_star or bool(keywords - set(condition[1]))
    if what == "on_class":
        return bool(shape.get("on_class", True))  # a shape that does not say counts as on the class
    return True                                   # "any", or a condition this version does not know


def breaking_reasons(breaks, shapes):
    """The reasons in `breaks` ({reason: [condition]}) under which a use whose calls pass
    their arguments as `shapes` can break, in order: every reason for a use with no recorded
    calls (shapes None or empty: an inheritance, a call from JS)."""
    if not shapes:
        return list(breaks)
    return [r for r, conditions in breaks.items() if any(call_breaks(s, c) for s in shapes for c in conditions)]
