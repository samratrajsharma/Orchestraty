"""Unit tests for pure agent-liveness helpers (no database)."""

from datetime import datetime, timedelta, timezone

from app import liveness


def test_has_capability():
    assert liveness.has_capability(["a", "b"], "a")
    assert not liveness.has_capability(["a"], "z")
    assert not liveness.has_capability(None, "a")


def test_is_stale():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert liveness.is_stale(None, now, 30)
    assert not liveness.is_stale(now - timedelta(seconds=10), now, 30)
    assert liveness.is_stale(now - timedelta(seconds=60), now, 30)
