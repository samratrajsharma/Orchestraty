"""Rollout strategy planning (pure)."""

STRATEGIES = ("direct", "canary", "blue-green")


class RolloutError(ValueError):
    pass


def plan(strategy, canary_weight=10, auto_promote=False):
    """Return the ordered model-server operations for a rollout strategy."""
    if strategy not in STRATEGIES:
        raise RolloutError(f"unknown strategy '{strategy}' (have {list(STRATEGIES)})")
    if strategy == "canary":
        steps = [{"op": "load", "slot": "canary", "weight": int(canary_weight)}]
        if auto_promote:
            steps.append({"op": "promote"})
        return steps
    # direct + blue-green both load straight into the stable slot
    return [{"op": "load", "slot": "stable"}]
