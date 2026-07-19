from recovery_agent import policies


def test_retrain_and_alert_on_accuracy():
    v = {"status": "unhealthy", "breaches": [{"metric": "accuracy", "observed": 0.7, "threshold": 0.85}]}
    acts = [a["action"] for a in policies.decide(v)]
    assert "retrain" in acts and "alert" in acts


def test_rollback_on_latency():
    v = {"status": "degraded", "breaches": [{"metric": "latency_ms", "observed": 900, "threshold": 500}]}
    acts = [a["action"] for a in policies.decide(v)]
    assert acts == ["rollback"]


def test_healthy_no_actions():
    assert policies.decide({"status": "healthy", "breaches": []}) == []


def test_dedup_retrain():
    v = {"status": "degraded", "breaches": [{"metric": "accuracy"}, {"metric": "drift"}]}
    acts = [a["action"] for a in policies.decide(v)]
    assert acts.count("retrain") == 1
