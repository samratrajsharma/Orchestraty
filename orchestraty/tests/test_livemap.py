"""The live map: which files and functions each session changed, and in its coordinator."""
import os
import time

import pytest

from helpers import spool_event
from orchestraty import config
from orchestraty.coordinator import main as cmain
from orchestraty.livemap import changes as git_changes
from orchestraty.livemap.map import LiveMap
from orchestraty.paths import State


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def lm(state):
    return LiveMap(state)


def head(git, path):
    return git(path, "rev-parse", "HEAD")


def session(sid, worktree, base, agent="claude", cwd=None):
    return {"id": sid, "agent": agent, "worktree": str(worktree), "cwd": str(cwd or worktree), "base_commit": base}


def write(path, text):
    path.write_text(text, encoding="utf-8")


def follow(lm, sid, worktree, base, **kw):
    lm.track(session(sid, worktree, base, **kw), "repo-key")
    return lm


# --------------------------------------------------------------------------------------- #
# git: files changed since a commit, worktrees, blobs
# --------------------------------------------------------------------------------------- #
def test_changed_files_covers_committed_staged_unstaged_untracked_and_deleted(repo, git):
    base = head(git, repo)
    write(repo / "a.py", "def charge(amount):\n    return amount\n")
    git(repo, "commit", "-q", "-am", "committed since the base")
    write(repo / "c.py", "x = 1\n")
    git(repo, "add", "c.py")                                     # staged
    write(repo / "d.py", "y = 2\n")                              # untracked
    os.remove(repo / "b.py")                                     # deleted, unstaged
    (repo / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    write(repo / "ignored.py", "z = 3\n")
    assert git_changes.changed_files(str(repo), base) == {"a.py": "M", "c.py": "A", "d.py": "A", "b.py": "D",
                                                           ".gitignore": "A"}


def test_a_rename_is_a_deletion_and_an_addition(repo, git):
    base = head(git, repo)
    git(repo, "mv", "b.py", "e.py")
    assert git_changes.changed_files(str(repo), base) == {"b.py": "D", "e.py": "A"}


def test_one_content_at_two_paths_is_two_parses(lm):
    from orchestraty.livemap.index import blob_id
    data = b"def f():\n    return 1\n"
    one, two = lm.cache.get("x/one.py", data), lm.cache.get("y/two.py", data)
    assert [s.id for s in one.symbols] == ["x/one.py::f"] and [s.id for s in two.symbols] == ["y/two.py::f"]
    assert lm.cache.get("x/one.py", data, blob_id(data)) is one


def test_changed_files_on_a_branch_with_no_commit(tmp_path, git):
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    write(empty / "new.py", "x = 1\n")
    assert git_changes.changed_files(str(empty), "") == {"new.py": "A"}


def test_worktrees_and_blob_ids(repo, git, tmp_path):
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(tmp_path / "wt-b"))
    trees = git_changes.worktrees(str(tmp_path / "wt-b"))
    assert [os.path.normcase(t["path"]) for t in trees] == [os.path.normcase(str(repo)),
                                                             os.path.normcase(str(tmp_path / "wt-b"))]
    assert [t["branch"] for t in trees] == ["main", "feat-b"] and trees[0]["head"] == head(git, repo)
    blobs = git_changes.blob_ids(str(repo), head(git, repo), ["a.py", "nope.py"])
    assert list(blobs) == ["a.py"] and len(blobs["a.py"]) == 40


# --------------------------------------------------------------------------------------- #
# What a session changed
# --------------------------------------------------------------------------------------- #
def test_a_signature_change_is_breaking(repo, git, lm):
    follow(lm, "a", repo, head(git, repo))
    write(repo / "a.py", "def charge(amount):\n    return amount\n")
    assert lm.refresh("a")
    v = lm.views["a"]
    assert v.files == {"a.py": "M"}
    c = v.symbols["a.py::charge"]
    assert (c.kind, c.change, c.contract, c.reasons) == ("modified", "signature", "breaking",
                                                          ["parameter currency removed"])
    assert c.lines == [1, 2]
    assert not lm.refresh("a")                                    # nothing new since


def test_a_change_that_keeps_every_function_and_line_is_still_a_change(repo, git, lm):
    follow(lm, "a", repo, head(git, repo))
    write(repo / "b.py", "from a import charge\n\n\ndef pay():\n    return charge(2, currency='EUR')\n")
    assert lm.refresh("a")
    write(repo / "b.py", "from a import charge\n\n\ndef pay():\n    return charge(2, 'EUR')\n")   # same lines
    assert lm.refresh("a")                                        # the call changed its form: the rules must run
    assert not lm.refresh("a")                                    # nothing new since


BROKEN = "def charge(amount):\n    return amount\n"
HALF_DONE = "def charge(amount, currency):\n    return (\n"                 # an edit caught half done


def test_a_file_that_does_not_parse_is_not_compared(repo, git, lm):
    follow(lm, "a", repo, head(git, repo))
    write(repo / "a.py", HALF_DONE)
    assert lm.refresh("a")
    v = lm.views["a"]
    assert v.files == {"a.py": "M"} and v.symbols == {}                 # not "every function removed"
    assert v.not_compared == {"a.py": "does not parse"}
    write(repo / "a.py", "def charge(amount, currency):\n    return amount\n\nfoo = (\n")   # charge whole, the file not
    lm.refresh("a")
    assert lm.views["a"].symbols == {}
    write(repo / "a.py", BROKEN)
    assert lm.refresh("a")
    assert lm.views["a"].not_compared == {} and lm.views["a"].symbols["a.py::charge"].contract == "breaking"
    write(repo / "a.py", BROKEN + "\nfoo = (\n")                        # half done again: kept as last read whole
    lm.refresh("a")
    v = lm.views["a"]
    assert v.not_compared == {"a.py": "does not parse"}
    assert v.symbols["a.py::charge"].reasons == ["parameter currency removed"]
    assert not lm.refresh("a")
    assert lm.indexes.wait(120)
    assert "b.py::pay" in set(lm.graph("a").callers("a.py::charge"))    # the graph keeps the file's functions


def test_a_file_grown_too_large_is_not_compared(repo, git, lm, monkeypatch):
    from orchestraty.livemap import map as livemap_map
    monkeypatch.setattr(livemap_map, "MAX_BLOB_BYTES", 200)              # the base a.py is 46 bytes
    follow(lm, "a", repo, head(git, repo))
    write(repo / "a.py", "def charge(amount, currency):\n    return amount\n" + "# more\n" * 40)
    lm.refresh("a")
    v = lm.views["a"]
    assert v.symbols == {} and v.not_compared == {"a.py": "too large"}
    os.remove(repo / "a.py")
    lm.refresh("a")
    v = lm.views["a"]
    assert v.symbols["a.py::charge"].kind == "removed" and v.not_compared == {}   # a deletion is still one


def test_a_file_grown_too_large_and_then_deleted_leaves_the_graph(repo, git, lm, monkeypatch):
    from orchestraty.livemap import map as livemap_map
    monkeypatch.setattr(livemap_map, "MAX_BLOB_BYTES", 200)
    follow(lm, "a", repo, head(git, repo))
    write(repo / "a.py", "def charge(amount, currency):\n    return amount\n" + "# more\n" * 40)
    lm.refresh("a")
    assert lm.indexes.wait(120)
    assert "a.py::charge" in lm.graph("a").nodes                       # not compared: it stands as it was
    os.remove(repo / "a.py")
    lm.refresh("a")
    assert "a.py::charge" not in lm.graph("a").nodes                   # gone now: not the cached graph


def test_a_cloud_branch_file_too_large_is_not_compared(repo, git, lm, monkeypatch):
    from orchestraty.livemap import map as livemap_map
    base = head(git, repo)
    write(repo / "a.py", "def charge(amount, currency):\n    return amount\n" + "# more\n" * 40)
    git(repo, "commit", "-q", "-am", "grown")
    tip = head(git, repo)
    monkeypatch.setattr(livemap_map, "MAX_BLOB_BYTES", 200)
    lm.track_cloud(session("c", "", base), "repo-key", str(repo), tip)
    lm.refresh("c")
    v = lm.views["c"]
    assert v.symbols == {} and v.not_compared == {"a.py": "too large"}


def test_body_changes_additions_removals_and_moves(repo, git, lm):
    follow(lm, "a", repo, head(git, repo))
    write(repo / "a.py", "def charge(amount, currency):\n    return amount * 2\n\n\ndef refund(x):\n    return x\n")
    lm.refresh("a")
    s = lm.views["a"].symbols
    assert (s["a.py::charge"].change, s["a.py::charge"].contract) == ("body", "none")
    assert (s["a.py::refund"].kind, s["a.py::refund"].contract) == ("added", "compatible")
    os.remove(repo / "b.py")
    write(repo / "a.py", "")
    write(repo / "c.py", "def charge(amount, currency):\n    return amount\n")     # moved, body as before
    lm.refresh("a")
    s = lm.views["a"].symbols
    assert (s["b.py::pay"].kind, s["b.py::pay"].contract) == ("removed", "breaking")
    assert "a.py::charge" not in s                                # a move is not a removal
    moved = s["c.py::charge"]
    assert (moved.kind, moved.moved_from, moved.contract) == ("moved", "a.py::charge", "review")
    assert lm.views["a"].files == {"a.py": "M", "b.py": "D", "c.py": "A"}


def test_files_changed_before_the_session_are_not_its_own_until_they_change(repo, git, lm):
    write(repo / "a.py", "def charge(amount):\n    return amount\n")          # someone's earlier edit
    follow(lm, "a", repo, head(git, repo))
    lm.refresh("a")
    assert lm.views["a"].files == {} and lm.views["a"].symbols == {}
    write(repo / "a.py", "def charge(amount, x=1):\n    return amount\n")     # now the session edits it
    lm.refresh("a")
    assert lm.views["a"].files == {"a.py": "M"}


def test_an_edit_event_makes_a_file_the_sessions_own(repo, git, lm):
    write(repo / "a.py", "def charge(amount):\n    return amount\n")
    follow(lm, "a", repo, head(git, repo))
    lm.note_event(lm.sessions["a"], "PostToolUse", {"tool_name": "Edit", "file_path": str(repo / "a.py")})
    lm.refresh_pending()
    assert lm.views["a"].files == {"a.py": "M"}


def test_in_a_shared_folder_only_named_files_are_attributed(repo, git, lm):
    base = head(git, repo)
    follow(lm, "a", repo, base)
    follow(lm, "b", repo, base)
    write(repo / "a.py", "def charge(amount):\n    return amount\n")
    write(repo / "b.py", "def pay():\n    return 2\n")
    write(repo / "c.py", "x = 1\n")                              # changed by a shell command, say
    lm.note_event(lm.sessions["a"], "PostToolUse", {"tool_name": "Edit", "file_path": str(repo / "a.py")})
    lm.note_event(lm.sessions["b"], "PostToolUse", {"tool_name": "Write", "file_path": "b.py"})
    lm.refresh_pending()
    assert lm.views["a"].files == {"a.py": "M"} and lm.views["b"].files == {"b.py": "M"}
    assert lm.views["a"].unattributed == ["c.py"] == lm.views["b"].unattributed
    assert "a.py::charge" in lm.views["a"].symbols and "a.py::charge" not in lm.views["b"].symbols


def test_files_the_record_gave_a_session_stay_its_own_when_it_is_followed_again(repo, git, lm):
    base = head(git, repo)
    write(repo / "a.py", "def charge(amount):\n    return amount\n")        # its change, from before a restart
    write(repo / "c.py", "x = 1\n")                                         # someone's earlier change
    lm.track(session("a", repo, base), "repo-key", recorded=({"a.py": "M"}, []))
    lm.refresh("a")
    v = lm.views["a"]
    assert v.files == {"a.py": "M"} and v.symbols["a.py::charge"].contract == "breaking"


def test_in_a_shared_folder_what_the_record_held_stays_as_it_was(repo, git, lm):
    base = head(git, repo)
    write(repo / "a.py", "def charge(amount):\n    return amount\n")
    write(repo / "c.py", "x = 1\n")
    lm.track(session("a", repo, base), "repo-key", recorded=({"a.py": "M"}, ["c.py"]))
    lm.track(session("b", repo, base), "repo-key", recorded=({}, ["c.py"]))
    lm.refresh_pending()
    assert lm.views["a"].files == {"a.py": "M"} and lm.views["b"].files == {}
    assert lm.views["a"].unattributed == ["c.py"] == lm.views["b"].unattributed


def test_paths_from_events(repo, git, lm, tmp_path):
    follow(lm, "a", repo, head(git, repo), cwd=repo / "sub")
    s = lm.sessions["a"]
    assert lm.rel(s, str(repo / "pkg" / "m.py")) == "pkg/m.py"
    assert lm.rel(s, "m.py") == "sub/m.py"                       # relative to the session's folder
    assert lm.rel(s, str(tmp_path / "elsewhere.py")) is None
    assert lm.rel(s, str(repo)) is None and lm.rel(s, "") is None and lm.rel(s, None) is None


def test_which_tool_calls_trigger_a_refresh(repo, git, lm):
    follow(lm, "a", repo, head(git, repo))
    lm.pending.clear()
    s = lm.sessions["a"]
    lm.note_event(s, "PostToolUse", {"tool_name": "Read", "file_path": None})
    lm.note_event(s, "PreToolUse", {"tool_name": "Edit", "file_path": str(repo / "a.py")})
    assert lm.pending == set()
    lm.note_event(s, "PostToolUse", {"tool_name": "Bash"})
    assert lm.pending == {"a"}


def test_two_worktrees_share_one_parse(repo, git, lm, tmp_path):
    base = head(git, repo)
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(tmp_path / "wt-b"))
    follow(lm, "a", repo, base)
    follow(lm, "b", tmp_path / "wt-b", base)
    for top in (repo, tmp_path / "wt-b"):
        write(top / "a.py", "def charge(amount):\n    return amount\n")
    lm.refresh("a")
    misses = lm.cache.misses
    lm.refresh("b")
    assert lm.cache.misses == misses                              # same content: parsed once
    assert lm.views["b"].symbols["a.py::charge"].contract == "breaking"


def test_the_session_graph_finds_callers_in_its_own_copy(repo, git, lm):
    follow(lm, "a", repo, head(git, repo))
    write(repo / "c.py", "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n")
    lm.refresh("a")
    assert lm.graph("a") is None or True                          # may still be building
    assert lm.indexes.wait(120)
    g = lm.graph("a")
    callers = set(g.callers("a.py::charge"))
    assert {"b.py::pay", "c.py::use"} <= callers
    assert lm.index_state("a") == "ready"
    assert lm.graph("a") is g                                     # cached until the files change


# --------------------------------------------------------------------------------------- #
# In the coordinator
# --------------------------------------------------------------------------------------- #
@pytest.fixture
def coordinator(state):
    made = []

    def make(**kw):
        c = cmain.Coordinator(state, **kw)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def test_the_coordinator_records_what_a_session_changed(state, repo, coordinator):
    config.watch(state, str(repo / ".git"))
    spool_event(state.root, "s1", "SessionStart", repo)
    c = coordinator()
    c.step()
    write(repo / "a.py", "def charge(amount):\n    return amount\n")
    spool_event(state.root, "s1", "PostToolUse", repo, tool_name="Edit", file_path=str(repo / "a.py"))
    c.step()
    assert "s1" in c.changed
    files, unattributed, symbols = c.record.changes("s1")
    assert files == {"a.py": "M"} and unattributed == []
    [charge] = symbols
    assert charge["symbol"] == "a.py::charge" and charge["contract"] == "breaking"
    assert charge["reasons"] == ["parameter currency removed"]


def test_a_restarted_coordinator_keeps_what_each_session_changed(state, repo, coordinator):
    config.watch(state, str(repo / ".git"))
    spool_event(state.root, "s1", "SessionStart", repo)
    c = coordinator()
    c.step()
    write(repo / "a.py", "def charge(amount):\n    return amount\n")
    spool_event(state.root, "s1", "PostToolUse", repo, tool_name="Edit", file_path=str(repo / "a.py"))
    c.step()
    c.close()
    again = coordinator()
    again.step()
    v = again.livemap.views["s1"]
    assert v.files == {"a.py": "M"} and v.symbols["a.py::charge"].contract == "breaking"


def test_a_session_back_after_it_timed_out_keeps_its_changes(state, repo, coordinator):
    config.watch(state, str(repo / ".git"))
    now = [time.time_ns()]
    c = coordinator(now_ns=lambda: now[0])
    spool_event(state.root, "s1", "SessionStart", repo, ts=now[0])
    c.step()
    write(repo / "a.py", "def charge(amount):\n    return amount\n")
    spool_event(state.root, "s1", "PostToolUse", repo, tool_name="Edit", file_path=str(repo / "a.py"), ts=now[0])
    c.step()
    now[0] += (config.DEFAULTS["session_timeout"] + 60) * cmain.NS
    c.step()
    assert c.record.session("s1")["state"] == "ended"
    spool_event(state.root, "s1", "UserPromptSubmit", repo, ts=now[0])
    c.step()
    c._last_scan = None
    c.step()
    assert c.record.session("s1")["state"] == "live"
    assert c.livemap.views["s1"].files == {"a.py": "M"}


def test_plain_git_worktrees_become_level0_sessions(state, repo, git, tmp_path, coordinator):
    config.watch(state, str(repo / ".git"))
    base = head(git, repo)
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(tmp_path / "wt-b"))
    git(repo, "worktree", "add", "-q", "-b", "feat-c", str(tmp_path / "wt-c"))
    write(tmp_path / "wt-b" / "a.py", "def charge(amount):\n    return amount\n")   # an agent with no hooks
    write(tmp_path / "wt-b" / "d.py", "def extra():\n    return 1\n")
    git(tmp_path / "wt-b", "add", "d.py")
    git(tmp_path / "wt-b", "commit", "-q", "-m", "committed on its branch")      # seen too: base is the fork point
    spool_event(state.root, "s1", "SessionStart", repo)
    c = coordinator()
    c.step()
    level0 = [s for s in c.record.sessions(states=("live",)) if s["agent"] == "git"]
    assert len(level0) == 1 and os.path.normcase(level0[0]["worktree"]) == os.path.normcase(str(tmp_path / "wt-b"))
    assert level0[0]["base_commit"] == base and level0[0]["branch"] == "feat-b"
    files, _, symbols = c.record.changes(level0[0]["id"])
    assert files == {"a.py": "M", "d.py": "A"}
    assert {s["symbol"]: s["contract"] for s in symbols} == {"a.py::charge": "breaking", "d.py::extra": "compatible"}
    assert c.live_count(hooked_only=True) == 1 and c.live_count() == 2
    # A hooked session starts there: the plain-git one ends.
    spool_event(state.root, "s2", "SessionStart", tmp_path / "wt-b")
    c._last_scan = None
    c.step()
    assert c.record.session(level0[0]["id"])["note"] == "a hooked session works here"
    # A plain-git worktree that becomes clean ends too.
    write(tmp_path / "wt-c" / "b.py", "x = 1\n")
    c._last_scan = None
    c.step()
    [wc] = [s for s in c.record.sessions(states=("live",)) if s["agent"] == "git"]
    os.remove(tmp_path / "wt-c" / "b.py")
    git(tmp_path / "wt-c", "checkout", "-q", "--", "b.py")
    c._last_scan = None
    c.step()
    assert c.record.session(wc["id"])["note"] == "clean"


def test_the_main_worktree_of_a_plain_git_repository_is_based_on_its_head(state, repo, git, tmp_path, coordinator):
    config.watch(state, str(repo / ".git"))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(tmp_path / "wt-b"))
    spool_event(state.root, "s1", "SessionStart", tmp_path / "wt-b")
    write(repo / "b.py", "def pay():\n    return 0\n")               # a person editing the main checkout
    c = coordinator()
    c.step()
    [main] = [s for s in c.record.sessions(states=("live",)) if s["agent"] == "git"]
    assert os.path.normcase(main["worktree"]) == os.path.normcase(str(repo))
    assert main["base_commit"] == head(git, repo)
    assert c.record.changes(main["id"])[0] == {"b.py": "M"}
