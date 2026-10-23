// Orchestraty's state folder, and the two files the extension reads there: status.json, which
// the coordinator keeps current, and hooks/launch.txt, the Python and import path orch init
// recorded for starting Orchestraty. The extension writes nothing there, nor in the repository;
// the one thing it starts is Orchestraty's own command line (orch status --json, orch config).
import * as os from "node:os";
import * as path from "node:path";

import type { Status } from "./view";

/** The status.json schema this extension reads. */
export const SCHEMA = 1;

function expandUser(value: string, env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string {
  const sep = platform === "win32" ? /^~(?=$|[\\/])/ : /^~(?=$|\/)/;
  if (!sep.test(value)) {
    return value;
  }
  const home = (platform === "win32" ? env.USERPROFILE : env.HOME) || os.homedir();
  return value.replace(sep, home);
}

/** The state folder, found the way `orch` finds it (orchestraty/paths.py): ORCH_HOME, else
 * %USERPROFILE%\.orchestraty on Windows, else $XDG_STATE_HOME/orchestraty, by default
 * ~/.local/state/orchestraty. */
export function stateRoot(env: NodeJS.ProcessEnv = process.env, platform: NodeJS.Platform = process.platform): string {
  const paths = platform === "win32" ? path.win32 : path.posix;
  const override = env.ORCH_HOME ?? "";
  if (override) {
    return paths.resolve(expandUser(override, env, platform));
  }
  if (platform === "win32") {
    return paths.join(env.USERPROFILE || os.homedir(), ".orchestraty");
  }
  let base = env.XDG_STATE_HOME ?? "";
  if (!paths.isAbsolute(base)) {
    // the XDG rule: a relative value is ignored
    base = paths.join(env.HOME || os.homedir(), ".local", "state");
  }
  return paths.join(base, "orchestraty");
}

/** status.json, or `orch status --json`'s output: the object when it is the schema this
 * extension reads, else null. */
export function parseStatus(text: string): Status | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object") {
    return null;
  }
  const status = data as Status;
  return status.schema === SCHEMA && Array.isArray(status.repositories) ? status : null;
}

export interface Launch {
  python: string;
  path: string;
}

/** hooks/launch.txt: the interpreter on its first line, the import path on its second. */
export function parseLaunch(text: string): Launch | null {
  const [python, importPath] = text.split(/\r?\n/);
  return python ? { python, path: importPath || "" } : null;
}

export interface Command {
  file: string;
  args: string[];
  env: NodeJS.ProcessEnv;
}

/** How to run `orch <args>` for this state folder: with the Python and import path orch init
 * recorded, as the Claude Code mod does, or else the `orch` on the PATH. */
export function orchCommand(folder: string, args: string[], launch: Launch | null,
                            env: NodeJS.ProcessEnv = process.env, platform: NodeJS.Platform = process.platform): Command {
  const withHome = { ...env, ORCH_HOME: folder };
  if (!launch) {
    return { file: "orch", args, env: withHome };
  }
  const sep = platform === "win32" ? ";" : ":";
  const code = "import os, sys; os.environ['ORCH_HOME'] = " + JSON.stringify(folder) +
               "; sys.path[:0] = " + JSON.stringify(launch.path.split(sep).filter(Boolean)) +
               "; from orchestraty.cli import main; sys.exit(main(" + JSON.stringify(args) + "))";
  return { file: launch.python, args: ["-c", code], env: withHome };
}
