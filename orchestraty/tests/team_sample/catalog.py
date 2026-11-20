"""The scenarios of the labelled sample, played on tests/fixtures/team_shop.

Alice's changes (C1 to C9) are each one commit on a branch she pushed, named in her record.
Bob's behaviours (K1 to K6) are what a session on his machine does before he reads her record:
uncommitted edits through spooled events, or none. The matrix plays every change with every
behaviour; the controls check the cases where nothing must be compared or told twice; the hand
scenarios add the cases the matrix misses.
"""
from dataclasses import dataclass, field
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "team_shop"


def text(rel):
    return (FIXTURE / rel).read_text(encoding="utf-8")


BILLING, REFUNDS, REPORTS, API = (text(f"shop/{name}.py") for name in ("billing", "refunds", "reports", "api"))
SIGNATURE = 'def charge(amount, currency="EUR", *, retries=0):'
CHARGE = SIGNATURE + "\n    return amount\n\n\n"
TOTAL = "\n    def total(self, tax):\n        return self.amount * (1 + tax)\n"


def signature(new):
    return {"shop/billing.py": BILLING.replace(SIGNATURE, new)}


CHANGES = {                       # alice's: {path: text, or None to delete}
    "C1": {"shop/billing.py": BILLING.replace(CHARGE, "")},                                   # remove charge
    "C2": signature('def charge(amount, cur="EUR", *, retries=0):'),                        # rename currency
    "C3": signature('def charge(amount, currency="EUR", *, retries=0, region):'),           # a required region
    "C4": signature('def charge(amount, currency="EUR", *, retries=0, note=None):'),        # an optional note
    "C5": signature("def charge(currency, amount, *, retries=0):"),                         # reorder
    "C6": {"shop/billing.py": BILLING.replace("    return amount\n",                           # the body only
                                              "    if amount < 0:\n        raise ValueError(amount)\n"
                                              "    return amount\n", 1)},
    "C7": {"shop/billing.py": BILLING.replace(CHARGE, ""),                                    # move to payments.py
           "shop/payments.py": SIGNATURE + "\n    return amount\n"},
    "C8": {"shop/billing.py": BILLING.replace(TOTAL, "")},                                    # remove Invoice.total
    "C9": {"shop/api.py": API.replace('@app.post("/refunds")', '@app.post("/refund")')},     # change the route
}

BEHAVIOURS = {                    # bob's uncommitted edits, and a file whose edit slot his hook takes
    "K1": {"edits": {"shop/refunds.py": REFUNDS.replace("def refund(a, c):\n", "def refund(a, c):\n    a = abs(a)\n")}},
    "K2": {"edits": {"shop/refunds.py": REFUNDS.replace("def flat():\n    return charge(10)",
                                                         "def flat():\n    fee = 10\n    return charge(fee)")}},
    "K3": {"edits": {"shop/billing.py": BILLING.replace("    return amount\n", "    return amount if amount > 0 else 0\n",
                                                         1)}},
    "K4": {"edits": {"shop/notes.py": "NOTE = 1\n"}},                                     # an unrelated file
    "K5": {"edits": {}, "take": "shop/refunds.py"},                                         # about to edit refunds.py
    "K6": {"edits": {"shop/api.py": API.replace('@app.post("/refunds")', '@app.put("/refunds")')}},
}


@dataclass(frozen=True)
class Scenario:
    """One play: alice's files, bob's edits, and what is special about it (`control`)."""
    id: str
    change: dict
    edits: dict = field(default_factory=dict)
    take: str = ""                 # bob's hook takes the edit slot of this file after the fetch
    control: str = ""              # merged, deleted, checkout, stacked, claims, cloud or two_devices
    agent: str = "claude"          # bob's agent
    declare: tuple = ()            # paths bob's session declares (MCP declare)
    claims: tuple = ()             # alice's shared claims: (kind, target, file)
    bob_commit: dict = field(default_factory=dict)  # stacked: bob's pushed commit alice builds on


def matrix():
    for c, change in CHANGES.items():
        for k, how in BEHAVIOURS.items():
            yield Scenario(f"{c}.{k}", change, edits=how["edits"], take=how.get("take", ""))


K1 = BEHAVIOURS["K1"]["edits"]
CONTROLS = [
    Scenario("X1.merged", CHANGES["C3"], edits=K1, control="merged"),          # merged before bob reads
    Scenario("X2.deleted", CHANGES["C3"], edits=K1, control="deleted"),        # deleted before bob reads
    Scenario("X3.checkout", CHANGES["C3"], edits=K1, control="checkout"),      # bob goes on from her tip (D4)
    Scenario("X4.stacked", {"shop/reports.py": REPORTS.replace("0.2", "0.25")},  # her branch on his (D4')
             bob_commit=CHANGES["C3"], control="stacked"),
    Scenario("X5.claims", {"shop/notes.py": "NOTE = 2\n"}, declare=("shop/refunds.py",),   # her claim, his plan
             claims=(("area", "shop", ""),), control="claims"),
    Scenario("X6.cloud", CHANGES["C3"], edits=K1, control="cloud"),            # also a cloud branch (D3)
    Scenario("X7.two_devices", CHANGES["C3"], edits=K1, control="two_devices"),  # two of her devices (D2)
]

HAND = [
    Scenario("H1.adapted", CHANGES["C3"], edits={"shop/refunds.py": REFUNDS.replace(       # bob passes region already
        "charge(a, currency=c)", "charge(a, currency=c, region='EU')").replace("charge(10)", "charge(10, region='EU')")}),
    Scenario("H2.reports", CHANGES["C8"], edits={"shop/reports.py": REPORTS.replace("0.2", "0.25")}),
    Scenario("H3.new_caller", CHANGES["C2"], edits={"shop/credit.py": "from shop.billing import charge\n\n\n"
                                                                      "def credit():\n    return charge(5, currency='USD')\n"}),
    Scenario("H4.keywords", CHANGES["C5"], edits={"shop/refunds.py": REFUNDS.replace(      # keyword calls survive a reorder
        "charge(a, currency=c)", "charge(amount=a, currency=c)").replace("charge(10)", "charge(amount=10, currency='EUR')")}),
    Scenario("H5.other_route", CHANGES["C9"], edits={"shop/api.py": API + '\n\n@app.get("/refunds/{rid}")\n'
                                                                      'def read_refund(rid):\n    return {}\n'}),
    Scenario("H6.reports_only", CHANGES["C1"], edits={"shop/reports.py": REPORTS.replace("0.2", "0.25")}),
    Scenario("H7.two_changes", {"shop/billing.py": BILLING.replace(SIGNATURE, 'def charge(amount, cur="EUR", *, retries=0):')
                                .replace(TOTAL, "")},
             edits={**K1, "shop/reports.py": REPORTS.replace("0.2", "0.25")}),
    Scenario("H8.required_retries", signature('def charge(amount, currency="EUR", *, retries):'),
             edits=BEHAVIOURS["K2"]["edits"]),
    Scenario("H9.antigravity", CHANGES["C3"], edits=K1, agent="antigravity"),   # takes no edit note: the next one
    Scenario("H10.new_function", {"shop/billing.py": BILLING + "\n\ndef capture(amount):\n    return amount\n"},
             edits=K1),
]

SCENARIOS = list(matrix()) + CONTROLS + HAND
