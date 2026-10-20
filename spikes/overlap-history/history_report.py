"""The two files the overlap history writes: ``overlap-report.html``, the full local report with
file and function names, and ``overlap-counts.json``, counts only. The counts file is the only
one a volunteer may choose to send back, so it holds numbers under fixed keys and nothing else.
"""
import html
import json
import time

RULES = ("O1", "O2", "O3", "O4", "O6")
STRONG = ("O3", "O4", "O6")
FOLLOWS = ("conflict", "revert", "fix")


def counts(result):
    """The counts file's content: numbers only, under fixed keys."""
    pairs = result["pairs"]
    distinct = {}
    for p in pairs:
        for f in p["findings"]:
            distinct[f["key"]] = f["rule"]
    weeks = result["days"] / 7.0
    strong = sum(1 for r in distinct.values() if r in STRONG)

    def followed_by(selected):
        out = {k: sum(1 for p in selected if p["followed"].get(k)) for k in FOLLOWS}
        out["any"] = sum(1 for p in selected if any(p["followed"].get(k) for k in FOLLOWS))
        out["conflict_unknown"] = sum(1 for p in selected if p["followed"].get("conflict") is None)
        return out

    with_strong = [p for p in pairs if any(f["rule"] in STRONG for f in p["findings"])]
    with_any = [p for p in pairs if p["findings"]]
    return {
        "tool": "overlap-history", "version": 1, "days": result["days"], "weeks": round(weeks, 2),
        "sessions": result["sessions"], "sessions_with_edits": result["sessions_with_edits"],
        "repositories": len(result["repositories"]),
        "parallel_pairs": len(pairs), "parallel_pairs_active": sum(1 for p in pairs if p["active"]),
        "pairs_both_committed": sum(1 for p in pairs if p["a"]["committed"] and p["b"]["committed"]),
        "pairs_same_worktree": sum(1 for p in pairs if p.get("same_worktree")),
        "overlaps": {r: sum(1 for v in distinct.values() if v == r) for r in RULES},
        "pairs_with": {r: sum(1 for p in pairs if any(f["rule"] == r for f in p["findings"])) for r in RULES},
        "strong_overlaps": strong, "strong_per_week": round(strong / weeks, 2) if weeks else 0.0,
        "strong_pairs": len(with_strong), "strong_pairs_followed_by": followed_by(with_strong),
        "overlap_pairs": len(with_any), "overlap_pairs_followed_by": followed_by(with_any),
        "unreadable_lines": result["unreadable_lines"],
        "edits_outside_git": result["left_out"]["outside"], "edits_folder_gone": result["left_out"]["gone"],
        "edits_ignored_by_git": result["left_out"]["ignored"], "index_failures": result["index_failures"],
    }


def counts_json(result):
    return json.dumps(counts(result), indent=2, sort_keys=True) + "\n"


def _when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "?"


def _e(value):
    return html.escape(str(value), quote=True)


def _session(s):
    state = "committed" if s["committed"] else "nothing committed"
    return "%s, %s to %s, %s, %d file(s)" % (_e(s["session"][:8]), _when(s["start"]), _when(s["end"]),
                                              state, len(s["files"]))


def _finding(f):
    d = f.get("detail", {})
    who = [u["symbol"] for u in d.get("uses", [])] + [c["symbol"] for c in d.get("left", [])]
    what = _e(f["symbol"] or f["file"])
    reasons = "; ".join(d.get("reasons", []))
    extra = (" &mdash; " + _e(reasons)) if reasons else ""
    users = ("<br><small>uses: %s</small>" % _e(", ".join(who[:12]))) if who else ""
    return "<li><b>%s</b> (%s) %s%s%s</li>" % (_e(f["rule"]), _e(f["strength"]), what, extra, users)


def report_html(result):
    c = counts(result)
    rows = []
    for p in result["pairs"]:
        if not p["findings"]:
            continue
        fol = [k for k in FOLLOWS if p["followed"].get(k)] or ["nothing seen"]
        where = " (both in one working tree: file overlaps only)" if p.get("same_worktree") else ""
        rows.append("<section><h3>%s%s</h3><p>A: %s<br>B: %s<br>Active at the same time: %s<br>"
                    "What followed (%s days seen): %s</p><ul>%s</ul></section>"
                    % (_e(p["repo"]), where, _session(p["a"]), _session(p["b"]), "yes" if p["active"] else "no",
                       _e(p["followed"].get("window_days")), _e(", ".join(fol)),
                       "".join(_finding(f) for f in p["findings"])))
    summary = ("<p>%d sessions in the last %d days (%d with edits), %d repositories, %d parallel pairs "
               "(%d active at the same time). Overlaps: %s. Strong overlaps: %d (%.2f a week).</p>"
               % (c["sessions"], c["days"], c["sessions_with_edits"], c["repositories"], c["parallel_pairs"],
                  c["parallel_pairs_active"], _e(", ".join("%s %d" % kv for kv in sorted(c["overlaps"].items()))),
                  c["strong_overlaps"], c["strong_per_week"]))
    notes = ("<p><small>O1 the same file changed in both; O2 the same function; O3 a breaking change used in a "
             "file the other changed; O4 a removed function still used there; O6 callers neither session "
             "updated. Function-level rules need the session to have committed its work, in a working tree "
             "of its own. Not seen: calls made at module level, dynamic calls, %d edits in folders that no "
             "longer exist; left out: %d edits outside any repository and %d to files git ignores.</small></p>"
             % (c["edits_folder_gone"], c["edits_outside_git"], c["edits_ignored_by_git"]))
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Overlap history</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}"
            "section{border-top:1px solid #ccc;padding:.5rem 0}</style></head><body>"
            "<h1>Overlap history</h1>%s%s%s</body></html>\n"
            % (summary, notes, "".join(rows) or "<p>No overlaps found.</p>"))
