"""Name-only references: places that use a function's name without a resolved edge.

Status: throwaway (Phase 0). Not part of the product.

    name_only_references(change, files) -> [NameRef(file, line, column, kind, scope, ...)]

`files` maps repo-relative paths to their text (for example the head commit's files). In
Python files the search uses the tokenizer, so a name inside a comment or a string never
counts, and a definition of the name is not a reference. Each hit gets a kind:

  decorator  @name or @pkg.name              (a decorator use)
  call       name(...)                       (a call the resolver did not link)
  attribute  obj.name, self.name, super().name (a method reached through an object)
  import     in an import statement
  reference  any other use: a callback such as target=name or key=name, or an assignment

and a scope (module level, class body or function), from the syntax tree. Other files are
searched as text, as whole words, with kind "text". Lines that hold one of the change's
resolved call sites are skipped: those are already known.

Every result is labelled "name-only" and "low" confidence. It shows callers the resolver
cannot link yet (decorator uses, callbacks, module-level callers, inherited methods and
super() calls), so that they are listed rather than silently missing. Python 3.9 or later.
"""
import ast
import io
import keyword
import re
import tokenize
from dataclasses import dataclass

from effects_record import FunctionChange

SKIP = {tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT}     # NEWLINE ends a statement


@dataclass(frozen=True)
class NameRef:
    file: str
    line: int
    column: int
    kind: str
    scope: str
    label: str = "name-only"
    confidence: str = "low"


def _scopes(text):
    """[(start, end, 'function'|'class body')] for every def and class, or None if it won't parse."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # From the def or class line: decorators run in the enclosing scope.
            out.append((n.lineno, n.end_lineno, "class body" if isinstance(n, ast.ClassDef) else "function"))
    return out


def _scope_of(scopes, line):
    if scopes is None:
        return "unknown"
    inner = [s for s in scopes if s[0] <= line <= s[1]]
    if not inner:
        return "module level"
    return max(inner, key=lambda s: s[0])[2]       # the innermost starts last


def _python_refs(path, text, name):
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type not in SKIP]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    scopes = _scopes(text)
    refs = []
    statement_start = 0
    for i, tok in enumerate(tokens):
        if i and tokens[i - 1].type == tokenize.NEWLINE:
            statement_start = i
        if tok.type != tokenize.NAME or tok.string != name or keyword.iskeyword(name):
            continue
        prev = tokens[i - 1].string if i else ""
        if prev in ("def", "class"):
            continue                                     # a definition, not a use
        first = tokens[statement_start].string if statement_start < len(tokens) else ""
        j = i - 1                                        # walk back over a dotted chain: @a.b.name
        while j > 0 and tokens[j].string == "." and tokens[j - 1].type == tokenize.NAME:
            j -= 2
        nxt = tokens[i + 1].string if i + 1 < len(tokens) else ""
        if first in ("import", "from"):
            kind = "import"
        elif j >= 0 and tokens[j].string == "@":
            kind = "decorator"
        elif prev == ".":
            kind = "attribute"
        elif nxt == "(":
            kind = "call"
        else:
            kind = "reference"
        refs.append(NameRef(path, tok.start[0], tok.start[1], kind, _scope_of(scopes, tok.start[0])))
    return refs


def _text_refs(path, text, name):
    word = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
    return [NameRef(path, n, m.start(), "text", "unknown")
            for n, line in enumerate(text.splitlines(), 1) for m in word.finditer(line)]


def name_only_references(change: FunctionChange, files):
    """Every use of change.name in `files` that is not on a resolved call site's line."""
    known = {(c.file, c.line) for c in change.call_sites if c.resolved}
    refs = []
    for path in sorted(files):
        text = files[path]
        found = _python_refs(path, text, change.name) if path.endswith(".py") else None
        if found is None:
            found = _text_refs(path, text, change.name)
        refs += [r for r in found if (r.file, r.line) not in known]
    return refs
