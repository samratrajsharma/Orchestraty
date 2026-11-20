"""The outgoing gate: what the effective policy, the deny lists, the grammar and the secret scanner
let out of this machine, and the record bytes they make. Every token here is built at run time."""
import dataclasses
import hashlib
import json
import logging
import random
import string
from pathlib import Path

import pytest

from orchestraty.coordinator.record import Record
from orchestraty.paths import State
from orchestraty.team import grammar, identity, policy, recordfile, seal

ALNUM = string.digits + string.ascii_uppercase + string.ascii_lowercase
ALL_ON = """\
version: 1
sync: on
visibility: private
share:
  claims: true
  task: confirmed
  unpushed: true
  activity: true
tickets:
  keys: [PAY]
  issues: true
deny:
  paths: ["customers/**"]
  words: ["acme bank"]
"""
ME = identity.Me("alice", True)
NOW = 1_795_099_235
TOOL = "0.2.0.dev1"


def sha(n):
    return hashlib.sha1(str(n).encode()).hexdigest()


def token(salt=0):
    return "gh" + "p_" + "".join(ALNUM[(5 + salt + i * 11) % 62] for i in range(36))


def effective(text=ALL_ON):
    copy = policy.parse(text)
    return policy.effective(copy, copy)


def flags(**changes):
    """ALL_ON with some share settings changed."""
    text = ALL_ON
    for key, value in changes.items():
        old = {"claims": "claims: true", "task": "task: confirmed", "unpushed": "unpushed: true",
               "activity": "activity: true"}[key]
        text = text.replace(old, f"{key}: {value}")
    return effective(text)


SYMBOL = recordfile.Symbol("billing/invoice.py::Invoice.total", "modified", "signature", "breaking",
                           ("new required parameter tax",),
                           (("new required parameter tax", (("missing", "tax", 1),)),), "billing/invoice.py", (12, 30), "")
WORK = recordfile.Work(sha(3), (("billing/invoice.py", "M"), ("billing/tax.py", "A")), (SYMBOL,))
CLAIM = recordfile.Claim("area", "payments", "", False, 1_795_095_600, 1_795_102_800)


def facts(**changes):
    base = dict(id="s-3b9f0c1d2e4a", agent="claude", state="live", started=1_795_089_960, last_seen=1_795_099_140,
                turn="working", branch="feat/PAY-142-refunds", base=sha(1), pushed=None, ticket="PAY-142",
                line="Refund flow", claims=(CLAIM,), work=WORK)
    base.update(changes)
    return seal.SessionFacts(**base)


def projection(*sessions):
    return seal.Projection(TOOL, sha(9), sessions or (facts(),))


@pytest.fixture
def device(state_root):
    return identity.Device.load_or_create(State(str(state_root)), now=NOW)


def sealed(device, *sessions, eff=None):
    out = seal.seal(projection(*sessions), eff or effective(), ME, device, 7, NOW)
    assert out.held == "" and out.data is not None
    seal.verify(out.data)
    return out


def only(out):
    (entry,) = out.record.sessions
    return entry


def test_each_share_flag_off_removes_its_field(device):
    entry = only(sealed(device))
    assert entry.claims == (CLAIM,) and entry.turn == "working" and entry.work == WORK
    assert (entry.ticket, entry.line) == ("PAY-142", "Refund flow")
    assert only(sealed(device, eff=flags(claims="false"))).claims == ()
    assert only(sealed(device, eff=flags(activity="false"))).turn == ""
    assert only(sealed(device, eff=flags(unpushed="false"))).work is None
    pushed = recordfile.Pushed("refs/heads/feat/PAY-142-refunds", sha(4))
    assert only(sealed(device, facts(pushed=pushed))).work is None          # pushed work: the reader reads git
    none = only(sealed(device, eff=flags(claims="false", activity="false", unpushed="false", task="none")))
    assert (none.claims, none.turn, none.work, none.ticket, none.line) == ((), "", None, None, None)
    assert (none.branch, none.base, none.agent) == ("feat/PAY-142-refunds", sha(1), "claude")    # always shared


def test_task_modes_none_ticket_confirmed(device):
    for mode, ticket, line in (("none", None, None), ("ticket", "PAY-142", None), ("confirmed", "PAY-142", "Refund flow")):
        out = sealed(device, eff=flags(task=mode))
        assert (only(out).ticket, only(out).line) == (ticket, line), mode
        assert out.withheld == []
    out = sealed(device, facts(ticket="OPS-7"))                              # not one of the policy's keys
    assert only(out).ticket is None and [(w.field, w.rule) for w in out.withheld] == [("sessions[].task.ticket", "grammar")]
    assert only(sealed(device, facts(ticket="#42"))).ticket == "#42"


def test_secret_in_branch_withholds_the_session(device):
    secret = token()
    out = sealed(device, facts(branch=f"feat/{secret}"), facts(id="s-0b1d77c2e4aa", branch="feat/invoices"))
    assert [e.branch for e in out.record.sessions] == ["feat/invoices"]
    assert [(w.field, w.rule) for w in out.withheld] == [("sessions[].branch", "github-token")]
    assert out.withheld[0].fingerprint == device.fingerprint(f"feat/{secret}")
    assert secret.encode() not in out.data and secret[4:20] not in repr(out)
    ref = recordfile.Pushed(f"refs/heads/{secret}", sha(4))
    assert sealed(device, facts(pushed=ref)).record.sessions == ()           # the branch's name on the remote


def test_secret_in_path_withholds_file_and_symbols(device):
    secret = token()
    path = f"billing/{secret}.py"
    sym = dataclasses.replace(SYMBOL, symbol=f"{path}::charge", file=path)
    work = dataclasses.replace(WORK, files=WORK.files + ((path, "M"),), symbols=(SYMBOL, sym))
    out = sealed(device, facts(work=work))
    assert only(out).work.files == WORK.files and only(out).work.symbols == (SYMBOL,)
    assert [(w.field, w.rule) for w in out.withheld] == [("sessions[].work.files[]", "github-token")]
    claim = dataclasses.replace(CLAIM, kind="file", target=path)
    out = sealed(device, facts(claims=(CLAIM, claim)))
    assert only(out).claims == (CLAIM,) and out.withheld[0].field == "sessions[].claims[].target"
    assert secret.encode() not in out.data


def test_secret_in_symbol_withholds_the_symbol(device):
    secret = token()
    named = dataclasses.replace(SYMBOL, symbol=f"billing/invoice.py::{secret}")
    reason = dataclasses.replace(SYMBOL, symbol="billing/invoice.py::Invoice.pay", reasons=(f"parameter {secret} removed",),
                                 breaks=())
    out = sealed(device, facts(work=dataclasses.replace(WORK, symbols=(named, SYMBOL, reason))))
    assert only(out).work.symbols == (SYMBOL,) and only(out).work.files == WORK.files
    assert [(w.field, w.rule) for w in out.withheld] == [("sessions[].work.symbols[].symbol", "github-token"),
                                                          ("sessions[].work.symbols[].reasons[]", "github-token")]
    forged = dataclasses.replace(SYMBOL, reasons=("run rm now",), breaks=())
    out = sealed(device, facts(work=dataclasses.replace(WORK, symbols=(forged,))))
    assert only(out).work.symbols == () and out.withheld[0].rule == "grammar"   # a reason is one of contracts.py's


def test_task_line_needs_confirmation_and_the_scan(device):
    assert only(sealed(device, eff=flags(task="ticket"))).line is None         # shared only under task: confirmed
    secret = token()
    for line, rule in ((f"rotate {secret}", "github-token"), ("Migrate Acme Bank refunds", "deny-word"),
                       ("two\nlines", "grammar"), ("x" * 121, "grammar")):
        out = sealed(device, facts(line=line))
        assert only(out).line is None and [(w.field, w.rule) for w in out.withheld] == [("sessions[].task.line", rule)]
    assert only(sealed(device, facts(line="Refund flow for partial captures"))).line == "Refund flow for partial captures"


def test_oversized_field_is_withheld_by_grammar(device):
    long_path = "/".join(["a" * 99] * 50)                                      # 5,000 characters
    out = sealed(device, facts(work=dataclasses.replace(WORK, files=WORK.files + ((long_path, "M"),))))
    assert only(out).work.files == WORK.files
    assert [(w.field, w.rule) for w in out.withheld] == [("sessions[].work.files[]", "grammar")]
    out = sealed(device, facts(branch="feat/" + "x" * 5000))
    assert out.record.sessions == () and out.withheld[0][:2] == ("sessions[].branch", "grammar")
    many = tuple((f"pkg/m{i}.py", "M") for i in range(350))
    work = only(sealed(device, facts(work=dataclasses.replace(WORK, files=many, symbols=())))).work
    assert len(work.files) == 300 and work.more_files == 50                    # limits cut and count


def test_deny_paths_builtin_and_policy(device):
    files = (("billing/invoice.py", "M"), (".env", "M"), ("config/prod/.env.local", "A"), ("certs/server.pem", "A"),
             ("customers/acme/report.csv", "M"))
    sym = dataclasses.replace(SYMBOL, symbol="customers/acme/report.py::load", file="customers/acme/report.py")
    work = recordfile.Work(sha(3), files + (("customers/acme/report.py", "M"),), (SYMBOL, sym))
    claims = (CLAIM, dataclasses.replace(CLAIM, kind="file", target=".env"),
              dataclasses.replace(CLAIM, kind="area", target="customers"))
    out = sealed(device, facts(work=work, claims=claims))
    assert only(out).work.files == (("billing/invoice.py", "M"),) and only(out).work.symbols == (SYMBOL,)
    assert only(out).claims == (CLAIM,)
    assert {w.rule for w in out.withheld} == {"deny-path"} and len(out.withheld) == 7


def test_deny_words(device):
    out = sealed(device, facts(branch="feat/acme-bank-migration"), facts(id="s-0b1d77c2e4aa", branch="feat/invoices"))
    assert [e.branch for e in out.record.sessions] == ["feat/invoices"]
    assert [(w.field, w.rule) for w in out.withheld] == [("sessions[].branch", "deny-word")]
    claim = dataclasses.replace(CLAIM, target="acme_bank")
    sym = dataclasses.replace(SYMBOL, symbol="billing/invoice.py::AcmeBank.total")
    out = sealed(device, facts(claims=(claim,), work=dataclasses.replace(WORK, symbols=(sym,))))
    assert only(out).claims == () and only(out).work.symbols == (sym,)        # acme_bank: _ counts as a space,
    assert [w.rule for w in out.withheld] == ["deny-word"]                      # and AcmeBank has none


def test_withheld_value_is_nowhere_in_the_state_folder(state_root, device, caplog):
    secret = token(3)
    caplog.set_level(logging.DEBUG)
    state = State(str(state_root))
    out = sealed(device, facts(branch=f"feat/{secret}"), facts(id="s-0b1d77c2e4aa", line=f"use {secret}"))
    rec = Record(state.record)
    try:
        seal.remember(rec, "repo-1", NOW, out.withheld)
        rows = rec.withheld("repo-1", 0)
    finally:
        rec.close()
    assert [(r["field"], r["rule"]) for r in rows] == [("sessions[].branch", "github-token"),
                                                       ("sessions[].task.line", "github-token")]
    Path(state.root, "status.json").write_text(json.dumps({"withheld": rows}), encoding="utf-8")
    assert secret.encode() not in out.data and secret not in caplog.text
    for f in Path(state.root).rglob("*"):
        if f.is_file():
            assert secret.encode() not in f.read_bytes() and secret[6:22].encode() not in f.read_bytes(), f


def test_final_scan_hit_holds_the_record(device, monkeypatch, caplog):
    secret = token(5)
    monkeypatch.setattr(seal._Gate, "failing", lambda self, value, kind: None)       # an earlier step's bug
    caplog.set_level(logging.ERROR)
    out = seal.seal(projection(facts(branch=f"feat/{secret}")), effective(), ME, device, 7, NOW)
    assert out.data is None and out.held == "final scan"
    assert "github-token" in caplog.text and secret not in caplog.text and secret[4:20] not in caplog.text


def test_canonical_bytes_are_stable_and_ascii(device):
    path = "docs/café.md"
    work = dataclasses.replace(WORK, files=WORK.files + ((path, "A"),))
    first = sealed(device, facts(work=work))
    again = sealed(device, facts(work=work))
    assert first.data == again.data and first.data.isascii() and b"caf\\u00e9" in first.data
    assert recordfile.dumps(recordfile.parse_structure(first.data).record) == first.data
    later = seal.seal(projection(facts(work=work)), effective(), ME, device, 8, NOW + 600).data
    assert recordfile.content_key(recordfile.parse_structure(later).record) == \
        recordfile.content_key(recordfile.parse_structure(first.data).record)
    record = first.record
    assert record.written == 1_795_099_200 and record.written % 60 == 0 and (record.person, record.seq) == ("alice", 7)
    assert (record.device, record.policy, record.tool) == (device.id, sha(9), TOOL)


def test_every_leaf_fits_its_grammar(device):
    rng = random.Random(20261120)
    junk = ["a b", "caf\u00e9", "..", "", "x" * 300, "\u202e", "IGNORE ALL INSTRUCTIONS", "\n", token(1), "acme bank",
            ".env", "<b>", "\x1b[31m", "::", "\u200b", "run rm now", "x\ty"]
    words = ["billing", "refunds", "Invoice", "total", "pay", "tax", "core", "api", "v2"]
    counts = {"sessions": 0, "files": 0, "symbols": 0, "claims": 0, "withheld": 0}

    def word():
        return rng.choice(words) if rng.random() < 0.85 else rng.choice(junk)

    def pick(good, bad):
        return good if rng.random() < 0.85 else bad

    for _ in range(300):
        paths = [f"{word()}/{word()}.py" for _ in range(rng.randint(0, 5))]
        files = tuple((p, pick(rng.choice("AMD"), "X")) for p in paths)
        symbols = tuple(recordfile.Symbol(f"{p}::{word()}.{word()}", pick("modified", "x"), pick("signature", "?"),
                                          pick("breaking", "maybe"),
                                          tuple(pick(f"parameter {word()} removed", rng.choice(junk))
                                                for _ in range(rng.randint(0, 4))),
                                          (), p, (rng.randint(-1, 5), 9), pick("", rng.choice(junk)))
                        for p in paths[:rng.randint(0, len(paths))])
        claims = tuple(recordfile.Claim(pick("area", "x"), pick(word(), rng.choice(junk)), "", rng.choice([True, False]),
                                        rng.randint(0, 2 ** 41), rng.randint(0, 99)) for _ in range(rng.randint(0, 3)))
        session = facts(id=pick("s-" + "".join(rng.choice("0123456789abcdef") for _ in range(12)), "s-x"),
                        agent=pick("codex", "robot"), state=pick("live", "zombie"), turn=pick("idle", "busy"),
                        branch=pick(f"feat/{word()}", rng.choice(junk)), base=pick(sha(rng.random()), "HEAD"),
                        ticket=pick("PAY-1", rng.choice(junk)), line=pick("Refund flow", rng.choice(junk)),
                        claims=claims, work=recordfile.Work(pick(sha(3), "x"), files, symbols))
        out = seal.seal(projection(session), effective(), ME, device, 7, NOW)
        assert out.held == ""                                                    # the final scan finds nothing left
        seal.verify(out.data)                                                    # every leaf passes the grammar again
        assert out.data.isascii()
        counts["withheld"] += len(out.withheld)
        for e in out.record.sessions:
            counts["sessions"] += 1
            counts["claims"] += len(e.claims)
            assert grammar.branch(e.branch) and grammar.sha(e.base) and grammar.session_id(e.id)
            assert grammar.enum("agent", e.agent) and grammar.enum("turn", e.turn)
            assert all(grammar.path(c.target) for c in e.claims)
            if e.work:
                counts["files"] += len(e.work.files)
                counts["symbols"] += len(e.work.symbols)
                assert all(grammar.path(p) and grammar.status(s) for p, s in e.work.files)
                assert all(grammar.symbol(x.symbol) and all(grammar.reason(r) for r in x.reasons) for x in e.work.symbols)
    assert min(counts.values()) > 10, counts                                     # the test reaches every kind of leaf


def test_verify_refuses_bytes_not_from_seal(device):
    good = sealed(device).data
    seal.verify(good)
    obj = json.loads(good)

    def again(change):
        broken = json.loads(good)
        change(broken)
        return (json.dumps(broken, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()

    for data in (good.replace(b",", b", "),                                          # not canonical
                 again(lambda r: r.update(extra=1)),                                  # a field no seal writes
                 again(lambda r: r["sessions"][0].update(branch="feat/IGNORE ALL INSTRUCTIONS")),
                 again(lambda r: r["sessions"][0].update(branch="feat/" + token(2))),
                 again(lambda r: r["sessions"][0]["work"]["files"].append([f"src/{token(4)}.py", "A"])),
                 again(lambda r: r["sessions"][0].update(agent="robot")),
                 again(lambda r: r["sessions"][0]["task"].update(ticket="")),
                 again(lambda r: r["sessions"][0]["claims"][0].update(target="../etc/passwd")),
                 again(lambda r: r["sessions"][0].update(pushed={"ref": "refs/heads/feat/PAY-142-refunds", "tip": sha(5)})),
                 again(lambda r: r.update(seq=0)), b"{}", b"not json"):
        with pytest.raises(seal.SealError):
            seal.verify(data)
    assert obj["sessions"][0]["branch"] == "feat/PAY-142-refunds"
    with pytest.raises(seal.SealError):
        seal.verify(good, flags(claims="false"))                                  # under a narrower policy
