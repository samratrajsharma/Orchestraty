"""The fixed "Not analysed" block: what the analysis did not cover.

Status: throwaway (Phase 0). Not part of the product.

    not_analysed_block(changes, index_not_analysed, changed_files) -> Block(fixed, found)
    render_text(block) -> the block as plain text

The fixed items are always there, in the same order, whatever the change: a short brief or
an empty change never reads as "all clear". Below them, the block lists what was found in this
run: changes in languages the classifier doesn't analyse, changed files in those languages,
and the paths the index left out (skipped folders, symlinks, submodules, oversized files),
passed in as {"path", "reason"}. Python 3.9 or later.
"""
import os
from dataclasses import dataclass
from typing import Tuple

from effects_classify import PYTHON

FIXED = (
    ("languages", "Languages other than Python: contract changes, call binding and references in "
                  "them are not analysed. Changed files in those languages are listed below."),
    ("unresolved-callers", "Decorator uses, callbacks and module-level callers: the resolver does not "
                           "link them. They appear only as name-only references, with low confidence."),
    ("inheritance", "Methods reached through a grandparent class, and super() calls: not resolved. "
                    "They appear only as name-only references, with low confidence."),
    ("dynamic", "Dynamic calls: getattr, string dispatch, reflection, monkeypatching and eval."),
    ("module-level", "Code outside functions and classes: changes to module-level statements are "
                     "not classified."),
    ("renames", "Renames and moves: a renamed or moved function shows as one removal and one "
                "addition; the two are not paired."),
    ("wrapping-decorators", "Decorators that may change how a function is called: such changes are "
                            "marked needs review, never breaking or compatible."),
    ("routes", "Routes registered by Django urlpatterns, and JavaScript and TypeScript routes."),
    ("cycles", "Import cycles."),
    ("size-and-entry-points", "Lines of code and entry points outside Python."),
    ("runtime", "Runtime behaviour: no test was run and no type checker was used."),
    ("left-out", "Files the index left out: skipped folders, symlinks, submodules and oversized files, "
                 "listed below."),
)
PYTHON_EXTS = {".py", ".pyi"}
CODE_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".go", ".java", ".rs", ".cs", ".rb",
             ".php", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".kt", ".kts", ".swift", ".scala"}


@dataclass(frozen=True)
class Block:
    fixed: Tuple[Tuple[str, str], ...]
    found: Tuple[Tuple[str, str], ...]          # (path or id, why it is not analysed)


def not_analysed_block(changes=(), index_not_analysed=(), changed_files=()):
    found = []
    for c in changes:
        if c.language != PYTHON:
            found.append((c.id or c.file, f"{c.language} is not analysed"))
    for path in changed_files:
        ext = os.path.splitext(path)[1].lower()
        if ext in CODE_EXTS and ext not in PYTHON_EXTS:
            found.append((path, f"{ext} files are not analysed"))
    for entry in index_not_analysed:
        found.append((entry.get("path") or "(the rest of the repository)", entry.get("reason", "left out")))
    seen, unique = set(), []
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return Block(FIXED, tuple(unique))


def render_text(block):
    lines = ["Not analysed"]
    lines += [f"- {text}" for _, text in block.fixed]
    if block.found:
        lines.append("Found in this change:")
        lines += [f"- {path}: {why}" for path, why in block.found]
    else:
        lines.append("Found in this change: nothing beyond the items above.")
    return "\n".join(lines)
