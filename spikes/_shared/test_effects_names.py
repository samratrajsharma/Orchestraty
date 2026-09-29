"""Tests for name-only references, on a synthetic repository written to a temp folder.

The repository holds the callers the resolver can't link yet: decorator uses and callbacks
(D-E6), module-level callers (D-E7), and an inherited method reached through a grandparent
and super() (D-E9/E15). They must show up here, labelled name-only and low confidence."""
import pytest

from effects_names import name_only_references
from effects_record import CallSite, FunctionChange, signature_from_source as sig

REPO = {
    "lib.py": ("def register(fn):\n    return fn\n\n\ndef worker():\n    return 1\n\n\n"
               "def main():\n    return 0\n\n\ndef create_app():\n    return object()\n"),
    "jobs.py": ("import threading\nfrom lib import register, worker\n\n\n@register\ndef job():\n"
                "    return worker()\n\n\nt = threading.Thread(target=worker)\ncallbacks = [worker]\n"
                "# worker() in a comment is not a reference\ntext = \"worker() in a string is not one either\"\n"),
    "app.py": ("from lib import main, create_app\n\napp = create_app()\n\nif __name__ == \"__main__\":\n"
               "    main()\n"),
    "models.py": ("class Base:\n    def save(self):\n        return 1\n\n\nclass Middle(Base):\n    pass\n\n\n"
                  "class Child(Middle):\n    def store(self):\n        return self.save()\n\n"
                  "    def save(self):\n        return super().save()\n"),
    "plugins.py": "import lib\n\n\n@lib.register\ndef plugin():\n    return 2\n",
    "web/app.ts": "import { worker } from './lib';\nexport const workers = [worker];\nworker();\n",
    "broken.py": "def oops(:\n    worker(\n",
}


@pytest.fixture
def repo(tmp_path):
    """Write the synthetic repository to disk and read it back, as a caller would."""
    for rel, text in REPO.items():
        path = tmp_path.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return {p.relative_to(tmp_path).as_posix(): p.read_text(encoding="utf-8")
            for p in tmp_path.rglob("*") if p.is_file()}


def refs(files, name, resolved=()):
    change = FunctionChange(id=f"lib.py::{name}", name=name, file="lib.py", before=sig(f"def {name}(): pass"),
                            after=sig(f"def {name}(x): pass"),
                            call_sites=tuple(CallSite(f, line, resolved=True) for f, line in resolved))
    return [(r.file, r.line, r.kind, r.scope) for r in name_only_references(change, files)]


def test_decorator_uses_and_callbacks_show_up(repo):                         # D-E6
    assert ("jobs.py", 5, "decorator", "module level") in refs(repo, "register")
    assert ("plugins.py", 4, "decorator", "module level") in refs(repo, "register")
    worker = refs(repo, "worker", resolved=[("jobs.py", 7)])
    assert ("jobs.py", 10, "reference", "module level") in worker                  # Thread(target=worker)
    assert ("jobs.py", 11, "reference", "module level") in worker                  # [worker]
    assert ("jobs.py", 7, "call", "function") not in worker, "a resolved call site is not repeated"


def test_module_level_callers_show_up(repo):                                    # D-E7
    assert ("app.py", 3, "call", "module level") in refs(repo, "create_app")
    assert ("app.py", 6, "call", "module level") in refs(repo, "main")


def test_inherited_methods_and_super_calls_show_up(repo):                     # D-E9, D-E15
    save = refs(repo, "save")
    assert ("models.py", 12, "attribute", "function") in save                       # self.save(), from a grandparent
    assert ("models.py", 15, "attribute", "function") in save                       # super().save()
    assert not [r for r in save if r[1] in (2, 14)], "a def is a definition, not a reference"


def test_comments_strings_and_definitions_never_count(repo):
    lines = {(f, line) for f, line, _, _ in refs(repo, "worker")}
    assert ("jobs.py", 12) not in lines and ("jobs.py", 13) not in lines
    assert ("lib.py", 5) not in lines
    assert ("jobs.py", 2) in lines                                                  # the import


def test_other_files_are_searched_as_whole_words(repo):
    ts = [r for r in refs(repo, "worker") if r[0] == "web/app.ts"]
    assert [(line, kind) for _, line, kind, _ in ts] == [(1, "text"), (2, "text"), (3, "text")]
    assert len([r for r in ts if r[1] == 2]) == 1                                   # 'workers' is not 'worker'


def test_a_file_that_does_not_parse_is_still_searched_as_text(repo):
    assert ("broken.py", 2, "text", "unknown") in refs(repo, "worker")


def test_every_reference_is_labelled_name_only_and_low_confidence(repo):
    change = FunctionChange(id="lib.py::worker", name="worker", before=sig("def worker(): pass"),
                            after=sig("def worker(): pass"))
    found = name_only_references(change, repo)
    assert found and {(r.label, r.confidence) for r in found} == {("name-only", "low")}
