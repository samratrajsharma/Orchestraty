"""Builds overlap-history.pyz, the one file a volunteer runs: this folder's modules (tests left
out), the engine and the product package, with history_cli as the entry point.

    python history_build.py [--out FOLDER]

Run it with Python 3.10 or later: python overlap-history.pyz
"""
import argparse
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
NAME = "overlap-history.pyz"
MAIN = "import sys\nimport history_cli\nsys.exit(history_cli.main())\n"
PACKAGES = ((os.path.join(REPO, "engine"), "orch_engine"), (os.path.join(REPO, "orchestraty"), "orchestraty"))
LEAVE_OUT = ("tests", "__pycache__", "mod")             # the product's tests and its Claude Code mod


def files():
    """[(path on disk, name in the archive)]: Python files only."""
    out = [(os.path.join(HERE, n), n) for n in sorted(os.listdir(HERE))
           if n.startswith("history_") and n.endswith(".py")]
    for base, package in PACKAGES:
        for dirpath, dirnames, filenames in os.walk(os.path.join(base, package)):
            dirnames[:] = sorted(d for d in dirnames if d not in LEAVE_OUT)
            for n in sorted(filenames):
                if n.endswith(".py"):
                    path = os.path.join(dirpath, n)
                    out.append((path, os.path.relpath(path, base).replace(os.sep, "/")))
    return out


def build(out_dir):
    target = os.path.join(out_dir, NAME)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", MAIN)
        for path, name in files():
            z.write(path, name)
    return target


def main(argv=None):
    p = argparse.ArgumentParser(description="Build " + NAME)
    p.add_argument("--out", default=".")
    args = p.parse_args(argv)
    print("wrote " + build(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
