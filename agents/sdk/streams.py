"""Shared event-bus conventions for agents (mirrors the orchestrator)."""

import json

STREAM_PREFIX = "orch"
RESULTS_STREAM = f"{STREAM_PREFIX}:results"
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


def result_envelope(task_id, success, result=None, error=None) -> dict:
    env = {"task_id": task_id, "success": bool(success)}
    if result is not None:
        env["result"] = result
    if error is not None:
        env["error"] = error
    return env
