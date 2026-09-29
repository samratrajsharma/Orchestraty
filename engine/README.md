# Engine

Orchestraty's effect engine. It ingests a repository, parses it and builds the code graph
that the analyses below read from. Parsing uses Python's `ast` for Python, heuristics for
JavaScript/TypeScript, and tree-sitter for other languages when the `treesitter` extra is
installed. The core needs only the standard library.

| Module | What it does |
| --- | --- |
| `ingest` | Opens a local folder or clones a repository safely, and lists its files; reads any commit from git's object store |
| `parsing`, `treesitter_parser` | Symbols, imports, calls and complexity per file |
| `graph` | The code graph: files and symbols, with contains, imports, calls and method_of edges |
| `pipeline` | `build_index()`: ingest, parse and build the graph, with an HMAC-signed cache. `build_index_at()` does the same for any commit, straight from git's objects |
| `impact` | Blast radius of a symbol or a file |
| `insights` | Entry points, hub files, routes and data models |
| `techdebt` | Import cycles, complexity hotspots, dead code and near-duplicates |
| `coverage` | Which symbols the tests reference |
| `config_map` | The configuration surface |
| `diagram` | Architecture map and focused views, as Mermaid or Graphviz |
| `track` | Structural snapshots of commits (read from git's objects) and the differences between them |
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
- **A commit is read, never checked out.** `build_index_at(repo, ref)` and `track.snapshot` list the commit with `git ls-tree` and read its files with `git cat-file --batch`, at the repository's top folder. No worktree or archive is made and nothing is written into the repository, so `export-ignore` attributes don't hide files. The ref is resolved to its full SHA, which alone keys the caches.
- **Not analysed.** `RepoIndex.not_analysed` lists what an index left out, as `{"path", "reason"}`: skipped folders (`env/`, `build/`, `node_modules/` and the others in `SKIP_DIRS`, at any depth), symlinks, submodules, and files over 2 MB when read from a commit. The MCP `overview` tool shows it.
- **Git output is read as UTF-8,** whatever the locale, so a non-ASCII author, message or folder name works on Windows without `PYTHONUTF8`.
