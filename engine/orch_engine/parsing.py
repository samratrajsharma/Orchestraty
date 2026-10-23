from __future__ import annotations
import ast
import bisect
import hashlib
import json
import os
import re
from .models import ParsedFile, Symbol

JS_EXTS = {".js", ".jsx", ".mjs", ".cjs"}
TS_EXTS = {".ts", ".tsx"}
# languages parsed by the optional tree-sitter backend (Python and JS/TS have dedicated
# parsers above; these need tree-sitter, and degrade to searchable text without it).
_EXT_LANG = {
    ".go": "go", ".java": "java", ".rs": "rust", ".cs": "c_sharp", ".rb": "ruby",
    ".php": "php", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift",
    ".scala": "scala",
}


def _read(abs_path):
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""


# ---------------- call sites ----------------
# Every call a function makes is kept as (kind, name, attrs, line, ref), and a Python call
# also as (kind, name, attrs, line, ref, args), where args is how it passes its arguments:
# (positional count, uses *x, sorted keyword names, uses **x); see arg_shape.
#   name   the name the call starts with, as written: f in f(), m in m.f(), obj in obj.m()
#   attrs  the attribute names after it: () for f(), ("f",) for m.f(), ("b", "f") for a.b.f()
#   line   the line the call starts on
#   kind   how `name` is bound where the call is made, decided from this file alone:
#     same       a function or class defined at the top level of this file
#     import     an import; ref is its binding, ("module", dotted name) or
#                ("from", module, name, aliased); JS/TS modules are import specifiers
#     star       not bound here, but the file has `from m import *` (resolved against m's
#                literal __all__)
#     self       self.m() or cls.m(); this.m() in JS/TS
#     typed      obj.m() where obj = Cls() or obj: Cls; ref is (kind, name, ref) for Cls
#     bare       a plain name in a language without import bindings (tree-sitter)
#     local      a parameter of the calling function or a name assigned in it: never bound
#     global     a name assigned at module level that is not a def or an import
#     ambiguous  bound to two different things (try/except import alternatives)
#     free       bound nowhere in the file: a builtin, a global, or an undefined name
#     unknown    a receiver that is not a plain name: f().m(), x[0].m(), super().m()
# The graph turns these into edges (see graph.build_graph); only same, import, star, self,
# typed and bare can ever produce one.
AMBIGUOUS = ("ambiguous",)


def call_name(site):
    """The name a call site calls: the last attribute (m in obj.m()), else the first name."""
    return site[2][-1] if site[2] else (site[1] or "")


def arg_shape(call):
    """How one ast.Call passes its arguments: (positional count, uses *x, sorted keyword
    names, uses **x). The positional count leaves out starred arguments."""
    positional = sum(1 for a in call.args if a.__class__ is not ast.Starred)
    star = any(a.__class__ is ast.Starred for a in call.args)
    keywords = tuple(sorted(k.arg for k in call.keywords if k.arg is not None))
    double_star = any(k.arg is None for k in call.keywords)
    return (positional, star, keywords, double_star)


def site_args(site):
    """The args of a call site, or None when it has none (JS, TS, tree-sitter)."""
    return site[5] if len(site) > 5 else None


def _bind(table, name, binding):
    old = table.get(name)
    if old is None:
        table[name] = binding
    elif old != binding:
        table[name] = AMBIGUOUS


# ---------------- fingerprints ----------------
# A body hash is taken over the syntax tree, never the text: no line or column attributes, the
# docstring removed, and comments gone by construction. So reformatting, comment edits and
# docstring edits never change it, while a logic change always does.
_NORM_SKIP_FIELDS = frozenset(("kind", "type_comment"))
_NORM_FIELDS = {}                # node class -> its fields, without the skipped ones


def _norm(node, out, skip):
    """Append a canonical text form of the AST node `node` to `out`. Fields that are None or
    empty are left out, so a field a newer Python adds (type_params) changes nothing; a
    string's u prefix (Constant.kind) is ignored. Nodes whose id() is in `skip` are left out."""
    cls = node.__class__
    fields = _NORM_FIELDS.get(cls)
    if fields is None:
        fields = _NORM_FIELDS[cls] = tuple(f for f in cls._fields if f not in _NORM_SKIP_FIELDS)
    out.append(cls.__name__ + "(")
    for name in fields:
        value = getattr(node, name, None)
        if value is None:
            continue
        if isinstance(value, ast.AST):
            if name == "ctx":
                out.append(value.__class__.__name__)       # Load, Store or Del: no fields
            else:
                out.append(name + "=")
                _norm(value, out, skip)
        elif value.__class__ is list:
            if value:
                out.append(name + "=[")
                for item in value:
                    if not isinstance(item, ast.AST):
                        out.append(repr(item) + ",")
                    elif skip is None or id(item) not in skip:
                        _norm(item, out, skip)
                        out.append(",")
                out.append("]")
        else:
            out.append(name + "=" + repr(value))
    out.append(")")


def _digest(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def _tree_hash(nodes, skip=None, fallback=""):
    """Hash of a list of statements in canonical form (see _norm)."""
    out = ["["]
    try:
        for item in nodes:
            if skip is None or id(item) not in skip:
                _norm(item, out, skip)
                out.append(",")
    except RecursionError:
        # pathologically deep nesting: the text without whitespace stands in for the tree
        return _digest("text:" + "".join(fallback.split()))
    out.append("]")
    return _digest("".join(out))


def _without_docstring(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _src(node):
    """Normalised source text of an expression, so formatting never matters."""
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except (RecursionError, ValueError):
        return "<too deeply nested>"


# Parameter kinds, named as in Python's inspect module.
POSITIONAL_ONLY = "positional_only"
POSITIONAL_OR_KEYWORD = "positional_or_keyword"
VAR_POSITIONAL = "var_positional"
KEYWORD_ONLY = "keyword_only"
VAR_KEYWORD = "var_keyword"


def _param(arg, kind, default):
    return {"name": arg.arg, "kind": kind, "has_default": default is not None,
            "default": _src(default), "annotation": _src(arg.annotation)}


def _function_signature(node):
    """Every parameter (name, kind, default, annotation), the return annotation, the
    decorators and whether it is async, all as normalised source text."""
    a = node.args
    positional = list(a.posonlyargs) + list(a.args)
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    params = [_param(arg, POSITIONAL_ONLY if i < len(a.posonlyargs) else POSITIONAL_OR_KEYWORD, defaults[i])
              for i, arg in enumerate(positional)]
    if a.vararg is not None:
        params.append(_param(a.vararg, VAR_POSITIONAL, None))
    params.extend(_param(arg, KEYWORD_ONLY, d) for arg, d in zip(a.kwonlyargs, a.kw_defaults))
    if a.kwarg is not None:
        params.append(_param(a.kwarg, VAR_KEYWORD, None))
    sig = {"params": params, "returns": _src(node.returns),
           "decorators": [_src(d) for d in node.decorator_list],
           "is_async": isinstance(node, ast.AsyncFunctionDef)}
    if getattr(node, "type_params", None):
        sig["type_params"] = [_src(t) for t in node.type_params]
    return sig


def _class_signature(node):
    """A class's decorators and bases (keywords such as metaclass=M included)."""
    bases = [_src(b) for b in node.bases]
    bases += ["%s=%s" % (k.arg, _src(k.value)) if k.arg else "**" + _src(k.value) for k in node.keywords]
    sig = {"decorators": [_src(d) for d in node.decorator_list], "bases": bases}
    if getattr(node, "type_params", None):
        sig["type_params"] = [_src(t) for t in node.type_params]
    return sig


def _sig_hash(signature):
    return _digest(json.dumps(signature, sort_keys=True)) if signature else ""


def _class_statements(node):
    """A class's own statements: its body without the docstring and without the methods,
    which are symbols with hashes of their own."""
    return [s for s in _without_docstring(node.body)
            if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _accessor(node):
    """'.setter', '.deleter' or '.getter' for a property accessor (@x.setter def x), else ''."""
    for d in getattr(node, "decorator_list", []):
        if isinstance(d, ast.Attribute) and d.attr in ("setter", "deleter", "getter") \
                and isinstance(d.value, ast.Name) and d.value.id == node.name:
            return "." + d.attr
    return ""


# ---------------- Python (stdlib ast) ----------------
def _callee_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _deco_name(d):
    """Last component of a decorator expression: @app.get(...) -> 'get', @property -> 'property'."""
    if isinstance(d, ast.Call):
        d = d.func
    if isinstance(d, ast.Attribute):
        return d.attr
    if isinstance(d, ast.Name):
        return d.id
    return ""


def _module_all(tree):
    """Names listed in a module-level __all__ = [...] / (...)."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                return [e.value for e in node.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _has_main_guard(tree):
    """True iff the module has a real `if __name__ == '__main__':` at top level - not just
    the string '__main__' somewhere in a comment or docstring."""
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            t = node.test
            names = [t.left] + list(t.comparators)
            if any(isinstance(n, ast.Name) and n.id == "__name__" for n in names) and \
               any(isinstance(n, ast.Constant) and n.value == "__main__" for n in names):
                return True
    return False


def _from_module(node, pkg):
    """The absolute dotted module an ImportFrom names; relative imports are resolved against
    the file's package path from the indexed folder. None when a relative import climbs
    above the indexed folder: what it names is not in the index."""
    level = getattr(node, "level", 0) or 0
    if level - 1 > len(pkg):
        return None
    base = pkg[: len(pkg) - (level - 1)] if level else []
    return ".".join(base + (node.module.split(".") if node.module else []))


def _import_binds(node, pkg):
    """[(local name, binding)] for one import statement; ("*", module) for a star import.
    `import a.b` binds a to module a; `import a.b as c` binds c to module a.b. An import
    that climbs above the indexed folder binds nothing."""
    out = []
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.asname:
                out.append((a.asname, ("module", a.name)))
            else:
                root = a.name.split(".")[0]
                out.append((root, ("module", root)))
    else:
        mod = _from_module(node, pkg)
        if mod is None:
            return out
        for a in node.names:
            if a.name == "*":
                out.append(("*", mod))
            else:
                out.append((a.asname or a.name, ("from", mod, a.name, bool(a.asname) and a.asname != a.name)))
    return out


def _names_in(target):
    """The names an assignment target binds: a and b in (a, [b, *c]), but neither x nor i in
    x[i] = ..., which only stores into an existing object."""
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)]


def _stored_names(stmt):
    """Names a simple statement assigns, from its targets only: the values are never walked."""
    if isinstance(stmt, (ast.Assign, ast.Delete)):
        return [name for t in stmt.targets for name in _names_in(t)]
    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        return _names_in(stmt.target)
    return []


def _module_scope(tree, pkg):
    """What names mean at module level: import bindings, star imports, the top-level defs
    and classes, other assigned names, and how often each name is bound. A name both
    imported and bound another way (a def, an assignment, or an import of something else)
    is ambiguous."""
    bindings, stars, defs, assigned, stores = {}, [], set(), set(), {}

    def store(names):
        for name in names:
            stores[name] = stores.get(name, 0) + 1

    def visit(stmts):
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs.add(stmt.name)
                store([stmt.name])
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for name, b in _import_binds(stmt, pkg):
                    if name == "*":
                        if b not in stars:
                            stars.append(b)
                    else:
                        _bind(bindings, name, b)
                        store([name])
            elif isinstance(stmt, _BLOCK_STMTS):
                names = []
                if isinstance(stmt, (ast.For, ast.AsyncFor)):
                    names = _names_in(stmt.target)
                elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                    names = [n for item in stmt.items if item.optional_vars is not None
                             for n in _names_in(item.optional_vars)]
                names += [h.name for h in getattr(stmt, "handlers", []) if h.name]
                assigned.update(names)
                store(names)
                for suite in _block_suites(stmt):
                    visit(suite)
            else:
                names = _stored_names(stmt)
                assigned.update(names)
                store(names)

    visit(tree.body)
    for name in bindings:
        if name in defs or name in assigned:
            bindings[name] = AMBIGUOUS
    return {"bindings": bindings, "stars": stars, "defs": defs, "assigned": assigned, "stores": stores}


class _Scope:
    """One scope inside a function: the function itself, or a function, lambda or class
    nested in it. Comprehensions share the scope they are in."""
    __slots__ = ("parent", "is_class", "imports", "local", "glob", "nonlocal_", "stores")

    def __init__(self, parent=None, is_class=False):
        self.parent, self.is_class = parent, is_class
        self.imports, self.local, self.glob, self.nonlocal_, self.stores = {}, set(), set(), set(), {}

    def store(self, name):
        self.local.add(name)
        self.stores[name] = self.stores.get(name, 0) + 1


def _params(args):
    return [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs] + \
        [a.arg for a in (args.vararg, args.kwarg) if a is not None]


def _outer_parts(n):
    """What a nested def, lambda or class evaluates in the scope around it: decorators,
    defaults, annotations, bases."""
    parts = list(getattr(n, "decorator_list", []))
    if isinstance(n, ast.ClassDef):
        return parts + list(n.bases) + [k.value for k in n.keywords]
    a = n.args
    parts += [d for d in a.defaults + a.kw_defaults if d is not None]
    if not isinstance(n, ast.Lambda):
        args = a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x is not None]
        parts += [x.annotation for x in args if x.annotation is not None]
        if n.returns is not None:
            parts.append(n.returns)
    return parts


_CHILD_FIELDS = {}               # node class -> the fields that can hold child nodes
_BRANCHES = frozenset((ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert))
_SCOPES = frozenset((ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
_IMPORTS = frozenset((ast.Import, ast.ImportFrom))


def _scan_function(node, pkg):
    """One walk over a function's BODY: its Call nodes in source order, each with the scope
    it is made in, its cyclomatic complexity, and its own scope. A nested function, lambda
    or class opens a scope of its own, so what it binds never changes what a name means
    outside it; comprehensions share their scope, so a name bound in one counts as local to
    it, which can only hide an edge."""
    root = _Scope()
    for p in _params(node.args):
        root.store(p)
    calls, c = [], 1
    stack = [(n, root) for n in node.body]
    while stack:
        n, sc = stack.pop()
        cls = n.__class__
        if cls is ast.Name:
            if n.ctx.__class__ is not ast.Load:
                sc.store(n.id)
            continue                                  # no child nodes besides its context
        if cls is ast.Call:
            calls.append((n, sc))
        elif cls in _BRANCHES:
            c += 1
            if cls is ast.ExceptHandler and n.name:
                sc.store(n.name)
        elif cls is ast.BoolOp:
            c += len(n.values) - 1
        elif cls is ast.comprehension:
            c += 1 + len(n.ifs)
        elif cls in _SCOPES:
            if cls is not ast.Lambda:
                sc.store(n.name)
            inner = _Scope(sc, is_class=cls is ast.ClassDef)
            if cls is not ast.ClassDef:
                for p in _params(n.args):
                    inner.store(p)
            stack.extend((x, sc) for x in _outer_parts(n))
            stack.extend((x, inner) for x in (n.body if isinstance(n.body, list) else [n.body]))
            continue
        elif cls in _IMPORTS:
            for name, b in _import_binds(n, pkg):
                if name != "*":
                    _bind(sc.imports, name, b)
            continue
        elif cls is ast.Global:
            sc.glob.update(n.names)
            continue
        elif cls is ast.Nonlocal:
            sc.nonlocal_.update(n.names)
            continue
        elif cls is ast.MatchAs or cls is ast.MatchStar:
            if n.name:
                sc.store(n.name)
        elif cls is ast.MatchMapping:
            if n.rest:
                sc.store(n.rest)
        fields = _CHILD_FIELDS.get(cls)
        if fields is None:
            fields = _CHILD_FIELDS[cls] = tuple(f for f in cls._fields if f != "ctx")
        for f in fields:
            v = getattr(n, f, None)
            if v.__class__ is list:
                stack.extend((x, sc) for x in v if isinstance(x, ast.AST))
            elif isinstance(v, ast.AST):
                stack.append((v, sc))
    calls.sort(key=lambda pair: (pair[0].lineno, pair[0].col_offset))
    return calls, c, root


def _lookup(name, sc):
    """What `name` means where scope `sc` uses it, from the function scopes alone:
    ("import", binding), ("local", None) or ("ambiguous", None); None when no enclosing
    function binds it, so module level decides. A class body does not enclose the functions
    inside it."""
    first = True
    while sc is not None:
        if sc.is_class and not first:
            sc = sc.parent
            continue
        first = False
        if name in sc.glob:
            return None
        if name in sc.nonlocal_:
            sc = sc.parent
            continue
        b = sc.imports.get(name)
        if b is not None:
            return ("ambiguous", None) if b == AMBIGUOUS or name in sc.local else ("import", b)
        if name in sc.local:
            return ("local", None)
        sc = sc.parent
    return None


_NO_SCOPE = _Scope()


def _name_ref(name, sc, mscope):
    """(kind, ref) for a name used in scope `sc`: see the call-site kinds above."""
    found = _lookup(name, sc)
    if found is not None:
        return found
    b = mscope["bindings"].get(name)
    if b is not None:
        return ("ambiguous", None) if b == AMBIGUOUS else ("import", b)
    if name in mscope["defs"]:
        return ("same", None)
    if name in mscope["assigned"]:
        return ("global", None)
    if mscope["stars"]:
        return ("star", None)
    return ("free", None)


def _chain(func):
    """(base node, attribute names) of an attribute chain: a.b.c -> (Name a, ["b", "c"])."""
    attrs = []
    while isinstance(func, ast.Attribute):
        attrs.append(func.attr)
        func = func.value
    attrs.reverse()
    return func, tuple(attrs)


def _binds_in_root(name, sc, root):
    """True when `name`, used in scope `sc`, is bound by the function itself (`root`): no
    scope between `sc` and the function binds it again."""
    while sc is not root and sc is not None:
        if not sc.is_class and (name in sc.local or name in sc.imports or name in sc.glob):
            return False
        sc = sc.parent
    return sc is root and name in root.local and name not in root.glob


def _site(func, line, sc, root, self_name, mscope, local_types, module_types):
    """The call site for a call to `func` made in scope `sc` (see the call-site kinds above),
    or None. `self_name` is the method's own first parameter (self, cls), else None."""
    if isinstance(func, ast.Name):
        kind, ref = _name_ref(func.id, sc, mscope)
        return (kind, func.id, (), line, ref)
    if not isinstance(func, ast.Attribute):
        return None                                      # (lambda: x)(), f()() ...
    base, attrs = _chain(func)
    if not isinstance(base, ast.Name):
        return ("unknown", None, attrs, line, None)
    recv = base.id
    if recv == self_name and _binds_in_root(recv, sc, root):
        return ("self", recv, attrs, line, None) if len(attrs) == 1 else ("unknown", recv, attrs, line, None)
    if len(attrs) == 1:
        # obj.m() where obj's only binding gives its class: a parameter annotation or one
        # `obj = Cls()` in the function itself, else a module-level `obj = Cls()` bound once
        # that no enclosing function rebinds. Anything bound twice (a loop, if/else, a later
        # assignment) has no known class.
        cls = None
        if _binds_in_root(recv, sc, root):
            cls = None if recv in root.imports else local_types.get(recv)
        elif _lookup(recv, sc) is None:
            cls = module_types.get(recv)
        if cls is not None:
            ckind, cref = _name_ref(cls, sc, mscope)
            return ("typed", recv, attrs, line, (ckind, cls, cref))
    kind, ref = _name_ref(recv, sc, mscope)
    return (kind, recv, attrs, line, ref)


def _self_name(node, parent, decorators):
    """A method's own first parameter (self for a method, cls for a classmethod), or None
    for a function or a staticmethod."""
    if not parent or "staticmethod" in decorators:
        return None
    first = (node.args.posonlyargs + node.args.args)[:1]
    return first[0].arg if first else None


def _calls_and_complexity(node, pkg=(), mscope=None, module_types=None, self_name=None):
    """Bare callee names (compat) + call sites (see above) + cyclomatic complexity of a
    function, from one walk (_scan_function).

    Walks the function BODY only. Walking the whole FunctionDef also traversed
    `decorator_list`, so `@app.get("/x")` injected a bogus `get` call/call-site into every
    decorated handler and inflated its complexity - which then manufactured phantom call
    edges to any repo symbol named `get`/`post`/... (QA audit finding). Signature defaults
    and annotations are likewise not calls the function makes."""
    if mscope is None:
        mscope = {"bindings": {}, "stars": [], "defs": set(), "assigned": set(), "stores": {}}
    module_types = module_types or {}
    call_nodes, c, root = _scan_function(node, list(pkg))
    # receiver types: a parameter annotation (x: Cls) when nothing rebinds x, else plain
    # assignments that all hold one class or None
    local_types = {p: t for p, t in _param_types(node).items() if root.stores.get(p) == 1}
    local_types.update(_known_classes(_assigned_kinds(node.body), root.stores))
    calls, sites, seen, seen_sites = [], [], set(), set()
    for n, sc in call_nodes:
        nm = _callee_name(n.func)
        if nm and nm not in seen:
            seen.add(nm)
            calls.append(nm)
        cs = _site(n.func, n.lineno, sc, root, self_name, mscope, local_types, module_types)
        if cs:
            cs = cs + (arg_shape(n),)
        if cs and cs not in seen_sites:
            seen_sites.add(cs)
            sites.append(cs)
    return calls, sites, c


def _segment(lines, node):
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    return "\n".join(lines[start - 1:end]), start, end


def _bases(node):
    out = []
    for b in getattr(node, "bases", []):
        if isinstance(b, ast.Name):
            out.append(b.id)
        elif isinstance(b, ast.Attribute):
            out.append(b.attr)
    return out


def _base_refs(node, mscope):
    """How each base class name is bound, as (kind, name, attrs, line, ref) like a call site.
    Base[T] is read as Base."""
    out = []
    for b in node.bases:
        if isinstance(b, ast.Subscript):
            b = b.value
        base, attrs = _chain(b)
        if isinstance(base, ast.Name):
            kind, ref = _name_ref(base.id, _NO_SCOPE, mscope)
            out.append((kind, base.id, attrs, node.lineno, ref))
    return out


# Module-level defs can hide inside compound statements - the classic case is a
# try/except import fallback, or an `if TYPE_CHECKING:` / `if sys.platform` guard.
# The old code only iterated tree.body, so those defs were invisible to the graph,
# search, Files "Defines", and Ask (QA finding G-01). Walk into block bodies, but
# stop at any def/class boundary (a class handles its own methods; functions' inner
# defs are locals, intentionally not indexed as top-level symbols).
_BLOCK_STMTS = (ast.If, ast.Try, ast.With, ast.AsyncWith,
                ast.For, ast.AsyncFor, ast.While)


def _block_suites(node):
    suites = []
    for attr in ("body", "orelse", "finalbody"):
        v = getattr(node, attr, None)
        if isinstance(v, list):
            suites.append(v)
    if isinstance(node, ast.Try):
        for h in node.handlers:
            suites.append(h.body)
    return suites


def _statements(body):
    """Every statement in a body, at any depth: the suites of compound statements, function
    and class bodies included. Expressions are never entered, so this is cheap."""
    stack = list(body)
    while stack:
        s = stack.pop()
        yield s
        for attr in ("body", "orelse", "finalbody"):
            v = getattr(s, attr, None)
            if v.__class__ is list:
                stack.extend(v)
        for part in getattr(s, "handlers", ()) or getattr(s, "cases", ()):
            stack.extend(part.body)


def _module_defs(body):
    """Yield top-level function/class defs, descending through compound statements
    but not into other defs/classes."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node
        elif isinstance(node, _BLOCK_STMTS):
            for suite in _block_suites(node):
                yield from _module_defs(suite)


def _assigned_kinds(stmts):
    """{name: [what each plain assignment to it holds]} for `name = ...` and `name: Ann = ...`
    in these statements, descending through if/for/try/with blocks but NOT into nested
    def/class scopes (a different scope): the class name for `name = Cls(...)` (or the
    annotation of `name: Cls`), "None" for `name = None`, else "other". Only bare-Name
    constructors count, so a `d = {}` literal never gives `d` a class; the graph also requires
    the name to be a real repo CLASS, so factory functions and builtins produce no edge."""
    out = {}

    def kind_of(value, annotation=None):
        if value is None:
            return annotation.id if isinstance(annotation, ast.Name) else "other"
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            return value.func.id
        if isinstance(value, ast.Constant) and value.value is None:
            return "None"
        return "other"

    def visit(body):
        for s in body:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(s, ast.Assign):
                for tgt in s.targets:
                    if isinstance(tgt, ast.Name):
                        out.setdefault(tgt.id, []).append(kind_of(s.value))
            elif isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                out.setdefault(s.target.id, []).append(kind_of(s.value, s.annotation))
            elif isinstance(s, _BLOCK_STMTS):
                for suite in _block_suites(s):
                    visit(suite)

    visit(stmts)
    return out


def _known_classes(kinds, stores):
    """{name: class} for the names whose every binding (`stores` counts them all) is one of
    these assignments and holds one class or None. A name also bound another way (a loop, a
    parameter, a tuple target) or to two classes has no known class."""
    out = {}
    for name, ks in kinds.items():
        classes = {k for k in ks if k != "None"}
        if len(classes) == 1 and "other" not in classes and stores.get(name) == len(ks):
            out[name] = classes.pop()
    return out


def _param_types(node):
    """`{param -> ClassName}` from simple `def f(x: ClassName)` annotations."""
    args = getattr(node, "args", None)
    if not args:
        return {}
    allargs = list(getattr(args, "posonlyargs", [])) + list(args.args) + list(args.kwonlyargs)
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            allargs.append(extra)
    out = {}
    for a in allargs:
        ann = getattr(a, "annotation", None)
        if isinstance(ann, ast.Name):
            out[a.arg] = ann.id
    return out


def parse_python(rel_path, source):
    pf = ParsedFile(file=rel_path, language="python", text=source,
                    loc=source.count("\n") + 1)
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        pf.error = f"SyntaxError: {e}"
        return pf
    except (ValueError, RecursionError, MemoryError) as e:     # null bytes, absurd nesting
        pf.error = f"{type(e).__name__}: {e}"
        return pf
    lines = source.splitlines()

    # Resolve imports to absolute dotted module paths so the graph can match them to repo
    # files by FULL path (QA #10/#18). Relative imports (from . import x) were dropped
    # entirely, from-pkg-import-module produced no submodule candidate, and a bare-basename
    # fallback made `import logging` collide with a repo logging.py.
    # `imports` is the human-facing module list (what file_summary shows); `import_targets`
    # holds the extra "from pkg import submodule" candidates the graph needs to match a
    # submodule *file* (pkg/submodule.py) - those must NOT pollute the display list.
    pkg = rel_path[:-3].replace(os.sep, "/").split("/")[:-1] if rel_path.endswith(".py") else []
    mods = set()        # module-level imports (display + graph resolution)
    targets = set()     # from-import name candidates (graph file-matching only)
    for n in _statements(tree.body):                    # imports are statements, never expressions
        if isinstance(n, ast.Import):
            for a in n.names:
                mods.add(a.name)                        # absolute: import a.b.c -> "a.b.c"
        elif isinstance(n, ast.ImportFrom):
            mod = _from_module(n, pkg)
            if mod is None:
                continue                                # climbs above the indexed folder
            if mod:
                mods.add(mod)                           # the module/package imported from
            for a in n.names:                            # each imported name MAY be a submodule
                if a.name != "*":
                    targets.add(f"{mod}.{a.name}" if mod else a.name)
    pf.imports = sorted(m for m in mods if m)
    pf.import_targets = sorted(t for t in targets if t)

    mscope = _module_scope(tree, pkg)
    pf.bindings = mscope["bindings"]
    from .routes import file_facts              # here, not at the top: routes reads this module's helpers
    pf.routes = file_facts(tree, mscope, pkg, source)
    pf.star_imports = list(mscope["stars"])
    pf.assigned = sorted(mscope["assigned"])
    module_types = _known_classes(_assigned_kinds(tree.body), mscope["stores"])  # `_MODEL = Detector()`
    counts, groups = {}, {}

    def add_symbol(node, qualprefix, parent):
        # A unique id even for duplicate definitions: a property's accessors are x, x.setter
        # and x.deleter; other duplicates (try/except or if/else alternatives, overload stubs)
        # keep the plain id for the first and get #2, #3 in source order.
        base_qual = f"{qualprefix}{node.name}{_accessor(node)}"
        counts[base_qual] = n = counts.get(base_qual, 0) + 1
        qual = base_qual if n == 1 else f"{base_qual}#{n}"
        is_class = isinstance(node, ast.ClassDef)
        kind = "class" if is_class else ("method" if parent else "function")
        code, s, e = _segment(lines, node)
        if is_class:
            calls, sites, cx = [], [], 0
            signature = _class_signature(node)
            body_hash = _tree_hash(_class_statements(node), fallback=code)
            base_refs = _base_refs(node, mscope)
        else:
            # receiver types come from the function's own bindings and module-level instances
            # (_MODEL = Detector()): obj.method() then resolves to that class's method
            # precisely, without guessing
            decorators = {_deco_name(d) for d in node.decorator_list}
            calls, sites, cx = _calls_and_complexity(node, pkg, mscope, module_types,
                                                     _self_name(node, parent, decorators))
            signature = _function_signature(node)
            body_hash = _tree_hash(_without_docstring(node.body), fallback=code)
            base_refs = []
        sym = Symbol(
            id=f"{rel_path}::{qual}", name=node.name, qualname=qual, kind=kind,
            file=rel_path, start_line=s, end_line=e,
            docstring=(ast.get_docstring(node) or ""), code=code,
            calls=calls, parent=parent, complexity=cx,
            bases=_bases(node) if is_class else [],
            decorators=[_deco_name(d) for d in getattr(node, "decorator_list", [])],
            call_sites=sites, base_refs=base_refs, signature=signature,
            sig_hash=_sig_hash(signature), body_hash=body_hash,
        )
        pf.symbols.append(sym)
        groups.setdefault(base_qual, []).append((sym, node))
        if is_class:
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add_symbol(b, qual + ".", parent=qual)

    defs = list(_module_defs(tree.body))
    for node in defs:
        add_symbol(node, "", parent=None)
    for cls in [x for x in pf.symbols if x.kind == "class"]:
        cls.complexity = sum(m.complexity for m in pf.symbols
                             if m.parent == cls.qualname) or 1
    # A name with typing.overload stubs is marked overloaded, stubs and implementation alike:
    # which definition a caller binds to needs review.
    for members in groups.values():
        if any(_deco_name(d) == "overload" for _, node in members for d in node.decorator_list):
            for sym, _ in members:
                sym.overloaded = True
    pf.exports = _module_all(tree)
    pf.has_main = _has_main_guard(tree)
    pf.module_hash = _tree_hash(_without_docstring(tree.body), skip={id(d) for d in defs}, fallback=source)
    return pf


# ---------------- JavaScript / TypeScript (hand-written, brace-matched) ----------------
# A real extractor (not the old 12-line-stub regex): masks comments/strings so nothing is
# matched inside them, brace-matches bodies for true line ranges, finds class methods and
# typed/generic arrow components, and computes complexity, imports and calls so the graph,
# dead-code and RAG work for JS/TS (QA #9/#15/#16).
# There is no scope analysis: a local name that shadows a function of the same file can link
# to that function.

_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "class",
                "new", "typeof", "await", "yield", "case", "do", "else", "try", "throw",
                "const", "let", "var", "in", "of", "instanceof", "void", "delete", "super",
                "async"}

_JS_IMPORT_FROM = re.compile(r"""\bimport\b[^;'"]*?from\s*['"]([^'"]+)['"]""")
_JS_IMPORT_BARE = re.compile(r"""\bimport\s*['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)""")

# Bindings, matched on the masked text; a specifier is read from the original source at the
# span of the masked string's (blanked) content.
_JS_IMPORT_STMT = re.compile(r"""\bimport\s+(?:type\s+)?([^;'"]*?)\s*\bfrom\s*(['"])([^'"]*)\2""")
_JS_REQUIRE_NS = re.compile(r"""\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*(['"])([^'"]*)\2\s*\)""")
_JS_REQUIRE_NAMED = re.compile(r"""\b(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*(['"])([^'"]*)\2\s*\)""")
_JS_REEXPORT = re.compile(r"""\bexport\s+(?:type\s+)?\{([^}]*)\}\s*from\s*(['"])([^'"]*)\2""")
_JS_REEXPORT_STAR = re.compile(r"""\bexport\s*\*\s*(?:as\s+([A-Za-z_$][\w$]*)\s*)?from\s*(['"])([^'"]*)\2""")
# export default function f / class C, or export default name; (never export default make())
_JS_DEFAULT_DECL = re.compile(r"""\bexport\s+default\s+(?:async\s+)?(?:function\s*\*?\s*|class\s+)([A-Za-z_$][\w$]*)""")
_JS_DEFAULT_NAME = re.compile(r"""\bexport\s+default\s+([A-Za-z_$][\w$]*)\s*(?:;|$)""", re.M)
_JS_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")

_JS_CLASS = re.compile(r"\b(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"
                       r"(?:\s+extends\s+([A-Za-z_$][\w$.]*))?")
_JS_FUNC = re.compile(r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)")
# arrow const with optional type annotation, optional generic params, optional return type
_JS_ARROW = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*"
    r"(?::\s*[^=;]+?)?=\s*(?:async\s*)?(?:<[^>]*>\s*)?\([^)]*\)\s*(?::\s*[^={;]+?)?=>")
# a method inside a class body
_JS_METHOD = re.compile(
    r"(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|readonly\s+|static\s+|async\s+|get\s+|set\s+)*"
    r"([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?::\s*[^={;]+?)?\{")
_JS_CALL = re.compile(r"([A-Za-z_$][\w$.]*)\s*\(")
# A symbol's text with comments and whitespace removed and string contents kept (strings are
# matched first, so a // inside one is not a comment).
_JS_NORM = re.compile(r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|//[^\n]*|/\*.*?\*/|\s+""", re.S)


def _js_mask(source):
    """Replace string/comment CONTENT with spaces (newlines preserved) so declaration
    scanning never fires inside a comment or string literal, while byte offsets and line
    numbers stay exact."""
    out = list(source)
    i, n = 0, len(source)
    st = None  # None | "//" | "/*" | "'" | '"' | '`'
    while i < n:
        c = source[i]; nxt = source[i + 1] if i + 1 < n else ""
        if st is None:
            if c == "/" and nxt == "/":
                st = "//"; i += 2; continue
            if c == "/" and nxt == "*":
                st = "/*"; out[i] = out[i + 1] = " "; i += 2; continue
            if c in "'\"`":
                st = c; i += 1; continue
            i += 1; continue
        if st == "//":
            if c == "\n": st = None
            else: out[i] = " "
            i += 1; continue
        if st == "/*":
            if c == "*" and nxt == "/": out[i] = out[i + 1] = " "; st = None; i += 2; continue
            if c != "\n": out[i] = " "
            i += 1; continue
        # inside a string/template
        if c == "\\" and st in "'\"`":
            out[i] = " ";
            if i + 1 < n and source[i + 1] != "\n": out[i + 1] = " "
            i += 2; continue
        if c == st:
            st = None; i += 1; continue
        if c != "\n": out[i] = " "
        i += 1
    return "".join(out)


def _brace_span(masked, open_brace):
    """Index just past the '}' matching the '{' at open_brace (masked text)."""
    depth = 0
    for i in range(open_brace, len(masked)):
        ch = masked[i]
        if ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0: return i + 1
    return len(masked)


def _line_of(source, pos):
    return source.count("\n", 0, pos) + 1


def _js_complexity(masked_body):
    c = 1
    for kw in ("if", "for", "while", "case", "catch"):
        c += len(re.findall(r"\b" + kw + r"\b", masked_body))
    c += masked_body.count("&&") + masked_body.count("||") + masked_body.count("??")
    c += len(re.findall(r"(?<![?])\?(?![.?])", masked_body))   # ternary ?
    return max(1, c)


def _js_text_hash(text):
    """Hash of a symbol's text with comments and whitespace removed, string contents kept.
    A reformat that changes tokens (added parentheses, a trailing comma) still shows."""
    return _digest(_JS_NORM.sub(lambda m: m.group(1) or "", text))


def _js_items(clause):
    """[(imported name, local name)] from the inside of { ... } in an import or export."""
    out = []
    for item in clause.split(","):
        item = re.sub(r"^\s*type\s+", "", item).strip()
        if not item:
            continue
        parts = re.split(r"\s+as\s+|\s*:\s*", item)
        orig, local = (parts[0], parts[-1]) if len(parts) > 1 else (item, item)
        if _JS_IDENT.match(orig) and _JS_IDENT.match(local):
            out.append((orig, local))
    return out


def _js_bindings(source, masked):
    """(bindings, reexports, star re-exports, default export name) of a JS/TS file. Bindings
    map a local name to ("from", spec, name, aliased) or ("module", spec)."""
    bindings, reexports, stars = {}, {}, []

    def spec_of(m):
        return source[m.start(3):m.end(3)]

    for m in _JS_IMPORT_STMT.finditer(masked):
        clause, spec = m.group(1), spec_of(m)
        star = re.search(r"\*\s*as\s+([A-Za-z_$][\w$]*)", clause)
        if star:
            _bind(bindings, star.group(1), ("module", spec))
        brace = re.search(r"\{([^}]*)\}", clause)
        if brace:
            for orig, local in _js_items(brace.group(1)):
                _bind(bindings, local, ("from", spec, orig, orig != local))
        default = re.match(r"\s*([A-Za-z_$][\w$]*)\s*(?:,|$)", clause)
        if default and default.group(1) != "type":
            _bind(bindings, default.group(1), ("from", spec, "default", False))
    for m in _JS_REQUIRE_NS.finditer(masked):
        _bind(bindings, m.group(1), ("module", spec_of(m)))
    for m in _JS_REQUIRE_NAMED.finditer(masked):
        for orig, local in _js_items(m.group(1)):
            _bind(bindings, local, ("from", spec_of(m), orig, orig != local))
    for m in _JS_REEXPORT.finditer(masked):
        for orig, local in _js_items(m.group(1)):
            reexports[local] = ("from", spec_of(m), orig, orig != local)
    for m in _JS_REEXPORT_STAR.finditer(masked):
        if m.group(1):
            reexports[m.group(1)] = ("module", spec_of(m))
        elif spec_of(m) not in stars:
            stars.append(spec_of(m))
    d = _JS_DEFAULT_DECL.search(masked) or _JS_DEFAULT_NAME.search(masked)
    default = d.group(1) if d and d.group(1) not in _JS_KEYWORDS and d.group(1) != "extends" else ""
    return bindings, reexports, stars, default


def parse_js(rel_path, source, language):
    lines = source.splitlines()
    pf = ParsedFile(file=rel_path, language=language, text=source, loc=source.count("\n") + 1)

    # P2-4: skip minified / generated blobs - deep parsing them is quadratic and useless
    longest = max((len(l) for l in lines), default=0)
    if len(source) > 2_000_000 or longest > 50_000:
        pf.error = "skipped: minified or oversized file"
        return pf

    masked = _js_mask(source)

    imps = set()
    for rx in (_JS_IMPORT_FROM, _JS_IMPORT_BARE, _JS_REQUIRE):
        for m in rx.finditer(masked):
            # match position is comment-free (masked), but the path is a string literal
            # (masked to spaces) - read it from the ORIGINAL source at the captured span
            imps.add(source[m.start(1):m.end(1)])
    pf.imports = sorted(i for i in imps if i.strip())
    pf.bindings, pf.reexports, pf.star_reexports, pf.default_export = _js_bindings(source, masked)

    starts = [0] + [m.end() for m in re.finditer("\n", source)]
    top_names = set()          # filled below, before any call site is classified
    pending = []               # (symbol, start, end) whose call sites are read at the end

    def line_at(pos):
        return bisect.bisect_right(starts, pos)

    symbols = []
    class_spans = []
    spans = []                 # line ranges of every top-level definition (for module_hash)

    # classes + their methods
    for m in _JS_CLASS.finditer(masked):
        cname = m.group(1); bases = [m.group(2)] if m.group(2) else []
        brace = masked.find("{", m.end())
        if brace == -1:
            continue
        body_end = _brace_span(masked, brace)
        class_spans.append((m.start(), body_end))
        s0, s1 = _line_of(source, m.start()), _line_of(source, body_end)
        spans.append((s0, s1))
        cls = Symbol(
            id=f"{rel_path}::{cname}", name=cname, qualname=cname, kind="class",
            file=rel_path, start_line=s0, end_line=s1, docstring="",
            code="\n".join(lines[s0 - 1:s1]), calls=[], call_sites=[], parent=cname,
            complexity=0, bases=bases)
        symbols.append(cls)
        method_lines = set()
        # methods within the class body
        inner = masked[brace + 1:body_end - 1]
        for mm in _JS_METHOD.finditer(inner):
            mname = mm.group(1)
            if mname in _JS_KEYWORDS:
                continue
            mpos = brace + 1 + mm.start(1)
            mbrace = brace + 1 + mm.end() - 1     # the '{' captured at end of _JS_METHOD
            mend = _brace_span(masked, mbrace)
            ms0, ms1 = _line_of(source, mpos), _line_of(source, mend)
            method_lines.update(range(ms0, ms1 + 1))
            mcode = "\n".join(lines[ms0 - 1:ms1])
            meth = Symbol(
                id=f"{rel_path}::{cname}.{mname}", name=mname, qualname=f"{cname}.{mname}",
                kind="method", file=rel_path, start_line=ms0, end_line=ms1, docstring="",
                code=mcode, parent=cname, complexity=_js_complexity(masked[mbrace:mend]), bases=[],
                body_hash=_js_text_hash(mcode))
            symbols.append(meth)
            pending.append((meth, mbrace, mend))
        cls.body_hash = _js_text_hash("\n".join(lines[i - 1] for i in range(s0, s1 + 1)
                                                if i not in method_lines))

    def _in_class(pos):
        return any(a <= pos < b for a, b in class_spans)

    # top-level function declarations
    for m in _JS_FUNC.finditer(masked):
        if _in_class(m.start()):
            continue
        name = m.group(1)
        brace = masked.find("{", m.end())
        if brace == -1 or brace - m.end() > 400:
            continue
        bend = _brace_span(masked, brace)
        s0, s1 = _line_of(source, m.start()), _line_of(source, bend)
        spans.append((s0, s1))
        code = "\n".join(lines[s0 - 1:s1])
        fn = Symbol(
            id=f"{rel_path}::{name}", name=name, qualname=name, kind="function",
            file=rel_path, start_line=s0, end_line=s1, docstring="", code=code,
            parent=None, complexity=_js_complexity(masked[brace:bend]), bases=[],
            body_hash=_js_text_hash(code))
        symbols.append(fn)
        pending.append((fn, brace, bend))

    # top-level arrow-function consts (incl. typed React.FC and generics)
    for m in _JS_ARROW.finditer(masked):
        if _in_class(m.start()):
            continue
        name = m.group(1)
        arrow = masked.find("=>", m.start())
        after = masked.find("{", arrow) if arrow != -1 else -1
        if after != -1 and masked[arrow + 2:after].strip() == "":
            bend = _brace_span(masked, after)       # block body
        else:
            semi = masked.find(";", arrow); nl = masked.find("\n", arrow)
            cand = [x for x in (semi, nl) if x != -1]
            bend = min(cand) if cand else len(masked)   # expression body
        s0, s1 = _line_of(source, m.start()), _line_of(source, bend)
        spans.append((s0, s1))
        code = "\n".join(lines[s0 - 1:s1])
        fn = Symbol(
            id=f"{rel_path}::{name}", name=name, qualname=name, kind="function",
            file=rel_path, start_line=s0, end_line=s1, docstring="", code=code,
            parent=None, complexity=_js_complexity(masked[m.start():bend]), bases=[],
            body_hash=_js_text_hash(code))
        symbols.append(fn)
        pending.append((fn, m.end(), bend))

    # de-dup by id (keep first)
    byid = {}
    for s in symbols:
        byid.setdefault(s.id, s)
    pf.symbols = list(byid.values())
    top_names.update(s.name for s in pf.symbols if s.kind == "class" or s.parent is None)

    def ref(name):
        b = pf.bindings.get(name)
        if b is not None:
            return ("ambiguous", None) if b == AMBIGUOUS else ("import", b)
        return ("same", None) if name in top_names else ("free", None)

    for sym, start, end in pending:
        if sym.id not in byid or byid[sym.id] is not sym:
            continue
        sites, seen = [], set()
        for m in _JS_CALL.finditer(masked, start, end):
            parts = m.group(1).split(".")
            name = parts[-1]
            if not name or name in _JS_KEYWORDS:
                continue
            root, attrs, line = parts[0], tuple(parts[1:]), line_at(m.start(1))
            k = m.start(1) - 1
            while k >= 0 and masked[k] in " \t\r\n":
                k -= 1
            if k >= 0 and masked[k] == "." and masked[k - 2:k + 1] != "...":
                # the tail of a chain on a call result: a.b().listen(), user?.save(), or a
                # call continued on the next line; its receiver is not a name
                site = ("unknown", None, tuple(parts), line, None)
            elif root == "this":
                site = ("self", root, attrs, line, None) if len(attrs) == 1 else ("unknown", root, attrs, line, None)
            else:
                kind, r = ref(root)
                site = (kind, root, attrs, line, r)
            if site not in seen:
                seen.add(site)
                sites.append(site)
        sym.call_sites = sites
        sym.calls = list(dict.fromkeys(call_name(s) for s in sites))
    for sym in pf.symbols:
        if sym.kind == "class" and sym.bases:
            refs = []
            for b in sym.bases:
                root, *rest = b.split(".")
                kind, r = ref(root)
                refs.append((kind, root, tuple(rest), sym.start_line, r))
            sym.base_refs = refs
    covered = set()
    for s0, s1 in spans:
        covered.update(range(s0, s1 + 1))
    pf.module_hash = _js_text_hash("\n".join(l for i, l in enumerate(lines, 1) if i not in covered))
    return pf


# ---------------- dispatch ----------------
def parse_file(rel_path, abs_path, text=None):
    """Parse one file. `text`, when given, is used instead of reading `abs_path` (a file
    read from git's object store has no path on disk)."""
    ext = os.path.splitext(rel_path)[1].lower()
    src = _read(abs_path) if text is None else text
    if ext == ".py":
        return parse_python(rel_path, src)          # stdlib ast - always, most reliable
    if ext in JS_EXTS:
        return parse_js(rel_path, src, "javascript")
    if ext in TS_EXTS:
        return parse_js(rel_path, src, "typescript")
    # Go/Java/Rust/C#/... : tree-sitter if installed, else index as searchable text so the
    # files are at least visible (they used never to be enumerated at all - QA #14).
    lang = _EXT_LANG.get(ext)
    if lang:
        try:
            from . import treesitter_parser as _ts
            if _ts.available():
                pf = _ts.ts_parse(rel_path, src, lang)
                if pf is not None:
                    return pf
        except Exception:
            pass
        return ParsedFile(file=rel_path, language=lang, text=src, loc=src.count("\n") + 1)
    if ext in (".yaml", ".yml", ".toml", ".ini", ".cfg"):
        return ParsedFile(file=rel_path, language="config", text=src, loc=src.count("\n") + 1)
    lang = "markdown" if ext == ".md" else "other"
    return ParsedFile(file=rel_path, language=lang, text=src, loc=src.count("\n") + 1)
