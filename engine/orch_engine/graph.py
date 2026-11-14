from __future__ import annotations
import builtins
import configparser
import posixpath
import re
from collections import defaultdict

from .parsing import AMBIGUOUS, call_name, site_args

PYTHON = "python"
JS_LANGS = ("javascript", "typescript")
_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
MAX_REEXPORT_HOPS = 3
_BUILTINS = frozenset(dir(builtins))
_JS_GLOBALS = frozenset((        # not counted as unresolved in JS/TS
    "console", "Math", "JSON", "Object", "Array", "Promise", "Number", "String", "Boolean", "Date",
    "Symbol", "Error", "Reflect", "Proxy", "Map", "Set", "WeakMap", "WeakSet", "RegExp", "Intl",
    "parseInt", "parseFloat", "isNaN", "isFinite", "setTimeout", "setInterval", "clearTimeout",
    "clearInterval", "queueMicrotask", "structuredClone", "fetch", "require", "window", "document",
    "globalThis", "process", "Buffer", "encodeURIComponent", "decodeURIComponent", "alert"))

# How a calls (or inherits) edge was bound; every such edge carries exactly one of these.
# engine/README.md explains each.
EVIDENCE_TAGS = ("same_file", "import", "import_alias", "module_attr", "class_attr",
                 "self_method", "constructor", "reexport", "instance")

JS_TEXT_NOTE = ("JavaScript and TypeScript symbols are compared as normalised text (comments and "
                "whitespace removed), so a reformat that changes tokens can show as a change")
NAMESPACE_NOTE = "namespace package (no __init__.py): imports through it are not resolved"


class CodeGraph:
    """Pure-Python directed multigraph of code structure.

    Node ids:
      - file node:   repo-relative path, e.g. "infer.py"
      - symbol node: "<rel_path>::<qualname>", e.g. "model.py::Detector.predict"
    Edge types: contains, method_of, calls, imports, inherits. A calls or inherits edge also
    records how it was bound (an evidence tag) and the source lines it comes from; see
    edge_info(); a Python calls edge also records how each of its calls passes its arguments,
    and whether it is made on the class or on an instance.
    Calls that bound to nothing in the repository are kept per file in
    `unresolved`, for name-only searches.
    """

    def __init__(self):
        self.nodes = {}                  # id -> {"type":..., "data":...}
        self._out = defaultdict(list)    # id -> [(dst, etype)]
        self._in = defaultdict(list)     # id -> [(src, etype)]
        self._info = {}                  # (src, dst, etype) -> (evidence, lines, argument shapes)
        self.unresolved = {}             # file -> [(caller id, called name, line, why)]
        self.not_analysed = []           # what binding could not cover, as {"path", "reason"}
        self.resolution = {}             # Python source roots and ambiguous module names

    def add_node(self, nid, ntype, data=None):
        if nid not in self.nodes:
            self.nodes[nid] = {"type": ntype, "data": data or {}}
        return nid

    def add_edge(self, src, dst, etype, evidence=None, lines=(), args=()):
        """Add an edge between two existing nodes. An edge with `evidence` (calls, inherits)
        is never duplicated: adding it again only merges its lines and argument shapes."""
        if src not in self.nodes or dst not in self.nodes:
            return
        if evidence is not None:
            key = (src, dst, etype)
            old = self._info.get(key)
            if old is not None:
                self._info[key] = (old[0], tuple(sorted(set(old[1]) | set(lines))),
                                   tuple(sorted(set(old[2]) | set(args))))
                return
            self._info[key] = (evidence, tuple(sorted(set(lines))), tuple(sorted(set(args))))
        self._out[src].append((dst, etype))
        self._in[dst].append((src, etype))

    def edge_info(self, src, dst, etype="calls"):
        """{"evidence": tag, "lines": [...]} for a calls or inherits edge, else {}. A calls
        edge whose calls are Python adds "args": how each call passes its arguments, as
        {"positional": n, "star": bool, "keywords": [...], "double_star": bool, "on_class": bool}.
        For a method the positional count leaves out the receiver, also where a call passes it
        by hand (Cls.m(obj, ...) counts like obj.m(...)); on_class says whether the call is made
        on the class (Cls.m(...), or cls.m(...) in a classmethod) rather than on an instance."""
        info = self._info.get((src, dst, etype))
        if not info:
            return {}
        out = {"evidence": info[0], "lines": list(info[1])}
        if len(info) > 2 and info[2]:
            out["args"] = [{"positional": p, "star": s, "keywords": list(k), "double_star": d, "on_class": c}
                           for p, s, k, d, c in info[2]]
        return out

    def successors(self, nid, etype=None):
        return [d for d, t in self._out.get(nid, []) if etype is None or t == etype]

    def predecessors(self, nid, etype=None):
        return [s for s, t in self._in.get(nid, []) if etype is None or t == etype]

    def neighbors(self, nid):
        return list({d for d, _ in self._out.get(nid, [])}
                    | {s for s, _ in self._in.get(nid, [])})

    def callees(self, sym_id):
        return self.successors(sym_id, "calls")

    def callers(self, sym_id):
        return self.predecessors(sym_id, "calls")

    def get(self, nid):
        return self.nodes.get(nid)

    def all_edges(self, etype=None):
        return [(src, dst, t) for src, lst in self._out.items()
                for dst, t in lst if etype is None or t == etype]

    def stats(self):
        et = defaultdict(int)
        for lst in self._out.values():
            for _, t in lst:
                et[t] += 1
        nt = defaultdict(int)
        for v in self.nodes.values():
            nt[v["type"]] += 1
        return {"nodes": len(self.nodes), "edges": sum(et.values()),
                "node_types": dict(nt), "edge_types": dict(et)}


# ---------------- Python source roots ----------------
# A module name resolves against every root: the indexed folder itself, the parent of every
# top-level package (a folder with __init__.py whose parent has none), src/, and any literal
# pythonpath, package-dir or where setting in pyproject.toml, setup.cfg or pytest.ini. A name
# that maps to two different files is ambiguous: it is skipped and counted, never guessed.
_NOT_LITERAL = re.compile(r"[*?\[\]{}$%]")


def _strip_toml_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _toml_settings(text):
    """{(table, key): raw value} from TOML text, enough for the settings below: strings,
    arrays of strings and inline tables of strings, also over several lines. (Python 3.10,
    which the engine supports, has no TOML parser.)"""
    out, table = {}, ""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = _strip_toml_comment(lines[i]).strip()
        i += 1
        header = re.match(r"^\[\[?\s*([^\]]+?)\s*\]\]?$", line)
        if header:
            table = re.sub(r"""\s*\.\s*""", ".", header.group(1)).replace('"', "").replace("'", "")
            continue
        m = re.match(r"""^("[^"]*"|'[^']*'|[A-Za-z0-9_.-]+)\s*=\s*(.*)$""", line)
        if not m:
            continue
        value = m.group(2)
        while (value.count("[") > value.count("]") or value.count("{") > value.count("}")) and i < len(lines):
            value += " " + _strip_toml_comment(lines[i]).strip()
            i += 1
        out[(table, m.group(1).strip("\"'"))] = value
    return out


def _strings(value):
    return [a or b for a, b in re.findall(r'"([^"]*)"|\'([^\']*)\'', value or "")]


def _config_values(path, text):
    """The literal folders a config file adds to the import path."""
    name = path.rsplit("/", 1)[-1]
    values = []
    if name == "pyproject.toml":
        s = _toml_settings(text)
        values += _strings(s.get(("tool.pytest.ini_options", "pythonpath")))
        values += _strings(s.get(("tool.setuptools.packages.find", "where")))
        for key, val in re.findall(r"""("[^"]*"|'[^']*'|[\w.-]+)\s*=\s*("[^"]*"|'[^']*')""",
                                   s.get(("tool.setuptools", "package-dir"), "")):
            if key.strip("\"'") == "":
                values.append(val.strip("\"'"))
        values += _strings(s.get(("tool.setuptools.package-dir", "")))
    else:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read_string(text)
        except configparser.Error:
            return []
        section = "pytest" if name == "pytest.ini" else "tool:pytest"
        if cp.has_option(section, "pythonpath"):
            values += cp.get(section, "pythonpath").split()
        if name == "setup.cfg":
            for key in ("package_dir", "package-dir"):
                if cp.has_option("options", key):
                    for line in cp.get("options", key).splitlines():
                        k, eq, v = line.partition("=")
                        if eq and not k.strip() and v.strip():
                            values.append(v.strip())
            if cp.has_option("options.packages.find", "where"):
                values += cp.get("options.packages.find", "where").split()
    return values


def _config_roots(path, text):
    base = posixpath.dirname(path)
    roots = []
    for v in _config_values(path, text):
        v = v.strip().replace("\\", "/")
        if not v or _NOT_LITERAL.search(v) or v.startswith("/") or re.match(r"^[A-Za-z]:", v):
            continue
        r = posixpath.normpath(posixpath.join(base, v))
        if r == ".":
            r = ""
        if r == ".." or r.startswith("../"):
            continue
        roots.append(r)
    return roots


def python_roots(parsed_files):
    """The folders Python module names are resolved from (see above), "" for the indexed folder."""
    py = [pf.file for pf in parsed_files if pf.language == PYTHON]
    inits = {f for f in py if f == "__init__.py" or f.endswith("/__init__.py")}
    roots = {""}
    for f in inits:
        pkg_dir = posixpath.dirname(f)
        if pkg_dir:
            parent = posixpath.dirname(pkg_dir)
            if (parent + "/__init__.py" if parent else "__init__.py") not in inits:
                roots.add(parent)
    if any(f.startswith("src/") for f in py):
        roots.add("src")
    for pf in parsed_files:
        if pf.file.rsplit("/", 1)[-1] in ("pyproject.toml", "setup.cfg", "pytest.ini"):
            roots.update(_config_roots(pf.file, pf.text))
    return sorted(roots)


def python_modules(parsed_files):
    """({module name: file}, [ambiguous module names], [roots])."""
    roots = python_roots(parsed_files)
    index = defaultdict(set)
    for pf in parsed_files:
        if pf.language != PYTHON or not pf.file.endswith(".py"):
            continue
        for r in roots:
            if r and not pf.file.startswith(r + "/"):
                continue
            mod = (pf.file[len(r) + 1:] if r else pf.file)[:-3].replace("/", ".")
            if mod == "__init__":
                continue                  # the root's own package has no name here
            if mod.endswith(".__init__"):
                mod = mod[:-len(".__init__")]
            index[mod].add(pf.file)
    modules = {m: next(iter(fs)) for m, fs in index.items() if len(fs) == 1}
    ambiguous = sorted(m for m, fs in index.items() if len(fs) > 1)
    return modules, ambiguous, roots


# ---------------- binding ----------------
class _Resolver:
    """Binds call sites and base classes to symbols. A cross-file edge needs import evidence;
    Python binds only to Python and JS/TS only to JS/TS; anything uncertain stays unbound,
    because a wrong edge is worse than a missing one."""

    def __init__(self, parsed_files):
        self.files = {pf.file: pf for pf in parsed_files}
        self.top = {}                    # file -> {name: [top-level symbol ids]}
        self.members = {}                # class id -> {name: [member ids]}
        self.syms = {}                   # id -> Symbol
        for pf in parsed_files:
            for sym in pf.symbols:
                self.syms[sym.id] = sym
                parent_id = f"{pf.file}::{sym.parent}" if sym.parent else None
                if parent_id and parent_id != sym.id:
                    self.members.setdefault(parent_id, {}).setdefault(sym.name, []).append(sym.id)
                else:
                    self.top.setdefault(pf.file, {}).setdefault(sym.name, []).append(sym.id)
        self.modules, self.ambiguous, self.roots = python_modules(parsed_files)
        self.prefixes = set()            # every module name and package path under a root
        for m in list(self.modules) + self.ambiguous:
            parts = m.split(".")
            for i in range(1, len(parts) + 1):
                self.prefixes.add(".".join(parts[:i]))
        self.ambiguous_set = set(self.ambiguous)
        self.assigned = {pf.file: set(pf.assigned) for pf in parsed_files if pf.assigned}
        self._bases = {}

    # -- modules
    def sibling(self, importer, name):
        """The module `name` next to the importing file, when that file's folder is not a
        package: the folder a script runs from, which Python puts first on sys.path (and pytest
        does the same for test folders without __init__.py)."""
        d = posixpath.dirname(importer or "")
        if not d or not name or (d + "/__init__.py") in self.files:
            return None
        base = d + "/" + name.replace(".", "/")
        for cand in (base + ".py", base + "/__init__.py"):
            pf = self.files.get(cand)
            if pf is not None and pf.language == PYTHON:
                return cand
        return None

    def module_for(self, name, importer=None):
        """The file a module name resolves to from `importer`: through the source roots, else
        through the importer's own folder (see sibling). When both find a file and they
        differ, or the name is ambiguous across roots, it resolves to nothing."""
        if not name:
            return None
        parts = name.split(".")
        if any(".".join(parts[:i]) in self.ambiguous_set for i in range(1, len(parts) + 1)):
            return None                           # under a package name that maps to two folders
        f = self.modules.get(name)
        s = self.sibling(importer, name) if importer else None
        if f is not None and s is not None and f != s:
            return None
        return f or s

    def js_module(self, importer, spec, any_language=False):
        """The file a relative specifier names (with extensions and index files); package
        specifiers, tsconfig paths and workspaces are not resolved."""
        if not spec.startswith("."):
            return None
        target = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
        cands = [target] + [target + e for e in _JS_EXTS] + [target + "/index" + e for e in _JS_EXTS]
        stem, ext = posixpath.splitext(target)
        if ext in (".js", ".jsx"):                # TypeScript ESM imports name the compiled file
            cands += [stem + ".ts", stem + ".tsx"]
        for c in cands:
            pf = self.files.get(c)
            if pf is not None and (any_language or pf.language in JS_LANGS):
                return c
        return None

    def submodule(self, pkg_file, name):
        if not (pkg_file == "__init__.py" or pkg_file.endswith("/__init__.py")):
            return None
        d = posixpath.dirname(pkg_file)
        prefix = d + "/" if d else ""
        for cand in (prefix + name + ".py", prefix + name + "/__init__.py"):
            pf = self.files.get(cand)
            if pf is not None and pf.language == PYTHON:
                return cand
        return None

    # -- what a name means in a module: ("symbols", ids, hops) or ("module", file, name, hops)
    def py_member(self, file, name, hops):
        pf = self.files[file]
        if pf.bindings.get(name) == AMBIGUOUS:
            return None                           # bound two ways there (try/except alternatives)
        ids = self.top.get(file, {}).get(name)
        if ids:
            return ("symbols", tuple(ids), hops)
        if hops < MAX_REEXPORT_HOPS:
            b = pf.bindings.get(name)
            if b is not None and b != AMBIGUOUS:
                t = self.py_binding(b, hops + 1, importer=file)
                if t is not None:                 # a re-exported module is not a re-export hop
                    return t if t[0] == "symbols" else ("module", t[1], t[2], hops)
        if name in self.assigned.get(file, ()):
            return None                           # a variable there, which shadows any submodule
        sub = self.submodule(file, name)
        if sub:
            return ("module", sub, None, hops)
        if hops < MAX_REEXPORT_HOPS:
            for star in pf.star_imports:          # import * counts only names in a literal __all__
                sf = self.module_for(star, file)
                if sf and sf != file and name in self.files[sf].exports:
                    t = self.py_member(sf, name, hops + 1)
                    if t is not None:
                        return t
        return None

    def py_binding(self, b, hops=0, importer=None):
        """What an import binding in `importer` names. `from . import x` inside a package's
        own __init__.py is a submodule, never a member of itself."""
        if b[0] == "module":
            return ("module", self.module_for(b[1], importer), b[1], hops)
        mod, attr = b[1], b[2]
        full = f"{mod}.{attr}" if mod else attr
        f = self.module_for(mod, importer)
        if f is not None and f != importer:
            return self.py_member(f, attr, hops)  # members, re-exports and submodules
        sub = self.module_for(full, importer)
        if sub is not None:
            return ("module", sub, full, hops)
        if full in self.prefixes:                 # a namespace package path
            return ("module", None, full, hops)
        return None

    def js_member(self, file, name, hops):
        pf = self.files[file]
        if name == "default" and pf.default_export:
            name = pf.default_export
        ids = self.top.get(file, {}).get(name)
        if ids:
            return ("symbols", tuple(ids), hops)
        if hops < MAX_REEXPORT_HOPS:
            b = pf.reexports.get(name)
            if b is not None:
                t = self.js_binding(b, file, hops + 1)
                if t is not None:
                    return t
            for spec in pf.star_reexports:
                sf = self.js_module(file, spec)
                if sf and sf != file:
                    t = self.js_member(sf, name, hops + 1)
                    if t is not None:
                        return t
        return None

    def js_binding(self, b, importer, hops=0):
        f = self.js_module(importer, b[1])
        if f is None:
            return None
        if b[0] == "module":
            return ("module", f, None, hops)
        return self.js_member(f, b[2], hops)

    def binding(self, b, file):
        if self.files[file].language == PYTHON:
            return self.py_binding(b, importer=file)
        return self.js_binding(b, file)

    def external(self, b, file):
        """True when a binding names a module outside the repository (stdlib, a package)."""
        if self.files[file].language == PYTHON:
            full = f"{b[1]}.{b[2]}" if b[0] == "from" and b[1] else (b[2] if b[0] == "from" else b[1])
            return not any(n in self.prefixes or self.sibling(file, n) for n in (b[1], full))
        return not b[1].startswith(".")

    def walk(self, t, attrs, lang, importer=None):
        """Follow attribute names from a module target through members, re-exports and
        submodules: (symbol ids, hops, attributes left) once symbols are reached, else None."""
        _, f, name, hops = t
        for i, a in enumerate(attrs):
            nt = None
            if f is not None:
                nt = self.py_member(f, a, hops) if lang == PYTHON else self.js_member(f, a, hops)
            sub = f"{name}.{a}" if name else None
            if nt is None and sub and lang == PYTHON:
                sf = self.module_for(sub, importer)
                if sf is not None:
                    nt = ("module", sf, sub, hops)
                elif sub in self.prefixes:
                    nt = ("module", None, sub, hops)
            if nt is None:
                return None
            if nt[0] == "symbols":
                return nt[1], nt[2], attrs[i + 1:]
            _, f, name, hops = nt
            if name is None:
                name = sub
        return None

    def star(self, file, name):
        found = []
        for mod in self.files[file].star_imports:
            sf = self.module_for(mod, file)
            if sf and name in self.files[sf].exports:
                t = self.py_member(sf, name, 0)
                if t is not None and t not in found:
                    found.append(t)
        return found[0] if len(found) == 1 else None

    # -- classes
    def class_ref(self, file, ref):
        """[(class id, evidence)] for a reference to a class (a base, or a typed receiver's class)."""
        kind, name, attrs, line, b = ref
        lang = self.files[file].language
        hits = []
        if kind in ("same", "bare"):
            if not attrs:
                hits = [(sid, "same_file") for sid in self.top.get(file, {}).get(name, ())]
        elif kind in ("import", "star"):
            t = self.binding(b, file) if kind == "import" else self.star(file, name)
            ev = "import_alias" if kind == "import" and b[0] == "from" and b[3] else "import"
            if t is not None and t[0] == "symbols" and not attrs:
                hits = [(sid, "reexport" if t[2] else ev) for sid in t[1]]
            elif t is not None and t[0] == "module" and attrs:
                w = self.walk(t, attrs, lang, file)
                if w is not None and not w[2]:
                    hits = [(sid, "reexport" if w[1] else "module_attr") for sid in w[0]]
        return [(sid, ev) for sid, ev in hits if self.syms[sid].kind == "class"]

    def bases(self, cls_id):
        """[(base class id, evidence)]: the direct bases that bind to repository classes."""
        if cls_id in self._bases:
            return self._bases[cls_id]
        self._bases[cls_id] = []                  # guards a cycle
        out = []
        sym = self.syms.get(cls_id)
        for ref in (sym.base_refs if sym is not None else ()):
            for base, ev in self.class_ref(sym.file, ref):
                if base != cls_id and base not in [b for b, _ in out]:
                    out.append((base, ev))
        self._bases[cls_id] = out
        return out

    def _methods(self, cls_id, name, with_bases):
        """The class's own methods called `name`; with `with_bases`, else those of the first
        direct base that defines it (an overridden method is never the one called)."""
        own = self.members.get(cls_id, {}).get(name)
        if own or not with_bases:
            return list(own or ())
        for base, _ in self.bases(cls_id):
            found = self.members.get(base, {}).get(name)
            if found:
                return list(found)
        return []

    # -- call sites
    def _finish(self, ids, rest, hops, ev):
        """Edges for calling the symbols `ids` with the attribute names `rest` still to apply:
        a function is called itself; a class is a constructor call (its __init__, else the
        class); Cls.m is the method on the class, else on a direct base."""
        out = []
        for sid in ids:
            is_class = self.syms[sid].kind == "class"
            if not rest:
                if is_class:
                    lang = self.files[self.syms[sid].file].language
                    ctor = "__init__" if lang == PYTHON else "constructor" if lang in JS_LANGS else None
                    init = self._methods(sid, ctor, False) if ctor else []
                    out.extend((t, "constructor") for t in (init or [sid]))
                else:
                    out.append((sid, "reexport" if hops else ev))
            elif len(rest) == 1 and is_class:
                out.extend((m, "class_attr") for m in self._methods(sid, rest[0], True))
        return out

    def resolve(self, sym, site):
        """[(target id, evidence)] for one call site of `sym`, or why it binds to nothing in
        the repository: external, missing, local, global, free, unknown or ambiguous."""
        kind, name, attrs, line, ref = site[:5]
        file = sym.file
        lang = self.files[file].language
        if kind in ("same", "bare"):
            # a plain call never reaches a method, even one recorded at the top level (Go)
            ids = [i for i in self.top.get(file, {}).get(name, ()) if self.syms[i].kind != "method"]
            return (self._finish(ids, attrs, 0, "same_file") or "missing") if ids else "free"
        if kind in ("import", "star"):
            if kind == "import":
                t = self.binding(ref, file)
                if t is None:
                    return "external" if self.external(ref, file) else "missing"
                ev = "import_alias" if ref[0] == "from" and ref[3] else "import"
            else:
                t = self.star(file, name)
                if t is None:
                    return "free"
                ev = "import"
            if t[0] == "symbols":
                return self._finish(t[1], attrs, t[2], ev) or "missing"
            if not attrs:
                return "missing"                  # a module called like a function
            w = self.walk(t, attrs, lang, file)
            if w is None:
                return "external" if kind == "import" and self.external(ref, file) else "missing"
            return self._finish(w[0], w[2], w[1], "module_attr") or "missing"
        if kind == "self":
            cls = f"{file}::{sym.parent}" if sym.parent else None
            if cls not in self.syms:
                return "unknown"
            return [(m, "self_method") for m in self._methods(cls, attrs[0], True)] or "missing"
        if kind == "typed":
            ckind, cname, cref = ref
            if (ckind == "import" and self.binding(cref, file) is None and self.external(cref, file)) \
                    or (ckind == "free" and cname in _BUILTINS):
                return "external"                 # an instance of a class from outside the repo
            if ckind not in ("same", "import", "star"):
                return ckind
            classes = self.class_ref(file, (ckind, cname, (), line, cref))
            if not classes:
                return "unknown"                  # obj = factory(): its type is not known
            out = []
            for cls, _ in classes:
                out.extend((m, "instance") for m in self._methods(cls, attrs[0], True))
            return out or "missing"
        return kind


def _namespace_notes(r):
    """A Not analysed entry for each folder without __init__.py that an unresolved Python
    import goes through."""
    dirs = defaultdict(set)
    for f, pf in r.files.items():
        d = posixpath.dirname(f)
        if pf.language == PYTHON and d and (d + "/__init__.py") not in r.files:
            dirs[posixpath.basename(d)].add(d)
    hit = set()
    for f, pf in r.files.items():
        if pf.language == PYTHON:
            for m in pf.imports:
                if r.module_for(m, f) is None:
                    hit.update(dirs.get(m.split(".")[0], ()))
    return [{"path": d + "/", "reason": NAMESPACE_NOTE} for d in sorted(hit)]


_IMPLICIT_CLASS = ("__new__", "__init_subclass__", "__class_getitem__")   # class methods without the decorator


def _on_class(caller, evidence):
    """Whether a call bound with `evidence` is made on the class, not on an instance: Cls.m(...),
    or cls.m(...) in a classmethod."""
    if evidence == "class_attr":
        return True
    return evidence == "self_method" and ("classmethod" in caller.decorators or caller.name in _IMPLICIT_CLASS)


def _passes_receiver(target, on_class):
    """Whether a call made on the class passes the method's receiver by hand: Cls.m(obj, ...)
    does, for a method that is neither a classmethod nor a staticmethod."""
    return (on_class and target.kind == "method"
            and not {"classmethod", "staticmethod"} & set(target.decorators))


def _after_receiver(shape):
    """The argument shape of Cls.m(obj, ...) without obj, as obj.m(...) counts it."""
    positional, star, keywords, double_star = shape
    return (positional - 1, star, keywords, double_star) if positional else shape


def build_graph(parsed_files):
    g = CodeGraph()
    for pf in parsed_files:
        g.add_node(pf.file, "file", {"language": pf.language, "loc": pf.loc,
                                     "imports": pf.imports, "error": pf.error})
        for sym in pf.symbols:
            g.add_node(sym.id, "symbol", {
                "name": sym.name, "qualname": sym.qualname, "kind": sym.kind,
                "file": sym.file, "start": sym.start_line, "end": sym.end_line,
                "doc": sym.docstring, "complexity": sym.complexity,
                "bases": sym.bases,
            })
            g.add_edge(pf.file, sym.id, "contains")
    for pf in parsed_files:
        for sym in pf.symbols:
            if sym.parent:
                parent_id = f"{pf.file}::{sym.parent}"
                if parent_id in g.nodes and parent_id != sym.id:
                    g.add_edge(sym.id, parent_id, "method_of")

    r = _Resolver(parsed_files)
    for pf in parsed_files:
        unresolved = []
        for sym in pf.symbols:
            found = {}                            # target -> [evidence, lines, args]: one edge each
            for site in sym.call_sites:
                res = r.resolve(sym, site)
                if isinstance(res, str):
                    if res == "external" or (res == "free" and site[1] in (
                            _BUILTINS if pf.language == PYTHON else _JS_GLOBALS)):
                        continue
                    unresolved.append((sym.id, call_name(site), site[3], res))
                    continue
                shape = site_args(site)
                for target, ev in res:
                    if target == sym.id:
                        continue
                    if target not in found:
                        found[target] = [ev, set(), set()]
                    found[target][1].add(site[3])
                    if shape is not None:
                        on_class = _on_class(sym, ev)
                        s = _after_receiver(shape) if _passes_receiver(r.syms[target], on_class) else shape
                        found[target][2].add(s + (on_class,))
            for target, (ev, lines, args) in found.items():
                g.add_edge(sym.id, target, "calls", evidence=ev, lines=lines, args=args)
            if sym.kind == "class":
                for base, ev in r.bases(sym.id):
                    g.add_edge(sym.id, base, "inherits", evidence=ev, lines=(sym.start_line,))
        if unresolved:
            g.unresolved[pf.file] = unresolved
            g.nodes[pf.file]["data"]["unresolved_calls"] = len(unresolved)

    for pf in parsed_files:
        seen_imp = set()
        if pf.language in JS_LANGS:
            dsts = [r.js_module(pf.file, spec, any_language=True) for spec in pf.imports]
        else:
            # module-level imports resolve the file; import_targets add submodule-file
            # candidates (pkg/submodule.py) for the Python "from pkg import submodule" case.
            dsts = [r.module_for(m, pf.file) for m in list(pf.imports) + list(pf.import_targets)]
        for dst in dsts:
            if dst and dst != pf.file and dst not in seen_imp:
                seen_imp.add(dst)
                g.add_edge(pf.file, dst, "imports")

    if any(pf.language in JS_LANGS for pf in parsed_files):
        g.not_analysed.append({"path": "", "reason": JS_TEXT_NOTE})
    g.not_analysed.extend(_namespace_notes(r))
    g.resolution = {"roots": r.roots, "ambiguous_modules": r.ambiguous}
    return g
