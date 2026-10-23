"""orch uninstall, in an isolated state folder, with settings files of every agent that already
hold entries of other tools, a watched repository with a pre-push hook of its own, a workspace's
Antigravity hooks file, and cloud-branch shadows: everything Orchestraty added goes, the
backups come back where that loses nothing, other entries stay, and a second run changes
nothing."""
import json
import os
import sys
from pathlib import Path

import pytest

from helpers import symlinks_work
from orchestraty import cli, config, plugin, uninstall
from orchestraty.adapters import claude
from orchestraty.paths import State

CLAUDE_BEFORE = ('{\n    "model": "their-model",\n    "env": {"THEIRS": "1"},\n    "hooks": {"PreToolUse": [\n'
                 '        {"matcher": "Bash", "hooks": [{"type": "command", "command": "their-guard"}]}\n    ]}\n}\n')
CODEX_BEFORE = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "their-codex-hook"}]}]}}
PRE_PUSH = "#!/bin/sh\necho their pre-push\n"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def snapshot(*paths):
    """Each path's bytes, or None when it is missing; for a folder, its files'."""
    out = {}
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                out[str(f)] = f.read_bytes() if f.is_file() else "dir"
        else:
            out[str(p)] = p.read_bytes() if p.exists() else None
    return out


@pytest.fixture
def scene(tmp_path, git, monkeypatch, capsys):
    """Orchestraty installed for every agent, with other tools' entries already there."""
    claude_settings = tmp_path / "claude-config" / "settings.json"
    claude_settings.parent.mkdir()
    claude_settings.write_text(CLAUDE_BEFORE, encoding="utf-8")
    codex_hooks = tmp_path / "codex-home" / "hooks.json"
    codex_hooks.parent.mkdir()
    codex_hooks.write_text(json.dumps(CODEX_BEFORE), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    pre_push = repo / ".git" / "hooks" / "pre-push"
    pre_push.write_text(PRE_PUSH, encoding="utf-8")
    exclude = repo / ".git" / "info" / "exclude"
    exclude_before = exclude.read_bytes()
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes"]) == 0                                   # Claude Code, the mod, the push line
    assert cli.main(["init", "--yes", "--agent", "codex", "--no-git-hooks"]) == 0
    assert cli.main(["init", "--yes", "--agent", "antigravity", "--no-git-hooks"]) == 0
    assert cli.main(["init", "--yes", "--agent", "antigravity", "--project", "--no-git-hooks"]) == 0
    assert cli.main(["config", "wake", "on"]) == 0
    state = State()
    shadow = Path(state.root) / "shadows" / "0123456789abcdef.git"
    shadow.mkdir(parents=True)
    (shadow / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    cfg = config.load(state)
    cfg["remote"] = {"some-repo": {"on": True, "remote": "origin", "patterns": ["claude/*"]}}
    config.save(state, cfg)
    # another tool adds an entry to Codex's file after orch init
    later = read(codex_hooks)
    later["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "their-later-hook"}]})
    codex_hooks.write_text(json.dumps(later), encoding="utf-8")
    capsys.readouterr()
    return {"claude": claude_settings, "codex": codex_hooks, "repo": repo, "pre_push": pre_push, "exclude": exclude,
            "exclude_before": exclude_before, "antigravity": tmp_path / "home" / ".gemini" / "config" / "hooks.json",
            "workspace": repo / ".agents" / "hooks.json", "state": Path(state.root)}


def everything(scene):
    return snapshot(scene["claude"], scene["codex"], scene["antigravity"], scene["workspace"], scene["exclude"],
                    scene["repo"] / ".git" / "config", scene["repo"] / ".git" / "hooks", scene["state"])


def test_everything_orchestraty_added_goes_and_the_rest_stays(scene, git, capsys):
    s = scene
    script = str(Path(s["state"]) / "hooks" / "hook.py")
    assert json.dumps(script)[1:-1] in s["claude"].read_text(encoding="utf-8")          # as JSON writes it
    assert "orch-hooks" in git(s["repo"], "config", "core.hooksPath")
    assert cli.main(["uninstall", "--yes"]) == 0
    out = capsys.readouterr().out
    plan = out.split("Left as it is:")[0]
    assert plan.startswith("orch uninstall will:\n")
    for line in (f"Claude Code: put back {s['claude']} as it was before orch init",
                 f"Codex: take Orchestraty's entries out of {s['codex']}; it changed since orch init",
                 f"Antigravity: delete {s['antigravity']}, which orch init made",
                 f"Antigravity: delete {s['workspace']}, which orch init made",
                 f"git: take the line orch init added (/.agents/hooks.json) out of {s['exclude']}",
                 f"git: take the push line out of the hooks of {s['repo']}",
                 "cloud branches: remove their shadows", "then ask before removing the state folder"):
        assert line in plan, line
    assert "MCP: Orchestraty registered its server with no agent itself" in out
    # the backup came back as it was, byte for byte: its other entries, its formatting
    assert s["claude"].read_text(encoding="utf-8") == CLAUDE_BEFORE
    # Codex's file changed since: only Orchestraty's entries came out, with --yes
    assert read(s["codex"]) == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "their-codex-hook"}]},
                                                    {"hooks": [{"type": "command", "command": "their-later-hook"}]}]}}
    assert not s["antigravity"].exists() and not s["workspace"].exists() and not (s["repo"] / ".agents").exists()
    assert s["exclude"].read_bytes() == s["exclude_before"]
    assert not (s["repo"] / ".git" / "orch-hooks").exists()
    assert "hookspath" not in git(s["repo"], "config", "--local", "--list").lower()
    assert s["pre_push"].read_text(encoding="utf-8") == PRE_PUSH
    assert git(s["repo"], "status", "--porcelain") == ""
    assert not s["state"].exists()
    # a second run finds nothing and changes nothing
    before = everything(s)
    assert cli.main(["uninstall", "--yes"]) == 0
    assert capsys.readouterr().out.startswith("Nothing of Orchestraty's is left to take out.")
    assert everything(s) == before


def test_keep_state_keeps_the_record_and_backups_and_a_second_run_changes_nothing(scene, capsys):
    s = scene
    assert cli.main(["uninstall", "--yes", "--keep-state"]) == 0
    out = capsys.readouterr().out
    assert f"keep the state folder {s['state']}" in out and "Removed the cloud branches' shadows" in out
    assert s["state"].is_dir() and (s["state"] / "backups").is_dir()
    assert not (s["state"] / "shadows").exists()
    assert all(not v["on"] for v in config.load(State())["remote"].values())          # cloud branches off
    assert s["claude"].read_text(encoding="utf-8") == CLAUDE_BEFORE
    before = everything(s)
    assert cli.main(["uninstall", "--yes", "--keep-state"]) == 0
    assert capsys.readouterr().out.startswith(f"Nothing of Orchestraty's is left to take out. Its state folder "
                                              f"{s['state']} is kept.")
    assert everything(s) == before


def test_a_file_changed_since_orch_init_is_put_back_only_when_asked(scene, capsys):
    s = scene
    asked = []

    def say_yes(question):
        asked.append(question)
        return True
    assert uninstall.run(State(), keep_state=True, ask=say_yes, out=lambda line: None) == 0
    assert asked[0] == "Go ahead?"
    assert asked[1] == f"Put back {s['codex']} as it was before orch init? The changes made to it since then are lost."
    assert read(s["codex"]) == CODEX_BEFORE                       # the later entry went with the backup, as asked
    assert asked[2:] == []                                         # --keep-state: no question about the state folder


def test_answering_no_changes_nothing(scene):
    s = scene
    before = everything(s)
    lines = []
    assert uninstall.run(State(), ask=lambda question: False, out=lines.append) == 1
    assert lines[0] == "orch uninstall will:" and lines[-1] == "Nothing changed."
    assert everything(s) == before


def test_the_state_folder_stays_when_the_answer_is_no(scene):
    s = scene
    answers = iter([True, False])                                   # go ahead; keep the state folder
    lines = []
    assert uninstall.run(State(), ask=lambda question: next(answers, False), out=lines.append) == 0
    assert f"Kept {s['state']}." in lines and s["state"].is_dir()
    assert s["claude"].read_text(encoding="utf-8") == CLAUDE_BEFORE


def test_the_plugin_is_uninstalled_with_claude_codes_own_command(tmp_path, state_root):
    state = State()
    (Path(state_root) / "hooks").mkdir(parents=True)
    plugin.write_switch(state, sys.executable, False)
    plugins = tmp_path / "claude-config" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "orchestraty@orchestraty": [{"scope": "user", "installPath": "/x", "version": "1"},
                                    {"scope": "local", "installPath": "/x", "version": "1",
                                     "projectPath": str(tmp_path)}]}}), encoding="utf-8")
    ran, lines = [], []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def runner(argv, **kwargs):
        ran.append((argv, kwargs.get("cwd")))
        return Done()
    assert uninstall.run(state, keep_state=True, assume_yes=True, out=lines.append,
                         which=lambda name: "/bin/claude" if name == "claude" else None, runner=runner) == 0
    argv = ["/bin/claude", "plugin", "uninstall", "orchestraty@orchestraty", "--scope"]
    assert ran == [(argv + ["user"], None), (argv + ["local"], str(tmp_path))]
    assert plugin.read_switch(state) is None
    assert "Turned off the hooks of Claude Code's Orchestraty plugin." in lines
    lines.clear()                                                   # no claude command: the way to do it is shown
    assert uninstall.run(state, keep_state=True, assume_yes=True, out=lines.append, which=lambda name: None) == 0
    assert any("In Claude Code, run /plugin uninstall orchestraty@orchestraty." in line for line in lines)


def test_a_linked_settings_file_is_left_alone(tmp_path, state_root, capsys):
    if not symlinks_work(tmp_path):
        pytest.skip("symbolic links are not available here")
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    state = State()
    real.write_text(json.dumps({"hooks": claude.hook_block(sys.executable, state.hook_script)}), encoding="utf-8")
    link = tmp_path / "claude-config" / "settings.json"
    link.parent.mkdir()
    os.symlink(real, link)
    before = real.read_bytes()
    assert cli.main(["uninstall", "--yes"]) == 0
    out = capsys.readouterr().out
    assert f"{link} is a link, which orch uninstall does not replace" in out
    assert real.read_bytes() == before and link.is_symlink()


def test_empty_hooks_objects_do_not_hide_the_backup_or_make_work(tmp_path, state_root, repo, monkeypatch, capsys):
    claude_settings = tmp_path / "claude-config" / "settings.json"
    claude_settings.parent.mkdir()
    claude_settings.write_text('{"model": "x", "hooks": {}}\n', encoding="utf-8")
    codex_hooks = tmp_path / "codex-home" / "hooks.json"                     # never installed: nothing of ours
    codex_hooks.parent.mkdir()
    codex_hooks.write_text('{"hooks": {}}\n', encoding="utf-8")
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--yes", "--no-git-hooks"]) == 0
    capsys.readouterr()
    assert cli.main(["uninstall", "--yes", "--keep-state"]) == 0
    out = capsys.readouterr().out
    assert f"Claude Code: put back {claude_settings} as it was before orch init" in out and "Codex" not in out
    assert claude_settings.read_text(encoding="utf-8") == '{"model": "x", "hooks": {}}\n'
    assert codex_hooks.read_text(encoding="utf-8") == '{"hooks": {}}\n'
    assert cli.main(["uninstall", "--yes", "--keep-state"]) == 0
    assert capsys.readouterr().out.startswith("Nothing of Orchestraty's is left to take out.")


def test_a_file_orch_init_wrote_is_found_where_it_was_written(tmp_path, state_root, monkeypatch, capsys):
    first = tmp_path / "codex-first"
    monkeypatch.setenv("CODEX_HOME", str(first))
    assert cli.main(["init", "--yes", "--agent", "codex", "--no-git-hooks"]) == 0
    assert config.load(State())["installed"] == [{"agent": "codex", "path": str(first / "hooks.json")}]
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-later"))             # moved since orch init
    capsys.readouterr()
    assert cli.main(["uninstall", "--yes"]) == 0
    assert f"Codex: delete {first / 'hooks.json'}" in capsys.readouterr().out
    assert not (first / "hooks.json").exists()
