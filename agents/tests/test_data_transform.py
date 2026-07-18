import pytest

from data_transform_agent.extract import SchemaError, extract


def test_extract_builtins():
    text = "Order total 42.5 for jane@acme.io on 2026-07-18, see https://acme.io"
    r = extract(text, [
        {"name": "email", "type": "email"},
        {"name": "amount", "type": "number"},
        {"name": "date", "type": "date"},
        {"name": "link", "type": "url"},
    ])
    assert r["email"] == "jane@acme.io" and r["amount"] == 42.5
    assert r["date"] == "2026-07-18" and r["link"].startswith("https://acme.io")


def test_custom_pattern_and_missing():
    assert extract("SKU: AB-9931", [{"name": "sku", "type": "regex", "pattern": r"[A-Z]{2}-\d+"}])["sku"] == "AB-9931"
    assert extract("nothing here", [{"name": "email", "type": "email"}])["email"] is None


def test_schema_errors():
    for bad in [{"bad": "x"}, [{"type": "email"}], [{"name": "x", "type": "zzz"}]]:
        with pytest.raises(SchemaError):
            extract("x", bad)
