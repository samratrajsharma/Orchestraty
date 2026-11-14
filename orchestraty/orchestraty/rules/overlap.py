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
caller counts as updated when a live session changed it so that, in that session's own copy
of the code, its calls no longer run into the breaking reasons. A use left of a removed
function always breaks. A reason that breaks every call (a function made async, say) shows
nothing in a call's arguments, so there the change of the caller counts; so does it while that
copy's graph is not known. Calls made at module level, outside any function, are not seen.

For TypeScript (``orch ts on``), the changes are those of a module's exports (``livemap.typescript``)
and a use needs import evidence: a file of the other session's copy that imports the export (or
a module re-exporting it) and names it. Which of those uses are calls is not resolved, so an O3
or O4 about TypeScript is medium, never strong, and says so (``heuristic``); O6 does not run for
it.

O5 compares the routes of each session's copy of the code with its base commit's
(``livemap.routes``: FastAPI, Flask and Django routes, from the engine). A route whose method or
path changed counts under both its old and its new one. Only a route the engine is sure of
(``exact``: every part of its path a literal, and its chain up to an app known) counts, on both
sides: a heuristic route never makes this strong finding. When both sessions changed the function
that handles a route O5 is about, O2 stays silent on that function: the O5 finding says it.

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

from orchestraty.livemap.contracts import ANY, breaking_reasons

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


def _changed_callers(views):
    """{symbol as the base commit has it: [(session id, its name in that session's copy)]} for every
    function or class a live session modified, moved or removed."""
    out = {}
    for sid, v in sorted(views.items()):
        for k, c in v.symbols.items():
            if c.kind in CHANGED_KINDS:
                out.setdefault(c.moved_from or k, []).append((sid, k))
    return out


def _checked_in_copy(change):
    """Whether a session's own copy can show that a caller still breaks: a removal (any use left
    breaks), or a breaking reason that shows in how a call passes its arguments. A reason that
    breaks every call (now async, a base class removed) shows nothing there."""
    return change.kind == "removed" or any(ANY not in c for c in (change.breaks or {}).values())


def _still_breaks(change, uses, caller):
    """Whether `caller` still uses what `change` broke in one session's copy (`uses`: [_uses dict
    per name]): any use of a removed function; else a use that runs into a reason its calls'
    arguments show."""
    shown = {r: c for r, c in (change.breaks or {}).items() if ANY not in c}
    for found in uses:
        use = found.get(caller)
        if use is not None and (change.kind == "removed" or breaking_reasons(shown, use[1])):
            return True
    return False


def _updated(change, copies, copy_uses):
    """Whether a session that changed this caller (`copies`: [(sid, its name there)]) updated it: in
    its own copy the caller no longer breaks. While that copy's graph is not known, or when the
    change cannot be seen in a call's arguments, the session's change of the caller counts."""
    for sid, name in copies:
        uses = copy_uses(sid) if _checked_in_copy(change) else None
        if uses is None or not _still_breaks(change, uses, name):
            return True
    return False


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


def _ts_uses(change, sym, imports):
    """{file: [{"symbol": file, "lines"}]} of the files that import a TypeScript export and use it."""
    module, name = sym.split("::", 1)
    if imports is None or name == "*":
        return {}
    return {path: [{"symbol": path, "lines": lines}] for path, lines in imports.uses(module, name).items()}


def contract_uses(changer, user, views, graphs, ts_uses=None):
    """O3 and O4: what `changer` broke or removed, used in `user`'s copy of the code; latent
    where `user` is not changing the file. ts_uses(sid): that session's TypeScript imports, or
    None (TypeScript contracts off, or not known yet)."""
    files = set(views[user].files)
    graph, imports = None, None
    for sym, change in sorted(views[changer].symbols.items()):
        if change.contract != BREAKING:
            continue
        target = change.moved_from or sym                    # where the other session's code finds it
        removed = change.kind == "removed"
        typescript = change.language == "typescript"
        by_file, why = {}, {}
        if typescript:
            if imports is None and ts_uses is not None:
                imports = ts_uses(user)
            by_file = _ts_uses(change, target, imports)
        else:
            if graph is None:
                graph = graphs(user)
                if graph is None:
                    graph = False
            if graph is False:
                continue
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
            detail = {"symbol": target, "kind": change.kind, "moved_to": sym if change.moved_from else "",
                      "uses": uses, "latent": latent,
                      "reasons": list(change.reasons) if typescript else _reasons(change, why[path])}
            if typescript:
                detail.update(language="typescript", heuristic=True, evidence="import",
                              source="source" if change.heuristic else "compiler")
            yield Finding(f"{rule}|{target}|{changer}|{user}|{path}", rule, MEDIUM if typescript else STRONG,
                          tuple(sorted((changer, user))), changer=changer, receiver=user, symbol=target, file=path,
                          detail=detail)


def left_behind(changer, views, base_graphs, graphs=None):
    """O6: callers of what `changer` broke that no live session has updated: changed so that, in
    its own copy of the code, their calls no longer break."""
    graph = base_graphs(changer)
    if graph is None:
        return
    changed = _changed_callers(views)
    for sym, change in sorted(views[changer].symbols.items()):
        if change.contract != BREAKING or change.language == "typescript":
            continue
        target = change.moved_from or sym
        names = (target, sym) if sym != target else (target,)
        memo = {}

        def copy_uses(sid, names=names, memo=memo):
            if sid not in memo:
                g = graphs(sid) if graphs is not None else None
                memo[sid] = None if g is None else [_uses(g, n) for n in names]
            return memo[sid]
        left, why = [], set()
        for use, lines, hit in _breaking_uses(change, graph, target):
            if not _updated(change, changed.get(use, ()), copy_uses):
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


def evaluate(views, graphs, base_graphs, hooked=None, planned=None, routes=None, ts_uses=None):
    """Every finding among the sessions of one repository.

    views: {session id: SessionView}; graphs(sid) and base_graphs(sid) give that session's code
    graph and its base commit's graph, or None while they are not ready (then the rules that
    need them wait, and the rest still run). hooked: the sessions whose agent's hooks deliver
    notes, the only ones W1 is about (None: W1 does not run). planned: {session id: [item]},
    what each declared or claimed, for O7 (None: O7 does not run). routes(sid): that session's
    route changes, or None while they are not known, for O5 (None: O5 does not run).
    ts_uses(sid): that session's TypeScript imports, for O3 and O4 about TypeScript."""
    out = {}
    ids = sorted(views)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            shared = one_folder(a, b, views)
            found = list(same_file(a, b, views))
            if not shared:
                by_route = list(same_route(a, b, routes)) if routes is not None else []
                handlers = {c["handler"] for f in by_route for c in f.detail["changes"].values()}
                found += [f for f in same_function(a, b, views) if f.symbol not in handlers]   # O5 says it
                found += by_route
            elif hooked is not None and a in hooked and b in hooked:
                found += same_folder(a, b, views)
            if planned:
                found += declared_work(a, b, planned)
            for f in found:
                out[f.key] = f
        for b in ids:
            if b != a and not one_folder(a, b, views):
                for f in contract_uses(a, b, views, graphs, ts_uses):
                    out[f.key] = f
        for f in left_behind(a, views, base_graphs, graphs):
            out[f.key] = f
    return [out[k] for k in sorted(out)]
