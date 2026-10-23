"""The overlap rules: how the changes of two live sessions on one repository touch.

| Rule | Fires when                                                              | Strength |
|------|-------------------------------------------------------------------------|----------|
| O1   | both sessions changed the same file                                     | weak     |
| O2   | both changed the same function or class                                 | medium   |
| O3   | one made a breaking change to X, and a file the other is changing uses X | strong   |
| O4   | one removed (or renamed) X, and a file the other is changing still uses X | strong   |
| O5   | both changed the same HTTP route (its method and path): each added,     | strong   |
|      | removed it, or changed its method or path                               |          |
| O6   | one made a breaking change to X, and callers of X that no session has   | strong   |
|      | updated are left behind                                                 |          |
| W1   | two sessions with hooks work in the same worktree (the same working     | medium   |
|      | folder, whichever subfolder each started in)                            |          |
| O7   | the work two sessions declared (the MCP tool ``declare``) or claimed    | medium   |
|      | (``orch claim``) overlaps                                               |          |

Two sessions in one worktree share its files, so what each of them did to a function cannot be
told apart: between them O1 runs on the files each edited, and O2 to O5 do not. W1 says
they share the folder; a plain-git session (no hooks) neither gets nor causes one.

Uses come from the code graphs the engine builds: a call resolved with evidence (an import,
the same file, a typed receiver, ...) or an inheritance, never a match by name alone. A use
counts only when the change can break it: the classifier says which calls each breaking
reason breaks (a parameter renamed breaks the calls that pass it by name, not those that pass
it by position), and the engine records how each call passes its arguments; an inheritance
always counts. A finding gives the reasons its uses run into first. A use counts for O3 and
O4 in the other session's own copy of the code. In a file that session is
changing it is a finding; in any other file that uses it, the finding is latent: not shown as
an overlap, but it gives the session its note before its first edit of that file (at most
MAX_LATENT files per change and session). O6 looks at the callers the base commit had, and a
caller counts as updated when some live session modified it. Calls made at module level,
outside any function, are not seen.

O5 compares the routes of each session's copy of the code with its base commit's
(``livemap.routes``: FastAPI, Flask and Django routes, from the engine). A route whose method or
path changed counts under both its old and its new one. Only a route the engine is sure of
(``exact``: every part of its path a literal, and its chain up to an app known) counts, on both
sides: a heuristic route never makes this strong finding.

O7 compares what each session said it will change, not what it changed: a declared path, a
claimed file or folder (a path covers the files below it), and a declared or claimed function
or class (``path::name``; a bare name a declaration of several files gave is not compared, and
a declared file whose functions the declaration names stands for those functions only). Two
items overlap when one path is the other or holds it, unless both name functions and those
differ. The finding is about the narrower item, the function where there is one, and
says how each session named it: ``claimed`` over ``declared``.

Every finding has a stable key, so the coordinator can tell a new finding from one it already
knows, and one that stopped holding.
"""
import os
from dataclasses import dataclass, field

from orch_engine.routes import EXACT

from orchestraty.livemap.contracts import breaking_reasons

WEAK, MEDIUM, STRONG = "weak", "medium", "strong"
MAX_LATENT = 100
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
    """{user symbol: (sorted lines, call shapes)} for the calls to `target` and the classes
    inheriting it. The shapes say how each call passes its arguments; None when some use is
    not a Python call with recorded arguments (an inheritance, a call from JS)."""
    out = {}
    if graph is None or target not in graph.nodes:
        return out
    for etype in ("calls", "inherits"):
        for user in graph.predecessors(target, etype):
            if user == target:
                continue
            info = graph.edge_info(user, target, etype)
            lines, shapes = out.get(user, (set(), []))
            lines.update(info.get("lines", []))
            out[user] = (lines, shapes + info["args"] if shapes is not None and info.get("args") else None)
    return {u: (sorted(lines), shapes) for u, (lines, shapes) in out.items()}


def _breaking_uses(change, graph, target):
    """[(user symbol, sorted lines, the breaking reasons it runs into)] for the uses of `target`
    that `change` can break: every use, with no reasons, when the change does not say which
    calls it breaks."""
    out = []
    for use, (lines, shapes) in sorted(_uses(graph, target).items()):
        why = breaking_reasons(change.breaks, shapes) if change.breaks else []
        if change.breaks and not why:
            continue                      # each of its calls passes its arguments in a way that still works
        out.append((use, lines, why))
    return out


def _reasons(change, why):
    """The change's reasons for one finding: the breaking reasons its uses run into, then the
    reasons that break no call; all of them, in order, when the change does not say which
    calls it breaks."""
    if not change.breaks:
        return list(change.reasons)
    return [r for r in change.reasons if r in why] + [r for r in change.reasons if r not in change.breaks]


def same_file(a, b, views):
    va, vb = views[a], views[b]
    for path in sorted(set(va.files) & set(vb.files)):
        yield Finding(f"O1|{path}|{a}|{b}", "O1", WEAK, (a, b), symbol="", file=path, detail={"file": path})


def one_folder(a, b, views):
    """Whether two sessions work in the same worktree."""
    wa, wb = views[a].worktree, views[b].worktree
    return bool(wa) and os.path.normcase(os.path.normpath(wa)) == os.path.normcase(os.path.normpath(wb))


def same_folder(a, b, views):
    """W1: two sessions with hooks in one worktree. The key names the folder and both sessions."""
    folder = views[a].worktree
    yield Finding(f"W1|{folder}|{a}|{b}", "W1", MEDIUM, (a, b), symbol="", file="", detail={"worktree": folder})


def same_function(a, b, views):
    sa, sb = views[a].symbols, views[b].symbols
    for sym in sorted(set(sa) & set(sb)):
        if sa[sym].kind in CHANGED_KINDS and sb[sym].kind in CHANGED_KINDS:
            yield Finding(f"O2|{sym}|{a}|{b}", "O2", MEDIUM, (a, b), symbol=sym, file=_file_of(sym),
                          detail={"symbol": sym, "kinds": {a: sa[sym].kind, b: sb[sym].kind}})


def _route_change(change, route):
    """What one session did to the route `route` (the one under the finding's method and path)."""
    out = {"kind": change.kind, "handler": route["handler"], "file": route["file"], "line": route["line"],
           "from": "", "to": ""}
    if change.kind == "changed":
        if route is change.to:
            out["from"] = f"{change.route['method']} {change.route['path']}"
        else:
            out["to"] = f"{change.to['method']} {change.to['path']}"
    return out


def same_route(a, b, routes):
    """O5: both sessions changed the same route, method and path, each as sure of it as can be.
    routes(sid) gives that session's livemap.routes.RouteView, or None while it is not known.
    The key keeps a "|" in a path out of its separators."""
    ra, rb = routes(a), routes(b)
    if ra is None or rb is None:
        return
    for key in sorted(set(ra.touched) & set(rb.touched)):
        (ca, route_a), (cb, route_b) = ra.touched[key], rb.touched[key]
        if route_a["confidence"] != EXACT or route_b["confidence"] != EXACT:
            continue
        method, path = key
        yield Finding(f"O5|{method} {path.replace('|', '%7C')}|{a}|{b}", "O5", STRONG, (a, b),
                      symbol=route_a["handler"], file=route_a["file"],
                      detail={"method": method, "path": path, "framework": route_a["framework"],
                              "changes": {a: _route_change(ca, route_a), b: _route_change(cb, route_b)}})


def contract_uses(changer, user, views, graphs):
    """O3 and O4: what `changer` broke or removed, used in `user`'s copy of the code; latent
    where `user` is not changing the file."""
    graph = graphs(user)
    if graph is None:
        return
    files = set(views[user].files)
    for sym, change in sorted(views[changer].symbols.items()):
        if change.contract != BREAKING:
            continue
        target = change.moved_from or sym                    # where the other session's code finds it
        removed = change.kind == "removed"
        by_file, why = {}, {}
        for use, lines, hit in _breaking_uses(change, graph, target):
            by_file.setdefault(_file_of(use), []).append({"symbol": use, "lines": lines})
            why.setdefault(_file_of(use), set()).update(hit)
        latent_left = MAX_LATENT
        for path, uses in sorted(by_file.items(), key=lambda kv: (kv[0] not in files, kv[0])):
            latent = path not in files
            if latent:
                if latent_left <= 0:
                    continue
                latent_left -= 1
            rule = "O4" if removed else "O3"
            yield Finding(f"{rule}|{target}|{changer}|{user}|{path}", rule, STRONG, tuple(sorted((changer, user))),
                          changer=changer, receiver=user, symbol=target, file=path,
                          detail={"symbol": target, "kind": change.kind, "reasons": _reasons(change, why[path]),
                                  "moved_to": sym if change.moved_from else "", "uses": uses, "latent": latent})


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
        left, why = [], set()
        for use, lines, hit in _breaking_uses(change, graph, target):
            if use not in updated:
                left.append({"symbol": use, "file": _file_of(use), "lines": lines})
                why.update(hit)
        if left:
            yield Finding(f"O6|{target}|{changer}", "O6", STRONG, (changer,), changer=changer, symbol=target,
                          file=_file_of(target),
                          detail={"symbol": target, "kind": change.kind, "reasons": _reasons(change, why),
                                  "left": left})


def _within(path, folder):
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _meet(x, y):
    """The narrower of two planned items when they overlap, else None. An item: {"path", "symbol",
    "how"}; a function's path is its file."""
    if x["symbol"] and y["symbol"]:
        return x if x["symbol"] == y["symbol"] else None
    if _within(x["path"], y["path"]):
        return x if x["symbol"] or not y["symbol"] else y
    if _within(y["path"], x["path"]):
        return y if y["symbol"] or not x["symbol"] else x
    return None


def declared_work(a, b, planned):
    """O7: the items `a` and `b` both declared or claimed. `planned`: {session id: [item]}."""
    met = {}
    for x in planned.get(a, ()):
        for y in planned.get(b, ()):
            item = _meet(x, y)
            if item is None:
                continue
            what = item["symbol"] or item["path"]
            how = met.setdefault(what, (item, {a: "declared", b: "declared"}))[1]
            for sid, z in ((a, x), (b, y)):
                if z["how"] == "claimed":
                    how[sid] = "claimed"
    for what, (item, how) in sorted(met.items()):
        yield Finding(f"O7|{what}|{a}|{b}", "O7", MEDIUM, (a, b), symbol=item["symbol"], file=item["path"],
                      detail={"what": what, "kind": "symbol" if item["symbol"] else "path", "how": how})


def evaluate(views, graphs, base_graphs, hooked=None, planned=None, routes=None):
    """Every finding among the sessions of one repository.

    views: {session id: SessionView}; graphs(sid) and base_graphs(sid) give that session's code
    graph and its base commit's graph, or None while they are not ready (then the rules that
    need them wait, and the rest still run). hooked: the sessions whose agent's hooks deliver
    notes, the only ones W1 is about (None: W1 does not run). planned: {session id: [item]},
    what each declared or claimed, for O7 (None: O7 does not run). routes(sid): that session's
    route changes, or None while they are not known, for O5 (None: O5 does not run)."""
    out = {}
    ids = sorted(views)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            shared = one_folder(a, b, views)
            found = list(same_file(a, b, views))
            if not shared:
                found += same_function(a, b, views)
                if routes is not None:
                    found += same_route(a, b, routes)
            elif hooked is not None and a in hooked and b in hooked:
                found += same_folder(a, b, views)
            if planned:
                found += declared_work(a, b, planned)
            for f in found:
                out[f.key] = f
        for b in ids:
            if b != a and not one_folder(a, b, views):
                for f in contract_uses(a, b, views, graphs):
                    out[f.key] = f
        for f in left_behind(a, views, base_graphs):
            out[f.key] = f
    return [out[k] for k in sorted(out)]
