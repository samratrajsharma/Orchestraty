"""Orchestraty keeps parallel coding-agent sessions aware of each other.

The subpackages name the components: capture (the hook script, its installer, the git hook
chain), the coordinator, the live map, the overlap rules, delivery, the adapters for each
agent and the dashboard (ui).
"""

__version__ = "0.1.4"        # the one place the version is kept (pyproject.toml reads it)


def installed_version():
    """The version of the Orchestraty installed on disk now, read from this file: in a process
    that started before an upgrade it differs from __version__. None when it cannot be read."""
    import os
    import re
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py"), encoding="utf-8") as fh:
            found = re.search(r'^__version__ = "([^"]+)"', fh.read(), re.M)
    except OSError:
        return None
    return found.group(1) if found else None
