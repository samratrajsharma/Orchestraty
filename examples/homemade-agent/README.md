# A homemade agent

`agent.py` is an agent with no model that uses Orchestraty's two-call helper (`orchestraty.helper`). It plays two sessions in two worktrees of a small repository it makes, so the notes they get can be read:

```bash
orch init                                   # once: installs the hook script the helper calls
python examples/homemade-agent/agent.py /tmp/orch-demo
```

The folder must be new or empty. The agent watches the repository it makes there while it runs, and stops watching it at the end. Session A makes `charge`'s `currency` keyword-only. Session B is about to edit `pay()`, which still passes `currency` by position: before B writes `b.py`, `before_write` hands it the note about A's change. A then hears at the end of its turn, from `end_turn`, that `pay()` is left behind, and B passes `currency` by name.

```text
A, before writing a.py, hears nothing.
A, after making currency keyword-only in a.py, hears nothing.
B, before writing b.py, hears:
    Orchestraty: another helper-homemade session (worktree wt-a, branch feat-a) changed the contract of charge in a.py: parameter currency can no longer be passed by position. ...
A, at the end of its turn, hears:
    Orchestraty: you changed the contract of charge in a.py (parameter currency can no longer be passed by position). These callers still use it and no session has updated them: pay in b.py (line 5). ...
B, after passing currency by name in b.py, hears nothing.
```

`orchestraty/tests/test_homemade_agent.py` runs it with the installed hook script, a coordinator the hook starts, and a state folder of its own.
