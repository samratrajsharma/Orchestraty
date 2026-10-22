"""Agent adapters: for each agent Orchestraty connects to, its hook events mapped to the common
ones (Claude Code's names, which the rest of Orchestraty uses), what the hook script reads from
their payloads, its edit tools, how a note is handed back at each event (the exact output that
agent accepts), and what it cannot be told.

The hook script (``capture/hook.py``) imports nothing of ours, so it keeps its own copy of what
it reads and prints; a test checks it against the adapters. An agent with no adapter of its
own (one built with the two-call helper, say) is taken to have Claude Code's shape, the one most
agents copy.
"""
from orchestraty.adapters import antigravity, claude, codex

AGENTS = {m.NAME: m for m in (claude, codex, antigravity)}
READ_ONLY_TOOLS = frozenset(t for m in AGENTS.values() for t in getattr(m, "READ_ONLY_TOOLS", ()))


def get(agent):
    """The adapter of an agent: Claude Code's for one with none of its own."""
    return AGENTS.get(agent, claude)


def hears(agent):
    """The kinds of note a session of this agent can be handed: of edit, next, stop and wake."""
    return get(agent).HEARS


def cannot(agent):
    """{kind of note: why this agent cannot be handed it}, for the kinds it cannot take."""
    return get(agent).CANNOT


def output(agent, kind, text, steer=False):
    """The bytes the hook prints for a note of this kind, in the form the agent accepts."""
    return get(agent).output(kind, text, steer)


def title(agent):
    """The agent's own name, as people know it ("Claude Code"), or the short one we were given."""
    module = AGENTS.get(agent)
    return module.TITLE if module else agent
