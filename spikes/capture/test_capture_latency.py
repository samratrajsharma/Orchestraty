"""Tests for the latency harness."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import capture_latency as cl

HERE = Path(__file__).resolve().parent


def test_percentiles_match_hand_computed_values():
    # Linear interpolation between closest ranks (numpy's default, Excel PERCENTILE.INC).
    v = [4, 1, 3, 2]                                   # sorted: 1 2 3 4
    assert cl.percentile(v, 0) == 1 and cl.percentile(v, 100) == 4
    assert cl.percentile(v, 50) == pytest.approx(2.5)
    assert cl.percentile(v, 90) == pytest.approx(3.7)  # position 2.7: 3 + 0.7 * (4 - 3)
    assert cl.percentile(v, 99) == pytest.approx(3.97)
    assert cl.percentile([7], 99) == 7
    assert cl.percentile([], 50) is None
    ten = list(range(1, 11))                           # 1..10
    assert cl.percentile(ten, 90) == pytest.approx(9.1)
    assert cl.stats(ten) == {"n": 10, "median": 5.5, "p90": pytest.approx(9.1), "p99": pytest.approx(9.91), "max": 10}


def test_rounds_warm_ups_and_cold_runs_are_counted_as_documented():
    calls = []

    def fake(argv, payload):
        calls.append(argv[0])
        return 0.010, (1 if argv[0] == "bad" else 0), argv[0] == "loud"

    commands = {"quiet": ["quiet"], "bad": ["bad"], "loud": ["loud"]}
    results = cl.measure(commands, [b"{}"], n=7, warmup=3, parallel=5, runner=fake)
    assert len(calls) == 5 * (3 + 7) * 3
    for name in commands:
        r = results[name]
        assert len(r["cold"]) == 5                     # the first run in each replayer
        assert len(r["warm"]) == 5 * 7                 # warm-up rounds are not measured
    assert results["bad"]["bad_exit"] == 50 and results["quiet"]["bad_exit"] == 0
    assert results["loud"]["printed"] == 50


def test_added_time_is_the_difference_at_each_statistic():
    def fake(argv, payload):
        return (0.020 if argv[0] == "shim" else 0.012), 0, False

    rows = cl.summarize(cl.measure({"shim": ["shim"], "noop": ["noop"]}, [b"{}"], n=20, warmup=2, parallel=5,
                                   runner=fake), "noop")
    assert rows["shim"]["added_over_baseline"] == pytest.approx({"median": 8.0, "p90": 8.0, "p99": 8.0, "max": 8.0})
    assert rows["noop"]["added_over_baseline"] == pytest.approx({"median": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0})


def test_a_no_op_measured_against_itself_adds_about_nothing():
    noop = [sys.executable, "-S", "-I", "-c", "pass"]
    rows = cl.summarize(cl.measure({"a": noop, "b": list(noop)}, [cl.synthetic_payload()], n=40, warmup=3,
                                   parallel=5), "b")
    median = rows["b"]["warm"]["median"]
    added = rows["a"]["added_over_baseline"]["median"]
    # A check for gross bias only. With n=40 on a shared CI machine, two identical no-ops can
    # differ by several milliseconds (6 ms on a 24 ms median has been seen). The precise
    # calibration, a no-op against itself at the full N, belongs in each T1 run's report.
    assert abs(added) < max(10.0, 0.5 * median), (added, median)
    assert rows["a"]["bad_exit"] == 0 and rows["a"]["printed"] == 0


def test_the_synthetic_payload_is_valid_and_about_the_asked_size():
    data = cl.synthetic_payload(size=2048)
    assert json.loads(data)["hook_event_name"] == "PreToolUse"
    assert 1900 <= len(data) <= 2200


def test_the_report_holds_numbers_and_no_pass_marks():
    noop = [sys.executable, "-S", "-I", "-c", "pass"]
    proc = subprocess.run([sys.executable, str(HERE / "capture_latency.py"), "--cmd", "noop=" + json.dumps(noop),
                           "--cmd", "other=" + json.dumps(noop), "--baseline", "noop", "--n", "3", "--warmup", "1",
                           "--parallel", "2"], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout.lower()
    assert "added over noop" in text and "median ms" in text
    for word in ("pass", "fail", "budget", "ok", "within", "exceed"):
        assert word not in text.replace("passed", ""), word
