import pytest

from monitor_agent import detectors


def test_psi_stable_vs_shifted():
    base = [float(i) for i in range(100)]
    same = [float(i) for i in range(100)]
    shifted = [float(i) + 80 for i in range(100)]
    assert detectors.psi(base, same) < 0.1
    assert detectors.psi(base, shifted) > 0.25


def test_evaluate_healthy():
    v = detectors.evaluate({"accuracy": 0.95, "latency_ms": 100})
    assert v["status"] == "healthy" and v["breaches"] == []


def test_evaluate_degraded_latency():
    v = detectors.evaluate({"accuracy": 0.95, "latency_ms": 900})
    assert v["status"] == "degraded" and any(b["metric"] == "latency_ms" for b in v["breaches"])


def test_evaluate_unhealthy_accuracy():
    v = detectors.evaluate({"accuracy": 0.60})
    assert v["status"] == "unhealthy" and any(b["metric"] == "accuracy" for b in v["breaches"])


def test_evaluate_drift_breach():
    v = detectors.evaluate({"drift": 0.4})
    assert v["status"] == "degraded" and any(b["metric"] == "drift" for b in v["breaches"])
