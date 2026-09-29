"""Test-file patterns, and which references to a function come from tests.

Status: throwaway (Phase 0). Not part of the product.

    test_file_pattern(path) -> the name of the pattern that matches, or None
    test_references(change, name_refs) -> resolved calls from tests, and name-only uses in tests

A path is a test file when any of these match (paths are repo-relative, with / or \\):

  test_*.py       pytest's default               tests/test_api.py
  *_test.py       pytest's other default         api_test.py
  conftest.py     pytest fixtures                tests/conftest.py
  tests.py        Django's per-app test module   app/tests.py
  *.test.<js>     Jest and Vitest                src/api.test.ts
  *.spec.<js>     Jest, Vitest and Jasmine       src/api.spec.tsx
  __tests__/      Jest's folder                  src/__tests__/api.ts
  tests/, test/   a test folder anywhere         tests/helpers.py

where <js> is ts, tsx, js, jsx, mjs, cjs, mts or cts. Matching is by path only; the file is
never read. "Tested" comes only from references found in test files: resolved call sites
(high confidence), or name-only references (low confidence). A name in a comment never
counts, because the name-only search reads Python through the tokenizer. Python 3.9 or later.
"""
import re

from effects_record import FunctionChange

JS = r"(ts|tsx|js|jsx|mjs|cjs|mts|cts)"
FILE_PATTERNS = (
    ("test_*.py", re.compile(r"^test_.*\.py$")),
    ("*_test.py", re.compile(r"^.+_test\.py$")),
    ("conftest.py", re.compile(r"^conftest\.py$")),
    ("tests.py", re.compile(r"^tests\.py$")),
    ("*.test.<js>", re.compile(r"^.+\.test\." + JS + r"$")),
    ("*.spec.<js>", re.compile(r"^.+\.spec\." + JS + r"$")),
)
FOLDER_PATTERNS = (("__tests__/", "__tests__"), ("tests/", "tests"), ("test/", "test"))


def test_file_pattern(path):
    """The name of the first pattern that makes `path` a test file, or None."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    if not parts:
        return None
    for label, pattern in FILE_PATTERNS:
        if pattern.match(parts[-1]):
            return label
    for label, folder in FOLDER_PATTERNS:
        if folder in parts[:-1]:
            return label
    return None


def is_test_file(path):
    return test_file_pattern(path) is not None


def test_references(change: FunctionChange, name_refs=()):
    """{"resolved": call sites in test files (high confidence),
        "name_only": name-only references in test files (low confidence)}."""
    return {"resolved": [c for c in change.call_sites if c.resolved and is_test_file(c.file)],
            "name_only": [r for r in name_refs if is_test_file(r.file)]}


# Not tests themselves: keep pytest from collecting the helpers above as test functions.
test_file_pattern.__test__ = False
test_references.__test__ = False
