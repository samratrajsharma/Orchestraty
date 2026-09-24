# Engine

Orchestraty's effect engine. It ingests a repository, parses it and builds the code graph
that the analyses below read from. Parsing uses Python's `ast` for Python, heuristics for
JavaScript/TypeScript, and tree-sitter for other languages when the `treesitter` extra is
installed. The core needs only the standard library.

| Module | What it does |
| --- | --- |
| `ingest` | Opens a local folder or clones a repository safely, and lists its files |
| `parsing`, `treesitter_parser` | Symbols, imports, calls and complexity per file |
| `graph` | The code graph: files and symbols, with contains, imports, calls and method_of edges |
| `pipeline` | `build_index()`: ingest, parse and build the graph, with an HMAC-signed cache |
| `impact` | Blast radius of a symbol or a file |
| `insights` | Entry points, hub files, routes and data models |
| `techdebt` | Import cycles, complexity hotspots, dead code and near-duplicates |
| `coverage` | Which symbols the tests reference |
| `config_map` | The configuration surface |
| `diagram` | Architecture map and focused views, as Mermaid or Graphviz |
| `track` | Structural snapshots of commits and the differences between them |
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
| `ORCH_USE_CACHE` | `1` | Set to `0` to turn the cache off |
