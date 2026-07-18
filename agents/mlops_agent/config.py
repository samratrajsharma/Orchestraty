"""Pure input helpers for the MLOps agent (no ML dependencies)."""

DATASETS = ("iris", "wine")
MODELS = ("random_forest", "logistic_regression")


class ConfigError(ValueError):
    pass


def resolve_config(payload: dict) -> dict:
    """Validate + normalize a training request payload."""
    payload = payload or {}
    dataset = payload.get("dataset", "iris")
    if dataset not in DATASETS:
        raise ConfigError(f"unknown dataset '{dataset}' (have {list(DATASETS)})")
    model = payload.get("model", "random_forest")
    if model not in MODELS:
        raise ConfigError(f"unknown model '{model}' (have {list(MODELS)})")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise ConfigError("params must be an object")
    return {
        "dataset": dataset,
        "model": model,
        "params": params,
        "registered_model_name": payload.get("registered_model_name"),
    }
