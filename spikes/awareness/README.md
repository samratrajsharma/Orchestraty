# Awareness

Status: throwaway (Phase 0). Not part of the product.

Runs task pairs fixed in advance: two headless Claude Code sessions at once, in two worktrees of a scratch clone, with and without Orchestraty. It then checks each branch alone, merges them, and runs the tests and a combination check on the merged code. The task pairs and the measures live outside this repository.

## Run it

```
python aware_run.py --pairs <folder of pair files> --out <results folder> [--repeats 2] [--only ID,...]
python aware_score.py --out <results folder>
```

- **Sessions:** the pinned Claude Code in the isolated profile (`%USERPROFILE%\orch-phase0\profiles\claude`), one pinned model, a 15-minute limit each, edits allowed, and only `python -m pytest` as a shell command.
- **Environment:** each session runs without `PYTHONPATH` and without the runner's `GIT_*` or `ORCH_*` variables, so its own commands see only its worktree.
- **With Orchestraty:** `orch init` installs the hooks into the isolated profile for that run only, with a state folder of its own, and `orch restore` takes them out afterwards.
- **Resuming:** each run is saved as it finishes (`<pair>/<condition>-<repeat>/result.json`), and finished runs are skipped. A usage limit stops the series, or waits and retries with `--wait-on-limit`.

## Files

| File | What it is |
| --- | --- |
| `aware_run.py` | The runner. |
| `aware_score.py` | Breaks, breaks avoided with a bootstrap interval, false notes, time to awareness and token cost, from the saved runs. |
| `test_aware.py` | Tests of the run order and the scoring, on synthetic results. |
