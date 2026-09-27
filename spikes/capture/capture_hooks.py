"""Hook-config generator: puts the capture hooks into an isolated Claude Code profile.

Status: throwaway (Phase 0). Not part of the product.

    python capture_hooks.py [--root <phase-0 folder>] [--python <absolute python>] [--async] [--headsup]

It writes only inside the phase-0 folder (%LOCALAPPDATA%\\orch-phase0, or
~/.local/state/orch-phase0 on WSL2 and macOS):
  * profiles/claude/settings.json: the hooks, merged into any settings already there;
  * shim/capture_shim.py: a copy of the shim, read back once after writing (a first read of a
    freshly written file was slow, probably on-access scanning);
  * spool/claude/: the folder the shim appends to;
  * with --headsup, also shim/capture_headsup.py and the folder headsup/, and a PreToolUse
    hook for Edit, Write and MultiEdit that returns the ready-made heads-up for the file
    (capture_headsup.py; never async, since its output must reach the agent).
It never writes to the daily profile (~/.claude) or to a repository's .claude/ or .codex/
folder: any target outside <root>/profiles, shim, spool and headsup, or reached through a
link, is refused. Claude
Code does not pass CLAUDE_CONFIG_DIR to hooks, so the shim gets its spool folder as an
argument.

Each hook is in exec form (no shell): `command` is the absolute path to Python and `args` is
["-S", "-I", <shim>, <spool>, "claude", <event>].
"""
import argparse
import json
import os
import sys

ROOT_NAME = "orch-phase0"
SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture_shim.py")
HEADSUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture_headsup.py")
HEADSUP_MATCHER = "Edit|Write|MultiEdit"
WRITABLE = ("profiles", "shim", "spool", "headsup")
# Claude Code events captured. PreToolUse is the pre-edit heads-up read (--headsup).
EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
          "Stop", "SubagentStop"]
TOOL_EVENTS = {"PostToolUse", "PostToolUseFailure"}          # take a tool matcher
TIMEOUT_SECONDS = 10


class Refused(Exception):
    """A target outside the phase-0 profiles folder, or reached through a link."""


def default_root():
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), ROOT_NAME)
    return os.path.join(os.path.expanduser("~"), ".local", "state", ROOT_NAME)


def _is_link(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    return (st.st_mode & 0o170000) == 0o120000 or bool(getattr(st, "st_reparse_tag", 0) & 0x20000000)


def checked_target(root, relative):
    """The absolute path of `relative` inside `root`, or Refused. The root folder must be named
    orch-phase0; the target must lie under <root>/profiles, shim, spool or headsup; and no
    existing part of the path (root included) may be a link, so nothing can lead outside."""
    root = os.path.abspath(root)
    if os.path.basename(root) != ROOT_NAME:
        raise Refused(f"the phase-0 folder must be named {ROOT_NAME}")
    target = os.path.abspath(os.path.join(root, relative))
    parts = os.path.relpath(target, root).split(os.sep)
    if parts[0] not in WRITABLE or ".." in parts:
        raise Refused("only <root>/profiles, shim, spool and headsup are written")
    probe = root
    for part in [""] + parts:
        probe = os.path.join(probe, part) if part else probe
        if _is_link(probe):
            raise Refused("a part of the target path is a link")
    real_root = os.path.normcase(os.path.realpath(root))
    if not os.path.normcase(os.path.realpath(target)).startswith(real_root + os.sep):
        raise Refused("the target resolves outside the phase-0 folder")
    return target


def hook_entry(python, shim, spool, event, use_async):
    hook = {"type": "command", "command": python, "args": ["-S", "-I", shim, spool, "claude", event],
            "timeout": TIMEOUT_SECONDS}
    if use_async:
        hook["async"] = True
    entry = {"hooks": [hook]}
    if event in TOOL_EVENTS:
        entry["matcher"] = "*"
    return entry


def headsup_entry(python, headsup_script, headsup_dir):
    hook = {"type": "command", "command": python, "args": ["-S", "-I", headsup_script, headsup_dir],
            "timeout": TIMEOUT_SECONDS}
    return {"matcher": HEADSUP_MATCHER, "hooks": [hook]}


def merged_settings(existing, python, shim, spool, use_async, headsup=None):
    """Settings with the capture hooks added. Other settings and other hooks are kept;
    earlier capture hooks (same shim) are replaced, so running it twice changes nothing."""
    settings = dict(existing) if isinstance(existing, dict) else {}
    hooks = dict(settings.get("hooks") or {})

    def ours(entry):
        ours_args = {shim} | ({headsup[0]} if headsup else set())
        return any(isinstance(h, dict) and ours_args & set(h.get("args") or []) for h in entry.get("hooks", []))

    for event in EVENTS:
        kept = [e for e in hooks.get(event, []) if isinstance(e, dict) and not ours(e)]
        hooks[event] = kept + [hook_entry(python, shim, spool, event, use_async)]
    if headsup:
        kept = [e for e in hooks.get("PreToolUse", []) if isinstance(e, dict) and not ours(e)]
        hooks["PreToolUse"] = kept + [headsup_entry(python, headsup[0], headsup[1])]
    settings["hooks"] = hooks
    return settings


def _write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    with open(path, "rb") as fh:              # read back once, off the hot path
        fh.read()


def install(root, python, use_async=False, headsup=False):
    """Install the shim and the hooks. Returns the settings path written."""
    if not os.path.isabs(python) or not os.path.isfile(python):
        raise Refused("--python must be the absolute path of an existing interpreter")
    if os.name == "nt" and not python.lower().endswith(".exe"):
        raise Refused("exec form on Windows needs a real .exe")
    profile = checked_target(root, os.path.join("profiles", "claude"))
    settings_path = checked_target(root, os.path.join("profiles", "claude", "settings.json"))
    shim_path = checked_target(root, os.path.join("shim", "capture_shim.py"))
    spool = checked_target(root, "spool")
    checked_target(root, os.path.join("spool", "claude"))
    for folder in (profile, os.path.dirname(shim_path), os.path.join(spool, "claude")):
        os.makedirs(folder, exist_ok=True)
    with open(SHIM, "rb") as fh:
        _write_atomic(shim_path, fh.read())
    headsup_paths = None
    if headsup:
        script = checked_target(root, os.path.join("shim", "capture_headsup.py"))
        folder = checked_target(root, "headsup")
        os.makedirs(folder, exist_ok=True)
        with open(HEADSUP, "rb") as fh:
            _write_atomic(script, fh.read())
        headsup_paths = (script, folder)
    existing = {}
    if os.path.exists(settings_path):
        with open(settings_path, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
    settings = merged_settings(existing, python, shim_path, spool, use_async, headsup_paths)
    _write_atomic(settings_path, (json.dumps(settings, indent=2) + "\n").encode("utf-8"))
    return settings_path


def main(argv=None):
    p = argparse.ArgumentParser(description="Install the capture hooks into the isolated Claude Code profile.")
    p.add_argument("--root", default=default_root(), help=f"the phase-0 folder (default: {default_root()})")
    p.add_argument("--python", default=sys.executable, help="absolute path of the Python the hooks run")
    p.add_argument("--async", dest="use_async", action="store_true", help="mark every captured hook async")
    p.add_argument("--headsup", action="store_true", help="also install the pre-edit heads-up hook")
    args = p.parse_args(argv)
    try:
        settings_path = install(args.root, os.path.abspath(args.python) if args.python else args.python,
                                args.use_async, args.headsup)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    profile = os.path.dirname(settings_path)
    print(f"wrote {settings_path}")
    print("log in to the isolated profile with:")
    print(f"  PowerShell:  $env:CLAUDE_CONFIG_DIR = '{profile}'; claude")
    print(f"  bash:        CLAUDE_CONFIG_DIR='{profile}' claude")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
