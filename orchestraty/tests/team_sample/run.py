"""The runner of the labelled sample: every scenario on one shared remote and one pair of
machines (tests/team_harness.py), and one row per finding bob's machine makes about alice's
branch. Alice's side is plain git and her record, written as her machine writes it, so a
scenario costs bob's coordinator only: one pass that starts his session (and reads the end of
the scenario before it), his edits, one team fetch after her push. Between scenarios his
session ends, his worktree goes and her branch is deleted.

    rows = run(scenarios, tmp)

A row: {scenario, key, rule, file, strength, evidence, latent, delivered_kind, counted}. A
finding counts for the metrics when its rule carries a note (O2 to O7) and, if latent, once its
note was delivered before bob's first edit of the file.
"""
import contextlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import __version__, config, declarations, remote
from orchestraty.capture.hook import take_slot
from orchestraty.coordinator.record import Record
from orchestraty.coordinator.team import session_id
from orchestraty.delivery import slots
from team_harness import NS, Clock, Machine, publish_raw, run_git
from team_sample import catalog

COUNTED = ("O2", "O3", "O4", "O5", "O6", "O7")
LABELS = ("real", "harmless", "false")
FETCH = 150                      # seconds on the shared clock: one team fetch interval
SECOND_DEVICE = "a1b2c3d4e5f6"   # alice's other machine (control two_devices)


@contextlib.contextmanager
def isolated(tmp):
    """A module-wide run, with its own state, home, agent config and git config folders, so
    nothing reaches the real ones (the per-test isolation is not set up yet when it runs)."""
    tmp = Path(tmp)
    empty = tmp / "gitconfig"
    empty.write_text("", encoding="utf-8")
    (tmp / "cwd").mkdir()
    with pytest.MonkeyPatch.context() as mp:
        for name, sub in (("ORCH_HOME", "orch-home"), ("HOME", "home"), ("USERPROFILE", "home"),
                          ("CLAUDE_CONFIG_DIR", "claude-config"), ("CODEX_HOME", "codex-home")):
            mp.setenv(name, str(tmp / sub))
        mp.setenv("GIT_CONFIG_GLOBAL", str(empty))
        mp.setenv("GIT_CONFIG_SYSTEM", str(empty))
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        mp.setenv("ORCH_NO_COORDINATOR", "1")
        mp.chdir(tmp / "cwd")
        yield tmp


def seed(tmp):
    """The shop on a new bare remote: tests/fixtures/team_shop, committed on main."""
    src = tmp / "seed"
    shutil.copytree(catalog.FIXTURE, src)
    run_git(src, "init", "-q")
    run_git(src, "add", "-A")
    run_git(src, "commit", "-q", "-m", "the shop", identity=("owner", "owner@example.com"))
    bare = tmp / "remote.git"
    run_git(tmp, "clone", "-q", "--bare", str(src), str(bare))
    return bare


class Sample:
    def __init__(self, tmp):
        self.tmp, self.clock = Path(tmp), Clock()
        self.remote = seed(self.tmp)
        self.alice = Machine(self.tmp, "alice", "alice@example.com", self.remote, None, self.clock)
        self.bob = Machine(self.tmp, "bob", "bob@example.com", self.remote, None, self.clock)
        self.alice.team_on()                             # her device; her coordinator never runs here
        self.bob.team_on()
        self.common = str(self.bob.clone / ".git")
        self.device = self.alice.device()["id"]
        self.main = run_git(self.remote, "rev-parse", "main")
        self.policy = run_git(self.remote, "rev-parse", "main:.orchestraty/policy.yml")
        self.seq, self.count = {}, 0

    def close(self):
        self.alice.close()
        self.bob.close()

    # alice: plain git, and her record as her machine writes it
    def alice_push(self, branch, files, start="origin/main"):
        clone = self.alice.clone
        run_git(clone, "fetch", "-q", "origin")
        run_git(clone, "checkout", "-q", "-B", branch, start)
        for rel, body in files.items():
            path = clone / rel
            if body is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body.encode("utf-8"))
        run_git(clone, "add", "-A")
        run_git(clone, "commit", "-q", "-m", f"work on {branch}")
        run_git(clone, "push", "-q", "origin", branch)
        return run_git(clone, "rev-parse", "HEAD"), run_git(clone, "merge-base", "HEAD", "origin/main")

    def publish(self, entries, device=None):
        device = device or self.device
        self.seq[device] = self.seq.get(device, 0) + 1
        now = self.clock() // NS // 60 * 60                # the record keeps minutes
        record = {"format": 1, "policy": self.policy, "seq": self.seq[device], "tool": __version__,
                  "writer": {"device": device, "label": "", "person": "alice"}, "written": now,
                  "sessions": [dict(e, last_seen=now, started=now) for e in entries]}
        publish_raw(self.remote, "alice", device,
                    (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
                    "alice@example.com")

    def entry(self, branch, tip, base, claims=()):
        now = self.clock() // NS // 60 * 60
        self.count += 1
        return {"agent": "claude", "base": base, "branch": branch, "ended": 0, "id": "s-%012x" % self.count,
                "pushed": {"ref": f"refs/heads/{branch}", "tip": tip}, "state": "live", "task": {}, "turn": "",
                "work": None, "claims": [{"kind": k, "target": t, "file": f, "hard": False, "since": now,
                                          "until": now + 3600} for k, t, f in claims]}

    # bob: a session on his machine, his coordinator, and what it found
    def bob_start(self, sid, branch, start="origin/main", agent="claude"):
        """A session in a new worktree; its first pass also reads the end of the scenario before."""
        bob = self.bob
        run_git(bob.clone, "fetch", "-q", "origin")
        top = bob.folder / f"wt-{sid}"
        run_git(bob.clone, "worktree", "add", "-q", "-b", branch, str(top), start)
        bob.worktrees[sid], bob.agents[sid] = top, agent
        spool_event(bob.state.root, sid, "SessionStart", top, agent=agent, ts=self.clock())
        self.fetch()
        return top

    def fetch(self):
        self.clock.advance(FETCH)
        self.bob.settle()

    def worktree(self, sid):
        rec = Record(self.bob.state.record, readonly=True)
        try:
            return rec.session(sid)["worktree"]
        finally:
            rec.close()

    def rows(self, scenario, sid, branch):
        team = session_id(self.common, "origin", branch)
        rec = Record(self.bob.state.record, readonly=True)
        try:
            found = [f for f in rec.findings() if team in f["sessions"] and sid in f["sessions"]]
            notes = rec.notes(session_id=sid)
        finally:
            rec.close()
        out = []
        for f in found:
            mine = [n for n in notes if n["finding_key"] == f["key"]]
            delivered = sorted(n["kind"] for n in mine if n["state"] == "delivered")
            ready = sorted(n["kind"] for n in mine if n["state"] in ("written", "delivered"))
            latent = bool(f["detail"].get("latent"))
            out.append({"scenario": scenario.id, "key": f["key"], "rule": f["rule"], "file": f["file"],
                        "strength": f["strength"], "evidence": f["detail"].get("evidence", "commits"),
                        "latent": latent, "delivered_kind": (delivered or ready or [None])[0],
                        "counted": f["rule"] in COUNTED and (not latent or bool(delivered))})
        return out

    def play(self, s):
        """One scenario: bob's session and edits, alice's branch and record, one fetch."""
        n = self.count + 1
        sid, branch = f"B{n}", (f"claude/s{n}" if s.control == "cloud" else f"feat/s{n}")
        bob, start, gone = self.bob, "origin/main", [branch]
        if s.control == "cloud":
            remote.save_settings(bob.state, self.common, on=True, remote="origin", patterns=["claude/*"])
        if s.control == "checkout":                       # alice first; bob goes on from her tip
            tip, base = self.alice_push(branch, s.change)
            self.publish([self.entry(branch, tip, base)])
            start = f"origin/{branch}"
        top = self.bob_start(sid, f"bob/s{n}", start, s.agent)
        if s.control == "stacked":                        # bob's pushed commit, which alice builds on
            for rel, body in s.bob_commit.items():
                bob.edit(sid, rel, body)
            run_git(top, "add", "-A")
            run_git(top, "commit", "-q", "-m", "bob's work")
            run_git(top, "push", "-q", "origin", f"bob/s{n}")
            gone.append(f"bob/s{n}")
        for rel, body in s.edits.items():
            bob.edit(sid, rel, body)
        if s.declare:
            declarations.append(bob.state, sid, list(s.declare), [], "", now=self.clock() / NS)
        if s.control != "checkout":
            tip, base = self.alice_push(branch, s.change, f"origin/bob/s{n}" if s.control == "stacked" else "origin/main")
            entry = self.entry(branch, tip, base, s.claims)
            self.publish([entry])
            if s.control == "two_devices":
                self.publish([dict(entry, id="s-%012x" % (self.count + 10_000))], device=SECOND_DEVICE)
        if s.control == "merged":
            run_git(self.remote, "update-ref", "refs/heads/main", tip)
        if s.control == "deleted":
            run_git(self.remote, "update-ref", "-d", f"refs/heads/{branch}")
            gone.remove(branch)
        if s.control == "cloud":
            assert remote.fetch_now(bob.state, self.common, remote.settings(config.load(bob.state), self.common),
                                    300)["ok"]
        self.fetch()
        if s.take:                                        # bob's hook, before his first edit of that file
            take_slot(slots.edit_slot(bob.state, sid, os.path.join(self.worktree(sid), *s.take.split("/"))))
            bob.settle()
        rows = self.rows(s, sid, branch)
        # the end of the scenario: read by bob's next session's first pass
        spool_event(bob.state.root, sid, "SessionEnd", top, agent=s.agent, ts=self.clock())
        for b in gone:
            run_git(self.remote, "update-ref", "-d", f"refs/heads/{b}")
        if s.control == "merged":
            run_git(self.remote, "update-ref", "refs/heads/main", self.main)
        if s.control == "two_devices":
            run_git(self.remote, "update-ref", "-d", f"refs/orchestraty/alice/{SECOND_DEVICE}")
        if s.control == "cloud":
            remote.save_settings(bob.state, self.common, on=False, remote="origin", patterns=["claude/*"])
        self.publish([])
        run_git(bob.clone, "worktree", "remove", "--force", str(top))
        return rows


def run(scenarios, tmp):
    """Every scenario in turn, on one sample; the rows of all."""
    sample = Sample(tmp)
    try:
        rows = []
        for s in scenarios:
            rows += sample.play(s)
        return rows
    finally:
        sample.close()


# ------------------------------------------------------------------------------ labels and metrics
def labelled(rows, oracle):
    """The counted rows, each with its label from the oracle, or "unlabelled"."""
    out = []
    for r in rows:
        if not r["counted"]:
            continue
        expected = oracle.get((r["scenario"], r["evidence"])) or []
        match = [label for rule, file, label in expected if (rule, file) == (r["rule"], r["file"])]
        out.append(dict(r, label=match[0] if match else "unlabelled"))
    return out


def check(rows, oracle):
    """What fails the sample, each naming its scenario: a finding the oracle does not hold, and a
    strong finding it labels false."""
    problems = []
    for r in labelled(rows, oracle):
        if r["label"] == "unlabelled":
            problems.append(f"{r['scenario']}: {r['rule']} {r['file']} ({r['strength']}) is not in the oracle")
        elif r["label"] == "false" and r["strength"] == "strong":
            problems.append(f"{r['scenario']}: {r['rule']} {r['file']} is strong and labelled false")
    return problems


def misses(rows, oracle, scenarios):
    """The real findings the oracle expects that the run did not count: (scenario, rule, file)."""
    got = {(r["scenario"], r["rule"], r["file"]) for r in rows if r["counted"]}
    return [(s.id, rule, file) for s in scenarios for rule, file, label in oracle.get((s.id, "commits"), ())
            if label == "real" and (s.id, rule, file) not in got]


def metrics(rows, oracle, scenarios):
    """Strict precision real/all (the gate), lenient (real + harmless)/all, false alarms, and
    recall: real findings counted over real findings expected."""
    done = labelled(rows, oracle)
    by = Counter(r["label"] for r in done)
    total = sum(by[x] for x in LABELS)
    expected = sum(1 for s in scenarios for _, _, label in oracle.get((s.id, "commits"), ()) if label == "real")
    found = len({(r["scenario"], r["rule"], r["file"]) for r in done if r["label"] == "real"})
    return {"scenarios": len(scenarios), "findings": len(done), "real": by["real"], "harmless": by["harmless"],
            "false": by["false"], "unlabelled": by["unlabelled"],
            "precision_strict": by["real"] / total if total else 1.0,
            "precision_lenient": (by["real"] + by["harmless"]) / total if total else 1.0,
            "false_alarms": by["false"], "recall": found / expected if expected else 1.0,
            "misses": misses(rows, oracle, scenarios)}


def table(rows, oracle, scenarios):
    """The figures, per rule and evidence, and the misses."""
    m = metrics(rows, oracle, scenarios)
    lines = [f"team sample: {m['scenarios']} scenarios, {m['findings']} findings counted; strict precision "
             f"{m['precision_strict']:.3f}, lenient {m['precision_lenient']:.3f}, false alarms {m['false_alarms']}, "
             f"recall {m['recall']:.3f}",
             f"{'rule':<6}{'evidence':<10}{'real':>6}{'harmless':>10}{'false':>7}{'unlabelled':>12}"]
    groups = Counter((r["rule"], r["evidence"], r["label"]) for r in labelled(rows, oracle))
    for rule, evidence in sorted({(r, e) for r, e, _ in groups}):
        lines.append(f"{rule:<6}{evidence:<10}" + "".join(f"{groups[(rule, evidence, x)]:>{w}}" for x, w in
                                                          (("real", 6), ("harmless", 10), ("false", 7),
                                                           ("unlabelled", 12))))
    lines += [f"missed: {sid} {rule} {file}" for sid, rule, file in m["misses"]]
    return "\n".join(lines)
