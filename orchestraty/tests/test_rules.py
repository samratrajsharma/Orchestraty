"""The overlap rules, on session views and code graphs built from real (tiny) code."""
import pytest

from orch_engine.graph import build_graph
from orch_engine.parsing import parse_python
from orchestraty.livemap.map import SessionView, SymbolChange, analyse
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


TOOL = "class Tool:\n    def run(self, x):\n        return x\n"
ON_AN_INSTANCE = "from m import Tool\n\n\ndef use():\n    t = Tool()\n    return t.run(1)\n"
ON_THE_CLASS = "from m import Tool\n\n\ndef use():\n    t = Tool()\n    return Tool.run(t, 1)\n"


@pytest.mark.parametrize("made", ["    @staticmethod\n    def run(x):\n        return x\n",
                                  "    @classmethod\n    def run(cls, x):\n        return x\n"])
def test_a_method_made_static_or_class_reaches_only_the_calls_on_the_class(made):
    after = "class Tool:\n" + made
    change_ = changed(TOOL, after, path="m.py")
    base = {"m.py": TOOL, "k_instance.py": ON_AN_INSTANCE, "k_class.py": ON_THE_CLASS}
    findings = run({"A": view("A", ["m.py"], [change_]), "B": view("B")},
                   graphs={"B": graph_of(base)}, base=graph_of(base))
    assert [f.file for f in by_rule(findings, "O3", latent=True)] == ["k_class.py"]
    [o6] = by_rule(findings, "O6")
    assert [u["file"] for u in o6.detail["left"]] == ["k_class.py"]
    assert o6.detail["reasons"][0].startswith("now a ")


def test_a_staticmethod_made_a_plain_method_reaches_only_the_calls_on_the_class():
    static = "class Tool:\n    @staticmethod\n    def run(x):\n        return x\n"
    base = {"m.py": static, "k_instance.py": ON_AN_INSTANCE,
            "k_class.py": "from m import Tool\n\n\ndef use():\n    return Tool.run(1)\n"}
    change_ = changed(static, TOOL, path="m.py")
    [o6] = by_rule(run({"A": view("A", ["m.py"], [change_])}, base=graph_of(base)), "O6")
    assert [u["file"] for u in o6.detail["left"]] == ["k_class.py"]


KEYWORD_ONLY = "def charge(amount, *, currency):\n    return amount\n"
TOUCHED = "from a import charge\n\n\ndef pay():\n    z = 3\n    return charge(1, 'EUR')\n"
FIXED = "from a import charge\n\n\ndef pay():\n    z = 3\n    return charge(1, currency='EUR')\n"
TOUCH = change("b.py::pay", contract="none", reasons=())


def test_o6_a_caller_a_session_changed_but_left_on_the_old_form_is_still_left_behind():
    views = {"A": view("A", ["a.py"], [changed(BASE["a.py"], KEYWORD_ONLY)]), "B": view("B", ["b.py"], [TOUCH])}
    graphs = {"B": graph_of(dict(BASE, **{"b.py": TOUCHED}))}
    [o6] = by_rule(run(views, graphs=graphs, base=graph_of(BASE)), "O6")
    assert o6.detail["left"] == [{"symbol": "b.py::pay", "file": "b.py", "lines": [5]}]
    graphs["B"] = graph_of(dict(BASE, **{"b.py": FIXED}))                 # B fixed the call: it is updated
    assert by_rule(run(views, graphs=graphs, base=graph_of(BASE)), "O6") == []
    views["C"] = view("C", ["b.py"], [TOUCH])
    graphs["C"] = graph_of(dict(BASE, **{"b.py": TOUCHED}))                # one session's fix is enough
    assert by_rule(run(views, graphs=graphs, base=graph_of(BASE)), "O6") == []
    del views["C"]
    assert by_rule(run(views, graphs={}, base=graph_of(BASE)), "O6") == []  # B's copy not known yet: its change counts


def test_o6_the_changing_sessions_own_callers_are_judged_in_its_own_copy():
    views = {"A": view("A", ["a.py", "b.py"], [changed(BASE["a.py"], KEYWORD_ONLY), TOUCH])}
    [o6] = by_rule(run(views, graphs={"A": graph_of({"a.py": KEYWORD_ONLY, "b.py": TOUCHED})},
                       base=graph_of(BASE)), "O6")
    assert [u["symbol"] for u in o6.detail["left"]] == ["b.py::pay"]
    assert by_rule(run(views, graphs={"A": graph_of({"a.py": KEYWORD_ONLY, "b.py": FIXED})}, base=graph_of(BASE)),
                   "O6") == []


def test_o6_a_call_left_to_a_removed_function_is_never_updated():
    removed = changed(BASE["a.py"], "")
    views = {"A": view("A", ["a.py"], [removed]), "B": view("B", ["b.py"], [TOUCH])}
    graphs = {"B": graph_of(dict(BASE, **{"b.py": TOUCHED}))}
    [o6] = by_rule(run(views, graphs=graphs, base=graph_of(BASE)), "O6")
    assert o6.detail["left"][0]["symbol"] == "b.py::pay"
    made_async = changed(BASE["a.py"], "async def charge(amount, currency):\n    return amount\n")
    assert made_async.breaks == {"now async": [["any"]]}
    views["A"] = view("A", ["a.py"], [made_async])                       # every call breaks: arguments show nothing,
    assert by_rule(run(views, graphs=graphs, base=graph_of(BASE)), "O6") == []   # so B's change of pay counts


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


def changed(before, after, path="a.py"):
    """The one change an edit of a file makes, as the live map classifies it."""
    [c] = analyse({path: parse_python(path, before)}, {path: parse_python(path, after)}).values()
    return c


RENAMED = "def charge(amount, cur):\n    return amount\n"
BY_POSITION = "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"
BY_NAME = "from a import charge\n\n\ndef named():\n    return charge(2, currency='USD')\n"


def test_a_rename_with_only_positional_callers_gives_no_contract_finding():
    rename = changed(BASE["a.py"], RENAMED)
    assert rename.contract == "breaking" and rename.reasons == ["parameter currency renamed to cur"]
    copy = dict(BASE, **{"c.py": BY_POSITION})
    findings = run({"A": view("A", ["a.py"], [rename]), "B": view("B", ["c.py"])},
                   graphs={"B": graph_of(copy)}, base=graph_of(copy))
    assert [f for f in findings if f.rule in ("O3", "O4", "O6")] == []


def test_a_rename_reaches_exactly_the_callers_that_pass_it_by_name():
    copy = dict(BASE, **{"c.py": BY_POSITION, "d.py": BY_NAME})
    views = {"A": view("A", ["a.py"], [changed(BASE["a.py"], RENAMED)]), "B": view("B", ["c.py", "d.py"])}
    findings = run(views, graphs={"B": graph_of(copy)}, base=graph_of(copy))
    [o3] = [f for f in findings if f.rule == "O3"]
    assert (o3.file, o3.receiver, o3.detail["latent"]) == ("d.py", "B", False)
    assert o3.detail["uses"] == [{"symbol": "d.py::named", "lines": [5]}]
    assert o3.detail["reasons"] == ["parameter currency renamed to cur"]
    [o6] = by_rule(findings, "O6")
    assert o6.detail["left"] == [{"symbol": "d.py::named", "file": "d.py", "lines": [5]}]


def test_a_parameter_removed_from_the_end_breaks_the_calls_that_pass_it():
    base = {"a.py": "def charge(amount, currency='EUR'):\n    return amount\n",
            "b.py": "from a import charge\n\n\ndef short():\n    return charge(1)\n\n\n"
                    "def full():\n    return charge(1, 'USD')\n\n\ndef named():\n    return charge(1, currency='USD')\n"}
    removed = changed(base["a.py"], "def charge(amount):\n    return amount\n")
    [o6] = run({"A": view("A", ["a.py"], [removed])}, base=graph_of(base))
    assert [c["symbol"] for c in o6.detail["left"]] == ["b.py::full", "b.py::named"]


def test_a_call_that_unpacks_its_arguments_still_counts():
    base = dict(BASE, **{"b.py": "from a import charge\n\n\ndef pay(opts):\n    return charge(1, **opts)\n"})
    [o6] = run({"A": view("A", ["a.py"], [changed(BASE["a.py"], RENAMED)])}, base=graph_of(base))
    assert o6.detail["left"][0]["symbol"] == "b.py::pay"
    # and **kwargs taken away breaks a call passing a keyword only it took, not a plain call
    base = {"a.py": "def charge(amount, **extra):\n    return amount\n",
            "b.py": "from a import charge\n\n\ndef plain():\n    return charge(1)\n\n\n"
                    "def tagged():\n    return charge(1, tag='x')\n"}
    [o6] = run({"A": view("A", ["a.py"], [changed(base["a.py"], "def charge(amount):\n    return amount\n")])},
               base=graph_of(base))
    assert [c["symbol"] for c in o6.detail["left"]] == ["b.py::tagged"]


def test_a_finding_names_the_reasons_its_uses_run_into():
    base = {"a.py": "def charge(amount, currency, *rest) -> int:\n    return amount\n",
            "b.py": "from a import charge\n\n\ndef pay():\n    return charge(1, currency='EUR')\n"}
    both = changed(base["a.py"], "def charge(amount, cur) -> str:\n    return amount\n")
    assert both.reasons == ["parameter currency renamed to cur", "*args removed", "return type changed"]
    [o6] = run({"A": view("A", ["a.py"], [both])}, base=graph_of(base))
    assert o6.detail["reasons"] == ["parameter currency renamed to cur", "return type changed"]


def test_a_removed_function_and_an_inherited_class_break_every_use():
    removed = changed(BASE["a.py"], "")
    assert removed.kind == "removed" and removed.breaks == {"removed": [["any"]]}
    [o6] = run({"A": view("A", ["a.py"], [removed])}, base=graph_of(BASE))
    assert o6.detail["left"][0]["symbol"] == "b.py::pay"
    base = {"m.py": "class Base(Mixin):\n    pass\n",
            "n.py": "from m import Base\n\n\nclass Child(Base):\n    pass\n"}
    broken = changed(base["m.py"], "class Base:\n    pass\n", path="m.py")
    [o6] = run({"A": view("A", ["m.py"], [broken])}, base=graph_of(base))
    assert o6.detail["left"][0]["symbol"] == "n.py::Child"


# --------------------------------------------------------------------------------------- #
# W1: two sessions in one folder
# --------------------------------------------------------------------------------------- #
def in_folder(sid, files=(), symbols=(), folder="/w/repo"):
    v = view(sid, files, symbols)
    v.worktree = folder
    return v


def keys_of(views, hooked, graphs=None, base=None):
    graphs = graphs or {}
    return {f.key for f in evaluate(views, lambda sid: graphs.get(sid), lambda sid: base, hooked=hooked)}


def test_w1_two_sessions_with_hooks_in_one_folder():
    views = {"A": in_folder("A"), "B": in_folder("B")}
    [f] = evaluate(views, lambda sid: None, lambda sid: None, hooked={"A", "B"})
    assert (f.key, f.rule, f.strength, f.sessions) == ("W1|/w/repo|A|B", "W1", "medium", ("A", "B"))
    assert (f.symbol, f.file, f.detail) == ("", "", {"worktree": "/w/repo"})
    views["C"] = in_folder("C")                                       # a third: each new pair its own
    assert keys_of(views, {"A", "B", "C"}) == {"W1|/w/repo|A|B", "W1|/w/repo|A|C", "W1|/w/repo|B|C"}


def test_no_w1_for_two_worktrees_a_plain_git_session_or_unknown_hooks():
    assert keys_of({"A": view("A"), "B": view("B")}, {"A", "B"}) == set()
    assert keys_of({"A": in_folder("A"), "git-1": in_folder("git-1")}, {"A"}) == set()
    assert keys_of({"A": in_folder("A"), "B": in_folder("B")}, None) == set()


def test_in_one_folder_o1_stays_and_the_function_rules_are_off():
    b_copy = dict(BASE, **{"c.py": "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"})
    body = change("a.py::charge", contract="none", reasons=())
    views = {"A": in_folder("A", ["a.py", "x.py"], [change("a.py::charge")]),
             "B": in_folder("B", ["a.py", "c.py", "x.py"], [body])}
    graphs = {"A": graph_of(b_copy), "B": graph_of(b_copy)}
    found = evaluate(views, lambda sid: graphs.get(sid), lambda sid: graph_of(BASE), hooked={"A", "B"})
    rules = {f.rule for f in found}
    assert {"O1", "W1", "O6"} <= rules                                # O6 is one session's own: it stays
    assert not rules & {"O2", "O3", "O4"}
    # A session in a worktree of its own still hears about the change.
    views["C"] = view("C", ["c.py"])
    graphs["C"] = graph_of(b_copy)
    found = evaluate(views, lambda sid: graphs.get(sid), lambda sid: graph_of(BASE), hooked={"A", "B", "C"})
    assert "O3|a.py::charge|A|C|c.py" in {f.key for f in found}
    assert not [f for f in found if f.rule in ("O2", "O3", "O4") and set(f.sessions) == {"A", "B"}]
