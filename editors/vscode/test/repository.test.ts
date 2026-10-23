// The repository a workspace folder is in, read from git's files, against git's own answer.
import { after, test } from "node:test";
import * as assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { findPlace, real } from "../src/repository";

const scratch = realpathSync.native(mkdtempSync(path.join(os.tmpdir(), "orch-vscode-")));
after(() => rmSync(scratch, { recursive: true, force: true }));

function inRepository(dir: string): boolean {
  for (let d = dir; ; d = path.dirname(d)) {
    if (existsSync(path.join(d, ".git"))) {
      return true;
    }
    if (path.dirname(d) === d) {
      return false;
    }
  }
}

test("a main worktree, a linked one and a folder in no repository", () => {
  const main = path.join(scratch, "made", "repo");
  mkdirSync(path.join(main, ".git", "worktrees", "wt"), { recursive: true });
  mkdirSync(path.join(main, "pkg", "deep"), { recursive: true });
  assert.deepEqual(findPlace(path.join(main, "pkg", "deep")), { top: main, commonDir: path.join(main, ".git") });
  const linked = path.join(scratch, "made", "wt");
  mkdirSync(path.join(linked, "src"), { recursive: true });
  writeFileSync(path.join(linked, ".git"), `gitdir: ${path.join(main, ".git", "worktrees", "wt")}\n`);
  writeFileSync(path.join(main, ".git", "worktrees", "wt", "commondir"), "../..\n");
  assert.deepEqual(findPlace(path.join(linked, "src")), { top: linked, commonDir: path.join(main, ".git") });
  const relativeLink = path.join(scratch, "made", "wt2");
  mkdirSync(relativeLink);
  mkdirSync(path.join(main, ".git", "worktrees", "wt2"));
  writeFileSync(path.join(relativeLink, ".git"), "gitdir: ../repo/.git/worktrees/wt2\n");
  writeFileSync(path.join(main, ".git", "worktrees", "wt2", "commondir"), "../..");
  assert.deepEqual(findPlace(relativeLink), { top: relativeLink, commonDir: path.join(main, ".git") });
  const none = path.join(scratch, "none");
  mkdirSync(none);
  if (!inRepository(scratch)) {
    assert.equal(findPlace(none), null);
  }
  assert.equal(real(path.join(scratch, "missing")), path.join(scratch, "missing"));
});

test("the same answer as git, for a real repository and its worktree", (t) => {
  const git = (cwd: string, ...args: string[]) => spawnSync("git", ["-c", "init.defaultBranch=main", ...args],
    { cwd, encoding: "utf8", windowsHide: true, env: { ...process.env, GIT_CONFIG_GLOBAL: os.devNull, GIT_CONFIG_NOSYSTEM: "1" } });
  const repo = path.join(scratch, "git", "repo");
  mkdirSync(path.join(repo, "sub"), { recursive: true });
  if (git(repo, "init", "-q").status !== 0) {
    t.skip("git is not available here");
    return;
  }
  writeFileSync(path.join(repo, "sub", "a.txt"), "a\n");
  git(repo, "add", "-A");
  git(repo, "-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base");
  const worktree = path.join(scratch, "git", "wt");
  assert.equal(git(repo, "worktree", "add", "-q", worktree, "-b", "other").status, 0);
  for (const folder of [path.join(repo, "sub"), worktree]) {
    const out = git(folder, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir").stdout.split(/\r?\n/);
    const place = findPlace(folder)!;
    assert.equal(real(place.top), real(path.normalize(out[0])));
    assert.equal(real(place.commonDir), real(path.normalize(out[1])));
  }
});
