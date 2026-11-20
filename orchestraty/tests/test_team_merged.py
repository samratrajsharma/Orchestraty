"""When a teammate's branch counts as merged (team/merged.py), the first check that holds winning:
an ancestor of the default branch; else a merge of it into the default branch that changes
nothing (a squash, a rebase, a cherry-pick: git 2.38 or later); else a whole change equal to a
commit on the default branch since the branch's base (a squash merge followed by more edits).
The default branch's patch ids are worked out once per tip, and only for its new commits."""
import subprocess

from orchestraty import remote
from orchestraty.paths import State
from orchestraty.team import merged

CHARGE = "def charge(amount, currency):\n    return amount\n"


def git(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout.decode("utf-8").strip()


def commit(top, files, message):
    for rel, text in files.items():
        path = top / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", message)
    return git(top, "rev-parse", "HEAD")


def setup(tmp_path):
    """A repository with main and a branch feat that changed charge in two commits, main having
    moved on in another file meanwhile. Returns (worktree, shadow, base)."""
    top = tmp_path / "repo"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    git(top, "config", "user.name", "alice")
    git(top, "config", "user.email", "alice@example.com")
    base = commit(top, {"pkg/billing.py": CHARGE, "pkg/other.py": "X = 1\n"}, "start")
    git(top, "checkout", "-q", "-b", "feat")
    commit(top, {"pkg/billing.py": CHARGE.replace("currency)", "currency, tax)")}, "tax")
    commit(top, {"pkg/billing.py": CHARGE.replace("currency)", "currency, tax)") + "# taxed\n"}, "note")
    git(top, "checkout", "-q", "main")
    commit(top, {"pkg/other.py": "X = 2\n"}, "unrelated")
    shadow = remote.Shadow(State(str(tmp_path / "state")), str(top / ".git"))
    shadow.ensure()                                             # reads the repository's objects
    return top, shadow, base


def squash(top, message="feat, squashed"):
    git(top, "merge", "-q", "--squash", "feat")
    git(top, "commit", "-q", "-m", message)
    return git(top, "rev-parse", "HEAD")


def tips(top):
    return git(top, "rev-parse", "feat"), git(top, "rev-parse", "main")


def test_squash_merge_without_deleting_the_branch_is_merged(tmp_path, git):  # noqa: F811
    top, shadow, base = setup(tmp_path)
    squash(top)
    tip, default = tips(top)
    assert subprocess.run(["git", "merge-base", "--is-ancestor", tip, default], cwd=top).returncode == 1
    assert merged.merged(shadow, tip, default, base, merged.PatchIds()) == "merge-tree"


def test_squash_merge_then_a_conflicting_edit_stays_merged(tmp_path, git):  # noqa: F811
    top, shadow, base = setup(tmp_path)
    squash(top)
    commit(top, {"pkg/billing.py": "def charge(amount, currency, tax, fee):\n    return amount\n"}, "fee")
    tip, default = tips(top)
    assert merged.merged(shadow, tip, default, base, merged.PatchIds()) == "patch-id"


def test_merge_commit_is_ancestor(tmp_path, git):  # noqa: F811
    top, shadow, base = setup(tmp_path)
    git(top, "merge", "-q", "--no-ff", "-m", "merge feat", "feat")
    tip, default = tips(top)
    assert merged.merged(shadow, tip, default, base, merged.PatchIds()) == "ancestor"


def test_an_unmerged_branch_is_not_merged(tmp_path, git):  # noqa: F811
    top, shadow, base = setup(tmp_path)
    tip, default = tips(top)
    assert merged.merged(shadow, tip, default, base, merged.PatchIds()) == ""


def test_old_git_skips_merge_tree(tmp_path, git, monkeypatch):  # noqa: F811
    top, shadow, base = setup(tmp_path)
    squash(top)
    tip, default = tips(top)
    monkeypatch.setattr(merged, "merge_tree_works", lambda: False)    # git older than 2.38
    assert merged.merged(shadow, tip, default, base, merged.PatchIds()) == "patch-id"


def test_patch_ids_are_cached_by_default_tip(tmp_path, git):  # noqa: F811
    top, shadow, base = setup(tmp_path)
    squash(top)
    tip, default = tips(top)
    ids = merged.PatchIds()
    calls = []
    real = shadow.git

    def spy(args, **kwargs):
        calls.append(list(args))
        return real(args, **kwargs)

    shadow.git = spy
    first = ids.of(shadow, default)
    logs = [c for c in calls if c[0] == "log"]
    assert len(logs) == 1 and len(first) == 3                   # the default branch's three commits with a change
    assert ids.of(shadow, default) == first and [c for c in calls if c[0] == "log"] == logs   # the same tip: kept
    later = commit(top, {"pkg/other.py": "X = 3\n"}, "later")
    second = ids.of(shadow, later)
    new_logs = [c for c in calls if c[0] == "log"][1:]
    assert len(new_logs) == 1 and f"{default}..{later}" in new_logs[0]  # only what is new is read
    assert set(second) == set(first) | {later}
