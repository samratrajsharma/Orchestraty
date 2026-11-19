"""Checks the package's layout rules: every subpackage says what it is for, and imports
only flow from this package to the engine."""
import ast
import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE_ROOT / "orchestraty"
REPO = PACKAGE_ROOT.parent
ENGINE = REPO / "engine"


def _python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _imported_top_levels(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def test_every_subpackage_has_a_docstring():
    subpackages = [p for p in SOURCE.iterdir() if p.is_dir() and (p / "__init__.py").is_file()]
    assert subpackages
    for package in subpackages:
        init = package / "__init__.py"
        doc = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8")))
        assert doc, f"{package.name} has no docstring saying what it is for"


def test_the_engine_never_imports_this_package():
    if not ENGINE.is_dir():
        return
    offenders = [str(p.relative_to(REPO)) for p in _python_files(ENGINE / "orch_engine")
                 if "orchestraty" in _imported_top_levels(p)]
    assert not offenders


NETWORKING = {"socket", "urllib", "http"}


def _imported_modules(path):
    """Every module a file imports, by its full name (relative imports made absolute within the package)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = ".".join(path.relative_to(SOURCE.parent).with_suffix("").parts[:-1])
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.split(".")[:len(package.split(".")) - node.level + 1]
                base = ".".join(parent + ([base] if base else []))
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def test_team_modules_import_no_networking():
    """Team code talks to the network only through git, run as a separate process."""
    team = SOURCE / "team"
    offenders = sorted(f"{p.relative_to(REPO)}: {name}" for p in (_python_files(team) if team.is_dir() else ())
                       for name in _imported_modules(p) if name.split(".")[0] in NETWORKING)
    assert not offenders


def test_the_hook_imports_no_team_module():
    """The hook script stays a quick append: no team code is ever loaded into it."""
    hook = SOURCE / "capture" / "hook.py"
    assert not {name for name in _imported_modules(hook)
                if name == "orchestraty.team" or name.startswith("orchestraty.team.") or name.split(".")[0] == "team"}
