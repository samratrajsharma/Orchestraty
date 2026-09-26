"""Builds the single file that participants receive.

Status: throwaway (Phase 0). Not part of the product.

Joins scan_collect.py and scan_cli.py into one readable Python file, so a
participant can read all of it before running it. Usage:

    python scan_bundle.py --out out/orch-scan.py

The default output folder, out/, is gitignored. The build also prints the
file's SHA-256, which is recorded when a build is sent out.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FUTURE = "from __future__ import annotations"
CLI_IMPORT = "import scan_collect as sc  # bundle: replaced by the collectors themselves"

HEADER = '''#!/usr/bin/env python3
"""Coding agent setup scanner (read-only), single-file build.

Private, pre-release work. Please do not pass this file on.

Run it with:  python orch-scan.py     (or python3 on macOS and Linux)

It shows what it will do, asks before scanning, shows the full result, and
saves one file only if you agree. It never opens session files, never sends
anything over the network, and changes nothing on your machine.

Built from spikes/scan/scan_collect.py and spikes/scan/scan_cli.py. Do not edit.
"""
'''


def _collect_part(src):
    lines = src.splitlines()
    # Drop the developer entry point at the end: it must not run in the bundle.
    for i, line in enumerate(lines):
        if line.startswith('if __name__ == "__main__":'):
            lines = lines[:i]
            break
    return "\n".join(line for line in lines if line.strip() != FUTURE).rstrip() + "\n"


def _cli_part(src):
    if CLI_IMPORT not in src:
        raise SystemExit("scan_cli.py no longer has the import line scan_bundle.py replaces")
    body = src.replace(CLI_IMPORT, "sc = sys.modules[__name__]  # the collectors are defined above")
    return "\n".join(line for line in body.splitlines() if line.strip() != FUTURE).rstrip() + "\n"


def build(out_path):
    collect_src = (HERE / "scan_collect.py").read_text(encoding="utf-8")
    cli_src = (HERE / "scan_cli.py").read_text(encoding="utf-8")
    text = (HEADER + FUTURE + "\n\n"
            + "# " + "-" * 70 + "\n# Collectors (from scan_collect.py)\n# " + "-" * 70 + "\n"
            + _collect_part(collect_src) + "\n\n"
            + "# " + "-" * 70 + "\n# Consent screen, questions and output (from scan_cli.py)\n# "
            + "-" * 70 + "\n" + _cli_part(cli_src))
    if re.search(r"^\s*import scan_collect\b", text, re.MULTILINE):
        raise SystemExit("bundle still imports scan_collect")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:   # Path.write_text(newline=) needs 3.10
        fh.write(text)
    return hashlib.sha256(out_path.read_bytes()).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the single-file scanner.")
    p.add_argument("--out", default=str(HERE / "out" / "orch-scan.py"))
    args = p.parse_args(argv)
    digest = build(args.out)
    print(f"built {args.out}")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
