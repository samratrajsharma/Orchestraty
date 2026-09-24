"""Regression suite — stdlib unittest only (no pytest, no network, no secrets).
Run from the repo root:  python -m unittest discover -s tests -v
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orch_engine.config import Config
from orch_engine.pipeline import build_index
from orch_engine import insights, diagram, techdebt, coverage, impact, config_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "sample_repo")

def _write(d, rel, content):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w") as fh:
        fh.write(content)


class SampleRepoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.cfg = Config(data_dir=os.path.join(cls.tmp, "cache"))
        cls.idx = build_index(SAMPLE, cls.cfg)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pipeline(self):
        s = self.idx.stats()
        self.assertGreaterEqual(s["symbols"], 10)
        self.assertGreater(s["graph_edges"], 0)

    def test_insights(self):
        ins = insights.repo_insights(self.idx)
        self.assertIn("api.py", ins["entry_files"])
        self.assertTrue(insights.file_summary(self.idx, "infer.py")["internal_deps"])

    def test_diagrams(self):
        self.assertTrue(diagram.architecture_dot(self.idx).startswith("digraph"))
        nodes, tree = diagram.mindmap_tree(self.idx, "__repo__", 2, ("contains",), 6)
        self.assertEqual(len(tree), len(nodes) - 1)   # single-parent tree
        self.assertTrue(diagram.mindmap_dot(self.idx, "__repo__", nodes, tree,
                                            diagram.node_roles(self.idx)).startswith("digraph"))

    def test_path_finding(self):
        p = diagram.path_between(self.idx, "api.py::create_app", "model.py::Detector.predict")
        self.assertGreaterEqual(len(p), 2)

    def test_techdebt(self):
        self.assertIn("cross_entropy", [d["symbol"] for d in techdebt.dead_code(self.idx)])
        self.assertIsInstance(techdebt.import_cycles(self.idx), list)
        self.assertIsInstance(techdebt.secret_scan(self.idx), list)

class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = Config(data_dir=os.path.join(self.tmp, "c"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_duplicate_detection(self):
        d = os.path.join(self.tmp, "dup")
        _write(d, "a.py", "def f(items):\n    t=0\n    for x in items:\n        if x>0:\n            t=t+x\n    return t\n")
        _write(d, "b.py", "def g(values):\n    t=0\n    for x in values:\n        if x>0:\n            t=t+x\n    return t\n")
        idx = build_index(d, self.cfg)
        self.assertTrue(techdebt.duplicate_pairs(idx, min_tokens=10))

    def test_coverage_and_impact(self):
        d = os.path.join(self.tmp, "cov")
        _write(d, "a.py", "def alpha(x):\n    return x+1\ndef beta(x):\n    return alpha(x)\ndef lonely(x):\n    return x\n")
        _write(d, "tests/test_a.py", "from a import alpha\ndef test_alpha():\n    assert alpha(1)==2\n")
        idx = build_index(d, self.cfg)
        cs = coverage.coverage_summary(idx)
        self.assertEqual(cs["n_test_files"], 1)
        self.assertIn("lonely", [u["symbol"] for u in cs["untested_complex"]])
        self.assertIn("beta", impact.impact_of(idx, "a.py::alpha")["symbols"])

    def test_config_surface(self):
        d = os.path.join(self.tmp, "cfg")
        _write(d, "config.yaml", "model: detr\nlr: 0.001\n")
        _write(d, "settings.py", "DATA_DIR='/d'\nMAX_STEPS=10\n")
        idx = build_index(d, self.cfg)
        kinds = {c["kind"] for c in config_map.config_surface(idx)}
        self.assertIn("yaml", kinds)
        self.assertIn("settings", kinds)

    def test_track_git(self):
        if not shutil.which("git"):
            self.skipTest("git not available")
        from orch_engine import track
        d = os.path.join(self.tmp, "g")
        os.makedirs(d)
        run = lambda *a: subprocess.run(["git", *a], cwd=d, capture_output=True)
        run("init"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
        _write(d, "m.py", "def a():\n    return 1\n")
        run("add", "-A"); run("commit", "-m", "c1")
        _write(d, "m.py", "def a():\n    return 2\ndef b():\n    return 3\n")
        run("add", "-A"); run("commit", "-m", "c2")
        log = track.git_log(d)
        self.assertEqual(len(log), 2)
        base = track.snapshot(d, log[-1]["sha"], self.cfg)
        head = track.snapshot(d, log[0]["sha"], self.cfg)
        diff = track.diff(base, head)
        self.assertTrue(any("b" in s for s in diff["symbols_added"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
