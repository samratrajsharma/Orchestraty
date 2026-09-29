"""Tests for the fixed "Not analysed" block."""
from effects_notanalysed import FIXED, not_analysed_block, render_text
from effects_record import FunctionChange, signature_from_source as sig


def test_the_fixed_items_are_always_there_in_the_same_order():
    empty = not_analysed_block()
    assert empty.fixed == FIXED and empty.found == ()
    text = render_text(empty)
    assert text.splitlines()[0] == "Not analysed"
    positions = [text.index(item) for _, item in FIXED]
    assert positions == sorted(positions)
    assert "Found in this change: nothing beyond the items above." in text


def test_the_fixed_items_cover_every_deferred_gap():
    keys = [k for k, _ in FIXED]
    assert keys == ["languages", "unresolved-callers", "inheritance", "dynamic", "module-level", "renames",
                    "wrapping-decorators", "routes", "cycles", "size-and-entry-points", "runtime", "left-out"]


def test_what_this_change_left_out_is_listed_below_the_fixed_items():
    ts = FunctionChange(id="web/app.ts::run", name="run", language="typescript", before=sig("def run(): pass"),
                        after=sig("def run(a): pass"))
    py = FunctionChange(id="pkg/mod.py::f", name="f", before=sig("def f(): pass"), after=sig("def f(a): pass"))
    index = [{"path": "env/", "reason": "skipped folder"}, {"path": "vendor/lib", "reason": "submodule"},
             {"path": "env/", "reason": "skipped folder"}, {"path": "", "reason": "file cap (5) reached"}]
    block = not_analysed_block([ts, py], index, ["web/app.ts", "pkg/mod.py", "docs/a.md", "cmd/main.go"])
    assert block.fixed == FIXED
    assert block.found == (("web/app.ts::run", "typescript is not analysed"),
                           ("web/app.ts", ".ts files are not analysed"), ("cmd/main.go", ".go files are not analysed"),
                           ("env/", "skipped folder"), ("vendor/lib", "submodule"),
                           ("(the rest of the repository)", "file cap (5) reached"))
    text = render_text(block)
    assert text.index("Found in this change:") > text.index(FIXED[-1][1])
    assert "- env/: skipped folder" in text and text.count("env/: skipped folder") == 1
