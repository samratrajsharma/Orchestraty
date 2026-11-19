"""A small reader for the YAML of ``.orchestraty/policy.yml`` and ``people.yml``.

It reads what those files need, and refuses the rest with the line it is on, so that a file
means the same thing to every teammate's Orchestraty and no YAML feature can surprise one:

* block mappings, and block sequences of scalars or of mappings;
* single-line flow sequences of scalars: ``[a, "b", 'c']``;
* plain, single-quoted (``''`` for a quote) and double-quoted scalars (only the escapes
  ``\\"`` and ``\\\\``);
* ``#`` comments. A byte-order mark is dropped, and CRLF line ends are read as LF.

A plain ``true`` or ``false`` is a bool and a plain whole number an int; everything else is a
str (``on`` stays the word "on": its reader decides what it means).

Refused: a tab; anchors, aliases and tags; a second document (``---``); multi-line scalars
(``|``, ``>``, or a plain value that goes on to the next line); a key given twice; null
(``null``, ``~``, or a key with nothing after it); a list of lists, or ``{...}`` mappings; a
file over its size limit (64 KiB unless the caller says) and nesting deeper than four levels.

Mappings come back as ``Map`` (a dict) and sequences as ``Seq`` (a list). Each keeps the line of
every key or item in ``lines``, so a reader of the file can say where a value is wrong.
"""

MAX_BYTES = 64 * 1024
MAX_DEPTH = 4
_NULLS = ("null", "Null", "NULL", "~")
_TRUE, _FALSE = ("true", "True", "TRUE"), ("false", "False", "FALSE")
_STARTS_A_SCALAR = (None, ":", "-", "[", ",")      # a quote after one of these (or spaces) opens a quoted value


class Error(ValueError):
    """The file cannot be read: `message` says why, and `line` where (None for the whole file)."""

    def __init__(self, message, line=None):
        super().__init__(f"line {line}: {message}" if line else message)
        self.message, self.line = message, line


class Map(dict):
    """A mapping; `lines` gives the line each key is on, `line` the line it starts on."""

    def __init__(self, line=None):
        super().__init__()
        self.lines, self.line = {}, line


class Seq(list):
    """A sequence; `lines` gives the line each item is on, `line` the line it starts on."""

    def __init__(self, line=None):
        super().__init__()
        self.lines, self.line = [], line


class _Line:
    __slots__ = ("no", "indent", "text")

    def __init__(self, no, indent, text):
        self.no, self.indent, self.text = no, indent, text

    def is_item(self):
        return self.text == "-" or self.text.startswith("- ")


def parse(data, max_bytes=MAX_BYTES, max_depth=MAX_DEPTH):
    """The value a YAML document holds. `data` is bytes (UTF-8) or str. Raises Error."""
    size = len(data) if isinstance(data, bytes) else len(data.encode("utf-8", "surrogatepass"))
    if size > max_bytes:
        raise Error(f"the file is larger than {max_bytes // 1024} KiB")
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            raise Error("the file is not UTF-8") from None
    if data.startswith("﻿"):
        data = data[1:]
    lines = _lines(data)
    if not lines:
        raise Error("the file holds nothing but comments", 1)
    reader = _Reader(lines, max_depth)
    value = reader.block(lines[0].indent, 1)
    if reader.pos < len(lines):
        raise Error("this line is indented where no key or list item takes it", lines[reader.pos].no)
    return value


def _lines(text):
    """The lines that hold something, comments cut off, each with its indent."""
    out = []
    for no, raw in enumerate(text.split("\n"), start=1):
        if raw.endswith("\r"):
            raw = raw[:-1]
        if "\t" in raw:
            raise Error("a tab: indent with spaces, and write no tab in a value", no)
        content = _without_comment(raw, no).rstrip(" ")
        stripped = content.lstrip(" ")
        if not stripped:
            continue
        if content.startswith(("---", "...")) and content[3:].strip() == "":
            raise Error("a second document (--- or ...): one file holds one document", no)
        if content.startswith("%"):
            raise Error("a directive (%): one file holds one document and no directive", no)
        out.append(_Line(no, len(content) - len(stripped), stripped))
    return out


def _without_comment(raw, no):
    """The line up to its comment. A # starts one at the line's start or after a space, outside
    quotes; a quote opens a quoted value only where a value starts."""
    quote, prev, i = None, None, 0
    while i < len(raw):
        c = raw[i]
        if quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = None
        elif quote == "'":
            if c == "'" and raw[i + 1:i + 2] == "'":
                i += 1
            elif c == "'":
                quote = None
        elif c == "#" and (i == 0 or raw[i - 1] == " "):
            return raw[:i]
        elif c in "'\"" and prev in _STARTS_A_SCALAR:
            quote = c
        if quote is None and c != " ":
            prev = c
        i += 1
    if quote is not None:
        raise Error("a quote is not closed on its line", no)
    return raw


class _Reader:
    def __init__(self, lines, max_depth):
        self.lines, self.max_depth, self.pos = lines, max_depth, 0

    def _deep(self, depth, no):
        if depth > self.max_depth:
            raise Error(f"nested more than {self.max_depth} levels", no)

    def block(self, indent, depth):
        """The mapping or sequence whose first line is at self.pos, indented `indent`."""
        line = self.lines[self.pos]
        self._deep(depth, line.no)
        return self.sequence(indent, depth) if line.is_item() else self.mapping(indent, depth)

    def mapping(self, indent, depth):
        first = self.lines[self.pos]
        out = Map(first.no)
        while self.pos < len(self.lines):
            line = self.lines[self.pos]
            if line.indent < indent:
                break
            if line.indent > indent:
                raise Error("this line is indented more than the keys around it", line.no)
            if line.is_item():
                if out:
                    raise Error("a list item among keys: put the list under a key of its own", line.no)
                raise Error("a list item where a key was expected", line.no)
            key, rest = _key(line)
            if key in out:
                raise Error(f"the key {key} is given twice", line.no)
            out.lines[key] = line.no
            self.pos += 1
            if rest:
                out[key] = self.inline(rest, line.no, depth + 1)
                self._no_continuation(indent, line.no)
                continue
            nxt = self.lines[self.pos] if self.pos < len(self.lines) else None
            if nxt is not None and (nxt.indent > indent or (nxt.indent == indent and nxt.is_item())):
                out[key] = self.block(nxt.indent, depth + 1)
            else:
                raise Error(f"the key {key} has no value (null is not read)", line.no)
        return out

    def sequence(self, indent, depth):
        first = self.lines[self.pos]
        out = Seq(first.no)
        while self.pos < len(self.lines):
            line = self.lines[self.pos]
            if line.indent != indent or not line.is_item():
                if line.indent > indent:
                    raise Error("this line is indented more than the list items around it", line.no)
                break
            rest = line.text[1:].lstrip(" ")
            out.lines.append(line.no)
            if not rest:
                self.pos += 1
                nxt = self.lines[self.pos] if self.pos < len(self.lines) else None
                if nxt is None or nxt.indent <= indent:
                    raise Error("a list item has no value (null is not read)", line.no)
                if nxt.is_item():
                    raise Error("a list of lists is not read", nxt.no)
                out.append(self.block(nxt.indent, depth + 1))
            elif rest == "-" or rest.startswith("- "):
                raise Error("a list of lists is not read", line.no)
            elif _is_entry(rest):
                column = indent + len(line.text) - len(rest)       # the item's keys line up under this one
                self.lines[self.pos] = _Line(line.no, column, rest)
                self._deep(depth + 1, line.no)
                out.append(self.mapping(column, depth + 1))
            else:
                self.pos += 1
                out.append(self.inline(rest, line.no, depth + 1))
                self._no_continuation(indent, line.no)
        return out

    def _no_continuation(self, indent, no):
        if self.pos < len(self.lines) and self.lines[self.pos].indent > indent:
            raise Error("a value goes on to the next line: multi-line values are not read",
                        self.lines[self.pos].no)

    def inline(self, text, no, depth):
        """A value written on its key's or item's line: a scalar or a flow sequence."""
        if text.startswith("["):
            self._deep(depth, no)
            return _flow(text, no)
        if text.startswith("- ") or text == "-":
            raise Error("a list cannot start on a key's line: put its items on the lines below", no)
        return _scalar(text, no)


def _is_entry(text):
    """Whether a list item's text starts a mapping (key: value)."""
    try:
        _key(_Line(0, 0, text))
    except Error:
        return False
    return True


def _key(line):
    """(key, rest) of a `key: value` line; rest is "" when the value is on the lines below."""
    text = line.text
    if text[0] in "'\"":
        value, end = _quoted(text, 0, line.no)
        key, after = value, text[end:]
        if not (after == ":" or after.startswith(": ")):
            raise Error("expected key: value", line.no)
        return key, after[1:].strip(" ")
    cut = text.find(": ")
    if cut < 0:
        if not text.endswith(":"):
            raise Error("expected key: value", line.no)
        cut = len(text) - 1
    key = text[:cut]
    if key[0] in "&*!":
        raise Error({"&": "an anchor (&) is not read", "*": "an alias (*) is not read",
                     "!": "a tag (!) is not read"}[key[0]], line.no)
    if not key or not (key[0].isalnum() or key[0] == "_") or not all(c.isalnum() or c in "_.-" for c in key):
        raise Error(f"a key is letters, digits, _ . and -, or quoted: {key[:40]!r}", line.no)
    return key, text[cut + 1:].strip(" ")


def _quoted(text, start, no):
    """(value, end) of the quoted scalar that starts at text[start]."""
    quote, out, i = text[start], [], start + 1
    while i < len(text):
        c = text[i]
        if quote == '"' and c == "\\":
            nxt = text[i + 1:i + 2]
            if nxt not in ('"', "\\"):
                raise Error(f"the escape \\{nxt} is not read: only \\\" and \\\\ are", no)
            out.append(nxt)
            i += 2
            continue
        if c == quote:
            if quote == "'" and text[i + 1:i + 2] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise Error("a quote is not closed on its line", no)


def _scalar(text, no):
    """One scalar: quoted (a str), or plain (a bool, an int or a str)."""
    if text[0] in "'\"":
        value, end = _quoted(text, 0, no)
        if text[end:].strip(" "):
            raise Error("text after a quoted value", no)
        return value
    first = text[0]
    if first == "&":
        raise Error("an anchor (&) is not read", no)
    if first == "*":
        raise Error("an alias (*) is not read: quote a value that starts with *", no)
    if first == "!":
        raise Error("a tag (!) is not read", no)
    if first in "|>":
        raise Error("a multi-line value (| or >) is not read", no)
    if first == "{":
        raise Error("a {...} mapping is not read: write it as keys on lines of their own", no)
    if first in "@`%?,]}":
        raise Error(f"a value that starts with {first} must be quoted", no)
    if ": " in text or text.endswith(":"):
        raise Error("a plain value cannot hold ': ': quote it", no)
    if text in _NULLS:
        raise Error("null is not read: give a value, or leave the key out", no)
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    digits = text[1:] if text[0] in "+-" else text
    if digits.isdigit() and digits.isascii():
        return int(text)
    return text


def _flow(text, no):
    """A single-line flow sequence of scalars: [a, "b", 'c']."""
    out = Seq(no)
    inner, i, items, start = text, 1, [], 1
    quote = None
    while i < len(inner):
        c = inner[i]
        if quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = None
        elif quote == "'":
            if c == "'" and inner[i + 1:i + 2] == "'":
                i += 1
            elif c == "'":
                quote = None
        elif c in "'\"" and inner[start:i].strip(" ") == "":
            quote = c
        elif c in "[{":
            raise Error("a list in a [...] list is not read", no)
        elif c == ",":
            items.append(inner[start:i])
            start = i + 1
        elif c == "]":
            if inner[i + 1:].strip(" "):
                raise Error("text after a [...] list", no)
            last = inner[start:i]
            if last.strip(" ") or items:
                items.append(last)
            for item in items:
                item = item.strip(" ")
                if not item:
                    raise Error("an empty item in a [...] list", no)
                out.append(_scalar(item, no))
                out.lines.append(no)
            return out
        i += 1
    raise Error("a [...] list must close on its line", no)
