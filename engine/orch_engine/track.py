"""Track. Snapshot a repo at commits and diff the structure over time:
changelog, architecture delta, and learning delta. Snapshots read each commit from git's
object store (no worktree, nothing written into the repository) — works on any git repo;
structural by default, with optional LLM narration."""
from __future__ import annotations
import json
import os
import subprocess
from collections import defaultdict

from . import models
from .config import CONFIG
from .ingest import UntrustedRepositoryError, check_ownership, git_command

CODE_LANGS = ("python", "javascript", "typescript")


def _git(args, cwd, timeout=120):
    # git_command adds the defensive options and keeps git's ownership check for the
    #   user's own paths (see ingest.git_command); a refusal raises UntrustedRepositoryError.
    # encoding: git writes UTF-8; decoding with the locale (cp1252 on Windows) crashed
    #   git_log on the first non-ASCII author or subject.
    argv, env = git_command(args, cwd)
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env)
    check_ownership(r.returncode, r.stderr, cwd)
    return r


def git_probe(path):
    """(is_git, reason). Never answers 'not a git repo' when it actually means
    'git blew up and I threw the reason away' - that is indistinguishable to the
    user and sends them chasing the wrong problem."""
    if not path or not os.path.isdir(path):
        return False, "The working copy is not on disk any more: %s" % (path or "(empty path)")
    dotgit = os.path.join(path, ".git")
    has_dotgit = os.path.isdir(dotgit) or os.path.isfile(dotgit)   # worktrees use a file
    try:
        r = _git(["rev-parse", "--is-inside-work-tree"], path, timeout=30)
    except UntrustedRepositoryError:
        raise
    except FileNotFoundError:
        return has_dotgit, "git is not installed, or not on PATH for the backend process."
    except subprocess.TimeoutExpired:
        return has_dotgit, "git did not respond within 30s."
    except Exception as e:
        return has_dotgit, "%s: %s" % (type(e).__name__, e)
    if r.returncode == 0 and r.stdout.strip() == "true":
        return True, ""
    err = (r.stderr or r.stdout or "").strip()
    if has_dotgit:
        # there IS a .git here - so this is a git failure, not a missing repo
        return True, err or "git rev-parse failed with no output."
    return False, err or "No .git directory found in %s" % path


def is_git(path):
    return git_probe(path)[0]


def git_log(path, n=30):
    # No separate is_git() probe: `git log` on a non-repo just fails and yields no
    # lines, so the pre-check was one extra subprocess for the same result.
    r = _git(["log", f"-n{n}", "--pretty=format:%H|%h|%an|%ad|%s", "--date=short"], path)
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5:
            out.append({"sha": parts[0], "short": parts[1], "author": parts[2],
                        "date": parts[3], "subject": parts[4]})
    return out


def _symbol_print(sym):
    """What a fingerprint keeps about one symbol: where it is, and the hashes that tell a
    body change from a signature change (plus the captured signature itself)."""
    entry = {"file": sym.file, "kind": sym.kind, "complexity": sym.complexity,
             "lines": [sym.start_line, sym.end_line],
             "body_hash": sym.body_hash, "sig_hash": sym.sig_hash}
    if sym.signature:
        entry["signature"] = sym.signature
    if sym.overloaded:
        entry["overloaded"] = True
    return entry


def file_fingerprint(pf):
    """The fingerprint of one parsed file, with no graph needed: its module_hash and each
    symbol's entry, keyed by symbol id. So a caller can fingerprint only the files a commit
    changed; fingerprint_text() does it straight from a file's text."""
    return {"file": pf.file, "language": pf.language, "loc": pf.loc,
            "module_hash": pf.module_hash,
            "symbols": {sym.id: _symbol_print(sym) for sym in pf.symbols}}


def fingerprint_text(rel_path, text):
    """file_fingerprint() of one file's text, for example a blob read from git."""
    from .parsing import parse_file
    return file_fingerprint(parse_file(rel_path, None, text=text))


def fingerprint(idx, commit):
    files, modules, symbols = {}, {}, {}
    for p in idx.parsed_files:
        if p.language in CODE_LANGS:
            fp = file_fingerprint(p)
            files[p.file] = p.loc
            modules[p.file] = fp["module_hash"]
            symbols.update(fp["symbols"])
    imports = sorted({(s, d) for s, d, _ in idx.graph.all_edges("imports")})
    calls = sorted({(s, d) for s, d, _ in idx.graph.all_edges("calls")})
    return {"schema": models.ENGINE_SCHEMA, "commit": commit, "files": files, "modules": modules,
            "symbols": symbols, "imports": imports, "calls": calls}


def snapshot(source_path, commit, config=CONFIG, use_cache=None):
    """Build a structural fingerprint of the repo at `commit` (any ref git accepts).

    The commit is read from git's object store: no worktree is created and nothing is
    written into the repository. The ref is resolved to its full SHA first; the JSON cache
    is keyed by that SHA and the engine's schema version, which fix the content, so a moved
    branch, a ref with a slash, a 12-character prefix clash, another repository or another
    engine version can't be served a wrong entry. With use_cache off (config.use_cache by
    default) nothing is read or written."""
    from .ingest import resolve_commit
    from .pipeline import build_index_at
    if use_cache is None:
        use_cache = config.use_cache
    sha = resolve_commit(source_path, commit)
    cache = os.path.join(config.data_dir, "snapshots", "%s-v%d.json" % (sha, models.ENGINE_SCHEMA))
    if use_cache and os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as fh:
                fp = json.load(fh)
            if fp.get("schema") == models.ENGINE_SCHEMA:
                return fp
        except Exception:
            pass
    idx = build_index_at(source_path, sha, config, use_cache=False, register=False)
    fp = fingerprint(idx, sha)
    if use_cache:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump(fp, fh)
        except Exception:
            pass
    return fp


def _qual(key):
    return key.split("::", 1)[1] if "::" in key else key


def _change(before, after):
    """'body', 'signature', 'both' or None for one symbol's two fingerprint entries."""
    body = before.get("body_hash") != after.get("body_hash")
    sig = before.get("sig_hash") != after.get("sig_hash")
    return "both" if body and sig else "body" if body else "signature" if sig else None


def diff(base, head):
    """The structural difference between two fingerprints:
      files and symbols added and removed;
      symbols_moved: a symbol gone from one file and found in another with the same
        qualified name and the same body hash ({"from", "to", "signature_changed"}), never
        reported as removed plus added. Renames are not paired: they stay removed + added;
      symbols_modified, with symbol_changes saying "body", "signature" or "both" for each
        (a moved symbol whose signature changed is listed too);
      modules_changed: files whose module-level code (outside any definition) changed;
      imports and complexity changes."""
    bf, hf = set(base["files"]), set(head["files"])
    bs, hs = base["symbols"], head["symbols"]
    bk, hk = set(bs), set(hs)
    removed, added = bk - hk, hk - bk
    gone, new = defaultdict(list), defaultdict(list)
    for k in removed:
        gone[(_qual(k), bs[k].get("body_hash"))].append(k)
    for k in added:
        new[(_qual(k), hs[k].get("body_hash"))].append(k)
    moved = []
    for key, olds in gone.items():
        news = new.get(key, [])
        if key[1] and len(olds) == 1 and len(news) == 1:        # one-to-one only, never guessed
            moved.append({"from": olds[0], "to": news[0],
                          "signature_changed": bs[olds[0]].get("sig_hash") != hs[news[0]].get("sig_hash")})
    moved.sort(key=lambda m: m["from"])
    removed -= {m["from"] for m in moved}
    added -= {m["to"] for m in moved}
    changes = {k: c for k in (bk & hk) for c in [_change(bs[k], hs[k])] if c}
    changes.update({m["to"]: "signature" for m in moved if m["signature_changed"]})
    complexity = [{"symbol": k.split("::")[-1], "file": bs[k]["file"],
                   "from": bs[k]["complexity"], "to": hs[k]["complexity"]}
                  for k in (bk & hk) if bs[k]["complexity"] != hs[k]["complexity"]]
    bm, hm = base.get("modules", {}), head.get("modules", {})
    bi, hi = {tuple(x) for x in base["imports"]}, {tuple(x) for x in head["imports"]}
    return {
        "files_added": sorted(hf - bf), "files_removed": sorted(bf - hf),
        "symbols_added": sorted(added), "symbols_removed": sorted(removed),
        "symbols_moved": moved,
        "symbols_modified": sorted(changes),
        "symbol_changes": {k: changes[k] for k in sorted(changes)},
        "modules_changed": sorted(f for f in bf & hf if bm.get(f) != hm.get(f)),
        "imports_added": sorted(hi - bi), "imports_removed": sorted(bi - hi),
        "complexity_changes": complexity,
    }


def changelog_text(d):
    lines = []

    def section(title, items, fmt=lambda x: x):
        if items:
            lines.append(f"**{title}** ({len(items)})")
            lines.extend("  - " + fmt(i) for i in items[:25])

    def name(key):
        return key.split("::")[-1] + f"  ({key.split('::')[0]})"

    how = {"body": "body", "signature": "signature", "both": "body and signature"}
    changes = d.get("symbol_changes", {})
    section("Files added", d["files_added"])
    section("Files removed", d["files_removed"])
    section("Symbols added", [name(s) for s in d["symbols_added"]])
    section("Symbols removed", [name(s) for s in d["symbols_removed"]])
    section("Symbols moved", [m["from"].split("::")[-1] + f"  ({m['from'].split('::')[0]} → {m['to'].split('::')[0]})"
                              + ("  [signature changed]" if m["signature_changed"] else "")
                              for m in d.get("symbols_moved", [])])
    section("Symbols modified", [name(s) + (f"  [{how[changes[s]]}]" if s in changes else "")
                                 for s in d["symbols_modified"]])
    section("Module-level code changed", d.get("modules_changed", []))
    section("New imports", d["imports_added"], lambda e: f"{e[0]} → {e[1]}")
    section("Removed imports", d["imports_removed"], lambda e: f"{e[0]} → {e[1]}")
    section("Complexity changes",
            [f"{c['symbol']} ({c['file']}): {c['from']} → {c['to']}" for c in d["complexity_changes"]])
    return "\n".join(lines) if lines else "_No structural changes between these commits._"


def architecture_delta_dot(base, head):
    bi = {tuple(x) for x in base["imports"]}
    hi = {tuple(x) for x in head["imports"]}
    bf, hf = set(base["files"]), set(head["files"])
    nodes = bf | hf
    out = ["digraph G {", "  rankdir=LR;",
           '  node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10];']
    for f in sorted(nodes):
        if f in hf and f not in bf:
            out.append(f'  "{f}" [fillcolor="#DEF7E0" color="#2E8B57" label="{f} (new)"];')
        elif f in bf and f not in hf:
            out.append(f'  "{f}" [fillcolor="#FBE3E3" color="#C0392B" label="{f} (removed)"];')
        else:
            out.append(f'  "{f}" [fillcolor="#EAF1FB" color="#2E75B6"];')
    for s, d in sorted(hi - bi):
        out.append(f'  "{s}" -> "{d}" [color="#2E8B57" penwidth=2 label="+"];')
    for s, d in sorted(bi - hi):
        out.append(f'  "{s}" -> "{d}" [color="#C0392B" style=dashed label="-"];')
    for s, d in sorted(bi & hi):
        out.append(f'  "{s}" -> "{d}" [color="#bbbbbb"];')
    out.append('}')
    return "\n".join(out)


def learning_delta(d, explored):
    explored = set(explored)
    changed_files = set(d["files_added"]) | set(d["files_removed"]) | set(d.get("modules_changed", []))
    for key in d["symbols_modified"] + d["symbols_added"] + d["symbols_removed"]:
        changed_files.add(key.split("::")[0])
    for m in d.get("symbols_moved", []):
        changed_files.update((m["from"].split("::")[0], m["to"].split("::")[0]))
    return {
        "re_learn": sorted(explored & changed_files),     # you studied these and they changed
        "new_files": sorted(set(d["files_added"]) - explored),
        "removed_files": sorted(set(d["files_removed"]) & explored),
    }
