"""``orch uninstall``: takes out everything Orchestraty added, then, after asking, its state folder.

It first works out what there is to take out, prints it, and asks once; then it does it. What
it looks at:

* **Agent config files**: Claude Code's settings (the file ``orch init`` used, the user's own,
  and ``.claude/settings.local.json`` in every worktree of a repository Orchestraty watches or
  watched), Codex's ``hooks.json``, Antigravity's (the user's, and ``.agents/hooks.json`` in
  those worktrees), every file ``orch init`` recorded writing (``config.json``'s ``installed``)
  and every file a backup in the state folder was taken of. Orchestraty's entries come out: the
  hooks that run its hook script, the wake hook, and the mod's folder in
  ``CLAUDE_CODE_PLUGIN_DIRS``. Everything else in the file stays.

  The backups ``orch init`` made are put back where that loses nothing: when the file without
  Orchestraty's entries is what it was before ``orch init`` first changed it, the copy from
  then is put back as it was, byte for byte. When it changed since, the copy is put back only
  if the user says so (never with ``--yes``); else only Orchestraty's entries come out. A file
  ``orch init`` created that holds nothing else is deleted.
* **.git/info/exclude**: the lines ``orch init --project`` added for a workspace's Claude Code
  settings file or Antigravity hooks file, every one since the first ``orch init``. When nothing
  else in the file changed, the copy from before is put back byte for byte; a file ``orch init``
  made is deleted.
* **Claude Code's Orchestraty plugin**: its hooks are turned off (``plugin.txt``). The plugin
  was installed with Claude Code's own command, and is uninstalled with it, when that command
  is on the PATH; else the command to run is shown.
* **MCP**: Orchestraty registers its server with no agent itself (``orch mcp --config`` only
  prints how), so there is no registration of its own to take out; it says how to remove one
  made by hand.
* **git**: the hook chain (``orch-hooks``) and ``core.hooksPath``, in every repository watched
  or ever recorded.
* **Cloud branches**: the shadows in the state folder (and, with ``--keep-state``, each
  repository's cloud branches turned off).
* **The coordinator**: stopped. Then the state folder (the record, the backups, the settings)
  is removed after asking, or kept with ``--keep-state``. It is kept, too, when a step could not
  be done, the coordinator did not stop (the folder is not removed from under it), or an agent's
  config file still runs its hook script (one that could not be written, or a link): those
  hooks would fail without it, and a second run needs its backups. The exit code is then 1,
  with ``--keep-state`` too.

A second run finds nothing left and changes nothing. Before a file is changed or deleted, a
copy goes into the state folder's backups, as with ``orch init``; those copies go with the
folder when it is removed.
"""
import json
import os
import shutil
import subprocess
import time
from collections import namedtuple

from orchestraty import config, gitio, plugin
from orchestraty.adapters import antigravity, claude, codex
from orchestraty.capture import githooks
from orchestraty.capture import install as hooks
from orchestraty.coordinator.lock import Lock, touch

ADAPTERS = {"claude": claude, "codex": codex, "antigravity": antigravity}
TITLES = {"claude": claude.TITLE, "codex": codex.TITLE, "antigravity": antigravity.TITLE}
EXCLUDE_SUFFIX = ("/.agents/hooks.json", "/.claude/settings.local.json")   # the lines orch init adds to exclude
STOP_SECONDS = 30.0
NOWHERE = os.path.join(os.sep, "\u2205", "\u2205")   # a hook script and a mod folder no config names
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UNFINISHED = "orch uninstall did not finish (see above). Once that is fixed, run orch uninstall again."

# One thing to do: what the plan says, and how (`kind` and its fields).
Step = namedtuple("Step", "kind text path data extra")


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _key(path):
    return os.path.normcase(os.path.abspath(path))


# ------------------------------------------------------------------------------ where to look
def backups(state):
    """[(backup folder, source path, reason)], oldest first."""
    try:
        names = sorted(os.listdir(state.backups))
    except OSError:
        return []
    out = []
    for name in names:
        folder = os.path.join(state.backups, name)
        try:
            with open(os.path.join(folder, hooks.BACKUP_SOURCE), "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        if len(lines) >= 2 and os.path.isfile(os.path.join(folder, hooks.BACKUP_NAME)):
            out.append((folder, lines[0], lines[1]))
    return out


def repositories(state, cfg):
    """The git common folders of every repository watched now or recorded before."""
    found = list(cfg["repos"])
    if os.path.exists(state.record):
        from orchestraty.coordinator.record import Record
        try:
            record = Record(state.record, readonly=True)
            try:
                found += [r["common_dir"] for r in record.repos()]
            finally:
                record.close()
        except Exception:                        # a record it cannot read: the watched ones still count
            pass
    unique = []
    for common in found:
        if isinstance(common, str) and os.path.isdir(common) and not any(_same(common, u) for u in unique):
            unique.append(os.path.normpath(common))
    return unique


def worktrees(common_dir):
    """The tops of a repository's worktrees that exist, the main one first."""
    tops = []
    try:
        out = gitio.run(["worktree", "list", "--porcelain"], common_dir)
        tops = [line[len("worktree "):] for line in out.splitlines() if line.startswith("worktree ")]
    except (gitio.GitError, gitio.UntrustedRepositoryError):
        if os.path.basename(os.path.normpath(common_dir)) == ".git":
            tops = [os.path.dirname(os.path.normpath(common_dir))]
    return [os.path.normpath(t) for t in tops if os.path.isdir(t)]


def kind_of(path):
    """Which agent's config file a path is, by where it is; None when it is none of them."""
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    name = os.path.basename(path)
    if name in ("settings.json", "settings.local.json") and parent == ".claude":
        return "claude"
    if name == "hooks.json" and parent == ".codex":
        return "codex"
    if name == "hooks.json" and (parent == ".agents" or ".gemini" in os.path.abspath(path).split(os.sep)):
        return "antigravity"
    if name == "exclude" and parent == "info":
        return "exclude"
    return None


def candidates(state, cfg, environ=None):
    """{path key: (path, kind)} for every file to look at."""
    found = {}

    def add(path, kind):
        if path and kind and _key(path) not in found:
            found[_key(path)] = (os.path.abspath(path), kind)
    if cfg["settings"]:
        add(cfg["settings"], "claude")
    add(claude.settings_path(environ=environ), "claude")
    add(codex.config_path(environ=environ), "codex")
    add(antigravity.config_path(), "antigravity")
    for entry in cfg.get("installed", []):
        if isinstance(entry, dict) and entry.get("agent") in ADAPTERS and isinstance(entry.get("path"), str):
            add(entry["path"], entry["agent"])
    for common in repositories(state, cfg):
        add(os.path.join(common, "info", "exclude"), "exclude")
        for top in worktrees(common):
            add(os.path.join(top, ".claude", "settings.local.json"), "claude")
            add(os.path.join(top, ".agents", "hooks.json"), "antigravity")
    for _, source, _ in backups(state):
        add(source, kind_of(source))
    return found


# ------------------------------------------------------------------------------ agent files
def cleaned(kind, data, state):
    """The config without Orchestraty's entries (and, for Claude Code, without its mod, and
    without the hooks and the mod of another state folder)."""
    if kind == "claude":
        return claude.without_orchestraty(data, state.hook_script, state.mod)
    return ADAPTERS[kind].without_orchestraty(data, state.hook_script)


def tidied(kind, data):
    """The config as `cleaned` leaves it when nothing in it is Orchestraty's: without an empty
    hooks object or an empty list of plugin folders, which the cleaning drops too."""
    if kind == "antigravity":
        return data
    out = ADAPTERS[kind].without_ours(data, NOWHERE)
    return claude.without_mod(out, NOWHERE) if kind == "claude" else out


def install_copies(state, path):
    """[(backup folder, its bytes)] of the copies of `path` taken before an orch init changed
    it, oldest first."""
    out = []
    for folder, source, reason in backups(state):
        if reason == "install" and _same(source, path):
            with open(os.path.join(folder, hooks.BACKUP_NAME), "rb") as fh:
                out.append((folder, fh.read()))
    return out


def original(state, path, kind):
    """(backup folder, its bytes) of the file as it was before orch init first changed it: the
    newest backup taken before an install that holds none of Orchestraty's entries; or None."""
    for folder, data in reversed(install_copies(state, path)):
        try:
            parsed = json.loads(data.decode("utf-8-sig") or "{}")
        except ValueError:
            continue
        if isinstance(parsed, dict) and cleaned(kind, parsed, state) == tidied(kind, parsed):
            return folder, data
    return None


def _parse(data):
    return json.loads(data.decode("utf-8-sig") or "{}")


def agent_step(state, path, kind):
    """The step for one agent config file, or None when it holds nothing of Orchestraty's."""
    if not os.path.exists(path):
        return None
    title = TITLES[kind]
    if hooks._is_link(path) or hooks._is_link(os.path.dirname(path)):
        return Step("note", f"{title}: {path} is a link, which orch uninstall does not replace: take Orchestraty's "
                            "entries out of the file it points to yourself", path, None, None)
    try:
        current = hooks.load_settings(path)
    except hooks.Refused as exc:
        return Step("note", f"{title}: {exc}; take Orchestraty's entries out of it yourself", path, None, None)
    wanted = cleaned(kind, current, state)
    if wanted == tidied(kind, current):
        return None
    others = ADAPTERS[kind].other_states(current, state.hook_script)
    if others:
        title += (f" (with the hooks of the state folder {', '.join(others)}, which an orch init with another "
                  "ORCH_HOME added)")
    before = original(state, path, kind)
    if before and tidied(kind, _parse(before[1])) == wanted:
        return Step("restore", f"{title}: put back {path} as it was before orch init (it holds nothing else of "
                               "Orchestraty's since)", path, before[1], before[0])
    if before:
        return Step("ask", f"{title}: take Orchestraty's entries out of {path}; it changed since orch init, so the "
                           f"copy from before (in {before[0]}) is put back only if you say so", path, wanted, before)
    if not wanted:
        return Step("delete", f"{title}: delete {path}, which orch init made and which holds nothing else", path,
                    None, None)
    return Step("write", f"{title}: take Orchestraty's entries out of {path} (everything else in it stays)", path,
                wanted, None)


def exclude_step(state, path):
    """The step for a .git/info/exclude with the lines orch init added, or None. Its lines are
    the ones not in the oldest copy, the file before any orch init. When nothing else changed
    since a copy was taken, the file goes back to that copy's bytes; one left with nothing in
    it (orch init made it: its copy is empty) is deleted."""
    try:
        with open(path, "rb") as fh:
            current = fh.read()
    except OSError:
        return None
    copies = install_copies(state, path)
    kept = set(copies[0][1].decode("utf-8", "replace").splitlines()) if copies else set()
    lines = current.decode("utf-8", "replace").splitlines(keepends=True)
    ours = [line for line in lines if line.strip().startswith("/") and line.strip().endswith(EXCLUDE_SUFFIX)
            and line.rstrip("\r\n") not in kept]
    if not ours:
        return None
    added = "".join(ours).encode("utf-8")
    back = next((data for _, data in copies         # orch init adds a newline first when the file ends without one
                 if current == data + (b"\n" if data and not data.endswith(b"\n") else b"") + added), None)
    if back is None:
        back = "".join(line for line in lines if line not in ours).encode("utf-8")
    if not back:
        return Step("delete", f"git: delete {path}, which orch init made and which holds nothing else", path,
                    None, None)
    return Step("exclude", f"git: take the line{'s' if len(ours) > 1 else ''} orch init added "
                           f"({', '.join(line.strip() for line in ours)}) out of {path}", path, back, None)


# ------------------------------------------------------------------------------ the plan
def _claude_folder(cfg):
    path = cfg["settings"] or claude.settings_path()
    return os.path.dirname(path) if os.path.basename(path) == "settings.json" else None


def plan(state, environ=None, which=shutil.which):
    """(steps, notes): what there is to do, and what is left as it is (with why)."""
    cfg = config.load(state)
    steps, notes = [], []
    if plugin.read_switch(state) is not None:
        steps.append(Step("switch", f"Claude Code: turn off the hooks of Orchestraty's plugin "
                                    f"({plugin.switch_path(state)})", None, None, None))
    for path, kind in candidates(state, cfg, environ).values():
        step = exclude_step(state, path) if kind == "exclude" else agent_step(state, path, kind)
        if step and step.kind == "note":
            notes.append(step.text)
        elif step:
            steps.append(step)
    for common in repositories(state, cfg):
        tops = worktrees(common)
        if tops and (os.path.isdir(os.path.join(common, githooks.CHAIN_DIR)) or _chain_lost(tops[0])):
            steps.append(Step("githooks", f"git: take the push line out of the hooks of {tops[0]} (core.hooksPath "
                              "goes back to what it was)", tops[0], None, None))
    if hook_alive(state):
        steps.append(Step("stop", "stop the coordinator", None, None, None))
    exe = which("claude")
    for pid, record in plugin.installs(_claude_folder(cfg), environ):
        scope = record.get("scope") if isinstance(record.get("scope"), str) else "user"
        argv = ["claude", "plugin", "uninstall", pid, "--scope", scope]
        where = record.get("projectPath") if scope in ("project", "local") else None
        if exe:
            steps.append(Step("plugin", f"Claude Code: uninstall its Orchestraty plugin ({pid}, {scope} scope) with "
                                        f"{' '.join(argv)}", where, [exe] + argv[1:], None))
        else:
            notes.append(f"Claude Code's Orchestraty plugin ({pid}, {scope} scope) stays installed: there is no claude "
                         f"command here to uninstall it with. In Claude Code, run /plugin uninstall {pid}.")
    shadows = os.path.join(state.root, "shadows")
    if os.path.isdir(shadows):
        steps.append(Step("shadows", f"cloud branches: remove their shadows ({shadows})", shadows, None, None))
    notes.append("MCP: Orchestraty registered its server with no agent itself (orch mcp --config only prints how). "
                 "A registration you made by hand stays: claude mcp remove orchestraty, codex mcp remove orchestraty, "
                 "or Antigravity's MCP servers panel.")
    return steps, notes


def _chain_lost(top):
    """Whether core.hooksPath still names the push line's folder there, which is gone: git then
    runs none of the repository's hooks, so it is taken out too."""
    try:
        return githooks.lost(top)
    except (gitio.GitError, gitio.UntrustedRepositoryError, OSError):
        return False


def still_hooked(state, environ=None):
    """The agent config files that still run this state folder's hook script, or load its mod:
    removing the folder would make their hooks fail (a link orch uninstall leaves alone, or a
    file it could not write)."""
    left = []
    for path, kind in candidates(state, config.load(state), environ).values():
        if kind not in ADAPTERS or not os.path.exists(path):
            continue
        try:
            current = hooks.load_settings(path)
        except hooks.Refused:                       # the agent cannot read it either
            continue
        mine = ADAPTERS[kind].without_ours(current, state.hook_script)
        if kind == "claude":
            mine = claude.without_mod(mine, state.mod)
        if mine != tidied(kind, current):
            left.append(path)
    return left


def hook_alive(state):
    from orchestraty.capture import hook
    return hook.coordinator_alive(state.root, time.time())


# ------------------------------------------------------------------------------ doing it
def _write(state, path, data, backed):
    if backed and os.path.exists(path):
        hooks.backup(state, path, "uninstall")
    hooks._write_atomic(path, data)


def _remove_empty(folder):
    if os.path.basename(folder) in (".claude", ".agents", "info"):
        try:
            os.rmdir(folder)                        # only when empty
        except OSError:
            pass


def stop_coordinator(state, seconds=STOP_SECONDS):
    lock = Lock(state.lock)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if lock.acquire():
            lock.release()
            return True
        try:
            touch(state.stop)
        except OSError:                             # Windows: the coordinator is removing it
            pass
        time.sleep(0.1)
    return False


def do(state, step, ask, keep_backups, runner=subprocess.run):
    """Do one step; returns the line that says what was done."""
    if step.kind == "switch":
        plugin.remove_switch(state)
        return "Turned off the hooks of Claude Code's Orchestraty plugin."
    if step.kind == "restore":
        _write(state, step.path, step.data, keep_backups)
        return f"Put back {step.path} from {step.extra}."
    if step.kind == "ask":
        folder, data = step.extra
        if ask(f"Put back {step.path} as it was before orch init? The changes made to it since then are lost."):
            _write(state, step.path, data, keep_backups)
            return f"Put back {step.path} from {folder}."
        _write(state, step.path, hooks._dump(step.data), keep_backups)
        return f"Took Orchestraty's entries out of {step.path}; its other changes since orch init stay."
    if step.kind == "write":
        _write(state, step.path, hooks._dump(step.data), keep_backups)
        return f"Took Orchestraty's entries out of {step.path}."
    if step.kind == "delete":
        if keep_backups:
            hooks.backup(state, step.path, "uninstall")
        os.remove(step.path)
        _remove_empty(os.path.dirname(step.path))
        return f"Deleted {step.path}."
    if step.kind == "exclude":
        _write(state, step.path, step.data, keep_backups)
        return f"Took Orchestraty's line out of {step.path}."
    if step.kind == "githooks":
        githooks.uninstall(step.path)
        return f"Took the push line out of the git hooks of {step.path}."
    if step.kind == "plugin":
        proc = runner(step.data, cwd=step.path or None, capture_output=True, text=True, timeout=120,
                   stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        if proc.returncode != 0:
            said = (proc.stderr or proc.stdout or "").strip().splitlines()
            return f"claude plugin uninstall did not work ({said[-1] if said else proc.returncode}); run it yourself."
        return "Uninstalled Claude Code's Orchestraty plugin."
    if step.kind == "shadows":
        gitio.remove_tree(step.path)
        cfg = config.load(state)
        if any(isinstance(v, dict) and v.get("on") for v in cfg["remote"].values()):
            cfg["remote"] = {k: dict(v, on=False) if isinstance(v, dict) else v for k, v in cfg["remote"].items()}
            config.save(state, cfg)
        return "Removed the cloud branches' shadows; cloud branches are off."
    if step.kind == "stop":
        if not stop_coordinator(state):
            raise TimeoutError("it did not stop in time")
        return "Stopped the coordinator."
    raise ValueError(step.kind)


def run(state, keep_state=False, assume_yes=False, ask=None, out=print, environ=None, which=shutil.which,
        runner=subprocess.run):
    """orch uninstall. `ask(question)` answers the questions (with `assume_yes`, every one but
    putting back a file that changed since orch init is a yes). Returns the exit code."""
    def question(text, default_yes=True):
        if assume_yes:
            return default_yes
        return ask(text)
    steps, notes = plan(state, environ, which)
    state_there = os.path.isdir(state.root)
    if not steps and not (state_there and not keep_state):
        hooked = still_hooked(state, environ)
        kept = f" Its state folder {state.root} is kept." if state_there else ""
        if hooked:
            out(f"{', '.join(hooked)} still run{'s' if len(hooked) == 1 else ''} Orchestraty's hook script, and orch "
                f"uninstall cannot take those hooks out.{kept}")
        else:
            out("Nothing of Orchestraty's is left to take out." + kept)
        for line in notes[:-1]:
            out("  " + line)
        if hooked:
            out(UNFINISHED)
            return 1
        return 0
    out("orch uninstall will:")
    for step in steps:
        out(f"  - {step.text}")
    if state_there:
        out(f"  - {'keep' if keep_state else 'then ask before removing'} the state folder {state.root} "
            "(the record of sessions, findings and notes, the backups, the settings)"
            + (" (--keep-state)" if keep_state else ""))
    if notes:
        out("Left as it is:")
        for line in notes:
            out(f"  - {line}")
    if state_there and not keep_state:
        out("To send feedback first, run orch feedback now: it needs the record.")
    if not question("Go ahead?"):
        out("Nothing changed.")
        return 1
    failed = stuck = False
    for step in steps:
        try:
            out(do(state, step, lambda text: question(text, default_yes=False), keep_backups=state_there,
                   runner=runner))
        except (OSError, gitio.GitError, gitio.UntrustedRepositoryError, hooks.Refused,
                subprocess.SubprocessError) as exc:
            out(f"Could not: {step.text} ({exc})")
            failed = True
            stuck = stuck or step.kind == "stop"
    hooked = still_hooked(state, environ)
    if state_there and not keep_state:
        if failed or hooked:
            if hooked:
                why = (f"{', '.join(hooked)} still run{'s' if len(hooked) == 1 else ''} its hook script, which would "
                       "fail without it")
            else:
                why = _stuck(state) if stuck else "a second run of orch uninstall needs its backups"
            out(f"Kept {state.root}: {why}.")
        elif question(f"Remove the state folder {state.root}? The record, the backups and the settings go with it."):
            failed = not remove_state(state, out)
        else:
            out(f"Kept {state.root}.")
    if failed or hooked:
        out(UNFINISHED)
        return 1
    out("Orchestraty is uninstalled. The orch command itself stays: pipx uninstall orchestraty (or uv tool uninstall "
        "orchestraty) removes it.")
    return 0


def _stuck(state):
    """Why the state folder stays when the coordinator did not stop (with the pid it wrote)."""
    try:
        with open(state.info, "r", encoding="utf-8") as fh:
            pid = json.load(fh).get("pid")
    except (OSError, ValueError, AttributeError):
        pid = None
    return (f"the coordinator{f' (pid {pid})' if isinstance(pid, int) else ''} did not stop, and the folder is not "
            "removed from under it")


def remove_state(state, out):
    """Stop the coordinator and remove the state folder; False when that could not be done. A
    coordinator that does not stop keeps the folder: it would be removed from under it."""
    if not stop_coordinator(state):
        out(f"Kept {state.root}: {_stuck(state)}.")
        return False
    try:
        gitio.remove_tree(state.root)
    except OSError as exc:
        out(f"Could not remove {state.root} ({exc}).")
        return False
    out(f"Removed {state.root}.")
    return True
