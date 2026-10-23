// The state folder, found the way orch finds it (checked against the Python package when a
// Python is here), launch.txt, and how orch is run.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as path from "node:path";

import { orchCommand, parseLaunch, stateRoot } from "../src/state";

const REPO = path.join(__dirname, "..", "..", "..", "..");
const PYTHON = process.env.ORCH_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");

function pythonStateRoot(env: NodeJS.ProcessEnv): string | null {
  const proc = spawnSync(PYTHON, ["-c", "from orchestraty import paths\nprint(paths.state_root())"], {
    env: { ...env, PYTHONPATH: path.join(REPO, "orchestraty"), PYTHONDONTWRITEBYTECODE: "1" },
    encoding: "utf8", windowsHide: true,
  });
  return proc.status === 0 ? proc.stdout.trim() : null;
}

test("the state folder is found the way orch finds it", (t) => {
  assert.equal(stateRoot({ USERPROFILE: "C:\\Users\\me" }, "win32"), "C:\\Users\\me\\.orchestraty");
  assert.equal(stateRoot({ ORCH_HOME: "D:\\o", USERPROFILE: "C:\\Users\\me" }, "win32"), "D:\\o");
  assert.equal(stateRoot({ HOME: "/h" }, "linux"), "/h/.local/state/orchestraty");
  assert.equal(stateRoot({ HOME: "/h", XDG_STATE_HOME: "/x" }, "darwin"), "/x/orchestraty");
  assert.equal(stateRoot({ HOME: "/h", XDG_STATE_HOME: "x" }, "linux"), "/h/.local/state/orchestraty");
  assert.equal(stateRoot({ HOME: "/h", ORCH_HOME: "~/s" }, "linux"), "/h/s");
  const cases: NodeJS.ProcessEnv[] =
    process.platform === "win32"
      ? [{ ORCH_HOME: "C:\\o\\h" }, { USERPROFILE: "C:\\Users\\me" }, { ORCH_HOME: "~\\s", USERPROFILE: "C:\\Users\\me" }]
      : [{ ORCH_HOME: "/o/h" }, { ORCH_HOME: "~/s", HOME: "/h" }, { XDG_STATE_HOME: "/xdg", HOME: "/h" },
         { XDG_STATE_HOME: "not/absolute", HOME: "/h" }, { HOME: "/h" }];
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const name of ["ORCH_HOME", "XDG_STATE_HOME", "HOME", "USERPROFILE"]) {
    delete env[name];
  }
  if (pythonStateRoot({ ...env, ...cases[0] }) === null) {
    t.skip("no Python with this repository's orchestraty package to compare with");
    return;
  }
  for (const given of cases) {
    assert.equal(stateRoot({ ...env, ...given }), pythonStateRoot({ ...env, ...given }), JSON.stringify(given));
  }
});

test("launch.txt: the interpreter, then the import path", () => {
  assert.deepEqual(parseLaunch("/usr/bin/python3\n/opt/orch/lib\n"), { python: "/usr/bin/python3", path: "/opt/orch/lib" });
  assert.deepEqual(parseLaunch("C:\\Python\\python.exe\r\nC:\\a;C:\\b\r\n"), { python: "C:\\Python\\python.exe", path: "C:\\a;C:\\b" });
  assert.deepEqual(parseLaunch("/usr/bin/python3"), { python: "/usr/bin/python3", path: "" });
  assert.equal(parseLaunch(""), null);
});

test("orch runs with orch init's Python and import path, else from the PATH", () => {
  const plain = orchCommand("/s", ["status", "--json"], null, { PATH: "/bin" }, "linux");
  assert.deepEqual(plain, { file: "orch", args: ["status", "--json"], env: { PATH: "/bin", ORCH_HOME: "/s" } });
  const posix = orchCommand("/s", ["config", "steer", "on"], { python: "/py", path: "/a:/b" }, {}, "linux");
  assert.equal(posix.file, "/py");
  assert.equal(posix.args[0], "-c");
  assert.equal(posix.args[1], "import os, sys; os.environ['ORCH_HOME'] = \"/s\"; sys.path[:0] = [\"/a\",\"/b\"]; " +
                              "from orchestraty.cli import main; sys.exit(main([\"config\",\"steer\",\"on\"]))");
  assert.equal(posix.env.ORCH_HOME, "/s");
  const windows = orchCommand("C:\\s", ["status"], { python: "C:\\py.exe", path: "C:\\a;D:\\b" }, {}, "win32");
  assert.match(windows.args[1], /sys\.path\[:0\] = \["C:\\\\a","D:\\\\b"\]/);
});

test("orch config runs through the command it builds", (t) => {
  const command = orchCommand(path.join(REPO, "nowhere"), ["--version"], { python: PYTHON, path: path.join(REPO, "orchestraty") },
                              { ...process.env, PYTHONDONTWRITEBYTECODE: "1" });
  const proc = spawnSync(command.file, command.args, { env: command.env, encoding: "utf8", windowsHide: true, cwd: REPO });
  if (proc.error || (proc.status !== 0 && /No module named/.test(proc.stderr))) {
    t.skip("no Python here to run orch with");
    return;
  }
  assert.equal(proc.status, 0, proc.stderr);
  assert.match(proc.stdout, /^orch \d/);
});
