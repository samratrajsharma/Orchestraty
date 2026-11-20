"""The labelled precision sample of team notes (tests/team_sample): alice's changes on pushed
branches against bob's work on his machine, each finding his machine makes about her branch
labelled by an oracle written before the first run. No finding may be missing from the oracle,
no strong one may be labelled false, and strict precision and recall must stay at or above the
floors pinned after the first run. Run with -s for the table."""
import pytest

from team_sample import catalog, run
from team_sample.oracle import KNOWN_MISSES, ORACLE

# Pinned after the first run (71 scenarios): 21 of the 26 findings counted are real, 5 harmless
# and none false (lenient precision 1.0); 21 of the 27 real findings the oracle expects are found.
FLOORS = {"precision_strict": 21 / 26, "recall": 21 / 27}
# What the first run missed: the misses known before it, and those it showed.
MISSED = set(KNOWN_MISSES) | {
    ("C7.K3", "O2", "shop/billing.py"),          # a move keeps the function under its new id: no O2 by id
    ("H2.reports", "O4", "shop/reports.py"),     # Invoice(5).total(0.2): a method on a constructor's result
    ("H7.two_changes", "O4", "shop/reports.py"),  # is not resolved; inv = Invoice(5); inv.total() is
}


@pytest.fixture(scope="module")
def produced(tmp_path_factory):
    with run.isolated(tmp_path_factory.mktemp("team-sample")) as tmp:
        return run.run(catalog.SCENARIOS, tmp)


def test_every_finding_is_in_the_oracle(produced):
    assert [p for p in run.check(produced, ORACLE) if "is not in the oracle" in p] == []


def test_no_strong_finding_is_labelled_false(produced):
    assert [p for p in run.check(produced, ORACLE) if "labelled false" in p] == []


def test_precision_and_recall_hold_their_floors(produced):
    print("\n" + run.table(produced, ORACLE, catalog.SCENARIOS))
    figures = run.metrics(produced, ORACLE, catalog.SCENARIOS)
    for name, floor in FLOORS.items():
        assert figures[name] >= floor - 1e-9, (name, figures[name], floor)
    assert set(figures["misses"]) <= MISSED                # a new miss fails, even with recall held


def test_the_runner_names_the_scenario_of_a_wrong_oracle_entry(produced):
    wrong = dict(ORACLE)
    wrong[("C3.K1", "commits")] = []                    # planted: silence where C3 breaks refund
    assert [p for p in run.check(produced, wrong) if p.startswith("C3.K1: ")] == [
        "C3.K1: O3 shop/refunds.py (strong) is not in the oracle"]
