"""This device's record chain on the team remote (team/store.py's Outbox): written in the shadow,
pushed with a lease to refs/orchestraty/<person>/<device> once a team fetch has read the remote,
never when nothing changed, compacted, continued from the remote after a loss, and stopped when
another machine writes as this device. The user's own repository is never touched."""
import json
import shutil
from pathlib import Path

from orchestraty import remote
from orchestraty.team import commands, identity, seal, store, teamfile
from team_harness import NS, Machine, run_git, team  # noqa: F401  (fixtures)


def common(machine):
    return str(machine.clone / ".git")


def outbox_of(machine):
    device = identity.Device.load(machine.state)
    return store.Outbox(remote.Shadow(machine.state, common(machine)), "origin", machine.name, device.id,
                        machine.email)


def fetch(machine):
    shadow = remote.Shadow(machine.state, common(machine))
    assert store.fetch_records(shadow, remote.remote_url(common(machine), "origin"), "origin",
                               machine.clock() / NS)


def send(machine, sealed=None):
    """Seal this machine's record (unless given), write it to the outbox and push it, under the
    shadow's lock: what team sync does after the user's own push. Returns (commit or None, Sent)."""
    now = machine.clock() / NS
    sealed = sealed or commands.sealed_now(machine.state, common(machine), now)
    outbox = outbox_of(machine)
    with store.locked(outbox.shadow):
        try:
            commit = outbox.write(sealed, now)
        except store.Clash:
            commit = None
        sent = outbox.push(remote.push_url(common(machine), "origin"))
    return commit, sent


def sealed_with(machine, last_seen, seq):
    """A record of one live session last seen at `last_seen`: content that changes with it."""
    _, _, _, _, eff = commands.effective_for(machine.state, common(machine))
    device = identity.Device.load(machine.state)
    base = run_git(machine.clone, "rev-parse", "origin/main")
    blob = run_git(machine.clone, "rev-parse", "origin/main:.orchestraty/policy.yml")
    facts = seal.SessionFacts(id=device.session_id("A1"), agent="claude", state="live", started=1_795_000_020,
                              last_seen=last_seen, branch="feat/refunds", base=base)
    return seal.seal(seal.Projection("0.2.0", blob, (facts,)), eff, identity.Me(machine.name, True), device, seq,
                     last_seen)


def ref_on_remote(team, machine):  # noqa: F811
    device = identity.Device.load(machine.state).id
    proc = run_git(team.remote, "for-each-ref", "--format=%(objectname)", f"refs/orchestraty/{machine.name}/{device}")
    return proc.strip()


def fingerprint(clone):
    """What a change to a user's repository would show: its refs, objects, config and files (but
    the index, which a status may refresh)."""
    git_dir = clone / ".git"
    files = sorted(str(p.relative_to(git_dir)) for p in git_dir.rglob("*") if p.is_file() and p.name != "index")
    return (run_git(clone, "for-each-ref", "--format=%(refname) %(objectname)"), run_git(clone, "count-objects", "-v"),
            (git_dir / "config").read_bytes(), files)


def started(machine, sid="A1", branch="feat/refunds"):
    machine.team_on()
    machine.start(sid, branch)
    machine.settle()


def test_record_ref_reaches_the_bare_remote_and_the_users_git_is_untouched(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    started(alice)
    before = {m.name: fingerprint(m.clone) for m in (alice, bob)}
    fetch(alice)
    commit, sent = send(alice)
    assert sent.kind == "pushed" and commit
    assert ref_on_remote(team, alice) == commit
    assert run_git(team.remote, "ls-tree", "--name-only", commit) == "record.json"
    record = json.loads(run_git(team.remote, "cat-file", "blob", f"{commit}:record.json"))
    assert record["writer"]["person"] == "alice" and record["sessions"][0]["branch"] == "feat/refunds"
    assert {m.name: fingerprint(m.clone) for m in (alice, bob)} == before         # neither clone changed
    assert "refs/orchestraty" not in run_git(alice.clone, "for-each-ref")


def test_unchanged_snapshot_writes_no_commit(team):  # noqa: F811
    alice = team.alice
    started(alice)
    fetch(alice)
    first, sent = send(alice)
    assert first and sent.kind == "pushed"
    team.clock.advance(600)                                                        # only `written` would differ
    again, sent = send(alice)
    assert again is None and sent.kind == "unchanged"
    assert run_git(team.remote, "rev-list", "--count", ref_on_remote(team, alice)) == "1"


def test_chain_compacts_and_reader_reads_the_tip(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    started(alice)
    fetch(alice)
    outbox = outbox_of(alice)
    for n in range(120):
        commit, sent = send(alice, sealed_with(alice, 1_795_000_080 + 60 * n, outbox.next_seq()))
        assert commit and sent.kind == "pushed", (n, sent)
    tip = ref_on_remote(team, alice)
    assert int(run_git(team.remote, "rev-list", "--count", tip)) <= 100
    root = run_git(team.remote, "rev-list", "--max-parents=0", tip)
    assert "parent " not in run_git(team.remote, "cat-file", "commit", root)
    assert run_git(team.remote, "log", "-1", "--format=%s", root).endswith("(compacted)")
    run_git(team.remote, "fsck", "--no-progress", "--strict")
    bob.team_on()
    fetch(bob)
    data = remote.Shadow(bob.state, common(bob)).git(
        ["cat-file", "blob", f"refs/orch-team/origin/records/alice/{outbox.device}:record.json"], raw=True)[1]
    assert json.loads(data)["seq"] == 120                                          # the tip, read whole


def test_lost_outbox_continues_from_remote_seq(team):  # noqa: F811
    alice = team.alice
    started(alice)
    fetch(alice)
    outbox = outbox_of(alice)
    for n in range(3):
        send(alice, sealed_with(alice, 1_795_000_080 + 60 * n, outbox.next_seq()))
    third = ref_on_remote(team, alice)
    shutil.rmtree(outbox.shadow.path)                                              # the shadow is lost, and with it
    teamfile.update(outbox.shadow, seq=None, last_pushed=None)                     # what it kept
    fetch(alice)
    outbox = outbox_of(alice)
    assert outbox.next_seq() == 4
    commit, sent = send(alice, sealed_with(alice, 1_795_000_900, 4))
    assert sent.kind == "pushed"
    assert run_git(team.remote, "rev-parse", f"{commit}^") == third                 # on top of what the remote had


def test_lease_detects_a_copied_state_folder(team, tmp_path):  # noqa: F811
    alice = team.alice
    started(alice)
    fetch(alice)
    send(alice)
    twin = Machine(tmp_path, "twin", "alice@example.com", team.remote, alice.git, team.clock)
    try:
        (Path(twin.state.root) / "team").mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(alice.state.root) / "team" / "device.json", Path(twin.state.root) / "team" / "device.json")
        twin.name = "alice"                                                        # the same person and device
        started(twin, "T1", "feat/twin")
        fetch(twin)
        _, sent = send(twin)                                                       # the copy goes on from the remote
        assert sent.kind == "pushed"
        _, sent = send(alice, sealed_with(alice, 1_795_000_600, outbox_of(alice).next_seq()))
        assert sent.kind == "stale" and "another machine writes as device" in sent.detail
        _, sent = send(alice, sealed_with(alice, 1_795_000_660, outbox_of(alice).next_seq()))
        assert sent.kind == "held" and "orch team device --new" in sent.detail     # until it gets its own
        assert ref_on_remote(team, alice) == outbox_of(twin).tip()                  # the twin's record stands
    finally:
        twin.close()


def test_record_commit_dates_are_utc_unsigned_and_named_by_handle(team, monkeypatch):  # noqa: F811
    alice = team.alice
    run_git(alice.clone, "config", "--global", "commit.gpgsign", "true")          # never signed, all the same
    run_git(alice.clone, "config", "--global", "user.signingkey", "no-such-key")
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    started(alice)
    fetch(alice)
    commit, sent = send(alice)
    assert sent.kind == "pushed"
    raw = run_git(team.remote, "cat-file", "commit", commit)
    header, _, message = raw.partition("\n\n")
    written = json.loads(run_git(team.remote, "cat-file", "blob", f"{commit}:record.json"))["written"]
    assert f"author alice <alice@example.com> {written} +0000" in header
    assert f"committer alice <alice@example.com> {written} +0000" in header
    assert "gpgsig" not in header and message.strip() == "orchestraty record 1"


def test_no_push_before_first_fetch(team):  # noqa: F811
    alice = team.alice
    started(alice)
    teamfile.update(remote.Shadow(alice.state, common(alice)), fetch=None)        # no team fetch has worked yet
    commit, sent = send(alice)
    assert commit and sent.kind == "held" and "team fetch" in sent.detail
    assert run_git(team.remote, "for-each-ref", "refs/orchestraty") == ""
    fetch(alice)
    again, sent = send(alice)
    assert again is None and sent.kind == "pushed"                                 # the record written before goes
    assert ref_on_remote(team, alice) == commit
