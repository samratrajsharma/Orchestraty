"""The agents table in the README is generated from docs/agents.json, and says "Supported" only
where the agent's capture test with the real agent passed."""
import importlib.util
import json
import re
from pathlib import Path

from orchestraty import adapters

ROOT = Path(__file__).resolve().parents[2]


def generator():
    spec = importlib.util.spec_from_file_location("agents_table", ROOT / "scripts" / "agents_table.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(text, header):
    """{first cell: cells} of the Markdown table that starts with `header`."""
    lines = text[text.index(header):].splitlines()[2:]
    out = {}
    for line in lines:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        out[cells[0]] = cells
    return out


def test_the_readme_table_is_the_one_the_data_gives():
    at = generator()
    data = at.load()
    assert at.check_data(data) == []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert at.rendered(readme, data) == readme, "run python scripts/agents_table.py"
    assert at.main(["--check"]) == 0


def test_supported_only_where_the_capture_test_passed():
    at = generator()
    data = at.load()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    table = rows(readme, "| Agent | Connects through |")
    assert set(table) == {e["agent"] for e in data["agents"]}
    for entry in data["agents"]:
        state = table[entry["agent"]][2]
        assert (state == "Supported") == (entry["result"] == "pass" and entry["connects"] == "hooks"), entry["agent"]
        if entry["connects"] == "git":
            assert state == "Basic support (git only)"
        elif entry["result"] != "pass":
            assert state == "Built; not yet tested with the real agent"
    block = readme[readme.index(at.START):readme.index(at.END)]
    assert block.count("| Supported |") == sum(1 for e in data["agents"] if e["result"] == "pass")


def test_every_adapter_is_listed_under_its_own_name():
    data = json.loads((ROOT / "docs" / "agents.json").read_text(encoding="utf-8"))
    listed = {e["adapter"]: e for e in data["agents"] if e["adapter"]}
    assert set(listed) == set(adapters.AGENTS)
    for name, entry in listed.items():
        assert entry["agent"] == adapters.AGENTS[name].TITLE and entry["connects"] == "hooks"
    assert [e["connects"] for e in data["agents"] if not e["adapter"]] == ["git"]


def test_the_package_readme_says_the_same():
    data = json.loads((ROOT / "docs" / "agents.json").read_text(encoding="utf-8"))
    package = rows((ROOT / "orchestraty" / "README.md").read_text(encoding="utf-8"), "| Agent | Hooks file")
    for entry in data["agents"]:
        if entry["connects"] == "hooks":
            want = "Capture test passed" if entry["result"] == "pass" else "Built; not yet tested with the real agent"
            assert package[entry["agent"]][-1] == want, entry["agent"]


def test_the_rules_of_the_table():
    at = generator()
    data = {"agents": [
        {"agent": "A", "adapter": "a", "connects": "hooks", "result": "pass", "version_tested": "1.2.3",
         "platform": "macOS", "test_date": "2026-01-02"},
        {"agent": "B", "adapter": "b", "connects": "hooks", "result": "pending", "version_tested": None,
         "platform": None, "test_date": None},
        {"agent": "C", "adapter": "c", "connects": "hooks", "result": "not run", "version_tested": None,
         "platform": None, "test_date": None},
        {"agent": "D", "adapter": None, "connects": "git", "result": "not run", "version_tested": None,
         "platform": None, "test_date": None}]}
    assert at.check_data(data) == []
    assert at.table(data).splitlines()[2:] == [
        "| A | Hooks (level 2) | Supported | Passed with A 1.2.3 on macOS, 2 January 2026 |",
        "| B | Hooks (level 2) | Built; not yet tested with the real agent | Pending |",
        "| C | Hooks (level 2) | Built; not yet tested with the real agent | Not run |",
        "| D | Plain git (level 0) | Basic support (git only) | None needed: its changes are read from git |"]
    data["agents"][0]["version_tested"] = None                    # a pass needs its evidence
    data["agents"][1]["result"] = "passed"
    problems = at.check_data(data)
    assert any(re.search(r"A: a capture test that passed needs its version_tested", p) for p in problems)
    assert any(p.startswith("B: result is 'passed'") for p in problems)
