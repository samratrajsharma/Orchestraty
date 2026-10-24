"""TypeScript imports in one copy of the code: which file a specifier names, through relative
paths and a tsconfig's ``baseUrl`` and ``paths`` aliases, and which files use an export of a
module, with the lines they use it on.

A use needs import evidence: the file imports the module (directly, or through re-exports, at
most ``MAX_HOPS`` of them), and names what it imported outside the import itself (a named or
default import by its local name, a namespace import as ``ns.name``). Which of those are calls,
and what they pass, is not resolved: uses are heuristic.

tsconfig files are read leniently (comments and trailing commas allowed) and their ``extends``
chain is followed through relative paths; a package's config (``"@scope/base"``) is not read.
``paths`` are relative to ``baseUrl`` when one is set, else to the tsconfig that sets them; the
pattern with the longest prefix before its ``*`` wins, as the compiler does.
"""
import json
import os
import posixpath
import re

from orchestraty.livemap import dts

TS_EXTS = (".ts", ".tsx", ".mts", ".cts")
JS_EXTS = (".js", ".jsx", ".mjs", ".cjs")
CODE_EXTS = TS_EXTS + JS_EXTS
TRY = (".ts", ".tsx", ".d.ts", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
JS_TO_TS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
MAX_HOPS = 3
MAX_EXTENDS = 8
_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', re.S)


def is_typescript(path):
    return path.endswith(TS_EXTS) and not path.endswith((".d.ts", ".d.mts", ".d.cts"))


def lenient_json(text):
    """A tsconfig's JSON: comments and trailing commas allowed. None when it does not parse."""
    text = _COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)
    text = _TRAILING_COMMA.sub(lambda m: m.group(0) if m.group(0).startswith('"') else m.group(1), text)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class TsConfig:
    """The module resolution settings of one tsconfig.json (its extends chain merged): its
    folder, baseUrl and paths, as posix paths inside the copy of the code."""

    def __init__(self, path, read):
        """`path`: the tsconfig's posix path in the copy; `read(path)`: its text, or None."""
        self.path = path
        self.folder = posixpath.dirname(path)
        self.base_url = None            # posix folder, or None
        self.paths = []                 # [(pattern, [target folder-relative patterns], folder they are relative to)]
        self.options = {}               # compilerOptions, the extends chain merged
        self.ok = False
        self._load(path, read, 0, set())

    def _load(self, path, read, depth, seen):
        if depth > MAX_EXTENDS or path in seen:
            return
        seen.add(path)
        text = read(path)
        data = lenient_json(text) if text is not None else None
        if data is None:
            return
        self.ok = True
        folder = posixpath.dirname(path)
        parents = data.get("extends")
        for parent in [parents] if isinstance(parents, str) else parents if isinstance(parents, list) else []:
            if isinstance(parent, str) and parent.startswith("."):
                target = posixpath.normpath(posixpath.join(folder, parent))
                if not target.endswith(".json"):
                    target += ".json"
                self._load(target, read, depth + 1, seen)      # the parent first: this file overrides it
        options = data.get("compilerOptions")
        if not isinstance(options, dict):
            return
        self.options.update(options)
        if isinstance(options.get("baseUrl"), str):
            self.base_url = posixpath.normpath(posixpath.join(folder, options["baseUrl"]))
        if isinstance(options.get("paths"), dict):
            self.paths = [(pattern, [t for t in targets if isinstance(t, str)], folder)
                          for pattern, targets in options["paths"].items() if isinstance(targets, list)]

    def targets(self, spec):
        """The candidate paths (without extension) an alias gives `spec`, best match first."""
        best, out = None, []
        for pattern, targets, folder in self.paths:
            if "*" in pattern:
                head, _, tail = pattern.partition("*")
                if spec.startswith(head) and spec.endswith(tail) and len(spec) >= len(head) + len(tail):
                    star = spec[len(head):len(spec) - len(tail)]
                    if best is None or len(head) > best[0]:
                        best = (len(head), [(t.replace("*", star), folder) for t in targets])
            elif pattern == spec:
                best = (len(pattern) + 1, [(t, folder) for t in targets])
                break
        if best is not None:
            root = self.base_url if self.base_url is not None else None
            for target, folder in best[1]:
                out.append(posixpath.normpath(posixpath.join(root if root is not None else folder, target)))
        if self.base_url is not None:
            out.append(posixpath.normpath(posixpath.join(self.base_url, spec)))
        return out


class Resolver:
    """Specifiers to files, in one copy of the code: `files` is the set of its posix paths and
    `read(path)` gives a file's text (for its tsconfigs)."""

    def __init__(self, files, read):
        self.files = files
        self.read = read
        self._configs = {}

    def config_for(self, path):
        """The nearest tsconfig.json at or above a file's folder, or None."""
        folder = posixpath.dirname(path)
        while True:
            candidate = posixpath.join(folder, "tsconfig.json") if folder else "tsconfig.json"
            if candidate not in self._configs:
                text = self.read(candidate)
                self._configs[candidate] = TsConfig(candidate, self.read) if text is not None else None
            if self._configs[candidate] is not None:
                return self._configs[candidate]
            if not folder:
                return None
            folder = posixpath.dirname(folder)

    def file(self, candidate):
        """The file a candidate path names: as written, its .js mapped to .ts, with an extension
        added, or as a folder's index."""
        stem, ext = posixpath.splitext(candidate)
        options = []
        if ext in TRY:
            options.append(candidate)
            options += [stem + e for e in JS_TO_TS.get(ext, ())]
        options += [candidate + e for e in TRY] + [candidate + "/index" + e for e in TRY]
        for path in options:
            if path in self.files:
                return path
        return None

    def resolve(self, importer, spec):
        """The file in this copy that `spec`, imported from `importer`, names, or None (a package,
        or nothing that exists)."""
        if not spec:
            return None
        if spec.startswith("."):
            return self.file(posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec)))
        config = self.config_for(importer)
        if config is None:
            return None
        for candidate in config.targets(spec):
            found = self.file(candidate)
            if found:
                return found
        return None


def _skip_import(toks, k):
    """Past an import statement, or an export list or "export *" (re-exports name no use)."""
    n = len(toks)
    j = k + 1
    if toks[k][1] == "export":
        if toks[j][1] == "type":
            j += 1
        if toks[j][1] == "{":
            depth = 0
            while j < n:
                depth += {"{": 1, "}": -1}.get(toks[j][1], 0)
                j += 1
                if depth == 0:
                    break
            if j < n and toks[j][1] == "from":
                j += 2
            return j
    while j < n and toks[j][0] != "str" and toks[j][1] != ";":
        j += 1
    return j + 1


def _substitutions(template, line):
    """The tokens of a template literal's ${...} expressions, on the lines they are on."""
    out, j, n = [], 1, len(template)
    while j < n - 1:
        c = template[j]
        if c == "\\":
            j += 2
        elif c == "$" and template[j + 1] == "{":
            end = dts._expression_end(template, j + 2)
            first = line + template.count("\n", 0, j + 2)
            out += [(kind, v, first + at - 1) for kind, v, at in dts.tokens(template[j + 2:end - 1], source=True)]
            j = end
        else:
            j += 1
    return out


def _with_substitutions(toks):
    """The tokens, each template literal followed by the tokens of its ${...} expressions."""
    out = []
    for tok in toks:
        out.append(tok)
        if tok[0] == "str" and tok[1].startswith("`") and "${" in tok[1]:
            out += _with_substitutions(_substitutions(tok[1], tok[2]))
    return out


def local_uses(toks, local, member=None):
    """The lines where a local name is used (outside strings, comments and import or re-export
    statements, and inside a template literal's ${...} expressions): `local`, or with `member`,
    `local.member`."""
    toks = _with_substitutions(toks)
    lines, k, n = set(), 0, len(toks)
    while k < n:
        kind, v, line = toks[k]
        if kind == "id" and ((v == "import" and k + 1 < n and toks[k + 1][1] not in ("(", "."))
                             or (v == "export" and k + 1 < n and toks[k + 1][1] in ("{", "*", "type")
                                 and (toks[k + 1][1] != "type" or (k + 2 < n and toks[k + 2][1] == "{")))):
            k = _skip_import(toks, k)
            continue
        if kind == "id" and v == local and not (k > 0 and toks[k - 1][1] in (".", "?.")):
            if member is None or (k + 2 < n and toks[k + 1][1] in (".", "?.") and toks[k + 2][1] == member):
                lines.add(line)
        k += 1
    return sorted(lines)


class Imports:
    """Who uses what in one copy of the code. `texts`: {posix path: text} of its TypeScript and
    JavaScript files; `resolver`: a Resolver over the same copy."""

    def __init__(self, texts, resolver):
        self.texts = texts
        self.r = resolver
        self._resolved, self._tokens, self._modules = {}, {}, {}

    def _resolve(self, importer, spec):
        key = (importer, spec)
        if key not in self._resolved:
            self._resolved[key] = self.r.resolve(importer, spec)
        return self._resolved[key]

    def tokens(self, path):
        if path not in self._tokens:
            self._tokens[path] = dts.tokens(self.texts.get(path) or "", source=True)
        return self._tokens[path]

    def module(self, path):
        """What a file exports and imports (the declaration reader, on its source)."""
        if path not in self._modules:
            self._modules[path] = dts.read_tokens(self.tokens(path), source=not path.endswith(".d.ts"))
        return self._modules[path]

    def origin(self, file, name, hops=0):
        """The (module, export name) an export of `file` comes from, following re-exports (an
        export list naming an import, "export ... from", "export *"); (file, name) when the file
        declares it, or when where it comes from is not known."""
        if file is None or hops >= MAX_HOPS:
            return file, name
        m = self.module(file)
        d = m["exports"].get(name)
        if d is not None and d["kind"] == "reexport" and d["name"] != "*":
            target = self._resolve(file, d["from"])
            return self.origin(target, d["name"], hops + 1) if target else (file, name)
        if d is not None:
            return file, name
        for spec in m["stars"]:
            target = self._resolve(file, spec)
            if target and target != file:
                found = self.origin(target, name, hops + 1)
                if found[0] is not None and name in self.module(found[0])["exports"]:
                    return found
        return file, name

    def uses(self, module, name):
        """{file: sorted lines} of the files of this copy that import `name` from `module` (or from
        a file re-exporting it) and use it. Heuristic: calls are not resolved."""
        out = {}
        needle = name if name not in ("default", "*") else posixpath.splitext(posixpath.basename(module))[0]
        for path, text in sorted(self.texts.items()):
            if path == module or needle not in (text or ""):
                continue
            lines = set()
            for local, (spec, imported) in self.module(path)["imports"].items():
                target = self._resolve(path, spec)
                if target is None:
                    continue
                if imported == "*":
                    if self.origin(target, name) == (module, name):
                        lines.update(local_uses(self.tokens(path), local, member=name))
                elif self.origin(target, imported) == (module, name):
                    lines.update(local_uses(self.tokens(path), local))
            if lines:
                out[path] = sorted(lines)
        return out
