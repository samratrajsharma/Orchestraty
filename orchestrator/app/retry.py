"""Retry / backoff policy (pure logic)."""


def should_retry(attempts_used: int, max_attempts: int) -> bool:
    """True if another attempt is allowed."""
    return attempts_used < max_attempts


def backoff_delay(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    """Exponential backoff in seconds: base * 2^(attempt-1), capped."""
    attempt = max(1, attempt)
    return min(cap, base * (2 ** (attempt - 1)))
