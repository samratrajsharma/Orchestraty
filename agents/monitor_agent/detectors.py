"""Pure drift + health detectors for the monitor agent (no I/O)."""

import math

DEFAULT_THRESHOLDS = {
    "min_accuracy": 0.85,
    "max_latency_ms": 500,
    "max_error_rate": 0.05,
    "max_drift": 0.25,
    "max_cost_usd": 10.0,
}


def _bin_props(values, edges):
    counts = [0] * (len(edges) - 1)
    for v in values:
        for i in range(len(edges) - 1):
            if v <= edges[i + 1]:
                counts[i] += 1
                break
    total = len(values) or 1
    return [c / total for c in counts]


def psi(baseline, current, bins=10):
    """Population Stability Index: ~0 identical, 0.1-0.25 moderate, >0.25 significant drift."""
    if not baseline or not current:
        return 0.0
    lo, hi = min(baseline), max(baseline)
    if hi == lo:
        hi = lo + 1e-9
    edges = [lo + (hi - lo) * i / bins for i in range(bins + 1)]
    edges[0] = float("-inf")
    edges[-1] = float("inf")
    b = _bin_props(baseline, edges)
    c = _bin_props(current, edges)
    score = 0.0
    for pb, pc in zip(b, c):
        pb = max(pb, 1e-6)
        pc = max(pc, 1e-6)
        score += (pc - pb) * math.log(pc / pb)
    return round(score, 4)


def evaluate(observation, thresholds=None):
    """Compare observed metrics to thresholds; return status + breaches."""
    obs = observation or {}
    th = thresholds or DEFAULT_THRESHOLDS
    breaches = []
    if "accuracy" in obs and "min_accuracy" in th and obs["accuracy"] < th["min_accuracy"]:
        breaches.append({"metric": "accuracy", "observed": obs["accuracy"],
                         "threshold": th["min_accuracy"], "rule": "min"})
    for metric, key in [("latency_ms", "max_latency_ms"), ("error_rate", "max_error_rate"),
                        ("drift", "max_drift"), ("cost_usd", "max_cost_usd")]:
        if metric in obs and key in th and obs[metric] > th[key]:
            breaches.append({"metric": metric, "observed": obs[metric],
                             "threshold": th[key], "rule": "max"})
    if not breaches:
        status = "healthy"
    elif any(b["metric"] in ("accuracy", "error_rate") for b in breaches):
        status = "unhealthy"
    else:
        status = "degraded"
    return {"status": status, "breaches": breaches}
