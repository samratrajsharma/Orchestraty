"""Writes the Claude Code plugin's built files from the package, so they never drift from it:

* ``plugins/claude-code/hooks/register.js``: the mod's hooks module, copied from
  ``orchestraty/orchestraty/mod/hooks/`` (a plugin may not reach outside its own folder, so the
  plugin cannot point at the mod where it is);
* ``plugins/claude-code/hooks/hooks.json``: the mod's module list, and a command hook for each
  event orch init installs (``orchestraty.plugin.hooks_file``);
* ``plugins/claude-code/.claude-plugin/plugin.json``: the manifest, with the package's version.

    python scripts/build_plugin.py            # write them
    python scripts/build_plugin.py --check    # change nothing; exit 1 naming any that differ

Run it after changing the mod, the Claude Code events or the version; a test runs --check. The
plugin's other files (``.mcp.json``, the skill) are written by hand. Standard library only.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "orchestraty", "orchestraty", "mod")
PLUGIN = os.path.join(ROOT, "plugins", "claude-code")
DESCRIPTION = ("Keeps parallel coding-agent sessions on one repository aware of each other: the hooks that record "
               "this session's edits and hand it Orchestraty's notes, a status line, a band and a pane (the mod), "
               "the MCP tools to check before an edit, and a skill on how to treat the notes. Needs Orchestraty "
               "installed and orch init run; makes no network requests.")


def _package():
    sys.dont_write_bytecode = True                   # no __pycache__ left in the working copy
    sys.path.insert(0, os.path.join(ROOT, "orchestraty"))
    import orchestraty
    from orchestraty import plugin
    return orchestraty.__version__, plugin


def _json(value):
    return json.dumps(value, indent=2) + "\n"


def semver(version):
    """The package's version as a plugin manifest writes it (semver): 0.2.0.dev0 is 0.2.0-dev.0,
    0.2.0a1 is 0.2.0-a.1; a release such as 0.1.1 is the same in both."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)\.?(dev|a|b|rc)(\d+)", version)
    return f"{match.group(1)}-{match.group(2)}.{match.group(3)}" if match else version


def built():
    """{path under the plugin folder: its text}."""
    version, plugin = _package()
    with open(os.path.join(MOD, "hooks", "hooks.json"), "r", encoding="utf-8") as fh:
        modules = json.load(fh)["modules"]
    files = {"hooks/hooks.json": _json(plugin.hooks_file(modules)),
             ".claude-plugin/plugin.json": _json({"name": plugin.NAME, "version": semver(version),
                                                  "description": DESCRIPTION, "author": {"name": "Orchestraty"}})}
    for module in modules:
        with open(os.path.join(MOD, "hooks", module), "r", encoding="utf-8", newline="") as fh:
            files["hooks/" + os.path.normpath(module).replace(os.sep, "/")] = fh.read()
    return files


def _current(rel):
    try:
        with open(os.path.join(PLUGIN, rel), "r", encoding="utf-8", newline="") as fh:
            return fh.read().replace("\r\n", "\n")
    except FileNotFoundError:
        return None


def differing():
    """The built files whose copy in the plugin folder differs (or is missing)."""
    return sorted(rel for rel, text in built().items() if _current(rel) != text.replace("\r\n", "\n"))


def write():
    for rel, text in built().items():
        path = os.path.join(PLUGIN, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the Claude Code plugin's built files.")
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 if any file differs")
    args = parser.parse_args(argv)
    if args.check:
        stale = differing()
        for rel in stale:
            print(f"plugins/claude-code/{rel} differs from what scripts/build_plugin.py writes")
        return 1 if stale else 0
    write()
    return 0


if __name__ == "__main__":
    sys.exit(main())
