"""Puts this repository's engine and product package on the path for these tests. They need
Python 3.10 or later, as the engine does; below that only the session-log reader is tested."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

collect_ignore = []
if sys.version_info < (3, 10):
    collect_ignore = ["test_history_pairs.py", "test_history_cli.py"]
else:
    for sub in ("orchestraty", "engine"):
        path = os.path.join(REPO, sub)
        if path not in sys.path:
            sys.path.insert(0, path)
