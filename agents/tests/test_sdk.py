from sdk import streams


def test_stream_names():
    assert streams.task_stream("mlops.train") == "orch:tasks:mlops.train"
    assert streams.RESULTS_STREAM == "orch:results"


def test_encode_decode_roundtrip():
    m = {"task_id": "t", "payload": {"x": 1}}
    assert streams.decode(streams.encode(m)) == m


def test_decode_bytes_and_empty():
    assert streams.decode({b"data": streams.encode({"a": 1})["data"].encode()}) == {"a": 1}
    assert streams.decode({}) == {}


def test_result_envelope():
    assert streams.result_envelope("t1", True, result={"a": 1}) == {
        "task_id": "t1", "success": True, "result": {"a": 1}}
    assert streams.result_envelope("t2", False, error="boom") == {
        "task_id": "t2", "success": False, "error": "boom"}
