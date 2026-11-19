"""Orchestraty keeps parallel coding-agent sessions aware of each other.

The subpackages name the components: capture (the hook script, its installer, the git hook
chain), the coordinator, the live map, the overlap rules, delivery, the adapters for each
agent and the dashboard (ui).
"""

__version__ = "0.2.0.dev0"        # the one place the version is kept (pyproject.toml reads it)
