"""The overlap rules across people: a teammate's branch (a session of agent team, read from its
commits, or from their record for unpushed work) against the sessions on this machine, and never
two teammates' branches with each other. With no teammates the rules are exactly Phase 1's."""
from orchestraty.rules.overlap import evaluate
from test_rules import BASE, by_rule, change, graph_of, view

USES = dict(BASE, **{"c.py": "from a import charge\n\n\ndef use():\n    return charge(2, 'USD')\n"})
T = "team-3fa94c2b1d0e"


def team_view(sid, files=(), symbols=()):
    v = view(sid, files, symbols)
    v.worktree = ""                                     # no working copy on this disk
    return v


def run(views, graphs=None, base=None, planned=None, hooked=None, **team):
    graphs = graphs or {}
    return evaluate(views, lambda sid: graphs.get(sid), lambda sid: base, hooked=hooked, planned=planned, **team)


def commits(sid):
    return "commits"


def test_two_team_views_overlapping_give_no_finding():
    other = "team-0b1d77c2e4aa"
    views = {T: team_view(T, ["a.py"], [change("a.py::charge")]),
             other: team_view(other, ["a.py", "c.py"], [change("a.py::charge")])}
    graphs = {T: graph_of(USES), other: graph_of(USES)}
    assert run(views, graphs, base=graph_of(BASE), others=frozenset(views), evidence=commits) == []


def test_o2_between_a_local_and_a_team_session_is_medium_with_the_phase_1_key():
    views = {"B1": view("B1", ["a.py"], [change("a.py::charge", contract="none", reasons=())]),
             T: team_view(T, ["a.py"], [change("a.py::charge")])}
    [f] = by_rule(run(views, others=frozenset({T}), evidence=commits), "O2")
    assert (f.key, f.strength, f.sessions) == (f"O2|a.py::charge|B1|{T}", "medium", ("B1", T))


def test_o3_from_a_commits_changer_is_strong_and_from_a_record_changer_medium():
    views = {T: team_view(T, ["a.py"], [change("a.py::charge")]), "B1": view("B1", ["c.py"])}
    graphs = {"B1": graph_of(USES)}
    for evidence, strength in (("commits", "strong"), ("record", "medium")):
        found = run(views, graphs, others=frozenset({T}), evidence=lambda sid, e=evidence: e)
        [f] = by_rule(found, "O3")
        assert (f.key, f.changer, f.receiver) == (f"O3|a.py::charge|{T}|B1|c.py", T, "B1")
        assert (f.strength, f.detail["evidence"]) == (strength, evidence)
        [latent] = by_rule(found, "O3", latent=True)                              # B1 is told before b.py too
        assert (latent.receiver, latent.file, latent.strength) == ("B1", "b.py", strength)


def test_no_latent_finding_has_a_team_receiver():
    views = {"B1": view("B1", ["a.py"], [change("a.py::charge")]), T: team_view(T, ["c.py"])}
    found = run(views, {T: graph_of(USES)}, others=frozenset({T}), evidence=commits)
    o3 = [(f.changer, f.receiver, f.file, f.detail["latent"]) for f in found if f.rule == "O3"]
    assert o3 == [("B1", T, "c.py", False)]                                       # b.py: nobody here to tell
    assert "evidence" not in by_rule(found, "O3")[0].detail                       # a local changer: as in Phase 1


def test_no_o6_for_a_team_changer():
    views = {T: team_view(T, ["a.py"], [change("a.py::charge")])}
    assert by_rule(run(views, base=graph_of(BASE)), "O6") != []                   # as a local session would
    assert by_rule(run(views, base=graph_of(BASE), others=frozenset({T}), evidence=commits), "O6") == []


def test_a_team_view_that_modified_a_caller_counts_as_updated_for_the_local_changers_o6():
    views = {"A1": view("A1", ["a.py"], [change("a.py::charge")])}
    [left] = by_rule(run(views, base=graph_of(BASE)), "O6")
    assert left.detail["left"] == [{"symbol": "b.py::pay", "file": "b.py", "lines": [5]}]
    views[T] = team_view(T, ["b.py"], [change("b.py::pay", contract="none", reasons=())])
    assert by_rule(run(views, base=graph_of(BASE), others=frozenset({T}), evidence=commits), "O6") == []


def test_apart_suppresses_only_that_pair():
    symbols = [change("a.py::charge", contract="none", reasons=())]
    views = {"A1": view("A1", ["a.py"], symbols), "B1": view("B1", ["a.py"], symbols), T: team_view(T, ["a.py"], symbols)}
    found = run(views, others=frozenset({T}), evidence=commits, apart=lambda a, b: {a, b} == {"A1", T})
    assert sorted(f.key for f in by_rule(found, "O2")) == ["O2|a.py::charge|A1|B1", f"O2|a.py::charge|B1|{T}"]
    assert sorted(f.key for f in by_rule(found, "O1")) == ["O1|a.py|A1|B1", f"O1|a.py|B1|{T}"]


def test_no_w1_with_an_empty_worktree():
    local, team = view("A1", ["x.py"]), team_view(T, ["x.py"])
    local.worktree = ""                                                           # nor between two empty ones
    found = run({"A1": local, T: team}, hooked={"A1", T}, others=frozenset({T}), evidence=commits)
    assert [f.rule for f in found] == ["O1"]


def test_o7_between_a_local_declaration_and_a_team_claim():
    other = "team-0b1d77c2e4aa"
    views = {"A1": view("A1"), T: team_view(T), other: team_view(other)}
    planned = {"A1": [{"path": "billing/charge.py", "symbol": "", "how": "declared"}],
               T: [{"path": "billing", "symbol": "", "how": "claimed"}],
               other: [{"path": "billing", "symbol": "", "how": "claimed"}]}
    found = run(views, planned=planned, others=frozenset({T, other}), evidence=commits)
    assert sorted((f.key, f.strength, f.detail["how"]) for f in found) == sorted([
        (f"O7|billing/charge.py|A1|{T}", "medium", {"A1": "declared", T: "claimed"}),
        (f"O7|billing/charge.py|A1|{other}", "medium", {"A1": "declared", other: "claimed"})])  # never T with T


def phase_1_scenes():
    """The Phase 1 rule fixtures' scenes: (views, graphs, base)."""
    body = change("a.py::charge", contract="none", reasons=())
    removed = change("a.py::charge", kind="removed", reasons=("removed",))
    yield {"A": view("A", ["x.py", "y.py"]), "B": view("B", ["x.py"])}, {}, None
    yield {"A": view("A", ["a.py"], [body]), "B": view("B", ["a.py"], [change("a.py::charge")])}, {}, None
    yield ({"A": view("A", ["a.py"], [change("a.py::charge")]), "B": view("B", ["c.py"])}, {"B": graph_of(USES)},
           graph_of(BASE))
    yield {"A": view("A", ["a.py"], [removed]), "B": view("B", ["c.py"])}, {"B": graph_of(USES)}, graph_of(BASE)
    yield ({"A": view("A", ["a.py"], [change("a.py::charge")]),
            "B": view("B", ["b.py"], [change("b.py::pay", contract="none", reasons=())])}, {}, graph_of(BASE))


def test_parity_with_no_teammates():
    seen = []
    for views, graphs, base in phase_1_scenes():
        today = run(views, graphs, base)
        assert today == run(views, graphs, base, others=frozenset(), apart=None, evidence=None)
        seen += today
    assert {f.rule for f in seen} == {"O1", "O2", "O3", "O4", "O6"}
