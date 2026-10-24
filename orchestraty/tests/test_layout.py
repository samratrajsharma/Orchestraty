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
