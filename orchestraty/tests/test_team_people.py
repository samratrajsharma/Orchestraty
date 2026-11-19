"""The people file (.orchestraty/people.yml) and CODEOWNERS: who is who on the team, and who owns
which paths. A people file with anything wrong in it is refused whole, with its line."""
import base64

import pytest

from orchestraty.team import codeowners
from orchestraty.team.people import Error, People, email_key, normalise_email

ED25519 = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))).decode()

PEOPLE = f"""\
# Who is who on this team. Read from the remote's default branch.
version: 1
people:
  - handle: alice
    emails: [alice@example.com, 1234+alice@users.noreply.github.com, Alice.Example@Work.example]
    logins: ["@alice"]
    teams: ["@acme/payments", "@acme/leads"]
    keys: ["ssh-ed25519 {ED25519} alice@laptop"]
    name: Alice Example
  - handle: bob
    emails:
      - bob@example.com
      - bob@users.noreply.github.com
    logins: ["@Bob-B"]
    teams: ["@acme/payments"]
"""


def refused(text):
    with pytest.raises(Error) as caught:
        People.parse(text)
    return caught.value


def test_several_emails_one_handle():
    people = People.parse(PEOPLE)
    assert [p.handle for p in people] == ["alice", "bob"]
    for email in ("alice@example.com", "  ALICE@example.com ", "alice.example@work.example"):
        assert people.handle_for(email) == "alice", email
    assert people.handle_for("bob@example.com") == "bob" and people.handle_for("carol@example.com") is None
    assert people.person("alice").name == "Alice Example" and people.person("bob").name == ""
    assert people.emails("bob") == ("bob@example.com", "bob@users.noreply.github.com")
    assert "alice" in people and "carol" not in people and len(people) == 2


def test_noreply_forms_match():
    people = People.parse(PEOPLE)
    assert people.handle_for("alice@users.noreply.github.com") == "alice"          # the file has 1234+alice@
    assert people.handle_for("98765+bob@users.noreply.github.com") == "bob"        # the file has bob@
    assert people.handle_for("1234+alice@users.noreply.github.com") == "alice"
    assert normalise_email("  Alice@Example.COM\n") == "alice@example.com"
    assert email_key("1234+alice@users.noreply.github.com") == email_key("alice@users.noreply.github.com")
    assert email_key("1+carol@example.com") != email_key("carol@example.com")     # nothing else is folded
    assert email_key("x+alice@users.noreply.github.com") != email_key("alice@users.noreply.github.com")


def test_duplicate_email_or_handle_refuses_the_file():
    error = refused("version: 1\npeople:\n  - handle: alice\n    emails: [a@example.com]\n"
                    "  - handle: alice\n    emails: [b@example.com]\n")
    assert error.line == 5 and "alice" in error.message and "twice" in error.message
    error = refused("version: 1\npeople:\n  - handle: alice\n    emails: [a@example.com]\n"
                    "  - handle: bob\n    emails: [A@Example.com]\n")
    assert error.line == 6 and "a@example.com" in error.message
    error = refused("version: 1\npeople:\n  - handle: alice\n    emails: [7+al@users.noreply.github.com]\n"
                    "  - handle: bob\n    emails: [al@users.noreply.github.com]\n")
    assert error.line == 6
    error = refused("version: 1\npeople:\n  - handle: alice\n")
    assert error.line == 3 and "email" in error.message


def test_unknown_key_refuses_the_file():
    error = refused("version: 1\nadmins: [alice]\npeople: []\n")
    assert error.line == 2 and "admins" in error.message
    error = refused("version: 1\npeople:\n  - handle: alice\n    emails: [a@example.com]\n    role: lead\n")
    assert error.line == 5 and "role" in error.message
    assert refused("version: 2\npeople: []\n").line == 1
    assert "version" in refused("people: []\n").message
    assert refused("version: 1\npeople: alice\n").line == 2
    assert refused("version: 1\npeople:\n  - alice\n").line == 3


def test_keys_parsed_comment_dropped():
    people = People.parse(PEOPLE)
    assert people.person("alice").keys == (("ssh-ed25519", ED25519),)              # "alice@laptop" is gone
    for bad in ("ssh-dss " + ED25519, "ssh-ed25519", "ssh-ed25519 not*base64", "ssh-ed25519 " + ED25519[:-1] + "!",
                "ssh-ed25519 QUJD"):
        error = refused(f"version: 1\npeople:\n  - handle: alice\n    emails: [a@example.com]\n    keys: [\"{bad}\"]\n")
        assert error.line == 5 and "key" in error.message, bad
    for kind in ("sk-ssh-ed25519@openssh.com", "ecdsa-sha2-nistp256", "ssh-rsa"):
        text = f"version: 1\npeople:\n  - handle: a\n    emails: [a@example.com]\n    keys: [\"{kind} {ED25519}\"]\n"
        assert People.parse(text).person("a").keys == ((kind, ED25519),)


def test_every_field_and_limit_is_checked():
    head = "version: 1\npeople:\n  - handle: alice\n    emails: [a@example.com]\n"
    for line, word in (("    logins: [alice]", "login"), ('    logins: ["@al ice"]', "login"),
                       ('    teams: ["@Acme/payments"]', "team"), ('    teams: ["@acme"]', "team"),
                       ('    name: "' + "x" * 81 + '"', "name"), ('    name: "Al\u202eice"', "name"),
                       ("    logins: [7]", "login")):
        error = refused(head + line + "\n")
        assert error.line == 5 and word in error.message, line
    assert "handle" in refused("version: 1\npeople:\n  - handle: Alice\n    emails: [a@example.com]\n").message
    assert "handle" in refused("version: 1\npeople:\n  - handle: unlisted-1a2b3c4d\n    emails: [a@example.com]\n").message
    assert "email" in refused("version: 1\npeople:\n  - handle: alice\n    emails: [not-an-email]\n").message
    assert "email" in refused("version: 1\npeople:\n  - handle: alice\n    emails: []\n").message
    many = "version: 1\npeople:\n" + "".join(f"  - handle: p{i}\n    emails: [p{i}@example.com]\n" for i in range(2001))
    assert "2000" in refused(many).message
    assert "256 KiB" in refused("version: 1\npeople: []\n# " + "x" * (256 * 1024) + "\n").message
    assert People.parse("version: 1\npeople: []\n").handle_for("a@example.com") is None
    assert len(People.empty()) == 0


RULES = """\
# Owners of this repository's code.
*                   @acme/everyone
/docs/              @acme/docs
/docs/api.md        @alice
/vendor/
*.js                @Bob-B
apps/               ops@example.com
/build/logs/        @acme/payments
docs/*              @acme/writers
**/logs             @alice @Bob-B
/apps/github
\\#notes.md          @alice   # an inline comment
"""


def test_last_match_wins():
    rules = codeowners.Rules.parse("*  @everyone\n/docs/ @docs\n/docs/api.md @alice\n/vendor/\n")
    assert rules.owners("docs/api.md") == ["@alice"]
    assert rules.owners("docs/guide/intro.md") == ["@docs"]
    assert rules.owners("src/billing.py") == ["@everyone"]
    assert rules.owners("vendor/lib/x.py") == []                                    # no owners: left with none
    rules = codeowners.Rules.parse(RULES)
    assert rules.owners("docs/api.md") == ["@acme/writers"]                         # docs/* comes after /docs/api.md
    assert rules.owners("docs/guide/intro.md") == ["@acme/docs"]
    assert rules.matched("docs/guide/intro.md").line == 3


def test_anchored_and_directory_patterns():
    rules = codeowners.Rules.parse(RULES)
    assert rules.owners("build/logs/today.txt") == ["@alice", "@Bob-B"]             # **/logs is later
    assert rules.owners("tools/build/logs/x") == ["@alice", "@Bob-B"]
    assert codeowners.Rules.parse("/build/logs/ @x\n").owners("tools/build/logs/x") == []
    assert codeowners.Rules.parse("/build/logs/ @x\n").owners("build/logs/a/b") == ["@x"]
    one = codeowners.Rules.parse("apps/ @x\n")
    assert one.owners("web/apps/main.py") == ["@x"] and one.owners("apps") == [] and one.owners("apps/a") == ["@x"]
    star = codeowners.Rules.parse("docs/* @x\n")
    assert star.owners("docs/a.md") == ["@x"] and star.owners("docs/b/c.md") == []
    js = codeowners.Rules.parse("*.js @x\n")
    assert js.owners("a/b/c.js") == ["@x"] and js.owners("a.js/readme.md") == []
    gh = codeowners.Rules.parse("/apps/github @x\n")
    assert gh.owners("apps/github/x.py") == ["@x"] and gh.owners("apps/githubx") == [] and gh.owners("x/apps/github") == []
    assert codeowners.Rules.parse("\\#notes.md @x\n").owners("a/#notes.md") == ["@x"]
    deep = codeowners.Rules.parse("src/**/test_*.py @x\n")
    assert deep.owners("src/test_a.py") == ["@x"] and deep.owners("src/a/b/test_c.py") == ["@x"]
    assert deep.owners("lib/src/test_a.py") == [] and deep.owners("src/a/b/c.py") == []
    assert codeowners.Rules.parse("file?.txt @x\n").owners("file1.txt") == ["@x"]
    assert codeowners.Rules.parse("[ab].txt @x\n").owners("a.txt") == []           # no character ranges


def test_team_and_login_tokens_resolve():
    people = People.parse(PEOPLE)
    assert people.resolve("@acme/payments") == ["alice", "bob"]
    assert people.resolve("@ACME/Payments") == ["alice", "bob"]
    assert people.resolve("@acme/leads") == ["alice"] and people.resolve("@alice") == ["alice"]
    assert people.resolve("@bob-b") == ["bob"] and people.resolve("bob@EXAMPLE.com") == ["bob"]
    assert people.resolve("@acme/nobody") == [] and people.resolve("@carol") == [] and people.resolve("alice") == []
    rules = codeowners.Rules.parse(RULES)
    assert sorted({h for t in rules.owners("build/logs/x") for h in people.resolve(t)}) == ["alice", "bob"]


def test_bad_codeowners_lines_ignored():
    text = ("* @everyone\n"
            "!vendor/ @nobody\n"                 # negation: not read
            "docs//api.md @nobody\n"             # an empty segment
            "src/ alice\n"                       # an owner that is no token
            "[Section] @nobody\n"                # GitLab sections are skipped
            "^[Optional][2] @nobody\n"
            "src/ @ok @not a token\n"
            "lib/\x07 @nobody\n")
    rules = codeowners.Rules.parse(text)
    for path in ("vendor/a", "docs/api.md", "src/x.py", "lib/a"):
        assert rules.owners(path) == ["@everyone"], path
    lines = "".join(f"/f{i} @x{i}\n" for i in range(codeowners.MAX_LINES + 10))
    long = codeowners.Rules.parse(lines)
    assert long.owners(f"f{codeowners.MAX_LINES - 1}") == [f"@x{codeowners.MAX_LINES - 1}"]
    assert long.owners(f"f{codeowners.MAX_LINES}") == []
    found = {".github/CODEOWNERS": None, "CODEOWNERS": b"* @root\n", "docs/CODEOWNERS": b"* @docs\n"}
    where, rules = codeowners.find(found.get)
    assert where == "CODEOWNERS" and rules.owners("a") == ["@root"]
    assert codeowners.find(lambda p: None) == (None, codeowners.Rules([]))
