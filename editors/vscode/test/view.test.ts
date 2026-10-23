// The layer that turns Orchestraty's status into what the extension shows, against a
// status.json the coordinator wrote for two scripted sessions (paths shortened to /work), with
// a medium overlap, a weak one and a declaration added.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as path from "node:path";

import { parseStatus } from "../src/state";
import {
  type Overlap, type Repository, affected, ago, clock, describe, key, relative, repositoryFor, samePath, statusBar,
  target, toNotify, tree,
} from "../src/view";

const FIXTURE = path.join(__dirname, "..", "..", "test", "fixtures", "status.json");
const status = parseStatus(readFileSync(FIXTURE, "utf8"));
const repo = status!.repositories[0];
const A = "/work/wt-a";
const B = "/work/wt-b";

function overlap(rule: string): Overlap {
  return repo.overlaps!.find((o) => o.rule === rule)!;
}

test("the fixture is a status the extension reads", () => {
  assert.ok(status);
  assert.equal(repositoryFor(status, "/work/repo/.git", "linux"), repo);
  assert.equal(repositoryFor(status, "/work/repo/.git/", "linux"), repo);
  assert.equal(repositoryFor(status, "/work/other/.git", "linux"), null);
  assert.equal(parseStatus(JSON.stringify({ ...status, schema: 2 })), null);
  assert.equal(parseStatus("{\"schema\": 1}"), null);
  assert.equal(parseStatus("{\"schema\": 1, \"repo"), null);
});

test("folders compare in the system's own way", () => {
  assert.equal(key("C:\\Work\\Repo\\.git\\", "win32"), key("c:/work/repo/.git", "win32"));
  assert.ok(samePath("C:\\Work\\wt-a", "c:\\work\\WT-A", "win32"));
  assert.ok(!samePath("/work/WT-A", "/work/wt-a", "linux"));
  assert.ok(!samePath("", "", "linux"));
  assert.equal(relative("/work/wt-b/pkg/b.py", B, "linux"), "pkg/b.py");
  assert.equal(relative("/work/wt-a/b.py", B, "linux"), null);
  assert.equal(relative("C:\\work\\wt-b\\pkg\\b.py", "C:\\work\\wt-b", "win32"), "pkg/b.py");
});

test("the status bar counts the other live sessions and the strong overlaps", () => {
  assert.deepEqual(statusBar(repo, B, "linux"), {
    text: "$(organization) 1 other session, $(warning) 2 strong",
    tooltip: "Orchestraty: 1 other live session on repo, 2 strong overlaps. Click to open the panel.",
  });
  assert.equal(statusBar(repo, "/work/repo", "linux").text, "$(organization) 2 other sessions, $(warning) 2 strong");
  const quiet: Repository = { ...repo, sessions: repo.sessions!.slice(1), overlaps: [] };
  assert.deepEqual(statusBar(quiet, B, "linux"), {
    text: "$(organization) 0 other sessions",
    tooltip: "Orchestraty: 0 other live sessions on repo, no strong overlap. Click to open the panel.",
  });
});

test("each overlap is described from its facts", () => {
  assert.equal(describe(repo, overlap("O3")), "charge (a.py) changed by Claude Code in wt-a; b.py uses it (Claude Code in wt-b)");
  assert.equal(describe(repo, overlap("O6")), "charge (a.py) changed by Claude Code in wt-a: 1 caller left behind");
  assert.equal(describe(repo, overlap("O2")), "pay (b.py) changed by Claude Code in wt-a and Claude Code in wt-b");
  assert.equal(describe(repo, overlap("O1")), "b.py changed by Claude Code in wt-a and Claude Code in wt-b");
  const w1: Overlap = { key: "W1", rule: "W1", strength: "medium", symbol: "", file: "", sessions: ["smoke-a", "smoke-b"] };
  assert.equal(describe(repo, w1), "Claude Code in wt-a and Claude Code in wt-b work in one folder");
  const o5: Overlap = { key: "O5", rule: "O5", strength: "strong", symbol: "", file: "api.py", sessions: ["smoke-a", "smoke-b"],
                        detail: { method: "GET", path: "/items" } };
  assert.equal(describe(repo, o5), "the route GET /items (api.py) changed by Claude Code in wt-a and Claude Code in wt-b");
  const o7: Overlap = { key: "O7", rule: "O7", strength: "medium", symbol: "", file: "pkg", sessions: ["smoke-a", "smoke-b"],
                        detail: { how: { "smoke-a": "claimed" } } };
  assert.equal(describe(repo, o7), "pkg claimed by Claude Code in wt-a and declared by Claude Code in wt-b");
  assert.equal(describe(repo, { ...overlap("O3"), changer: "gone-session-id" }).split(" changed by ")[1].slice(0, 8), "gone-ses");
});

test("an overlap's file, line and the worktrees to look in, this one first", () => {
  assert.deepEqual(target(repo, overlap("O3"), B, "linux"), { file: "b.py", line: 5, worktrees: [B, A] });
  assert.deepEqual(target(repo, overlap("O3"), A, "linux"), { file: "b.py", line: 5, worktrees: [A, B] });
  assert.deepEqual(target(repo, overlap("O6"), A, "linux"), { file: "b.py", line: 5, worktrees: [A, B] });
  assert.deepEqual(target(repo, overlap("O2"), B, "linux"), { file: "b.py", line: 4, worktrees: [B, A] });
  assert.deepEqual(target(repo, overlap("O1"), B, "linux"), { file: "b.py", line: 1, worktrees: [B, A] });
  const o5: Overlap = { key: "O5", rule: "O5", strength: "strong", symbol: "", file: "api.py", sessions: ["smoke-a", "smoke-b"],
                        detail: { changes: { "smoke-a": { file: "api/routes.py", line: 12 }, "smoke-b": { file: "api.py", line: 3 } } } };
  assert.deepEqual(target(repo, o5, B, "linux"), { file: "api.py", line: 3, worktrees: [B, A] });
  assert.deepEqual(target(repo, o5, A, "linux"), { file: "api/routes.py", line: 12, worktrees: [A, B] });
  assert.equal(target(repo, { ...overlap("O1"), rule: "W1", file: "" }, B, "linux"), null);
});

test("the silence rule: strong overlaps on an open file, once each, nothing weaker", () => {
  const keys = (list: Overlap[]) => list.map((o) => o.rule);
  assert.deepEqual(affected(overlap("O3")).sort(), ["a.py", "b.py"]);
  assert.deepEqual(affected(overlap("O6")).sort(), ["a.py", "b.py"]);
  assert.deepEqual(keys(toNotify(repo, new Set(["b.py"]), new Set())), ["O3", "O6"]);   // not O2 nor O1, on b.py too
  assert.deepEqual(keys(toNotify(repo, new Set(["a.py"]), new Set())), ["O3", "O6"]);
  assert.deepEqual(keys(toNotify(repo, new Set(["c.py"]), new Set())), []);
  assert.deepEqual(keys(toNotify(repo, new Set(), new Set())), []);
  assert.deepEqual(keys(toNotify(repo, new Set(["b.py"]), new Set([overlap("O3").key]))), ["O6"]);
  assert.deepEqual(keys(toNotify(repo, new Set(["b.py"]), new Set([overlap("O3").key, overlap("O6").key]))), []);
});

test("the panel: sessions, overlaps by strength, claims and declarations", () => {
  const nodes = tree(repo, B, "linux");
  assert.deepEqual(nodes.map((n) => [n.label, n.description]),
                   [["Sessions", "2"], ["Overlaps", "4"], ["Claims", "1"], ["Declarations", "1"]]);
  const [sessions, overlaps, claims, declared] = nodes;
  assert.deepEqual(sessions.children!.map((n) => [n.label, n.description]), [
    ["smoke-a", "Claude Code · wt-a · just now"],
    ["smoke-b", "Claude Code · wt-b (this worktree) · just now"],
  ]);
  assert.match(sessions.children![0].tooltip!, /worktree: \/work\/wt-a\nbranch: smoke-a\nworking\nlast event: /);
  assert.deepEqual(overlaps.children!.map((n) => [n.label, n.description, n.expanded]),
                   [["Strong", "2", true], ["Medium", "1", false], ["Weak", "1", false]]);
  const strong = overlaps.children![0].children!;
  assert.deepEqual(strong.map((n) => [n.description, n.overlap]), [["O3", overlap("O3").key], ["O6", overlap("O6").key]]);
  assert.match(strong[0].tooltip!, /^strong \(O3\): charge \(a\.py\) changed by .*\nparameter currency can no longer be passed by position$/);
  assert.equal(claims.children![0].label, "#1 a.py");
  assert.match(claims.children![0].description!, /^claim by Claude Code in wt-a until \d\d:\d\d$/);
  assert.equal(declared.children![0].label, "b.py, b.py::pay");
  assert.match(declared.children![0].tooltip!, /"pass currency by name"$/);
  const ids = nodes.flatMap(function all(n): string[] { return [n.id, ...(n.children ?? []).flatMap(all)]; });
  assert.equal(new Set(ids).size, ids.length);                     // VS Code needs every id unique
  const bare = tree({ ...repo, sessions: repo.sessions!.map((s) => ({ ...s, claims: [], declared: [] })), overlaps: [] }, B, "linux");
  assert.deepEqual(bare.map((n) => [n.label, n.description]), [["Sessions", "2"], ["Overlaps", "none"]]);
});

test("ages and times", () => {
  assert.deepEqual([ago(3), ago(42), ago(125), ago(7300), ago(200000), ago(undefined)],
                   ["just now", "42 s ago", "2 min ago", "2 h ago", "2 d ago", ""]);
  const t = new Date("2026-10-23T15:07:00Z");
  assert.equal(clock("2026-10-23T15:07:00Z"),
               `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`);
  assert.equal(clock("not a time"), "");
});
