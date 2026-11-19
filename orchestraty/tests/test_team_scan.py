"""The secret scanner: named token formats, an entropy backstop and long hex runs, over the short
fields of a record shared with teammates. Every sample here is built at run time, in two parts,
so no file holds a token."""
import ast
import hashlib
import random
import re
import string
import subprocess
import sysconfig
import time
from pathlib import Path

import pytest

from orchestraty.team import scan

ROOT = Path(__file__).resolve().parents[2]
ALNUM = string.digits + string.ascii_uppercase + string.ascii_lowercase
UPPER = string.ascii_uppercase + string.digits
B64 = ALNUM + "+/"
URLSAFE = ALNUM + "_-"


def body(n, alphabet=ALNUM, step=11, start=5):
    """n characters that look random: every character of the alphabet in turn, stepping."""
    return "".join(alphabet[(start + i * step) % len(alphabet)] for i in range(n))


SAMPLES = {
    "aws-access-key": lambda: "AK" + "IA" + body(16, UPPER),
    "github-token": lambda: "gh" + "p_" + body(36),
    "github-pat": lambda: "github" + "_pat_" + body(22) + "_" + body(59, start=9),
    "gitlab-token": lambda: "gl" + "pat-" + body(20, URLSAFE),
    "slack-token": lambda: "xo" + "xb-" + body(12, string.digits) + "-" + body(24),
    "stripe-key": lambda: "sk" + "_live_" + body(24),
    "google-api-key": lambda: "AI" + "za" + body(35, URLSAFE),
    "openai-key": lambda: "sk" + "-proj-" + body(48, URLSAFE),
    "anthropic-key": lambda: "sk" + "-ant-" + "api03-" + body(40, URLSAFE),
    "npm-token": lambda: "np" + "m_" + body(36),
    "pypi-token": lambda: "pypi" + "-AgEIcHlwaS5vcmc" + body(50, URLSAFE),
    "sendgrid-key": lambda: "S" + "G." + body(22, URLSAFE) + "." + body(43, URLSAFE, start=7),
    "jwt": lambda: "ey" + "J" + body(20, URLSAFE) + ".ey" + "J" + body(40, URLSAFE) + "." + body(43, URLSAFE),
    "private-key": lambda: "-----BEGIN RSA PRI" + "VATE KEY-----",
    "url-credentials": lambda: "https://" + "deploy:" + body(16) + "@git.example.com/acme/app.git",
    "assignment": lambda: "pass" + "word=" + body(16),
    "azure-key": lambda: "Account" + "Key=" + body(86, B64) + "==",
}


def test_each_named_rule_fires_in_branch_path_and_symbol():
    assert set(SAMPLES) == set(scan.NAMED_RULES)
    for rule, make in SAMPLES.items():
        sample = make()
        for text in (f"feat/{sample}", f"src/{sample}/models.py", f"PaymentClient.{sample}"):
            hits = [h for h in scan.scan(text) if h.rule == rule]
            assert hits, (rule, text[:30], [h.rule for h in scan.scan(text)])
            at = text.index(sample)
            assert all(h.start < at + len(sample) and h.start + h.length > at for h in hits), rule   # on the sample


def test_no_false_positives_on_this_repository_and_the_stdlib():
    names = set(_git("ls-files").splitlines())
    names |= set(_git("for-each-ref", "--format=%(refname:short)").splitlines())
    names |= {"claude/task-123e4567-e89b-12d3-a456-426614174000", "codex/task-7f9a2c41e8b34d0f9a1b2c3d4e5f6a7b",
              "copilot/fix-login-redirect-loop", "claude/new-session-3qm0dz", "p2/2.1-building-blocks",
              "bookmark_test_restores_the_scroll_position", "disk-0123456789abcdef0123456789abcdef"}
    for path in _git("ls-files", "*.py").splitlines():
        names |= _identifiers((ROOT / path).read_text(encoding="utf-8"))
    ours = len(names)
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    for path in stdlib.rglob("*.py"):
        rel = path.relative_to(stdlib)
        if rel.parts[0] in ("test", "tests", "idlelib", "site-packages", "dist-packages") or "tests" in rel.parts:
            continue
        names.add(rel.as_posix())
        names |= set(re.findall(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_]\w*)",
                                path.read_text(encoding="utf-8", errors="replace"), re.M))
    assert ours > 2000 and len(names) - ours > 5000, (ours, len(names))
    hits = {name: [h.rule for h in scan.scan(name)] for name in names if scan.scan(name)}
    assert hits == {}


def test_entropy_backstop_catches_random_tokens():
    rng = random.Random(20261119)
    tokens = ["".join(rng.choice(ALNUM) for _ in range(32)) for _ in range(2000)]
    caught = [t for t in tokens if any(h.rule == "entropy" for h in scan.scan(t))]
    assert len(caught) >= 0.95 * len(tokens), len(caught)
    for text in (f"feat/{caught[0]}-retry", f"src/{caught[1]}.py", f"Client_{caught[2]}"):
        assert "entropy" in [h.rule for h in scan.scan(text)], text
    hexes = ["".join(rng.choice("0123456789abcdef") for _ in range(40)) for _ in range(2000)]
    assert all("hex-run" in [h.rule for h in scan.scan(t)] for t in hexes)
    b64 = "".join(rng.choice(ALNUM) for _ in range(38)) + "+x=="                # with a +: scored whole
    assert "entropy" in [h.rule for h in scan.scan(b64)]
    for clean in ("TestSessionKindsAreParametrisedOverGitCloudAndTeam", "x" * 200, "q1Z2" * 20,
                  "orchestraty/tests/test_team_policy_branch_copy_is_not_in_force.py"):
        assert scan.scan(clean) == [], clean


def test_sha_fields_are_exempt():
    for sha in (hashlib.sha1(b"a commit").hexdigest(), hashlib.sha256(b"a commit").hexdigest()):
        assert scan.scan(sha, sha=True) == []
        assert [h.rule for h in scan.scan(sha)] == ["hex-run"]                # in any other field it is a hit
        assert [h.rule for h in scan.scan(f"cache/{sha}.pkl")] == ["hex-run"]
    assert "github-token" in [h.rule for h in scan.scan(SAMPLES["github-token"](), sha=True)]


def test_hit_never_holds_the_text():
    secret = SAMPLES["github-token"]()
    hits = scan.scan(f"feat/{secret}-and-more")
    assert hits and scan.Hit._fields == ("rule", "start", "length")
    for hit in hits:
        assert type(hit) is scan.Hit and hit.rule in scan.RULES
        assert isinstance(hit.start, int) and isinstance(hit.length, int)
        assert secret not in repr(hit) and secret[4:12] not in repr(hit) and secret[4:12] not in str(tuple(hit))


def test_self_test_passes(monkeypatch):
    assert scan.self_test() == []
    monkeypatch.setattr(scan, "_COMPILED", scan._COMPILED[1:])
    assert scan.self_test() == [f"{scan.NAMED_RULES[0]} does not fire"]


def test_no_token_shaped_literal_in_the_source():
    found = []
    for path in _git("ls-files", "orchestraty/orchestraty/team", "orchestraty/tests").splitlines():
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        found += [(path, h.rule) for h in scan.scan(text) if h.rule in scan.PROVIDER_RULES]
    assert found == []


def test_scanning_is_linear():
    for text in ("a" * 10_000, "github_pat_" * 1000, "https://" + "u" * 10_000, "password" + " " * 10_000 + ":",
                 "-" * 10_000, "A1b2+" * 2_000, "0" * 10_000):
        started = time.perf_counter()
        scan.scan(text)
        took = time.perf_counter() - started
        assert took < 0.05, (text[:12], took)


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _identifiers(source):
    names = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:                  # a fixture written not to parse
        return names
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


pytestmark = pytest.mark.filterwarnings("ignore::SyntaxWarning")
