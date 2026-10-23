"""The one distribution, orchestraty: its wheel holds both packages (orchestraty and the engine,
orch_engine) and the files orch init copies out of it, and nothing it should not.

CI builds the sdist and the wheel (python -m build) and names them in ORCH_WHEEL and ORCH_SDIST,
with ORCH_PACKAGE_REQUIRED set. Without them the test builds a wheel itself from a copy of the
sources, with the setuptools installed here (no download), or is skipped when there is none."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from orchestraty import __version__

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {"orchestraty": ROOT / "orchestraty" / "orchestraty", "orch_engine": ROOT / "engine" / "orch_engine"}
DATA = ["orchestraty/mod/.claude-plugin/plugin.json", "orchestraty/mod/hooks/hooks.json",
        "orchestraty/mod/hooks/register.js", "orchestraty/capture/hook.py"]
# Read now: every test runs in a folder of its own (conftest), where a relative path would not lead.
GIVEN = {name: os.path.abspath(os.environ[name]) for name in ("ORCH_WHEEL", "ORCH_SDIST") if os.environ.get(name)}


def sources():
    """Every .py file of both packages, as the wheel names it."""
    found = set()
    for name, folder in PACKAGES.items():
        for path in folder.rglob("*.py"):
            if "__pycache__" not in path.parts:
                found.add(f"{name}/{path.relative_to(folder).as_posix()}")
    return found


def skip_or_fail(why):
    if os.environ.get("ORCH_PACKAGE_REQUIRED"):
        pytest.fail(why)
    pytest.skip(why)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    if "ORCH_WHEEL" in GIVEN:
        return Path(GIVEN["ORCH_WHEEL"])
    if os.environ.get("ORCH_PACKAGE_REQUIRED"):
        pytest.fail("ORCH_PACKAGE_REQUIRED is set but ORCH_WHEEL names no wheel")
    if not all(importlib.util.find_spec(m) for m in ("pip", "setuptools")):
        pytest.skip("no pip and setuptools here to build the wheel with; CI builds it with python -m build")
    copy = tmp_path_factory.mktemp("source")
    for rel in ("pyproject.toml", "README.md"):
        shutil.copyfile(ROOT / rel, copy / rel)
    for name, folder in PACKAGES.items():
        shutil.copytree(folder, copy / folder.relative_to(ROOT), ignore=shutil.ignore_patterns("__pycache__"))
    out = tmp_path_factory.mktemp("dist")
    proc = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-cache-dir",
                           "--disable-pip-version-check", "--quiet", "--wheel-dir", str(out), str(copy)],
                          capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if proc.returncode != 0:
        last = [line.strip() for line in (proc.stderr or proc.stdout).splitlines() if line.strip()][-1:]
        pytest.skip("the setuptools here could not build the wheel (CI builds it with python -m build): "
                    + "".join(last)[:200])
    [built] = out.glob("*.whl")
    return built


def test_the_wheel_holds_both_packages_and_the_data_files(wheel):
    assert wheel.name == f"orchestraty-{__version__}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
        info = f"orchestraty-{__version__}.dist-info"
        metadata = z.read(f"{info}/METADATA").decode("utf-8")
        entry_points = z.read(f"{info}/entry_points.txt").decode("utf-8")
        tag = z.read(f"{info}/WHEEL").decode("utf-8")
    assert {n.split("/")[0] for n in names} == {"orchestraty", "orch_engine", info}      # no tests, no scripts
    assert sources() <= names
    assert set(DATA) <= names
    assert not [n for n in names if "__pycache__" in n or n.endswith((".pyc", ".test.ts"))]
    headers = [line for line in metadata.split("\n\n", 1)[0].splitlines()]
    assert "Name: orchestraty" in headers and f"Version: {__version__}" in headers
    assert "Requires-Python: >=3.10" in headers
    assert [h for h in headers if h.startswith("Requires-Dist:") and "extra ==" not in h] == []   # nothing at run time
    assert "Provides-Extra: treesitter" in headers
    assert "orch = orchestraty.cli:main" in entry_points
    assert "Tag: py3-none-any" in tag and "Root-Is-Purelib: true" in tag


def test_the_sdist_has_what_the_wheel_is_built_from():
    """CI only: the sdist has what the wheel is built from (python -m build builds the wheel
    from the sdist, so a file it lacked would be missing from the wheel too)."""
    sdist = GIVEN.get("ORCH_SDIST")
    if not sdist:
        skip_or_fail("no sdist named in ORCH_SDIST; CI builds one with python -m build")
    with tarfile.open(sdist) as tar:
        names = {"/".join(n.split("/")[1:]) for n in tar.getnames()}
    assert {"pyproject.toml", "README.md", "PKG-INFO"} <= names
    assert {f"orchestraty/orchestraty/{n[len('orchestraty/'):]}" for n in DATA} <= names
    assert {f"{'orchestraty/' if n.startswith('orchestraty/') else 'engine/'}{n}" for n in sources()} <= names
