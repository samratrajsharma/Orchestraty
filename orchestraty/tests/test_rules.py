"""The overlap rules, on session views and code graphs built from real (tiny) code."""
import pytest

from orch_engine.graph import build_graph
from orch_engine.parsing import parse_python
from orchestraty.livemap.map import SessionView, SymbolChange
from orchestraty.rules.overlap import evaluate

BASE = {
    "a.py": "def charge(amount, currency):\n    return amount\n",
    "b.py": "from a import charge\n\n\ndef pay():\n    return charge(1, 'EUR')\n",
}


def graph_of(files):
    return build_graph([parse_python(p, src) for p, src in files.items()])


def view(sid, files=(), symbols=()):
    v = SessionView(sid, "repo", "/w/" + sid, "base")
    v.files = {f: "M" for f in files}
    v.symbols = {s.symbol: s for s in symbols}
    return v


def change(symbol, kind="modified", contract="breaking", reasons=("parameter currency removed",), moved_from=""):
    return SymbolChange(symbol, kind, "signature" if kind != "removed" else "", contract, list(reasons),
                        symbol.split("::")[0], [1, 2], moved_from=moved_from)


def run(views, graphs=None, base=None):
    graphs = graphs or {}
    return evaluate(views, lambda sid: graphs.get(sid), lambda sid: base)


def by_rule(findings, rule, latent=False):
    """The findings of one rule; by default only those about files a session is changing."""
    return [f for f in findings if f.rule == rule and bool(f.detail.get("latent")) == latent]


def test_o1_the_same_file():
    findings = run({"A": view("A", ["x.py", "y.py"]), "B": view("B", ["x.py"])})
    [f] = findings
    assert (f.rule, f.strength, f.file, f.sessions) == ("O1", "weak", "x.py", ("A", "B"))


def test_o2_the_same_function():
    body = change("a.py::charge", contract="none", reasons=())
    findings = run({"A": view("A", ["a.py"], [body]), "B": view("B", ["a.py"], [change("a.py::charge")])})
    [f] = by_rule(findings, "O2")
    assert f.strength == "medium" and f.symbol == "a.py::charge"
    assert f.detail["kinds"] == {"A": "modified", "B": "modified"}
    added = change("a.py::charge", kind="added", contract="compatible", reasons=())
    assert by_rule(run({"A": view("A", ["a.py"], [added]), "B": view("B", ["a.py"], [body])}), "O2") == []


def test_o3_a_breaking_change_used_in_a_file_the_other_session_is_changing():
    b_copy = dict(BASE, **{"c.py": "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"})
    views = {"A": view("A", ["a.py"], [change("a.py::charge")]), "B": view("B", ["c.py"])}
    findings = run(views, graphs={"B": graph_of(b_copy)})
    [f] = by_rule(findings, "O3")
    assert (f.changer, f.receiver, f.file, f.symbol, f.strength) == ("A", "B", "c.py", "a.py::charge", "strong")
    assert f.detail["uses"] == [{"symbol": "c.py::use", "lines": [5]}]
    assert f.detail["reasons"] == ["parameter currency removed"]
    # b.py calls charge too, but B is not changing b.py: a latent finding, which only gives B
    # its note before a first edit of b.py.
    [latent] = by_rule(findings, "O3", latent=True)
    assert (latent.file, latent.receiver) == ("b.py", "B")
    assert latent.detail["uses"] == [{"symbol": "b.py::pay", "lines": [5]}]


def test_no_contract_note_for_compatible_or_review_changes_or_without_a_graph():
    b_copy = dict(BASE, **{"c.py": "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"})
    for contract in ("compatible", "review", "none"):
        views = {"A": view("A", ["a.py"], [change("a.py::charge", contract=contract)]), "B": view("B", ["c.py"])}
        assert run(views, graphs={"B": graph_of(b_copy)}, base=graph_of(BASE)) == []
    views = {"A": view("A", ["a.py"], [change("a.py::charge")]), "B": view("B", ["c.py"])}
    assert by_rule(run(views, graphs={}), "O3") == []               # graph still being built


def test_o4_a_removed_function_still_used():
    b_copy = dict(BASE, **{"c.py": "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"})
    removed = change("a.py::charge", kind="removed", reasons=("removed",))
    findings = run({"A": view("A", ["a.py"], [removed]), "B": view("B", ["c.py"])}, graphs={"B": graph_of(b_copy)})
    [f] = by_rule(findings, "O4")
    assert (f.changer, f.receiver, f.file) == ("A", "B", "c.py") and f.detail["kind"] == "removed"
    assert by_rule(findings, "O3") == []


def test_o6_callers_left_behind_and_callers_someone_updated():
    views = {"A": view("A", ["a.py"], [change("a.py::charge")])}
    [f] = run(views, base=graph_of(BASE))
    assert (f.rule, f.changer, f.symbol) == ("O6", "A", "a.py::charge")
    assert f.detail["left"] == [{"symbol": "b.py::pay", "file": "b.py", "lines": [5]}]
    # Another session updated the caller: nothing is left behind.
    views["B"] = view("B", ["b.py"], [change("b.py::pay", contract="none", reasons=())])
    assert by_rule(run(views, base=graph_of(BASE)), "O6") == []
    # The changing session updated it itself: the same.
    views = {"A": view("A", ["a.py", "b.py"], [change("a.py::charge"),
                                               change("b.py::pay", contract="none", reasons=())])}
    assert by_rule(run(views, base=graph_of(BASE)), "O6") == []


def test_a_moved_and_broken_function_is_found_where_the_other_session_uses_it():
    b_copy = dict(BASE, **{"c.py": "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"})
    moved = change("d.py::charge", kind="moved", moved_from="a.py::charge")
    findings = run({"A": view("A", ["a.py", "d.py"], [moved]), "B": view("B", ["c.py"])},
                   graphs={"B": graph_of(b_copy)}, base=graph_of(BASE))
    [o3] = by_rule(findings, "O3")
    assert o3.symbol == "a.py::charge" and o3.detail["moved_to"] == "d.py::charge"
    [o6] = by_rule(findings, "O6")
    assert o6.detail["left"][0]["symbol"] == "b.py::pay"


def test_inheriting_a_class_counts_as_a_use():
    base = {"m.py": "class Base:\n    pass\n",
            "n.py": "from m import Base\n\n\nclass Child(Base):\n    pass\n"}
    removed = change("m.py::Base", kind="removed", reasons=("removed",))
    findings = run({"A": view("A", ["m.py"], [removed]), "B": view("B", ["n.py"])}, graphs={"B": graph_of(base)})
    [f] = by_rule(findings, "O4")
    assert f.detail["uses"][0]["symbol"] == "n.py::Child"


def test_a_session_never_hears_about_its_own_change():
    b_copy = dict(BASE, **{"c.py": "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"})
    views = {"A": view("A", ["a.py", "c.py"], [change("a.py::charge")])}
    findings = run(views, graphs={"A": graph_of(b_copy)}, base=graph_of(BASE))
    assert by_rule(findings, "O3") == [] and by_rule(findings, "O4") == []


def test_findings_are_sorted_and_keys_are_stable():
    b_copy = dict(BASE, **{"c.py": "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"})
    views = {"B": view("B", ["c.py", "a.py"]), "A": view("A", ["a.py"], [change("a.py::charge")])}
    first = run(views, graphs={"B": graph_of(b_copy)}, base=graph_of(BASE))
    again = run(dict(reversed(list(views.items()))), graphs={"B": graph_of(b_copy)}, base=graph_of(BASE))
    assert [f.key for f in first] == [f.key for f in again] == sorted(f.key for f in first)
    assert {f.rule for f in first} == {"O1", "O3", "O6"}
    assert "O3|a.py::charge|A|B|c.py" in {f.key for f in first}
