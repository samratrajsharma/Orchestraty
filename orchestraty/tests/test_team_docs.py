"""The README's "Teams: people and policy" section names every orch team action and flag, and every
key of the policy and people files, that the code has, and none that it lacks."""
import argparse
import re
from pathlib import Path

from orchestraty import cli
from orchestraty.team import miniyaml, people, policy

README = Path(__file__).resolve().parents[1] / "README.md"


def section():
    text = README.read_text(encoding="utf-8")
    start = text.index("## Teams: people and policy")
    end = text.find("\n## ", start + 3)
    return text[start:end if end > 0 else None]


def blocks(text, language):
    return re.findall(rf"```{language}\n(.*?)```", text, re.S)


def subparsers(parser):
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices


def test_every_orch_team_action_and_flag_is_named_both_ways():
    text = section()
    (usage,) = [b for b in blocks(text, "text") if b.startswith("orch team")]
    actions = subparsers(subparsers(cli.build_parser())["team"])
    named = {}
    for line in usage.splitlines():
        m = re.match(r"orch team(?: ([a-z]+))?", line)
        if m and m.group(1):
            named.setdefault(m.group(1), set()).update(re.findall(r"--[a-z][a-z-]*", line.split("  ")[0]))
    assert set(named) == set(actions)
    for action, parser in actions.items():
        flags = {o for a in parser._actions for o in a.option_strings if o.startswith("--")} - {"--help", "--repo"}
        assert named[action] == flags, action
    assert "--repo DIR" in text                                                   # every action takes it


def test_every_policy_and_people_key_is_named_both_ways():
    text = section()
    (example,) = [b for b in blocks(text, "yaml") if b.startswith("# .orchestraty/policy.yml")]
    doc = miniyaml.parse(example)
    assert set(doc) == set(policy.TOP_KEYS)
    for section_name, keys in (("share", policy.SHARE_KEYS), ("tickets", policy.TICKET_KEYS),
                               ("branches", policy.BRANCH_KEYS), ("deny", policy.DENY_KEYS),
                               ("people", policy.PEOPLE_KEYS)):
        assert set(doc[section_name]) == set(keys), section_name
    assert policy.parse(example).sync_on()                                         # the example is a policy
    (listed,) = [b for b in blocks(text, "yaml") if b.startswith("# .orchestraty/people.yml")]
    entries = miniyaml.parse(listed)["people"]
    assert set().union(*(set(e) for e in entries)) == set(people.ENTRY_KEYS)
    assert [p.handle for p in people.People.parse(listed)] == [e["handle"] for e in entries]
    for builtin in policy.BUILT_IN_PATHS:
        assert f"`{builtin}`" in text, builtin
