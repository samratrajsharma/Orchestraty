"""A branch's view read from commits alone (``livemap.map.branch_view``): what a cloud branch's
session shows, and what a teammate's branch will. The live map's cloud branches go through it,
so a view made on its own equals the one the coordinator keeps."""
import pytest

from orchestraty import remote
from orchestraty.coordinator import main as cmain
from orchestraty.livemap import map as livemap
from orchestraty.paths import State
from test_cloud import BROKEN, agent, fetch, origin, settle, turn_on  # noqa: F401  (fixtures)


@pytest.fixture
def state(state_root):
    return State(str(state_root))


def symbols(view):
    return {k: s.as_dict() for k, s in view.symbols.items()}


def test_branch_view_matches_cloud_view(state, repo, origin, agent):  # noqa: F811
    turn_on(state, repo)
    agent("claude/drop-currency", {"a.py": BROKEN, "notes.txt": "why\n", "c.py": "from a import charge\n"})
    assert fetch(state, repo)["ok"]
    c = cmain.Coordinator(state)
    try:
        settle(c)
        [sid] = [s for s, row in c.sessions.items() if row["agent"] == "cloud"]
        kept = c.livemap.views[sid]
        assert kept.symbols and not kept.error
        shadow = remote.Shadow(state, str(repo / ".git")).path
        made = livemap.branch_view(shadow, kept.repo_key, sid, kept.base, kept.tip)
        assert made.key() == kept.key()
        assert (made.files, symbols(made), made.not_compared, made.git_dir, made.tip, made.worktree) == (
            kept.files, symbols(kept), kept.not_compared, kept.git_dir, kept.tip, kept.worktree)
        assert made.files == {"a.py": "M", "c.py": "A", "notes.txt": "A"}
        again = livemap.branch_view(shadow, kept.repo_key, sid, kept.base, kept.tip, before=kept,
                                    cache=c.livemap.cache)
        assert again.key() == kept.key()
        assert c.livemap.branches is c.livemap.cloud and sid in c.livemap.branches
    finally:
        c.cloud.wait(120)
        c.close()


def test_a_branch_that_cannot_be_read_is_the_views_error(state, repo, origin):  # noqa: F811
    turn_on(state, repo)
    assert fetch(state, repo)["ok"]
    shadow = remote.Shadow(state, str(repo / ".git")).path
    made = livemap.branch_view(shadow, "key", "cloud-x", "0" * 40, "1" * 40)
    assert made.error and made.files == {} and made.symbols == {}


def test_the_old_names_still_follow_a_branch():
    lm = livemap.LiveMap.__dict__
    assert lm["track_cloud"] is lm["track_branch"] and lm["move_cloud"] is lm["move_branch"]
    assert lm["reread_cloud"] is lm["reread_branch"]
