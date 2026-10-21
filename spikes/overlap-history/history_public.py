"""Public history: overlaps among agent pull requests in public GitHub repositories, found by the
rules the overlap history runs on one person's sessions.

    python history_public.py select [--days 183] [--top 40] [--out FOLDER]
    python history_public.py scan OWNER/NAME [OWNER/NAME ...] [--days 183] [--out FOLDER]

select only asks GitHub's API, through gh. It searches for merged agent pull requests in Python
repositories, lists each candidate's merged pull requests and writes candidates.json: each
repository's size, its agent pull requests and its parallel pairs. It clones nothing. scan
clones the repositories it is named, and only those, into FOLDER/repos/: partial clones, so a
file's contents are fetched only when a commit that needs them is read. Nothing is checked out
and nothing from those repositories is run.

* An agent pull request has an agent's footer or task link in its description, was opened by a
  coding agent's GitHub app, or has an agent's branch prefix (AGENT_TEXT, AGENT_APPS,
  AGENT_BRANCHES).
* A parallel pair is two agent pull requests into one base branch whose open periods (created
  to merged) overlap.
* A pull request's change is its head against its merge base with the base branch just before
  it merged. The rules then run on the two changes as on two sessions' work
  (``history_overlaps.findings_for``), each pull request in a place of its own.
* What followed, within 14 days of the later merge: a text conflict between the two heads in a
  file both changed, and on the base branch a revert or a later fix to a function the overlap
  involves.
Pull requests from forks, and those whose head or merge base cannot be read, are left out and
counted. Merged pull requests miss the rework done before merging, so the rate is a lower bound.
"""
import argparse
import calendar
import collections
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time

DAYS = 183
OUT = os.path.join(os.path.expanduser("~"), "orch-phase0", "agent-history")
SEARCH_PAGES = 10                     # 100 results a page; GitHub's search stops at 1,000
SEARCH_PACE = 3.0                     # seconds before each request: GitHub allows 30 searches a minute, not in bursts
SLOW_DOWN = (120, 300, 600)           # seconds to wait each time GitHub says to slow down
LIST_LIMIT = 3000
FETCH_CHUNK = 2000
AGENT_TEXT = (("generated with an agent", "claude-code"), ("Generated with [an agent]", "claude-code"),
              ("chatgpt.com/codex/tasks/", "codex"))
AGENT_APPS = frozenset({"copilot-swe-agent", "copilot", "claude", "devin-ai-integration", "google-labs-jules",
                        "chatgpt-codex-connector", "codegen-sh", "sweep-ai"})
AGENT_BRANCHES = ("claude/", "codex/", "copilot/")
SEARCHES = ('"generated with an agent" in:body', '"chatgpt.com/codex/tasks" in:body', "author:app/copilot-swe-agent")
PR_FIELDS = ("number,author,body,createdAt,mergedAt,baseRefName,baseRefOid,headRefName,headRefOid,"
             "isCrossRepository,mergeCommit")
PASS, RARE = 0.05, 0.02               # strong overlaps in 5% of pairs or more passes; below 2% they look rare
MAX_PAIRS = 300                       # pairs analysed per repository, so no one repository decides the rate
NET_ENV = {"GIT_TERMINAL_PROMPT": "0",                       # a repository gone private fails; it never asks
           "GIT_HTTP_LOW_SPEED_LIMIT": "1000", "GIT_HTTP_LOW_SPEED_TIME": "120"}   # nor hangs on a stalled transfer
CANDIDATES, RESULT, COUNTS, REPORT = "candidates.json", "public-result.json", "public-counts.json", "public-report.html"
_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _time(iso):
    """Seconds since the epoch for GitHub's UTC timestamps (2026-10-01T09:00:00Z)."""
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


def _day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _key(repo):
    if not _NAME.match(repo) or any(p in (".", "..") for p in repo.split("/")):
        raise ValueError("not a repository name: %r" % (repo,))
    return repo.replace("/", "__")


def gh(args):
    """gh's output for one command, read as JSON."""
    proc = subprocess.run(["gh"] + list(args), stdin=subprocess.DEVNULL, capture_output=True)
    if proc.returncode:
        raise RuntimeError("gh %s failed: %s" % (" ".join(args[:2]), proc.stderr.decode("utf-8", "replace")[:300]))
    return json.loads(proc.stdout.decode("utf-8") or "null")


# ---------------- which pull requests, and which pairs ----------------

def agent_marker(raw):
    """Why a pull request (as gh lists it) counts as an agent's: "claude-code" or "codex" (its
    description), "app" (a coding agent's GitHub app opened it), "branch" (an agent's branch
    prefix); None for anyone else's."""
    body = (raw.get("body") or "").lower()
    for text, marker in AGENT_TEXT:
        if text in body:
            return marker
    author = raw.get("author") or {}
    login = (author.get("login") or "").lower()
    login = login[4:] if login.startswith("app/") else login
    login = login[:-5] if login.endswith("[bot]") else login
    if (author.get("is_bot") or author.get("type") == "Bot") and login in AGENT_APPS:
        return "app"
    if (raw.get("headRefName") or "").startswith(AGENT_BRANCHES):
        return "branch"
    return None


def pr_record(raw):
    """What the scan keeps of one pull request: no title and no description."""
    return {"number": raw["number"], "agent": agent_marker(raw), "created": _time(raw["createdAt"]),
            "merged": _time(raw["mergedAt"]) if raw.get("mergedAt") else None,
            "base": raw.get("baseRefName") or "", "base_oid": raw.get("baseRefOid") or "",
            "head": raw.get("headRefOid") or "", "fork": bool(raw.get("isCrossRepository")),
            "merge_commit": (raw.get("mergeCommit") or {}).get("oid") or ""}


def parallel_prs(prs):
    """[(a, b)] for the agent pull requests (merged, not from a fork) into one base branch whose
    open periods overlap, a opened first."""
    usable = sorted((p for p in prs if p["agent"] and p["merged"] and not p["fork"]),
                    key=lambda p: (p["created"], p["number"]))
    out = []
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            if b["created"] > a["merged"]:
                break                         # opened after a merged, as is every one after it
            if b["base"] == a["base"]:
                out.append((a, b))
    return out


def sample_pairs(pairs, cap, seed):
    """At most `cap` of the pairs, in their order, drawn at random with a fixed seed (the
    repository's name), so a rerun draws the same ones. All of them when cap is 0 or None."""
    if not cap or len(pairs) <= cap:
        return list(pairs)
    return [pairs[i] for i in sorted(random.Random(seed).sample(range(len(pairs)), cap))]


def list_prs(repo, since, run=gh, limit=LIST_LIMIT):
    """The repository's pull requests merged since `since`, as pr_record keeps them."""
    raw = run(["pr", "list", "--repo", repo, "--state", "merged", "--limit", str(limit), "--json", PR_FIELDS])
    out = [pr_record(r) for r in raw or [] if r.get("mergedAt") and _time(r["mergedAt"]) >= since]
    return sorted(out, key=lambda p: p["number"])


def _prs_path(out_dir, repo):
    return os.path.join(out_dir, "prs", _key(repo) + ".json")


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _ask(run, args, pace, say):
    """One API request, after a pause; when GitHub says to slow down, wait and ask again."""
    for wait in SLOW_DOWN + (None,):
        time.sleep(pace)
        try:
            return run(args)
        except RuntimeError as e:
            if wait is None or "rate limit" not in str(e).lower():
                raise
            say("GitHub asks to slow down: waiting %d s" % wait)
            time.sleep(wait)


def search(since, now, run=gh, pace=SEARCH_PACE, say=None):
    """({repository: agent pull requests found}, searches cut short): GitHub's search for merged
    agent pull requests in Python repositories, 30 days and one page at a time. A search with
    more than 1,000 results is cut short there, so its repositories are undercounted."""
    say = say or (lambda message: None)
    seen, cut = set(), 0
    start = since
    while start < now:
        end = min(start + 29 * 86400, now)
        for text in SEARCHES:
            query = "%s is:pr is:merged language:python merged:%s..%s" % (text, _day(start), _day(end))
            for page in range(1, SEARCH_PAGES + 1):
                found = _ask(run, ["api", "-X", "GET", "search/issues", "-f", "q=" + query, "-f", "per_page=100",
                                   "-f", "page=%d" % page], pace, say) or {}
                items = found.get("items") or []
                seen.update((i["repository_url"].split("/repos/", 1)[-1], i["number"]) for i in items)
                if len(items) < 100:
                    break
            cut += (found.get("total_count") or 0) > SEARCH_PAGES * 100
        say("searched to %s: %d agent pull requests so far" % (_day(end), len(seen)))
        start = end + 86400
    found = {}
    for repo, _ in seen:
        found[repo] = found.get(repo, 0) + 1
    return found, cut


def select(days=DAYS, now=None, out_dir=OUT, top=40, run=gh, pace=SEARCH_PACE, say=None):
    """Candidate repositories, most parallel agent pairs first, written to candidates.json; each
    candidate's pull requests go to prs/<owner>__<name>.json for the scan. Nothing is cloned."""
    say = say or (lambda message: None)
    now = time.time() if now is None else now
    since = now - days * 86400
    found, cut = search(since, now, run, pace, say)
    rows = []
    for repo, hits in sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))[:top]:
        try:
            prs = list_prs(repo, since, lambda args: _ask(run, args, pace, say))
            meta = _ask(run, ["api", "repos/" + repo], pace, say) or {}
        except (RuntimeError, ValueError) as e:
            say("%s left out: %s" % (repo, e))
            continue
        _write(_prs_path(out_dir, repo), json.dumps({"repo": repo, "since": since, "now": now, "prs": prs}) + "\n")
        agent = [p for p in prs if p["agent"]]
        rows.append({"repo": repo, "size_mb": round((meta.get("size") or 0) / 1024.0, 1),
                     "stars": meta.get("stargazers_count"), "fork": bool(meta.get("fork")),
                     "archived": bool(meta.get("archived")), "language": meta.get("language"),
                     "search_hits": hits, "merged_prs": len(prs), "agent_prs": len(agent),
                     "agent_prs_from_forks": sum(1 for p in agent if p["fork"]),
                     "parallel_pairs": len(parallel_prs(prs))})
        say("%s: %d agent pull requests, %d parallel pairs" % (repo, len(agent), rows[-1]["parallel_pairs"]))
    rows.sort(key=lambda r: (-r["parallel_pairs"], r["repo"]))
    doc = {"days": days, "since": since, "now": now, "searches": list(SEARCHES),
           "searches_cut_short": cut, "repositories_found": len(found), "candidates": rows}
    _write(os.path.join(out_dir, CANDIDATES), json.dumps(doc, indent=2) + "\n")
    return doc


def candidates_table(doc):
    """The candidates with parallel pairs, as text to read before anything is cloned."""
    rows = [r for r in doc["candidates"] if r["parallel_pairs"]]
    lines = ["%-45s %9s %10s %9s %7s" % ("repository", "size MB", "agent PRs", "pairs", "stars")]
    lines += ["%-45s %9.1f %10d %9d %7s" % (r["repo"], r["size_mb"], r["agent_prs"], r["parallel_pairs"], r["stars"])
              for r in rows]
    lines.append("%d with pairs; %.1f MB in all (GitHub's size; a partial clone fetches less)"
                 % (len(rows), sum(r["size_mb"] for r in rows)))
    return "\n".join(lines)


# ---------------- a partial clone, read only as far as needed ----------------

def clone(url, top, say=None):
    """A partial clone of `url` at `top`: commits and trees, nothing checked out. One already
    there is brought up to date instead, or used as it is when that fails."""
    if os.path.isdir(os.path.join(top, ".git")):
        if _run(top, ["fetch", "--quiet", "--no-tags", "origin"])[0] and say:
            say("%s: could not update the clone; using it as it is" % url)
        return
    os.makedirs(os.path.dirname(top), exist_ok=True)
    code, _ = _run(os.path.dirname(top), ["clone", "--quiet", "--filter=blob:none", "--no-checkout", "--no-tags",
                                          url, top])
    if code:
        raise RuntimeError("could not clone %s" % url)


def _run(top, args, stdin=b"", env=None):
    from history_repos import GIT
    proc = subprocess.run(GIT + list(args), cwd=top, input=stdin, capture_output=True,
                          env=dict(env or os.environ, **NET_ENV))
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def fetch_heads(top, numbers):
    """The heads of these pull requests, as refs/pr/<number> (commits and trees only)."""
    have = set(_run(top, ["for-each-ref", "--format=%(refname)", "refs/pr/"])[1].split())
    want = [n for n in numbers if "refs/pr/%d" % n not in have]
    if not want:
        return
    specs = "".join("+refs/pull/%d/head:refs/pr/%d\n" % (n, n) for n in want).encode()
    code, _ = _run(top, ["fetch", "--quiet", "--no-tags", "--stdin", "origin"], specs)
    if code:                                  # one ref gone fails them all: fetch them one by one
        for n in want:
            _run(top, ["fetch", "--quiet", "--no-tags", "origin", "+refs/pull/%d/head:refs/pr/%d" % (n, n)])


def _missing(top, oids):
    """The objects among `oids` this clone lacks, found without fetching them."""
    if not oids:
        return []
    _, out = _run(top, ["cat-file", "--batch-check"], "".join(o + "\n" for o in oids).encode(),
                  env=dict(os.environ, GIT_NO_LAZY_FETCH="1"))
    return [line.split()[0] for line in out.splitlines() if line.endswith(" missing")]


def prefetch(top, wants):
    """Fetches in one go the files these commits hold that the partial clone lacks; asked for
    one at a time, each would be a round trip. wants: [(commit, paths)], paths None for every
    file. Returns how many were fetched."""
    oids = set()
    for commit, paths in wants:
        if not commit or paths == []:
            continue
        wanted = None if paths is None else set(paths)
        _, out = _run(top, ["ls-tree", "-r", "-z", "--full-tree", commit])   # filtered here: a long list of paths
        for record in out.split("\0"):                                      # would not fit on a command line
            meta, _, path = record.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob" and (wanted is None or path in wanted):
                oids.add(parts[2])
    missing = _missing(top, sorted(oids))
    for i in range(0, len(missing), FETCH_CHUNK):
        _run(top, ["-c", "fetch.negotiationAlgorithm=noop", "fetch", "origin", "--no-tags", "--no-write-fetch-head",
                   "--recurse-submodules=no", "--filter=blob:none", "--stdin"],
             "".join(o + "\n" for o in missing[i:i + FETCH_CHUNK]).encode())
    return len(missing)


def _read(top, commit, paths):
    """history_changes.files_at, with the files fetched first."""
    from history_changes import files_at
    prefetch(top, [(commit, paths)])
    return files_at(top, commit, paths)


class _Fetching:
    """history_overlaps.Indexes for a partial clone: a commit's files are fetched in one go
    before the engine reads them, and only the last KEEP indexes stay in memory. A large
    repository's index is large; pairs come in time order, so nearby ones share bases."""

    KEEP = 6

    def __init__(self, indexes):
        self.indexes = indexes
        self.kept = collections.OrderedDict()
        self.failed = set()

    def get(self, top, commit):
        key = (top, commit)
        if key in self.failed:
            return None
        if key in self.kept:
            self.kept.move_to_end(key)
            return self.kept[key]
        prefetch(top, [(commit, None)])
        idx = self.indexes.get(top, commit)
        self.indexes._built.pop(key, None)            # kept here instead, a few at a time
        if idx is None:
            self.failed.add(key)
            return None
        self.kept[key] = idx
        while len(self.kept) > self.KEEP:
            self.kept.popitem(last=False)
        return idx


# ---------------- a pull request as a session's work ----------------

class _Session:
    """A pull request in the shape of a session: opened to merged."""

    def __init__(self, pr):
        self.id = "#%d" % pr["number"]
        self.start, self.end = pr["created"], pr["merged"]
        self.stretches = [(self.start, self.end)]


class _Work:
    """A pull request's work in the shape history_overlaps reads, in a place of its own."""

    def __init__(self, session, repo, top, files):
        self.session, self.repo, self.top, self.files = session, repo, top, files
        self.place = session.id


def _commit(top, rev):
    """The full sha of the commit `rev` names, or None."""
    if not rev:
        return None
    code, out = _run(top, ["rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{commit}"])
    return out.strip() if code == 0 and out.strip() else None


def _changed(top, x, y):
    """The paths that differ between two commits (renames as a delete and an add)."""
    _, out = _run(top, ["diff-tree", "-r", "--name-only", "--no-renames", "-z", x, y])
    return sorted(p for p in out.split("\0") if p)


def pr_change(top, repo, pr):
    """The Change for one pull request, its function changes still to come (pr_symbols); or
    why it is left out: "no_head" or "no_base"."""
    from history_changes import Change
    head = _commit(top, pr["head"])
    if not head:
        return "no_head"
    tips = [pr["merge_commit"] + "^1"] if pr["merge_commit"] and pr["merge_commit"] != pr["head"] else []
    start = None
    for tip in tips + [pr["base_oid"]]:      # the base branch just before it merged, else as gh saw it
        tip = _commit(top, tip)
        base = _run(top, ["merge-base", head, tip])[1].strip() if tip else ""
        if base and base != head:
            start = base
            break
    if not start:
        return "no_base"
    c = Change(_Work(_Session(pr), repo, top, _changed(top, start, head)))
    c.start, c.end = start, head
    c.commits = _run(top, ["rev-list", "%s..%s" % (start, head)])[1].split()
    c.commits += [pr["merge_commit"]] if pr["merge_commit"] else []
    return c


def pr_symbols(top, changes):
    """Each Change's function and class changes, with every file they read fetched in one go."""
    from history_changes import files_at
    from orchestraty.livemap.map import analyse
    py = {id(c): [p for p in c.work.files if p.endswith(".py")] for c in changes}
    prefetch(top, [(commit, py[id(c)]) for c in changes for commit in (c.start, c.end) if py[id(c)]])
    for c in changes:
        if py[id(c)]:
            c.symbols = analyse(files_at(top, c.start, py[id(c)]), files_at(top, c.end, py[id(c)]))


def pair_conflict(top, a, b):
    """True when merging the two heads conflicts in a file both changed, False when it does not,
    None when git cannot say. A conflict only in a file one of them changed is the base branch's
    doing between their starts, not theirs."""
    both = set(a.work.files) & set(b.work.files)
    if not both:
        return False
    base = _run(top, ["merge-base", a.end, b.end])[1].strip()
    if base:
        sides = [_changed(top, base, a.end), _changed(top, base, b.end)]
        prefetch(top, [(base, sorted(set(sides[0]) | set(sides[1]))), (a.end, sides[0]), (b.end, sides[1])])
    code, out = _run(top, ["merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", a.end, b.end])
    if code not in (0, 1):
        return None
    return code == 1 and bool(set(out.split("\0")[1:]) & both)


def pr_followed(top, a, b, findings, now, base):
    """{"conflict", "revert", "fix", "window_days"} for two pull requests. The revert and the
    fix are looked for on the base branch; both are None when that branch is gone."""
    from history_overlaps import FOLLOW_DAYS, involved, later
    since = max(a.work.session.end, b.work.session.end)
    until = min(since + FOLLOW_DAYS * 86400, now)
    out = {"conflict": pair_conflict(top, a, b), "revert": None, "fix": None,
           "window_days": round((until - since) / 86400, 1)}
    ref = "refs/remotes/origin/" + base
    if _commit(top, ref):
        wanted = set().union(*(involved(f) for f in findings)) if findings else set()
        out["revert"], out["fix"] = later(top, set(a.commits) | set(b.commits), since, until, wanted,
                                          refs=(ref,), read=_read)
    return out


def _side(pr, change):
    return {"number": pr["number"], "agent": pr["agent"], "created": pr["created"], "merged": pr["merged"],
            "files": change.work.files, "commits": len(change.commits)}


def scan_repo(repo, prs, url, top, now, say=None, max_pairs=MAX_PAIRS):
    """One repository: its parallel agent pairs (at most max_pairs of them, sample_pairs),
    their findings and what followed."""
    from history_overlaps import Indexes, findings_for
    say = say or (lambda message: None)
    clone(url, top, say)
    every = parallel_prs(prs)
    pairs = sample_pairs(every, max_pairs, repo)
    used = {p["number"]: p for pair in pairs for p in pair}
    fetch_heads(top, sorted(used))
    left_out = {"fork": sum(1 for p in prs if p["agent"] and p["fork"]), "no_head": 0, "no_base": 0, "pairs": 0}
    changes = {}
    for n in sorted(used):
        c = pr_change(top, repo, used[n])
        if isinstance(c, str):
            left_out[c] += 1
        else:
            changes[n] = c
    pr_symbols(top, list(changes.values()))
    say("%s: %d pull requests in %d parallel pairs (of %d)" % (repo, len(changes), len(pairs), len(every)))
    indexes, out = Indexes(), []
    try:
        fetching = _Fetching(indexes)
        for a, b in pairs:
            ca, cb = changes.get(a["number"]), changes.get(b["number"])
            if ca is None or cb is None:
                left_out["pairs"] += 1
                continue
            found = findings_for(ca, cb, fetching)
            out.append({"a": _side(a, ca), "b": _side(b, cb), "findings": [f.as_dict() for f in found],
                        "followed": pr_followed(top, ca, cb, found, now, a["base"])})
    finally:
        shutil.rmtree(indexes._data, ignore_errors=True)
    markers = {}
    for p in prs:
        if p["agent"]:
            markers[p["agent"]] = markers.get(p["agent"], 0) + 1
    return {"repo": repo, "merged_prs": len(prs), "agent_prs": sum(markers.values()), "markers": markers,
            "pairs_total": len(every), "pairs": out, "left_out": left_out, "index_failures": indexes.failed}


def scan(repos, out_dir=OUT, days=DAYS, now=None, run=gh, url=None, say=None, max_pairs=MAX_PAIRS):
    """Scans the named repositories, and only those, and writes public-result.json,
    public-counts.json and public-report.html into a folder named for the day. Each
    repository's result is saved there as it finishes, and a rerun the same day reuses it."""
    say = say or (lambda message: None)
    now = time.time() if now is None else now
    since = now - days * 86400
    url = url or (lambda name: "https://github.com/%s.git" % name)
    folder = os.path.join(out_dir, _day(now))
    rows = []
    for repo in repos:
        done = os.path.join(folder, "by-repo", _key(repo) + ".json")
        if os.path.exists(done):
            with open(done, encoding="utf-8") as fh:
                rows.append(json.load(fh))
            say("%s: scanned earlier today" % repo)
            continue
        path = _prs_path(out_dir, repo)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                prs = json.load(fh)["prs"]
        else:
            prs = list_prs(repo, since, run)
            _write(path, json.dumps({"repo": repo, "since": since, "now": now, "prs": prs}) + "\n")
        prs = [p for p in prs if p["merged"] and p["merged"] >= since]
        try:
            row = scan_repo(repo, prs, url(repo), os.path.join(out_dir, "repos", _key(repo)), now, say, max_pairs)
            _write(done, json.dumps(row) + "\n")
        except RuntimeError as e:
            say("%s failed: %s" % (repo, e))
            row = {"repo": repo, "error": str(e)[:300]}
        rows.append(row)
    result = {"days": days, "since": since, "now": now, "max_pairs": max_pairs, "repos": rows}
    _write(os.path.join(folder, RESULT), json.dumps(result, indent=1) + "\n")
    _write(os.path.join(folder, COUNTS), json.dumps(public_counts(result), indent=2, sort_keys=True) + "\n")
    _write(os.path.join(folder, REPORT), report_html(result))
    return result


# ---------------- the numbers and the report ----------------

def _strong(pair):
    from history_report import STRONG
    return any(f["rule"] in STRONG for f in pair["findings"])


def repo_interval(rows, draws=2000, seed=7):
    """The 95% interval of the pooled rate, resampling repositories: pairs from one repository
    are not independent of each other. rows: [(pairs with a strong overlap, pairs)]."""
    rows = [r for r in rows if r[1]]
    if not rows:
        return None
    rng = random.Random(seed)
    rates = []
    for _ in range(draws):
        pick = [rows[rng.randrange(len(rows))] for _ in rows]
        rates.append(sum(s for s, _ in pick) / float(sum(n for _, n in pick)))
    rates.sort()
    return [round(rates[int(0.025 * draws)], 4), round(rates[int(0.975 * draws) - 1], 4)]


def reading(rate):
    """The rate against the marks: "pass", "inconclusive", "rare", or "no pairs"."""
    if rate is None:
        return "no pairs"
    return "pass" if rate >= PASS else ("rare" if rate < RARE else "inconclusive")


def public_counts(result):
    """The scan's numbers: pairs, pairs with each rule, the strong-overlap rate with its 95%
    interval, and what followed."""
    from history_report import FOLLOWS, RULES
    rows = [r for r in result["repos"] if "error" not in r]
    pairs = [p for r in rows for p in r["pairs"]]

    def followed_by(selected):
        out = {k: sum(1 for p in selected if p["followed"].get(k)) for k in FOLLOWS}
        out["any"] = sum(1 for p in selected if any(p["followed"].get(k) for k in FOLLOWS))
        out["conflict_unknown"] = sum(1 for p in selected if p["followed"].get("conflict") is None)
        return out

    def total(field):
        out = {}
        for r in rows:
            for k, v in r[field].items():
                out[k] = out.get(k, 0) + v
        return out

    with_strong = [p for p in pairs if _strong(p)]
    with_any = [p for p in pairs if p["findings"]]
    rate = len(with_strong) / float(len(pairs)) if pairs else None
    rates = [sum(1 for p in r["pairs"] if _strong(p)) / float(len(r["pairs"])) for r in rows if r["pairs"]]
    return {
        "tool": "overlap-history-public", "version": 1, "days": result["days"],
        "repositories": len(rows), "repositories_failed": len(result["repos"]) - len(rows),
        "merged_prs": sum(r["merged_prs"] for r in rows), "agent_prs": sum(r["agent_prs"] for r in rows),
        "agent_prs_by_marker": total("markers"), "left_out": total("left_out"),
        "parallel_pairs_found": sum(r.get("pairs_total", len(r["pairs"])) for r in rows),
        "max_pairs_per_repository": result.get("max_pairs"), "parallel_pairs": len(pairs),
        "repository_rate_median": round(statistics.median(rates), 4) if rates else None,
        "pairs_with": {k: sum(1 for p in pairs if any(f["rule"] == k for f in p["findings"])) for k in RULES},
        "strong_pairs": len(with_strong), "strong_rate": None if rate is None else round(rate, 4),
        "strong_rate_95": repo_interval([(sum(1 for p in r["pairs"] if _strong(p)), len(r["pairs"])) for r in rows]),
        "repositories_with_strong_pairs": sum(1 for r in rows if any(_strong(p) for p in r["pairs"])),
        "reading": reading(rate),
        "strong_pairs_followed_by": followed_by(with_strong),
        "overlap_pairs": len(with_any), "overlap_pairs_followed_by": followed_by(with_any),
        "index_failures": sum(r["index_failures"] for r in rows),
    }


def report_html(result):
    """The local report: the numbers, each repository, and each pair with an overlap."""
    from history_report import FOLLOWS, _e, _finding
    c = public_counts(result)
    rate = "%.1f%%" % (100 * c["strong_rate"]) if c["strong_rate"] is not None else "none"
    ci = c["strong_rate_95"]
    summary = ("<p>%d repositories, %d agent pull requests, %d parallel pairs analysed (of %d found; at most %s a "
               "repository, drawn at random). Pairs with a strong overlap: %d, %s%s. The mark: 5%% or more passes; "
               "below 2%% collisions look rare. Reading: <b>%s</b>.</p>"
               % (c["repositories"], c["agent_prs"], c["parallel_pairs"], c["parallel_pairs_found"],
                  _e(c["max_pairs_per_repository"] or "all"), c["strong_pairs"], rate,
                  (" (95%% interval %.1f%% to %.1f%%, resampling repositories)" % (100 * ci[0], 100 * ci[1])) if ci else "",
                  _e(c["reading"])))
    table = ["<tr><th>Repository</th><th>Agent PRs</th><th>Pairs analysed</th><th>Strong</th><th>Left out</th></tr>"]
    sections = []
    for r in result["repos"]:
        if "error" in r:
            table.append("<tr><td>%s</td><td colspan=4>failed: %s</td></tr>" % (_e(r["repo"]), _e(r["error"])))
            continue
        table.append("<tr><td>%s</td><td>%d</td><td>%d of %d</td><td>%d</td><td>%s</td></tr>"
                     % (_e(r["repo"]), r["agent_prs"], len(r["pairs"]), r.get("pairs_total", len(r["pairs"])),
                        sum(1 for p in r["pairs"] if _strong(p)),
                        _e(", ".join("%s %d" % kv for kv in sorted(r["left_out"].items()) if kv[1]) or "-")))
        for p in r["pairs"]:
            if not p["findings"]:
                continue
            links = " and ".join("<a href='https://github.com/%s/pull/%d'>#%d</a> (%s)"
                                 % (_e(r["repo"]), s["number"], s["number"], _e(s["agent"])) for s in (p["a"], p["b"]))
            fol = [k for k in FOLLOWS if p["followed"].get(k)] or ["nothing seen"]
            sections.append("<section><h3>%s: %s</h3><p>What followed (%s days seen): %s</p><ul>%s</ul></section>"
                            % (_e(r["repo"]), links, _e(p["followed"]["window_days"]), _e(", ".join(fol)),
                               "".join(_finding(f) for f in p["findings"])))
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Public history</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}"
            "td,th{padding:.2rem .6rem;text-align:left}section{border-top:1px solid #ccc;padding:.5rem 0}</style>"
            "</head><body><h1>Public history</h1>%s<table>%s</table>%s</body></html>\n"
            % (summary, "".join(table), "".join(sections) or "<p>No overlaps found.</p>"))


def main(argv=None):
    if sys.version_info < (3, 10):
        print("The public history needs Python 3.10 or later.", file=sys.stderr)
        return 2
    p = argparse.ArgumentParser(description="Overlaps among agent pull requests in public GitHub repositories.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("select", help="find candidate repositories through GitHub's API; nothing is cloned")
    s.add_argument("--top", type=int, default=40, help="how many of the repositories found to list (default 40)")
    c = sub.add_parser("scan", help="clone the named repositories and run the rules on their pairs")
    c.add_argument("repos", nargs="+", metavar="OWNER/NAME")
    c.add_argument("--max-pairs", type=int, default=MAX_PAIRS,
                   help="pairs analysed per repository, drawn at random with a fixed seed (300; 0 for all)")
    for q in (s, c):
        q.add_argument("--days", type=int, default=DAYS, help="pull requests merged in the last DAYS days (183)")
        q.add_argument("--out", default=OUT, help="where everything goes (default: %s)" % OUT)
    args = p.parse_args(argv)
    from history_cli import _find_packages
    _find_packages()
    if args.command == "select":
        print(candidates_table(select(args.days, out_dir=args.out, top=args.top, say=print)))
        return 0
    try:
        for repo in args.repos:
            _key(repo)                        # a bad name stops the scan before anything is cloned
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    result = scan(args.repos, args.out, args.days, say=print, max_pairs=args.max_pairs)
    print(json.dumps(public_counts(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
