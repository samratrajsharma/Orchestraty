"""The records branch, refs/heads/orchestraty/records: where this device's record goes when the host
refuses custom refs (it hides them, or a hook says no), switched to on the first refusal and kept.
Each writer is one file, records/<person>/<device>.json; every commit says [skip ci]; two writers
racing both land; the branch compacts; and readers read both stores, the higher seq winning."""
import json

from orchestraty import remote
from orchestraty.coordinator.record import Record
from orchestraty.coordinator.team import session_id
from orchestraty.team import store, teamfile
from team_harness import BILLING, Machine, publish_raw, run_git, team, refusing_remote  # noqa: F401  (fixtures)

TAX = BILLING.replace("currency)", "currency, tax)")
BRANCH = "refs/heads/orchestraty/records"


def common(machine):
    return str(machine.clone / ".git")


def shadow_of(machine):
    return remote.Shadow(machine.state, common(machine))


def writers(machine):
    rec = Record(machine.state.record, readonly=True)
    try:
        return {(w["person"], w["device"]): w for w in rec.team_writers(rec.repo_id(common(machine)))}
    finally:
        rec.close()


def sent(machine):
    rec = Record(machine.state.record, readonly=True)
    try:
        return [r for repo in rec.repos() for r in rec.team_sent(repo["id"])]
    finally:
        rec.close()


def on(remote_path, tmp_path, team):  # noqa: F811
    """alice and bob, as ann and ben, on a remote of the team's repository that refuses custom refs."""
    ann = Machine(tmp_path, "ann", "alice@example.com", remote_path, team.alice.git, team.clock)
    ben = Machine(tmp_path, "ben", "bob@example.com", remote_path, team.alice.git, team.clock)
    return ann, ben


def falls_back(remote_path, tmp_path, team):  # noqa: F811
    ann, ben = on(remote_path, tmp_path, team)
    try:
        ann.team_on()
        ann.push_work("A1", "feat/x", "pkg/billing.py", TAX)
        device = ann.device()["id"]
        assert run_git(remote_path, "ls-tree", "-r", "--name-only", BRANCH) == f"records/alice/{device}.json"
        assert run_git(remote_path, "for-each-ref", "refs/orchestraty") == ""
        assert teamfile.read(shadow_of(ann))["store"] == "branch"                  # kept from now on
        (row,) = sent(ann)
        assert (row["result"], row["store"]) == ("pushed", "branch")
        ben.team_on()
        ben.settle()
        ben.settle()
        found = writers(ben)[("alice", device)]
        assert (found["verdict"], found["store"]) == ("admitted", "branch")
        rec = Record(ben.state.record, readonly=True)
        try:
            live = rec.session(session_id(common(ben), "origin", "feat/x"))
        finally:
            rec.close()
        assert live is not None and live["state"] == "live"                        # read like any record
    finally:
        ann.close()
        ben.close()


def test_a_host_hiding_custom_refs_falls_back_to_the_branch(team, refusing_remote, tmp_path):  # noqa: F811
    falls_back(refusing_remote.hidden, tmp_path, team)


def test_a_pre_receive_refusal_falls_back_too(team, refusing_remote, tmp_path):  # noqa: F811
    falls_back(refusing_remote.hook, tmp_path, team)


def test_two_writers_race_on_the_branch_and_both_land(team, refusing_remote, tmp_path, monkeypatch):  # noqa: F811
    hook = refusing_remote.hook
    ann, ben = on(hook, tmp_path, team)
    try:
        for m in (ann, ben):
            m.team_on()
            teamfile.update(shadow_of(m), store="branch")
        ann.push_work("A1", "feat/x", "pkg/billing.py", TAX)
        alices = run_git(hook, "cat-file", "blob", f"{BRANCH}:records/alice/{ann.device()['id']}.json")
        real = store.BranchStore._fetch_branch
        late = {"first": True}

        def stale_once(self, url, ssh_configured):
            if late["first"] and self.person == "bob":     # bob builds on what he last saw, while the branch moves
                late["first"] = False
                tip = run_git(hook, "rev-parse", BRANCH)
                moved = run_git(hook, "commit-tree", tip + "^{tree}", "-p", tip, "-m", "another writer [skip ci]",
                                identity=("alice", "alice@example.com"))
                run_git(hook, "update-ref", BRANCH, moved)
                return True
            return real(self, url, ssh_configured)

        monkeypatch.setattr(store.BranchStore, "_fetch_branch", stale_once)
        ben.push_work("B1", "feat/z", "pkg/zed.py", "Z = 1\n")
        assert not late["first"]
        files = run_git(hook, "ls-tree", "-r", "--name-only", BRANCH).splitlines()
        assert files == sorted([f"records/alice/{ann.device()['id']}.json", f"records/bob/{ben.device()['id']}.json"])
        assert run_git(hook, "cat-file", "blob", f"{BRANCH}:records/alice/{ann.device()['id']}.json") == alices
        (row,) = sent(ben)
        assert (row["result"], row["store"]) == ("pushed", "branch")
    finally:
        ann.close()
        ben.close()


def test_store_commit_messages_carry_skip_ci(team, refusing_remote, tmp_path):  # noqa: F811
    ann, ben = on(refusing_remote.hidden, tmp_path, team)
    try:
        ann.team_on()
        ann.push_work("A1", "feat/x", "pkg/billing.py", TAX)
        ann.push_work("A2", "feat/y", "pkg/other.py", "Y = 1\n")
        device = ann.device()["id"]
        messages = run_git(refusing_remote.hidden, "log", "--format=%s", BRANCH).splitlines()
        assert messages == [f"orchestraty records: alice/{device} 2 [skip ci]",
                            f"orchestraty records: alice/{device} 1 [skip ci]"]
        tree = run_git(refusing_remote.hidden, "ls-tree", "-r", "--name-only", BRANCH)
        assert tree == f"records/alice/{device}.json"                              # no project file to build
    finally:
        ann.close()
        ben.close()


def test_readers_merge_ref_and_branch_writers(team):  # noqa: F811
    alice, bob = team.alice, team.bob
    alice.team_on()
    bob.team_on()
    alice.push_work("A1", "feat/x", "pkg/billing.py", TAX)                       # seq 1, under its ref
    teamfile.update(shadow_of(alice), store="branch")                             # then the records branch
    alice.push_work("A2", "feat/y", "pkg/other.py", "Y = 1\n")                     # seq 2 goes there
    bob.push_work("B1", "feat/z", "pkg/zed.py", "Z = 1\n")                         # bob, under his ref
    team.clock.advance(150)
    bob.settle()
    carol = Machine(team.alice.folder.parent, "carol", "carol@example.com", team.remote, alice.git, team.clock)
    try:
        carol.team_on()
        carol.settle()
        seen = writers(carol)
        a = seen[("alice", alice.device()["id"])]
        assert (a["store"], a["seq"], a["verdict"]) == ("branch", 2, "admitted")   # the higher seq wins
        b = seen[("bob", bob.device()["id"])]
        assert (b["store"], b["verdict"]) == ("ref", "admitted")
    finally:
        carol.close()


def test_branch_store_compacts(team, refusing_remote, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(store, "BRANCH_COMPACT_COMMITS", 2)
    ann, ben = on(refusing_remote.hook, tmp_path, team)
    try:
        ann.team_on()
        old = {"format": 1, "writer": {"person": "carol", "device": "0123456789ab", "label": ""}, "seq": 1,
               "written": 1_700_000_040, "tool": "0.2.0", "policy": "8700e80478cb061e9f565612ee55378cfbddc448",
               "sessions": []}                                                     # far older than 4 x 14 days
        blob = run_git(refusing_remote.hook, "hash-object", "-w", "--stdin",
                       data=(json.dumps(old, sort_keys=True, separators=(",", ":")) + "\n").encode())
        tree = run_git(refusing_remote.hook, "mktree", data=f"100644 blob {blob}\t0123456789ab.json\n".encode())
        tree = run_git(refusing_remote.hook, "mktree", data=f"040000 tree {tree}\tcarol\n".encode())
        tree = run_git(refusing_remote.hook, "mktree", data=f"040000 tree {tree}\trecords\n".encode())
        first = run_git(refusing_remote.hook, "commit-tree", tree, "-m", "orchestraty records: carol/0123456789ab 1 "
                        "[skip ci]", identity=("carol", "carol@example.com"))
        run_git(refusing_remote.hook, "update-ref", BRANCH, first)
        for n in range(3):                                                         # the third finds more than 2
            ann.push_work(f"A{n}", f"feat/n{n}", f"pkg/n{n}.py", f"N = {n}\n")
        count = int(run_git(refusing_remote.hook, "rev-list", "--count", BRANCH))
        assert count <= 2
        root = run_git(refusing_remote.hook, "rev-list", "--max-parents=0", BRANCH)
        assert root != first                                                      # an orphan commit started again
        files = run_git(refusing_remote.hook, "ls-tree", "-r", "--name-only", BRANCH).splitlines()
        assert files == [f"records/alice/{ann.device()['id']}.json"]                # carol's old record went
        assert publish_raw
    finally:
        ann.close()
        ben.close()
