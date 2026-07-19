"""Pure helpers for the knowledge / insights layer."""

import json


def success_rate(by_status):
    """Fraction of workflows that SUCCEEDED, given a status->count map."""
    total = sum(by_status.values())
    if not total:
        return 0.0
    return round(by_status.get("SUCCEEDED", 0) / total, 4)


def matches_model(obj, model_name):
    """True if a task payload/result references the model name."""
    if not obj:
        return False
    try:
        return model_name in json.dumps(obj)
    except (TypeError, ValueError):
        return False
