"""Tests for the scanner's collectors. Every fixture is synthetic (see conftest.py)."""
import ast
import builtins
import getpass
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import scan_collect as sc
from conftest import SECRET, commit, git, snapshot_tree

HERE = Path(__file__).resolve().parent
SCANNER_FILES = ["scan_collect.py"]   # scan_cli.py and scan_bundle.py are checked in test_scan_cli.py


def _strings(obj):
    """Every string key and value in a parsed result."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def _identity_needles(home, host, user):
    """The host name, user name and home-folder path parts, which must never reach the output.
    Words the scanner itself is made of (agent names such as "claude", launchers such as "node",
    "pi") are not identifiers, whatever the machine or user happens to be called."""
    source = (HERE / "scan_collect.py").read_text(encoding="utf-8").lower()
    generic = {"home", "users", "appdata", "local", "temp", "tmp", "var", "private", "folders", "root"}
    candidates = [host, user] + [part for part in Path(str(home)).parts
                                 if len(part) > 3 and part.strip("\\/:").lower() not in generic]
    return [c for c in candidates if c and c.lower() not in source]


def _leaks(result, needles):
    """(needle, string) for every needle found in any string of the result."""
    return [(n, s) for s in _strings(result) for n in needles if n.lower() in s.lower()]


def test_the_leak_check_ignores_scanner_words_but_catches_a_real_host_name(tmp_path):
    # On a machine called "claude" the output's "claude-code" is not a leak; an unusual host
    # name in the output still is.
    result = {"agents": {"claude-code": {"installed": True}}, "mcp": {"by_app": {"cursor": {"commands": {"node": 1}}}},
              "run": {"os": "Linux"}}
    for host, user in (("claude", "node"), ("CLAUDE", "pi")):
        assert _leaks(result, _identity_needles(tmp_path / "home", host, user)) == []
    needles = _identity_needles(tmp_path / "home", "zqx-build-host-4417", "pi")
    assert "zqx-build-host-4417" in needles
    result["run"]["os"] = "Linux on zqx-build-host-4417"
    assert _leaks(result, needles) == [("zqx-build-host-4417", "Linux on zqx-build-host-4417")]


def _symlinks_work(tmp_path):
    try:
        os.symlink(tmp_path, tmp_path / "probe-link")
        return True
    except (OSError, NotImplementedError):
        return False


# ----------------------------------------------------------------- sessions


def test_sessions_are_counted_from_file_dates_only(machine):
    machine.claude_sessions(ages=(0.5, 1, 20))        # the 20-day-old one is outside a 14-day window
    machine.codex_sessions(ages=(2, 3))               # plus one archived session 60 days old
    machine.gemini_sessions(ages=(1,))
    machine.copilot_sessions(ages=(1, 40))
    machine.cursor_sessions(ages=(1,))
    machine.droid_sessions(ages=(2,))
    machine.pi_sessions(ages=(3,))
    result = sc.collect(machine.env(), days=14)
    counts = {n: (a["sessions"] or {}).get("sessions") for n, a in result["agents"].items()}
    assert counts == {"claude-code": 2, "codex": 2, "gemini-cli": 1, "copilot-cli": 1, "cursor": 1,
                      "factory-droid": 1, "pi": 1, "opencode": None}
    assert result["sessions_summary"]["agents_with_sessions"] == 7
    assert result["sessions_summary"]["sessions_total"] == 9


def test_codex_compressed_and_archived_sessions_count(linux_machine):
    base = linux_machine.home / ".codex"
    for rel, age in (("sessions/2026/09/20/rollout-2026-09-20T10-00-00-a.jsonl", 1),
                     ("sessions/2026/09/12/rollout-2026-09-12T10-00-00-b.jsonl.zst", 9),
                     ("archived_sessions/2026/09/15/rollout-2026-09-15T10-00-00-c.jsonl", 3),
                     ("sessions/2026/09/20/not-a-rollout.jsonl", 1)):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (linux_machine.now - age * 86400,) * 2)
    result = sc.collect(linux_machine.env(), days=14)
    assert result["agents"]["codex"]["sessions"]["sessions"] == 3


def test_relocated_agent_folders_are_used(machine, tmp_path):
    other = tmp_path / "elsewhere" / "claude-profile"
    machine.claude_sessions(ages=(1,), home=other)
    machine.claude_json({"x": {"command": "git"}}, base=other)   # CLAUDE_CONFIG_DIR also moves .claude.json
    machine.claude_json({"y": {"command": "git"}, "z": {"command": "git"}})
    gem = tmp_path / "gem-home"                                   # GEMINI_CLI_HOME replaces the home folder
    g = gem / ".gemini" / "tmp" / "p" / "chats" / "session-2026-09-20T10-00-abcd.jsonl"
    g.parent.mkdir(parents=True)
    g.write_text("{}", encoding="utf-8")
    result = sc.collect(machine.env(CLAUDE_CONFIG_DIR=str(other), GEMINI_CLI_HOME=str(gem)),
                        days=14, read_claude_json=True)
    agents = result["agents"]
    assert agents["claude-code"]["sessions"]["sessions"] == 1 and agents["claude-code"]["home_relocated"]
    assert agents["gemini-cli"]["sessions"]["sessions"] == 1 and agents["gemini-cli"]["home_relocated"]
    assert result["mcp"]["by_app"]["claude-code"]["servers"] == 1


def test_set_aside_claude_transcripts_are_not_sessions(linux_machine):
    base = linux_machine.home / ".claude" / "projects" / "-p"
    base.mkdir(parents=True)
    for name in ("0f8c-1a2b.jsonl", "0f8c-1a2b.orphaned-1712-ab.jsonl", "0f8c-1a2b.jsonl.superseded-1712"):
        (base / name).write_text("{}", encoding="utf-8")
    (base / "0f8c-1a2b" / "subagents").mkdir(parents=True)
    (base / "0f8c-1a2b" / "subagents" / "agent-1.jsonl").write_text("{}", encoding="utf-8")
    result = sc.collect(linux_machine.env(), days=14)
    assert result["agents"]["claude-code"]["sessions"]["sessions"] == 1


def test_a_common_program_name_alone_does_not_count_as_installed(linux_machine, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / ("pi.exe" if os.name == "nt" else "pi")
    fake.write_text("", encoding="utf-8")
    fake.chmod(0o755)
    result = sc.collect(linux_machine.env(PATH=str(bin_dir)), days=14)
    assert result["agents"]["pi"]["on_path"] is True
    assert result["agents"]["pi"]["installed"] is False


def test_max_concurrent_from_creation_and_modification_times(linux_machine, monkeypatch):
    # Sessions created 2 hours before their last write: the three recent ones overlap.
    paths = linux_machine.claude_sessions(ages=(0.01, 0.02, 0.03, 5))
    monkeypatch.setattr(sc, "_birth_time", lambda st, env: st.st_mtime - 7200)
    result = sc.collect(linux_machine.env(), days=14)
    assert result["agents"]["claude-code"]["sessions"]["max_concurrent"] == 3
    assert result["sessions_summary"]["max_concurrent_all_agents"] == 3
    assert len(paths) == 4


# ----------------------------------------------------------------- privacy: nothing private is opened


def test_private_files_are_never_opened_or_passed_to_git(machine, monkeypatch, make_repo):
    machine.everything_private()
    make_repo(machine.home / "code" / "svc")
    private = {os.path.normcase(os.path.abspath(str(p))) for p in machine.private_files}
    private_dirs = {os.path.normcase(os.path.abspath(str(p.parent))) for p in machine.private_files}
    touched = []

    def note(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            p = os.path.normcase(os.path.abspath(os.fsdecode(path)))
            if p in private:
                touched.append(p)

    real_open, real_os_open, real_fileio = builtins.open, os.open, io.FileIO
    real_popen = subprocess.Popen

    def spy_open(path, *a, **k):
        note(path)
        return real_open(path, *a, **k)

    def spy_os_open(path, *a, **k):
        note(path)
        return real_os_open(path, *a, **k)

    class SpyFileIO(real_fileio):
        def __init__(self, path, *a, **k):
            note(path)
            super().__init__(path, *a, **k)

    class SpyPopen(real_popen):
        def __init__(self, args, *a, **k):
            for arg in (args if isinstance(args, (list, tuple)) else [args]):
                s = os.fsdecode(arg) if isinstance(arg, (str, bytes, os.PathLike)) else ""
                if any(d in os.path.normcase(s) for d in private_dirs):
                    touched.append(s)
            super().__init__(args, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr(io, "FileIO", SpyFileIO)
    monkeypatch.setattr(os, "open", spy_os_open)
    monkeypatch.setattr(subprocess, "Popen", SpyPopen)
    sc.collect(machine.env(), days=14, read_claude_json=True)
    assert touched == [], f"private files were touched: {touched}"


def test_nothing_is_written_anywhere(linux_machine, make_repo, monkeypatch, tmp_path):
    m = linux_machine
    m.everything_private()
    repo = make_repo(m.home / "code" / "svc")
    commit(repo, "a.py", "1\n", "bob", days_ago=1)
    m.json_file(m.home / ".cursor" / "mcp.json", {"mcpServers": {"a": {"command": "npx"}}})
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(tmp))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    before = snapshot_tree(m.base)
    sc.collect(m.env(), days=14)
    assert snapshot_tree(m.base) == before, "the scan changed files in the synthetic machine"


def test_output_holds_no_identifiers(machine, make_repo, tmp_path):
    m = machine
    m.claude_sessions(project="C--Users-someone-secret-project")
    m.codex_sessions()
    repo = make_repo(m.home / "code" / "acme-billing-service")
    commit(repo, "billing.py", "x = 1\n", "carol-private")
    git(repo, "checkout", "-q", "-b", "feature/secret-merger-talks")
    commit(repo, "billing.py", "x = 2\n", "dave-private")
    git(repo, "checkout", "-q", "main")
    git(repo, "remote", "add", "origin", "https://github.com/acme-secret-org/acme-billing-service.git")
    m.json_file(m.home / ".cursor" / "mcp.json", {"mcpServers": {
        "internal-payroll-helper": {"command": "npx", "args": ["@acme/secret-server"], "env": {"API_KEY": "sk-live-123"}},
        "custom": {"command": "/opt/acme-corp/bin/acme-payroll-mcp"},
        "win": {"command": "C:\\Users\\alice.smith\\tools\\jira-acmecorp-bridge.exe"},
        "bare": {"command": "carol-private-helper"}}})
    bad = m.home / ".gemini" / "settings.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{broken", encoding="utf-8")
    other = tmp_path / "relocated-claude"
    m.claude_sessions(ages=(1,), home=other)
    m.claude_json({"another-private-helper": {"command": "uvx", "args": ["private-pkg"]}}, base=other)
    env = m.env(CLAUDE_CONFIG_DIR=str(other))
    result = sc.collect(env, days=14, read_claude_json=True)
    strings = list(_strings(result))
    needles = [SECRET, "acme", "carol-private", "dave-private", "example.com", "secret-merger",
               "internal-payroll-helper", "another-private-helper", "sk-live-123", "private-pkg",
               "secret-project", "alice.smith", "jira", "payroll", "owner@"]
    needles += _identity_needles(m.home, platform.node(), getpass.getuser())
    assert _leaks(result, needles) == []
    for s in strings:
        assert not re.search(r"\b[0-9a-f]{32}\b", s), f"a long hex value (a salt?) is in the output: {s!r}"
    assert result["mcp"]["by_app"]["cursor"]["commands"] == {"npx": 1, "other-path": 2, "other": 1}
    ids_1 = {i["id"] for i in result["repos"]["items"]}
    ids_2 = {i["id"] for i in sc.collect(env, days=14)["repos"]["items"]}
    assert ids_1 and ids_1.isdisjoint(ids_2), "repository ids must differ between runs"


# ----------------------------------------------------------------- .claude.json consent and links


def test_claude_json_is_not_opened_without_consent(machine, monkeypatch):
    path = machine.claude_json({"helper": {"command": "npx"}})
    opened = []
    real_open = builtins.open

    def spy(p, *args, **kwargs):
        opened.append(os.path.abspath(str(p)))
        return real_open(p, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    result = sc.collect(machine.env(), days=14, read_claude_json=False)
    assert os.path.abspath(str(path)) not in opened
    assert "claude-code" not in result["mcp"]["by_app"]
    assert any(".claude.json" in item for item in result["not_collected"])


def test_claude_json_servers_are_counted_with_consent(machine):
    machine.claude_json({"one": {"command": "npx"}, "two": {"type": "http", "url": "https://example.invalid"}})
    result = sc.collect(machine.env(), days=14, read_claude_json=True)
    app = result["mcp"]["by_app"]["claude-code"]
    assert app["servers"] == 2 and app["remote"] == 1
    assert result["mcp"]["claude_json_read"] is True


def test_config_links_to_private_files_are_not_followed(linux_machine, make_repo, tmp_path, monkeypatch):
    if not _symlinks_work(tmp_path):
        pytest.skip("symlinks are not available here")
    m = linux_machine
    sessions = m.claude_sessions()
    m.claude_json({"h": {"command": "npx"}})
    repo = make_repo(m.home / "code" / "cloned-repo")
    os.symlink(m.home / ".claude.json", repo / ".mcp.json")
    (repo / ".vscode").mkdir()
    os.symlink(sessions[0], repo / ".vscode" / "mcp.json")
    (m.home / ".cursor").mkdir()
    os.symlink(sessions[1], m.home / ".cursor" / "mcp.json")      # a user-level link to a session file
    opened = []
    real_open = builtins.open

    def spy(p, *a, **k):
        opened.append(os.path.realpath(str(p)))
        return real_open(p, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    result = sc.collect(m.env(), days=14, read_claude_json=False)
    private = {os.path.realpath(str(p)) for p in [m.home / ".claude.json"] + sessions}
    assert not private & set(opened)
    assert result["mcp"]["claude_json_read"] is False
    assert any("config files were skipped" in x for x in result["limits"])


def _open_spy(monkeypatch):
    opened = []
    real_open = builtins.open

    def spy(p, *a, **k):
        if isinstance(p, (str, bytes, os.PathLike)):
            opened.append(os.path.normcase(os.path.realpath(os.fsdecode(p))))
        return real_open(p, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    return opened


def test_user_level_links_to_history_sign_in_and_claude_json_are_not_followed(linux_machine, tmp_path,
                                                                              monkeypatch):
    if not _symlinks_work(tmp_path):
        pytest.skip("symlinks are not available here")
    m = linux_machine
    m.history_files()
    m.claude_json({"h": {"command": "npx"}})
    sign_in = m.json_file(m.home / ".codex" / "auth.json", {"mcpServers": {"t": {"command": "npx"}}})
    links = {
        m.home / ".codeium" / "windsurf" / "mcp_config.json": m.home / ".claude.json",   # no consent given
        m.home / ".gemini" / "settings.json": m.home / ".claude" / "history.jsonl",
        m.home / ".cursor" / "mcp.json": sign_in,
        m.home / ".claude" / "settings.json": m.claude_sessions()[0],                   # read for hook policy
    }
    for link, target in links.items():
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, link)
    opened = _open_spy(monkeypatch)
    result = sc.collect(m.env(), days=14, read_claude_json=False)
    targets = {os.path.normcase(os.path.realpath(str(t))) for t in links.values()}
    assert not targets & set(opened), "a private file was opened through a link"
    assert result["mcp"]["by_app"] == {}
    assert result["hook_policy"]["claude_code"]["user_hook_events"] == 0


def test_hard_links_and_other_letter_cases_do_not_reach_private_files(tmp_path, make_repo, monkeypatch):
    from conftest import SyntheticMachine
    m = SyntheticMachine(tmp_path, "Darwin")             # a case-insensitive file system, like macOS
    sessions = m.claude_sessions()
    repo = make_repo(m.home / "code" / "cloned")
    (m.home / ".cursor").mkdir()
    try:
        os.link(sessions[0], repo / ".mcp.json")         # a hard link is not a link to islink or realpath
        os.link(sessions[1], m.home / ".cursor" / "mcp.json")
    except (OSError, NotImplementedError):
        pytest.skip("hard links are not available here")
    if _symlinks_work(tmp_path):
        upper = m.json_file(m.home / ".CLAUDE.json", {"mcpServers": {"u": {"command": "npx"}}})
        other_case = m.home / ".Claude" / "projects" / "p" / "t.jsonl"
        m._plant(other_case, 1)
        (m.home / ".codeium").mkdir()
        os.symlink(upper, m.home / ".codeium" / "mcp_config.json")
        (m.home / ".gemini").mkdir()
        os.symlink(other_case, m.home / ".gemini" / "settings.json")
    opened = _open_spy(monkeypatch)
    result = sc.collect(m.env(), days=14, read_claude_json=False)
    assert result["mcp"]["by_app"] == {}
    assert not {os.path.normcase(os.path.realpath(str(p))) for p in m.private_files} & set(opened)


def test_links_inside_a_repository_are_never_followed(linux_machine, make_repo, tmp_path, monkeypatch):
    if not _symlinks_work(tmp_path):
        pytest.skip("symlinks are not available here")
    m = linux_machine
    repo = make_repo(m.home / "code" / "cloned")
    outside = tmp_path / "outside"
    m.json_file(outside / "mcp.json", {"servers": {"x": {"command": "npx"}}})
    m.json_file(outside / "top.json", {"mcpServers": {"y": {"command": "npx"}}})
    # A cloned repository can hold links to anywhere, including another machine's share.
    os.symlink(outside, repo / ".vscode", target_is_directory=True)
    os.symlink(outside / "top.json", repo / ".mcp.json")
    os.symlink(tmp_path / "gone", repo / ".husky", target_is_directory=True)
    os.symlink(tmp_path / "gone.yml", repo / "lefthook.yml")
    links = [os.path.normcase(str(repo / n)) for n in (".vscode", ".mcp.json", ".husky", "lefthook.yml")]
    followed = []
    real_stat, real_realpath = os.stat, os.path.realpath

    def through_link(p):
        s = os.path.normcase(os.path.abspath(os.fsdecode(p)))
        return any(s == link or s.startswith(link + os.sep) for link in links)

    def guard_stat(p, *a, follow_symlinks=True, **k):
        if follow_symlinks and not isinstance(p, int) and through_link(p):
            followed.append(("stat", os.fsdecode(p)))
        return real_stat(p, *a, follow_symlinks=follow_symlinks, **k)

    def guard_realpath(p, *a, **k):
        if through_link(p):
            followed.append(("realpath", os.fsdecode(p)))
        return real_realpath(p, *a, **k)

    monkeypatch.setattr(os, "stat", guard_stat)
    monkeypatch.setattr(os.path, "realpath", guard_realpath)
    for name in ("exists", "isfile", "isdir"):
        real = getattr(os.path, name)

        def check(p, _real=real, _name=name):
            if through_link(p):
                followed.append((_name, os.fsdecode(p)))
            return _real(p)

        monkeypatch.setattr(os.path, name, check)
    result = sc.collect(m.env(), days=14)
    assert followed == [], f"a link inside the repository was followed: {followed}"
    assert result["mcp"]["by_app"] == {}
    assert result["repos"]["items"][0]["hook_managers"] == ["husky", "lefthook"]


@pytest.mark.skipif(os.name != "nt", reason="junctions exist only on Windows")
def test_a_junction_inside_a_repository_is_not_followed(linux_machine, make_repo, tmp_path):
    import _winapi
    m = linux_machine
    repo = make_repo(m.home / "code" / "cloned")
    outside = tmp_path / "outside"
    m.json_file(outside / "mcp.json", {"servers": {"x": {"command": "npx"}}})
    _winapi.CreateJunction(str(outside), str(repo / ".vscode"))
    result = sc.collect(m.env(), days=14)
    assert "vscode" not in result["mcp"]["by_app"]
    assert any("config files were skipped" in x for x in result["limits"])


def test_files_not_stored_on_this_computer_are_not_opened(linux_machine, monkeypatch):
    # A cloud placeholder (e.g. OneDrive "online-only") would be downloaded if opened.
    m = linux_machine
    path = m.json_file(m.home / ".cursor" / "mcp.json", {"mcpServers": {"a": {"command": "npx"}}})
    real_stat = os.stat

    class Placeholder:
        st_file_attributes = 0x400000           # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

        def __init__(self, st):
            self._st = st

        def __getattr__(self, name):
            return getattr(self._st, name)

    def fake_stat(p, *a, **k):
        st = real_stat(p, *a, **k)
        same = not isinstance(p, int) and os.path.normcase(os.fsdecode(p)) == os.path.normcase(str(path))
        return Placeholder(st) if same else st

    monkeypatch.setattr(os, "stat", fake_stat)
    result = sc.collect(m.env(), days=14)
    assert "cursor" not in result["mcp"]["by_app"]


# ----------------------------------------------------------------- MCP helpers


def test_mcp_formats_are_parsed(machine, make_repo):
    repo = make_repo(machine.home / "code" / "svc")
    machine.json_file(machine.config_dir() / "Claude" / "claude_desktop_config.json",
                      {"mcpServers": {"fs": {"command": "git"}, "gone": {"command": "npx-that-is-not-installed"}}})
    p = machine.home / ".cursor" / "mcp.json"                        # a BOM, as PowerShell 5.1 writes it
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {"gh": {"command": "docker"}}}).encode())
    vs = repo / ".vscode" / "mcp.json"                               # JSON with comments and trailing commas
    vs.parent.mkdir(parents=True)
    vs.write_text('{\n  // a comment\n  "servers": {"db": {"type": "stdio", "command": "node",},},\n'
                  '  /* block */ "inputs": [],\n}\n', encoding="utf-8")
    machine.json_file(repo / ".mcp.json", {"mcpServers": {"proj": {"command": "uvx"}}})
    codex_cfg = machine.home / ".codex" / "config.toml"
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    codex_cfg.write_text(
        '\ufeffmodel = "x"\n'
        '[mcp_servers.docs]\ncommand = "npx"\nargs = ["-y", "pkg"]\n'
        '[mcp_servers.docs.env]\nKEY = "v"\n'
        '[ mcp_servers . "quoted.name" ]\nurl = "https://example.invalid/mcp"\n'
        'notes = """\n[mcp_servers.fake]\ncommand = "evil"\n"""\n'
        '[mcp_servers]\ninline = { command = "uvx", args = [] }\n'
        '[profiles.p]\ncommand = "not-a-server"\n', encoding="utf-8")
    result = sc.collect(machine.env(), days=14)
    by_app = result["mcp"]["by_app"]
    assert by_app["claude-desktop"]["servers"] == 2
    assert by_app["claude-desktop"]["missing_commands"] == 1
    assert by_app["cursor"]["commands"] == {"docker": 1}
    assert by_app["vscode"]["commands"] == {"node": 1}
    assert by_app["project-mcp-json"]["commands"] == {"uvx": 1}
    assert by_app["codex"]["servers"] == 3 and by_app["codex"]["remote"] == 1
    assert by_app["codex"]["commands"] == {"npx": 1, "remote": 1, "uvx": 1}
    assert result["mcp"]["local_servers"] == result["mcp"]["total_servers"] - 1
    assert result["errors"] == []


def test_real_app_count_merges_managed_and_ignores_shared_files(linux_machine, make_repo):
    m = linux_machine
    repo = make_repo(m.home / "code" / "svc")
    m.claude_json({"a": {"command": "npx"}})
    m.json_file(m.root / "etc" / "claude-code" / "managed-mcp.json", {"mcpServers": {"b": {"command": "npx"}}})
    m.json_file(repo / ".mcp.json", {"mcpServers": {"c": {"command": "npx"}, "d": {"url": "https://x.invalid"}}})
    result = sc.collect(m.env(), days=14, read_claude_json=True)
    assert result["mcp"]["apps_with_servers"] == 3
    assert result["mcp"]["real_apps_with_local_servers"] == 1       # all of it is Claude Code
    assert result["mcp"]["local_servers"] == 3


def _record_lookups(monkeypatch, needle):
    """Record every file-system lookup whose path contains `needle` (os.stat and the os.path
    checks, which on Windows no longer go through os.stat)."""
    seen = []
    real_stat = os.stat

    def stat(p, *a, **k):
        if not isinstance(p, int) and needle in os.fsdecode(p):
            seen.append(os.fsdecode(p))
        return real_stat(p, *a, **k)

    monkeypatch.setattr(os, "stat", stat)
    for name in ("exists", "lexists", "isfile", "isdir"):
        real = getattr(os.path, name)

        def check(p, _real=real):
            if needle in os.fsdecode(p):
                seen.append(os.fsdecode(p))
            return _real(p)

        monkeypatch.setattr(os.path, name, check)
    return seen


def test_network_paths_in_helper_commands_are_never_looked_up(linux_machine, monkeypatch):
    # Windows treats all of these as another machine's share (even '/' spellings and the NT
    # '\??\' prefix), and autofs does the same for /net and /Network on macOS and Linux.
    commands = {"a": "\\\\srv.invalid\\tools\\a.exe", "b": "/\\srv.invalid\\share\\b.exe",
                "c": "\\/srv.invalid/share/c.exe", "d": "\\??\\UNC\\srv.invalid\\share\\d.exe",
                "e": "//srv.invalid/share/e", "f": "/net/srv.invalid/tools/f",
                "g": "/Network/Servers/srv.invalid/g", "h": "tools\\srv.invalid\\h.exe",
                # The same places spelled so that a plain prefix check misses them.
                "i": "/./net/srv.invalid/tools/i", "j": "/NET/srv.invalid/tools/j",
                "k": "/usr/../net/srv.invalid/k", "l": "/network/Servers/srv.invalid/l"}
    m = linux_machine
    m.json_file(m.home / ".cursor" / "mcp.json", {"mcpServers": {k: {"command": v} for k, v in commands.items()}})
    touched = _record_lookups(monkeypatch, "srv.invalid")
    result = sc.collect(m.env(), days=14)
    assert touched == [], f"a network path was looked up: {touched}"
    cursor = result["mcp"]["by_app"]["cursor"]
    assert cursor["unchecked_commands"] == 12 and cursor["missing_commands"] == 0
    assert cursor["commands"] == {"other-path": 11, "other": 1}


def test_malformed_configs_never_stop_the_scan(linux_machine, make_repo):
    m = linux_machine
    repo = make_repo(m.home / "code" / "svc")
    (repo / ".mcp.json").write_text("[" * 100000, encoding="utf-8")          # too deep for json to parse
    m.json_file(m.home / ".cursor" / "mcp.json",
                {"mcpServers": {"nul": {"command": "acme\u0000npx"}, "ok": {"command": "npx"}}})
    (m.home / ".claude").mkdir()
    (m.home / ".claude" / "settings.json").write_text('{"a":' * 100000, encoding="utf-8")
    result = sc.collect(m.env(), days=14)
    assert any(e.startswith("project-mcp-json:") for e in result["errors"])
    assert "claude-code: user settings could not be parsed" in result["errors"]
    assert result["mcp"]["by_app"]["cursor"]["commands"] == {"unknown": 1, "npx": 1}


def test_switched_off_helpers_are_counted_apart(linux_machine):
    m = linux_machine
    m.json_file(m.home / ".cursor" / "mcp.json", {"mcpServers": {
        "on": {"command": "npx"}, "off": {"command": "npx", "disabled": True}}})
    m.json_file(m.home / ".config" / "opencode" / "opencode.json", {"mcp": {
        "x": {"type": "local", "command": ["bun", "run", "x"], "enabled": False}}})
    codex = m.home / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text('mcp_servers.c.command = "npx"\nmcp_servers.c.enabled = false\n'
                     '[mcp_servers.a]\ncommand = "npx"\nenabled = false\n'
                     '[mcp_servers]\nb = { command = "uvx", enabled = false }\nd = { command = "uvx" }\n',
                     encoding="utf-8")
    result = sc.collect(m.env(), days=14)
    by_app = result["mcp"]["by_app"]
    assert (by_app["cursor"]["servers"], by_app["cursor"]["disabled"]) == (1, 1)
    assert (by_app["opencode"]["servers"], by_app["opencode"]["disabled"]) == (0, 1)
    assert (by_app["codex"]["servers"], by_app["codex"]["disabled"]) == (1, 3)
    assert result["mcp"]["local_servers"] == 2 and result["mcp"]["real_apps_with_local_servers"] == 2


def test_broken_config_is_reported_not_fatal(linux_machine):
    p = linux_machine.home / ".cursor" / "mcp.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    result = sc.collect(linux_machine.env(), days=14)
    assert any("cursor" in e for e in result["errors"])


# ----------------------------------------------------------------- hook policy


def _managed_folder(machine):
    if machine.system == "Windows":
        return machine.root / "Program Files" / "ClaudeCode"
    if machine.system == "Darwin":
        return machine.root / "Library" / "Application Support" / "ClaudeCode"
    return machine.root / "etc" / "claude-code"


def test_managed_hook_policy_is_read(machine):
    folder = _managed_folder(machine)
    machine.json_file(folder / "managed-settings.json", {"hooks": {"PreToolUse": []}})
    machine.json_file(folder / "managed-settings.d" / "20-lockdown.json", {"allowManagedHooksOnly": True})
    machine.json_file(folder / "managed-mcp.json", {"mcpServers": {"corp": {"command": "git"}}})
    machine.json_file(machine.home / ".claude" / "settings.json",
                      {"hooks": {"Stop": [], "SessionStart": []}, "env": {"TOKEN": "sk-secret"}})
    result = sc.collect(machine.env(), days=14)
    cc = result["hook_policy"]["claude_code"]
    assert cc["managed_settings_found"] and cc["managed_hooks_only"]
    assert cc["managed_hook_events"] == 1
    assert cc["user_hook_events"] == 2
    assert result["mcp"]["by_app"]["claude-code-managed"]["servers"] == 1
    assert "sk-secret" not in json.dumps(result)


def test_server_pushed_settings_are_read(linux_machine):
    linux_machine.json_file(linux_machine.home / ".claude" / "remote-settings.json", {"allowManagedHooksOnly": True})
    result = sc.collect(linux_machine.env(), days=14)
    assert result["hook_policy"]["claude_code"]["managed_hooks_only"] is True


def test_synthetic_scans_never_read_the_registry(machine, monkeypatch):
    called = []
    monkeypatch.setitem(sys.modules, "winreg", type("W", (), {"__getattr__": lambda s, n: called.append(n)})())
    sc.collect(machine.env(), days=14)
    assert called == []


@pytest.mark.skipif(os.name != "nt", reason="the registry exists only on Windows")
def test_registry_values_of_the_wrong_type_are_ignored(monkeypatch):
    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = type("W", (), {"HKEY_LOCAL_MACHINE": 1, "HKEY_CURRENT_USER": 2, "KEY_READ": 0,
                          "OpenKey": staticmethod(lambda *a: Key()),
                          "QueryValueEx": staticmethod(lambda key, name: (42, 4))})
    monkeypatch.setitem(sys.modules, "winreg", fake)
    env = sc.Env(system="Windows")
    assert env.real_machine
    assert sc._registry_policy(env) == []


# ----------------------------------------------------------------- repository discovery


def test_claude_project_names_decode_to_real_folders(linux_machine):
    target = linux_machine.root / "work" / "my-app.v2" / "sub dir"
    target.mkdir(parents=True)
    (linux_machine.root / "work" / "my").mkdir()             # a decoy that matches a prefix
    env = linux_machine.env()
    assert sc.decode_claude_project(env, "-work-my-app-v2-sub-dir") == target
    assert sc.decode_claude_project(env, "-work-does-not-exist") is None


def test_decoding_is_case_insensitive_where_the_file_system_is(tmp_path):
    from conftest import SyntheticMachine
    mac = SyntheticMachine(tmp_path, "Darwin")
    (mac.root / "Users" / "Dev" / "App").mkdir(parents=True)
    assert sc.decode_claude_project(mac.env(), "-users-dev-app") is not None
    assert sc.decode_claude_project(mac.env(), "-Users-Dev-App") is not None


def test_characters_outside_the_basic_plane_encode_like_javascript():
    assert sc._encode_claude_segment("a\U0001F600b") == "a--b"
    assert sc._encode_claude_segment("a\u00e9b") == "a-b"


def test_long_project_names_fall_back_to_the_deepest_folder(linux_machine, make_repo):
    repo = make_repo(linux_machine.root / "srv" / "mono")
    deep = repo / ("x" * 120) / ("y" * 120)
    try:
        deep.mkdir(parents=True)
    except OSError:
        pytest.skip("this machine cannot create paths this long (Windows without long-path support)")
    full = "-" + sc._encode_claude_segment(str(deep.relative_to(linux_machine.root)))
    cut = full[:200] + "-3f2a9c"                              # what Claude Code does to long names
    real = sc.decode_claude_project(linux_machine.env(), cut)
    assert real is not None and sc._enclosing_repo(real, linux_machine.home) == repo


@pytest.mark.skipif(os.name != "nt", reason="drive letters only exist on Windows")
def test_claude_project_names_decode_on_real_windows_paths(tmp_path):
    target = tmp_path / "a.b" / "c d"
    target.mkdir(parents=True)
    encoded = sc._encode_claude_segment(str(target))
    env = sc.Env(system="Windows")
    assert os.path.normcase(str(sc.decode_claude_project(env, encoded))) == os.path.normcase(str(target))


def test_repos_are_found_from_all_three_sources(linux_machine, make_repo, monkeypatch):
    m = linux_machine
    walked = make_repo(m.home / "Projects" / "walked")            # capital P: matched on any OS
    given = make_repo(m.home / "elsewhere" / "given")
    via_claude = make_repo(m.root / "srv" / "via-claude")
    (m.home / ".claude" / "projects" / "-srv-via-claude").mkdir(parents=True)
    monkeypatch.chdir(given / "")
    counters = sc.Counters()
    items, by_source = sc.discover_repos(m.env(), extra_paths=["."], counters=counters)
    by_path = {os.path.normcase(str(i["path"])): i["sources"] for i in items}
    assert by_path[os.path.normcase(os.path.realpath(walked))] == {"common_folders"}
    assert by_path[os.path.normcase(os.path.realpath(given))] == {"given"}
    assert by_path[os.path.normcase(os.path.realpath(via_claude))] == {"claude_projects"}
    assert by_source == {"given": 1, "claude_projects": 1, "common_folders": 1}


def test_named_repos_survive_the_cap(linux_machine, make_repo, monkeypatch):
    m = linux_machine
    for i in range(4):
        make_repo(m.home / "code" / f"r{i}")
    named = make_repo(m.home / "zz-named")
    monkeypatch.setattr(sc, "MAX_REPOS", 2)
    result = sc.collect(m.env(), days=14, extra_repos=[str(named)])
    assert result["repos"]["found"] == 5 and result["repos"]["inspected"] == 2
    assert any("given" in i["found_by"] for i in result["repos"]["items"])
    assert any("only 2 were inspected" in x for x in result["limits"])


def test_linked_worktrees_count_once(linux_machine, make_repo):
    m = linux_machine
    repo = make_repo(m.home / "code" / "main-checkout")
    git(repo, "worktree", "add", "-q", "-b", "wt-a", str(m.home / "code" / "wt-a"))
    git(repo, "worktree", "add", "-q", "-b", "wt-b", str(m.home / "code" / "wt-b"))
    result = sc.collect(m.env(), days=14)
    assert result["repos"]["inspected"] == 1
    assert result["repos"]["other_checkouts_of_same_repo"] == 2
    assert result["repos"]["items"][0]["worktrees"] == 3


def test_subfolder_sessions_map_to_their_repo_but_never_to_home(linux_machine, make_repo):
    m = linux_machine
    repo = make_repo(m.root / "srv" / "mono")
    (repo / "pkg" / "sub").mkdir(parents=True)
    (m.home / ".claude" / "projects" / "-srv-mono-pkg-sub").mkdir(parents=True)
    (m.home / ".git").mkdir()
    (m.home / "notes").mkdir()
    items, _ = sc.discover_repos(m.env(), extra_paths=[m.home / "notes"])
    paths = {os.path.normcase(str(i["path"])) for i in items}
    assert os.path.normcase(os.path.realpath(repo)) in paths
    assert os.path.normcase(os.path.realpath(m.home)) not in paths


def test_an_empty_git_folder_never_falls_through_to_a_parent_repo(linux_machine, make_repo):
    m = linux_machine
    make_repo(m.home)                                       # a dotfiles repository in the home folder
    (m.home / "code" / "half-cloned" / ".git").mkdir(parents=True)
    result = sc.collect(m.env(), days=14)
    assert result["repos"]["inspected"] == 0


def test_permission_errors_do_not_stop_the_scan(linux_machine, make_repo, monkeypatch):
    m = linux_machine
    make_repo(m.home / "code" / "fine")
    blocked = m.home / "code" / "blocked"
    blocked.mkdir(parents=True)
    real_scandir, real_stat = os.scandir, os.stat
    key = os.path.normcase(str(blocked))

    def denied(path):
        p = os.path.normcase(os.path.abspath(os.fsdecode(path))) if not isinstance(path, int) else ""
        return p == key or p.startswith(key + os.sep)

    def guarded_scandir(path="."):
        if denied(path):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    def guarded_stat(path, *a, **k):
        if denied(path):
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(os, "scandir", guarded_scandir)
    monkeypatch.setattr(os, "stat", guarded_stat)
    result = sc.collect(m.env(), days=14)
    assert result["repos"]["inspected"] == 1
    assert any("could not be read" in x for x in result["limits"])


def test_a_long_name_is_never_credited_to_a_shorter_sibling(linux_machine, make_repo):
    m = linux_machine
    make_repo(m.root / "s" / "app")                      # /s/app exists; /s/app-v2/... was deleted
    rest = "-".join(f"segment{i}" for i in range(30))
    full = "-s-app-v2-" + rest
    assert sc.decode_claude_project(m.env(), full[:200] + "-3f2a9c") is None
    assert sc.decode_claude_project(m.env(), full[:200]) is None     # exactly 200: never cut, no fallback


def test_a_named_folder_that_holds_repositories_is_searched(linux_machine, make_repo, tmp_path):
    parent = tmp_path / "src"                            # e.g. C:\src, outside the home folder
    make_repo(parent / "one")
    make_repo(parent / "two")
    counters = sc.Counters()
    _, by_source = sc.discover_repos(linux_machine.env(), extra_paths=[str(parent)], counters=counters)
    assert by_source["given"] == 2 and counters.errors == []


def test_a_tilde_means_the_scanned_home(linux_machine, make_repo):
    m = linux_machine
    make_repo(m.home / "elsewhere" / "given")
    _, by_source = sc.discover_repos(m.env(), extra_paths=["~/elsewhere/given"])
    assert by_source["given"] == 1


def test_linked_code_folders_are_searched(linux_machine, make_repo, tmp_path):
    if not _symlinks_work(tmp_path):
        pytest.skip("symlinks are not available here")
    m = linux_machine
    data = tmp_path / "data" / "code"                    # ~/code is a link to another disk
    make_repo(data / "proj")
    os.symlink(data, m.home / "code", target_is_directory=True)
    other = make_repo(tmp_path / "elsewhere" / "linked-repo")
    (m.home / "src").mkdir()
    os.symlink(other, m.home / "src" / "linked-repo", target_is_directory=True)
    _, by_source = sc.discover_repos(m.env())
    assert by_source["common_folders"] == 2


def test_a_bare_repository_with_worktrees_beside_it_is_inspected(linux_machine, make_repo, tmp_path):
    m = linux_machine
    origin = make_repo(tmp_path / "origin")
    commit(origin, "a.txt", "1\n", "bob", days_ago=2)
    proj = m.home / "code" / "proj"                      # proj/.bare, proj/.git -> .bare, proj/main
    proj.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--bare", str(origin), str(proj / ".bare"))
    (proj / ".git").write_text("gitdir: ./.bare\n", encoding="utf-8")
    git(proj, "worktree", "add", "-q", str(proj / "main"), "main")
    result = sc.collect(m.env(), days=14)
    assert result["repos"]["inspected"] == 1 and result["errors"] == []
    item = result["repos"]["items"][0]
    assert item["worktrees"] == 1 and item["commits_in_window"] == 1


def test_worktrees_count_only_real_checkouts(linux_machine, make_repo):
    m = linux_machine
    repo = make_repo(m.home / "code" / "r")
    git(repo, "worktree", "add", "-q", "-b", "gone", str(m.home / "code" / "gone-wt"))
    shutil.rmtree(m.home / "code" / "gone-wt")           # deleted by hand: git calls it prunable
    item, _ = sc.inspect_repo(repo, m.env(), 14, sc.Hasher())
    assert item["worktrees"] == 1


def test_repository_inspection_has_a_time_budget_and_reports_progress(linux_machine, make_repo, monkeypatch):
    m = linux_machine
    make_repo(m.home / "code" / "a")
    make_repo(m.home / "code" / "b")
    calls = []
    result = sc.collect(m.env(), days=14, progress=lambda i, n: calls.append((i, n)))
    assert calls == [(1, 2), (2, 2)] and result["repos"]["inspected"] == 2
    duration = result["run"]["duration_seconds"]
    assert isinstance(duration, int) and 0 <= duration < 600
    monkeypatch.setattr(sc, "REPO_TIME_BUDGET", -1)
    result = sc.collect(m.env(), days=14)
    assert result["repos"]["inspected"] == 0
    assert any("repository inspection stopped" in x for x in result["limits"])


# ----------------------------------------------------------------- git: read-only, no network, no programs


def test_git_commands_outside_the_allowlist_are_refused(tmp_path):
    env = sc.Env.synthetic(tmp_path, "Linux")
    refused = [["status"], ["remote", "add", "x", "y"], ["symbolic-ref", "HEAD", "refs/heads/x"],
               ["config", "user.name", "x"], ["config", "--get", "alias.x"], ["worktree", "add", "x"],
               ["fetch"], ["gc"], ["log", "--output=x", "--all"], ["log", "--outpu=x", "--all"],
               ["log", "--all", "-p"], ["log", "--format=x", "--end-of-options", "--output=y"],
               ["log", "--end-of-options", "refs/heads/a..refs/heads/b", "--output=z"],
               ["log", "--format=x"], ["merge-base", "--octopus", "refs/heads/a"],
               ["rev-parse", "--verify", "--quiet", "--output=x"], ["for-each-ref", "--format=x", "--sort=x"],
               # Formats that make git check signatures, which runs gpg.program from the repo config.
               ["log", "--all", "--format=%G?"], ["log", "--all", "--format=%GS"],
               ["for-each-ref", "--format=%(signature)", "refs/heads"],
               ["for-each-ref", sc._FMT_REFS, "--sort=-committerdate", "--no-merged=--output=x",
                "refs/heads", "refs/remotes"],
               ["log", "--all", "--since=yesterday"], ["log", "--all", "--since=1.days.ago --output=x"]]
    for args in refused:
        with pytest.raises(ValueError):
            sc._git(tmp_path, args, env)


def test_git_is_never_started_from_the_current_folder(linux_machine, make_repo, tmp_path, monkeypatch):
    m = linux_machine
    repo = make_repo(m.home / "code" / "r")
    downloads = tmp_path / "Downloads"                   # where a participant runs the scanner from
    downloads.mkdir()
    for name in ("git", "git.exe", "git.cmd"):
        fake = downloads / name
        fake.write_text("", encoding="utf-8")
        fake.chmod(0o755)
    monkeypatch.chdir(downloads)
    env = m.env(PATH=os.pathsep.join([".", "", os.environ["PATH"]]))
    program = env.git_program()
    assert program and os.path.isabs(program)
    assert os.path.normcase(os.path.dirname(program)) != os.path.normcase(str(downloads))
    started = []
    real_run = subprocess.run

    def spy(cmd, *a, **k):
        started.append(cmd[0])
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    item, _ = sc.inspect_repo(repo, env, 14, sc.Hasher())
    assert item is not None and started and set(started) == {program}
    assert m.env(PATH=".").git_program() is None
    assert m.env(PATH=".").which("git") is None


def test_every_git_command_carries_the_safety_switches(linux_machine, make_repo, monkeypatch):
    # core.fsmonitor=false cannot be observed through the read-only commands used (none reads
    # the index), so its presence on every command line is checked instead.
    m = linux_machine
    repo = make_repo(m.home / "code" / "r")
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "a.py", "1\n", "bob", days_ago=1)
    git(repo, "checkout", "-q", "main")
    seen = []
    real_run = subprocess.run

    def spy(cmd, *a, **k):
        seen.append((list(cmd), dict(k.get("env") or {})))
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    sc.collect(m.env(GIT_DIR="x", GIT_CONFIG_PARAMETERS="'core.fsmonitor=evil'"), days=14)
    assert len(seen) > 10
    set_here = {"GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT", "GIT_NO_LAZY_FETCH", "GIT_ALLOW_PROTOCOL",
                "GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF", "GIT_CEILING_DIRECTORIES", "GIT_CONFIG_NOSYSTEM"}
    for cmd, genv in seen:
        assert os.path.isabs(cmd[0])
        for switch in ("core.fsmonitor=false", "core.untrackedCache=false", "log.showSignature=false",
                       "protocol.allow=never"):
            assert switch in cmd, f"{switch} missing from {cmd[3:]}"
        assert genv["GIT_NO_LAZY_FETCH"] == "1" and genv["GIT_ALLOW_PROTOCOL"] == "none"
        assert genv["GIT_OPTIONAL_LOCKS"] == "0" and genv["GIT_CONFIG_NOSYSTEM"] == "1"
        assert {k for k in genv if k.upper().startswith("GIT_")} <= set_here


def test_option_like_origin_head_is_ignored(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "r")
    ref = repo / ".git" / "refs" / "remotes" / "origin" / "HEAD"
    ref.parent.mkdir(parents=True)
    ref.write_text("ref: --output=PWNED.txt\n", encoding="utf-8")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item is not None and item["default_branch_found"]      # fell back to refs/heads/main
    assert not list(repo.rglob("PWNED*")) and not list(Path.cwd().glob("PWNED*"))


def test_dangling_origin_head_falls_back(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "r")
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "a.py", "1\n", "bob", days_ago=2)
    git(repo, "checkout", "-q", "main")
    commit(repo, "a.py", "2\n", "carol", days_ago=1)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")   # points nowhere
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 1 and item["cross_author_overlap_pairs"] == 1


def test_inherited_git_variables_cannot_redirect_git(linux_machine, make_repo, tmp_path):
    m = linux_machine
    repo = make_repo(m.home / "code" / "real")
    other = make_repo(tmp_path / "decoy")
    commit(other, "x.py", "1\n", "mallory", days_ago=1)
    env = m.env(GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other))
    item, _ = sc.inspect_repo(repo, env, 14, sc.Hasher())
    assert item["authors_in_window"] == 0


def _marker_program(folder, name):
    """A tiny program that records that it ran. Returns (path to use in git config, marker file)."""
    marker = folder / f"{name}.ran"
    script = folder / f"{name}.sh"
    script.write_text(f'#!/bin/sh\necho ran >> "{marker.as_posix()}"\nexit 1\n', encoding="utf-8")
    script.chmod(0o755)
    return script.as_posix(), marker


def test_signature_checks_and_fsmonitor_never_run_programs(linux_machine, make_repo, tmp_path):
    m = linux_machine
    repo = make_repo(m.home / "code" / "signed")
    gpg, gpg_marker = _marker_program(tmp_path, "gpg")
    fsm, fsm_marker = _marker_program(tmp_path, "fsmonitor")
    commit(repo, "b.py", "1\n", "bob", days_ago=1)
    # Give the recent commit a (fake) signature, so a signature check would call gpg.program.
    raw = git(repo, "cat-file", "commit", "HEAD").decode()
    head, _, body = raw.partition("\n\n")
    signed = head + "\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEz\n -----END PGP SIGNATURE-----\n\n" + body
    sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo, input=signed.encode(),
                         capture_output=True, check=True).stdout.decode().strip()
    git(repo, "update-ref", "refs/heads/main", sha)
    git(repo, "reset", "-q", "--hard", sha)
    # Set the dangerous config only now, so the fixture's own git commands cannot trigger it.
    git(repo, "config", "log.showSignature", "true")
    git(repo, "config", "gpg.program", gpg)
    git(repo, "config", "core.fsmonitor", fsm)
    item, _ = sc.inspect_repo(repo, m.env(), 14, sc.Hasher())
    assert item is not None
    assert not gpg_marker.exists(), "gpg.program ran during the scan"
    assert not fsm_marker.exists(), "core.fsmonitor ran during the scan"


def test_trace2_targets_in_the_users_git_config_write_nothing(linux_machine, make_repo, tmp_path):
    m = linux_machine
    repo = make_repo(m.home / "code" / "r")
    log = tmp_path / "trace2-event.log"
    user_home = tmp_path / "git-home"
    user_home.mkdir()
    (user_home / ".gitconfig").write_text(f"[trace2]\n\teventTarget = {log.as_posix()}\n", encoding="utf-8")
    env = m.env(HOME=str(user_home), USERPROFILE=str(user_home))
    sc.inspect_repo(repo, env, 14, sc.Hasher())
    assert not log.exists(), "a trace2 log was written"


def test_treeless_partial_clone_fetches_nothing_and_writes_nothing(linux_machine, make_repo, tmp_path):
    m = linux_machine
    origin = make_repo(tmp_path / "origin")
    for i in range(3):
        commit(origin, f"f{i}.py", f"{i}\n", "bob", days_ago=2 - i * 0.5)
    git(origin, "checkout", "-q", "-b", "feat")                 # work in flight: git must read its trees
    commit(origin, "f0.py", "feat\n", "carol", days_ago=0.5)
    git(origin, "checkout", "-q", "main")
    git(origin, "config", "uploadpack.allowFilter", "true")
    clone = m.home / "code" / "treeless"
    git(tmp_path, "clone", "-q", "--filter=tree:0", "--no-checkout", origin.as_uri(), str(clone))
    # A common setting since git 2.38.1. It beats -c protocol.allow=never, so only
    # GIT_NO_LAZY_FETCH (git >= 2.44) and GIT_ALLOW_PROTOCOL stand between it and a fetch.
    git(clone, "config", "protocol.file.allow", "always")
    upload, marker = _marker_program(tmp_path, "uploadpack")
    git(clone, "config", "remote.origin.uploadpack", f"{upload}; git-upload-pack")
    before = snapshot_tree(clone / ".git")
    item, _ = sc.inspect_repo(clone, m.env(), 14, sc.Hasher())
    assert snapshot_tree(clone / ".git") == before, "the scan wrote into .git (a lazy fetch?)"
    assert not marker.exists(), "a transport program ran"
    assert item is None or item["partial_clone"] is True


def test_blobless_partial_clone_still_counts_work_in_flight(linux_machine, make_repo, tmp_path):
    # Comparing patches (--cherry-pick) needs file contents, which a blobless clone lacks for a
    # branch never checked out; the scan falls back to comparing commits, which needs trees only.
    m = linux_machine
    origin = make_repo(tmp_path / "origin")
    commit(origin, "shared.py", "base\n", "alice", days_ago=10)
    git(origin, "checkout", "-q", "-b", "feat-bob")
    commit(origin, "shared.py", "bob\n", "bob", days_ago=3)
    git(origin, "checkout", "-q", "main")
    commit(origin, "shared.py", "carol\n", "carol", days_ago=2)
    git(origin, "config", "uploadpack.allowFilter", "true")
    clone = m.home / "code" / "blobless"
    git(tmp_path, "clone", "-q", "--filter=blob:none", origin.as_uri(), str(clone))
    git(clone, "config", "protocol.file.allow", "always")
    upload, marker = _marker_program(tmp_path, "uploadpack")
    git(clone, "config", "remote.origin.uploadpack", f"{upload}; git-upload-pack")
    before = snapshot_tree(clone / ".git")
    item, _ = sc.inspect_repo(clone, m.env(), 14, sc.Hasher())
    assert snapshot_tree(clone / ".git") == before, "the scan wrote into .git (a lazy fetch?)"
    assert not marker.exists(), "a transport program ran"
    assert item["partial_clone"] is True
    assert item["in_flight_branches"] == 1 and item["cross_author_overlap_pairs"] == 1
    assert item["branches_checked_by_commit_only"] == 1 and item["branches_skipped"]["unreadable"] == 0


# ----------------------------------------------------------------- work in flight by different people


def test_cross_author_overlap_and_no_writes(linux_machine, make_repo):
    m = linux_machine
    repo = make_repo(m.home / "code" / "team")
    commit(repo, "shared.py", "base\n", "alice", days_ago=10)
    git(repo, "checkout", "-q", "-b", "feat-bob")
    commit(repo, "shared.py", "bob\n", "bob", days_ago=3)
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "feat-carol")
    commit(repo, "shared.py", "carol\n", "carol", days_ago=2)
    commit(repo, "other.py", "carol\n", "carol", days_ago=2)
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "feat-bob-2")
    commit(repo, "solo.py", "bob\n", "bob", days_ago=1)
    git(repo, "checkout", "-q", "main")
    git(repo, "update-ref", "refs/remotes/origin/feat-bob", "refs/heads/feat-bob")   # the same stream again
    before = snapshot_tree(repo / ".git")
    item, errors = sc.inspect_repo(repo, m.env(), 14, sc.Hasher())
    assert snapshot_tree(repo / ".git") == before, "the scanner changed something inside .git"
    assert errors == []
    assert item["in_flight_branches"] == 3
    assert item["cross_author_overlap_pairs"] == 1          # bob vs carol on shared.py
    assert item["cross_author_overlap_files"] == 1
    assert item["authors_in_window"] == 3
    assert item["worktrees"] == 1


def test_default_branch_moving_under_a_branch_counts(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "moving")
    git(repo, "checkout", "-q", "-b", "feat-dan")
    commit(repo, "api.py", "dan\n", "dan", days_ago=4)
    git(repo, "checkout", "-q", "main")
    commit(repo, "api.py", "erin\n", "erin", days_ago=2)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 1
    assert item["cross_author_overlap_pairs"] == 1 and item["cross_author_overlap_files"] == 1


def test_one_person_on_the_shared_file_is_not_an_overlap(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "r")
    git(repo, "checkout", "-q", "-b", "feat-a")
    commit(repo, "f.txt", "a1\n", "alice", days_ago=3)
    commit(repo, "g.txt", "b1\n", "bob", days_ago=3)
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "feat-b")
    commit(repo, "f.txt", "a2\n", "alice", days_ago=2)
    git(repo, "checkout", "-q", "main")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 2 and item["cross_author_overlap_pairs"] == 0


def test_squash_merged_branch_is_not_in_flight(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "squash")
    git(repo, "checkout", "-q", "-b", "feat-s")
    commit(repo, "f.txt", "one\n", "bob", days_ago=5)
    commit(repo, "f.txt", "two\n", "bob", days_ago=4.5)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--squash", "feat-s")
    git(repo, "commit", "-q", "-m", "feat-s (#12)", env={"GIT_AUTHOR_NAME": "bob", "GIT_AUTHOR_EMAIL": "bob@example.com"})
    commit(repo, "f.txt", "three\n", "alice", days_ago=1)       # later work by someone else
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 0
    assert item["branches_skipped"]["squash_merged"] == 1
    assert item["cross_author_overlap_pairs"] == 0


def test_rebase_merged_branch_is_not_in_flight(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "rebase")
    git(repo, "checkout", "-q", "-b", "feat-r")
    commit(repo, "f.txt", "one\n", "bob", days_ago=5)
    git(repo, "checkout", "-q", "main")
    commit(repo, "g.txt", "x\n", "alice", days_ago=4)
    git(repo, "cherry-pick", "feat-r")                          # same patch, new commit, as a rebase merge makes
    commit(repo, "f.txt", "later\n", "alice", days_ago=1)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 0 and item["cross_author_overlap_pairs"] == 0


def test_orphan_branch_is_skipped(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "pages")
    git(repo, "checkout", "-q", "--orphan", "gh-pages")
    git(repo, "rm", "-rq", "--cached", ".")
    commit(repo, "index.html", "site\n", "ci-bot", days_ago=2)
    git(repo, "checkout", "-q", "-f", "main")
    commit(repo, "index.html", "main\n", "frank", days_ago=1)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["branches_skipped"]["unrelated_history"] == 1
    assert item["cross_author_overlap_pairs"] == 0


def test_bot_and_trailer_commits_are_counted(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "bots")
    commit(repo, "a.py", "1\n", "copilot-swe-agent[bot]", days_ago=1)
    (repo / "b.py").write_text("2\n", encoding="utf-8")
    git(repo, "add", "b.py")
    git(repo, "commit", "-q", "-m", "change b\n\nCo-authored-by: Agent <agent@example.invalid>")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["bot_authored_commits"] == {"copilot": 1}
    assert item["agent_trailer_commits"] == {"claude": 1}


def _commit_staged(repo, author, days_ago, message="change"):
    when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days_ago * 86400))
    git(repo, "commit", "-q", "-m", message, date=when,
        env={"GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@example.com"})


def test_cloud_and_local_agent_commits_never_count_the_same_commit(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "agents")
    # Cloud agents credit themselves in a trailer too; per-agent tallies count those twice.
    for i, (author, trailer) in enumerate((
            ("copilot-swe-agent[bot]", "Copilot <198982749+Copilot@users.noreply.github.com>"),
            ("claude[bot]", "Claude <agent@example.invalid>"),
            ("bob", "Claude <agent@example.invalid>"),
            ("dependabot[bot]", None))):
        message = f"change {i}" + (f"\n\nCo-authored-by: {trailer}" if trailer else "")
        commit(repo, f"f{i}.txt", "x\n", author, days_ago=1, message=message)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["bot_authored_commits"] == {"copilot": 1, "claude": 1}
    assert item["agent_trailer_commits"] == {"copilot": 1, "claude": 2}
    assert (item["cloud_agent_commits"], item["local_agent_commits"], item["other_bot_commits"]) == (2, 1, 1)


def test_dependency_bots_are_not_people_in_the_overlap(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "solo")
    commit(repo, "package.json", "{}\n", "alice", days_ago=5)
    git(repo, "checkout", "-q", "-b", "dependabot/npm_and_yarn/a-1.1")       # an open Dependabot branch
    commit(repo, "package.json", '{"a": "1.1"}\n', "dependabot[bot]", days_ago=2)
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "feat-mixed")                           # a person and a bot on one branch
    commit(repo, "src.py", "alice\n", "alice", days_ago=2)
    commit(repo, "package.json", '{"c": 1}\n', "renovate[bot]", days_ago=2)
    git(repo, "checkout", "-q", "main")
    commit(repo, "package.json", '{"b": 1}\n', "alice", days_ago=1)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 1 and item["branches_skipped"]["bot_only"] == 1
    assert item["cross_author_overlap_pairs"] == 0
    assert item["other_bot_commits"] == 2


def test_squash_merge_is_recognised_when_branch_changes_cancel_out(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "scratch")
    commit(repo, "f.txt", "base\n", "alice", days_ago=10)
    git(repo, "checkout", "-q", "-b", "feat-s")
    commit(repo, "notes.tmp", "scratch\n", "bob", days_ago=5)                # a scratch file, later deleted
    commit(repo, "f.txt", "bob\n", "bob", days_ago=5)
    git(repo, "rm", "-q", "notes.tmp")
    _commit_staged(repo, "bob", 4.5)
    commit(repo, "g.txt", "bob\n", "bob", days_ago=4.5)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--squash", "feat-s")
    _commit_staged(repo, "bob", 4, "feat-s (#7)")
    commit(repo, "f.txt", "carol\n", "carol", days_ago=1)                      # later work by someone else
    git(repo, "checkout", "-q", "-b", "only-scratch")                         # changes that cancel out entirely
    commit(repo, "tmp.txt", "x\n", "dave", days_ago=2)
    git(repo, "rm", "-q", "tmp.txt")
    _commit_staged(repo, "dave", 2)
    git(repo, "checkout", "-q", "main")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 0 and item["cross_author_overlap_pairs"] == 0
    assert item["branches_skipped"]["squash_merged"] == 1 and item["branches_skipped"]["no_net_change"] == 1


def test_squash_after_merging_the_default_branch_in_is_recognised(linux_machine, make_repo):
    # GitHub's "Update branch" merges the default branch in (resolving a conflict), then
    # "Squash and merge" lands the branch as one commit.
    repo = make_repo(linux_machine.home / "code" / "update-branch")
    commit(repo, "f.txt", "base\n", "alice", days_ago=10)
    git(repo, "checkout", "-q", "-b", "feat-b")
    commit(repo, "f.txt", "bob\n", "bob", days_ago=5)
    commit(repo, "h.txt", "bob\n", "bob", days_ago=5)
    git(repo, "checkout", "-q", "main")
    commit(repo, "f.txt", "carol\n", "carol", days_ago=4)
    git(repo, "checkout", "-q", "feat-b")
    with pytest.raises(subprocess.CalledProcessError):
        git(repo, "merge", "-q", "main")                                      # conflict on f.txt
    (repo / "f.txt").write_text("bob and carol\n", encoding="utf-8")
    git(repo, "add", "f.txt")
    _commit_staged(repo, "bob", 3.5, "Merge main into feat-b")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--squash", "feat-b")
    _commit_staged(repo, "bob", 3, "feat-b (#9)")
    commit(repo, "f.txt", "dave\n", "dave", days_ago=1)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 0 and item["cross_author_overlap_pairs"] == 0
    assert item["branches_skipped"]["squash_merged"] == 1


def test_the_branch_cap_skips_merged_branches_first(linux_machine, make_repo, monkeypatch):
    repo = make_repo(linux_machine.home / "code" / "busy")
    when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3 * 86400))
    for i in range(4):                                  # merged PR branches that were never deleted
        git(repo, "checkout", "-q", "-b", f"aa-merged-{i}")
        commit(repo, f"m{i}.txt", "x\n", "bob", days_ago=3)
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "-q", "--no-ff", "-m", f"merge {i}", f"aa-merged-{i}", date=when)
    git(repo, "checkout", "-q", "-b", "zz-topic")
    commit(repo, "shared.txt", "carol\n", "carol", days_ago=2)
    git(repo, "checkout", "-q", "main")
    commit(repo, "shared.txt", "dave\n", "dave", days_ago=1)
    monkeypatch.setattr(sc, "MAX_REFS", 2)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 1 and item["cross_author_overlap_pairs"] == 1
    assert item["branch_cap_hit"] is False


def test_a_git_timeout_on_one_branch_keeps_the_rest_of_the_repository(linux_machine, make_repo, monkeypatch):
    repo = make_repo(linux_machine.home / "code" / "slow")
    for branch, path, who in (("feat-a", "x.py", "bob"), ("feat-slow", "y.py", "carol")):
        git(repo, "checkout", "-q", "-b", branch)
        commit(repo, path, f"{who}\n", who, days_ago=3)
        git(repo, "checkout", "-q", "main")
    commit(repo, "x.py", "dave\n", "dave", days_ago=1)
    real_git = sc._git

    def slow(repo_, args, env):
        if args[0] in ("log", "merge-base") and any("feat-slow" in a for a in args):
            raise subprocess.TimeoutExpired(args, sc.GIT_TIMEOUT)
        return real_git(repo_, args, env)

    monkeypatch.setattr(sc, "_git", slow)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item is not None and item["branches_skipped"]["unreadable"] == 1
    assert item["in_flight_branches"] == 1 and item["cross_author_overlap_pairs"] == 1


def test_one_stream_of_work_counts_once(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "stacked")
    commit(repo, "x.py", "base\n", "alice", days_ago=10)
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "x.py", "bob 1\n", "bob", days_ago=3)
    git(repo, "update-ref", "refs/remotes/origin/feat", "refs/heads/feat")   # pushed ...
    commit(repo, "x.py", "bob 2\n", "bob", days_ago=2.5)                     # ... and one commit ahead
    git(repo, "checkout", "-q", "-b", "feat-2")                               # stacked on top
    commit(repo, "y.py", "bob\n", "bob", days_ago=2)
    git(repo, "checkout", "-q", "main")
    commit(repo, "x.py", "carol\n", "carol", days_ago=1)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 1 and item["cross_author_overlap_pairs"] == 1


def _commit_as(repo, path, text, name, email, days_ago, message=None):
    (repo / path).write_text(text, encoding="utf-8")
    when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days_ago * 86400))
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message or f"change {path}", date=when,
        env={"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email})


def _two_identity_repo(repo, second_name, second_email):
    """Alice's branch changes x.py as alice@work.example. After it forked, the default branch
    gains a squash merge of another pull request that also changes x.py, authored by the
    second identity."""
    _commit_as(repo, "x.py", "base\n", "Alice Smith", "alice@work.example", 20)
    git(repo, "checkout", "-q", "-b", "feat-b")
    _commit_as(repo, "x.py", "branch b\n", "Alice Smith", "alice@work.example", 3)
    git(repo, "checkout", "-q", "main")
    _commit_as(repo, "x.py", "other pr\n", second_name, second_email, 2, "Other PR (#4)")


def test_one_person_with_two_addresses_is_not_a_pair(linux_machine, make_repo):
    # The GitHub web UI squash-merges under the noreply address when email privacy is on.
    for name, email in (("Alice Smith", "1234567+alice-s@users.noreply.github.com"),   # same name
                        ("A. S.", "1234567+alicesmith@users.noreply.github.com"),       # the login matches her name
                        ("alice smith", "alice@home.example")):                        # another machine
        repo = make_repo(linux_machine.home / "code" / f"ids-{email.split('@')[0][-9:]}")
        _two_identity_repo(repo, name, email)
        item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
        assert item["in_flight_branches"] == 1
        assert item["cross_author_overlap_pairs"] == 0, (name, email)
        assert item["cross_email_overlap_pairs"] == 1                  # the strict count still shows it
        assert item["authors_in_window"] == 2 and item["people_in_window"] == 1


def test_two_people_are_still_a_pair(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "two-people")
    _two_identity_repo(repo, "Carol Jones", "carol@work.example")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["cross_author_overlap_pairs"] == 1 and item["cross_email_overlap_pairs"] == 1
    assert item["people_in_window"] == 2


def test_two_people_who_share_a_name_count_as_one(linux_machine, make_repo):
    # A known limit, listed in the README: identities are grouped by name as well as email.
    repo = make_repo(linux_machine.home / "code" / "same-name")
    _two_identity_repo(repo, "Alice Smith", "alice.smith@other-company.example")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["cross_author_overlap_pairs"] == 0 and item["cross_email_overlap_pairs"] == 1


def test_mailmap_groups_identities_only_when_safe_to_read(linux_machine, make_repo, tmp_path, monkeypatch):
    mapping = "Alice Smith <alice@work.example> A. S. <al@laptop.example>\n"
    repo = make_repo(linux_machine.home / "code" / "mailmap")
    _two_identity_repo(repo, "A. S.", "al@laptop.example")          # neither name nor email matches
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["mailmap_read"] is True and item["cross_author_overlap_pairs"] == 1
    (repo / ".mailmap").write_text(mapping, encoding="utf-8")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["mailmap_read"] is True and item["cross_author_overlap_pairs"] == 0
    if not _symlinks_work(tmp_path):
        return
    (repo / ".mailmap").unlink()
    outside = tmp_path / "elsewhere.mailmap"
    outside.write_text(mapping, encoding="utf-8")
    os.symlink(outside, repo / ".mailmap")          # a link: never handed to git (older git follows it)
    formats = []
    real_run = subprocess.run

    def spy(cmd, *a, **k):
        formats.extend(x for x in cmd if x.startswith("--format="))
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["mailmap_read"] is False and item["cross_author_overlap_pairs"] == 1
    assert not any("%aN" in f or "%aE" in f for f in formats)


def _lines(**edits):
    out = [f"line {i}" for i in range(30)]
    for key, value in edits.items():
        out[int(key[1:])] = value
    return "\n".join(out) + "\n"


def test_work_merged_after_the_default_branch_moved_is_not_in_flight(linux_machine, make_repo):
    # Squash- and rebase-merges made after the default branch changed the same file match
    # neither by blob nor by patch. Stale refs of merged pull requests are normal (no prune).
    repo = make_repo(linux_machine.home / "code" / "moved")
    commit(repo, "f.txt", _lines(), "alice", days_ago=20)
    commit(repo, "g.txt", _lines(), "alice", days_ago=20)
    git(repo, "checkout", "-q", "-b", "feat-squash")
    commit(repo, "f.txt", _lines(l2="bob a"), "bob", days_ago=6)
    commit(repo, "f.txt", _lines(l2="bob a", l8="bob b"), "bob", days_ago=5.8)
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "feat-rebase")
    commit(repo, "g.txt", _lines(l10="bob change"), "bob", days_ago=6)
    git(repo, "checkout", "-q", "main")
    commit(repo, "f.txt", _lines(l25="bob hotfix"), "bob", days_ago=5)       # the base moves
    commit(repo, "g.txt", _lines(l12="bob nearby"), "bob", days_ago=5)
    git(repo, "merge", "-q", "--squash", "feat-squash")
    _commit_staged(repo, "bob", 4, "feat-squash (#3)")
    git(repo, "checkout", "-q", "-b", "tmp", "feat-rebase")                   # "Rebase and merge"
    git(repo, "rebase", "-q", "main")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--ff-only", "tmp")
    git(repo, "branch", "-q", "-D", "tmp")
    commit(repo, "f.txt", _lines(l2="bob a", l8="bob b", l25="bob hotfix", l15="carol"), "carol", days_ago=1)
    commit(repo, "g.txt", _lines(l10="bob change", l12="bob nearby", l20="dave"), "dave", days_ago=1)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 0 and item["cross_author_overlap_pairs"] == 0
    assert item["branches_skipped"]["probably_merged"] == 2


def test_branches_merged_into_an_integration_branch_can_still_collide(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "staging")
    commit(repo, "f.txt", _lines(), "alice", days_ago=20)
    for branch, who, edit in (("f1", "bob", {"l2": "bob"}), ("f2", "carol", {"l20": "carol"})):
        git(repo, "checkout", "-q", "-b", branch, "main")
        commit(repo, "f.txt", _lines(**edit), who, days_ago=3)
    git(repo, "checkout", "-q", "-b", "staging", "main")                      # both merged to test them together
    when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 2 * 86400))
    git(repo, "merge", "-q", "--no-ff", "-m", "f1", "f1", date=when)
    git(repo, "merge", "-q", "--no-ff", "-m", "f2", "f2", date=when)
    git(repo, "checkout", "-q", "main")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 1 and item["cross_author_overlap_pairs"] == 1


def test_a_rebased_copy_of_a_branch_counts_once(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "diverged")
    commit(repo, "f.txt", _lines(), "alice", days_ago=20)
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "f.txt", _lines(l2="bob"), "bob", days_ago=4)
    git(repo, "update-ref", "refs/remotes/origin/feat", "refs/heads/feat")    # a reviewer's stale copy
    git(repo, "checkout", "-q", "main")
    commit(repo, "f.txt", _lines(l20="carol"), "carol", days_ago=3)
    git(repo, "checkout", "-q", "feat")
    git(repo, "rebase", "-q", "main")                                         # bob rebases onto carol's change
    git(repo, "checkout", "-q", "main")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 1 and item["cross_author_overlap_pairs"] == 0


def test_a_merge_diff_setting_in_the_repository_changes_nothing(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "diffmerges")
    git(repo, "config", "log.diffMerges", "combined")
    commit(repo, "f.txt", "base\n", "alice", days_ago=10)
    git(repo, "checkout", "-q", "-b", "feat-b")
    commit(repo, "f.txt", "bob\n", "bob", days_ago=5)
    git(repo, "checkout", "-q", "main")
    commit(repo, "f.txt", "carol\n", "carol", days_ago=4)
    git(repo, "checkout", "-q", "feat-b")
    with pytest.raises(subprocess.CalledProcessError):
        git(repo, "merge", "-q", "main")
    (repo / "f.txt").write_text("bob and carol\n", encoding="utf-8")
    git(repo, "add", "f.txt")
    _commit_staged(repo, "bob", 3.5, "Merge main into feat-b")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--squash", "feat-b")
    _commit_staged(repo, "bob", 3, "feat-b (#9)")
    commit(repo, "f.txt", "dave\n", "dave", days_ago=1)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 0 and item["branches_skipped"]["squash_merged"] == 1


def test_patch_equal_work_by_someone_else_is_not_in_flight(linux_machine, make_repo):
    # Only the patch comparison (--cherry-pick) catches this: new author, new date, same changes.
    repo = make_repo(linux_machine.home / "code" / "reapplied")
    commit(repo, "f.txt", _lines(), "alice", days_ago=20)
    commit(repo, "g.txt", _lines(), "alice", days_ago=20)
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "f.txt", _lines(l2="bob a"), "bob", days_ago=6)
    commit(repo, "g.txt", _lines(l8="bob b"), "bob", days_ago=5.8)
    git(repo, "checkout", "-q", "main")
    commit(repo, "f.txt", _lines(l2="bob a"), "erin", days_ago=5)            # the same two patches, re-applied
    commit(repo, "g.txt", _lines(l8="bob b"), "erin", days_ago=5)
    commit(repo, "f.txt", _lines(l2="bob a", l15="carol"), "carol", days_ago=1)
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 0 and item["cross_author_overlap_pairs"] == 0


def test_default_branch_changes_older_than_the_window_do_not_pair(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "old-change")
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "f.txt", "bob\n", "bob", days_ago=3)
    git(repo, "checkout", "-q", "main")
    commit(repo, "f.txt", "carol\n", "carol", days_ago=20)                    # after the fork, before the window
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["in_flight_branches"] == 1 and item["cross_author_overlap_pairs"] == 0


def test_agents_run_by_ci_bots_count_as_cloud_and_stashes_are_not_commits(linux_machine, make_repo):
    repo = make_repo(linux_machine.home / "code" / "ci")
    commit(repo, "a.txt", "x\n", "github-actions[bot]", days_ago=1,
           message="fix\n\nCo-authored-by: Agent <agent@example.invalid>")
    (repo / "README.md").write_text("work in progress\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("x\n", encoding="utf-8")
    git(repo, "stash", "push", "-q", "-u")
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["commits_in_window"] == 1
    assert (item["cloud_agent_commits"], item["local_agent_commits"], item["other_bot_commits"]) == (1, 0, 0)


def test_shallow_clones_are_reported_not_mistaken_for_unrelated_history(linux_machine, make_repo, tmp_path):
    m = linux_machine
    origin = make_repo(tmp_path / "origin")
    for i in range(5):
        commit(origin, "base.txt", f"{i}\n", "alice", days_ago=9 - i)
    git(origin, "checkout", "-q", "-b", "feat-bob")
    commit(origin, "shared.py", "bob\n", "bob", days_ago=3)
    git(origin, "checkout", "-q", "main")
    for i in range(4):
        commit(origin, "shared.py", f"carol {i}\n", "carol", days_ago=2 - i * 0.1)
    clone = m.home / "code" / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "2", "--no-single-branch", origin.as_uri(), str(clone))
    result = sc.collect(m.env(), days=14)
    item = result["repos"]["items"][0]
    assert item["shallow_clone"] is True and item["branches_skipped"]["unrelated_history"] == 0
    assert item["branches_skipped"]["unreadable"] == 1
    assert any("shallow clones" in x for x in result["limits"])


def test_a_linked_hook_is_counted_but_never_followed(linux_machine, make_repo, tmp_path):
    if not _symlinks_work(tmp_path):
        pytest.skip("symlinks are not available here")
    repo = make_repo(linux_machine.home / "code" / "hooks")
    os.symlink(tmp_path / "missing-hook", repo / ".git" / "hooks" / "pre-commit")   # dangling: counted by name
    item, _ = sc.inspect_repo(repo, linux_machine.env(), 14, sc.Hasher())
    assert item["active_git_hooks"] == 1


def test_repositories_in_cloud_synced_folders_are_not_inspected(linux_machine, make_repo, monkeypatch):
    m = linux_machine
    make_repo(m.home / "code" / "synced")
    make_repo(m.home / "code" / "local")
    real_lstat = os.lstat
    synced = os.path.normcase(str(m.home / "code" / "synced" / ".git"))

    class Placeholder:
        st_flags = 0x40000000                   # macOS: SF_DATALESS

        def __init__(self, st):
            self._st = st

        def __getattr__(self, name):
            return getattr(self._st, name)

    def fake_lstat(p, *a, **k):
        st = real_lstat(p, *a, **k)
        same = not isinstance(p, int) and os.path.normcase(os.fsdecode(p)) == synced
        return Placeholder(st) if same else st

    monkeypatch.setattr(os, "lstat", fake_lstat)
    result = sc.collect(m.env(), days=14)
    assert result["repos"]["inspected"] == 1
    assert any("cloud-synced" in x for x in result["limits"])


def test_future_dated_session_files_never_stop_the_scan(linux_machine):
    m = linux_machine
    path = m.claude_sessions(ages=(1,))[0]
    os.utime(path, (32536850000, 32536850000))                                  # the year 3001
    result = sc.collect(m.env(), days=14)
    assert result["agents"]["claude-code"]["sessions"]["sessions"] == 0
    assert any("dated in the future" in x for x in result["limits"])


def test_network_and_driveless_path_entries_are_never_searched(tmp_path):
    entries = ["\\\\srv.invalid\\tools\\bin", "//srv.invalid/bin", "/net/srv.invalid/bin", "\\rooted\\bin",
               str(tmp_path)]
    env = sc.Env.synthetic(tmp_path, "Linux", path=os.pathsep.join(entries))
    assert env.path_dirs() == [str(tmp_path)]


# ----------------------------------------------------------------- helpers and portability


def test_max_overlap():
    assert sc._max_overlap([]) == 0
    assert sc._max_overlap([(0, 10), (5, 15), (12, 20)]) == 2
    assert sc._max_overlap([(0, 1), (1, 2)]) == 1            # touching intervals do not overlap
    assert sc._max_overlap([(0, 10), (1, 9), (2, 8)]) == 3


def test_hashes_are_salted_per_run():
    a, b = sc.Hasher(), sc.Hasher()
    assert a("x") == a("x")
    assert a("x") != b("x")
    assert len(a("x")) == 12


@pytest.mark.parametrize("name", SCANNER_FILES)
def test_source_parses_as_python_3_9(name):
    ast.parse((HERE / name).read_text(encoding="utf-8"), feature_version=(3, 9))


def test_standard_library_only_and_no_network_modules():
    allowed = {"__future__", "argparse", "datetime", "hashlib", "json", "os", "pathlib", "platform", "posixpath", "re",
               "secrets", "shutil", "subprocess", "sys", "time", "winreg"}
    for name in SCANNER_FILES:
        tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        mods.discard("scan_collect")
        assert mods <= allowed, f"{name} imports {sorted(mods - allowed)}"
