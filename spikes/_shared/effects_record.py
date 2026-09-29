"""The effects module's input: one function's signature before and after a change, and its
call sites with their arguments, lines and evidence.

Status: throwaway (Phase 0). Not part of the product.

Everything in the effects module reads only this record, never the engine's internals. A thin
adapter fills it from whatever finds signatures and call edges (the engine's signature capture
and resolver later, synthetic code in the tests now). Records are plain data and round-trip
through JSON with to_dict and from_dict.

    FunctionChange   id, name, file, line, language, before, after, call_sites, still_resolves
      before/after   a Signature, or None when the function was added or removed
      still_resolves after a removal: whether the name still resolves at the call sites (the
                     function moved, is re-exported or inherited). None when not recorded
    Signature        params (in order), is_async, returns, decorators, implicit_first
      implicit_first the name of a first parameter that is bound implicitly (self or cls);
                     it is kept out of params. None for a plain function or a staticmethod
    Param            name, kind, has_default, default (source text, when known), annotation
      kind           positional_only | positional_or_keyword | var_positional |
                     keyword_only | var_keyword  (Python's parameter kinds)
    CallSite         file, line, positional (plain positional arguments), keywords (names),
                     star_args (a *expr), star_kwargs (a **expr), passes_first (the call
                     goes through the class, as in Cls.m(obj, ...), so a self parameter is not
                     bound implicitly; a classmethod's cls still is),
                     resolved (the edge is resolved; False means it was found by name only),
                     evidence (how the resolver found it, as it tags it)

signature_from_source and call_site_from_node build these from Python source with the ast
module; they are parsed facts, and the tests use them to build records from synthetic code.
Python 3.9 or later.
"""
import ast
import textwrap
from dataclasses import asdict, dataclass
from typing import Optional, Tuple

POSITIONAL_ONLY = "positional_only"
POSITIONAL_OR_KEYWORD = "positional_or_keyword"
VAR_POSITIONAL = "var_positional"
KEYWORD_ONLY = "keyword_only"
VAR_KEYWORD = "var_keyword"
KINDS = (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL, KEYWORD_ONLY, VAR_KEYWORD)


@dataclass(frozen=True)
class Param:
    name: str
    kind: str = POSITIONAL_OR_KEYWORD
    has_default: bool = False
    default: Optional[str] = None
    annotation: Optional[str] = None

    @property
    def positional(self):
        return self.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)

    @property
    def keyword(self):
        return self.kind in (POSITIONAL_OR_KEYWORD, KEYWORD_ONLY)

    @property
    def variadic(self):
        return self.kind in (VAR_POSITIONAL, VAR_KEYWORD)

    @property
    def required(self):
        return not self.variadic and not self.has_default


@dataclass(frozen=True)
class Signature:
    params: Tuple[Param, ...] = ()
    is_async: bool = False
    returns: Optional[str] = None
    decorators: Tuple[str, ...] = ()
    implicit_first: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "params", tuple(self.params))
        object.__setattr__(self, "decorators", tuple(self.decorators))
        names, last, defaulted = set(), -1, False
        for p in self.params:
            if p.kind not in KINDS:
                raise ValueError(f"unknown parameter kind {p.kind!r}")
            if not p.name or p.name in names or p.name == self.implicit_first:
                raise ValueError(f"missing or repeated parameter name {p.name!r}")
            names.add(p.name)
            rank = KINDS.index(p.kind)
            if rank < last or (rank == last and p.variadic):
                raise ValueError(f"parameter {p.name!r} is out of Python's order")
            last = rank
            if p.variadic and p.has_default:
                raise ValueError(f"*{p.name} cannot have a default")
            if p.positional:
                if defaulted and not p.has_default:
                    raise ValueError(f"{p.name!r} has no default but follows one that has")
                defaulted = defaulted or p.has_default

    @property
    def positional(self):
        return [p for p in self.params if p.positional]

    @property
    def var_positional(self):
        return next((p for p in self.params if p.kind == VAR_POSITIONAL), None)

    @property
    def var_keyword(self):
        return next((p for p in self.params if p.kind == VAR_KEYWORD), None)

    def named(self, name):
        """The non-variadic parameter called `name`, or None."""
        return next((p for p in self.params if p.name == name and not p.variadic), None)


@dataclass(frozen=True)
class CallSite:
    file: str
    line: int
    positional: int = 0
    keywords: Tuple[str, ...] = ()
    star_args: bool = False
    star_kwargs: bool = False
    passes_first: bool = False
    resolved: bool = False
    evidence: str = ""

    def __post_init__(self):
        object.__setattr__(self, "keywords", tuple(self.keywords))
        if self.positional < 0:
            raise ValueError("a call cannot have fewer than 0 positional arguments")


@dataclass(frozen=True)
class FunctionChange:
    id: str
    name: str
    file: str = ""
    line: int = 0
    language: str = "python"
    before: Optional[Signature] = None
    after: Optional[Signature] = None
    call_sites: Tuple[CallSite, ...] = ()
    still_resolves: Optional[bool] = None   # after a removal: does the name still resolve (moved,
    #                                         re-exported, inherited)? None = not recorded

    def __post_init__(self):
        object.__setattr__(self, "call_sites", tuple(self.call_sites))
        if self.before is None and self.after is None:
            raise ValueError("a change needs a signature before or after")


# ----------------------------------------------------------------- JSON


def to_dict(change):
    return asdict(change)


def _signature(d):
    if d is None:
        return None
    return Signature(params=tuple(Param(**p) for p in d.get("params", ())), is_async=d.get("is_async", False),
                     returns=d.get("returns"), decorators=tuple(d.get("decorators", ())),
                     implicit_first=d.get("implicit_first"))


def from_dict(d):
    return FunctionChange(id=d["id"], name=d["name"], file=d.get("file", ""), line=d.get("line", 0),
                          language=d.get("language", "python"), before=_signature(d.get("before")),
                          after=_signature(d.get("after")),
                          call_sites=tuple(CallSite(**c) for c in d.get("call_sites", ())),
                          still_resolves=d.get("still_resolves"))


# ----------------------------------------------------------------- from Python source


def _text(node):
    return None if node is None else ast.unparse(node)


def signature_from_node(fn, implicit_first=None):
    """A Signature from an ast FunctionDef or AsyncFunctionDef. With implicit_first ("self" or
    "cls"), the first parameter, which must have that name, is bound implicitly."""
    a = fn.args
    positional = list(a.posonlyargs) + list(a.args)
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    params = []
    for i, arg in enumerate(positional):
        kind = POSITIONAL_ONLY if i < len(a.posonlyargs) else POSITIONAL_OR_KEYWORD
        params.append(Param(arg.arg, kind, defaults[i] is not None, _text(defaults[i]), _text(arg.annotation)))
    if a.vararg:
        params.append(Param(a.vararg.arg, VAR_POSITIONAL, annotation=_text(a.vararg.annotation)))
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        params.append(Param(arg.arg, KEYWORD_ONLY, default is not None, _text(default), _text(arg.annotation)))
    if a.kwarg:
        params.append(Param(a.kwarg.arg, VAR_KEYWORD, annotation=_text(a.kwarg.annotation)))
    if implicit_first:
        if not params or params[0].name != implicit_first or not params[0].positional:
            raise ValueError(f"the first parameter is not {implicit_first!r}")
        params = params[1:]
    return Signature(tuple(params), isinstance(fn, ast.AsyncFunctionDef), _text(fn.returns),
                     tuple(ast.unparse(d) for d in fn.decorator_list), implicit_first)


def signature_from_source(source, implicit_first=None):
    """The Signature of the first function defined in `source` (a def, with its decorators)."""
    tree = ast.parse(textwrap.dedent(source))
    fn = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if fn is None:
        raise ValueError("no function definition in the source")
    return signature_from_node(fn, implicit_first)


def call_site_from_node(node, file, resolved, evidence="", passes_first=False):
    """A CallSite from an ast Call: counts plain positional arguments and names keywords."""
    return CallSite(file=file, line=node.lineno,
                    positional=sum(1 for a in node.args if not isinstance(a, ast.Starred)),
                    keywords=tuple(k.arg for k in node.keywords if k.arg is not None),
                    star_args=any(isinstance(a, ast.Starred) for a in node.args),
                    star_kwargs=any(k.arg is None for k in node.keywords),
                    passes_first=passes_first, resolved=resolved, evidence=evidence)
