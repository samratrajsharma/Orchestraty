"""Structural check — fail CI when a change introduces a structural regression.

Compares the working tree against a base ref (default `main`) over the code graph and exits
non-zero if the change:
  * introduces a new import cycle,                       (blocking)
  * removes a public symbol (breaking the API surface), (blocking)
  * spikes a symbol's complexity past a threshold.      (warning; blocking with --strict)

    python -m orch_engine.check --base main
    python -m orch_engine.check --base origin/main --strict

Exit codes: 0 = clean, 1 = regression, 2 = error (e.g. base ref not found).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .config import Config
from .pipeline import build_index
from . import track
from .mcp_server import _cycles_from_edges  # shared directed-cycle detection over import edges


# Test files, by path pattern, never by substring: attestation.py, latest/ and contest.py are
# not test files. The engine keeps its own copy of the effects module's list (same patterns).
_JS = r"(ts|tsx|js|jsx|mjs|cjs|mts|cts)"
TEST_FILE_PATTERNS = (
    ("test_*.py", re.compile(r"^test_.*\.py$")),
    ("*_test.py", re.compile(r"^.+_test\.py$")),
    ("conftest.py", re.compile(r"^conftest\.py$")),
    ("tests.py", re.compile(r"^tests\.py$")),
    ("*.test.<js>", re.compile(r"^.+\.test\." + _JS + r"$")),
    ("*.spec.<js>", re.compile(r"^.+\.spec\." + _JS + r"$")),
)
TEST_FOLDER_PATTERNS = (("__tests__/", "__tests__"), ("tests/", "tests"), ("test/", "test"))


def is_test_path(path: str) -> bool:
    """True when a repo-relative path (forward or back slashes) is a test file by its name
    or folder."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if any(pattern.match(parts[-1]) for _, pattern in TEST_FILE_PATTERNS):
        return True
    return any(folder in parts[:-1] for _, folder in TEST_FOLDER_PATTERNS)


_DUPLICATE = re.compile(r"#\d+")


def _symbol_name(key: str) -> str:
    # key is "<file>::<qualname>"; the symbol's own name is the last dotted segment, without
    # a duplicate definition's #n. A property accessor (Class.x.setter, always three or more
    # segments) is named after its property; a method that is merely called setter is not.
    parts = _DUPLICATE.sub("", key.split("::")[-1]).split(".")
    if len(parts) > 2 and parts[-1] in ("setter", "getter", "deleter"):
        parts = parts[:-1]
    return parts[-1]


def public_removals(delta: dict, head: dict) -> list:
    """The public symbols a diff removed. A moved symbol is in symbols_moved, never removed.
    Duplicate definitions are numbered in source order (f, f#2, f#3), so deleting one of
    them renumbers the rest: an id whose name still has a definition in the same file is a
    duplicate going away, not the name being removed."""
    remaining = {(k.split("::")[0], _DUPLICATE.sub("", k.split("::")[-1])) for k in head.get("symbols", {})}
    return sorted(k for k in delta.get("symbols_removed", [])
                  if _is_public(k) and (k.split("::")[0], _DUPLICATE.sub("", k.split("::")[-1])) not in remaining)


def _is_public(key: str) -> bool:
    name = _symbol_name(key)
    if name.startswith("_"):
        return False
    if is_test_path(key.split("::")[0]) or name.startswith("test_"):
        return False
    return True


def run_checks(idx, base: str, strict: bool = False, complexity_threshold: int = 5) -> dict:
    """Diff the current index against `base` and classify structural regressions."""
    base_fp = track.snapshot(idx.meta.path, base, idx.config)
    head_fp = track.fingerprint(idx, idx.meta.commit)
    delta = track.diff(base_fp, head_fp)

    base_cycles = {frozenset(c) for c in _cycles_from_edges(base_fp.get("imports", []))}
    head_cycles = {frozenset(c) for c in _cycles_from_edges(head_fp.get("imports", []))}
    cycles_introduced = sorted((sorted(c) for c in (head_cycles - base_cycles)), key=len)

    public_removed = public_removals(delta, head_fp)

    complexity_regressions = sorted(
        ({"symbol": c["symbol"], "file": c["file"], "from": c["from"], "to": c["to"]}
         for c in delta.get("complexity_changes", [])
         if (c["to"] - c["from"]) >= complexity_threshold),
        key=lambda c: -(c["to"] - c["from"]),
    )

    blocking = bool(cycles_introduced) or bool(public_removed) or (strict and bool(complexity_regressions))
    return {
        "base": base, "head": head_fp.get("commit", "working-tree"),
        "cycles_introduced": cycles_introduced,
        "public_removed": public_removed,
        "moved": delta.get("symbols_moved", []),
        "complexity_regressions": complexity_regressions,
        "strict": strict,
        "failed": blocking,
    }


def _report(idx, res: dict) -> str:
    L = [f"check: {idx.meta.name}  base={res['base']}  head={res['head']}", ""]
    if res["cycles_introduced"]:
        L.append(f"FAIL  Import cycles introduced ({len(res['cycles_introduced'])}):")
        for c in res["cycles_introduced"]:
            L.append("        " + " -> ".join(c) + " -> " + c[0])
    if res["public_removed"]:
        L.append(f"FAIL  Public symbols removed ({len(res['public_removed'])}):")
        for k in res["public_removed"]:
            L.append(f"        {k}")
    if res["complexity_regressions"]:
        tag = "FAIL" if res["strict"] else "WARN"
        L.append(f"{tag}  Complexity regressions (+{'>='}{5}) ({len(res['complexity_regressions'])}):")
        for c in res["complexity_regressions"]:
            L.append(f"        {c['file']}::{c['symbol']}  {c['from']} -> {c['to']}")
    if res.get("moved"):
        L.append(f"INFO  Symbols moved, not removed ({len(res['moved'])}):")
        for m in res["moved"]:
            L.append(f"        {m['from']} -> {m['to'].split('::')[0]}")
    if not (res["cycles_introduced"] or res["public_removed"] or res["complexity_regressions"]):
        L.append(f"OK — no structural regressions vs {res['base']}.")
    L.append("")
    L.append("Result: " + ("FAIL" if res["failed"] else "PASS"))
    return "\n".join(L)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="python -m orch_engine.check",
        description="Fail (exit non-zero) when a change introduces a structural regression vs a base ref.",
    )
    p.add_argument("--base", default="main", help="git ref to compare against (default: main)")
    p.add_argument("--repo", default=".", help="repo to check (default: current folder)")
    p.add_argument("--strict", action="store_true", help="also fail on complexity regressions")
    p.add_argument("--complexity-threshold", type=int, default=5,
                   help="min complexity increase to flag (default: 5)")
    p.add_argument("--data-dir", default=None, help="index cache dir (default: ~/.orchestraty/cache)")
    args = p.parse_args(argv)

    repo = args.repo
    if repo != "." and "://" not in repo:
        repo = str(Path(repo).resolve())
    cfg = Config(
        data_dir=args.data_dir or os.environ.get("ORCH_DATA_DIR") or str(Path.home() / ".orchestraty" / "cache"),
    )
    try:
        idx = build_index(repo, cfg, progress=lambda m: print(m, file=sys.stderr, flush=True), register=False)
    except Exception as e:  # noqa: BLE001 — not a folder, git refused the repository, etc.
        print(f"check: error — could not index {repo!r}: {e}", file=sys.stderr)
        return 2

    try:
        res = run_checks(idx, args.base, strict=args.strict, complexity_threshold=args.complexity_threshold)
    except Exception as e:  # noqa: BLE001 — base ref missing, not a git repo, etc.
        print(f"check: error — could not compare against {args.base!r}: {e}", file=sys.stderr)
        return 2

    print(_report(idx, res))
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
