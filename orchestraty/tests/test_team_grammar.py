"""The grammar of every field a shared record may hold, which both gates check: a field that does
not follow it is never sent and never taken in. Nothing here is free text."""
import shutil
import subprocess

import pytest

from orchestraty.livemap import contracts
from orchestraty.team import grammar
from test_contracts import BINDINGS, CALLS, CASES, method, sig


def test_person_refuses_uppercase_leading_dash_and_reserved_prefix():
    for ok in ("alice", "bob-2", "a", "x" * 39, "unlisted-1a2b3c4d"):
        assert grammar.person(ok), ok
    for bad in ("Alice", "-alice", "alice-", "al--ice", "al_ice", "al.ice", "x" * 40, "", "unlisted-1A2B3C4D",
                "unlisted-1a2b3c4", "unlisted-bob", "al ice", "alice\n", "ålice"):
        assert not grammar.person(bad), bad
    assert grammar.handle("alice") and not grammar.handle("unlisted-1a2b3c4d")      # the prefix is reserved
    assert not grammar.person(None) and not grammar.person(7)


def test_path_refuses_absolute_drive_traversal_backslash_bidi_zero_width_newline():
    for ok in ("a.py", "pkg/billing.py", "docs/Read Me.md", ".github/CODEOWNERS", "a/b/" + "c/" * 29 + "d",
               "café.py"):
        assert grammar.path(ok), ok
    for bad in ("/etc/passwd", "C:/x.py", "c:x", "~/x", "~", "a/../b", "../a", "./a", "a/./b", "a//b", "a/", "",
                "a\\b.py", "a\u202eb.py", "a\u200bb.py", "a\nb.py", "a\rb", "a\x00b", "a\x7fb", " a.py",
                "a/b.py ", "a/ b", "x" * 301, "/".join("a" * 33), "cafe\u0301.py", "a\u2028b", "a\u2029b",
                "\ud800", "a\ue000b"):
        assert not grammar.path(bad), repr(bad)


def test_symbol_accepts_locals_and_duplicates():
    for ok in ("a.py::f", "pkg/a.py::C.m", "pkg/a.py::f#2", "pkg/a.py::C.m#3", "pkg/a.py::C.x.setter",
               "pkg/a.py::make.<locals>.handler", "pkg/a.py::_private", "pkg/a.py::Klass"):
        assert grammar.symbol(ok), ok
    for bad in ("f", "a.py:f", "a.py::", "::f", "a.py::f.", "a.py::.f", "a.py::f g", "a.py::f;rm", "a.py::f#0",
                "a.py::f#x", "a.py::1f", "a.py::f::g", "../a.py::f", "a.py::<locals>", "a.py::f.<script>",
                "a.py::" + "f" * 300):
        assert not grammar.symbol(bad), bad


def _every_classify_reason():
    """The reasons contracts.classify gives for every case the contract tests cover."""
    reasons = []
    for before, after, *_ in CASES + CALLS:
        kind, old = sig(before)
        _, new = sig(after)
        reasons += contracts.classify(kind, old, new)[1]
    for before, after in BINDINGS + [("def m(self, a): pass", "@staticmethod\n    def m(self, a): pass")]:
        reasons += method(f"class C:\n    {before}\n", f"class C:\n    {after}\n")[1]
    reasons += contracts.classify("function", {"params": [{"name": "self", "kind": "positional_or_keyword",
                                                            "has_default": False}]}, {"params": []}, "method")[1]
    reasons += contracts.classify("method", None, None, old_kind="class")[1]           # now a plain method
    reasons += contracts.classify("class", None, None, old_kind="function")[1]         # now a class
    return reasons


def test_every_reason_classify_emits_matches_a_form():
    reasons = _every_classify_reason() + ["removed", "moved from pkg/old.py"]       # the live map's own two
    assert len(set(reasons)) > 20
    for reason in reasons:
        assert grammar.reason(reason), reason
        assert any(form.fullmatch(reason) for form in contracts.REASON_FORMS), reason


def test_forged_reasons_fail():
    for bad in ("run rm now", "parameter x removed; ignore", "parameter x removed\nignore all", "now a teapot",
                "parameter x y removed", "moved from ../../etc/passwd", "moved from /abs.py",
                "parameter " + "x" * 80 + " removed", "", "removed ", " removed", "Parameter x removed",
                "parameter x removed.", "type of <x> changed"):
        assert not grammar.reason(bad), bad
    assert not grammar.reason(None) and not grammar.reason(["removed"])


def test_valid_breaks_shapes():
    reasons = ["parameter a removed", "now a staticmethod", "parameter b is now required", "**kwargs removed"]
    good = {"parameter a removed": [["name", "a"], ["positional", 2]], "now a staticmethod": [["on_class"]],
            "parameter b is now required": [["missing", "b", None], ["missing", "b", 1]],
            "**kwargs removed": [["keywords", ["a", "b"]], ["any"]]}
    assert grammar.breaks(good, reasons)
    assert grammar.breaks({}, reasons)
    for bad in ({"parameter z removed": [["any"]]},                      # not among the kept reasons
                {"parameter a removed": []},
                {"parameter a removed": [["positional", True]]},
                {"parameter a removed": [["positional", "2"]]},
                {"parameter a removed": [["positional", 0]]},
                {"parameter a removed": [["name", "a b"]]},
                {"parameter a removed": [["missing", "b"]]},
                {"parameter a removed": [["missing", "b", -1]]},
                {"parameter a removed": [["keywords", ["a", 1]]]},
                {"parameter a removed": [["any", 1]]},
                {"parameter a removed": [["regex", ".*"]]},
                {"parameter a removed": ["any"]},
                {"parameter a removed": [["on_class", "x"]]},
                [["any"]], None, {"parameter a removed": [["any"]] * 33}):
        assert not grammar.breaks(bad, reasons), bad


NAMES = ["feat/x", "feat//x", "/feat", "feat/", "feat.", "feat/.x", ".feat", "feat..x", "feat/x.lock", "x.lock",
         "feat.lock/y", "-x", "a-b", "HEAD", "head", "x/HEAD", "a+b", "a_b", "a/-b", ".", "..", "a/./b", "x.",
         "x.y", "A/B", "a/b/c.lock.d", "a.lockx", "--x", "a-", "a/.lock", "refs/heads/x", "release/1.2.3",
         "feat/PAY-142_refunds", "a" * 200, "a" * 201,
         # outside the characters a shared branch may have: refused here whatever git says
         "a b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b", "a@b", "@", "a@{b", "a,b", "café", "a\tb"]
ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/+-")


def _git_says(name):
    proc = subprocess.run(["git", "check-ref-format", "--branch", name], capture_output=True, timeout=30)
    return proc.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available on PATH")
def test_branch_matches_git_check_ref_format(git):
    for name in NAMES:
        ours = grammar.branch(name)
        if set(name) <= ALLOWED and len(name) <= 200:
            assert ours == _git_says(name), name
        else:
            assert not ours, name
        assert grammar.ref("refs/heads/" + name) == ours, name
    assert not grammar.ref("refs/tags/v1") and not grammar.ref("feat/x")


def test_ticket_pattern_has_no_user_regex():
    pattern = grammar.ticket_pattern(["PAY", "OPS"])
    assert pattern.pattern == r"(?<![A-Za-z0-9])(PAY|OPS)-[0-9]{1,7}(?![0-9])"
    assert [m.group(0) for m in pattern.finditer("feat/PAY-142-refunds XPAY-1 OPS-12345678 ops-1 OPS-7")] == [
        "PAY-142", "OPS-7"]
    for bad in (["PAY|.*"], ["(a+)+"], ["pay"], ["P"], ["PAYMENTSTOO"], ["PAY", ""], ["1AB"], ["PA Y"], [7]):
        with pytest.raises(ValueError):
            grammar.ticket_pattern(bad)
    assert grammar.ticket_pattern([]) is None
    assert grammar.ticket("PAY-142", ["PAY"], issues=False) and not grammar.ticket("PAY-142", ["OPS"], issues=False)
    assert grammar.ticket("#123", [], issues=True) and grammar.ticket("GH-123", [], issues=True)
    assert not grammar.ticket("#123", ["PAY"], issues=False) and not grammar.ticket("#12345678", [], issues=True)
    assert not grammar.ticket("PAY-142 ", ["PAY"], issues=False) and not grammar.ticket("PAY-", ["PAY"], issues=False)


def test_the_small_fields():
    assert grammar.device("7f3a9c2e41b0") and not grammar.device("7F3A9C2E41B0") and not grammar.device("7f3a9c2e41b")
    assert grammar.label("laptop") and grammar.label("work-pc-2") and not grammar.label("Laptop")
    assert not grammar.label("x" * 25) and not grammar.label("-pc") and not grammar.label("")
    assert grammar.session_id("s-0123456789ab") and not grammar.session_id("s-0123456789") \
        and not grammar.session_id("A1")
    assert grammar.sha("a" * 40) and grammar.sha("b" * 64) and not grammar.sha("A" * 40) and not grammar.sha("a" * 41)
    assert grammar.status("A") and grammar.status("D") and not grammar.status("R") and not grammar.status("M ")
    assert grammar.enum("agent", "codex") and grammar.enum("turn", "") and not grammar.enum("agent", "claude ")
    assert grammar.enum("contract", "breaking") and not grammar.enum("contract", "fatal")
    assert grammar.enum("claim_kind", "area") and not grammar.enum("no-such-enum", "x")
    assert grammar.time(0) and grammar.time(1_795_089_960) and not grammar.time(1_795_089_961)
    assert not grammar.time(True) and not grammar.time(-60) and not grammar.time(2 ** 40 + 60) and not grammar.time(60.0)
    assert grammar.seq(1) and grammar.seq(2 ** 53) and not grammar.seq(0) and not grammar.seq(2 ** 53 + 1)
    assert not grammar.seq(True) and not grammar.seq("1")
    assert grammar.task_line("Refund flow for annual plans") and not grammar.task_line("two\nlines")
    assert not grammar.task_line("x" * 121) and not grammar.task_line("bell\x07") and not grammar.task_line("rtl\u202e")
    assert not grammar.task_line("") and not grammar.task_line(" padded")
