"""Writes the agents table in README.md from docs/agents.json, so the table never claims more
than the tests showed:

* an agent is **Supported** only when its capture test with the real agent passed (``pass``),
  and the table says on what, with which version and when;
* an agent with an adapter whose capture test is ``pending`` or ``not run`` is built, and not
  yet tested with the real agent;
* agents without hooks are followed through git alone: basic support (git only).

    python scripts/agents_table.py            # write the table into README.md
    python scripts/agents_table.py --check    # change nothing; exit 1 when the README differs

Run it after changing docs/agents.json; a test runs --check. The table sits between two marker
comments in README.md, and nothing else in the file is touched. Standard library only.
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "agents.json")
README = os.path.join(ROOT, "README.md")
START = "<!-- agents table: generated from docs/agents.json by scripts/agents_table.py; do not edit by hand -->"
END = "<!-- end of agents table -->"
RESULTS = ("pass", "pending", "not run")
CONNECTS = {"hooks": "Hooks (level 2)", "git": "Plain git (level 0)"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
SUPPORTED = "Supported"
BUILT = "Built; not yet tested with the real agent"
GIT_ONLY = "Basic support (git only)"


def load(path=DATA):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _date(iso):
    day = datetime.date.fromisoformat(iso)
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def status(entry):
    """The status the table gives an agent: supported only when its capture test passed."""
    if entry["connects"] == "git":
        return GIT_ONLY
    return SUPPORTED if entry["result"] == "pass" else BUILT


def tested(entry):
    """What the table says of the agent's capture test."""
    if entry["connects"] == "git":
        return "None needed: its changes are read from git"
    if entry["result"] == "pass":
        return (f"Passed with {entry['agent']} {entry['version_tested']} on {entry['platform']}, "
                f"{_date(entry['test_date'])}")
    return "Pending" if entry["result"] == "pending" else "Not run"


def check_data(data):
    """The problems in agents.json, as sentences; an empty list when there are none."""
    problems = []
    for entry in data.get("agents", []):
        name = entry.get("agent", "?")
        if entry.get("connects") not in CONNECTS:
            problems.append(f"{name}: connects is {entry.get('connects')!r}, not one of {sorted(CONNECTS)}")
        if entry.get("result") not in RESULTS:
            problems.append(f"{name}: result is {entry.get('result')!r}, not one of {list(RESULTS)}")
        if entry.get("result") == "pass":
            for key in ("version_tested", "platform", "test_date"):
                if not entry.get(key):
                    problems.append(f"{name}: a capture test that passed needs its {key}")
            try:
                _date(entry.get("test_date") or "")
            except ValueError:
                problems.append(f"{name}: test_date is not a date (YYYY-MM-DD)")
    return problems


def table(data):
    """The table, as Markdown lines ending in a newline."""
    lines = ["| Agent | Connects through | Status | Capture test with the real agent |",
             "| --- | --- | --- | --- |"]
    for entry in data["agents"]:
        lines.append(f"| {entry['agent']} | {CONNECTS[entry['connects']]} | {status(entry)} | {tested(entry)} |")
    return "\n".join(lines) + "\n"


def rendered(readme_text, data):
    """The README with the table between the markers replaced by the one the data gives."""
    start = readme_text.index(START) + len(START)
    end = readme_text.index(END, start)
    return readme_text[:start] + "\n" + table(data) + readme_text[end:]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the agents table in README.md from docs/agents.json.")
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 when the README differs")
    args = parser.parse_args(argv)
    data = load()
    problems = check_data(data)
    if problems:
        print("docs/agents.json: " + "; ".join(problems), file=sys.stderr)
        return 1
    with open(README, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    try:
        wanted = rendered(text, data)
    except ValueError:
        print("README.md has no agents table markers", file=sys.stderr)
        return 1
    if wanted == text:
        return 0
    if args.check:
        print("README.md's agents table differs from docs/agents.json: run python scripts/agents_table.py",
              file=sys.stderr)
        return 1
    with open(README, "w", encoding="utf-8", newline="") as fh:
        fh.write(wanted)
    print("Wrote the agents table in README.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
