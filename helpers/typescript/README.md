# orchestraty-helper

The two-call helper for agents you build yourself in TypeScript or JavaScript: Orchestraty's hook events from the agent's own loop, one call before it writes a file, one after, and one at the end of a turn. It is the same as the Python helper (`orchestraty.helper`), with async calls. Not published: build it from this folder.

```ts
import { Orchestraty } from "orchestraty-helper";

const orch = new Orchestraty({ agent: "mybot", cwd: worktree }); // a new session id unless sessionId is given
await orch.start();                                  // SessionStart
const before = await orch.beforeWrite("billing.py"); // the note waiting for this file, or null
// ... the agent writes billing.py ...
const next = await orch.afterWrite("billing.py");    // records the edit; the "next" note, or null
const left = await orch.endTurn();                   // callers left behind, or null
await orch.end();                                    // SessionEnd
```

Each call runs the hook script `orch init` installed, the way an agent runs a hook: `python -S -I <state>/hooks/hook.py helper-<name> <event>`, with the interpreter `orch init` recorded in `<state>/hooks/launch.txt` and the event's JSON on stdin. The state folder is found as `orch` finds it: `ORCH_HOME`, else `%USERPROFILE%\.orchestraty` on Windows, else `$XDG_STATE_HOME/orchestraty` (by default `~/.local/state/orchestraty`). A call resolves to the note's text, or null; it never rejects.

You need Node 18 or later, and Orchestraty set up: run `orch init` once, in the repository the agent works on (it installs the hook script and watches that repository).

```bash
npm ci          # the dev dependencies (the compiler and Node's types); there are no runtime ones
npm run build   # dist/src/index.js and its types
npm test        # builds, then runs the tests with node --test
```

The tests run the real hook script from this repository, in a state folder of their own, and use the Python package to plant notes and to compare the lines this helper writes with the Python helper's. They need Python 3.10 or later on the PATH (`python3`, or `python` on Windows; `ORCH_TEST_PYTHON` names another).
