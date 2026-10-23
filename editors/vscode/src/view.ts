// What the extension shows, worked out from Orchestraty's status (status.json, the schema in
// orchestraty/docs/status-json.md) and nothing else: the status bar's text, the panel's tree,
// where an overlap's file is, and which overlaps to notify the person of. No VS Code API here,
// so the tests run it with node --test.
import * as path from "node:path";

export interface FileSymbol {
  symbol: string;
  file: string;
  lines?: number[];
}

export interface Declaration {
  paths?: string[];
  symbols?: string[];
  task?: string;
  until?: string;
}

export interface Claim {
  id: number;
  kind: string;
  target: string;
  hard?: boolean;
  until?: string;
}

export interface Session {
  id: string;
  agent: string;
  worktree: string;
  branch: string;
  state?: string;
  turn?: string;
  last_event?: string;
  seconds_since_last_event?: number;
  changes?: { files?: Record<string, string>; symbols?: FileSymbol[] };
  declared?: Declaration[];
  claims?: Claim[];
}

export interface Overlap {
  key: string;
  rule: string;
  strength: string;
  symbol: string;
  file: string;
  changer?: string;
  receiver?: string;
  sessions?: string[];
  detail?: any;
}

export interface Repository {
  name: string;
  common_dir: string;
  sessions?: Session[];
  overlaps?: Overlap[];
}

export interface Status {
  schema: number;
  coordinator?: { running?: boolean };
  repositories: Repository[];
}

export const STRENGTHS = ["strong", "medium", "weak"];
const AGENTS: Record<string, string> = {
  claude: "Claude Code", codex: "Codex", antigravity: "Antigravity", git: "plain git", cloud: "cloud branch",
};

function paths(platform: NodeJS.Platform): path.PlatformPath {
  return platform === "win32" ? path.win32 : path.posix;
}

/** A folder in one form for comparing: normalized, no separator at the end, and, on Windows,
 * one case. */
export function key(p: string, platform: NodeJS.Platform = process.platform): string {
  let out = paths(platform).normalize(p);
  while (out.length > 1 && /[\\/]$/.test(out) && !/^[A-Za-z]:[\\/]$/.test(out)) {
    out = out.slice(0, -1);
  }
  return platform === "win32" ? out.toLowerCase() : out;
}

export function samePath(a: string, b: string, platform: NodeJS.Platform = process.platform): boolean {
  return !!a && !!b && key(a, platform) === key(b, platform);
}

/** The repository of status whose common directory is `commonDir`, or null. */
export function repositoryFor(status: Status | null, commonDir: string,
                              platform: NodeJS.Platform = process.platform): Repository | null {
  return status?.repositories.find((r) => samePath(r.common_dir, commonDir, platform)) ?? null;
}

export function liveSessions(repo: Repository): Session[] {
  return (repo.sessions ?? []).filter((s) => (s.state ?? "live") === "live");
}

export function strongOverlaps(repo: Repository): Overlap[] {
  return (repo.overlaps ?? []).filter((o) => o.strength === "strong");
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** The status bar item: the other live sessions on the repository (those not in this worktree)
 * and its strong overlaps. */
export function statusBar(repo: Repository, top: string,
                          platform: NodeJS.Platform = process.platform): { text: string; tooltip: string } {
  const others = liveSessions(repo).filter((s) => !samePath(s.worktree, top, platform)).length;
  const strong = strongOverlaps(repo).length;
  const text = `$(organization) ${plural(others, "other session")}` + (strong ? `, $(warning) ${strong} strong` : "");
  const tooltip = `Orchestraty: ${plural(others, "other live session")} on ${repo.name}, ` +
    (strong ? plural(strong, "strong overlap") : "no strong overlap") + ". Click to open the panel.";
  return { text, tooltip };
}

export function agentName(agent: string): string {
  return AGENTS[agent] ?? agent;
}

function baseName(p: string): string {
  return p.split(/[\\/]/).filter(Boolean).pop() ?? p;
}

/** A session as the panel names it: its agent and its worktree's folder name. */
export function who(repo: Repository, id: string | undefined): string {
  const s = (repo.sessions ?? []).find((x) => x.id === id);
  if (!s) {
    return String(id ?? "").slice(0, 8) || "a session";
  }
  return s.worktree ? `${agentName(s.agent)} in ${baseName(s.worktree)}` : `${agentName(s.agent)} ${s.branch}`;
}

function nameOf(symbol: string): string {
  return symbol.split("::").pop() ?? symbol;
}

function fileOf(o: Overlap): string {
  return o.symbol ? o.symbol.split("::")[0] : o.file;
}

function both(repo: Repository, o: Overlap): string {
  const [a, b] = o.sessions ?? [];
  return b === undefined ? who(repo, a) : `${who(repo, a)} and ${who(repo, b)}`;
}

/** One line for an overlap, from the facts status.json gives. */
export function describe(repo: Repository, o: Overlap): string {
  const d = o.detail ?? {};
  const named = o.symbol ? `${nameOf(o.symbol)} (${fileOf(o)})` : o.file;
  switch (o.rule) {
    case "O3":
    case "O4":
      return `${named} ${o.rule === "O4" ? "removed" : "changed"} by ${who(repo, o.changer)}; ` +
        `${o.file} uses it (${who(repo, o.receiver)})`;
    case "O6": {
      const left = Array.isArray(d.left) ? d.left.length : 0;
      return `${named} changed by ${who(repo, o.changer)}: ${plural(left, "caller")} left behind`;
    }
    case "O5":
      return `the route ${d.method ?? "?"} ${d.path ?? "?"} (${o.file}) changed by ${both(repo, o)}`;
    case "O7": {
      const how: Record<string, string> = d.how ?? {};
      const [a = "", b = ""] = o.sessions ?? [];
      return `${named} ${how[a] ?? "declared"} by ${who(repo, a)} and ${how[b] ?? "declared"} by ${who(repo, b)}`;
    }
    case "W1":
      return `${both(repo, o)} work in one folder`;
    default:
      return `${named} changed by ${both(repo, o)}`;
  }
}

/** Where an overlap's file is: its path in the repository, the line (1-based) and the
 * worktrees to look for it in, in order (this one first). Null for an overlap about no file. */
export function target(repo: Repository, o: Overlap, top: string,
                       platform: NodeJS.Platform = process.platform): { file: string; line: number; worktrees: string[] } | null {
  const d = o.detail ?? {};
  const sessions = repo.sessions ?? [];
  const order = (ids: Array<string | undefined>): string[] => {
    const out = [top];
    for (const s of [...ids.map((id) => sessions.find((x) => x.id === id)), ...sessions]) {
      if (s?.worktree && !out.some((w) => samePath(w, s.worktree, platform))) {
        out.push(s.worktree);
      }
    }
    return out;
  };
  const first = (lines: unknown): number => (Array.isArray(lines) && typeof lines[0] === "number" ? lines[0] : 1);
  switch (o.rule) {
    case "O3":
    case "O4":
      return { file: o.file, line: first(d.uses?.[0]?.lines), worktrees: order([o.receiver, o.changer]) };
    case "O6": {
      const left = d.left?.[0];
      return left ? { file: left.file, line: first(left.lines), worktrees: order([o.changer]) }
                  : { file: o.file, line: 1, worktrees: order([o.changer]) };
    }
    case "O5": {
      const changes = (d.changes ?? {}) as Record<string, { file?: string; line?: number }>;
      const mine = sessions.find((s) => samePath(s.worktree, top, platform) && changes[s.id]);
      const change = changes[mine?.id ?? ""] ?? Object.values(changes)[0];
      return { file: change?.file || o.file, line: change?.line || 1, worktrees: order(o.sessions ?? []) };
    }
    case "W1":
      return null;
    default: {
      if (!o.file) {
        return null;
      }
      const ids = o.sessions ?? [];
      let line = 1;
      for (const s of [...sessions.filter((x) => samePath(x.worktree, top, platform)), ...sessions]) {
        const hit = s.changes?.symbols?.find((x) => x.symbol === o.symbol);
        if (o.symbol && hit) {
          line = first(hit.lines);
          break;
        }
      }
      return { file: o.file, line, worktrees: order(ids) };
    }
  }
}

/** The files, as paths in the repository, that a strong overlap is about: its own file, the
 * changed function's file (O3, O4) and the callers left behind (O6). */
export function affected(o: Overlap): string[] {
  const out = new Set<string>();
  if (o.file) {
    out.add(o.file);
  }
  if ((o.rule === "O3" || o.rule === "O4") && o.symbol) {
    out.add(fileOf(o));
  }
  for (const left of (o.rule === "O6" && Array.isArray(o.detail?.left) ? o.detail.left : [])) {
    if (left?.file) {
      out.add(left.file);
    }
  }
  return [...out];
}

/** The silence rule: the overlaps to notify the person of now, those that are strong, affect
 * a file open in the editor (`open`: paths in the repository) and have not been notified yet.
 * Nothing for medium or weak ones. */
export function toNotify(repo: Repository, open: Set<string>, notified: Set<string>): Overlap[] {
  return strongOverlaps(repo).filter((o) => !notified.has(o.key) && affected(o).some((f) => open.has(f)));
}

/** A path in the repository for a file of this worktree, with "/" between folders; null for a
 * file outside it. */
export function relative(file: string, top: string, platform: NodeJS.Platform = process.platform): string | null {
  const p = paths(platform);
  const rel = p.relative(top, file);
  if (!rel || rel.startsWith("..") || p.isAbsolute(rel)) {
    return null;
  }
  return rel.split(p.sep).join("/");
}

export function ago(seconds: number | undefined): string {
  if (seconds === undefined || seconds < 0) {
    return "";
  }
  if (seconds < 10) {
    return "just now";
  }
  if (seconds < 60) {
    return `${Math.floor(seconds)} s ago`;
  }
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)} min ago`;
  }
  if (seconds < 86400) {
    return `${Math.floor(seconds / 3600)} h ago`;
  }
  return `${Math.floor(seconds / 86400)} d ago`;
}

/** A status.json time as the local hour and minute. */
export function clock(iso: string | undefined): string {
  const t = iso ? new Date(iso) : null;
  if (!t || Number.isNaN(t.getTime())) {
    return "";
  }
  return `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
}

export interface Node {
  id: string;
  label: string;
  description?: string;
  tooltip?: string;
  icon?: string;
  children?: Node[];
  /** An overlap's key: choosing the node opens its file. */
  overlap?: string;
  expanded?: boolean;
}

const ICONS: Record<string, string> = { strong: "warning", medium: "info", weak: "circle-outline" };

function sessionNode(repo: Repository, s: Session, top: string, platform: NodeJS.Platform): Node {
  const here = samePath(s.worktree, top, platform);
  const files = Object.keys(s.changes?.files ?? {}).length;
  const description = [agentName(s.agent), (s.worktree ? baseName(s.worktree) : "") + (here ? " (this worktree)" : ""),
                       ago(s.seconds_since_last_event)].filter(Boolean).join(" · ");
  const tooltip = [`${agentName(s.agent)} session ${s.id}`, `worktree: ${s.worktree || "none here"}`,
                   `branch: ${s.branch || "detached HEAD"}`, `${s.turn || s.state || ""}`.trim(),
                   `last event: ${s.last_event ?? ""} (${ago(s.seconds_since_last_event)})`,
                   `${plural(files, "file")} changed`].filter(Boolean).join("\n");
  return { id: `${repo.common_dir}|session|${s.id}`, label: s.branch || "(detached HEAD)", description, tooltip,
           icon: here ? "account" : "person" };
}

function overlapNode(repo: Repository, o: Overlap): Node {
  const reasons = Array.isArray(o.detail?.reasons) ? o.detail.reasons.join("; ") : "";
  const line = describe(repo, o);
  return { id: `${repo.common_dir}|overlap|${o.key}`, label: line, description: o.rule,
           tooltip: `${o.strength} (${o.rule}): ${line}` + (reasons ? `\n${reasons}` : ""),
           icon: ICONS[o.strength] ?? "circle-outline", overlap: o.key };
}

/** The panel's tree for one repository: its sessions; its overlaps, grouped by strength; and
 * the claims and declarations that hold, when there are any. */
export function tree(repo: Repository, top: string, platform: NodeJS.Platform = process.platform): Node[] {
  const id = (part: string) => `${repo.common_dir}|${part}`;
  const sessions = liveSessions(repo);
  const overlaps = repo.overlaps ?? [];
  const nodes: Node[] = [{
    id: id("sessions"), label: "Sessions", description: String(sessions.length), icon: "organization", expanded: true,
    children: sessions.map((s) => sessionNode(repo, s, top, platform)),
  }];
  const groups = STRENGTHS.map((strength) => ({ strength, items: overlaps.filter((o) => o.strength === strength) }))
    .filter((g) => g.items.length);
  nodes.push({
    id: id("overlaps"), label: "Overlaps", description: overlaps.length ? String(overlaps.length) : "none",
    icon: "git-compare", expanded: true,
    children: groups.map((g) => ({
      id: id(`overlaps|${g.strength}`), label: g.strength[0].toUpperCase() + g.strength.slice(1),
      description: String(g.items.length), icon: ICONS[g.strength], expanded: g.strength === "strong", children: g.items.map((o) => overlapNode(repo, o)),
    })),
  });
  const claims = sessions.flatMap((s) => (s.claims ?? []).map((c) => ({ s, c })));
  if (claims.length) {
    nodes.push({
      id: id("claims"), label: "Claims", description: String(claims.length), icon: "lock",
      children: claims.map(({ s, c }) => ({
        id: id(`claim|${c.id}`), label: `#${c.id} ${c.target}${c.kind === "area" ? "/" : ""}`,
        description: `${c.hard ? "hard claim" : "claim"} by ${who(repo, s.id)}` + (c.until ? ` until ${clock(c.until)}` : ""),
        tooltip: `${c.kind} ${c.target}, ${c.hard ? "hard claim" : "claim"} by ${who(repo, s.id)} until ${c.until ?? ""}` +
          `\norch release ${c.id} ends it`,
        icon: "lock",
      })),
    });
  }
  const declared = sessions.flatMap((s) => (s.declared ?? []).map((d, i) => ({ s, d, i })));
  if (declared.length) {
    nodes.push({
      id: id("declared"), label: "Declarations", description: String(declared.length), icon: "megaphone",
      children: declared.map(({ s, d, i }) => ({
        id: id(`declared|${s.id}|${i}`), label: [...(d.paths ?? []), ...(d.symbols ?? [])].join(", "),
        description: `declared by ${who(repo, s.id)}` + (d.until ? ` until ${clock(d.until)}` : ""),
        tooltip: `declared by ${who(repo, s.id)} until ${d.until ?? ""}` + (d.task ? `\n"${d.task}"` : ""),
        icon: "megaphone",
      })),
    });
  }
  return nodes;
}
