from app.metrics import render


def test_render_labels_and_scalar():
    out = render([
        ("m_gauge", "gauge", "a gauge", [({"status": "OK"}, 3), ({"status": "BAD"}, 1)]),
        ("m_count", "counter", "a counter", [({}, 5)]),
    ])
    assert "# HELP m_gauge a gauge" in out
    assert "# TYPE m_gauge gauge" in out
    assert 'm_gauge{status="OK"} 3' in out
    assert 'm_gauge{status="BAD"} 1' in out
    assert "m_count 5" in out
    assert out.endswith("\n")


def test_render_empty_samples():
    out = render([("m", "gauge", "h", [])])
    assert "# TYPE m gauge" in out
