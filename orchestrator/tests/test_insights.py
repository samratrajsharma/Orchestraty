from app.insights import matches_model, success_rate


def test_success_rate():
    assert success_rate({"SUCCEEDED": 3, "FAILED": 1}) == 0.75
    assert success_rate({}) == 0.0
    assert success_rate({"SUCCEEDED": 0, "RUNNING": 2}) == 0.0


def test_matches_model():
    assert matches_model({"registered_model": "iris-classifier"}, "iris-classifier")
    assert matches_model({"model_uri": "models:/iris-classifier/1"}, "iris-classifier")
    assert not matches_model({"model_uri": "models:/other/1"}, "iris-classifier")
    assert not matches_model(None, "iris-classifier")
