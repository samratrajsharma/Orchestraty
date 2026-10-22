/**
 * The two-call helper for homemade agents written in TypeScript or JavaScript: Orchestraty's
 * hook events from the agent's own loop, one call before it writes a file, one after, and one
 * at the end of a turn.
 *
 * ```ts
 * import { Orchestraty } from "orchestraty-helper";
 * const orch = new Orchestraty({ agent: "mybot", cwd: worktree }); // a new session id unless one is given
 * await orch.start();                       // SessionStart
 * let note = await orch.beforeWrite(path);  // the note waiting for this file, or null (like PreToolUse)
 * note = await orch.afterWrite(path);       // records the edit; the "next" note, or null (like PostToolUse)
 * note = await orch.endTurn();              // callers left behind, or null (like Stop)
 * await orch.end();                         // SessionEnd
 * ```
 *
 * Each call runs the hook script `orch init` installed in the state folder, as an agent runs a
 * hook: `python -S -I <state>/hooks/hook.py helper-<name> <event>`, with the interpreter recorded
 * in `<state>/hooks/launch.txt` and the event's JSON on stdin, in the shape Claude Code's hook
 * events have. The script appends the spool line, hands over the note waiting for that event,
 * and starts the coordinator when it is not running. The state folder is found as `orch` finds
 * it: `ORCH_HOME`, else `%USERPROFILE%\.orchestraty` on Windows, else
 * `$XDG_STATE_HOME/orchestraty` (by default `~/.local/state/orchestraty`).
 *
 * A call resolves to the note's text, without the hook's JSON wrapper, or null. It never
 * rejects: on any error it resolves to null, the way the hook fails open.
 */
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

export const PREFIX = "helper-";
const MAX_AGENT = 32; // the hook keeps this much of an agent's name
const MAX_SESSION = 64; // and this much of a session id, in file names
const TIMEOUT_MS = 10_000;

export interface OrchestratyOptions {
  /** The agent's name. The spool and `orch status` call it `helper-<agent>`. */
  agent: string;
  /** The folder the agent works in: inside a worktree of a repository `orch watch` watches. */
  cwd: string;
  /** The session's id; a new one unless given. */
  sessionId?: string;
  /** Where the state folder is read from, and what the hook gets (default: process.env). */
  env?: NodeJS.ProcessEnv;
  /** How long one hook may take, in milliseconds (default 10 000). */
  timeoutMs?: number;
}

/** Letters, digits, "-" and "_": what the hook keeps in agent names and session file names. */
function kept(text: string, limit: number): string {
  return String(text).replace(/[^A-Za-z0-9_-]/g, "").slice(0, limit);
}

/** The agent as the spool and `orch status` name it: helper-<name>. */
export function agentName(name: string): string {
  return (PREFIX + (kept(name, MAX_AGENT) || "agent")).slice(0, MAX_AGENT);
}

function expandUser(value: string, env: NodeJS.ProcessEnv, platform: NodeJS.Platform): string {
  const sep = platform === "win32" ? /^~(?=$|[\\/])/ : /^~(?=$|\/)/;
  if (!sep.test(value)) {
    return value;
  }
  const home = (platform === "win32" ? env.USERPROFILE : env.HOME) || os.homedir();
  return value.replace(sep, home);
}

/** The state folder, found the way `orch` finds it. */
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

/** The note in a hook's output: the text inside its JSON wrapper, or null. */
export function noteText(output: string): string | null {
  if (!output.trim()) {
    return null;
  }
  let data: unknown;
  try {
    data = JSON.parse(output);
  } catch {
    return output.trim() || null;
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    return null;
  }
  const record = data as Record<string, unknown>;
  const inner = record.hookSpecificOutput;
  const holders: Array<[unknown, string]> = [
    [inner, "additionalContext"],
    [inner, "permissionDecisionReason"],
    [record, "reason"],
  ];
  for (const [holder, key] of holders) {
    if (holder !== null && typeof holder === "object") {
      const value = (holder as Record<string, unknown>)[key];
      if (typeof value === "string" && value) {
        return value;
      }
    }
  }
  return null;
}

/** One session of a homemade agent. */
export class Orchestraty {
  readonly agent: string;
  readonly cwd: string;
  readonly sessionId: string;
  readonly stateRoot: string;
  private readonly env: NodeJS.ProcessEnv;
  private readonly timeoutMs: number;

  constructor(options: OrchestratyOptions) {
    this.env = options.env ?? process.env;
    this.agent = agentName(options.agent);
    this.cwd = path.resolve(String(options.cwd));
    this.sessionId = (options.sessionId !== undefined ? kept(options.sessionId, MAX_SESSION) : "") || randomUUID();
    this.stateRoot = stateRoot(this.env);
    this.timeoutMs = options.timeoutMs ?? TIMEOUT_MS;
  }

  /** SessionStart: the session begins. */
  async start(): Promise<void> {
    await this.call("SessionStart", () => ({ source: "startup" }));
  }

  /** Before writing `file`: the note waiting for that file (a change another session made that
   * this file uses), or null. */
  beforeWrite(file: string): Promise<string | null> {
    return this.call("PreToolUse", () => this.write(file));
  }

  /** After writing `file`: records the edit; the note waiting for the session's next step
   * (another session is changing the same function, or uses what this one changed), or null. */
  afterWrite(file: string): Promise<string | null> {
    return this.call("PostToolUse", () => this.write(file));
  }

  /** At the end of a turn: the callers this session's changes left behind, or null. */
  endTurn(): Promise<string | null> {
    return this.call("Stop", () => ({ stop_hook_active: false }));
  }

  /** SessionEnd: the session is over; its waiting notes are taken back. */
  async end(reason = "other"): Promise<void> {
    await this.call("SessionEnd", () => ({ reason }));
  }

  /** The event's JSON, as an agent's hook gets it. */
  payload(event: string, fields: Record<string, unknown>): string {
    return JSON.stringify({ session_id: this.sessionId, cwd: this.cwd, hook_event_name: event, ...fields });
  }

  private write(file: string): Record<string, unknown> {
    if (typeof file !== "string") {
      throw new TypeError("a file path is a string");
    }
    return { tool_name: "Write", tool_input: { file_path: path.resolve(this.cwd, file) } };
  }

  private async call(event: string, fields: () => Record<string, unknown>): Promise<string | null> {
    try {
      const output = await this.runHook(event, this.payload(event, fields()));
      return output === null ? null : noteText(output);
    } catch {
      return null;
    }
  }

  /** Run the installed hook script for one event: what it prints, or null when it could not run. */
  private async runHook(event: string, payload: string): Promise<string | null> {
    const hooks = path.join(this.stateRoot, "hooks");
    const script = path.join(hooks, "hook.py");
    if (!existsSync(script)) {
      return null;
    }
    const python = (await readFile(path.join(hooks, "launch.txt"), "utf8")).split(/\r?\n/)[0].trim();
    if (!python) {
      return null;
    }
    return new Promise((resolve) => {
      let settled = false;
      let timer: NodeJS.Timeout | undefined;
      const chunks: Buffer[] = [];
      const finish = (value: string | null) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          resolve(value);
        }
      };
      const child = spawn(python, ["-S", "-I", script, this.agent, event], {
        env: this.env,
        stdio: ["pipe", "pipe", "ignore"],
        windowsHide: true,
      });
      timer = setTimeout(() => {
        child.kill();
        finish(null);
      }, this.timeoutMs);
      child.on("error", () => finish(null));
      child.stdin.on("error", () => undefined); // a hook that exits early closes its stdin
      child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk));
      child.on("close", () => finish(Buffer.concat(chunks).toString("utf8")));
      child.stdin.end(payload, "utf8");
    });
  }
}
