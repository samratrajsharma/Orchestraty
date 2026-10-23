"""A session's route changes: the HTTP routes its copy of the code declares (the engine's
``routes``: FastAPI, Flask and Django, from the syntax tree) against those of its base commit.

A route is its method and path. Removed, or with its method or path changed (a route removed
and one added with the same handler), it is a breaking change of the contract; a new route is
compatible. A changed route counts under both its old and its new method and path, so another
session that changes either one changes the same route. Each route keeps the engine's
confidence: a heuristic one (some part of its path computed, or where it is mounted not known)
is listed but never makes a strong finding. A changed file that does not parse (an edit half
done) keeps its base commit's routes until it parses again: what it declares is not known, so
its routes are not taken as removed.
"""
from dataclasses import dataclass, field

from orch_engine import routes as engine_routes

from orchestraty.livemap.contracts import BREAKING, COMPATIBLE
from orchestraty.livemap.index import overlay


@dataclass
class RouteChange:
    kind: str                  # added | removed | changed
    contract: str              # breaking | compatible
    route: dict                # the engine's route: the base's (removed, changed) or the session's (added)
    to: dict = None            # for a change: the route it became

    def as_dict(self):
        return {"kind": self.kind, "contract": self.contract, "route": dict(self.route),
                "to": dict(self.to) if self.to else None}


@dataclass
class RouteView:
    changes: list = field(default_factory=list)     # [RouteChange]
    touched: dict = field(default_factory=dict)     # (method, path) -> (RouteChange, the route under that key)


def key(route):
    return (route["method"], route["path"])


def compare(base, head):
    """The RouteView between two lists of the engine's routes."""
    d = engine_routes.diff(base, head)
    changes = ([RouteChange("removed", BREAKING, r) for r in d["removed"]]
               + [RouteChange("changed", BREAKING, c["from"], c["to"]) for c in d["changed"]]
               + [RouteChange("added", COMPATIBLE, r) for r in d["added"]])
    view = RouteView(changes)
    for c in changes:
        for r in (c.route, c.to) if c.to else (c.route,):
            view.touched[key(r)] = (c, r)
    return view


def session_routes(base_index, head):
    """(the base commit's routes, the session's): `head` holds the session's changed Python
    files ({path: ParsedFile or None}) over the base commit's index."""
    base = engine_routes.for_index(base_index)
    head = {path: pf for path, pf in head.items() if pf is None or not pf.error}
    if not head:
        return base, base
    if not base and not any(pf is not None and pf.routes for pf in head.values()):
        return base, []                              # no routes before, and none of the changes declares one
    return base, engine_routes.resolve(overlay(base_index, head))
