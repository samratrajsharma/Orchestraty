"""HTTP routes, read from Python's syntax tree: FastAPI, Flask and Django.

Nothing is imported or run. Each Python file's parse keeps its route facts in
``ParsedFile.routes`` (``file_facts``): the apps and routers it makes, the routes declared on
them, how one is mounted in another, and Django's ``urlpatterns``. ``resolve`` links the facts
of all the files of an index into routes, following imports the way the code graph's resolver
binds them (``graph._Resolver``), and ``diff`` compares two lists of routes.

What is read:

* FastAPI: ``FastAPI()`` and ``APIRouter(prefix=...)``; routes declared with
  ``@x.get/post/put/patch/delete/head/options/trace(path)``, ``@x.api_route(path, methods=[...])``,
  ``@x.route(...)`` and ``@x.websocket(path)`` on an app or a router; ``x.include_router(router,
  prefix=...)``, routers in routers included, and ``x.mount(path, app)``.
* Flask: ``Flask()`` and ``Blueprint(..., url_prefix=...)``; ``@x.route(rule, methods=[...])`` and
  the shortcuts ``@x.get/post/put/patch/delete(rule)`` on an app or a blueprint;
  ``x.register_blueprint(bp, url_prefix=...)``, whose prefix replaces the blueprint's own, and
  blueprints in blueprints.
* Django: ``urlpatterns`` (a list, also built with ``+`` and ``+=``) of ``path()`` and
  ``re_path()`` (and the older ``url()``), with ``include()`` of a module (named by its dotted
  name, or imported), of a list, or of a module-level list of patterns. The root URLconf is
  the module a literal ``ROOT_URLCONF`` names; where none does, each URLconf no other includes
  is taken as a root, and its routes are heuristic. A Django route serves every method: ``ANY``.

An app, router or blueprint is known by the class it is made from (imported from ``fastapi``
or ``flask``), assigned to a name bound once in its scope: the module, or a function (an app
factory, whose own imports count). A name is followed into another module through the import
that binds it, and the re-exports there, as far as the code graph's resolver follows them.

Each route has one method (one declared with several is several routes), its full path (the
prefixes it is mounted under, then its own path), its handler (the symbol id of the function,
``<locals>`` in it when nested in another), the file and line where it is declared, and a
confidence:

* ``exact``: every part of the path is a literal (a string, a name its module binds once to a
  string, or ``+`` and f-strings of those), the methods are literals, and the chain from the
  route up to an app is known;
* ``heuristic``: some part is computed (a setting, a call, ...), or the object a route is
  declared on, or where a router is mounted, is not known. What could not be worked out is
  ``{?}`` in the path and said in ``unresolved``. A heuristic route is listed, but must never
  give a strong finding.

Not read: routes added by calls (``add_url_rule``, ``add_api_route``), class-based routers
(Django REST framework's), and routes built in loops.
"""
from __future__ import annotations

import ast

from .graph import _Resolver
from .parsing import _block_suites, _BLOCK_STMTS, _import_binds, _names_in, _statements

EXACT, HEURISTIC = "exact", "heuristic"
UNKNOWN = "{?}"                          # a part of a path that could not be worked out
ANY_METHOD = "ANY"                       # a Django route serves every method
UNKNOWN_METHOD = "?"
FASTAPI_VERBS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
FLASK_VERBS = ("get", "post", "put", "patch", "delete")
ROUTE_ATTRS = frozenset(FASTAPI_VERBS + ("route", "api_route", "websocket"))
KIND_ATTRS = {"fastapi": ROUTE_ATTRS, "router": ROUTE_ATTRS,
              "flask": frozenset(FLASK_VERBS + ("route",)), "blueprint": frozenset(FLASK_VERBS + ("route",))}
FRAMEWORK = {"fastapi": "fastapi", "router": "fastapi", "flask": "flask", "blueprint": "flask"}
APPS = ("fastapi", "flask")              # the kinds that serve their routes when mounted nowhere
CLASSES = {"fastapi.FastAPI": "fastapi", "fastapi.applications.FastAPI": "fastapi",
           "fastapi.APIRouter": "router", "fastapi.routing.APIRouter": "router",
           "flask.Flask": "flask", "flask.app.Flask": "flask",
           "flask.Blueprint": "blueprint", "flask.blueprints.Blueprint": "blueprint"}
DJANGO = {"django.urls.path": "path", "django.urls.re_path": "re_path", "django.conf.urls.url": "re_path",
          "django.urls.include": "include", "django.conf.urls.include": "include"}
MOUNTS = ("include_router", "register_blueprint", "mount")
# Text a file must hold to declare a route fact: a class or name above, or a decorator's call
MARKERS = ("FastAPI", "APIRouter", "Flask", "Blueprint", "include_router", "register_blueprint", ".mount(",
           "urlpatterns", "ROOT_URLCONF")
NOT_ROUTERS = frozenset(("cache", "lru_cache", "functools", "memoize", "redis"))   # @cache.get("k")
MAX_DEPTH = 8                            # mounts or includes followed from a route up to its app
MAX_HOPS = 3                             # re-exports followed from an import to an app or router
MAX_TEXT = 80


def _source(node):
    try:
        text = ast.unparse(node)
    except Exception:                   # noqa: BLE001 - an exotic node; say so rather than fail
        text = type(node).__name__
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "~"


def _binding(b):
    return isinstance(b, (tuple, list)) and len(b) >= 2 and b[0] in ("module", "from")


def _single_assign(st):
    """(name, value) for `name = value` or `name: T = value`, else (None, None)."""
    if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
        return st.targets[0].id, st.value
    if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.value is not None:
        return st.target.id, st.value
    return None, None


def _framework(b):
    """Whether an import binding names something of fastapi, flask or django."""
    return _binding(b) and (b[1] or (b[2] if len(b) > 2 else "")).split(".")[0] in ("fastapi", "flask", "django")


def _methods(node):
    """The methods a literal list (tuple, set) of strings gives, upper case; UNKNOWN_METHOD else."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and node.elts and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return sorted({e.value.upper() for e in node.elts})
    return UNKNOWN_METHOD


class _Fn:
    """A function's own names: how often it binds each (parameters, assignments, imports,
    nested definitions), the apps and routers it makes, and what its own imports bind. The
    names are read the first time they are asked for: most functions never are."""

    def __init__(self, qual, node, pkg):
        self.qual = qual
        self.objects = {}
        self._node, self._pkg, self._counts, self._bindings = node, pkg, None, None

    @property
    def counts(self):
        if self._counts is None:
            self._read()
        return self._counts

    @property
    def bindings(self):
        if self._bindings is None:
            self._read()
        return self._bindings

    def _read(self):
        self._counts, self._bindings = {}, {}
        args = self._node.args
        for a in args.posonlyargs + args.args + args.kwonlyargs + [x for x in (args.vararg, args.kwarg) if x]:
            self._bind(a.arg)
        self._visit(self._node.body, self._pkg)

    def _bind(self, name):
        self._counts[name] = self._counts.get(name, 0) + 1

    def _visit(self, body, pkg):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._bind(st.name)
            elif isinstance(st, (ast.Import, ast.ImportFrom)):
                for name, b in _import_binds(st, pkg):
                    if name != "*":
                        self._bind(name)
                        self._bindings[name] = b
            else:
                if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                        for name in _names_in(t):
                            self._bind(name)
                elif isinstance(st, (ast.For, ast.AsyncFor)):
                    for name in _names_in(st.target):
                        self._bind(name)
                elif isinstance(st, (ast.With, ast.AsyncWith)):
                    for item in st.items:
                        if item.optional_vars is not None:
                            for name in _names_in(item.optional_vars):
                                self._bind(name)
                for h in getattr(st, "handlers", ()):
                    if h.name:
                        self._bind(h.name)
                if isinstance(st, _BLOCK_STMTS):
                    for suite in _block_suites(st):
                        self._visit(suite, pkg)


class _Facts:
    """The route facts of one file, read in two passes over its statements: first the apps and
    routers it makes, then the routes, mounts and URL patterns that name them."""

    def __init__(self, tree, mscope, pkg):
        self.pkg = pkg
        self.bindings = mscope["bindings"]
        self.stores = mscope["stores"]
        self.defs = mscope["defs"]
        self.constants = {}             # module-level name -> string, for names bound once to one
        self.objects = {}               # key -> {"kind", "prefix", "line"}
        self.routes, self.mounts, self.urls, self.root_urlconf = [], [], [], []
        self.lists = {}                 # module-level name -> URL patterns, for include(name)
        self.fns = {}                   # id(function node) -> _Fn
        # the names an import anywhere in the file binds to something of fastapi, flask or django:
        # only a call through one of them can make an app or a router
        self.framework_names = {name for name, b in self.bindings.items() if _framework(b)}
        for st in _statements(tree.body):
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                self.framework_names.update(name for name, b in _import_binds(st, pkg) if name != "*" and _framework(b))
        for st in tree.body:
            name, value = _single_assign(st)
            if (name and isinstance(value, ast.Constant) and isinstance(value.value, str)
                    and self.stores.get(name) == 1):
                self.constants[name] = value.value
        self.walk(tree.body, [], "", first=True)
        self.walk(tree.body, [], "", first=False)

    def result(self):
        out = {}
        for key, value in (("objects", self.objects), ("routes", self.routes), ("mounts", self.mounts),
                           ("urls", self.urls), ("root_urlconf", self.root_urlconf)):
            if value:
                out[key] = value
        return out

    # ---- scopes
    def fn(self, node, scopes, cls_qual):
        f = self.fns.get(id(node))
        if f is None:
            f = self.fns[id(node)] = _Fn(self.qual(node.name, scopes, cls_qual), node, self.pkg)
        return f

    @staticmethod
    def qual(name, scopes, cls_qual):
        if cls_qual:
            return f"{cls_qual}.{name}"
        if scopes:
            return f"{scopes[-1].qual}.<locals>.{name}"
        return name

    def walk(self, body, scopes, cls_qual, first):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                f = self.fn(st, scopes, cls_qual)
                if not first:
                    for d in st.decorator_list:      # evaluated where the def is, not in it
                        self.decorator(d, f.qual, scopes)
                self.walk(st.body, scopes + [f], "", first)
            elif isinstance(st, ast.ClassDef):
                self.walk(st.body, scopes, self.qual(st.name, scopes, cls_qual), first)
            elif isinstance(st, _BLOCK_STMTS):
                for suite in _block_suites(st):
                    self.walk(suite, scopes, cls_qual, first)
            elif not cls_qual:                       # a class body makes no app and mounts nothing
                if first:
                    self.make(st, scopes)
                else:
                    self.use(st, scopes)

    # ---- names
    def _local(self, name, scopes):
        """The innermost function that binds `name`, or None when only the module does."""
        for f in reversed(scopes):
            if name in f.counts:
                return f
        return None

    def origin(self, node, scopes):
        """The dotted name an expression names through the imports ("fastapi.FastAPI"), or None."""
        attrs = []
        while isinstance(node, ast.Attribute):
            attrs.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        f = self._local(node.id, scopes)
        if f is not None:
            b = f.bindings.get(node.id) if f.counts.get(node.id) == 1 else None
        else:
            b = self.bindings.get(node.id)
        if not _binding(b):
            return None
        base = b[1] if b[0] == "module" else (f"{b[1]}.{b[2]}" if b[1] else b[2])
        return ".".join([base] + attrs)

    def ref(self, node, scopes):
        """How an expression names an app, router, module or function: ["local", key] (made
        here), ["import", binding, attributes], ["def", name] (a function or class this module
        defines), or None (a parameter, a call's result, ...)."""
        attrs = []
        while isinstance(node, ast.Attribute):
            attrs.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        name = node.id
        f = self._local(name, scopes)
        if f is not None:
            if f.counts[name] != 1:
                return None
            if name in f.objects:
                return None if attrs else ["local", f.objects[name]]
            if name in f.bindings:
                return ["import", list(f.bindings[name]), attrs]
            return None
        if name in self.objects:
            return None if attrs else ["local", name]
        b = self.bindings.get(name)
        if _binding(b):
            return ["import", list(b), attrs]
        if name in self.defs and self.stores.get(name) == 1 and not attrs:
            return ["def", name]
        return None

    def literal(self, node, scopes):
        """The string a literal expression makes (see the module's docstring), or None."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in self.constants and self._local(node.id, scopes) is None:
            return self.constants[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = self.literal(node.left, scopes), self.literal(node.right, scopes)
            return a + b if a is not None and b is not None else None
        if isinstance(node, ast.JoinedStr):
            out = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(v.value)
                    continue
                if not (isinstance(v, ast.FormattedValue) and v.conversion == -1 and v.format_spec is None):
                    return None
                s = self.literal(v.value, scopes)
                if s is None:
                    return None
                out.append(s)
            return "".join(out)
        return None

    def value(self, node, scopes):
        """{"text": s} for a literal string, else {"expr": its source}."""
        s = self.literal(node, scopes)
        return {"text": s} if s is not None else {"expr": _source(node)}

    # ---- first pass: the apps, routers and blueprints
    def make(self, st, scopes):
        name, call = _single_assign(st)
        if not (name and isinstance(call, ast.Call)):
            return
        base = call.func
        while isinstance(base, ast.Attribute):
            base = base.value
        if not (isinstance(base, ast.Name) and base.id in self.framework_names):
            return
        kind = CLASSES.get(self.origin(call.func, scopes) or "")
        if kind is None:
            return
        if scopes:
            f = scopes[-1]
            if f.counts.get(name) != 1:
                return                               # bound more than once there: which one is not known
            key = f"{f.qual}.<locals>.{name}"
            f.objects[name] = key
        else:
            if self.stores.get(name) != 1:
                return
            key = name
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        spread = any(k.arg is None for k in call.keywords)
        prefix = None
        if kind == "router":
            prefix = (self.value(kw["prefix"], scopes) if "prefix" in kw
                      else {"expr": "**" + _source(call)} if spread else {"text": ""})
        elif kind == "blueprint":
            v = kw.get("url_prefix", call.args[5] if len(call.args) > 5 else None)
            if v is not None and not (isinstance(v, ast.Constant) and v.value is None):
                prefix = self.value(v, scopes)
            elif spread:
                prefix = {"expr": "**" + _source(call)}
        self.objects[key] = {"kind": kind, "prefix": prefix, "line": st.lineno}

    # ---- second pass: routes, mounts and URL patterns
    def use(self, st, scopes):
        call = st.value if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) else None
        if call is not None and isinstance(call.func, ast.Attribute) and call.func.attr in MOUNTS:
            self.mount(call, scopes)
        if not scopes:
            self.module_level(st)

    def decorator(self, d, handler, scopes):
        if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ROUTE_ATTRS):
            return
        kw = {k.arg: k.value for k in d.keywords if k.arg}
        node = d.args[0] if d.args else kw.get("path", kw.get("rule"))
        if node is None:
            return
        self.routes.append({"on": self.ref(d.func.value, scopes), "receiver": _source(d.func.value),
                            "attr": d.func.attr, "path": self.value(node, scopes),
                            "methods": _methods(kw["methods"]) if "methods" in kw else None,
                            "handler": handler, "line": d.lineno})

    def mount(self, call, scopes):
        how = call.func.attr
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        spread = any(k.arg is None for k in call.keywords)
        args = call.args
        if how == "mount":
            where = args[0] if args else kw.get("path")
            child = args[1] if len(args) > 1 else kw.get("app")
            if where is None:
                return
            prefix = self.value(where, scopes)
        elif how == "include_router":
            child = args[0] if args else kw.get("router")
            prefix = (self.value(kw["prefix"], scopes) if "prefix" in kw
                      else {"expr": "**" + _source(call)} if spread else {"text": ""})
        else:
            child = args[0] if args else kw.get("blueprint")
            v = kw.get("url_prefix")
            prefix = (self.value(v, scopes) if v is not None and not (isinstance(v, ast.Constant) and v.value is None)
                      else {"expr": "**" + _source(call)} if spread else None)
        if child is None:
            return
        self.mounts.append({"how": how, "parent": self.ref(call.func.value, scopes), "parent_text": _source(call.func.value),
                            "child": self.ref(child, scopes), "prefix": prefix, "line": call.lineno})

    def module_level(self, st):
        name, value = _single_assign(st)
        if name == "ROOT_URLCONF":
            s = self.literal(value, [])
            if s:
                self.root_urlconf.append(s)
        elif name == "urlpatterns":
            self.urls = self.patterns(value)
        elif name and isinstance(value, (ast.List, ast.Tuple)) and self.stores.get(name) == 1:
            found = self.patterns(value)
            if found:
                self.lists[name] = found
        elif (isinstance(st, ast.AugAssign) and isinstance(st.op, ast.Add) and isinstance(st.target, ast.Name)
              and st.target.id == "urlpatterns"):
            self.urls = self.urls + self.patterns(st.value)

    def patterns(self, node):
        if isinstance(node, (ast.List, ast.Tuple)):
            return [e for e in (self.pattern(x) for x in node.elts) if e]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.patterns(node.left) + self.patterns(node.right)
        if isinstance(node, ast.Name) and node.id in self.lists:
            return list(self.lists[node.id])
        return []

    def pattern(self, e):
        if not isinstance(e, ast.Call):
            return None
        kind = DJANGO.get(self.origin(e.func, []) or "")
        if kind not in ("path", "re_path"):
            return None
        kw = {k.arg: k.value for k in e.keywords if k.arg}
        route = e.args[0] if e.args else kw.get("route")
        view = e.args[1] if len(e.args) > 1 else kw.get("view")
        if route is None or view is None:
            return None
        entry = {"kind": kind, "route": self.value(route, []), "line": e.lineno}
        if isinstance(view, ast.Call) and DJANGO.get(self.origin(view.func, []) or "") == "include":
            entry["include"] = self.included(view)
            return entry
        if isinstance(view, ast.Call) and isinstance(view.func, ast.Attribute) and view.func.attr == "as_view":
            view = view.func.value                   # a class-based view: the class handles it
        entry["view"] = self.ref(view, [])
        return entry

    def included(self, call):
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        arg = call.args[0] if call.args else kw.get("arg")
        if isinstance(arg, ast.Tuple) and arg.elts and not isinstance(arg.elts[0], ast.Call):
            arg = arg.elts[0]                        # (module or patterns, app name)
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return {"module": arg.value}
        if isinstance(arg, (ast.List, ast.Tuple)):
            return {"patterns": self.patterns(arg)}
        if isinstance(arg, ast.Name) and arg.id in self.lists:
            return {"patterns": list(self.lists[arg.id])}
        r = self.ref(arg, []) if arg is not None else None
        return {"ref": r} if r else {"expr": _source(arg) if arg is not None else ""}


def file_facts(tree, mscope, pkg, source=None):
    """The route facts of one parsed Python file ({} when it has none), from its syntax tree and
    the module scope the parser worked out. They are kept in ParsedFile.routes. A file whose
    source holds none of the text a fact needs is not walked."""
    if source is not None and not (any(m in source for m in MARKERS)
                                   or ("@" in source and any(f".{a}(" in source for a in ROUTE_ATTRS))):
        return {}
    return _Facts(tree, mscope, pkg).result()


# ------------------------------------------------------------------------------ linking
def _value(v, what, file, line):
    """(text, why) of a fact's value: why is None for a literal."""
    if v is None:
        return None, None
    if "text" in v:
        return v["text"], None
    return UNKNOWN, f"{what} {v.get('expr', '')} ({file} line {line})"


def _flask_join(prefix, rule):
    """A rule under a URL prefix, as Flask joins them."""
    if prefix is None:
        return rule
    if not rule:
        return prefix
    return "/".join((prefix.rstrip("/"), rule.lstrip("/")))


def _flask_nest(outer, inner):
    """The URL prefix of a blueprint registered in another, as Flask combines them."""
    if outer is not None and inner is not None:
        return outer.rstrip("/") + "/" + inner.lstrip("/")
    return inner if inner is not None else outer


class _Linker:
    def __init__(self, parsed_files, facts):
        self.files = {pf.file: pf for pf in parsed_files}
        self.facts = facts
        self.r = _Resolver(parsed_files)
        self.mounted = {}                       # child object -> [(parent object or None, mount fact, file)]
        self._contexts = {}
        for file, fx in sorted(facts.items()):
            for m in fx.get("mounts", ()):
                child = self.obj(file, m["child"])
                if child is not None:
                    self.mounted.setdefault(child, []).append((self.obj(file, m["parent"]), m, file))

    def kind(self, obj):
        return self.facts[obj[0]]["objects"][obj[1]]["kind"]

    # ---- names across files
    def obj(self, file, ref):
        """The app or router (file, key) a reference names, or None."""
        if not ref:
            return None
        if ref[0] == "local":
            return (file, ref[1]) if ref[1] in self.facts.get(file, {}).get("objects", {}) else None
        if ref[0] == "import":
            t = self.target(ref[1], ref[2], file, 0)
            return t[1] if t and t[0] == "object" else None
        return None

    def module(self, file, ref):
        """The module file a reference names, or None."""
        if ref and ref[0] == "import":
            t = self.target(ref[1], ref[2], file, 0)
            return t[1] if t and t[0] == "module" else None
        return None

    def target(self, b, attrs, importer, hops):
        """("object", (file, key)), ("module", file, dotted name) or None: what an import binding
        and the attributes after it name."""
        if b[0] == "module":
            t = ("module", self.r.module_for(b[1], importer), b[1])
        else:
            mod, attr = b[1], b[2]
            full = f"{mod}.{attr}" if mod else attr
            f = self.r.module_for(mod, importer) if mod else None
            t = self.member(f, attr, full, hops) if f is not None and f != importer else None
            if t is None:
                sf = self.r.module_for(full, importer)
                t = ("module", sf, full) if sf else None
        for a in attrs:
            if t is None or t[0] != "module":
                return None
            _, f, name = t
            dotted = f"{name}.{a}"
            t = self.member(f, a, dotted, hops) if f else None
            if t is None:
                sf = self.r.module_for(dotted, importer)
                t = ("module", sf, dotted) if sf else None
        return t

    def member(self, file, name, dotted, hops):
        """What `name` means in the module `file`: an app or router made there, what an import
        there binds it to (a re-export), or a submodule."""
        if name in self.facts.get(file, {}).get("objects", {}):
            return ("object", (file, name))
        pf = self.files.get(file)
        b = pf.bindings.get(name) if pf is not None else None
        if _binding(b) and hops < MAX_HOPS:
            return self.target(b, [], file, hops + 1)
        sub = self.r.submodule(file, name)
        return ("module", sub, dotted) if sub else None

    def symbol(self, file, ref):
        """The symbol id a view reference names, or ""."""
        if not ref:
            return ""
        if ref[0] == "def":
            return f"{file}::{ref[1]}"
        if ref[0] != "import":
            return ""
        t = self.r.py_binding(tuple(ref[1]), importer=file)
        if t is None:
            return ""
        if t[0] == "symbols":
            return t[1][0] if not ref[2] else ""
        got = self.r.walk(t, ref[2], "python", importer=file)
        return got[0][0] if got and not got[2] else ""

    # ---- FastAPI and Flask: where an app or router serves its routes
    def contexts(self, obj, depth=0, seen=()):
        """[(prefix, reasons)]: each prefix the routes declared on `obj` are served under (Flask:
        None for none), with what could not be worked out about it."""
        if depth == 0 and obj in self._contexts:
            return self._contexts[obj]
        kind = self.kind(obj)
        flask = FRAMEWORK[kind] == "flask"
        own_v = self.facts[obj[0]]["objects"][obj[1]]["prefix"]
        line = self.facts[obj[0]]["objects"][obj[1]]["line"]
        own, own_why = _value(own_v, "prefix", obj[0], line)
        name = f"{obj[1].rsplit('.', 1)[-1]} in {obj[0]}"
        out = []
        mounts = self.mounted.get(obj, [])
        if not mounts:
            if kind in APPS:
                out.append((None if flask else "", ()))
            elif flask:
                out.append((_flask_join(UNKNOWN, own) if own is not None else UNKNOWN,
                            tuple(x for x in (f"blueprint {name} is registered in no app that is known", own_why) if x)))
            else:
                out.append((UNKNOWN + own, tuple(x for x in (f"router {name} is included in no app that is known",
                                                              own_why) if x)))
        for parent, m, file in mounts:
            text, why = _value(m["prefix"], "prefix", file, m["line"])
            if parent is None or parent == obj or parent in seen or depth >= MAX_DEPTH:
                above = [(UNKNOWN, (f"{m['how']} on {m['parent_text']} ({file} line {m['line']}), "
                                    "which is not known to be an app or a router",))]
            else:
                above = self.contexts(parent, depth + 1, seen + (obj,))
            for up, up_why in above:
                reasons = up_why + tuple(x for x in (why,) if x)
                if flask:
                    mine = text if m["prefix"] is not None else own   # a registration's prefix replaces the blueprint's
                    if m["prefix"] is None and own_why:
                        reasons += (own_why,)
                    out.append((_flask_nest(up, mine), reasons))
                else:                                # FastAPI: the parent's prefix, the mount's, then the router's own
                    out.append(((up or "") + text + (own or ""), reasons + tuple(x for x in (own_why,) if x)))
        if depth == 0:
            self._contexts[obj] = out
        return out

    def declared(self, file, rt):
        """The routes one decorator declares."""
        obj = self.obj(file, rt["on"])
        path, path_why = _value(rt["path"], "path", file, rt["line"])
        if obj is None:
            receiver = rt["receiver"].rsplit(".", 1)[-1]
            if receiver.lower() in NOT_ROUTERS or path_why or not path.startswith("/"):
                return []                            # @cache.get("key") and the like: no route
            framework, contexts = "", [(UNKNOWN, (f"{rt['receiver']} ({file} line {rt['line']}) is not known to "
                                                  "be an app or a router",))]
        else:
            kind = self.kind(obj)
            if rt["attr"] not in KIND_ATTRS[kind]:
                return []
            framework, contexts = FRAMEWORK[kind], self.contexts(obj)
        if rt["attr"] in ("route", "api_route"):
            methods = rt["methods"] or ["GET"]
        elif rt["attr"] == "websocket":
            methods = ["WEBSOCKET"]
        else:
            methods = [rt["attr"].upper()]
        method_why = ()
        if methods == UNKNOWN_METHOD:
            methods, method_why = [UNKNOWN_METHOD], (f"methods are computed ({file} line {rt['line']})",)
        handler = f"{file}::{rt['handler']}"
        out = []
        for prefix, why in contexts:
            full = _flask_join(prefix, path) if framework == "flask" else (prefix or "") + path
            reasons = why + tuple(x for x in (path_why,) if x) + method_why
            for m in methods:
                out.append(_route(m, full, handler, file, rt["line"], framework, reasons))
        return out

    # ---- Django
    def django(self):
        urlconfs = sorted(f for f, fx in self.facts.items() if fx.get("urls"))
        if not urlconfs:
            return []
        named = sorted({f for fx in self.facts.values() for m in fx.get("root_urlconf", ())
                        for f in [self.r.module_for(m)] if f in urlconfs})
        if named:
            roots, why = named, ()
        else:
            included = set()
            for f in urlconfs:
                for target in self._includes(f, self.facts[f]["urls"]):
                    included.add(target)
            roots = [f for f in urlconfs if f not in included]
            why = ("no literal ROOT_URLCONF names the root URLconf",)
        out, reached = [], set()
        for f in roots:
            out += self.urlconf(f, [], why, reached, (f,))
        for f in urlconfs:
            if f not in reached:
                out += self.urlconf(f, [UNKNOWN + "/"], (f"{f} is included from no root URLconf that is known",),
                                    reached, (f,))
        return out

    def _includes(self, file, entries):
        for e in entries:
            inc = e.get("include")
            if not inc:
                continue
            if "patterns" in inc:
                yield from self._includes(file, inc["patterns"])
            else:
                t = self._included_file(file, inc)
                if t:
                    yield t

    def _included_file(self, file, inc):
        if "module" in inc:
            return self.r.module_for(inc["module"])
        if "ref" in inc:
            return self.module(file, inc["ref"])
        return None

    def urlconf(self, file, parts, why, reached, stack):
        reached.add(file)
        return self.entries(file, self.facts[file]["urls"], parts, why, reached, stack)

    def entries(self, file, entries, parts, why, reached, stack):
        out = []
        for e in entries:
            text, part_why = _value(e["route"], "route", file, e["line"])
            here = parts + [text]
            reasons = why + tuple(x for x in (part_why,) if x)
            inc = e.get("include")
            if inc is None:
                path = "/" + "".join(p[1:] if p.startswith("^") else p for p in here)
                out.append(_route(ANY_METHOD, path, self.symbol(file, e.get("view")), file, e["line"], "django",
                                  reasons))
            elif "patterns" in inc:
                out += self.entries(file, inc["patterns"], here, reasons, reached, stack)
            else:
                target = self._included_file(file, inc)
                if (target and self.facts.get(target, {}).get("urls") and target not in stack
                        and len(stack) < MAX_DEPTH):
                    reached.add(target)
                    out += self.entries(target, self.facts[target]["urls"], here, reasons, reached,
                                        stack + (target,))
        return out

    def routes(self):
        out = []
        for file, fx in sorted(self.facts.items()):
            for rt in fx.get("routes", ()):
                out += self.declared(file, rt)
        out += self.django()
        return sorted(out, key=lambda r: (r["path"], r["method"], r["file"], r["line"], r["handler"]))


def _route(method, path, handler, file, line, framework, reasons):
    return {"method": method, "path": path, "handler": handler, "file": file, "line": line,
            "framework": framework, "confidence": HEURISTIC if reasons else EXACT, "unresolved": list(reasons)}


def resolve(parsed_files):
    """Every route the parsed files declare, linked across files (see the module's docstring):
    a list of {"method", "path", "handler", "file", "line", "framework", "confidence",
    "unresolved"}, sorted by path, method, file and line."""
    facts = {pf.file: pf.routes for pf in parsed_files
             if pf.language == "python" and getattr(pf, "routes", None)}
    if not facts:
        return []
    return _Linker(parsed_files, facts).routes()


def for_index(idx):
    """resolve() for a RepoIndex, kept in its memo."""
    memo = getattr(idx, "memo", None)
    if memo is not None and "routes" in memo:
        return memo["routes"]
    found = resolve(idx.parsed_files)
    if memo is not None:
        memo["routes"] = found
    return found


def diff(base, head):
    """How the routes changed from one list (from resolve) to another: {"added": [...],
    "removed": [...], "changed": [{"from": route, "to": route}]}. A route is its method and
    path; a route removed and one added with the same handler are one route whose method or
    path changed."""
    bk = {(r["method"], r["path"]): r for r in base}
    hk = {(r["method"], r["path"]): r for r in head}
    removed = [bk[k] for k in sorted(set(bk) - set(hk))]
    added = [hk[k] for k in sorted(set(hk) - set(bk))]
    waiting = {}
    for r in added:
        if r["handler"]:
            waiting.setdefault(r["handler"], []).append(r)
    changed, gone, paired = [], [], set()
    for r in removed:
        new = waiting.get(r["handler"]) if r["handler"] else None
        if new:
            to = new.pop(0)
            paired.add((to["method"], to["path"]))
            changed.append({"from": r, "to": to})
        else:
            gone.append(r)
    return {"added": [r for r in added if (r["method"], r["path"]) not in paired], "removed": gone,
            "changed": changed}
