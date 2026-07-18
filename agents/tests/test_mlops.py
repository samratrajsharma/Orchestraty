import pytest

from mlops_agent.config import ConfigError, resolve_config


def test_defaults():
    assert resolve_config({}) == {
        "dataset": "iris", "model": "random_forest", "params": {}, "registered_model_name": None}


def test_overrides():
    c = resolve_config({"dataset": "wine", "model": "logistic_regression",
                        "params": {"C": 0.5}, "registered_model_name": "m"})
    assert c["dataset"] == "wine" and c["model"] == "logistic_regression"
    assert c["params"] == {"C": 0.5} and c["registered_model_name"] == "m"


def test_errors():
    for bad in [{"dataset": "mnist"}, {"model": "xgboost"}, {"params": [1, 2]}]:
        with pytest.raises(ConfigError):
            resolve_config(bad)
