"""Schema-guided extraction: unstructured text -> structured fields (pure)."""

import re

BUILTIN = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "phone": r"\+?\d[\d\s().-]{7,}\d",
    "number": r"-?\d+(?:\.\d+)?",
    "url": r"https?://\S+",
    "date": r"\d{4}-\d{2}-\d{2}",
}


class SchemaError(ValueError):
    pass


def extract(text, schema):
    text = text or ""
    if not isinstance(schema, list):
        raise SchemaError("schema must be a list of field definitions")
    out = {}
    for field in schema:
        name = field.get("name")
        if not name:
            raise SchemaError("each field needs a 'name'")
        ftype = field.get("type", "regex")
        pattern = field.get("pattern") or BUILTIN.get(ftype)
        if pattern is None:
            raise SchemaError(f"field '{name}': unknown type '{ftype}' and no pattern given")
        match = re.search(pattern, text)
        value = match.group(0) if match else None
        if value is not None and ftype == "number":
            value = float(value) if "." in value else int(value)
        out[name] = value
    return out
