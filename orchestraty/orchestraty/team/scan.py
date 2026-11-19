"""The secret scanner: the second line of defence for what a record shared with teammates holds.
The first is that no field of a record is free text.

``scan(text)`` gives a Hit (rule, start, length) for each part of a text shaped like a secret;
a Hit never holds the text. The rules:

* named formats: tokens and keys of known providers, a private key's header, credentials in a
  URL and assignments such as ``password=...``;
* the entropy backstop: in each run of 32 or more of ``A-Z a-z 0-9 + / = _ -``, a piece of 32
  characters or more with a digit, a lowercase and an uppercase letter and at least 4.2 bits
  per character is a hit (rule ``entropy``). A run with a ``+`` in it, or ending in ``=``, is
  scored whole, as base64; any other is split on ``_``, ``/`` and ``-`` first;
* a hex run of 40 characters or more (rule ``hex-run``).

A sha field (``sha=True``), a commit id by its grammar, is not given to the backstop or the hex
rule. A token's name must start a word (no letter or digit just before it), so ``task-...`` is
not taken for a key. Every repeat in a pattern is bounded, so a scan takes time in proportion to
the text. Each pattern is written here in two parts, so that this file holds nothing a secret
scanner would take for a token; ``self_test`` builds a sample of each rule at run time and checks
that it fires, and that clean samples do not.
"""
import math
import re
from collections import Counter, namedtuple

Hit = namedtuple("Hit", "rule start length")

_WORD = r"(?<![A-Za-z0-9])"          # a token starts a word
_NAMED = (
    ("aws-access-key", r"(?<![A-Z0-9])(?:AK" r"IA|AS" r"IA|AG" r"PA|AI" r"DA|AR" r"OA|AN" r"PA)[A-Z0-9]{16}"),
    ("github-token", _WORD + r"g" r"h[pousr]_[A-Za-z0-9]{36,255}"),
    ("github-pat", _WORD + r"github" r"_pat_[A-Za-z0-9_]{60,255}"),
    ("gitlab-token", _WORD + r"g" r"l(?:pat|ptt|rt|dt)-[A-Za-z0-9_-]{20,255}"),
    ("slack-token", _WORD + r"xo" r"x[abposr]-[A-Za-z0-9-]{10,255}"),
    ("stripe-key", _WORD + r"(?:s" r"k|r" r"k)_(?:live|test)_[A-Za-z0-9]{16,255}"),
    ("google-api-key", _WORD + r"AI" r"za[0-9A-Za-z_-]{35}"),
    ("openai-key", _WORD + r"s" r"k-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,255}"),
    ("anthropic-key", _WORD + r"s" r"k-ant-[A-Za-z0-9_-]{32,255}"),
    ("npm-token", _WORD + r"np" r"m_[A-Za-z0-9]{36}"),
    ("pypi-token", _WORD + r"pypi" r"-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{40,255}"),
    ("sendgrid-key", _WORD + r"S" r"G\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"),
    ("jwt", _WORD + r"ey" r"J[A-Za-z0-9_-]{8,1024}\.ey" r"J[A-Za-z0-9_-]{8,4096}\.[A-Za-z0-9_-]{0,1024}"),
    ("private-key", r"-----BEGIN [A-Z ]{0,20}PRI" r"VATE KEY"),
    ("url-credentials", r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s/?#@:]{1,128}:[^\s/?#@]{1,256}@"),
    ("assignment", r"(?i)(?:pass" r"word|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\s{0,16}[:=]\s{0,16}"
                   r"\S{8,255}"),
    ("azure-key", r"Account" r"Key=[A-Za-z0-9+/]{40,255}={0,2}"),
)
_COMPILED = tuple((rule, re.compile(pattern)) for rule, pattern in _NAMED)
NAMED_RULES = tuple(rule for rule, _ in _NAMED)
RULES = NAMED_RULES + ("entropy", "hex-run")
# The named rules for tokens and keys that providers issue: none may appear in this project's source.
PROVIDER_RULES = ("aws-access-key", "github-token", "github-pat", "gitlab-token", "slack-token", "stripe-key",
                  "google-api-key", "openai-key", "anthropic-key", "npm-token", "pypi-token", "sendgrid-key", "jwt",
                  "private-key", "azure-key")
MIN_PIECE = 32
MIN_BITS = 4.2
MIN_HEX = 40
_RUN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_PIECE = re.compile(r"[^_/-]+")
_HEX = re.compile(r"[0-9a-fA-F]{40,}")
_CLEAN = ("feat/refund-flow", "claude/task-123e4567-e89b-12d3-a456-426614174000", "orchestraty/team/policy.py",
          "test_each_named_rule_fires_in_branch_path_and_symbol", "PaymentGatewayClient.charge_customer",
          "Refund flow for partial captures")


def scan(text, sha=False):
    """The Hits in a text, in order. With `sha`, the text is a sha field: only the named rules
    look at it."""
    hits = [Hit(rule, m.start(), m.end() - m.start()) for rule, pattern in _COMPILED for m in pattern.finditer(text)]
    if not sha:
        for m in _RUN.finditer(text):
            run = m.group()
            if "+" in run or run.endswith("="):
                pieces = ((m.start(), run),)
            else:
                pieces = ((m.start() + p.start(), p.group()) for p in _PIECE.finditer(run))
            hits += [Hit("entropy", start, len(piece)) for start, piece in pieces if _random(piece)]
        hits += [Hit("hex-run", m.start(), m.end() - m.start()) for m in _HEX.finditer(text)]
    return sorted(hits, key=lambda h: (h.start, RULES.index(h.rule)))


def _random(piece):
    """Whether a piece reads as random: long enough, digits and both cases, and at least
    MIN_BITS of entropy per character."""
    if len(piece) < MIN_PIECE or not (any(c.isdigit() for c in piece) and any(c.islower() for c in piece)
                                      and any(c.isupper() for c in piece)):
        return False
    n = len(piece)
    return -sum(k / n * math.log2(k / n) for k in Counter(piece).values()) >= MIN_BITS


def _body(n, alphabet="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", step=7):
    """n characters that look random: the alphabet in turn, stepping."""
    return "".join(alphabet[(3 + i * step) % len(alphabet)] for i in range(n))


def _samples():
    """A sample of each rule, built here and now."""
    upper = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    urlsafe = upper + "abcdefghijklmnopqrstuvwxyz_-"
    return (
        ("aws-access-key", "AK" + "IA" + _body(16, upper)),
        ("github-token", "gh" + "p_" + _body(36)),
        ("github-pat", "github" + "_pat_" + _body(82)),
        ("gitlab-token", "gl" + "pat-" + _body(20)),
        ("slack-token", "xo" + "xb-" + _body(12, "0123456789") + "-" + _body(24)),
        ("stripe-key", "sk" + "_live_" + _body(24)),
        ("google-api-key", "AI" + "za" + _body(35, urlsafe)),
        ("openai-key", "sk" + "-proj-" + _body(48, urlsafe)),
        ("anthropic-key", "sk" + "-ant-" + _body(48, urlsafe)),
        ("npm-token", "np" + "m_" + _body(36)),
        ("pypi-token", "pypi" + "-AgEIcHlwaS5vcmc" + _body(48, urlsafe)),
        ("sendgrid-key", "S" + "G." + _body(22, urlsafe) + "." + _body(43, urlsafe)),
        ("jwt", "ey" + "J" + _body(20, urlsafe) + ".ey" + "J" + _body(40, urlsafe) + "." + _body(43, urlsafe)),
        ("private-key", "-----BEGIN OPENSSH PRI" + "VATE KEY-----"),
        ("url-credentials", "https://" + "deploy:" + _body(16) + "@git.example.com/app.git"),
        ("assignment", "api_" + "key: " + _body(20)),
        ("azure-key", "Account" + "Key=" + _body(86, upper + "abcdefghijklmnopqrstuvwxyz+/") + "=="),
        ("entropy", _body(40)),
        ("hex-run", _body(40, "0123456789abcdef")),
    )


def self_test():
    """Check the scanner: each rule fires on a sample of its own, alone and inside a branch and
    a path, clean samples give nothing, and a sha field is left to the named rules. Returns what
    failed, an empty list when all is well. Doctor runs it."""
    failed = []
    for rule, sample in _samples():
        if not all(rule in (h.rule for h in scan(text)) for text in (sample, f"feat/{sample}", f"src/{sample}/a.py")):
            failed.append(f"{rule} does not fire")
    failed += [f"{h.rule} fires on a clean sample" for text in _CLEAN for h in scan(text)]
    if scan(_body(40, "0123456789abcdef"), sha=True):
        failed.append("a sha field is not left to the named rules")
    return failed
