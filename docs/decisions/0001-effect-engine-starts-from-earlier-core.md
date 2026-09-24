# ADR-0001: The effect engine starts from the earlier product's core

- Status: Accepted
- Date: 2026-09-24
- Deciders: Samrat Raj Sharma

## Context

Orchestraty needs an effect engine: code that parses a repository, builds a graph of files and symbols, and works out what a change does to it.

An earlier product by the same author, Legend, already had such an engine. It was written in Python, and its core used only the standard library. Its test suite passed: 233 tests at tag `legend/final`, of which the 141 that cover the modules kept here still pass. Among other things it included:

- a conservative Python call resolver, which leaves an ambiguous call unresolved rather than guessing;
- a hardened git ingest;
- structural snapshots and diffs;
- blast-radius analysis;
- a structural check gate;
- an AGENTS.md generator;
- the wiring for an MCP server.

The rest of that product was a web app, a website, a Streamlit app, docs and packaging. It also had modules for question answering over code (RAG), code explanations, calls to language models, engineering memory, learning, media, research and portfolio features. None of these are part of the effect engine.

## Decision

The engine starts from Legend's core. Legend's history comes with it, so `git log --follow` and `git blame` reach back to the original commits.

- Merge `496b86b` brought in Legend's full history under `legend/`, keeping the original commit hashes. Tag `legend/final` marks that tree before any cleanup.
- Commit `7ed6ef0` removed Legend's apps, website, docs, packaging and CI.
- Commit `f2004db` moved the engine to `engine/` and renamed the package from `legend` to `orch_engine`. The project name is now `orch-engine`.
- Commit `f701a20` removed the modules that are not part of the engine:
  - `chunking`, `index`, `retrieval`, `llm` and `providers`;
  - `engmemory`, `eval_harness`, `research`, `teach` and `progress`;
  - `media`, `portfolio` and `export_site`;
  - the Streamlit app and its evaluation scripts.
- Commit `0415733` removed the Legend name from the engine:
  - settings are now `ORCH_*`;
  - the default cache folder is `.orch_cache`, and the `check`, `context` and MCP entry points use `~/.orchestraty/cache`;
  - the MCP server registers as `orchestraty`.

These modules remain:

- `ingest`, `parsing`, `treesitter_parser` and `graph`;
- `pipeline`, `impact`, `insights`, `techdebt` and `coverage`;
- `config_map`, `diagram`, `track`, `check`, `context` and `mcp_server`;
- the support modules `config` and `models`.

Legend's PyPI package, `legend-lens`, and its public MCP distribution are retired. Orchestraty publishes no PyPI package for now.

## Alternatives considered

- **Write a new engine from scratch.** This would throw away a tested resolver and git ingest, and Orchestraty's first months would go into rebuilding them.
- **Keep Legend as a separate package that Orchestraty depends on.** That means two projects to version, release and support, and a public package to maintain. Keeping one repository is simpler while the engine changes quickly.
- **Adopt an existing open-source code-graph engine.** This is still open. A planned comparison will measure this engine against existing ones, and may replace it. Starting from this core gives that comparison a working baseline.

## Consequences

- There are tests and history from the first commit.
- The engine brings its known defects with it: some edges resolve wrongly, some ids are OS-specific, and some analyses create side effects in the repository being analysed. These are tracked as separate work items. Each one has to be fixed before anything relies on the part it affects.
- Legend was published under the MIT License. Its LICENSE file was removed along with the rest of the product, so this repository has no license file for now. Choosing one is a separate decision. Copies of `legend-lens` that were already published stay under MIT.
- Anyone who installed `legend-lens` gets no further updates from this repository.
