// The extension itself (src/extension.ts) against a stand-in for the VS Code API, which records
// what the extension asks of the editor: a workspace in a linked worktree of a repository whose
// status.json lists two sessions, with one of the files their strong overlaps are about open.
// Headless; it does not replace trying the .vsix in VS Code and Cursor.
import { after, test } from "node:test";
import * as assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const REPO = path.join(__dirname, "..", "..", "..", "..");
const PYTHON = process.env.ORCH_TEST_PYTHON || (process.platform === "win32" ? "python" : "python3");
const scratch = realpathSync.native(mkdtempSync(path.join(os.tmpdir(), "orch-vscode-ext-")));
after(() => rmSync(scratch, { recursive: true, force: true }));

// ------------------------------------------------------------------ the stand-in
type Listener = (...args: unknown[]) => unknown;

class EventEmitter {
  private listeners: Listener[] = [];
  event = (listener: Listener) => {
    this.listeners.push(listener);
    return { dispose: () => undefined };
  };
  fire(value?: unknown): void {
    for (const listener of this.listeners) {
      listener(value);
    }
  }
  dispose(): void {
    this.listeners = [];
  }
}

class Uri {
  constructor(readonly scheme: string, readonly fsPath: string) {}
  static file(p: string): Uri {
    return new Uri("file", p);
  }
}

class Position {
  constructor(readonly line: number, readonly character: number) {}
}

class Range {
  constructor(readonly start: Position, readonly end: Position) {}
}

class TabInputText {
  constructor(readonly uri: Uri) {}
}

class TabInputTextDiff {
  constructor(readonly original: Uri, readonly modified: Uri) {}
}

class TreeItem {
  id?: string;
  description?: string;
  tooltip?: string;
  iconPath?: unknown;
  command?: { command: string; arguments?: unknown[] };
  constructor(readonly label: string, readonly collapsibleState: number) {}
}

const recorded = {
  contexts: {} as Record<string, unknown>,
  warnings: [] as string[],
  infos: [] as string[],
  errors: [] as string[],
  opened: [] as Array<{ file: string; line: number }>,
  focused: [] as string[],
  item: { text: "", tooltip: "", shown: false, command: "" },
};
const commands = new Map<string, Listener>();
const tabsChanged = new EventEmitter();
let provider: { getChildren(node?: unknown): unknown[]; getTreeItem(node: unknown): TreeItem } | null = null;
let openTabs: unknown[] = [];

const vscode = {
  EventEmitter, Uri, Position, Range, TabInputText, TabInputTextDiff, TreeItem,
  ThemeIcon: class { constructor(readonly id: string) {} },
  TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 },
  StatusBarAlignment: { Left: 1, Right: 2 },
  workspace: {
    workspaceFolders: [] as Array<{ uri: Uri }>,
    onDidChangeWorkspaceFolders: new EventEmitter().event,
  },
  window: {
    state: { focused: false },
    activeTextEditor: undefined,
    visibleTextEditors: [],
    tabGroups: { get all() { return [{ tabs: openTabs.map((input) => ({ input })) }]; }, onDidChangeTabs: tabsChanged.event },
    onDidChangeWindowState: new EventEmitter().event,
    onDidChangeActiveTextEditor: new EventEmitter().event,
    createStatusBarItem: () => ({
      set text(v: string) { recorded.item.text = v; },
      set tooltip(v: string) { recorded.item.tooltip = v; },
      set command(v: string) { recorded.item.command = v; },
      show: () => { recorded.item.shown = true; },
      hide: () => { recorded.item.shown = false; },
      dispose: () => undefined,
    }),
    createTreeView: (_id: string, options: { treeDataProvider: typeof provider }) => {
      provider = options.treeDataProvider;
      return { dispose: () => undefined };
    },
    showWarningMessage: async (text: string) => { recorded.warnings.push(text); return undefined; },
    showInformationMessage: async (text: string) => { recorded.infos.push(text); return undefined; },
    showErrorMessage: async (text: string) => { recorded.errors.push(text); return undefined; },
    showQuickPick: async () => undefined,
    showTextDocument: async (uri: Uri, options: { selection: Range }) => {
      recorded.opened.push({ file: uri.fsPath, line: options.selection.start.line });
    },
  },
  commands: {
    registerCommand: (name: string, run: Listener) => {
      commands.set(name, run);
      return { dispose: () => commands.delete(name) };
    },
    executeCommand: async (name: string, ...args: unknown[]) => {
      if (name === "setContext") {
        recorded.contexts[String(args[0])] = args[1];
      } else if (name.endsWith(".focus")) {
        recorded.focused.push(name);
      } else {
        return commands.get(name)?.(...args);
      }
      return undefined;
    },
  },
};

const Module = require("node:module") as { _resolveFilename: (request: string, ...rest: unknown[]) => string };
const resolve = Module._resolveFilename;
Module._resolveFilename = function (request: string, ...rest: unknown[]) {
  return request === "vscode" ? "vscode" : resolve.call(this, request, ...rest);
};
require.cache.vscode = { id: "vscode", filename: "vscode", loaded: true, exports: vscode } as unknown as NodeJS.Module;

// ------------------------------------------------------------------ the scene
function scene(): { state: string; wtA: string; wtB: string } {
  const repo = path.join(scratch, "repo");
  const wtA = path.join(scratch, "wt-a");
  const wtB = path.join(scratch, "wt-b");
  for (const [wt, name] of [[wtA, "wt-a"], [wtB, "wt-b"]]) {
    mkdirSync(path.join(repo, ".git", "worktrees", name), { recursive: true });
    writeFileSync(path.join(repo, ".git", "worktrees", name, "commondir"), "../..\n");
    mkdirSync(wt, { recursive: true });
    writeFileSync(path.join(wt, ".git"), `gitdir: ${path.join(repo, ".git", "worktrees", name)}\n`);
    writeFileSync(path.join(wt, "a.py"), "def charge(amount, *, currency):\n    return amount\n");
    writeFileSync(path.join(wt, "b.py"), "from a import charge\n\n\ndef pay():\n    return charge(1, 'EUR')\n");
  }
  const fixture = readFileSync(path.join(__dirname, "..", "..", "test", "fixtures", "status.json"), "utf8");
  const status = JSON.parse(fixture, (_key, value) =>
    typeof value === "string" && value.startsWith("/work/") ? path.join(scratch, ...value.slice(6).split("/")) : value);
  const state = path.join(scratch, "state");
  mkdirSync(path.join(state, "hooks"), { recursive: true });
  writeFileSync(path.join(state, "status.json"), JSON.stringify(status));
  writeFileSync(path.join(state, "hooks", "launch.txt"),
                `${PYTHON}\n${[path.join(REPO, "orchestraty"), path.join(REPO, "engine")].join(path.delimiter)}\n`);
  return { state, wtA, wtB };
}

async function until(check: () => boolean, what: string): Promise<void> {
  for (let i = 0; i < 200 && !check(); i++) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.ok(check(), `timed out waiting for ${what}`);
}

test("the extension shows the repository's sessions and tells of a strong overlap in an open file, once", async () => {
  const { state, wtA, wtB } = scene();
  process.env.ORCH_HOME = state;
  process.env.PYTHONDONTWRITEBYTECODE = "1";
  vscode.workspace.workspaceFolders = [{ uri: Uri.file(path.join(wtB)) }];
  openTabs = [new TabInputText(Uri.file(path.join(wtB, "b.py")))];
  const subscriptions: Array<{ dispose(): unknown }> = [];
  const memento = new Map<string, unknown>();
  const context = {
    subscriptions,
    workspaceState: { get: (k: string, d: unknown) => memento.get(k) ?? d, update: async (k: string, v: unknown) => { memento.set(k, v); } },
  };
  const extension = require("../src/extension") as typeof import("../src/extension");
  try {
    extension.activate(context as never);
    await until(() => recorded.item.shown, "the status bar item");
    assert.equal(recorded.contexts["orchestraty.watched"], true);
    assert.equal(recorded.item.text, "$(organization) 1 other session, $(warning) 2 strong");
    assert.equal(recorded.item.command, "orchestraty.showStatus");

    const top = provider!.getChildren() as Array<{ label: string; children?: unknown[] }>;
    assert.deepEqual(top.map((n) => n.label), ["Sessions", "Overlaps", "Claims", "Declarations"]);
    const item = provider!.getTreeItem(top[0]);
    assert.equal(item.collapsibleState, vscode.TreeItemCollapsibleState.Expanded);
    const strong = (provider!.getChildren(top[1]) as Array<{ children: unknown[] }>)[0];
    const o3 = provider!.getTreeItem(strong.children[0]);
    assert.equal(o3.command?.command, "orchestraty.openOverlap");

    // b.py is open: the two strong overlaps about it, once each
    await until(() => recorded.warnings.length === 2, "two notifications");
    assert.match(recorded.warnings[0], /^Orchestraty: charge \(a\.py\) changed by Claude Code in wt-a; b\.py uses it/);
    assert.match(recorded.warnings[1], /^Orchestraty: charge \(a\.py\) changed by Claude Code in wt-a: 1 caller left behind/);
    tabsChanged.fire();
    await commands.get("orchestraty.refresh")!();
    assert.equal(recorded.warnings.length, 2);
    assert.deepEqual(memento.get("orchestraty.notified"), [
      "O3|a.py::charge|smoke-a|smoke-b|b.py", "O6|a.py::charge|smoke-a"]);

    // choosing the overlap opens b.py in this worktree, at the line that uses charge
    await commands.get("orchestraty.openOverlap")!(o3.command!.arguments![0]);
    assert.deepEqual(recorded.opened, [{ file: path.join(wtB, "b.py"), line: 4 }]);
    rmSync(path.join(wtB, "b.py"));
    await commands.get("orchestraty.openOverlap")!(o3.command!.arguments![0]);
    assert.deepEqual(recorded.opened[1], { file: path.join(wtA, "b.py"), line: 4 });   // else in another worktree

    await commands.get("orchestraty.showStatus")!();
    assert.deepEqual(recorded.focused, ["orchestraty.status.focus"]);

    // Steer on, through orch config, in this state folder
    const python = spawnSync(PYTHON, ["--version"], { windowsHide: true });
    if (!python.error && python.status === 0) {
      await commands.get("orchestraty.steerOn")!();
      assert.deepEqual(recorded.errors, []);
      assert.deepEqual(recorded.infos, ["Orchestraty: steer: on"]);
      assert.equal(JSON.parse(readFileSync(path.join(state, "config.json"), "utf8")).steer, true);
    }

    // a workspace whose repository Orchestraty does not watch shows nothing
    const status = JSON.parse(readFileSync(path.join(state, "status.json"), "utf8"));
    writeFileSync(path.join(scratch, "fallback.json"), JSON.stringify(status));
    status.repositories = [];
    writeFileSync(path.join(state, "status.json"), JSON.stringify(status));
    await commands.get("orchestraty.refresh")!();
    assert.equal(recorded.item.shown, false);
    assert.equal(recorded.contexts["orchestraty.watched"], false);
    assert.deepEqual(provider!.getChildren(), []);

    // with no status.json, orch status --json stands in: an orchestraty.cli of the test's own,
    // first on the import path launch.txt gives, prints the status
    if (!python.error && python.status === 0) {
      const fake = path.join(scratch, "fake", "orchestraty");
      mkdirSync(fake, { recursive: true });
      writeFileSync(path.join(fake, "__init__.py"), "");
      writeFileSync(path.join(fake, "cli.py"), "import sys\n\ndef main(args):\n    assert args == ['status', '--json'], args\n" +
                    `    sys.stdout.write(open(${JSON.stringify(path.join(scratch, "fallback.json"))}, encoding='utf-8').read())\n` +
                    "    return 0\n");
      writeFileSync(path.join(state, "hooks", "launch.txt"), `${PYTHON}\n${path.dirname(fake)}\n`);
      rmSync(path.join(state, "status.json"));
      await commands.get("orchestraty.refresh")!();
      assert.equal(recorded.item.shown, true);
      assert.equal(recorded.item.text, "$(organization) 1 other session, $(warning) 2 strong");
    }
  } finally {
    for (const s of subscriptions) {
      s.dispose();
    }
  }
});
