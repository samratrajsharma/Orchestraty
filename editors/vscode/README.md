# Orchestraty for VS Code and Cursor

A status bar item and a panel showing the other coding-agent sessions on this workspace's repository, and how their changes overlap, whatever agents run them. Orchestraty's coordinator works all of this out; the extension only shows it, and tells you of a strong overlap in a file you have open. It detects nothing itself.

Built and unit-tested; not yet installed in VS Code or Cursor, nor tried against live sessions.

## Install

It is not on a marketplace. CI builds `orchestraty.vsix` for every push (the `editor extension` job's `vsix` artifact), or build it yourself (below). Then:

- VS Code: `code --install-extension orchestraty.vsix`, or **Extensions** › **...** › **Install from VSIX...**;
- Cursor: `cursor --install-extension orchestraty.vsix`, or the same menu.

It needs VS Code 1.90 or later (or a Cursor built on it), and Orchestraty installed with `orch init` run in the repository, so that Orchestraty watches it. In a workspace whose repository Orchestraty does not watch, it shows nothing.

## What it shows

- **The status bar item:** how many other live sessions work on the repository (those in another worktree), and its strong overlaps. Clicking it opens the panel.
- **The panel** (Orchestraty, in the panel area):
  - the live sessions, each with its branch, agent, worktree and last event;
  - the overlaps, grouped by strength (strong, medium, weak); choosing one opens its file at the line it is about;
  - the claims and declarations that hold, when there are any.
- **Notifications:** one for each strong overlap that affects a file open in the editor, once per overlap (again only if it goes away and comes back). Nothing for medium or weak overlaps.
- **Commands** (Command Palette):
  - `Orchestraty: Show status` opens the panel;
  - `Orchestraty: Steer on`, `Steer off`, `Wake on`, `Wake off` run `orch config steer|wake on|off`;
  - `Orchestraty: Open file at overlap` picks an overlap and opens its file;
  - `Orchestraty: Refresh` reads the status again.

## What it reads and runs

- **When it runs:** VS Code starts it once a window has finished starting. It finds the git repository of each workspace folder, and shows itself only when Orchestraty watches one of them (`status.json` lists it). With no workspace folder in a git repository it reads nothing; with no state folder it runs nothing.
- **Reads** Orchestraty's state folder only, found as `orch` finds it (`ORCH_HOME`; else `%USERPROFILE%\.orchestraty` on Windows; else `$XDG_STATE_HOME/orchestraty`, by default `~/.local/state/orchestraty`):
  - `status.json`, which the coordinator keeps current. It is read every 3 seconds while the window has the focus, and every 30 seconds otherwise, and only parsed when it changed.
  - `hooks/launch.txt`, the Python and import path `orch init` recorded, to run `orch` with.
- **Finds the repository** of each workspace folder from git's own files (the nearest `.git`), without running git.
- **Runs** only Orchestraty's command line, from the state folder and without a console window: `orch status --json` while `status.json` is missing (at most every 30 seconds), and `orch config` for Steer and Wake.
- **Writes nothing** itself: not to the repository, and not to Orchestraty's state folder (Steer and Wake change Orchestraty's `config.json` through `orch config`, as at the command line). Which overlaps it has already notified you of is kept in VS Code's own storage for the workspace.
- **No network requests and no telemetry.**

## Build from source

Node 22 or later (the packaging tool needs it):

```bash
cd editors/vscode
npm ci              # the pinned dev dependencies, from the lockfile
npm test            # compile, then the unit tests with node --test
npm run package     # orchestraty.vsix
```

The unit tests cover the layer that turns the status into what is shown (`src/view.ts`): the status bar's counts, the panel's tree, where an overlap's file is, and which overlaps to notify of. They run against a `status.json` the coordinator wrote for two scripted sessions (`test/fixtures/status.json`). They also check that the state folder is found as the Python package finds it, and that the repository is found as git finds it. There is no test inside a running VS Code.
