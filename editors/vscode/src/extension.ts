// Orchestraty in VS Code and Cursor: a status bar item and a panel for the repository of this
// workspace, whatever agents work on it. It shows what Orchestraty's coordinator worked out
// and tells the person of a strong overlap in a file they have open; it detects nothing.
//
// It reads <state>/status.json every few seconds while the window has the focus, and less
// often otherwise; `orch status --json` stands in while that file is missing. It starts only
// Orchestraty's own command line (for that, and for Steer and Wake on/off, which change
// Orchestraty's config through orch config), makes no network request, and writes nothing
// itself: not to the repository, not to Orchestraty's state folder.
import * as cp from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

import { findPlace, real } from "./repository";
import { orchCommand, parseLaunch, parseStatus, stateRoot } from "./state";
import {
  type Node, type Overlap, type Repository, type Status, STRENGTHS, describe, relative, samePath, statusBar, target,
  toNotify, tree,
} from "./view";

const VIEW = "orchestraty.status";
const FOCUSED_MS = 3000;
const UNFOCUSED_MS = 30000;
const FALLBACK_MS = 30000;          // orch status --json, while status.json is missing, at most this often
const NOTIFIED = "orchestraty.notified";

interface Place {
  top: string;                      // the worktree's top, as this workspace names it
  realTop: string;                  // the same with links resolved, as status.json names worktrees
  commonDir: string;
}

interface Watched {
  place: Place;
  repo: Repository;
}

interface Ran {
  code: number;
  stdout: string;
  stderr: string;
}

class Orchestraty implements vscode.TreeDataProvider<Node>, vscode.Disposable {
  private readonly folder = stateRoot();
  private status: Status | null = null;
  private stamp = "";               // status.json's time and size when last read
  private lastFallback = 0;
  private places: Place[] = [];
  private watched: Watched[] = [];
  private timer: NodeJS.Timeout | undefined;
  private busy = false;
  private again = false;            // a read asked for while one ran: done when that one ends
  private disposed = false;
  private readonly notified: Set<string>;
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private readonly item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);

  constructor(private readonly context: vscode.ExtensionContext) {
    this.notified = new Set(context.workspaceState.get<string[]>(NOTIFIED, []));
    this.item.command = "orchestraty.showStatus";
    context.subscriptions.push(
      this,
      this.item,
      this.changed,
      vscode.window.createTreeView(VIEW, { treeDataProvider: this }),
      vscode.commands.registerCommand("orchestraty.showStatus", () => this.show()),
      vscode.commands.registerCommand("orchestraty.refresh", () => this.tick(true)),
      vscode.commands.registerCommand("orchestraty.steerOn", () => this.config("steer", "on")),
      vscode.commands.registerCommand("orchestraty.steerOff", () => this.config("steer", "off")),
      vscode.commands.registerCommand("orchestraty.wakeOn", () => this.config("wake", "on")),
      vscode.commands.registerCommand("orchestraty.wakeOff", () => this.config("wake", "off")),
      vscode.commands.registerCommand("orchestraty.openOverlap", (key?: string) => this.openOverlap(key)),
      vscode.workspace.onDidChangeWorkspaceFolders(() => this.findPlaces(true)),
      vscode.window.onDidChangeWindowState((state) => state.focused && this.tick()),
      vscode.window.onDidChangeActiveTextEditor(() => this.showItem()),
      vscode.window.tabGroups.onDidChangeTabs(() => this.notify()),
    );
    this.findPlaces(false);
    void this.tick(true);
  }

  dispose(): void {
    this.disposed = true;
    clearTimeout(this.timer);
  }

  // ------------------------------------------------------------------ reading the status
  private findPlaces(again: boolean): void {
    const places: Place[] = [];
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      if (folder.uri.scheme !== "file") {
        continue;
      }
      const found = findPlace(folder.uri.fsPath);
      if (found && !places.some((p) => samePath(p.top, found.top))) {
        places.push({ top: found.top, realTop: real(found.top), commonDir: real(found.commonDir) });
      }
    }
    this.places = places;
    if (again) {
      void this.tick(true);
    }
  }

  private schedule(): void {
    clearTimeout(this.timer);
    if (!this.disposed && this.places.length) {
      this.timer = setTimeout(() => void this.tick(), vscode.window.state.focused ? FOCUSED_MS : UNFOCUSED_MS);
    }
  }

  private async tick(force = false): Promise<void> {
    if (this.disposed) {
      return;
    }
    if (this.busy) {
      this.again = true;
      return;
    }
    this.busy = true;
    try {
      if (!this.places.length) {
        this.status = null;
        this.render();
      } else if (await this.read(force)) {
        this.render();
      }
    } catch {
      // the next tick tries again
    } finally {
      this.busy = false;
      if (this.again) {
        this.again = false;
        void this.tick(true);
      } else {
        this.schedule();
      }
    }
  }

  /** Read status.json when it changed (or `orch status --json` while it is missing); true
   * when there is something new to show. */
  private async read(force: boolean): Promise<boolean> {
    const file = path.join(this.folder, "status.json");
    let stat: fs.Stats | null = null;
    try {
      stat = await fs.promises.stat(file);
    } catch {
      stat = null;
    }
    if (stat) {
      const stamp = `${stat.mtimeMs}:${stat.size}`;
      if (!force && stamp === this.stamp) {
        return false;
      }
      const parsed = parseStatus(await fs.promises.readFile(file, "utf8"));
      if (!parsed) {
        return false;                    // caught mid-write, or another schema: read again next time
      }
      this.stamp = stamp;
      this.status = parsed;
      return true;
    }
    this.stamp = "";
    if (!fs.existsSync(this.folder)) {           // Orchestraty is not installed here: nothing to run
      const had = this.status !== null;
      this.status = null;
      return had;
    }
    if (!force && Date.now() - this.lastFallback < FALLBACK_MS) {
      return false;
    }
    this.lastFallback = Date.now();
    const ran = await this.orch(["status", "--json"]);
    this.status = ran.code === 0 ? parseStatus(ran.stdout) : null;
    return true;
  }

  private render(): void {
    const watched: Watched[] = [];
    for (const place of this.places) {
      const repo = this.status?.repositories.find((r) => samePath(real(r.common_dir), place.commonDir)) ?? null;
      if (repo) {
        watched.push({ place, repo });
      }
    }
    this.watched = watched;
    void vscode.commands.executeCommand("setContext", "orchestraty.watched", watched.length > 0);
    this.forget();
    this.showItem();
    this.changed.fire(undefined);
    this.notify();
  }

  // ------------------------------------------------------------------ the status bar
  /** The repository of the file in the active editor, else the first watched one. */
  private current(): Watched | undefined {
    const file = vscode.window.activeTextEditor?.document.uri;
    if (file?.scheme === "file") {
      const hit = this.watched.find((w) => relative(file.fsPath, w.place.top) !== null);
      if (hit) {
        return hit;
      }
    }
    return this.watched[0];
  }

  private showItem(): void {
    const w = this.current();
    if (!w) {
      this.item.hide();
      return;
    }
    const shown = statusBar(w.repo, w.place.realTop);
    this.item.text = shown.text;
    this.item.tooltip = shown.tooltip;
    this.item.show();
  }

  private async show(): Promise<void> {
    await vscode.commands.executeCommand(`${VIEW}.focus`);
    await this.tick(true);
  }

  // ------------------------------------------------------------------ the panel
  getTreeItem(node: Node): vscode.TreeItem {
    const state = !node.children ? vscode.TreeItemCollapsibleState.None
      : node.expanded ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.Collapsed;
    const item = new vscode.TreeItem(node.label, state);
    item.id = node.id;
    item.description = node.description;
    item.tooltip = node.tooltip;
    if (node.icon) {
      item.iconPath = new vscode.ThemeIcon(node.icon);
    }
    if (node.overlap) {
      item.command = { command: "orchestraty.openOverlap", title: "Open file at overlap", arguments: [node.overlap] };
    }
    return item;
  }

  getChildren(node?: Node): Node[] {
    if (node) {
      return node.children ?? [];
    }
    if (this.watched.length === 1) {
      return tree(this.watched[0].repo, this.watched[0].place.realTop);
    }
    return this.watched.map((w) => ({
      id: `${w.repo.common_dir}|repository`, label: w.repo.name, description: w.place.top, icon: "repo",
      expanded: true, children: tree(w.repo, w.place.realTop),
    }));
  }

  // ------------------------------------------------------------------ notifications
  /** Files open in the editor: tabs and visible editors. */
  private openFiles(): string[] {
    const files = new Set<string>();
    for (const group of vscode.window.tabGroups.all) {
      for (const tab of group.tabs) {
        const input = tab.input;
        if (input instanceof vscode.TabInputText && input.uri.scheme === "file") {
          files.add(input.uri.fsPath);
        } else if (input instanceof vscode.TabInputTextDiff && input.modified.scheme === "file") {
          files.add(input.modified.fsPath);
        }
      }
    }
    for (const editor of vscode.window.visibleTextEditors) {
      if (editor.document.uri.scheme === "file") {
        files.add(editor.document.uri.fsPath);
      }
    }
    return [...files];
  }

  /** The silence rule (view.toNotify): one notification for each strong overlap that affects a
   * file open in the editor, once. */
  private notify(): void {
    const files = this.openFiles();
    let added = false;
    for (const w of this.watched) {
      const open = new Set<string>();
      for (const file of files) {
        const rel = relative(file, w.place.top);
        if (rel) {
          open.add(rel);
        }
      }
      for (const o of toNotify(w.repo, open, this.notified)) {
        this.notified.add(o.key);
        added = true;
        void vscode.window.showWarningMessage(`Orchestraty: ${describe(w.repo, o)}`, "Open file", "Show panel")
          .then((choice) => choice === "Open file" ? this.open(w, o) : choice === "Show panel" ? this.show() : undefined);
      }
    }
    if (added) {
      this.remember();
    }
  }

  private remember(): void {
    void this.context.workspaceState.update(NOTIFIED, [...this.notified]);
  }

  /** Overlaps that are gone may be told of again if they come back. */
  private forget(): void {
    if (!this.watched.length) {
      return;
    }
    const current = new Set(this.watched.flatMap((w) => (w.repo.overlaps ?? []).map((o) => o.key)));
    const before = this.notified.size;
    for (const key of [...this.notified]) {
      if (!current.has(key)) {
        this.notified.delete(key);
      }
    }
    if (this.notified.size !== before) {
      this.remember();
    }
  }

  // ------------------------------------------------------------------ commands
  private async openOverlap(key?: string): Promise<void> {
    const all = this.watched.flatMap((w) => (w.repo.overlaps ?? []).map((o) => ({ w, o })))
      .sort((a, b) => STRENGTHS.indexOf(a.o.strength) - STRENGTHS.indexOf(b.o.strength));
    let chosen = key ? all.find((x) => x.o.key === key) : undefined;
    if (!key) {
      if (!all.length) {
        void vscode.window.showInformationMessage("Orchestraty: no overlaps on this workspace's repository.");
        return;
      }
      const picked = await vscode.window.showQuickPick(all.map((x) => ({
        label: describe(x.w.repo, x.o), description: `${x.o.strength} (${x.o.rule})`, x,
      })), { placeHolder: "Open the file of an overlap" });
      chosen = picked?.x;
    }
    if (chosen) {
      await this.open(chosen.w, chosen.o);
    }
  }

  private async open(w: Watched, o: Overlap): Promise<void> {
    const where = target(w.repo, o, w.place.realTop);
    if (!where) {
      void vscode.window.showInformationMessage(`Orchestraty: ${describe(w.repo, o)}. It is about no file.`);
      return;
    }
    for (const worktree of where.worktrees) {
      const file = path.join(worktree, ...where.file.split("/"));
      let stat: fs.Stats;
      try {
        stat = await fs.promises.stat(file);
      } catch {
        continue;
      }
      const uri = vscode.Uri.file(file);
      if (stat.isDirectory()) {
        await vscode.commands.executeCommand("revealInExplorer", uri);
        return;
      }
      const at = new vscode.Position(Math.max(0, where.line - 1), 0);
      await vscode.window.showTextDocument(uri, { selection: new vscode.Range(at, at), preview: false });
      return;
    }
    void vscode.window.showWarningMessage(`Orchestraty: ${where.file} is in none of the session's worktrees.`);
  }

  private async config(name: string, value: string): Promise<void> {
    const ran = await this.orch(["config", name, value]);
    if (ran.code === 0) {
      void vscode.window.showInformationMessage(`Orchestraty: ${ran.stdout.trim() || `${name}: ${value}`}`);
    } else {
      void vscode.window.showErrorMessage(`Orchestraty: orch config ${name} ${value} did not run: ` +
                                          (ran.stderr || ran.stdout).trim());
    }
  }

  /** Orchestraty's own command line, started from the state folder, never from the repository,
   * and without a console window on Windows. */
  private async orch(args: string[]): Promise<Ran> {
    let launch = null;
    try {
      launch = parseLaunch(await fs.promises.readFile(path.join(this.folder, "hooks", "launch.txt"), "utf8"));
    } catch {
      launch = null;
    }
    const command = orchCommand(this.folder, args, launch);
    const cwd = fs.existsSync(this.folder) ? this.folder : os.homedir();
    return new Promise((resolve) => {
      cp.execFile(command.file, command.args, { cwd, env: command.env, windowsHide: true, timeout: 60000,
                                                 maxBuffer: 32 * 1024 * 1024 }, (error, stdout, stderr) => {
        const code = !error ? 0 : typeof error.code === "number" ? error.code : -1;
        resolve({ code, stdout: String(stdout), stderr: String(stderr) || (error ? error.message : "") });
      });
    });
  }
}

export function activate(context: vscode.ExtensionContext): void {
  new Orchestraty(context);
}

export function deactivate(): void {
  // the subscriptions dispose of everything
}
