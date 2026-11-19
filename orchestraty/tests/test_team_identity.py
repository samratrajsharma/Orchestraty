"""Who this machine shares as: the person (from the git email, through the people file) and the
device (a random id and a secret kept in the state folder, never anything from the host)."""
import json
import os
import re
import socket
import stat
import subprocess
from pathlib import Path

import pytest

from orchestraty.paths import State
from orchestraty.team import grammar, identity
from orchestraty.team.people import People

PEOPLE = "version: 1\npeople:\n  - handle: bob\n    emails: [bob@example.com]\n"


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def test_email_from_repo_config_with_includeIf(repo, git, tmp_path):
    include = tmp_path / "work.gitconfig"
    include.write_text("[user]\n\temail = Inc@Example.com\n", encoding="utf-8")
    top = str(repo).replace("\\", "/")
    with open(os.environ["GIT_CONFIG_GLOBAL"], "a", encoding="utf-8") as fh:
        fh.write(f'[includeIf "gitdir/i:{top}/"]\n\tpath = {str(include).replace(chr(92), "/")}\n')
    assert identity.my_email(str(repo / ".git")) == "Inc@Example.com"
    wt = tmp_path / "elsewhere" / "wt"
    git(repo, "worktree", "add", "-q", "-b", "other", str(wt))
    assert identity.my_email(str(repo / ".git")) == "Inc@Example.com"        # read in the main worktree
    git(repo, "config", "user.email", "local@example.com")                       # the repository's own wins
    assert identity.my_email(str(repo / ".git")) == "local@example.com"


def test_no_email_turns_team_off_with_a_fix(repo):
    with pytest.raises(identity.NoEmail) as caught:
        identity.my_email(str(repo / ".git"))
    assert caught.value.fix == "git config user.email you@example.com"
    assert "fix: git config user.email you@example.com" in str(caught.value)


def test_never_uses_git_var_ident(repo, git, monkeypatch):
    seen = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **k):
            seen.append([str(x) for x in args])
            super().__init__(args, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", Spy)
    with pytest.raises(identity.NoEmail):
        identity.my_email(str(repo / ".git"))
    git(repo, "config", "user.email", "a@example.com")
    assert identity.my_email(str(repo / ".git")) == "a@example.com"
    assert seen and all("var" not in argv and not any("IDENT" in a for a in argv) for argv in seen)


def test_unlisted_handle_is_ref_safe_and_stable(git, tmp_path):
    handle = identity.unlisted_handle(" Carol@Example.COM ")
    assert handle == identity.unlisted_handle("carol@example.com") == identity.unlisted_handle("carol@example.com")
    assert re.fullmatch(r"unlisted-[0-9a-f]{8}", handle) and grammar.person(handle) and not grammar.handle(handle)
    assert identity.unlisted_handle("dave@example.com") != handle
    proc = subprocess.run(["git", "check-ref-format", f"refs/orchestraty/{handle}/7f3a9c2e41b0"], capture_output=True)
    assert proc.returncode == 0
    people = People.parse(PEOPLE)
    assert identity.who_am_i(people, "BOB@example.com") == identity.Me("bob", True)
    assert identity.who_am_i(people, "carol@example.com") == identity.Me(handle, False)
    assert identity.who_am_i(People.empty(), "bob@example.com") == identity.Me(identity.unlisted_handle("bob@example.com"),
                                                                                False)
    assert identity.normalise_email(" A@B.C ") == "a@b.c"


def test_device_id_is_random_not_hostname(state, tmp_path, monkeypatch):
    canary, asked = "canary-host-5f2e", []

    def hostname(*args):
        asked.append(args)
        return canary
    monkeypatch.setattr(socket, "gethostname", hostname)
    monkeypatch.setattr(socket, "getfqdn", hostname)
    first = identity.Device.load_or_create(state, now=1_795_089_960)
    other = identity.Device.load_or_create(State(str(tmp_path / "other-state")), now=1_795_089_960)
    assert asked == []
    assert grammar.device(first.id) and re.fullmatch(r"[0-9a-f]{32}", first.secret) and first.label == ""
    assert first.id != other.id and first.secret != other.secret
    path = Path(state.root) / "team" / "device.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"id": first.id, "label": "", "secret": first.secret,
                                                             "created": 1_795_089_960}
    for f in Path(state.root).rglob("*"):
        if f.is_file():
            assert canary not in f.read_text(encoding="utf-8", errors="replace")
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert identity.Device.load_or_create(state) == first                     # made once
    assert identity.Device.load(State(str(tmp_path / "never-made"))) is None


def test_session_ids_are_opaque_and_stable(state, tmp_path):
    d = identity.Device.load_or_create(state)
    sid = d.session_id("2f1c-local-session")
    assert sid == d.session_id("2f1c-local-session") and grammar.session_id(sid)
    assert "2f1c" not in sid and sid != d.session_id("another-session")
    e = identity.Device.load_or_create(State(str(tmp_path / "other-state")))
    assert e.session_id("2f1c-local-session") != sid
    fp = d.fingerprint("a value withheld")
    assert re.fullmatch(r"[0-9a-f]{8}", fp) and fp == d.fingerprint("a value withheld")
    assert fp != d.fingerprint("another value") and fp != e.fingerprint("a value withheld")


def test_new_device_changes_id_and_secret(state):
    d = identity.Device.load_or_create(state, now=60)
    labelled = identity.Device.set_label(state, "laptop")
    assert labelled.label == "laptop" and labelled.id == d.id and labelled.secret == d.secret
    fresh = identity.Device.new(state, now=120)
    assert fresh.id != d.id and fresh.secret != d.secret and fresh.label == "laptop" and fresh.created == 120
    assert identity.Device.load(state) == fresh
    with pytest.raises(ValueError, match="label"):
        identity.Device.set_label(state, "My Laptop")
    assert identity.Device.set_label(state, "").label == ""
    path = Path(state.root) / "team" / "device.json"
    path.write_text('{"id": "HOSTNAME", "label": "", "secret": "x", "created": 0}', encoding="utf-8")
    with pytest.raises(identity.DeviceError, match="orch team device --new"):
        identity.Device.load(state)
    assert identity.Device.new(state).id != "HOSTNAME"                         # --new mends a damaged file
