"""The oracle of the labelled sample: for each scenario and evidence, the findings bob's machine
should make about alice's branch, each labelled. It was written from the rules as README.md
describes them, before the sample was first run, and is not changed to fit what the code gives.

    ORACLE[(scenario id, evidence)] -> [(rule, file, label)]

`[]` means silence is expected. A finding that tells bob's session what it needs is `real`:
merging both branches as they are would break bob's work, or lose one side's work, or both
reserved the same work. One that is true but needs nothing of bob is `harmless`: the changes
compose, or what breaks is alice's to fix (her machine tells her). One that is wrong is
`false`. O1 (the same file, weak, status only) is left out, and a latent finding (bob is not
changing the file) counts only once its note is delivered, before bob's first edit of the file.
"""
REAL, HARMLESS, FALSE = "real", "harmless", "false"
REFUNDS, BILLING, REPORTS, API = "shop/refunds.py", "shop/billing.py", "shop/reports.py", "shop/api.py"

_MATRIX = {
    # C1 remove charge: every call in refunds.py breaks; bob editing charge loses his edit.
    "C1": {"K1": [("O4", REFUNDS, REAL)], "K2": [("O4", REFUNDS, REAL)], "K3": [("O2", BILLING, REAL)],
           "K4": [], "K5": [("O4", REFUNDS, REAL)], "K6": []},
    # C2 rename currency: the keyword call breaks. Bob editing only the positional caller is told of
    # the keyword one in the same file: true, but not his to fix.
    "C2": {"K1": [("O3", REFUNDS, REAL)], "K2": [("O3", REFUNDS, HARMLESS)], "K3": [("O2", BILLING, HARMLESS)],
           "K4": [], "K5": [("O3", REFUNDS, REAL)], "K6": []},
    # C3 a required region: both calls break.
    "C3": {"K1": [("O3", REFUNDS, REAL)], "K2": [("O3", REFUNDS, REAL)], "K3": [("O2", BILLING, HARMLESS)],
           "K4": [], "K5": [("O3", REFUNDS, REAL)], "K6": []},
    # C4 an optional note: nothing breaks; bob's body edit and her signature compose.
    "C4": {"K1": [], "K2": [], "K3": [("O2", BILLING, HARMLESS)], "K4": [], "K5": [], "K6": []},
    # C5 reorder to (currency, amount), currency now required: both calls break.
    "C5": {"K1": [("O3", REFUNDS, REAL)], "K2": [("O3", REFUNDS, REAL)], "K3": [("O2", BILLING, HARMLESS)],
           "K4": [], "K5": [("O3", REFUNDS, REAL)], "K6": []},
    # C6 the body only: no call breaks; both editing the same lines must settle on one.
    "C6": {"K1": [], "K2": [], "K3": [("O2", BILLING, REAL)], "K4": [], "K5": [], "K6": []},
    # C7 move to payments.py: the imports of shop.billing.charge break (a known miss: a move rates
    # as review, not breaking); bob's edit of charge where it no longer lives is lost.
    "C7": {"K1": [("O3", REFUNDS, REAL)], "K2": [("O3", REFUNDS, REAL)], "K3": [("O2", BILLING, REAL)],
           "K4": [], "K5": [("O3", REFUNDS, REAL)], "K6": []},
    # C8 remove Invoice.total: only reports.py uses it, which no behaviour edits (K3: the same file only).
    "C8": {"K1": [], "K2": [], "K3": [], "K4": [], "K5": [], "K6": []},
    # C9 the route POST /refunds becomes /refund: bob changing the same route meets it.
    "C9": {"K1": [], "K2": [], "K3": [], "K4": [], "K5": [], "K6": [("O5", API, REAL)]},
}

_OTHERS = {
    "X1.merged": [],                                  # merged before bob reads: not a session
    "X2.deleted": [],                                 # deleted before bob reads: not a session
    "X3.checkout": [],                                # bob's copy holds her tip: not compared
    "X4.stacked": [],                                 # her branch builds on his pushed one: not compared
    "X5.claims": [("O7", REFUNDS, REAL)],             # her claim of shop/, his declaration of refunds.py
    "X6.cloud": [("O3", REFUNDS, REAL)],              # once, as a teammate's branch
    "X7.two_devices": [("O3", REFUNDS, REAL)],        # once, whichever device names it
    "H1.adapted": [],                                 # bob passes region already: nothing breaks
    "H2.reports": [("O4", REPORTS, REAL)],
    "H3.new_caller": [("O3", "shop/credit.py", REAL)],
    "H4.keywords": [],                                # every argument by keyword: a reorder breaks none
    "H5.other_route": [],                             # another route
    "H6.reports_only": [],                            # reports.py does not use charge
    "H7.two_changes": [("O3", REFUNDS, REAL), ("O4", REPORTS, REAL)],
    "H8.required_retries": [("O3", REFUNDS, REAL)],
    "H9.antigravity": [("O3", REFUNDS, REAL)],
    "H10.new_function": [],                           # a new function changes no contract
}

ORACLE = {(f"{c}.{k}", "commits"): expected for c, row in _MATRIX.items() for k, expected in row.items()}
ORACLE.update({(sid, "commits"): expected for sid, expected in _OTHERS.items()})

# Misses known before the first run, with why: they count against recall.
KNOWN_MISSES = {
    ("C7.K1", "O3", REFUNDS): "a move rates as review, not breaking (Phase 1)",
    ("C7.K2", "O3", REFUNDS): "a move rates as review, not breaking (Phase 1)",
    ("C7.K5", "O3", REFUNDS): "a move rates as review, not breaking (Phase 1)",
}
