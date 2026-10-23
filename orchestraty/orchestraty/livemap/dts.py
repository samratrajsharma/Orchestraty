"""The exported API of a TypeScript module, read from a declaration file (``.d.ts``, the
compiler's output) or, as the fallback, from ``.ts`` source, and compared between two
versions of the code. Standard library only; nothing is run.

``read(text, source=False)`` gives a module: ``{"exports": {name: declaration}, "imports":
{local name: [specifier, imported name]}, "stars": [specifiers]}``. A declaration is a plain
dict (functions with their signatures, classes, interfaces, type aliases, variables, enums,
namespaces, re-exports); a signature is its parameters (name, optional, rest, type) and return
type, and a type keeps its text (tokens joined by spaces, so formatting never matters), its
members when it is an object type, its signature when it is a function type, and its members
when it is a union. Reading source skips function bodies and initializers; types not written
there are not known, and nothing is said about them.

``compare(old, new, inputs)`` says what changed in one module's exports, each export rated:

* breaking: an export removed; a parameter removed or made required (a required one added); a
  property or method removed from an exported interface, object type or class; a required
  property added to an interface used as an input (``inputs``: a parameter's type names it);
  an enum member removed; a class made abstract;
* review: a return type changed; a type changed or narrowed (a union that lost members), where
  it can be told; a required property added to an interface not known to be an input; a
  property made optional or read-only; a base class or an export's kind changed;
* compatible: an optional parameter, a new export, an optional property, an enum member added;
  a parameter made optional, or its union type widened.
"""
import re

BREAKING, REVIEW, COMPATIBLE, NONE = "breaking", "review", "compatible", "none"
_RANK = {NONE: 0, COMPATIBLE: 1, REVIEW: 2, BREAKING: 3}
_ID = re.compile(r"[A-Za-z_$\u0080-￿][\w$\u0080-￿]*")
_NUM = re.compile(r"(?:0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?|\.\d[\d_]*(?:[eE][+-]?\d+)?)n?")
_REGEX_AFTER_PUNCT = set("(,=:[!&|?{};+-*%>~^") | {"=>"}       # not "<": "</" closes a JSX tag
_REGEX_AFTER_WORD = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw",
                     "yield", "await", "instanceof"}
_STARTS = {"export", "import", "function", "class", "interface", "type", "const", "let", "var", "enum",
           "declare", "namespace", "module", "abstract", "async", "default"}
_CONTINUES = set("|&,.?:=<([{+-*/%!~^") | {"=>", "...", "?.", "extends", "keyof", "typeof", "in", "of", "is",
                                            "as", "new", "infer", "readonly", "unique", "satisfies"}
_MODIFIERS = {"public", "private", "protected", "readonly", "static", "abstract", "override", "declare",
              "async", "accessor"}
_OPEN = {"(": ")", "[": "]", "{": "}"}
_TIGHT_AFTER = {"(", "[", "<", ".", "...", "?.", "@", "#"}
_TIGHT_BEFORE = {")", "]", ">", ",", ";", ".", "?", ":", "?.", "!"}


def worst(*verdicts):
    return max(verdicts, key=lambda v: _RANK[v]) if verdicts else NONE


# ------------------------------------------------------------------------------ tokens
def _string_end(text, i, quote):
    j, n = i + 1, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
        elif c == quote:
            return j + 1
        elif c == "\n":
            return j
        else:
            j += 1
    return n


def _template_end(text, i):
    j, n = i + 1, len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
        elif c == "`":
            return j + 1
        elif c == "$" and j + 1 < n and text[j + 1] == "{":
            j = _expression_end(text, j + 2)
        else:
            j += 1
    return n


def _expression_end(text, j):
    """Just past the "}" that closes a template's ${ expression."""
    depth, n = 1, len(text)
    while j < n:
        c = text[j]
        if c in "'\"":
            j = _string_end(text, j, c)
            continue
        if c == "`":
            j = _template_end(text, j)
            continue
        if text.startswith("//", j):
            k = text.find("\n", j)
            j = n if k < 0 else k
            continue
        if text.startswith("/*", j):
            k = text.find("*/", j + 2)
            j = n if k < 0 else k + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _regex_end(text, i):
    j, n, in_class = i + 1, len(text), False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return i + 1                     # not a regular expression: a division
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "/":
            j += 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            return j
        j += 1
    return i + 1


def tokens(text, source=False):
    """[(kind, value, line)]: kind is "id", "str" (a string or template literal), "num", "re"
    (a regular expression, in source) or "p" (punctuation: one character, or "=>", "...",
    "?."). Comments and whitespace are dropped. A quote that does not close on its line (an
    apostrophe in JSX text, or a string being typed) is punctuation; a block comment that never
    closes leaves a "/*" token at the end."""
    out, i, n, line = [], 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r\f\v﻿ ":
            i += 1
            continue
        if c == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                k = text.find("\n", i)
                i = n if k < 0 else k
                continue
            if nxt == "*":
                k = text.find("*/", i + 2)
                if k < 0:
                    out.append(("p", "/*", line))
                end = n if k < 0 else k + 2
                line += text.count("\n", i, end)
                i = end
                continue
            if source and nxt != ">":                            # "/>" ends a JSX tag
                prev = out[-1] if out else None
                if prev is None or (prev[0] == "p" and prev[1] in _REGEX_AFTER_PUNCT) or (
                        prev[0] == "id" and prev[1] in _REGEX_AFTER_WORD):
                    end = _regex_end(text, i)
                    if end > i + 1:
                        out.append(("re", text[i:end], line))
                        i = end
                        continue
        if c in "'\"":
            end = _string_end(text, i, c)
            if end - i < 2 or text[end - 1] != c:
                out.append(("p", c, line))
                i += 1
                continue
            out.append(("str", text[i:end], line))
            line += text.count("\n", i, end)
            i = end
            continue
        if c == "`":
            end = _template_end(text, i)
            out.append(("str", text[i:end], line))
            line += text.count("\n", i, end)
            i = end
            continue
        m = _ID.match(text, i)
        if m:
            out.append(("id", m.group(), line))
            i = m.end()
            continue
        if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            m = _NUM.match(text, i)
            end = m.end() if m and m.end() > i else i + 1
            out.append(("num", text[i:end], line))
            i = end
            continue
        for p in ("...", "=>", "?."):
            if text.startswith(p, i) and not (p == "?." and i + 2 < n and text[i + 2].isdigit()):
                out.append(("p", p, line))
                i += len(p)
                break
        else:
            out.append(("p", c, line))
            i += 1
    return out


def balanced(toks):
    """Whether source's brackets match and its template literals and comments close, as in code
    that parses. A file caught mid-edit often has one left open, and what it exports cannot then
    be read: the rest of the file would look like the inside of a function."""
    stack = []
    for kind, v, _line in toks:
        if kind == "str" and v[0] == "`" and (len(v) < 2 or v[-1] != "`") or kind == "p" and v == "/*":
            return False
        if kind == "p" and v in _OPEN:
            stack.append(_OPEN[v])
        elif kind == "p" and v in (")", "]", "}") and (not stack or stack.pop() != v):
            return False
    return not stack


def text_of(toks):
    """A type's text from its tokens: canonical (formatting never matters) and readable."""
    out = []
    for k, (_, v, _line) in enumerate(toks):
        if out and not (out[-1] in _TIGHT_AFTER or v in _TIGHT_BEFORE or (v in ("(", "[", "<") and toks[k - 1][0] == "id")):
            out.append(" ")
        out.append(v)
    return "".join(out).strip()


# ------------------------------------------------------------------------------ reading
class _Reader:
    def __init__(self, toks, source):
        self.t, self.n, self.source = toks, len(toks), source

    def v(self, i):
        return self.t[i][1] if i < self.n else None

    def k(self, i):
        return self.t[i][0] if i < self.n else None

    def line(self, i):
        return self.t[i][2] if i < self.n else (self.t[-1][2] if self.t else 1)

    def close(self, i):
        """Just past the bracket matching the one at i."""
        depth = 0
        while i < self.n:
            kind, v = self.t[i][0], self.t[i][1]
            if kind == "p":
                if v in _OPEN:
                    depth += 1
                elif v in (")", "]", "}"):
                    depth -= 1
                    if depth == 0:
                        return i + 1
            i += 1
        return self.n

    def close_angle(self, i):
        """Just past the ">" matching the "<" at i (types: generics only)."""
        depth = 0
        while i < self.n:
            v = self.t[i][1] if self.t[i][0] == "p" else None
            if v == "<":
                depth += 1
            elif v == ">":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif v in _OPEN:
                i = self.close(i)
                continue
            elif v in (")", "]", "}", ";"):
                return i
            i += 1
        return self.n

    def new_statement(self, i):
        """Whether token i starts a new statement on a new line (source without semicolons)."""
        return (i < self.n and i > 0 and self.t[i][2] > self.t[i - 1][2] and self.k(i) == "id"
                and self.v(i) in _STARTS and not (self.k(i - 1) == "p" and self.v(i - 1) in _CONTINUES)
                and self.v(i - 1) not in _CONTINUES)

    def statement_end(self, i, stop_brace=False):
        """Index of the end of a statement from i: past its ";", or at the next statement on a new
        line, or at a "}" closing the block it is in."""
        while i < self.n:
            kind, v = self.t[i][0], self.t[i][1]
            if kind == "p":
                if v == ";":
                    return i + 1
                if v in _OPEN:
                    i = self.close(i)
                    if stop_brace and v == "{":
                        return i
                    continue
                if v in (")", "]", "}"):
                    return i
            if self.new_statement(i):
                return i
            i += 1
        return i

    def member_start(self, i):
        """Whether token i starts a new member on a new line (a class written without semicolons)."""
        return (i > 0 and self.t[i][2] > self.t[i - 1][2] and self.v(i - 1) not in _CONTINUES
                and (self.k(i) in ("id", "str", "num") or self.v(i) in ("[", "#", "@")))

    # ---- types
    def type_end(self, i, stops=(",", ")", "]", "}", ";", "="), body=False, newline=False):
        """End index of the type starting at i (depth 0): at one of `stops`, or where a new
        statement (with `newline`, a new member) starts on a new line. With `body`, a "{" after
        a complete type starts a function body; where a type is expected, "{" starts an object
        type."""
        start, expecting = i, True
        while i < self.n:
            kind, v = self.t[i][0], self.t[i][1]
            if i > start and (self.new_statement(i) or (newline and self.member_start(i))):
                return i
            if kind == "p":
                if v == "{":
                    if not expecting and body:
                        return i
                    i = self.close(i)
                    expecting = False
                    continue
                if v in stops:
                    return i
                if v in ("(", "["):
                    i = self.close(i)
                    expecting = False
                    continue
                if v == "<":
                    i = self.close_angle(i)
                    expecting = False
                    continue
                expecting = v in ("|", "&", "=>", ":", "?", ",", "...", "-", "@", ".")
            else:
                expecting = kind == "id" and v in ("keyof", "typeof", "readonly", "unique", "infer", "extends", "is",
                                                   "asserts", "new", "abstract")
            i += 1
        return i

    def type_info(self, a, b):
        """{"text", "union", "members", "sig"} of the type tokens a..b."""
        toks = self.t[a:b]
        info = {"text": text_of(toks), "union": None, "members": None, "sig": None}
        if not toks:
            return info
        if self.v(a) == "{" and self.close(a) == b:
            info["members"] = self.members(a + 1, b - 1)
            return info
        sig = self.function_type(a, b)
        if sig is not None:
            info["sig"] = sig
            return info
        parts, depth, cur, conditional = [], 0, [], False
        for kind, v, line in toks:
            if kind == "p" and v in "([{<":
                depth += 1
            elif kind == "p" and v in ")]}>":
                depth -= 1
            if depth == 0 and kind == "p" and v in ("=>", "?", ":"):
                conditional = True
            if depth == 0 and kind == "p" and v == "|":
                if cur:
                    parts.append(cur)
                cur = []
                continue
            cur.append((kind, v, line))
        if cur:
            parts.append(cur)
        if len(parts) > 1 and not conditional:
            info["union"] = sorted({text_of(p) for p in parts})
        return info

    def function_type(self, a, b):
        """The signature of a function type "(params) => R" (or "<T>(params) => R", "new ..."),
        or None."""
        i = a
        if self.v(i) == "abstract":
            i += 1
        if self.v(i) == "new":
            i += 1
        tparams = ""
        if self.v(i) == "<":
            j = self.close_angle(i)
            tparams = text_of(self.t[i:j])
            i = j
        if self.v(i) != "(":
            return None
        j = self.close(i)
        if self.v(j) != "=>":
            return None
        return {"params": self.params(i + 1, j - 1), "ret": self.type_info(j + 1, b)["text"], "tparams": tparams}

    # ---- parameters and members
    def params(self, a, b):
        out, i = [], a
        while i < b:
            j = i
            while j < b and self.v(j) != ",":
                if self.v(j) in _OPEN:
                    j = self.close(j)
                    continue
                if self.v(j) == "<":
                    j = self.close_angle(j)
                    continue
                j += 1
            p = self.param(i, j)
            if p is not None:
                out.append(p)
            i = j + 1
        return out

    def param(self, a, b):
        i = a
        while i < b and self.v(i) == "@":                    # decorators
            i += 1
            while i < b and (self.k(i) == "id" or self.v(i) == "."):
                i += 1
            if self.v(i) == "(":
                i = self.close(i)
        while i < b and self.k(i) == "id" and self.v(i) in _MODIFIERS and self.v(i + 1) not in (":", "?", ",", ")", "="):
            i += 1
        if i >= b:
            return None
        rest = self.v(i) == "..."
        if rest:
            i += 1
        if self.v(i) in ("{", "["):
            j = self.close(i)
            name = "{...}" if self.v(i) == "{" else "[...]"
        else:
            name, j = self.v(i) or "", i + 1
        optional = self.v(j) == "?"
        if optional:
            j += 1
        typ = {"text": "", "union": None, "members": None, "sig": None}
        if self.v(j) == ":":
            end = min(self.type_end(j + 1, stops=(",", "=")), b)
            typ = self.type_info(j + 1, end)
            j = end
        if j < b and self.v(j) == "=":
            optional = True
        return {"name": name, "optional": optional, "rest": rest, "type": typ}

    def signature(self, i, end, arrow=False):
        """(signature, index past it) for "<T>(params): R" at i; the return type stops at a body
        (with `arrow`, at the "=>" of an arrow function)."""
        tparams = ""
        if self.v(i) == "<":
            j = self.close_angle(i)
            tparams = text_of(self.t[i:j])
            i = j
        if self.v(i) != "(":
            return None, i
        j = self.close(i)
        params = self.params(i + 1, j - 1)
        ret = ""
        if self.v(j) == ":":
            r = self.type_end(j + 1, stops=(";", ",", "}", ")", "=") + (("=>",) if arrow else ()), body=True,
                              newline=True)
            r = min(r, end)
            ret = self.type_info(j + 1, r)["text"]
            j = r
        return {"params": params, "ret": ret, "tparams": tparams}, j

    def members(self, a, b, cls=False):
        """{name: member} of an interface, object type or class body (tokens a..b); a
        constructor is "constructor"; private members, index signatures and static blocks are
        left out."""
        out, i = {}, a
        while i < b:
            if self.v(i) in (";", ","):
                i += 1
                continue
            start = i
            mods = set()
            while (i < b and self.k(i) == "id" and self.v(i) in _MODIFIERS | {"get", "set"}
                   and self.v(i + 1) not in ("(", ":", "?", "!", ";", ",", "=", "<")
                   and not (self.t[i + 1][2] > self.t[i][2] if i + 1 < b else False)):
                mods.add(self.v(i))
                i += 1
            if i >= b:
                break
            if cls and self.v(i) == "static" and self.v(i + 1) == "{":
                i = self.close(i + 1)
                continue
            private = "private" in mods or self.v(i) == "#"
            if self.v(i) == "#":
                i += 1
            if self.v(i) == "[":                              # an index signature or a computed name
                j = self.close(i)
                end = self.member_end(j, b)
                i = end
                continue
            if self.v(i) in ("(", "<"):                       # a call signature
                _, j = self.signature(i, b)
                i = self.member_end(j, b)
                continue
            if self.v(i) == "new" and self.v(i + 1) in ("(", "<"):   # a construct signature
                _, j = self.signature(i + 1, b)
                i = self.member_end(j, b)
                continue
            name = self.v(i)
            if self.k(i) == "str":
                name = name[1:-1]
            elif self.k(i) not in ("id", "num"):
                i = self.member_end(i + 1, b)
                continue
            line = self.line(i)
            i += 1
            optional = self.v(i) == "?"
            if optional or self.v(i) == "!":
                i += 1
            if self.v(i) in ("(", "<"):
                sig, j = self.signature(i, b)
                if self.v(j) == "{":
                    j = self.close(j)
                i = self.member_end(j, b)
                if sig is None or private:
                    continue
                if mods & {"get", "set"}:                    # an accessor is a property
                    known = out.get(name)
                    typ = ({"text": sig["ret"], "union": None, "members": None, "sig": None} if "get" in mods else
                           sig["params"][0]["type"] if sig["params"] else {"text": "", "union": None, "members": None,
                                                                          "sig": None})
                    if known is not None and known["kind"] == "property" and known["type"]["text"] and "set" in mods:
                        typ = known["type"]
                    out[name] = {"kind": "property", "optional": False, "type": typ, "sigs": [], "line": line,
                                 "static": "static" in mods,
                                 "readonly": "set" not in mods and not (known is not None and not known["readonly"])}
                    continue
                key = "constructor" if name == "constructor" and cls else name
                m = out.get(key)
                if m is None or m["kind"] != "method":
                    m = out[key] = {"kind": "method", "optional": optional, "readonly": False, "sigs": [], "type": None,
                                    "static": "static" in mods, "line": line}
                m["sigs"].append(sig)
                continue
            typ = {"text": "", "union": None, "members": None, "sig": None}
            if self.v(i) == ":":
                end = min(self.type_end(i + 1, stops=(";", ",", "}", "="), newline=True), b)
                typ = self.type_info(i + 1, end)
                i = end
            if self.v(i) == "=":                            # an initializer (source)
                i = self.member_end(i, b)
            else:
                i = self.member_end(i, b)
            if private:
                continue
            out[name] = {"kind": "property", "optional": optional, "readonly": "readonly" in mods or (
                "get" in mods and "set" not in mods), "type": typ, "sigs": [], "static": "static" in mods, "line": line}
            if i == start:
                i += 1
        return out

    def member_end(self, i, b):
        """Past the end of a member: its ";" or ",", or where the next member starts on a new line."""
        while i < b:
            v = self.v(i)
            if v in (";", ","):
                return i + 1
            if v in _OPEN:
                i = self.close(i)
                continue
            if i > 0 and self.t[i][2] > self.t[i - 1][2] and self.v(i - 1) not in _CONTINUES and (
                    self.k(i) in ("id", "str", "num") or v in ("[", "#", "(")):
                return i
            i += 1
        return b

    # ---- declarations
    def declaration(self, i):
        """(name, declaration or None, index past it) for a declaration at i (after export/declare)."""
        start_line = self.line(i)
        while self.v(i) in ("declare", "abstract", "async", "const") and self.v(i + 1) in (
                "function", "class", "enum", "abstract", "namespace", "module", "async", "interface"):
            if self.v(i) == "abstract" and self.v(i + 1) == "class":
                break
            i += 1
        v = self.v(i)
        if v == "abstract" and self.v(i + 1) == "class":
            name, d, j = self.class_decl(i + 1)
            if d is not None:
                d["abstract"] = True
            return name, d, j
        if v == "function":
            return self.function_decl(i)
        if v == "class":
            return self.class_decl(i)
        if v == "interface":
            return self.interface_decl(i)
        if v == "type" and self.k(i + 1) == "id":
            return self.type_decl(i)
        if v == "enum":
            return self.enum_decl(i)
        if v in ("namespace", "module") and self.k(i + 1) in ("id", "str"):
            return self.namespace_decl(i)
        if v in ("const", "let", "var"):
            return self.variable_decl(i)
        if v == "global":
            return None, None, self.statement_end(i + 1, stop_brace=True)
        return None, None, self.statement_end(i)

    def function_decl(self, i):
        line = self.line(i)
        i += 1
        if self.v(i) == "*":
            i += 1
        name = self.v(i) if self.k(i) == "id" else "default"
        if self.k(i) == "id":
            i += 1
        sig, j = self.signature(i, self.n)
        if self.v(j) == "{":
            j = self.close(j)
        elif self.v(j) == ";":
            j += 1
        if sig is None:
            return name, None, self.statement_end(j)
        return name, {"kind": "function", "sigs": [sig], "line": line}, j

    def class_decl(self, i):
        line = self.line(i)
        i += 1
        name = "default"
        if self.k(i) == "id" and self.v(i) not in ("extends", "implements"):
            name = self.v(i)
            i += 1
        tparams = ""
        if self.v(i) == "<":
            j = self.close_angle(i)
            tparams = text_of(self.t[i:j])
            i = j
        extends, implements = "", []
        while i < self.n and self.v(i) != "{":
            if self.v(i) == "extends":
                j = self.heritage_end(i + 1)
                extends = text_of(self.t[i + 1:j])
                i = j
            elif self.v(i) == "implements":
                j = self.heritage_end(i + 1)
                implements = [x.strip() for x in text_of(self.t[i + 1:j]).split(",") if x.strip()]
                i = j
            else:
                i += 1
        if i >= self.n:
            return name, None, i
        j = self.close(i)
        members = self.members(i + 1, j - 1, cls=True)
        ctor = members.pop("constructor", None)
        return name, {"kind": "class", "members": members, "ctor": ctor["sigs"] if ctor else [], "extends": extends,
                      "implements": implements, "abstract": False, "tparams": tparams, "line": line}, j

    def heritage_end(self, i):
        while i < self.n and self.v(i) not in ("{", "implements", "extends"):
            if self.v(i) == "<":
                i = self.close_angle(i)
                continue
            if self.v(i) in ("(", "["):
                i = self.close(i)
                continue
            i += 1
        return i

    def interface_decl(self, i):
        line = self.line(i)
        name = self.v(i + 1)
        i += 2
        tparams = ""
        if self.v(i) == "<":
            j = self.close_angle(i)
            tparams = text_of(self.t[i:j])
            i = j
        extends = []
        if self.v(i) == "extends":
            j = self.heritage_end(i + 1)
            extends = [x.strip() for x in text_of(self.t[i + 1:j]).split(",") if x.strip()]
            i = j
        while i < self.n and self.v(i) != "{":
            i += 1
        j = self.close(i)
        return name, {"kind": "interface", "members": self.members(i + 1, j - 1), "extends": extends,
                      "tparams": tparams, "line": line}, j

    def type_decl(self, i):
        line = self.line(i)
        name = self.v(i + 1)
        i += 2
        tparams = ""
        if self.v(i) == "<":
            j = self.close_angle(i)
            tparams = text_of(self.t[i:j])
            i = j
        if self.v(i) != "=":
            return name, None, self.statement_end(i)
        end = self.type_end(i + 1, stops=(";", ")", "]", "}"))
        info = self.type_info(i + 1, end)
        j = end + 1 if self.v(end) == ";" else end
        return name, {"kind": "type", "type": info, "tparams": tparams, "line": line}, j

    def enum_decl(self, i):
        line = self.line(i)
        name = self.v(i + 1)
        i += 2
        while i < self.n and self.v(i) != "{":
            i += 1
        j = self.close(i)
        members, k = [], i + 1
        while k < j - 1:
            if self.k(k) in ("id", "str"):
                members.append(self.v(k) if self.k(k) == "id" else self.v(k)[1:-1])
                k += 1
                while k < j - 1 and self.v(k) != ",":
                    if self.v(k) in _OPEN:
                        k = self.close(k)
                        continue
                    k += 1
            k += 1
        return name, {"kind": "enum", "members": sorted(set(members)), "line": line}, j

    def namespace_decl(self, i):
        line = self.line(i)
        name = self.v(i + 1)
        i += 2
        while self.v(i) == ".":                               # namespace A.B.C
            name += "." + (self.v(i + 1) or "")
            i += 2
        if self.v(i) != "{":
            return name, None, self.statement_end(i)
        j = self.close(i)
        inner = _Reader(self.t[i + 1:j - 1], self.source).module(ambient=not self.source)
        return name, {"kind": "namespace", "exports": inner["exports"], "line": line}, j

    def variable_decl(self, i):
        """The first declarator of const/let/var (an export list names the others)."""
        line = self.line(i)
        const = self.v(i) == "const"
        i += 1
        if self.v(i) in ("{", "["):                          # destructuring: names not read
            return None, None, self.statement_end(i)
        name = self.v(i)
        i += 1
        if self.v(i) == "!":
            i += 1
        typ = {"text": "", "union": None, "members": None, "sig": None}
        if self.v(i) == ":":
            end = self.type_end(i + 1, stops=(",", ";", "=", ")", "]", "}"))
            typ = self.type_info(i + 1, end)
            i = end
        if self.v(i) == "=":
            init = i + 1
            sig = self.arrow(init) if self.source else None
            if sig is not None and not typ["text"]:
                typ = {"text": "", "union": None, "members": None, "sig": sig}
            elif not typ["text"] and self.k(init) in ("str", "num") and not self.source:
                typ = {"text": self.v(init), "union": None, "members": None, "sig": None}
        return name, {"kind": "variable", "type": typ, "const": const, "line": line}, self.statement_end(i)

    def arrow(self, i):
        """The signature of an arrow function or function expression at i (source), or None."""
        if self.v(i) == "async":
            i += 1
        if self.v(i) == "function":
            i += 1
            if self.v(i) == "*":
                i += 1
            if self.k(i) == "id":
                i += 1
            sig, _ = self.signature(i, self.n)
            return sig
        if self.k(i) == "id" and self.v(i + 1) == "=>":
            return {"params": [{"name": self.v(i), "optional": False, "rest": False,
                                "type": {"text": "", "union": None, "members": None, "sig": None}}],
                    "ret": "", "tparams": ""}
        if self.v(i) not in ("(", "<"):
            return None
        sig, j = self.signature(i, self.n, arrow=True)
        if sig is None:
            return None
        return sig if self.v(j) == "=>" else None

    def export_list(self, i):
        """[(exported name, local name)], index past the "}"."""
        j = self.close(i)
        out, k = [], i + 1
        while k < j - 1:
            if self.v(k) == "type":
                k += 1
            if self.k(k) in ("id", "str"):
                local = self.v(k) if self.k(k) == "id" else self.v(k)[1:-1]
                exported = local
                if self.v(k + 1) == "as" and self.k(k + 2) in ("id", "str"):
                    exported = self.v(k + 2) if self.k(k + 2) == "id" else self.v(k + 2)[1:-1]
                    k += 2
                out.append((exported, local))
            k += 1
            while k < j - 1 and self.v(k) != ",":
                k += 1
            k += 1
        return out, j

    def module(self, ambient=False):
        exports, locals_, stars, imports, aliases = {}, {}, [], {}, []
        i = 0
        while i < self.n:
            v, kind = self.v(i), self.k(i)
            if kind != "id":
                i = self.statement_end(i + 1) if v not in ("}", ")", "]") else i + 1
                continue
            if v == "import" and self.v(i + 1) != "(":
                i = self.import_decl(i, imports)
                continue
            if v == "export":
                i = self.export_decl(i + 1, exports, locals_, stars, aliases)
                continue
            if v in ("declare", "function", "class", "interface", "type", "const", "let", "var", "enum", "abstract",
                     "async", "namespace", "module") and (v != "type" or self.k(i + 1) == "id"):
                j = i + 1 if v == "declare" else i
                name, d, end = self.declaration(j)
                if name and d is not None:
                    self.merge(exports if ambient else locals_, name, d)
                i = max(end, i + 1)
                continue
            i = self.statement_end(i + 1)
        for exported, local in aliases:
            if local in locals_:
                exports[exported] = locals_[local]
            elif local in imports:
                spec, name = imports[local]
                exports[exported] = {"kind": "reexport", "from": spec, "name": name, "line": 0}
        return {"exports": exports, "imports": imports, "stars": stars}

    @staticmethod
    def merge(table, name, d):
        """Overloads (and a namespace merged with a function) keep every signature."""
        old = table.get(name)
        if old is not None and old["kind"] == "function" and d["kind"] == "function":
            old["sigs"].extend(d["sigs"])
        elif old is None or old["kind"] == "namespace":
            table[name] = d

    def import_decl(self, i, imports):
        """Read an import statement into {local: [specifier, imported name]}; index past it."""
        j = i + 1
        if self.v(j) == "type":
            j += 1
        names, spec = [], None
        while j < self.n:
            v, kind = self.v(j), self.k(j)
            if kind == "str":
                spec = v[1:-1]
                j += 1
                break
            if v == "{":
                end = self.close(j)
                k = j + 1
                while k < end - 1:
                    if self.v(k) == "type":
                        k += 1
                    if self.k(k) in ("id", "str"):
                        orig = self.v(k) if self.k(k) == "id" else self.v(k)[1:-1]
                        local = orig
                        if self.v(k + 1) == "as":
                            local = self.v(k + 2)
                            k += 2
                        names.append((local, orig))
                    k += 1
                    while k < end - 1 and self.v(k) != ",":
                        k += 1
                    k += 1
                j = end
                continue
            if v == "*" and self.v(j + 1) == "as":
                names.append((self.v(j + 2), "*"))
                j += 3
                continue
            if v == "=" and self.v(j + 1) == "require" and self.k(j + 3) == "str":   # import x = require("y")
                spec = self.v(j + 3)[1:-1]
                names.append((self.v(j - 1), "*"))
                j += 5
                break
            if kind == "id" and v not in ("from", "type") and self.v(j + 1) in (",", "from"):
                names.append((v, "default"))
            if v in (";",) or (j > i + 1 and self.new_statement(j)):
                break
            j += 1
        if spec is not None:
            for local, orig in names:
                imports[local] = [spec, orig]
        if self.v(j) in ("assert", "with") and self.v(j + 1) == "{":
            j = self.close(j + 1)
        if self.v(j) == ";":
            j += 1
        return j

    def export_decl(self, i, exports, locals_, stars, aliases):
        v = self.v(i)
        if v == "default":
            i += 1
            if self.v(i) in ("function", "class", "abstract", "async", "interface"):
                _, d, j = self.declaration(i)
                if d is not None:
                    exports["default"] = d
                return max(j, i + 1)
            if self.k(i) == "id" and (self.v(i + 1) in (";", None) or self.new_statement(i + 1)):
                aliases.append(("default", self.v(i)))
                return i + 2 if self.v(i + 1) == ";" else i + 1
            sig = self.arrow(i) if self.source else None
            exports["default"] = {"kind": "variable", "type": {"text": "", "union": None, "members": None, "sig": sig},
                                  "const": True, "line": self.line(i)}
            return self.statement_end(i)
        if v == "=":                                          # export = name
            if self.k(i + 1) == "id":
                aliases.append(("=", self.v(i + 1)))
            return self.statement_end(i + 1)
        if v == "as" and self.v(i + 1) == "namespace":        # export as namespace X (UMD)
            return self.statement_end(i)
        if v == "*":
            j = i + 1
            alias = None
            if self.v(j) == "as":
                alias = self.v(j + 1)
                j += 2
            if self.v(j) == "from" and self.k(j + 1) == "str":
                spec = self.v(j + 1)[1:-1]
                if alias:
                    exports[alias] = {"kind": "reexport", "from": spec, "name": "*", "line": self.line(i)}
                else:
                    stars.append(spec)
                j += 2
            return j + 1 if self.v(j) == ";" else j
        if v == "type" and self.v(i + 1) == "{":
            i += 1
            v = "{"
        if v == "{":
            items, j = self.export_list(i)
            if self.v(j) == "from" and self.k(j + 1) == "str":
                spec = self.v(j + 1)[1:-1]
                for exported, local in items:
                    exports[exported] = {"kind": "reexport", "from": spec, "name": local, "line": self.line(i)}
                j += 2
            else:
                aliases.extend(items)
            return j + 1 if self.v(j) == ";" else j
        if v == "import":                                     # export import A = B.C
            return self.statement_end(i)
        j = i + 1 if v == "declare" else i
        name, d, end = self.declaration(j)
        if name and d is not None:
            self.merge(exports, name, d)
            if d["kind"] == "variable" and self.v(end - 1) != ";":
                pass
        return max(end, i + 1)


def read(text, source=False):
    """The module a declaration file (or, with source, a .ts file) declares: {"exports", "imports",
    "stars"}. Never raises: what it cannot read is left out."""
    return read_tokens(tokens(text, source), source)


def read_tokens(toks, source=False):
    """read() from tokens already made."""
    try:
        return _Reader(toks, source).module()
    except (IndexError, TypeError, RecursionError):
        return {"exports": {}, "imports": {}, "stars": []}


# ------------------------------------------------------------------------------ comparing
def _param_type_change(name, old, new, as_input=True):
    """(verdict, reason) of a parameter's (or property's) type change."""
    if not old["text"] or not new["text"] or old["text"] == new["text"]:
        return NONE, ""
    ou, nu = old.get("union"), new.get("union")
    if ou and nu and set(nu) < set(ou):
        return REVIEW, f"{name}: type narrowed"
    if ou and nu and set(nu) > set(ou) or (ou and not nu and new["text"] in ou):
        return (COMPATIBLE if as_input else REVIEW), f"{name}: type widened" if ou and nu else f"{name}: type narrowed"
    if nu and old["text"] in nu:
        return (COMPATIBLE if as_input else REVIEW), f"{name}: type widened"
    return REVIEW, f"{name}: type changed"


def _params(old, new, owner=""):
    """(verdict, reasons) between two parameter lists."""
    pre = f"{owner}: " if owner else ""
    po = [p for p in old if p["name"] != "this"]
    pn = [p for p in new if p["name"] != "this"]
    verdict, reasons = NONE, []

    def add(v, r):
        nonlocal verdict
        verdict = worst(verdict, v)
        reasons.append(pre + r)
    for k, p in enumerate(po):
        q = pn[k] if k < len(pn) else None
        if q is None:
            add(BREAKING, f"parameter {p['name']} removed")
            continue
        if p["rest"] and not q["rest"]:
            add(BREAKING, f"rest parameter {p['name']} removed")
            continue
        if q["rest"] and not p["rest"]:
            add(REVIEW, f"parameter {p['name']} became rest parameter {q['name']}")
            continue
        if p["optional"] and not q["optional"] and not q["rest"]:
            add(BREAKING, f"parameter {q['name']} made required")
        elif q["optional"] and not p["optional"]:
            add(COMPATIBLE, f"parameter {q['name']} made optional")
        v, r = _param_type_change(f"parameter {q['name']}", p["type"], q["type"])
        if r:
            add(v, r)
    for q in pn[len(po):]:
        if q["rest"]:
            add(COMPATIBLE, f"rest parameter {q['name']} added")
        elif q["optional"]:
            add(COMPATIBLE, f"optional parameter {q['name']} added")
        else:
            add(BREAKING, f"required parameter {q['name']} added")
    return verdict, reasons


def _signature(old, new, owner=""):
    pre = f"{owner}: " if owner else ""
    verdict, reasons = _params(old["params"], new["params"], owner)
    if old["ret"] and new["ret"] and old["ret"] != new["ret"]:
        verdict = worst(verdict, REVIEW)
        reasons.append(pre + "return type changed")
    if old.get("tparams", "") != new.get("tparams", ""):
        verdict = worst(verdict, REVIEW)
        reasons.append(pre + "type parameters changed")
    return verdict, reasons


def _sig_text(sig):
    params = ", ".join(("..." if p["rest"] else "") + p["name"] + ("?" if p["optional"] else "")
                       + (f": {p['type']['text']}" if p["type"]["text"] else "") for p in sig["params"])
    return f"{sig.get('tparams', '')}({params})" + (f": {sig['ret']}" if sig["ret"] else "")


def _signatures(old, new, owner=""):
    """Overloads: identical signatures match first; each other old signature against the new
    one it fares best with. An old overload left without one was removed; a new one is
    compatible."""
    pre = f"{owner}: " if owner else ""
    if not old and not new:
        return NONE, []
    if not new:
        return BREAKING, [pre + "no longer callable"]
    keys_old, keys_new = {_sig_text(s) for s in old}, {_sig_text(s) for s in new}
    old_left = [s for s in old if _sig_text(s) not in keys_new]
    new_left = [s for s in new if _sig_text(s) not in keys_old]
    verdict, reasons = NONE, []
    for o in old_left:
        if not new_left:
            verdict = BREAKING
            reasons.append(f"{pre}overload {_sig_text(o)} removed")
            continue
        best = min(new_left, key=lambda n: (_RANK[_signature(o, n, owner)[0]], len(_signature(o, n, owner)[1])))
        new_left.remove(best)
        v, r = _signature(o, best, owner)
        verdict = worst(verdict, v)
        reasons.extend(x for x in r if x not in reasons)
    for n in new_left:
        verdict = worst(verdict, COMPATIBLE)
        reasons.append(f"{pre}overload {_sig_text(n)} added")
    return verdict, reasons


def _members(old, new, is_input, owner, cls=False):
    verdict, reasons = NONE, []

    def add(v, r):
        nonlocal verdict
        verdict = worst(verdict, v)
        reasons.append(r)
    for name in sorted(set(old) - set(new)):
        what = "method" if old[name]["kind"] == "method" else "property"
        add(BREAKING, f"{what} {owner}.{name} removed")
    for name in sorted(set(new) - set(old)):
        m = new[name]
        what = "method" if m["kind"] == "method" else "property"
        if m["optional"] or cls:
            add(COMPATIBLE, f"{'optional ' if m['optional'] else ''}{what} {owner}.{name} added")
        elif is_input:
            add(BREAKING, f"required {what} {owner}.{name} added")
        else:
            add(REVIEW, f"required {what} {owner}.{name} added")
    for name in sorted(set(old) & set(new)):
        a, b = old[name], new[name]
        label = f"{owner}.{name}"
        if a["kind"] != b["kind"]:
            add(REVIEW, f"{label} changed from a {a['kind']} to a {b['kind']}")
            continue
        if a["kind"] == "method":
            v, r = _signatures(a["sigs"], b["sigs"], label)
            for x in r:
                add(v, x)
            if a["optional"] != b["optional"]:
                add(REVIEW, f"{label} made {'optional' if b['optional'] else 'required'}")
            continue
        if a["optional"] and not b["optional"]:
            add(BREAKING if is_input else (COMPATIBLE if cls else REVIEW), f"property {label} made required")
        elif b["optional"] and not a["optional"]:
            add(COMPATIBLE if is_input and not cls else REVIEW, f"property {label} made optional")
        if b["readonly"] and not a["readonly"]:
            add(REVIEW, f"property {label} made read-only")
        sa, sb = (a["type"] or {}).get("sig"), (b["type"] or {}).get("sig")
        if sa and sb:
            v, r = _signature(sa, sb, label)
            for x in r:
                add(v, x)
            continue
        v, r = _param_type_change(f"property {label}", a["type"] or {"text": ""}, b["type"] or {"text": ""},
                                  as_input=is_input)
        if r:
            add(v, r)
    return verdict, reasons


def _callable(d):
    if d["kind"] == "function":
        return d["sigs"]
    if d["kind"] in ("variable", "type") and (d["type"] or {}).get("sig"):
        return [d["type"]["sig"]]
    return None


def _shape(d):
    if d["kind"] == "interface":
        return d["members"]
    if d["kind"] == "type" and d["type"].get("members") is not None:
        return d["type"]["members"]
    return None


def compare_export(name, old, new, is_input=False):
    """(verdict, reasons) of one export's change."""
    oc, nc = _callable(old), _callable(new)
    if oc is not None and nc is not None:
        return _signatures(oc, nc)
    os_, ns = _shape(old), _shape(new)
    if os_ is not None and ns is not None:
        v, r = _members(os_, ns, is_input, name)
        if old.get("tparams", "") != new.get("tparams", ""):
            v, r = worst(v, REVIEW), r + ["type parameters changed"]
        if old["kind"] == "interface" and new["kind"] == "interface" and old["extends"] != new["extends"]:
            v, r = worst(v, REVIEW), r + ["extends changed"]
        return v, r
    if old["kind"] != new["kind"]:
        return REVIEW, [f"changed from a {old['kind']} to a {new['kind']}"]
    kind = old["kind"]
    if kind == "class":
        verdict, reasons = NONE, []
        if new["abstract"] and not old["abstract"]:
            verdict, reasons = BREAKING, ["made abstract"]
        v, r = _signatures(old["ctor"] or [{"params": [], "ret": "", "tparams": ""}],
                           new["ctor"] or [{"params": [], "ret": "", "tparams": ""}], "constructor")
        verdict, reasons = worst(verdict, v), reasons + r
        v, r = _members(old["members"], new["members"], False, name, cls=True)
        verdict, reasons = worst(verdict, v), reasons + r
        if old["extends"] != new["extends"]:
            verdict, reasons = worst(verdict, REVIEW), reasons + ["base class changed"]
        if old.get("tparams", "") != new.get("tparams", ""):
            verdict, reasons = worst(verdict, REVIEW), reasons + ["type parameters changed"]
        return verdict, reasons
    if kind == "enum":
        gone = sorted(set(old["members"]) - set(new["members"]))
        added = sorted(set(new["members"]) - set(old["members"]))
        reasons = [f"member {m} removed" for m in gone] + [f"member {m} added" for m in added]
        return (BREAKING if gone else COMPATIBLE if added else NONE), reasons
    if kind == "namespace":
        verdict, reasons = NONE, []
        for sub in sorted(set(old["exports"]) | set(new["exports"])):
            a, b = old["exports"].get(sub), new["exports"].get(sub)
            if a is None:
                verdict, reasons = worst(verdict, COMPATIBLE), reasons + [f"{sub} added"]
            elif b is None:
                verdict, reasons = BREAKING, reasons + [f"{sub} removed"]
            else:
                v, r = compare_export(f"{name}.{sub}", a, b)
                verdict, reasons = worst(verdict, v), reasons + [f"{sub}: {x}" for x in r]
        return verdict, reasons
    if kind == "reexport":
        if (old["from"], old["name"]) != (new["from"], new["name"]):
            return REVIEW, ["re-exported from elsewhere"]
        return NONE, []
    if kind in ("type", "variable"):
        v, r = _param_type_change("type", old["type"], new["type"], as_input=False)
        if old.get("tparams", "") != new.get("tparams", ""):
            v, r = worst(v, REVIEW), ([r] if r else []) + ["type parameters changed"]
            return v, r
        return v, [r] if r else []
    return NONE, []


def inputs(module):
    """The names of a module's exported interfaces and object types used as an input: named in
    the type of a parameter of one of its exported functions, methods or constructors."""
    names = set()

    def from_type(t):
        if t and t.get("text"):
            names.update(_ID.findall(t["text"]))

    def from_sigs(sigs):
        for s in sigs or ():
            for p in s["params"]:
                from_type(p["type"])
    for d in module["exports"].values():
        from_sigs(_callable(d))
        if d["kind"] == "class":
            from_sigs(d["ctor"])
            for m in d["members"].values():
                from_sigs(m["sigs"])
        if _shape(d):
            for m in _shape(d).values():
                from_sigs(m["sigs"])
    return names


def compare(old, new, used_as_input=frozenset()):
    """[(export name, kind, verdict, reasons)] between two versions of one module (from read);
    kind is "added", "removed" or "modified". An export not in `used_as_input` (beyond the
    module's own inputs) is not taken as an input."""
    found = set(used_as_input) | inputs(old) | inputs(new)
    out = []
    oe, ne = old["exports"], new["exports"]
    for name in sorted(set(oe) - set(ne)):
        out.append((name, "removed", BREAKING, ["export removed"]))
    for name in sorted(set(ne) - set(oe)):
        out.append((name, "added", COMPATIBLE, ["export added"]))
    for name in sorted(set(oe) & set(ne)):
        verdict, reasons = compare_export(name, oe[name], ne[name], is_input=name in found)
        if reasons:
            out.append((name, "modified", verdict, reasons))
    if sorted(old.get("stars", [])) != sorted(new.get("stars", [])):
        gone = sorted(set(old.get("stars", [])) - set(new.get("stars", [])))
        if gone:
            out.append(("*", "modified", BREAKING, [f"export * from {s} removed" for s in gone]))
    return out
