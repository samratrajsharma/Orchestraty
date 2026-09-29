"""Tests for the test-file patterns: every form in D-tests, and the paths that must not match."""
import pytest

from effects_names import name_only_references
from effects_record import CallSite, FunctionChange, signature_from_source as sig
from effects_testfiles import FILE_PATTERNS, FOLDER_PATTERNS, is_test_file, test_file_pattern, test_references

TEST_FILES = [
    ("tests/test_api.py", "test_*.py"), ("test_api.py", "test_*.py"), ("pkg/api_test.py", "*_test.py"),
    ("conftest.py", "conftest.py"), ("tests/conftest.py", "conftest.py"), ("app/tests.py", "tests.py"),
    ("src/api.test.ts", "*.test.<js>"), ("src/api.test.js", "*.test.<js>"), ("src/Button.test.tsx", "*.test.<js>"),
    ("src/api.spec.ts", "*.spec.<js>"), ("src/api.spec.jsx", "*.spec.<js>"), ("lib/x.spec.mjs", "*.spec.<js>"),
    ("src/__tests__/api.ts", "__tests__/"), ("src/__tests__/util.js", "__tests__/"),
    ("tests/helpers.py", "tests/"), ("test/helper.js", "test/"), ("tests\\test_api.py", "test_*.py"),
    ("pkg\\tests\\fixtures.py", "tests/"),
]
NOT_TEST_FILES = ["attestation.py", "pkg/contest.py", "latest.py", "testing_utils.py", "src/api.ts",
                  "protest/x.py", "src/tests.ts", "tests.pyc", "docs/testing.md", "src/testify.spec", "tests", ""]


@pytest.mark.parametrize("path,pattern", TEST_FILES)
def test_every_test_file_form_matches(path, pattern):
    assert test_file_pattern(path) == pattern and is_test_file(path)


@pytest.mark.parametrize("path", NOT_TEST_FILES)
def test_other_files_do_not_match(path):
    assert test_file_pattern(path) is None


def test_every_pattern_is_exercised():
    labels = {label for label, _ in FILE_PATTERNS} | {label for label, _ in FOLDER_PATTERNS}
    assert labels == {pattern for _, pattern in TEST_FILES}


def test_tested_comes_from_references_in_test_files_never_from_comments():
    files = {"tests/test_mod.py": "from mod import compute\n\n\ndef test_it():\n    # compute() is mentioned here\n"
                                  "    assert compute(1)\n",
             "tests/test_other.py": "# compute\nvalue = 'compute'\n",
             "mod_user.py": "from mod import compute\nx = compute(2)\n"}
    change = FunctionChange(id="mod.py::compute", name="compute", before=sig("def compute(a): pass"),
                            after=sig("def compute(a): pass"),
                            call_sites=(CallSite("tests/test_mod.py", 6, positional=1, resolved=True),
                                        CallSite("mod_user.py", 2, positional=1, resolved=True)))
    found = test_references(change, name_only_references(change, files))
    assert [(c.file, c.line) for c in found["resolved"]] == [("tests/test_mod.py", 6)]
    assert [(r.file, r.line, r.kind) for r in found["name_only"]] == [("tests/test_mod.py", 1, "import")]
