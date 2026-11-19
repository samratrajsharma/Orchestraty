"""The repository's team policy (.orchestraty/policy.yml): which copy is in force, what keeps
sync off, and the effective policy, never wider than what the person agreed to."""
import ast
import time
from pathlib import Path

import pytest

from orchestraty import remote
from orchestraty.paths import State
from orchestraty.team import policy

BASE = """\
version: 1
sync: on
visibility: private
share:
  claims: false
  task: ticket
tickets:
  keys: [PAY]
branches:
  share: ["feat/*", "fix/*"]
  never: ["feat/secret-*"]
deny:
  paths: ["customers/**"]
  words: ["acme bank", "project-falcon"]
"""


def p(text):
    return policy.parse(text)


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def origin(repo, git, tmp_path):
    path = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(path))
    git(repo, "remote", "add", "origin", str(path))
    return path


def commit_policy(git, top, text, branch=None):
    folder = Path(top) / ".orchestraty"
    folder.mkdir(exist_ok=True)
    (folder / "policy.yml").write_bytes(text.encode("utf-8"))
    if branch:
        git(top, "checkout", "-q", "-b", branch)
    git(top, "add", ".orchestraty/policy.yml")
    git(top, "commit", "-q", "-m", "policy")


def test_no_policy_no_sync(state, repo, origin):
    shadow = remote.Shadow(state, str(repo / ".git"))
    assert policy.in_force(shadow, "origin", url=str(origin)) is None
    eff = policy.effective(None, p(BASE))
    assert not eff.sync_on() and eff.off() == ("there is no team policy on the remote's default branch",
                                               "orch team init")
    assert policy.effective(p(BASE), None).off()[1] == "orch team on"
    assert not p("version: 1\n").sync_on() and p("version: 1\n").off()[0] == "the policy says sync: off"


def test_sync_needs_visibility():
    unsaid = p("version: 1\nsync: on\n")
    assert not unsaid.sync_on() and unsaid.off()[1] == "the policy must say visibility: private or public"
    assert p("version: 1\nsync: true\nvisibility: private\n").sync_on()
    assert not p("version: 1\nsync: maybe\nvisibility: private\n").sync_on()


def test_public_needs_public_sync():
    public = p("version: 1\nsync: on\nvisibility: public\n")
    assert not public.sync_on() and "public_sync: true" in public.off()[1]
    assert p("version: 1\nsync: on\nvisibility: public\npublic_sync: true\n").sync_on()
    private_consent = p(BASE)
    eff = policy.effective(p(BASE.replace("visibility: private", "visibility: public")), private_consent)
    assert eff.visibility == "public" and not eff.sync_on()                    # public if either says so
    public_force = p(BASE.replace("visibility: private", "visibility: public\npublic_sync: true"))
    eff = policy.effective(public_force, private_consent)
    assert not eff.sync_on() and eff.off() == ("the policy now says the repository is public, which you have not "
                                               "agreed to", "orch team on")
    assert policy.widened(public_force, private_consent) == ["visibility: private to public"]
    assert policy.effective(public_force, public_force).sync_on()
    eff = policy.effective(private_consent, public_force)                      # made private: still treated as public
    assert eff.sync_on() and eff.visibility == "public"


def test_a_policy_over_64_kib_or_not_a_plain_file_is_not_read(state, repo, origin, git, tmp_path):
    shadow = remote.Shadow(state, str(repo / ".git"))
    commit_policy(git, repo, BASE + "#" + "x" * (64 * 1024) + "\n")
    git(repo, "push", "-q", "origin", "main")
    big = policy.in_force(shadow, "origin", url=str(origin))
    assert big.off() == ("the team policy cannot be read", "fix .orchestraty/policy.yml: the file is larger than 64 KiB")
    (repo / "target.txt").write_text("elsewhere.yml", encoding="utf-8")
    link = git(repo, "hash-object", "-w", "target.txt")
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{link},.orchestraty/policy.yml")
    git(repo, "commit", "-q", "-m", "a link")
    git(repo, "push", "-q", "origin", "main")
    assert policy.in_force(shadow, "origin", url=str(origin)) is None             # a link is not the policy
    with pytest.raises(remote.RemoteError):
        policy.in_force(shadow, "origin", url=str(tmp_path / "nowhere.git"))


def test_branch_copy_is_not_in_force(state, repo, origin, git, tmp_path):
    commit_policy(git, repo, BASE)
    git(repo, "push", "-q", "origin", "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    commit_policy(git, clone, BASE.replace("claims: false", "claims: true").replace('words: ["acme bank", '
                                                                                   '"project-falcon"]', "words: []"),
                  branch="loosen")
    git(clone, "push", "-q", "origin", "loosen")
    shadow = remote.Shadow(state, str(repo / ".git"))
    force = policy.in_force(shadow, "origin", url=str(origin))
    assert force.claims is False and force.deny_words == ("acme bank", "project-falcon")
    assert policy.in_force(shadow, "origin") == force                        # read again, with no fetch
    assert git(shadow.path, "for-each-ref", "--format=%(refname)") == "refs/orch/origin/HEAD"


def test_local_narrowing_only_removes():
    base = p(BASE.replace("claims: false", "claims: true"))
    eff = policy.effective(base, base, narrow={"task": "confirmed", "claims": True, "unpushed": True})
    assert (eff.task, eff.claims, eff.unpushed) == ("ticket", True, False)
    eff = policy.effective(base, base, narrow={"task": "none", "claims": False}, never_branches=["fix/*"])
    assert (eff.task, eff.claims) == ("none", False)
    assert eff.branch_shared("feat/x") and not eff.branch_shared("fix/y")
    assert policy.effective(base, base, narrow={"task": "everything"}).task == "none"      # a bad value: the least
    junk = policy.effective(base, base, narrow="junk", never_branches="fix/*")
    assert (junk.task, junk.claims) == ("none", False) and not junk.branch_shared("feat/x") and len(junk.notes) == 2
    assert junk.sync_on()                                                        # it shares less, not nothing


def test_widening_waits_for_consent():
    consented = p(BASE)
    wider = p(BASE.replace("claims: false", "claims: true").replace("task: ticket", "task: confirmed")
              .replace('share: ["feat/*", "fix/*"]', 'share: ["feat/*", "fix/*", "*"]').replace("keys: [PAY]", "keys: [PAY, OPS]"))
    eff = policy.effective(wider, consented)
    assert (eff.claims, eff.task, eff.ticket_keys) == (False, "ticket", ("PAY",))
    assert eff.branch_shared("feat/x") and not eff.branch_shared("main")     # the new * waits too
    assert policy.widened(wider, consented) == [
        "share.claims turned on", "share.task: ticket to confirmed", "tickets.keys: OPS added",
        "branches.share: '*' added"]
    assert policy.widened(consented, consented) == []


def test_removed_deny_entry_waits_for_consent():
    consented = p(BASE)
    fewer = p(BASE.replace('words: ["acme bank", "project-falcon"]', 'words: ["acme bank"]')
              .replace('paths: ["customers/**"]', "paths: []").replace('never: ["feat/secret-*"]', "never: []"))
    eff = policy.effective(fewer, consented)
    assert eff.deny_words == ("acme bank", "project-falcon") and eff.path_denied("customers/acme/a.py")
    assert not eff.branch_shared("feat/secret-plan")
    assert policy.widened(fewer, consented) == [
        "branches.never: 'feat/secret-*' removed", "deny.paths: 'customers/**' removed",
        "deny.words: 'project-falcon' removed"]


def test_narrowing_applies_at_once():
    consented = p(BASE.replace("claims: false", "claims: true").replace("task: ticket", "task: confirmed"))
    narrower = p(BASE.replace('words: ["acme bank", "project-falcon"]', 'words: ["acme bank", "project-falcon", "kestrel"]'))
    eff = policy.effective(narrower, consented)
    assert (eff.claims, eff.task) == (False, "ticket") and "kestrel" in eff.deny_words
    assert policy.widened(narrower, consented) == []
    off = policy.effective(p(BASE.replace("sync: on", "sync: off")), consented)
    assert not off.sync_on() and off.off()[0] == "the policy says sync: off"


def test_unknown_share_key_grants_nothing():
    grants = p(BASE.replace("  task: ticket", "  task: ticket\n  telemetry: true"))
    assert grants.sync_on() and grants.ignored == ("share.telemetry",) and not hasattr(grants, "telemetry")
    assert grants.normalised()["share"] == {"claims": False, "task": "ticket", "unpushed": False, "activity": False}


def test_unknown_deny_kind_or_signatures_require_turns_sync_off():
    regex = p(BASE.replace("  words:", '  regex: ["a+"]\n  words:'))
    assert not regex.sync_on()
    assert regex.off() == ("the policy uses deny kind 'regex', which this version does not know",
                           "upgrade Orchestraty")
    signed = p(BASE + "signatures: require\n")
    assert not signed.sync_on() and "sign" in signed.off()[0]
    assert p(BASE + "signatures: verify\n").sync_on() and p(BASE + "signatures: verify\n").notes
    newer = p(BASE.replace("version: 1", "version: 2"))
    assert not newer.sync_on() and newer.off()[1] == "upgrade Orchestraty"
    unknown = p(BASE + "relay: on\n")
    assert not unknown.sync_on() and "relay" in unknown.off()[0]
    broken = p("version: 1\nsync: on\n\tvisibility: private\n")
    assert not broken.sync_on() and broken.off()[1] == "fix .orchestraty/policy.yml line 3: a tab: indent with spaces, " \
                                                       "and write no tab in a value"
    assert not p(BASE.replace("keys: [PAY]", 'keys: ["PAY|.*"]')).sync_on()


def test_branches_share_and_never():
    pol = p(BASE)
    assert pol.branch_shared("feat/refunds") and pol.branch_shared("fix/x/y")
    assert not pol.branch_shared("feat/secret-plan") and not pol.branch_shared("main")
    everything = p("version: 1\nsync: on\nvisibility: private\n")
    assert everything.branch_shared("main") and everything.branch_shared("any/thing")
    assert not p(BASE.replace('"fix/*"', '"fix/*/*"')).sync_on()             # one * at most
    assert everything.path_denied(".env") and everything.path_denied("config/prod/.env.local")
    assert everything.path_denied("certs/server.pem") and not everything.path_denied("src/environment.py")
    assert pol.path_denied("customers/acme/report.csv") and pol.path_denied("customers")
    assert not pol.path_denied("src/customers_view.py")
    assert pol.word_denied("feat/ACME-Bank-migration") and pol.word_denied("project_falcon/x")
    assert pol.word_denied("acme   bank") and not pol.word_denied("acmebank") and not pol.word_denied("bank of acme")
    assert pol.ticket_pattern().findall("feat/PAY-142") == ["PAY"]


def test_policy_has_no_regex_and_deny_words_are_linear():
    source = Path(policy.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"):
            assert node.args and isinstance(node.args[0], ast.Constant), ast.unparse(node)
    # at the limits: the words a plain substring search is slowest on, and globs with many stars
    words = ['"' + "a" * 199 + 'b"'] + [f'"{"a" * 49}b{"a" * 49}{i}"' for i in range(199)]
    globs = ['"' + "*a" * 99 + 'b"'] + [f'"**/x{i}/*a*a*a*a*a*b"' for i in range(199)]
    pol = p(f"version: 1\nsync: on\nvisibility: private\ndeny:\n  words: [{', '.join(words)}]\n"
            f"  paths: [{', '.join(globs)}]\n")
    assert pol.sync_on() and len(pol.deny_words) == 200 and len(pol.deny_paths) == 200
    assert not pol.word_denied("warm") and not pol.path_denied("warm")        # each matcher is made once
    for text in ("a" * 10_000, "a-" * 5_000, ("a" * 49 + "b") * 200):
        started = time.perf_counter()
        assert not pol.word_denied(text)
        took = time.perf_counter() - started
        assert took < 0.05, took
    for path, denied in (("x0/" + "a" * 290, False), ("/".join(["a" * 8] * 32), False), ("a" * 10_000, True),
                         ("/".join(["a"] * 33), True)):
        started = time.perf_counter()
        assert pol.path_denied(path) is denied                                  # too long or deep: not a path
        took = time.perf_counter() - started
        assert took < 0.05, took
    assert pol.word_denied("a" * 5_000 + "A" * 49 + "B" + "a" * 49 + "7")       # any case, after a long run
    assert not p("version: 1\nsync: on\nvisibility: private\ndeny:\n  words: [" + ", ".join(['"w%d"' % i for i in range(201)])
                 + "]\n").sync_on()                                            # at most 200 patterns
    assert not p('version: 1\nsync: on\nvisibility: private\ndeny:\n  words: ["' + "w" * 201 + '"]\n').sync_on()


def test_the_normalised_copy_reads_back_the_same():
    pol = p(BASE)
    again = policy.from_normalised(pol.normalised())
    assert again == pol and again.sync_on()
    assert policy.from_normalised({"version": 1, "sync": True, "visibility": "private", "share": {"claims": "x"}}).off()
