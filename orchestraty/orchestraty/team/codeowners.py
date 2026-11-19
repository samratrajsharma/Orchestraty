"""CODEOWNERS: who owns which paths, shown to people in status and the dashboard. A team's name
never goes into a note.

It is read from the default branch in force, the first there of ``.github/CODEOWNERS``,
``CODEOWNERS``, ``docs/CODEOWNERS`` and ``.gitlab/CODEOWNERS``. Each line is a pattern and its
owners: ``@login``, ``@org/team`` or an email, which ``People.resolve`` turns into handles.

Patterns are gitignore-like. A ``/`` at the start, or anywhere but the end, anchors the pattern
at the repository's top; a pattern with no ``/`` matches at any depth. A trailing ``/`` matches
a folder and everything in it, and so does a pattern whose last part has no wildcard. ``*`` and
``?`` match within one part of a path, ``**`` any number of parts, and ``\\#`` is a literal ``#``.
A last part with a wildcard matches files only: ``docs/*`` owns ``docs/a.md`` but not
``docs/b/c.md``. The last line that matches a path wins, and a line with no owners leaves the
path with none. GitLab's ``[Section]`` lines are skipped, a line that cannot be read is ignored
(a negation with ``!``, an empty part, an owner that is no token), ``[`` is a plain character
here, and only the first 5000 lines are read. No pattern anyone wrote becomes a regular
expression: each part is matched on its own with fnmatchcase.
"""
import fnmatch
import re
from collections import namedtuple

LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")
MAX_LINES = 5000
MAX_PATTERN = 200
MAX_OWNERS = 50
_OWNER = re.compile(r"@[A-Za-z0-9-]{1,39}(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?|[^@\s]+@[^@\s]+\.[^@\s]+")

Rule = namedtuple("Rule", "parts anchored folder owners line")


class Rules:
    """The lines of a CODEOWNERS file that could be read, in order."""

    def __init__(self, rules):
        self.rules = tuple(rules)

    def __eq__(self, other):
        return isinstance(other, Rules) and self.rules == other.rules

    def __repr__(self):
        return f"Rules({len(self.rules)} rules)"

    @classmethod
    def parse(cls, data):
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        out = []
        for no, raw in enumerate(data.splitlines()[:MAX_LINES], start=1):
            rule = _rule(raw, no)
            if rule is not None:
                out.append(rule)
        return cls(out)

    def matched(self, path):
        """The last rule that matches a repository path (posix), or None."""
        parts = [p for p in path.split("/") if p]
        found = None
        for rule in self.rules:
            if parts and _matches(rule, parts):
                found = rule
        return found

    def owners(self, path):
        """The owners' tokens of a repository path, as the last matching line gives them."""
        rule = self.matched(path)
        return list(rule.owners) if rule else []


def find(read):
    """(where, Rules) for the first CODEOWNERS that `read(path)` gives (bytes, or None when the
    branch has no such file); (None, Rules([])) when there is none."""
    for where in LOCATIONS:
        data = read(where)
        if data is not None:
            return where, Rules.parse(data)
    return None, Rules([])


def _rule(raw, no):
    line = raw.strip()
    if not line or line.startswith("#") or line.startswith("[") or line.startswith("^["):
        return None
    if any(ord(c) < 32 for c in line):
        return None
    words = line.split()
    pattern, owners = words[0], []
    for word in words[1:]:
        if word.startswith("#"):
            break                                        # the rest of the line is a comment
        owners.append(word)
    if pattern.startswith("!") or len(pattern) > MAX_PATTERN or len(owners) > MAX_OWNERS:
        return None
    if not all(_OWNER.fullmatch(o) for o in owners):
        return None
    pattern = pattern.replace("\\#", "#")
    folder = pattern.endswith("/")
    body = pattern.strip("/") if folder else pattern.lstrip("/")
    anchored = pattern.startswith("/") or "/" in body
    if not body:
        body, anchored = "**", True                      # "/" alone: everything
    parts = body.split("/")
    if any(p == "" for p in parts):
        return None
    return Rule(tuple(parts), anchored, folder, tuple(owners), no)


def _wild(part):
    return "*" in part or "?" in part


def _part(name, part):
    """One part of a path against one part of a pattern: * and ? only, [ taken as itself."""
    return fnmatch.fnmatchcase(name, part.replace("[", "[[]"))


def _prefixes(pattern, parts):
    """The lengths n for which the pattern's parts match parts[:n], ** matching any number of
    parts (none too). Worked out part by part, so it takes time in proportion to the two
    lengths multiplied, whatever the pattern."""
    reach = {0}                                          # how many of the path's parts are matched so far
    for p in pattern:
        nxt = set()
        if p == "**":
            low = min(reach) if reach else len(parts) + 1
            nxt = set(range(low, len(parts) + 1))
        else:
            for n in reach:
                if n < len(parts) and _part(parts[n], p):
                    nxt.add(n + 1)
        reach = nxt
        if not reach:
            break
    return reach


def _matches(rule, parts):
    if not rule.anchored:                                # one part, at any depth
        p = rule.parts[0]
        if rule.folder:
            return any(_part(name, p) for name in parts[:-1])
        if _wild(p):
            return _part(parts[-1], p)
        return any(_part(name, p) for name in parts)
    last = rule.parts[-1]
    for n in _prefixes(rule.parts, parts):
        if n == len(parts) and not rule.folder:
            return True
        if n < len(parts) and (rule.folder or last == "**" or not _wild(last)):
            return True
    return False
