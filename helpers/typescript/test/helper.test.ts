// The TypeScript helper against the real hook script, in a state folder of its own, with the
// Python package (from this repository) planting notes and playing the Python helper.
import { after, before, test } from "node:test";
import * as assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { Orchestraty, agentName, noteText, stateRoot } from "../src/index.js";

const REPO = fileURLToPath(new URL("../../../../", import.meta.url));
const PYTHON = process.env.ORCH_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");
const PYTHONPATH = [path.join(REPO, "orchestraty"), path.join(REPO, "engine")].join(path.delimiter);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

let scratch = "";
let state = "";
let work = "";
let env: NodeJS.ProcessEnv = {};

/** Run Python code with this repository's packages, `args` in sys.argv[1:]; its stdout. */
function python(code: string, args: string[] = [], withEnv: NodeJS.ProcessEnv = env): string {
  const proc = spawnSync(PYTHON, ["-c", code, ...args], {
    env: { ...withEnv, PYTHONPATH, PYTHONDONTWRITEBYTECODE: "1" },
    encoding: "utf8",
    windowsHide: true,
  });
  assert.equal(proc.status, 0, proc.stderr);
  return proc.stdout;
}

/** A state folder with the hook script installed, as orch init leaves it. */
function installedState(name: string): string {
  const root = path.join(scratch, name);
  mkdirSync(path.join(root, "hooks"), { recursive: true });
  copyFileSync(path.join(REPO, "orchestraty", "orchestraty", "capture", "hook.py"), path.join(root, "hooks", "hook.py"));
  writeFileSync(path.join(root, "hooks", "launch.txt"), `${PYTHON}\n\n`);
  return root;
}

function spool(root: string, session: string): Array<Record<string, unknown>> {
  const file = path.join(root, "spool", "helper-mybot", `${session}.jsonl`);
  return existsSync(file) ? readFileSync(file, "utf8").trim().split("\n").map((line) => JSON.parse(line)) : [];
}

/** Write a note into a slot the way the coordinator does. */
function plant(root: string, session: string, kind: "edit" | "next" | "stop", text: string, file = ""): void {
  python(
    [
      "import sys",
      "from orchestraty.delivery import notes, slots",
      "from orchestraty.paths import State",
      "root, sid, kind, text, file = sys.argv[1:]",
      "state = State(root)",
      "slot = {'edit': lambda: slots.edit_slot(state, sid, file), 'next': lambda: slots.next_slot(state, sid),",
      "        'stop': lambda: slots.stop_slot(state, sid)}[kind]()",
      "out = {'edit': notes.pre_tool_output, 'next': notes.post_tool_output, 'stop': notes.stop_output}[kind](text)",
      "assert slots.write(slot, out)",
    ].join("\n"),
    [root, session, kind, text, file],
  );
}

before(() => {
  scratch = mkdtempSync(path.join(os.tmpdir(), "orch-helper-ts-"));
  state = installedState("state");
  // Windows allows no '"' in a file name; elsewhere the folder tests the escapes too.
  work = path.join(scratch, "w\u00e9rk (1)" + (process.platform === "win32" ? "" : ' "q"'));
  mkdirSync(work);
  env = { ...process.env, ORCH_HOME: state, ORCH_NO_COORDINATOR: "1" };
});

after(() => {
  rmSync(scratch, { recursive: true, force: true });
});

test("the state folder is found the way orch finds it", () => {
  const cases: NodeJS.ProcessEnv[] =
    process.platform === "win32"
      ? [{ ORCH_HOME: "C:\\o\\h" }, { USERPROFILE: "C:\\Users\\me" }, { ORCH_HOME: "~\\s", USERPROFILE: "C:\\Users\\me" },
         { ORCH_HOME: "relative", USERPROFILE: "C:\\Users\\me" }]
      : [{ ORCH_HOME: "/o/h" }, { ORCH_HOME: "relative", HOME: "/h" }, { ORCH_HOME: "~/s", HOME: "/h" },
         { XDG_STATE_HOME: "/xdg", HOME: "/h" }, { XDG_STATE_HOME: "not/absolute", HOME: "/h" }, { HOME: "/h" }];
  for (const given of cases) {
    const caseEnv: NodeJS.ProcessEnv = { ...env };
    for (const name of ["ORCH_HOME", "XDG_STATE_HOME", "HOME", "USERPROFILE"]) {
      delete caseEnv[name];
    }
    Object.assign(caseEnv, given);
    const theirs = python("from orchestraty import paths\nprint(paths.state_root())", [], caseEnv);
    assert.equal(stateRoot(caseEnv), theirs.trim(), JSON.stringify(given));
  }
  assert.equal(stateRoot({ USERPROFILE: "C:\\Users\\me" }, "win32"), "C:\\Users\\me\\.orchestraty");
  assert.equal(stateRoot({ HOME: "/h" }, "linux"), "/h/.local/state/orchestraty");
  assert.equal(stateRoot({ HOME: "/h", XDG_STATE_HOME: "/x" }, "darwin"), "/x/orchestraty");
});

test("a session reaches the spool as an agent's hook events", async () => {
  const orch = new Orchestraty({ agent: "mybot", cwd: work, sessionId: "S1", env });
  assert.equal(await orch.start(), undefined);
  assert.equal(await orch.beforeWrite("src/a.py"), null);
  assert.equal(await orch.afterWrite(path.join(work, "src", "a.py")), null);
  assert.equal(await orch.endTurn(), null);
  assert.equal(await orch.end(), undefined);
  const lines = spool(state, "S1");
  assert.deepEqual(lines.map((l) => l.event), ["SessionStart", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"]);
  for (const line of lines) {
    assert.deepEqual([line.agent, line.session_id, line.cwd], ["helper-mybot", "S1", work]);
  }
  const target = path.join(work, "src", "a.py");
  assert.deepEqual(lines.slice(1, 3).map((l) => [l.tool_name, l.file_path]), [["Write", target], ["Write", target]]);
  assert.deepEqual([lines[0].source, lines[3].stop_hook_active, lines[4].reason], ["startup", false, "other"]);
});

test("it writes the very lines the Python helper writes", async () => {
  const orch = new Orchestraty({ agent: "mybot", cwd: work, sessionId: "TS", env });
  await orch.start();
  await orch.beforeWrite("a.py");
  await orch.afterWrite("a.py");
  await orch.endTurn();
  await orch.end();
  python(
    [
      "import sys",
      "from orchestraty.helper import Orchestraty",
      "o = Orchestraty(agent='mybot', cwd=sys.argv[1], session_id='PY', in_process=False)",
      "o.start(); o.before_write('a.py'); o.after_write('a.py'); o.end_turn(); o.end()",
    ].join("\n"),
    [work],
  );
  const strip = (root: string, session: string) =>
    readFileSync(path.join(root, "spool", "helper-mybot", `${session}.jsonl`), "utf8")
      .replace(/^\{"ts":\d+,"pid":\d+,/gm, "{")
      .split(`"session_id":"${session}"`)
      .join('"session_id":"X"');
  assert.equal(strip(state, "TS"), strip(state, "PY"));
});

test("each call hands over its note as text, once", async () => {
  const orch = new Orchestraty({ agent: "mybot", cwd: work, sessionId: "S2", env });
  plant(state, "S2", "edit", "before a.py", path.join(work, "a.py"));
  plant(state, "S2", "next", "with the next step");
  plant(state, "S2", "stop", "at the end of the turn");
  assert.equal(await orch.beforeWrite("b.py"), null);
  assert.equal(await orch.beforeWrite("a.py"), "before a.py");
  assert.equal(await orch.beforeWrite("a.py"), null);
  assert.equal(await orch.afterWrite("a.py"), "with the next step");
  assert.equal(await orch.endTurn(), "at the end of the turn");
  assert.deepEqual(spool(state, "S2").map((l) => l.took ?? null), [null, "edit", null, "next", "stop"]);
});

test("it never rejects: on any error a call resolves to null", async () => {
  const bare = path.join(scratch, "bare");
  mkdirSync(bare);
  const none = new Orchestraty({ agent: "mybot", cwd: work, env: { ...env, ORCH_HOME: bare } });
  assert.equal(await none.start(), undefined);
  assert.equal(await none.beforeWrite("a.py"), null);
  assert.equal(await none.endTurn(), null);
  const lost = installedState("lost");
  writeFileSync(path.join(lost, "hooks", "launch.txt"), path.join(scratch, "no-such-python") + "\n");
  const noPython = new Orchestraty({ agent: "mybot", cwd: work, env: { ...env, ORCH_HOME: lost } });
  assert.equal(await noPython.afterWrite("a.py"), null);
  assert.equal(await (new Orchestraty({ agent: "mybot", cwd: work, env }).beforeWrite(undefined as unknown as string)), null);
  const slow = installedState("slow");
  writeFileSync(path.join(slow, "hooks", "hook.py"), "import time\ntime.sleep(30)\n");
  const started = Date.now();
  const late = new Orchestraty({ agent: "mybot", cwd: work, env: { ...env, ORCH_HOME: slow }, timeoutMs: 300 });
  assert.equal(await late.beforeWrite("a.py"), null);
  assert.ok(Date.now() - started < 10_000);
});

test("names, session ids and note text", () => {
  assert.equal(agentName("mybot"), "helper-mybot");
  assert.equal(agentName("my:bot/2 \u00e9"), "helper-mybot2"); // no ':' ever reaches a folder name
  assert.equal(agentName(""), "helper-agent");
  assert.equal(agentName("x".repeat(80)).length, 32);
  assert.match(new Orchestraty({ agent: "b", cwd: work, env }).sessionId, UUID);
  assert.equal(new Orchestraty({ agent: "b", cwd: work, sessionId: "a:b/c", env }).sessionId, "abc");
  assert.equal(noteText(""), null);
  assert.equal(noteText('{"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "x"}}'), "x");
  assert.equal(noteText('{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "held"}}'), "held");
  assert.equal(noteText('{"reason": "r"}'), "r");
  assert.equal(noteText("[1]"), null);
  assert.equal(noteText("plain words\n"), "plain words");
});
