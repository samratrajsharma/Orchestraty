# Engine

Orchestraty's effect engine. It ingests a repository, parses it and builds the code graph
that the analyses below read from. Parsing uses Python's `ast` for Python, heuristics for
JavaScript/TypeScript, and tree-sitter for other languages when the `treesitter` extra is
installed. The core needs only the standard library.

| Module | What it does |
| --- | --- |
| `ingest` | Opens a local folder or clones a repository safely, and lists its files; reads any commit from git's object store |
| `parsing`, `treesitter_parser` | Symbols, imports, calls and complexity per file |
| `graph` | The code graph: files and symbols, with contains, imports, calls, inherits and method_of edges; the resolver that binds calls (see Call edges) |
| `pipeline` | `build_index()`: ingest, parse and build the graph, with an HMAC-signed cache. `build_index_at()` does the same for any commit, straight from git's objects |
| `impact` | Blast radius of a symbol or a file |
| `insights` | Entry points, hub files, routes and data models |
| `techdebt` | Import cycles, complexity hotspots, dead code and near-duplicates |
| `coverage` | Which symbols the tests reference |
| `config_map` | The configuration surface |
| `diagram` | Architecture map and focused views, as Mermaid or Graphviz |
| `track` | Fingerprints and structural snapshots of commits (read from git's objects), and the differences between them |
| `check` | Structural regression check between two refs |
| `context` | An AGENTS.md generated from the graph |
| `mcp_server` | Read-only graph tools for coding agents (needs the `mcp` extra) |

## Run the tests

```
cd engine
python -m pytest -q
```

Only pytest is needed. `pip install -e ".[treesitter]"` adds the other languages.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `ORCH_DATA_DIR` | `.orch_cache` | Cache, clones and snapshots |
| `ORCH_USE_CACHE` | `1` | Set to `0` to turn the cache off; nothing is then read from or written to the data folder |

## Ids and commits

- **Ids are POSIX paths on every OS:** a file is `pkg/sub/mod.py` and a symbol `pkg/sub/mod.py::compute`, relative to the indexed folder. The MCP tools accept `pkg\sub\mod.py`, `./pkg/sub/mod.py` or an absolute path inside the repository, and normalise it.
- **A commit is read, never checked out.** `build_index_at(repo, ref)` and `track.snapshot` list the commit with `git ls-tree` and read its files with `git cat-file --batch`, at the repository's top folder. No worktree or archive is made and nothing is written into the repository, so `export-ignore` attributes don't hide files. The ref is resolved to its full SHA, which keys the caches together with the engine's schema version.
- **Not analysed.** `RepoIndex.not_analysed` lists what an index left out, as `{"path", "reason"}`: skipped folders (`env/`, `build/`, `node_modules/` and the others in `SKIP_DIRS`, at any depth), symlinks, submodules, and files over 2 MB when read from a commit. It also lists what binding cannot cover: namespace packages whose imports stay unresolved, TypeScript path aliases and workspace packages, and (path `""`) that JS/TS symbols are compared as normalised text. The MCP `overview` tool shows it.
- **Git output is read as UTF-8,** whatever the locale, so a non-ASCII author, message or folder name works on Windows without `PYTHONUTF8`.

## Call edges

A call edge is made only when the resolver can show how a name is bound. A cross-file edge needs import evidence; Python binds only to Python and JS/TS only to JS/TS; anything uncertain stays unbound, because a missing edge is better than a wrong one. Every calls edge, and every inherits edge, carries one evidence tag and the lines of its call sites. A Python calls edge also carries how each of its calls passes its arguments (`args`: how many by position, which keywords, whether it unpacks `*x` or `**x`); for a method the positional count starts after the receiver, also when a call passes it by hand (`Cls.m(obj, ...)` counts like `obj.m(...)`). Duplicate edges are merged, keeping all their lines and argument shapes. `graph.edge_info(src, dst)` returns all of it, and so do the MCP tools `callers_of` and `callees_of`.

| Evidence | How the call was bound |
| --- | --- |
| `same_file` | A function or class defined at the top level of the caller's own file |
| `import` | `from m import f`, then `f()`, relative imports included; `import *` only through a literal `__all__`. In JS/TS, a named or default import |
| `import_alias` | `from m import f as g`, then `g()`. In JS/TS, `import { f as g }` |
| `module_attr` | `import m`, `import a.b as c` or `from pkg import mod`, then `m.f()`. In JS/TS, `import * as m` or `require` |
| `class_attr` | `Cls.m()` where `Cls` is itself bound: the method on the class, else on the first direct base that defines it |
| `self_method` | `self.m()` or `cls.m()` (`this.m()` in JS/TS), where `self` or `cls` is the method's own first parameter: the method on the caller's class, else on the first direct base that defines it |
| `constructor` | `Cls()`: `Cls.__init__` (JS/TS: `constructor`) when the class defines one, else the class |
| `reexport` | Reached through up to three re-exports: a module (a package's `__init__.py`, typically) that imports the name, or JS/TS `export ... from` |
| `instance` | `obj.m()` where `obj` is bound once, as `obj = Cls()` or `obj: Cls` in the calling function (or `obj = Cls()` at module level, unless the function rebinds it), and `Cls` is bound: the method on the class, else on the first direct base that defines it |

Names are looked up scope by scope, as Python does: a nested function, lambda or class never changes what a name means outside it, though its calls count as the enclosing symbol's.

**What never binds:**
- a name that is a parameter of the calling function, or is assigned inside it;
- an unknown receiver (`obj.get()`): no edge, and no fan-out to the same-named methods of the file;
- a receiver bound more than once (a loop, if/else, a later assignment): its class is not known;
- a name imported from two different modules, or imported and also defined (try/except alternatives), in the caller's module or in the module it is imported from;
- an overridden method's base version, and grandparent methods and `super().m()`;
- a relative import that climbs above the indexed folder.

These calls are kept per file in `graph.unresolved`, as (caller, name, line, why), so a name-only search can still list them. Calls to builtins and to outside packages are not kept.

**Python source roots.** A module name resolves against every root:
- the indexed folder;
- the parent of every top-level package (a folder with `__init__.py` whose parent has none);
- `src/`;
- any literal `pythonpath`, `package-dir` or `where` setting in `pyproject.toml`, `setup.cfg` or `pytest.ini`;
- last, for a file in a folder without `__init__.py`, that folder itself: the folder a script runs from, which Python puts first on `sys.path` (pytest does the same for such test folders).

A name that maps to two different files is skipped and counted in `graph.resolution`, never guessed, and so is every name under it. So is a script-folder module that another root would resolve to a different file. A variable that a package's `__init__.py` assigns shadows the submodule of the same name.

**JS/TS** binds through relative imports only, with extensions and index files; a TypeScript import of `./x.js` also finds `x.ts`. tsconfig `paths` and `baseUrl`, and workspace packages, are not followed; "Not analysed" says so for a repository that uses them. A call on a call result (`a.b().c()`, `user?.save()`) is not a plain call. The JS/TS parser has no scope analysis, so a local name that shadows a function of the same file can link to that function.

## Fingerprints

`track.fingerprint`, and `track.snapshot` for any commit, record for every symbol:
- `sig_hash`: the captured signature, also kept as `signature`. That is every parameter's name, kind, default and annotation, the return annotation, the decorators and whether it is async; for a class, its decorators and bases. All of it is `ast.unparse` text, so formatting never matters.
- `body_hash`: the normalised syntax tree of the body, without positions and without the docstring. Reformatting, comment edits and docstring edits never change it. A class's body hash leaves out its methods, which have their own.
- per file, `module_hash`: the code outside any definition.

`track.diff` reports:
- symbols added and removed;
- symbols moved: the same qualified name and body hash, now in another file. Renames are not paired, so a rename stays a removal plus an addition;
- symbols whose body, signature or both changed;
- files whose module-level code changed.

`track.file_fingerprint` and `track.fingerprint_text` fingerprint one file, with no graph.

**Duplicate definitions get unique ids.** A property's accessors are `C.x`, `C.x.setter` and `C.x.deleter`. Other duplicates (try/except or if/else alternatives, `@overload` stubs) keep the plain id for the first and get `#2`, `#3` in source order. Symbols with `@overload` stubs are marked `overloaded`. Because the numbers follow source order, removing one duplicate renumbers the later ones; the structural check does not count that as a removal while the name still has a definition in the file.

**JS/TS symbols are hashed as normalised text,** with comments and whitespace removed and strings kept. A reformat that changes tokens can show as a change, and "Not analysed" says so.

## Caches

Every cache key and cache file name includes `ENGINE_SCHEMA` (in `models.py`), so an entry that another engine version wrote is never served. It goes up whenever parse output, fingerprints or edges change.

## Git

- **Ownership.** git's own safe.directory check stays on for every path you give. When git refuses a repository that another user owns, the engine raises `UntrustedRepositoryError` with the command to run if you trust the folder, `git config --global --add safe.directory '<path>'` (the path in single quotes, so pasting it runs nothing). It never runs that for you. Only the engine's own clones, in its data folder, skip the check: git is pointed at each clone's own `.git` and never searches a parent folder.
- **No programs from a repository's config.** Every git command runs with `-c log.showSignature=false -c core.fsmonitor=false`. A command that diffs must add `--no-ext-diff --no-textconv`; `--textconv` and `--filters` are never used.
- **No network.** Only the engine's own clone and fetch may use a transport. Every other command refuses all of them and never fetches a missing object on demand, so a partial clone's settings cannot start a program such as `core.sshCommand`.
