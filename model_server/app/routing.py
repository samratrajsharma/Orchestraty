"""Pure traffic routing for the model server (stable vs canary)."""


def pick_slot(canary_loaded: bool, canary_weight, rand01: float) -> str:
    """Return 'canary' with probability canary_weight% when a canary is loaded."""
    try:
        weight = float(canary_weight or 0)
    except (TypeError, ValueError):
        weight = 0.0
    if canary_loaded and rand01 * 100 < weight:
        return "canary"
    return "stable"
