"""Hook-config generator for Codex: puts the capture hooks into the isolated CODEX_HOME.

Status: throwaway (Phase 0). Not part of the product.

    python capture_codex.py [--root <phase-0 folder>] [--python <absolute python>] [--async]
                            [--windows-shell powershell|cmd]

It writes only inside the phase-0 folder, with the same checks as capture_hooks.py:
  * profiles/codex/hooks.json: the hooks, merged into any hooks already there (idempotent);
  * profiles/codex/config.toml: two top-level settings, added only if missing, so that the
    pinned Codex does not check for updates and keeps its state databases in the profile:
        check_for_update_on_startup = false
        sqlite_home = '<the profile folder>'
    Every other byte of the file is kept;
  * shim/capture_shim.py, and the folder spool/codex/.
It never writes the daily profile (~/.codex) or a repository's .codex/ folder, and it never
writes Codex's trust state: Codex runs a new user hook only after the person has reviewed
and trusted it (/hooks), which is recorded in <CODEX_HOME>/config.toml.

Codex runs each hook's `command` through a shell; there is no exec form. On Windows that is
PowerShell by default (so the command starts with PowerShell's call operator), or cmd with
--windows-shell cmd; elsewhere it is sh -c. The hook payload arrives on stdin either way.
Codex allows at most 3 seconds for SessionEnd hooks.
"""
import argparse
import json
import os
import shlex
import sys

import capture_hooks

# Codex events captured (the same as Claude Code's where they exist; Codex has no
# PostToolUseFailure). PreToolUse is left out, as for Claude Code.
EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse", "Stop", "SubagentStop"]
TOOL_EVENTS = {"PostToolUse"}
TIMEOUT_SECONDS = capture_hooks.TIMEOUT_SECONDS
SESSION_END_TIMEOUT = 3


def _ps_quote(value):
    return capture_hooks.ps_quote(value)          # also doubles the typographic single quotes


BOM = chr(0xFEFF)                           # a UTF-8 byte-order mark, as Windows PowerShell 5.1 writes one
CMD_METACHARACTERS = set('&|<>^%!"')


def shell_command(argv, windows_shell="powershell", windows=None):
    """One command line for the shell Codex uses on this system. For cmd, an argument holding
    one of cmd's special characters is refused rather than escaped: cmd's escaping rules are
    easy to get wrong, and the phase-0 paths never need those characters."""
    windows = (os.name == "nt") if windows is None else windows
    if not windows:
        return " ".join(shlex.quote(a) for a in argv)
    if windows_shell == "cmd":
        bad = [a for a in argv if CMD_METACHARACTERS & set(a)]
        if bad:
            raise capture_hooks.Refused("a path holds a character cmd treats specially (one of & | < > ^ % ! \")")
        return " ".join('"%s"' % a if (" " in a or not a) else a for a in argv)
    return "& " + " ".join(_ps_quote(a) if (i == 0 or not a.startswith("-")) else a for i, a in enumerate(argv))


def hook_entry(python, shim, spool, event, use_async, windows_shell="powershell", windows=None):
    command = shell_command([python, "-S", "-I", shim, spool, "codex", event], windows_shell, windows)
    hook = {"type": "command", "command": command,
            "timeout": SESSION_END_TIMEOUT if event == "SessionEnd" else TIMEOUT_SECONDS}
    if use_async and event != "SessionEnd":              # Codex ignores async for SessionEnd
        hook["async"] = True
    entry = {"hooks": [hook]}
    if event in TOOL_EVENTS:
        entry["matcher"] = "*"
    return entry


def merged_hooks(existing, python, shim, spool, use_async, windows_shell="powershell", windows=None):
    """hooks.json content with the capture hooks added; other hooks and the description are kept,
    and earlier capture hooks (same shim) are replaced, so running it twice changes nothing."""
    data = dict(existing) if isinstance(existing, dict) else {}
    hooks = dict(data.get("hooks") or {})

    def ours(entry):
        return any(isinstance(h, dict) and shim in str(h.get("command", "")) for h in entry.get("hooks", []))

    for event in EVENTS:
        kept = [e for e in hooks.get(event, []) if isinstance(e, dict) and not ours(e)]
        hooks[event] = kept + [hook_entry(python, shim, spool, event, use_async, windows_shell, windows)]
    data["hooks"] = hooks
    return data


def merged_config(text, profile):
    """config.toml text with update checks off and the state databases in the profile. Only
    missing top-level lines are added, at the top; the rest is kept byte for byte, so the trust
    state Codex records in this file is never touched. A key already set to another value is
    refused rather than changed."""
    if "'" in profile or "\n" in profile or "\r" in profile:
        raise capture_hooks.Refused("the profile path holds a quote or a line break")
    bom = BOM if text.startswith(BOM) else ""                # kept in place, ahead of the added lines
    text = text[len(bom):]
    wanted = {"check_for_update_on_startup": "false", "sqlite_home": "'" + profile + "'"}
    found = {}
    for line in text.splitlines():
        if line.lstrip().startswith("["):                   # the first table ends the top level
            break
        key, sep, value = line.partition("=")
        if sep and key.strip() in wanted:
            found[key.strip()] = value.strip()
    for key, value in found.items():
        if value != wanted[key]:
            raise capture_hooks.Refused(f"config.toml already sets {key} to something else; change it by hand")
    # The scan reads plain top-level lines only. A wanted key anywhere else (quoted, after a
    # multi-line string or array, or inside a table) is refused rather than risk a duplicate key.
    for key in wanted:
        if key not in found and key in text:
            raise capture_hooks.Refused(f"config.toml mentions {key} in a form this tool cannot read safely; "
                                        "set it by hand")
    return bom + "".join(f"{k} = {v}\n" for k, v in wanted.items() if k not in found) + text


def install(root, python, use_async=False, windows_shell="powershell"):
    if not os.path.isabs(python) or not os.path.isfile(python):
        raise capture_hooks.Refused("--python must be the absolute path of an existing interpreter")
    profile = capture_hooks.checked_target(root, os.path.join("profiles", "codex"))
    hooks_path = capture_hooks.checked_target(root, os.path.join("profiles", "codex", "hooks.json"))
    config_path = capture_hooks.checked_target(root, os.path.join("profiles", "codex", "config.toml"))
    shim_path = capture_hooks.checked_target(root, os.path.join("shim", "capture_shim.py"))
    spool = capture_hooks.checked_target(root, "spool")
    config = ""
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8", newline="") as fh:
            config = fh.read()
    new_config = merged_config(config, profile)             # refuses before anything is written
    for folder in (profile, os.path.dirname(shim_path), os.path.join(spool, "codex")):
        os.makedirs(folder, exist_ok=True)
    if new_config != config:
        capture_hooks._write_atomic(config_path, new_config.encode("utf-8"))
    with open(capture_hooks.SHIM, "rb") as fh:
        capture_hooks._write_atomic(shim_path, fh.read())
    existing = {}
    if os.path.exists(hooks_path):
        with open(hooks_path, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
    data = merged_hooks(existing, python, shim_path, spool, use_async, windows_shell)
    capture_hooks._write_atomic(hooks_path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    return hooks_path


def main(argv=None):
    p = argparse.ArgumentParser(description="Install the capture hooks into the isolated Codex profile.")
    p.add_argument("--root", default=capture_hooks.default_root())
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--async", dest="use_async", action="store_true")
    p.add_argument("--windows-shell", choices=["powershell", "cmd"], default="powershell")
    p.add_argument("--codex", help="the pinned Codex program (default: found under <root>/tools/codex)")
    args = p.parse_args(argv)
    try:
        path = install(args.root, os.path.abspath(args.python), args.use_async, args.windows_shell)
    except capture_hooks.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}")
    program = args.codex or capture_hooks.pinned_program(args.root, "codex")
    if program is None:
        print(f"no pinned Codex under {os.path.join(args.root, 'tools', 'codex')}: install it there first")
        return 0
    powershell, bash = capture_hooks.login_lines("CODEX_HOME", os.path.dirname(path), os.path.abspath(program))
    print("Codex runs new hooks only after they are reviewed and trusted: start the pinned Codex in the")
    print("isolated profile, log in, and approve the hooks under /hooks (nothing stays set afterwards):")
    print(f"  PowerShell:  {powershell}")
    print(f"  bash:        {bash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
