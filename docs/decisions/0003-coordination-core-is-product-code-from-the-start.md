# ADR-0003: The coordination core is product code from the start, in `orchestraty/`

- Status: Accepted
- Date: 2026-10-19
- Deciders: Samrat Raj Sharma

## Context

Orchestraty keeps parallel coding-agent sessions aware of each other. Its core is a coordinator that reads hook events, keeps a live map of what each session is changing, runs the effect engine and the overlap rules, and writes notes for the agents.

The experiments that validate the approach need that core to exist and work: an experiment that measures whether agents avoid breaking each other's work when told can only run against a working coordinator. ADR-0002 puts experiment code in `spikes/` and deletes it when the experiments end. Building the coordinator there would mean building it twice.

## Decision

The coordination core is built as product code in a new top-level package, `orchestraty/` (Python package `orchestraty`, command `orch`), from the start.

- It follows the engine's rules: Python 3.10 or later, standard library only in the core, full tests, no files left behind by tests.
- It may import the engine (`orch_engine`). It never imports from `spikes/`, and the engine never imports it.
- Measurement harnesses, and code that runs on other people's machines during the experiments, stay in `spikes/` under ADR-0002.
- Pieces proven in `spikes/` (such as the capture shim) are rebuilt here, not imported.

## Alternatives considered

- **Build the core in `spikes/` and rebuild it after the experiments.** Keeps the experiment rule pure, but costs weeks of rebuilding exactly when the experiments succeed.
- **Put the core inside the engine package.** One package fewer, but it mixes analysis with process management, hooks and delivery, and makes the engine depend on agent-specific code.
- **Wait until the experiments end.** Not possible: the experiments need a working core.

## Consequences

- Some work is lost if the experiments show the approach does not work.
- The package's tests need their own CI job, alongside the engine and experiment jobs.
- `tests/test_layout.py` in the package checks the import rules.
