"""The overlap rules: how the changes of two live sessions on one repository touch.

| Rule | Fires when                                                              | Strength |
|------|-------------------------------------------------------------------------|----------|
| O1   | both sessions changed the same file                                     | weak     |
| O2   | both changed the same function or class                                 | medium   |
| O3   | one made a breaking change to X, and a file the other is changing uses X | strong   |
| O4   | one removed (or renamed) X, and a file the other is changing still uses X | strong   |
| O6   | one made a breaking change to X, and callers of X that no session has   | strong   |
|      | updated are left behind                                                 |          |

Uses come from the code graphs the engine builds: a call resolved with evidence (an import,
the same file, a typed receiver, ...) or an inheritance, never a match by name alone. A use
counts for O3 and O4 in the other session's own copy of the code, and only in the files that
session is changing. O6 looks at the callers the base commit had, and a caller counts as
updated when some live session modified it. Calls made at module level, outside any function,
are not seen.

Every finding has a stable key, so the coordinator can tell a new finding from one it already
knows, and one that stopped holding.
"""
from dataclasses import dataclass, field

WEAK, MEDIUM, STRONG = "weak", "medium", "strong"
BREAKING = "breaking"
CHANGED_KINDS = ("modified", "moved", "removed")


@dataclass
class Finding:
    key: str
    rule: str
    strength: str
    sessions: tuple            # every session involved, sorted
    changer: str = ""          # the session whose change it is (O3, O4, O6)
    receiver: str = ""         # the session that must hear first (O3, O4: the user of X)
    symbol: str = ""
    file: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {"key": self.key, "rule": self.rule, "strength": self.strength, "sessions": list(self.sessions),
                "changer": self.changer, "receiver": self.receiver, "symbol": self.symbol, "file": self.file,
                "detail": self.detail}


def _file_of(symbol):
    return symbol.split("::", 1)[0]


def _uses(graph, target):
    """{user symbol: sorted lines} for the calls to `target` and the classes inheriting it."""
    out = {}
    if graph is None or target not in graph.nodes:
        return out
    for etype in ("calls", "inherits"):
        for user in graph.predecessors(target, etype):
            if user == target:
                continue
            lines = graph.edge_info(user, target, etype).get("lines", [])
            out.setdefault(user, set()).update(lines)
    return {u: sorted(lines) for u, lines in out.items()}


def same_file(a, b, views):
    va, vb = views[a], views[b]
    for path in sorted(set(va.files) & set(vb.files)):
        yield Finding(f"O1|{path}|{a}|{b}", "O1", WEAK, (a, b), symbol="", file=path, detail={"file": path})


def same_function(a, b, views):
    sa, sb = views[a].symbols, views[b].symbols
    for sym in sorted(set(sa) & set(sb)):
        if sa[sym].kind in CHANGED_KINDS and sb[sym].kind in CHANGED_KINDS:
            yield Finding(f"O2|{sym}|{a}|{b}", "O2", MEDIUM, (a, b), symbol=sym, file=_file_of(sym),
                          detail={"symbol": sym, "kinds": {a: sa[sym].kind, b: sb[sym].kind}})


def contract_uses(changer, user, views, graphs):
    """O3 and O4: what `changer` broke or removed, used in the files `user` is changing."""
    graph = graphs(user)
    if graph is None:
        return
    files = set(views[user].files)
    for sym, change in sorted(views[changer].symbols.items()):
        if change.contract != BREAKING:
            continue
        target = change.moved_from or sym                    # where the other session's code finds it
        removed = change.kind == "removed"
        by_file = {}
        for use, lines in _uses(graph, target).items():
            if _file_of(use) in files:
                by_file.setdefault(_file_of(use), []).append({"symbol": use, "lines": lines})
        for path, uses in sorted(by_file.items()):
            rule = "O4" if removed else "O3"
            yield Finding(f"{rule}|{target}|{changer}|{user}|{path}", rule, STRONG, tuple(sorted((changer, user))),
                          changer=changer, receiver=user, symbol=target, file=path,
                          detail={"symbol": target, "kind": change.kind, "reasons": list(change.reasons),
                                  "moved_to": sym if change.moved_from else "", "uses": uses})


def left_behind(changer, views, base_graphs):
    """O6: callers of what `changer` broke that no live session has modified."""
    graph = base_graphs(changer)
    if graph is None:
        return
    updated = set()
    for v in views.values():
        updated.update(k for k, c in v.symbols.items() if c.kind in CHANGED_KINDS)
        updated.update(c.moved_from for c in v.symbols.values() if c.moved_from)
    for sym, change in sorted(views[changer].symbols.items()):
        if change.contract != BREAKING:
            continue
        target = change.moved_from or sym
        left = [{"symbol": use, "file": _file_of(use), "lines": lines}
                for use, lines in sorted(_uses(graph, target).items()) if use not in updated]
        if left:
            yield Finding(f"O6|{target}|{changer}", "O6", STRONG, (changer,), changer=changer, symbol=target,
                          file=_file_of(target),
                          detail={"symbol": target, "kind": change.kind, "reasons": list(change.reasons),
                                  "left": left})


def evaluate(views, graphs, base_graphs):
    """Every finding among the sessions of one repository.

    views: {session id: SessionView}; graphs(sid) and base_graphs(sid) give that session's code
    graph and its base commit's graph, or None while they are not ready (then the rules that
    need them wait, and the rest still run)."""
    out = {}
    ids = sorted(views)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            for f in list(same_file(a, b, views)) + list(same_function(a, b, views)):
                out[f.key] = f
        for b in ids:
            if b != a:
                for f in contract_uses(a, b, views, graphs):
                    out[f.key] = f
        for f in left_behind(a, views, base_graphs):
            out[f.key] = f
    return [out[k] for k in sorted(out)]
