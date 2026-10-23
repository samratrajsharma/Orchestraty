// The git repository a workspace folder is in, read from git's own files the way git finds
// them: the nearest .git above the folder, a folder in the main worktree, or a file naming the
// worktree's git folder in a linked one, whose commondir file leads to the folder all the
// worktrees share. That shared folder is how status.json names a repository (common_dir).
import * as fs from "node:fs";
import * as path from "node:path";

export interface Place {
  /** The top of the worktree the folder is in. */
  top: string;
  /** The repository's git common directory, as `git rev-parse --git-common-dir` gives it. */
  commonDir: string;
}

function commonOf(gitDir: string): string {
  try {
    const common = fs.readFileSync(path.join(gitDir, "commondir"), "utf8").trim();
    if (common) {
      return path.resolve(gitDir, common);
    }
  } catch {
    // a main worktree's git folder has no commondir file
  }
  return gitDir;
}

/** The worktree and repository that `folder` is in, or null when it is in none. */
export function findPlace(folder: string): Place | null {
  let dir = path.resolve(folder);
  for (;;) {
    const dotGit = path.join(dir, ".git");
    let stat: fs.Stats | null = null;
    try {
      stat = fs.statSync(dotGit);
    } catch {
      stat = null;
    }
    if (stat?.isDirectory()) {
      return { top: dir, commonDir: commonOf(dotGit) };
    }
    if (stat?.isFile()) {
      const match = /^gitdir:\s*(.+?)\s*$/m.exec(fs.readFileSync(dotGit, "utf8"));
      if (match) {
        return { top: dir, commonDir: commonOf(path.resolve(dir, match[1])) };
      }
    }
    const up = path.dirname(dir);
    if (up === dir) {
      return null;
    }
    dir = up;
  }
}

/** The path with links resolved, where it exists; as given otherwise. */
export function real(p: string): string {
  try {
    return fs.realpathSync.native(p);
  } catch {
    return p;
  }
}
