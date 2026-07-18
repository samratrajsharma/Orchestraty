"""Pure agent-liveness helpers (no database)."""


def has_capability(capabilities, capability) -> bool:
    return capability in (capabilities or [])


def is_stale(last_heartbeat, now, ttl_seconds) -> bool:
    """True if the agent has not been seen within ttl_seconds."""
    if last_heartbeat is None:
        return True
    return (now - last_heartbeat).total_seconds() > ttl_seconds
