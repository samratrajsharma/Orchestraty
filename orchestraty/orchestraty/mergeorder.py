"""Merge-order advice, worked out from the findings, the same way every time.

A finding O3 or O4 that is not latent says that one session (the receiver) is changing a file
that uses what another session (the changer) changed in a way that breaks it, or removed: the
receiver's work and the changer's change meet in that file, and the receiver has to be brought
up to date with the change. So the changer merges first. When each of two sessions uses what
the other broke, neither order works alone: they merge together, or agree first. Where no
session uses another's breaking change, there is nothing to say.

A session is named by its branch (a cloud agent's branch by ``<remote>/<branch>``), or by the
start of its id when it has none. A teammate's branch is named with their handles ("asha's
feat/refunds"), so it is never taken for a local branch of the same name; the user's own work
on another machine is "your feat/x on another machine". Every name and function is cleaned as
in notes.
"""
from orchestraty import sessionkinds
from orchestraty.delivery import notes as templates

RULES = ("O3", "O4")


def name(session, sid):
    if session and session.get("agent") == sessionkinds.TEAM:          # never taken for a local branch
        people = [templates.clean(p, 40) for p in session.get("people") or []]
        branch = templates.clean(str(session.get("branch") or "?").split("/", 1)[-1], 80)
        if people:
            return f"{' and '.join(people)}'s {branch}"
        return f"your {branch} on another machine" if session.get("own") else f"a teammate's {branch}"
    if session and session.get("branch"):
        return templates.clean(session["branch"], 80)
    return f"session {sid[:8]}"


def advise(findings, sessions):
    """[{"sessions": [a, b], "first": a or "", "symbols": [...], "line": text}] for the open
    findings of one repository; `sessions`: {id: session row}. One entry per pair of sessions."""
    uses = {}                                     # (changer, receiver) -> {symbol: rule}
    for f in findings:
        if f["rule"] in RULES and not f["detail"].get("latent") and f["changer"] and f["receiver"]:
            uses.setdefault((f["changer"], f["receiver"]), {})[f["symbol"]] = f["rule"]
    out, seen = [], set()
    for (a, b), symbols in sorted(uses.items()):
        pair = tuple(sorted((a, b)))
        if pair in seen:
            continue
        seen.add(pair)
        na, nb = name(sessions.get(a), a), name(sessions.get(b), b)
        back = uses.get((b, a))
        if back:
            what = ", ".join(_what(x) for x in sorted(symbols)) + "; " + ", ".join(_what(x) for x in sorted(back))
            out.append({"sessions": list(pair), "first": "", "symbols": sorted(set(symbols) | set(back)),
                        "line": f"{na} and {nb} depend on each other ({what}): merge them together, or "
                                "coordinate."})
            continue
        how = "removed" if set(symbols.values()) == {"O4"} else "changed"
        listed = ", ".join(_what(x) for x in sorted(symbols))
        out.append({"sessions": [a, b], "first": a, "symbols": sorted(symbols),
                    "line": f"merge {na} first: {nb} uses {listed}, which {na} {how}."})
    return out


def _what(symbol):
    return f"{templates.clean(symbol.split('::', 1)[-1])} in {templates.clean(symbol.split('::', 1)[0])}"
