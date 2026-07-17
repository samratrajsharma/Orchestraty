"""Event-bus stream naming and message (de)serialization (pure, no Redis)."""

import json

STREAM_PREFIX = "orch"
RESULTS_STREAM = f"{STREAM_PREFIX}:results"
RESULTS_GROUP = "orchestrator"
SELFTEST_STREAM = f"{STREAM_PREFIX}:selftest"
DATA_FIELD = "data"


def task_stream(capability: str) -> str:
    return f"{STREAM_PREFIX}:tasks:{capability}"


def encode(message: dict) -> dict:
    return {DATA_FIELD: json.dumps(message)}


def decode(fields) -> dict:
    raw = None
    if isinstance(fields, dict):
        raw = fields.get(DATA_FIELD)
        if raw is None:
            raw = fields.get(DATA_FIELD.encode())
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw) if raw else {}
