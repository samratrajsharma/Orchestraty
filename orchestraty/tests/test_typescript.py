"""TypeScript contracts in the coordinator, without Node: off by default, and the fallback (the
exports read from source) giving the session that imports a changed export a heuristic O3,
never a strong one."""
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

from helpers import spool_event
from orchestraty import cli, config, status
from orchestraty.coordinator import main as cmain
from orchestraty.delivery import slots
from orchestraty.livemap import typescript
from orchestraty.paths import State

PAY = ("export function charge(amount: number, currency: string): number {\n  return amount\n}\n\n"
       "export interface Receipt {\n  id: string\n}\n")
USE = 'import { charge } from "./pay"\n\nexport function total(): number {\n  return charge(3, "EUR")\n}\n'
DOCS = Path(__file__).resolve().parents[1] / "docs" / "status-json.md"


def documented(heading):
    """The fields the table under a heading of docs/status-json.md names."""
    section = DOCS.read_text(encoding="utf-8").split(f"### {heading}\n", 1)[1].split("\n#", 1)[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", section, re.M))


@pytest.fixture
def state(state_root):
    return State(str(state_root))


@pytest.fixture
def project(tmp_path, git):
    """A TypeScript project with no tsconfig.json, and two worktrees on branches of their own."""
    repo = tmp_path / "web"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "src" / "pay.ts").write_text(PAY, encoding="utf-8")
    (repo / "src" / "use.ts").write_text(USE, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    return repo, wa, wb


def coordinator(state, repo, wa, wb):
    config.watch(state, str(repo / ".git"))
    spool_event(state.root, "A", "SessionStart", wa)
    spool_event(state.root, "B", "SessionStart", wb)
    c = cmain.Coordinator(state)
    c.step()
    assert c.livemap.indexes.wait(120)
    return c


def edit(state, c, sid, top, rel, text):
    (top / rel).write_text(text, encoding="utf-8")
    spool_event(state.root, sid, "PostToolUse", top, tool_name="Edit", file_path=str(top / rel))
    for _ in range(2):
        c.step()
        c.livemap.indexes.wait(120)
        c._last_scan = None
    c.step()


def test_off_by_default_nothing_runs(state, project):
    repo, wa, wb = project
    c = coordinator(state, repo, wa, wb)
    try:
        edit(state, c, "A", wa, "src/pay.ts", PAY.replace(", currency: string", ""))
        edit(state, c, "B", wb, "src/use.ts", USE + "\nexport const again = 1\n")
        assert not [k for k in c.livemap.views["A"].symbols if k.endswith(".ts::charge")]
        assert not [f for f in c.record.findings() if f["rule"] in ("O3", "O4")]
        assert not os.path.exists(typescript.folder(state)) and not c.livemap.ts.busy()
        assert status.collect(state)["repositories"][0]["typescript"] == {"on": False, "timeout": 300, "runs": []}
    finally:
        c.close()


def test_the_fallback_reads_the_source_and_its_o3_is_heuristic(state, project, capsys, monkeypatch):
    repo, wa, wb = project
    monkeypatch.chdir(repo)
    config.watch(state, str(repo / ".git"))
    assert cli.main(["ts", "on", "--timeout", "60"]) == 0
    assert typescript.settings(config.load(state), str(repo / ".git")) == {"on": True, "timeout": 60}
    capsys.readouterr()
    cfg = config.load(state)
    cfg["steer"] = True                                          # a strong note would stop the edit
    config.save(state, cfg)
    c = coordinator(state, repo, wa, wb)
    try:
        edit(state, c, "A", wa, "src/pay.ts", PAY.replace(", currency: string", ""))
        change = c.livemap.views["A"].symbols["src/pay.ts::charge"]
        assert (change.kind, change.contract, change.reasons, change.language, change.heuristic) == (
            "modified", "breaking", ["parameter currency removed"], "typescript", True)
        assert change.lines == [1, 1]
        edit(state, c, "B", wb, "src/use.ts", USE + "\nexport const again = 1\n")
        [o3] = [f for f in c.record.findings() if f["rule"] == "O3"]
        assert (o3["key"], o3["strength"], o3["file"]) == ("O3|src/pay.ts::charge|A|B|src/use.ts", "medium", "src/use.ts")
        assert (o3["detail"]["heuristic"], o3["detail"]["evidence"], o3["detail"]["source"]) == (True, "import", "source")
        assert o3["detail"]["uses"] == [{"symbol": "src/use.ts", "lines": [4]}]
        assert not [f for f in c.record.findings() if f["rule"] == "O6"]       # never for TypeScript
        [note] = [n for n in c.record.notes(session_id="B") if n["kind"] == "edit"]
        assert "changed the contract of charge in src/pay.ts: parameter currency removed" in note["text"]
        assert "heuristic: calls not resolved" in note["text"]
        slot = json.loads(Path(slots.edit_slot(state, "B", str(wb / "src" / "use.ts"))).read_bytes())
        assert "permissionDecision" not in slot["hookSpecificOutput"]           # steer on, but not strong: no deny
        data = status.collect(state)["repositories"][0]["typescript"]
        assert [(r["session"], r["source"], r["why"]) for r in data["runs"]] == [
            ("A", "fallback", "no tsconfig.json at the top of the worktree"),
            ("B", "fallback", "no tsconfig.json at the top of the worktree")]
        assert all(set(r) == documented("TypeScript run") for r in data["runs"])
        shown = status.render(status.collect(state))
        assert "TypeScript contracts: on" in shown and "medium  O3" in shown
        assert "A: exports read from the source, heuristic: no tsconfig.json at the top of the worktree" in shown
    finally:
        c.close()
    assert cli.main(["ts"]) == 0
    out = capsys.readouterr().out
    assert "TypeScript contracts: on" in out and "exports read from the source, heuristic" in out
    assert cli.main(["ts", "off"]) == 0
    assert typescript.settings(config.load(state), str(repo / ".git"))["on"] is False


def test_orch_ts_needs_a_watched_repository(state, project, monkeypatch, capsys):
    repo, wa, wb = project
    monkeypatch.chdir(repo)
    assert cli.main(["ts", "on"]) == 2
    assert "not watched" in capsys.readouterr().err


def test_a_file_caught_mid_edit_is_not_compared(state, project):
    """Its brace left open, the rest of the file would read as the inside of a function, and
    Receipt as removed: the file is not compared until it reads whole again."""
    repo, wa, wb = project
    typescript.save_settings(state, str(repo / ".git"), on=True)
    c = coordinator(state, repo, wa, wb)
    try:
        edit(state, c, "B", wb, "src/use.ts", USE + "\nexport const again = 1\n")
        typing = PAY.replace("  return amount\n}\n", "  if (amount) {\n    return amount\n}\n", 1)
        edit(state, c, "A", wa, "src/pay.ts", typing)
        assert not [k for k in c.livemap.views["A"].symbols if k.startswith("src/pay.ts::")]
        assert not [f for f in c.record.findings() if f["rule"] in ("O3", "O4")]
        assert typescript.runs(state)["A"]["not_compared"] == ["src/pay.ts"]
        assert ("A: exports read from the source, heuristic: no tsconfig.json at the top of the worktree; "
                "not compared, a syntax error: src/pay.ts") in status.render(status.collect(state))
        edit(state, c, "A", wa, "src/pay.ts", PAY.replace(", currency: string", ""))       # the edit done
        assert [f["key"] for f in c.record.findings() if f["rule"] == "O3"] == ["O3|src/pay.ts::charge|A|B|src/use.ts"]
        assert typescript.runs(state)["A"]["not_compared"] == []
    finally:
        c.close()


def test_which_compiler_errors_are_about_the_project_and_which_are_syntax_errors():
    out = ["tsconfig.json(3,15): error TS6046: Argument for '--target' option must be: 'es5', 'es6'.",
           "error TS18003: No inputs were found in config file '/w/tsconfig.json'.",
           "error TS5083: Cannot read file '/w/tsconfig.base.json'.",
           "tsconfig.json(2,3): error TS1005: ',' expected.",
           "error TS2688: Cannot find type definition file for 'node'.",
           "src/pay.ts(4,10): error TS2322: Type 'string' is not assignable to type 'number'.",
           "src/pay.ts(5,3): error TS18048: 'amount' is possibly 'undefined'.",
           "src/(group)/use.tsx(1,10): error TS6133: 'refund' is declared but its value is never read.",
           "src/pay.ts(6,1): error TS1005: '}' expected.",
           "src\\win.ts(1,1): error TS1128: Declaration or statement expected.",
           "  error TS1005, named in an elaboration"]
    assert [typescript._project_error(line) for line in out] == [True] * 5 + [False] * 6
    assert typescript.syntax_errors(out) == {"src/pay.ts", "src/win.ts"}


def test_what_keeps_a_project_from_being_compiled(tmp_path):
    assert typescript.project_problem(str(tmp_path)) == "no tsconfig.json at the top of the worktree"
    (tmp_path / "tsconfig.json").write_text("{ nope", encoding="utf-8")
    assert typescript.project_problem(str(tmp_path)) == "tsconfig.json could not be read"
    (tmp_path / "base.json").write_text('{"compilerOptions": {"outFile": "all.js"}}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text('{"extends": "./base.json"}', encoding="utf-8")
    assert typescript.project_problem(str(tmp_path)) == "tsconfig.json sets outFile"      # its declarations would go there
    lenient = '{\n  // comments allowed\n  "compilerOptions": {"strict": true,},\n}'
    (tmp_path / "tsconfig.json").write_text(lenient, encoding="utf-8")
    assert typescript.project_problem(str(tmp_path)) == ""


def fake_compiler(top, seconds):
    """A tsc in the worktree's node_modules/.bin that only waits."""
    folder = top / "node_modules" / ".bin"
    folder.mkdir(parents=True)
    wait = f'"{sys.executable}" -c "import time; time.sleep({seconds})"'
    if os.name == "nt":
        (folder / "tsc.cmd").write_text(f"@{wait}\r\n", encoding="utf-8")
    else:
        (folder / "tsc").write_text(f"#!/bin/sh\nexec {wait}\n", encoding="utf-8")
        (folder / "tsc").chmod(0o755)


def test_a_compiler_run_that_takes_too_long_falls_back(state, tmp_path, git):
    repo = tmp_path / "web"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q")
    for rel, text in (("tsconfig.json", '{"compilerOptions": {"strict": true}}\n'), (".gitignore", "node_modules/\n"),
                      ("src/pay.ts", PAY), ("src/use.ts", USE)):
        (repo / rel).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    wa, wb = tmp_path / "wt-a", tmp_path / "wt-b"
    git(repo, "worktree", "add", "-q", "-b", "feat-a", str(wa))
    git(repo, "worktree", "add", "-q", "-b", "feat-b", str(wb))
    fake_compiler(wa, 60)
    typescript.save_settings(state, str(repo / ".git"), on=True, timeout=1)
    c = coordinator(state, repo, wa, wb)
    try:
        edit(state, c, "A", wa, "src/pay.ts", PAY.replace(", currency: string", ""))
        for _ in range(600):
            c.step()
            if not c.livemap.ts.busy() and "A" in typescript.runs(state):
                break
            time.sleep(0.05)
        c._last_scan = None
        c.step()
        run_a = typescript.runs(state)["A"]
        assert (run_a["source"], run_a["why"]) == ("fallback", "the working copy took longer than 1 s to compile")
        assert run_a["head"]["timed_out"] and run_a["base"] is None              # the base was not compiled for nothing
        change = c.livemap.views["A"].symbols["src/pay.ts::charge"]
        assert (change.contract, change.reasons, change.heuristic) == ("breaking", ["parameter currency removed"], True)
    finally:
        c.close()
