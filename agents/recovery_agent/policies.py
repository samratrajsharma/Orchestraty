"""Declarative recovery policies (pure): map a monitor verdict to actions."""

DEFAULT_POLICIES = [
    {"when": {"metric": "accuracy"}, "action": "retrain"},
    {"when": {"metric": "drift"}, "action": "retrain"},
    {"when": {"metric": "latency_ms"}, "action": "rollback"},
    {"when": {"metric": "error_rate"}, "action": "rollback"},
    {"when": {"status": "unhealthy"}, "action": "alert"},
]


def decide(verdict, policies=None):
    """Given a monitor verdict (status + breaches), return the corrective actions."""
    policies = policies or DEFAULT_POLICIES
    verdict = verdict or {}
    breaches = verdict.get("breaches") or []
    status = verdict.get("status")
    actions = []
    seen = set()
    for p in policies:
        when = p.get("when") or {}
        reason = None
        if "metric" in when:
            for b in breaches:
                if b.get("metric") == when["metric"]:
                    reason = f"{b['metric']} breached (observed {b.get('observed')} vs {b.get('threshold')})"
                    break
        elif "status" in when and status == when["status"]:
            reason = f"status is {status}"
        if reason is not None:
            act = p["action"]
            if act not in seen:
                actions.append({"action": act, "reason": reason, "params": p.get("params") or {}})
                seen.add(act)
    return actions
