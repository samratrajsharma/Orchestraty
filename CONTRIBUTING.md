# Contributing

Thank you for helping. Read [the rules](docs/rules.md) first: they decide what Orchestraty will and will not do, and a change that breaks one of them will not be merged, however good it is.

## Setting up

You need Python 3.10 or later, git, and pytest 8 or 9.

```bash
pip install -e .                       # the orch command and the engine, from where they are
python -m pip install "pytest>=8,<10"
(cd orchestraty && python -m pytest)
(cd engine && python -m pytest -p no:cacheprovider)
(cd spikes && python -m pytest)
```

Set `PYTHONDONTWRITEBYTECODE=1` so the tests leave no bytecode caches. The TypeScript helper (`helpers/typescript`) and the editor extension (`editors/vscode`) are tested with Node: `npm ci && npm test` in each folder.

## The code

- **Python 3.10 or later, standard library only at run time.** Add no runtime dependency. Optional extras are imported lazily, so a missing one turns off a feature instead of breaking an import.
- **Windows, macOS and Linux.** Build paths with `os.path`, use no call that exists only on POSIX, and start subprocesses without a console window on Windows (`creationflags=CREATE_NO_WINDOW`, the way the coordinator does).
- **The hook stays a quick append.** The hook script (`orchestraty/orchestraty/capture/hook.py`) imports only built-in modules, never parses the payload, and must not get slower: `tests/test_agent_hooks.py` measures it. Anything heavy belongs in the background process.
- **Facts, no model; templates, not text.** Nothing calls a language model. A note is a template over identifiers, cleaned first; another session's words never reach it.
- **Imports go one way.** The main package may import the engine (`orch_engine`); the engine never imports the main package, and neither imports from `spikes/`, which is throwaway experiment code. `tests/test_layout.py` checks it.
- **The record's schema** changes only with a migration and a schema-version bump.

## Tests

- pytest, in each package's `tests/` folder. Fixtures are synthetic: no real transcript, prompt or user data is ever committed.
- **Never touch the real user's state, settings or repositories.** The fixtures in `orchestraty/tests/conftest.py` give every test its own state folder and home (`ORCH_HOME`, `HOME`, `USERPROFILE`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`) and keep the background process from starting; the `git` fixture runs git with an empty user and system config, in temporary repositories. Use them; never point a test at `~`, the real agent settings or a real repository.
- **Leave nothing behind.** No `__pycache__`, no `.pytest_cache`, no output files in the working copy. CI fails any job whose tests leave a file behind.
- A change that affects behaviour comes with a test that fails without it.

## Adding an agent adapter

An adapter tells Orchestraty how one agent's hooks look. The three that exist, `orchestraty/orchestraty/adapters/claude.py`, `codex.py` and `antigravity.py`, are the models to follow.

1. **The adapter module,** `orchestraty/orchestraty/adapters/<agent>.py`, declares:
   - `NAME` (the short name, used on the command line and in the spool) and `TITLE` (the agent's own name);
   - `EVENTS`: its hook events, mapped to the common ones (Claude Code's names, which the rest of Orchestraty uses);
   - `READS`: where the session id, folder, tool, call id and edited file sit in its payloads; `EDIT_TOOLS`: its tools that change a file, and the field that names it;
   - `SLOTS` and `HEARS`: which events can hand a note back, and which kinds of note (before an edit, with the next step, at the end of a turn, woken); `CANNOT`: why it cannot take the others; `ASKS`: whether an edit can be made to wait for its user's answer; `QUIET`: what it must be answered when there is no note;
   - `output()`: the exact bytes it accepts for a note at each event;
   - its hooks file: `config_path()`, `hook_block()`, `merged()` (ours added, others kept) and `without_ours()` (only ours taken out).
2. **Register it** in `adapters/__init__.py` (`AGENTS`), and add it to the `--agent` choices of `orch init` and `orch mcp --config` in `cli.py`.
3. **Teach the hook script.** The hook imports nothing of ours, to stay fast, so it keeps its own copy of what it reads and prints (`capture/hook.py`); `tests/test_adapters.py` checks that the two agree.
4. **Test it with synthetic payloads** in the agent's own shape: the spool line and the hook's time (`tests/test_agent_hooks.py`), the notes in its own form (`tests/test_agent_delivery.py`), and installing, restoring and uninstalling its hooks with other entries left alone (`tests/test_agent_install.py`, `tests/test_uninstall.py`).
5. **List it** in [`docs/agents.json`](docs/agents.json) with the result `not run` (or `pending` once its capture test is planned), run `python scripts/agents_table.py` to rewrite the README's table, and describe what it can be told in the main package's README. Until its capture test passes, it is "built; not yet tested with the real agent", everywhere.

### The capture test

An agent is called **supported** only after its capture test with the real agent passes. The test runs the real agent, at a pinned version, in a profile of its own (never your everyday one), with Orchestraty's hooks installed:

- scripted sessions of every kind: several at once, in separate worktrees, with subagents, a session that is killed, and tool calls that are denied;
- the hook's lines are compared with the agent's own record of each session: every expected event must be there exactly once, none lost and none extra, and the hook must print nothing it should not and never fail the agent;
- the time the hook adds over a command that does nothing is measured on the same machine.

The harness for it lives in [`spikes/capture/`](spikes/capture/README.md) (`capture_count.py` compares the lines with the agent's records, `capture_latency.py` measures the time), with set-ups for Claude Code, Codex and Antigravity. A new agent needs a set-up of its own and a reader for its own session records. When the test passes, record the agent's version, the platform and the date in `docs/agents.json`, with the result `pass`, and regenerate the table: only then does it say "Supported".

## Commits

- **One commit per change,** with the files added by their paths, never `git add -A` or `git add .`.
- **[Conventional Commits](https://www.conventionalcommits.org/)** with a scope: `feat(orchestraty): ...`, `fix(engine): ...`, `test(spikes): ...`, `docs: ...`, `ci: ...`. Say what changed and why, in plain words; no ticket or test numbers.
- **Through a branch.** Work on a short-lived branch; it reaches `main` only after CI is green on it, fast-forward only, so history stays linear.
- Name only the agents Orchestraty connects to, where the code supports them; don't name other products in the repository.
- Anything that shapes the design is recorded as an ADR in [`docs/decisions/`](docs/decisions/), from the [template](docs/decisions/template.md).
