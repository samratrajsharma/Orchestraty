"""Checks the rules in spikes/README.md that can be checked mechanically: rule 2 (the status
line), rule 3 (import direction) and rule 8 (unique Python file names)."""
import ast
import os
import re
from pathlib import Path

SPIKES = Path(__file__).resolve().parent
ENGINE = SPIKES.parent / "engine"
STATUS_LINE = "Status: throwaway (Phase 0). Not part of the product."
SKIP_DIRS = {"__pycache__", "out", "data"}


def _spike_dirs():
    return sorted(p for p in SPIKES.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP_DIRS)


def _first_text_line(readme):
    for line in readme.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _imports(path):
    """(top-level module names imported, relative-import levels) for one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names, levels = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                levels.add(node.level)
            elif node.module:
                names.add(node.module.split(".")[0])
    return names, levels


def _module_names(folder):
    """Everything another file could import from a spike folder: its name and its modules."""
    names = {folder.name.replace("-", "_")}
    names |= {p.stem for p in _python_files(folder) if p.stem != "conftest"}
    return names


def test_every_readme_starts_with_the_status_line():
    readmes = [SPIKES / "README.md"] + [d / "README.md" for d in _spike_dirs()]
    missing = [str(r.relative_to(SPIKES)) for r in readmes if not r.is_file()]
    assert not missing, f"folders without a README.md: {missing}"
    wrong = [str(r.relative_to(SPIKES)) for r in readmes if _first_text_line(r) != STATUS_LINE]
    assert not wrong, f"README files whose first line is not the status line: {wrong}"


def test_spikes_do_not_import_each_other():
    folders = _spike_dirs()
    problems = []
    for folder in folders:
        others = set()
        for other in folders:
            if other != folder and not (folder.name != "_shared" and other.name == "_shared"):
                others |= _module_names(other)
        for path in _python_files(folder):
            names, levels = _imports(path)
            rel = path.relative_to(SPIKES)
            if names & others:
                problems.append(f"{rel} imports {sorted(names & others)}")
            if "spikes" in names:
                problems.append(f"{rel} imports the spikes package")
            if any(level >= 2 for level in levels):
                problems.append(f"{rel} uses a relative import that leaves its folder")
    assert not problems, problems


def test_engine_never_imports_spikes():
    # A text scan, not ast: the engine needs Python 3.10+, and these tests also run on 3.9.
    if not ENGINE.is_dir():
        return
    forbidden = {"spikes"}
    for folder in _spike_dirs():
        forbidden |= _module_names(folder)
    problems = []
    for path in _python_files(ENGINE):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+(.+))", line)
            if not m:
                continue
            mods = [m.group(1)] if m.group(1) else [p.strip().split()[0] for p in m.group(2).split(",") if p.strip()]
            hits = {mod.split(".")[0] for mod in mods} & forbidden
            if hits:
                problems.append(f"{path.relative_to(ENGINE)} imports {sorted(hits)}")
    assert not problems, problems


def test_python_file_names_are_unique_across_spikes():
    # All spike tests run in one pytest session; two files with the same name would collide.
    seen = {}
    problems = []
    for folder in _spike_dirs():
        for path in _python_files(folder):
            if path.name == "conftest.py":
                continue
            if path.name in seen and seen[path.name] != folder.name:
                problems.append(f"{path.name} is in both {seen[path.name]}/ and {folder.name}/")
            seen.setdefault(path.name, folder.name)
    assert not problems, problems
