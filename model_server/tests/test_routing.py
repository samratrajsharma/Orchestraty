from app.routing import pick_slot


def test_no_canary_is_stable():
    assert pick_slot(False, 100, 0.0) == "stable"


def test_canary_weighting():
    assert pick_slot(True, 100, 0.5) == "canary"
    assert pick_slot(True, 20, 0.10) == "canary"
    assert pick_slot(True, 20, 0.50) == "stable"
    assert pick_slot(True, 0, 0.0) == "stable"
    assert pick_slot(True, None, 0.0) == "stable"
