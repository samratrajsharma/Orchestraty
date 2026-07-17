"""Unit tests for event-bus serialization (no Redis required)."""

from app import streams


def test_task_stream_naming():
    assert streams.task_stream("mlops.train") == "orch:tasks:mlops.train"
    assert streams.RESULTS_STREAM == "orch:results"


def test_encode_decode_roundtrip():
    msg = {"task_id": "t1", "payload": {"x": 1}, "n": 3}
    enc = streams.encode(msg)
    assert set(enc) == {"data"} and isinstance(enc["data"], str)
    assert streams.decode(enc) == msg


def test_decode_bytes_fields():
    enc = {b"data": streams.encode({"a": 1})["data"].encode()}
    assert streams.decode(enc) == {"a": 1}


def test_decode_empty():
    assert streams.decode({}) == {}
