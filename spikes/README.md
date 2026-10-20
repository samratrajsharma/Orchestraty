# Spikes

Status: throwaway (Phase 0). Not part of the product.

This folder holds short-lived experiments. Each one answers one question before any product code is written. When the experiments are finished, the whole folder is deleted in one commit. Anything worth keeping is then rebuilt properly in its long-term home, never copied across.

Each experiment has its own folder, and that folder's README says what it is for and how to run it. `_shared/` holds code that more than one experiment uses.

## Rules

1. **Neutral names.** A folder name says what the code does. Test plans, pass marks and results are kept outside this repository.
2. **Every folder's README starts with the same status line:** "Status: throwaway (Phase 0). Not part of the product." It then says how to run the code.
3. **Imports go one way.**
   - A spike may import the engine package (`orch_engine`), the product package (`orchestraty`) and `spikes/_shared`.
   - A spike never imports another spike.
   - Nothing under `engine/` imports from `spikes/`.
4. **No pass marks in code.** Spikes print measurements. Whether a result is good enough is decided elsewhere.
5. **Outputs stay out of git.** By default, results are written outside the repository, or to an `out/` or `data/` folder, which `.gitignore` excludes. Real data from anyone's machine is never committed. Test fixtures are synthetic.
6. **Every spike has tests,** mostly for safety properties: for example, that the scanner never opens a session file and never writes anywhere except its output file.
7. **Deleted at the end.** When the experiments are done, `spikes/` is removed in one commit. Nothing moves from here into product code by copy and paste.
8. **Unique file names.** All spike tests run in one pytest session, so no two spikes may have a Python file with the same name. `conftest.py` is the one exception. Prefix names with the spike's name, as `scan/scan_collect.py` does.

`spikes/test_rules.py` checks rules 2, 3 and 8.

## Run the tests

You need Python 3.9 or later and pytest. The overlap history needs 3.10 or later, as the engine does; below that its tests are skipped. From the repository root:

```
cd spikes
python -m pytest
```

To leave no caches in the working copy, set `PYTHONDONTWRITEBYTECODE=1` before running. `spikes/pytest.ini` already turns off pytest's cache folder.
