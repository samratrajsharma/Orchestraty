# Contributing

Thank you for helping. Read [the rules](docs/rules.md) first: they decide what Orchestraty will and will not do, and a change that breaks one of them will not be merged, however good it is.

Everyone who takes part follows the [code of conduct](CODE_OF_CONDUCT.md). For a bug or an idea, open an issue: its templates ask for what helps. A security or conduct problem goes privately to the Security tab, as [SECURITY.md](SECURITY.md) and the code of conduct say. A pull request's template lists the checks below.

## Setting up

You need Python 3.10 or later, git, and pytest 8 or 9.

```bash
pip install -e .                       # the orch command and the engine, from where they are
python -m pip install "pytest>=8,<10"
(cd orchestraty && python -m pytest)
(cd engine && python -m pytest -p no:cacheprovider)
```

Set `PYTHONDONTWRITEBYTECODE=1` so the tests leave no bytecode caches. On Windows, run the same from PowerShell, one folder at a time. The TypeScript helper (`helpers/typescript`) and the editor extension (`editors/vscode`) are tested with Node: `npm ci && npm test` in each folder.

From the repository's top folder, run `orch` rather than `python -m orchestraty`: there, Python finds the folder `orchestraty/` before the package. `pip install -e ".[treesitter]"` adds the optional extra that lets the engine parse more languages (Go, Java, Rust, C#, Ruby, PHP, C and C++); the tests don't need it.

## The repository

```text
.
├── orchestraty/            The main package: the orch command, hooks, coordinator, live map, rules, notes, adapters
├── engine/                 The contract engine (orch_engine): parsing, code graph, contracts
├── plugins/claude-code/    The Claude Code plugin: the hooks, the mod, the MCP server and a skill
├── editors/vscode/         The VS Code and Cursor extension
├── helpers/typescript/     The two-call helper for homemade agents, in TypeScript
├── examples/               A homemade agent that uses the Python helper
├── scripts/                The smoke test of the built wheel, install-to-first-note timing, the plugin's build, the agents table
├── docs/                   The rules, the agents' test results, and the decision records
└── pyproject.toml          One distribution, orchestraty: both packages in one wheel
```

Everything runs on the user's machine. An agent's hook runs a small script that appends one line to a file and exits; the one thing it waits for is a note already written for it. One background process per user, started by the first hook that needs it, reads those lines and git, keeps a live map of what each session has changed since its base commit, runs the [contract engine](engine/) and the overlap rules, and writes each note in advance for the session that must hear it. `orch status`, the Claude Code mod, the editor extension and the MCP tools all show what that process found; none of them detects anything itself.

## The code

- **Python 3.10 or later, standard library only at run time.** Add no runtime dependency. Optional extras are imported lazily, so a missing one turns off a feature instead of breaking an import.
- **Windows, macOS and Linux.** Build paths with `os.path`, use no call that exists only on POSIX, and start subprocesses without a console window on Windows (`creationflags=CREATE_NO_WINDOW`, the way the coordinator does).
- **The hook stays a quick append.** The hook script (`orchestraty/orchestraty/capture/hook.py`) imports only built-in modules, never parses the payload, and must not get slower: `tests/test_agent_hooks.py` measures it. Anything heavy belongs in the background process.
- **Facts, no model; templates, not text.** Nothing calls a language model. A note is a template over identifiers, cleaned first; another session's words never reach it.
- **Imports go one way.** The main package may import the engine (`orch_engine`); the engine never imports the main package. `tests/test_layout.py` checks it.
- **The record's schema** changes only with a migration and a schema-version bump.

## Tests

- pytest, in each package's `tests/` folder. Fixtures are synthetic: no real transcript, prompt or user data is ever committed.
- **Never touch the real user's state, settings or repositories.** The fixtures in `orchestraty/tests/conftest.py` give every test its own state folder and home (`ORCH_HOME`, `HOME`, `USERPROFILE`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`) and keep the background process from starting; the `git` fixture runs git with an empty user and system config, in temporary repositories. Use them; never point a test at `~`, the real agent settings or a real repository.
- **Leave nothing behind.** No `__pycache__`, no `.pytest_cache`, no output files in the working copy. CI fails any job whose tests leave a file behind.
- A change that affects behaviour comes with a test that fails without it.
- **The labelled sample of team notes** (`orchestraty/tests/test_team_sample.py`) plays alice's changes on pushed branches against bob's work on his machine (`tests/team_sample/catalog.py`, on `tests/fixtures/team_shop`) and labels every finding with an oracle written before its first run (`tests/team_sample/oracle.py`): real, harmless or false. It fails when a finding is not in the oracle, when a strong one is labelled false, or when strict precision (real over all), recall or the list of misses gets worse than the figures pinned after the first run. `python -m pytest tests/test_team_sample.py -s` prints the table. Change a label only to correct the label, never to fit the code; a better figure is pinned in the same change.

### Continuous integration

GitHub counts the minutes CI uses (Windows twice, macOS ten times), so each run does only what it must.

| Run | Platforms and Python | When |
| --- | --- | --- |
| Quick | Linux 3.12, every test; Windows 3.12, every test but those marked `heavy` (slow tests that run many git commands, which take about ten times as long on the Windows runner) | Pushes to `main` and `phase-2`, and pull requests |
| Package and smoke | The sdist and the wheel built on Linux; the smoke test of that wheel on Windows and Linux 3.12 | Pushes to `main` and `phase-2`, and pull requests |
| Node | The TypeScript helper, the TypeScript contracts and the editor extension (its `.vsix` kept as an artifact), on Linux | Pushes to `main` and `phase-2`, and pull requests |
| Full | Adds Linux 3.10 and 3.11, the whole suite on Windows (the `heavy` tests too) and the Node jobs on Windows; macOS 3.12 and the smoke test on macOS only on manual runs and release tags | Weekly on Mondays (`main`), on manual runs, and on release tags (`v*`) |

A work branch gets its CI run through its pull request; a push to it alone runs nothing. A newer push to the same branch or pull request cancels the run it makes out of date. Before a push, run the tests on your own machine: `cd orchestraty && python -m pytest` (`-m "not heavy"` leaves out the slow ones while you work).

The smoke test (`scripts/smoke.py`) installs the built wheel in a clean virtual environment and runs `orch --version`, `orch init --print`, two scripted sessions through the installed hook script that reach a note, and `orch doctor`, with no real agent and no network. Then an ordinary day runs on the same wheel (`scripts/real_day.py`): one `orch init` from a folder that is no project, a repository with many worktrees and a folder without git, sessions shown in `orch status` within seconds, a caller left behind found in the folder, the dashboard's one address and a second `orch ui` opening it, and an upgrade that restarts the coordinator with no command run. Run both before a change that touches installing, upgrading or what a session sees: `python scripts/smoke.py dist/*.whl` and `python scripts/real_day.py dist/*.whl`. Every job fails if its tests leave any file behind.

### Releases

`.github/workflows/release.yml` runs on a tag `v<version>`, the version kept in `orchestraty/orchestraty/__init__.py`. It builds the sdist and the wheel, runs the smoke test and the ordinary day on Windows, macOS and Linux, and attaches the files to a draft GitHub release, which stays a draft until it is published by hand. Publishing to PyPI is a job of its own, off until it is turned on: it runs only when the workflow is started by hand on the tag with `publish-to-pypi` ticked, and uses trusted publishing, so no token is kept anywhere. `scripts/first_note_timing.py` times install to first note on a 10,000-file repository.

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

The maintainers run it with a harness kept outside this repository, which has set-ups for Claude Code, Codex and Antigravity; a new agent needs a set-up of its own and a reader for its own session records, so open an issue first. When the test passes, record the agent's version, the platform and the date in `docs/agents.json`, with the result `pass`, and regenerate the table: only then does it say "Supported".

## Commits

- **One commit per change,** with the files added by their paths, never `git add -A` or `git add .`.
- **[Conventional Commits](https://www.conventionalcommits.org/)** with a scope: `feat(orchestraty): ...`, `fix(engine): ...`, `test(orchestraty): ...`, `docs: ...`, `ci: ...`. Say what changed and why, in plain words; no ticket or test numbers.
- **Through a branch.** Work on a short-lived branch; it reaches `main` only after CI is green on it, fast-forward only, so history stays linear.
- Name only the agents Orchestraty connects to, where the code supports them; don't name other products in the repository.
- Anything that shapes the design is recorded as an ADR in [`docs/decisions/`](docs/decisions/), from the [template](docs/decisions/template.md).

## Decision records

| ADR | Decision |
| --- | --- |
| [0001](docs/decisions/0001-effect-engine-starts-from-earlier-core.md) | The effect engine starts from the core of an earlier product. |
| [0002](docs/decisions/0002-throwaway-spikes-live-in-spikes.md) | Throwaway experiment code lives in `spikes/`. |
| [0003](docs/decisions/0003-coordination-core-is-product-code-from-the-start.md) | The coordination core is product code from the start, in `orchestraty/`. |
