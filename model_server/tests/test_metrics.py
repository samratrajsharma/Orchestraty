from app.metrics import render


def test_render_counter_and_labels():
    out = render([
        ("model_server_predictions_total", "counter", "preds", [({}, 7)]),
        ("model_server_model_loaded", "gauge", "loaded", [({"slot": "stable"}, 1)]),
    ])
    assert "model_server_predictions_total 7" in out
    assert 'model_server_model_loaded{slot="stable"} 1' in out
    assert out.endswith("\n")
