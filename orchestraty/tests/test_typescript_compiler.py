"""TypeScript contracts with the real compiler: the pinned TypeScript in tests/fixtures/ts, which
CI installs from its lockfile (npm ci). Skipped without it, unless ORCH_TS_REQUIRED is set."""
import os
import subprocess
import time
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import config, status
from orchestraty.coordinator import main as cmain
from orchestraty.livemap import typescript
from orchestraty.paths import State

BIN = Path(__file__).resolve().parent / "fixtures" / "ts" / "node_modules" / ".bin"
TSC = BIN / ("tsc.cmd" if os.name == "nt" else "tsc")
if not TSC.exists():
    if os.environ.get("ORCH_TS_REQUIRED"):
        raise RuntimeError(f"ORCH_TS_REQUIRED is set but {TSC} is missing: run npm ci in tests/fixtures/ts")
    pytest.skip("no pinned TypeScript compiler: run npm ci in tests/fixtures/ts", allow_module_level=True)

TSCONFIG = ('{\n  // the project\'s own settings, which the run overrides where they would write\n'
            '  "compilerOptions": {"target": "ES2020", "module": "ESNext", "moduleResolution": "Bundler",\n'
            '    "strict": true, "noEmit": true, "outDir": "dist", "baseUrl": ".", "paths": {"@/*": ["src/*"]}},\n'
            '  "include": ["src"]\n}\n')
PAY = ('import type { Money } from "money-types"\n\n'
       "export function charge(amount: Money, currency: string): number {\n  return amount.cents\n}\n")
USE = ('import { charge } from "@/pay"\n\n'
       'export function total(): number {\n  return charge({ cents: 3 }, "EUR")\n}\n')


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def compiler_on_path(monkeypatch):
    monkeypatch.setenv("PATH", str(BIN) + os.pathsep + os.environ.get("PATH", ""))


def project(tmp_path, git, tsconfig=TSCONFIG, pay=PAY, use=USE):
    """The project, committed (node_modules ignored), and two worktrees, each with the package
    its code imports a type from in node_modules, as an install would leave it."""
    repo = tmp_path / "web"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q")
    for rel, text in (("tsconfig.json", tsconfig), (".gitignore", "node_modules/\n"), ("src/pay.ts", pay),
                      ("src/use.ts", use)):
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    trees = []
    for name in ("a", "b"):
        wt = tmp_path / f"wt-{name}"
        git(repo, "worktree", "add", "-q", "-b", f"feat-{name}", str(wt))
        package = wt / "node_modules" / "money-types"
        package.mkdir(parents=True)
        (package / "package.json").write_text('{"name": "money-types", "types": "index.d.ts"}', encoding="utf-8")
        (package / "index.d.ts").write_text("export interface Money { cents: number }\n", encoding="utf-8")
        trees.append(wt)
    return repo, trees[0], trees[1]


def run(state, repo, wa, wb, edits):
    config.watch(state, str(repo / ".git"))
    typescript.save_settings(state, str(repo / ".git"), on=True, timeout=120)
    spool_event(state.root, "A", "SessionStart", wa)
    spool_event(state.root, "B", "SessionStart", wb)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    for sid, top, rel, text in edits:
        (top / rel).write_text(text, encoding="utf-8")
        spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(top / rel))
        c.step()
    for _ in range(600):
        c.livemap.indexes.wait(120)
        c._last_scan = None
        c.step()
        if not c.livemap.ts.busy() and len(typescript.runs(state)) == len({e[0] for e in edits}):
            break
        time.sleep(0.1)
    for _ in range(2):
        c._last_scan = None
        c.step()
    return c


def git_status(top):
    return subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=top, capture_output=True, text=True,
                          timeout=60).stdout.splitlines()


def test_a_removed_parameter_gives_the_o3_note_to_the_session_that_imports_it(state, tmp_path, git, compiler_on_path):
    repo, wa, wb = project(tmp_path, git)
    c = run(state, repo, wa, wb, [("A", wa, "src/pay.ts", PAY.replace(", currency: string", "")),
                                  ("B", wb, "src/use.ts", USE + '\nexport const again: number = "1"\n')])
    try:
        runs = typescript.runs(state)
        assert {sid: (r["source"], r["why"]) for sid, r in runs.items()} == {"A": ("compiler", ""), "B": ("compiler", "")}
        assert runs["A"]["base"]["exit_code"] == 0                     # the base compiled, its node_modules borrowed
        assert runs["B"]["head"]["exit_code"] == 2 and "TS2322" in runs["B"]["head"]["error"]   # a type error: compiled
        change = c.livemap.views["A"].symbols["src/pay.ts::charge"]
        assert (change.contract, change.reasons, change.heuristic) == ("breaking", ["parameter currency removed"], False)
        [o3] = [f for f in c.record.findings() if f["rule"] == "O3"]
        assert (o3["key"], o3["strength"], o3["detail"]["source"]) == ("O3|src/pay.ts::charge|A|B|src/use.ts",
                                                                          "medium", "compiler")
        assert o3["detail"]["uses"] == [{"symbol": "src/use.ts", "lines": [4]}]     # through the @/ alias
        [note] = [n for n in c.record.notes(session_id="B") if n["kind"] == "edit"]
        assert "parameter currency removed" in note["text"] and "heuristic: calls not resolved" in note["text"]
        # nothing written into the repository: only the edits, and node_modules, which git ignores
        assert sorted(git_status(wa)) == sorted(["!! node_modules/", " M src/pay.ts"])
        assert sorted(git_status(wb)) == sorted(["!! node_modules/", " M src/use.ts"])
        assert sorted(n[:5] for n in os.listdir(typescript.folder(state))) == ["head-", "head-", "runs."]
        assert (wa / "node_modules" / "money-types" / "index.d.ts").exists()       # the borrowed folder, untouched
    finally:
        c.close()


def test_a_project_that_does_not_compile_falls_back_and_says_why(state, tmp_path, git, compiler_on_path):
    broken = TSCONFIG.replace('"target": "ES2020"', '"target": "nonsense"')
    repo, wa, wb = project(tmp_path, git, tsconfig=broken)
    c = run(state, repo, wa, wb, [("A", wa, "src/pay.ts", PAY.replace(", currency: string", ""))])
    try:
        run_a = typescript.runs(state)["A"]
        assert run_a["source"] == "fallback" and run_a["why"].startswith("the working copy did not compile: ")
        assert "TS6046" in run_a["why"]
        change = c.livemap.views["A"].symbols["src/pay.ts::charge"]
        assert (change.contract, change.heuristic) == ("breaking", True)               # from the source
        shown = status.render(status.collect(state))
        assert "A: exports read from the source, heuristic: the working copy did not compile" in shown
    finally:
        c.close()


def test_a_file_with_a_syntax_error_is_not_compared(state, tmp_path, git, compiler_on_path):
    """Caught mid-edit, its charge left open: the compiler declares refund inside it, so refund
    would read as removed, and B uses it. The file has a syntax error its base commit does not."""
    pay = PAY + "\nexport function refund(id: string): void {}\n"
    use = USE.replace("{ charge }", "{ charge, refund }").replace("  return charge", '  refund("r1")\n  return charge')
    repo, wa, wb = project(tmp_path, git, pay=pay, use=use)
    typing = pay.replace("  return amount.cents\n}\n", "  if (amount.cents) {\n    return amount.cents\n}\n", 1)
    c = run(state, repo, wa, wb, [("A", wa, "src/pay.ts", typing), ("B", wb, "src/use.ts", use + "\nexport const k = 1\n")])
    try:
        run_a = typescript.runs(state)["A"]
        assert (run_a["source"], run_a["head"]["exit_code"], run_a["not_compared"]) == ("compiler", 2, ["src/pay.ts"])
        assert not [k for k in c.livemap.views["A"].symbols if k.startswith("src/pay.ts::")]
        assert not [f for f in c.record.findings() if f["rule"] in ("O3", "O4")]
        assert "A: compiled (with errors); not compared, a syntax error: src/pay.ts" in status.render(status.collect(state))
    finally:
        c.close()
