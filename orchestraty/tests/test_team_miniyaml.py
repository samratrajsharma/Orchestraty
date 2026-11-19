"""The small YAML reader for .orchestraty/policy.yml and people.yml: it reads what those files
need, and refuses everything else with the line it is on."""
import pytest

from orchestraty.team import miniyaml

POLICY = """\
# What Orchestraty shares between teammates in this repository. Read from the remote's default branch.
version: 1
sync: on                  # on | off (true/false accepted). Default off.
visibility: private       # private | public. Required when sync is on.
public_sync: false        # must be true for sync to run when visibility is public
share:
  claims: true            # live claims: kind, target, until (always soft for other people)
  task: ticket            # none | ticket | confirmed  (confirmed allows tickets too)
  unpushed: false         # committed but unpushed work: files and functions of base..HEAD
  activity: false         # whether a session's agent is working or idle
tickets:
  keys: [PAY, OPS]        # Jira-style KEY-123; each key [A-Z][A-Z0-9]{1,9}
  issues: true            # #123 and GH-123
branches:
  share: ["*"]            # refspec-style globs, one '*' at most
  never: ["security/*", "private/*"]
deny:
  paths: ["secrets/**", "customers/**"]     # globs over repository paths; a built-in list is always added
  words: ["acme bank", "project-falcon"]    # literal, case-insensitive; - _ . / and space count as one
people:
  unlisted: share         # share | refuse
signatures: off           # off only in this version (D7); 'require' turns sync off, 'verify' gives a note
"""

PEOPLE = """\
# Who is who on this team. Read from the remote's default branch.
version: 1
people:
  - handle: alice
    emails: [alice@example.com, 1234+alice@users.noreply.github.com]
    logins: ["@alice"]              # the CODEOWNERS tokens that mean this person
    teams: ["@acme/payments"]       # the CODEOWNERS teams this person belongs to
    keys: ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample alice@laptop"]   # optional
    name: Alice Example             # optional; shown to people only, never to an agent
  - handle: bob
    emails: [bob@example.com]
"""


def test_parses_the_policy_and_people_examples():
    policy = miniyaml.parse(POLICY)
    assert policy == {
        "version": 1, "sync": "on", "visibility": "private", "public_sync": False,
        "share": {"claims": True, "task": "ticket", "unpushed": False, "activity": False},
        "tickets": {"keys": ["PAY", "OPS"], "issues": True},
        "branches": {"share": ["*"], "never": ["security/*", "private/*"]},
        "deny": {"paths": ["secrets/**", "customers/**"], "words": ["acme bank", "project-falcon"]},
        "people": {"unlisted": "share"}, "signatures": "off"}
    assert isinstance(policy, miniyaml.Map) and isinstance(policy["tickets"]["keys"], miniyaml.Seq)
    assert policy.lines["share"] == 6 and policy["share"].lines["task"] == 8 and policy["deny"].lines["words"] == 19
    people = miniyaml.parse(PEOPLE)
    assert people == {"version": 1, "people": [
        {"handle": "alice", "emails": ["alice@example.com", "1234+alice@users.noreply.github.com"],
         "logins": ["@alice"], "teams": ["@acme/payments"],
         "keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample alice@laptop"], "name": "Alice Example"},
        {"handle": "bob", "emails": ["bob@example.com"]}]}
    assert people["people"].lines == [4, 10] and people["people"][1].lines["emails"] == 11


def test_crlf_a_bom_comments_and_an_indentless_list():
    text = "\ufeff# a comment\r\nversion: 1\r\nname: O'Brien # it's plain\r\nlist:\r\n- a#b\r\n- 'c # d'\r\n"
    assert miniyaml.parse(text) == {"version": 1, "name": "O'Brien", "list": ["a#b", "c # d"]}
    assert miniyaml.parse(b"a: [1, -2, x, true, 'false', \"\"]\n") == {"a": [1, -2, "x", True, "false", ""]}
    assert miniyaml.parse("a: []\nb: ''\n") == {"a": [], "b": ""}


@pytest.mark.parametrize("third_line, word", [
    ("\tvisibility: private", "tab"),
    ("c: &shared true", "anchor"),
    ("c: *shared", "alias"),
    ("c: !!str 3", "tag"),
    ("---", "document"),
    ("c: |", "multi-line"),
    ("a: 3", "twice"),
    ("c: \"not closed", "quote"),
    ("c: null", "null"),
    ("c: ~", "null"),
    ("c:", "null"),
    ("c: >-", "multi-line"),
    ("c: {x: 1}", "{"),
    ("- item", "list item"),
], ids=["tab", "anchor", "alias", "tag", "document", "literal-block", "duplicate-key", "unterminated-quote",
        "null", "tilde", "empty-value", "folded-block", "flow-mapping", "stray-list-item"])
def test_each_refused_construct_names_its_line(third_line, word):
    with pytest.raises(miniyaml.Error) as caught:
        miniyaml.parse(f"a: 1\nb: 2\n{third_line}\nd: 4\n")
    assert caught.value.line == 3 and word in caught.value.message
    assert str(caught.value).startswith("line 3: ")


def test_a_plain_value_that_goes_on_is_refused():
    with pytest.raises(miniyaml.Error) as caught:
        miniyaml.parse("a: 1\nb: two\n  words\n")
    assert caught.value.line == 3 and "multi-line" in caught.value.message
    with pytest.raises(miniyaml.Error) as caught:
        miniyaml.parse("a:\n  - x\n  - - y\n")
    assert caught.value.line == 3 and "list of lists" in caught.value.message
    with pytest.raises(miniyaml.Error) as caught:
        miniyaml.parse("a: [x,\n  y]\n")
    assert caught.value.line == 1 and "close" in caught.value.message


def test_quoted_escapes_only_quote_and_backslash():
    assert miniyaml.parse('a: "say \\"hi\\" \\\\ ok"\n') == {"a": 'say "hi" \\ ok'}
    assert miniyaml.parse("a: 'it''s a \\n literal'\n") == {"a": "it's a \\n literal"}
    assert miniyaml.parse('"a key": "#not a comment"\n') == {"a key": "#not a comment"}
    for escape in ("\\n", "\\t", "\\u0041", "\\x41", "\\0"):
        with pytest.raises(miniyaml.Error) as caught:
            miniyaml.parse(f'a: 1\nb: "x{escape}y"\n')
        assert caught.value.line == 2 and "escape" in caught.value.message, escape
    with pytest.raises(miniyaml.Error, match="after a quoted value"):
        miniyaml.parse('a: "x" y\n')


def test_size_and_depth_limits():
    filler = "a: " + "x" * (miniyaml.MAX_BYTES - 4) + "\n"
    assert len(filler.encode("utf-8")) == miniyaml.MAX_BYTES
    assert miniyaml.parse(filler)["a"].startswith("xx")
    with pytest.raises(miniyaml.Error, match="64 KiB"):
        miniyaml.parse(filler + "b: 1\n")
    with pytest.raises(miniyaml.Error, match="1 KiB"):
        miniyaml.parse("a: " + "x" * 2000 + "\n", max_bytes=1024)
    assert miniyaml.parse("a:\n  b:\n    c:\n      - x\n") == {"a": {"b": {"c": ["x"]}}}
    assert miniyaml.parse("a:\n  b:\n    c: [x, y]\n") == {"a": {"b": {"c": ["x", "y"]}}}
    with pytest.raises(miniyaml.Error) as caught:
        miniyaml.parse("a:\n  b:\n    c:\n      d:\n        - x\n")
    assert caught.value.line == 5 and "4 levels" in caught.value.message
    with pytest.raises(miniyaml.Error) as caught:
        miniyaml.parse("a:\n  b:\n    c:\n      d: [x]\n")
    assert caught.value.line == 4 and "4 levels" in caught.value.message
    with pytest.raises(miniyaml.Error, match="nothing"):
        miniyaml.parse("# only a comment\n\n")
    with pytest.raises(miniyaml.Error, match="UTF-8"):
        miniyaml.parse(b"a: \xff\n")
