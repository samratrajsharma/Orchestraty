# ADR-0002: Throwaway experiment code lives in `spikes/`

- Status: Accepted; carried out: `spikes/` was deleted on 2026-10-25, when the experiments ended (rule 7)
- Date: 2026-09-24
- Deciders: Samrat Raj Sharma

## Context

Before any product code is written, a short series of experiments has to be built. These include a read-only scanner, hook prototypes with a latency harness, a brief generator for past pull requests, a backfill report, and comparison harnesses. Most of this code exists only to answer one question and is then thrown away.

The effect engine (`engine/`) is meant to last, and it gets fixes during the same period. If throwaway code and lasting code are mixed, throwaway code can end up shipping.

The build works like this:
- changes are prepared and left uncommitted;
- the owner reviews and commits them;
- everything goes through the same CI.

The experiment code has to be reviewable and testable in that same flow.

## Decision

Throwaway experiment code lives in `spikes/` on `main`, one folder per experiment, with these rules:

1. **Names.** Folder names say what the code does. Test plans, pass marks and results stay outside the repository.
2. **README.** Every folder's README starts with "Status: throwaway (Phase 0). Not part of the product."
3. **Imports.**
   - A spike may import `orch_engine` and `spikes/_shared`.
   - Spikes never import each other.
   - `engine/` never imports from `spikes/`.
4. **No pass marks in code.** Spikes print measurements.
5. **Outputs.** Outputs are written outside the repository or to `out/` and `data/` folders, which are gitignored. Fixtures are synthetic.
6. **Tests.** Every spike has tests, mostly for safety properties. They run in a separate CI job.
7. **Deletion.** When the experiments end, `spikes/` is deleted in one commit. Anything that should last is rebuilt in its proper home.
8. **Unique file names.** Python file names are unique across spikes (except `conftest.py`), because all spike tests run in one pytest session.
   Rule 8 was added after the first seven rules were approved, and was accepted on 27 September 2026.

`spikes/test_rules.py` checks rules 2, 3 and 8.

## Alternatives considered

- **One branch per experiment.** Keeps `main` clean, but reviewers and CI would see less, and each branch would later have to be merged or dropped.
- **A separate private repository.** Clean separation, but it splits review and history, and the engine would have to be vendored or added as a submodule.
- **A subpackage inside the engine.** Nothing new at the top level, but throwaway code would sit inside the package that ships.
- **A local, untracked folder.** Nothing lands in git, but nothing gets reviewed, versioned or tested in CI either.

## Consequences

- Experiment code stays in `main`'s history after the folder is deleted. It must therefore follow the same rules as everything else in the repository: neutral wording, synthetic fixtures, no data.
- CI runs separate jobs for spike tests, on each operating system participants use, and on Linux with Python 3.9 to 3.12, because the scanner has to run on older Pythons. The 3.9 job is pinned to `ubuntu-24.04`, because no 3.9 build exists for the Ubuntu 26.04 image that `ubuntu-latest` moves to from October 2026.
- The rules test fails as soon as a new folder lacks the status line; a spike imports another spike's folder or modules, the `spikes` package, or reaches out of its folder with a relative import; the engine imports a spike; or two spikes share a Python file name (rule 8).
