"""The team policy, ``.orchestraty/policy.yml``: what Orchestraty may share between teammates in
a repository::

    version: 1
    sync: on                  # on or off (true or false); off unless it says on
    visibility: private       # private or public: required when sync is on
    public_sync: false        # must be true for sync to run in a public repository
    share:
      claims: false           # live claims (kind, target, until), always soft for other people
      task: none              # none, ticket or confirmed (confirmed allows tickets too)
      unpushed: false         # committed but unpushed work
      activity: false         # whether a session's agent is working or idle
    tickets:
      keys: [PAY, OPS]        # KEY-123: each key two to ten capital letters and digits
      issues: false           # #123 and GH-123
    branches:
      share: ["*"]            # branch globs: one * at most, which spans /
      never: ["security/*"]
    deny:
      paths: ["customers/**"] # path globs; a built-in list is always added
      words: ["acme bank"]    # any case; - _ . / and space count as one
    people:
      unlisted: share         # share or refuse
    signatures: off           # off; verify gives a note; require keeps sync off (records are not signed yet)

The copy in force is the one on the remote's default branch, as the last team fetch brought it
into the shadow (``refs/orch/<remote>/HEAD``): a copy changed on a branch is not in force until
it is merged. What a seal follows is the effective policy, the most restrictive of the copy in
force, the copy the person agreed to at ``orch team on`` and their own narrowing in
config.json. So a narrowing on the default branch applies at once, and a widening waits until
the person agrees again; ``widened`` lists what they would be agreeing to.

A policy that cannot be read keeps sync off and says why, with its line, and so does one this
version cannot honour: an unknown version, an unknown kind of deny entry, ``signatures:
require``, or an unknown key anywhere but under ``share``. An unknown key under ``share`` could
only grant something, so it grants nothing and is listed in ``ignored``.

No pattern anyone writes becomes a regular expression. A branch glob has one ``*`` at most. A
path glob is matched part by part with fnmatchcase, ``**`` standing for any number of parts and
``[`` for itself, without regard to case; a glob with no ``/`` but at its end matches a part at
any depth, any other is anchored at the repository's top, and a folder it matches denies all
inside it. Deny words are matched without regard to case, all at once in one pass over the
text. At most 200 entries of each kind, each at most 200 characters.
"""
import fnmatch
import re
from collections import deque
from dataclasses import dataclass, field, replace
from functools import cached_property, lru_cache

from orchestraty import remote
from orchestraty.team import grammar, miniyaml

FILE = ".orchestraty/policy.yml"
VERSION = 1
MAX_BYTES = 64 * 1024
MAX_PATTERNS = 200                   # entries in each list
MAX_PATTERN = 200                    # characters in each entry
MAX_PARTS = grammar.MAX_SEGMENTS     # parts of a path glob
TASKS = ("none", "ticket", "confirmed")          # from the least to the most
SHARE_FLAGS = ("claims", "unpushed", "activity")
VISIBILITIES = ("private", "public")
UNLISTED = ("share", "refuse")
SIGNATURES = ("off", "verify", "require")
TOP_KEYS = ("version", "sync", "visibility", "public_sync", "share", "tickets", "branches", "deny", "people",
            "signatures")
SHARE_KEYS = ("claims", "task", "unpushed", "activity")
TICKET_KEYS = ("keys", "issues")
BRANCH_KEYS = ("share", "never")
DENY_KEYS = ("paths", "words")
PEOPLE_KEYS = ("unlisted",)
# Files that usually hold secrets: never named in a shared record, whatever the policy says.
BUILT_IN_PATHS = (".env", ".env.*", "*.env", ".envrc", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
                  "*.ppk", "*.kdbx", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".ssh", ".aws", ".gnupg",
                  ".netrc", "_netrc", ".git-credentials", ".pypirc", ".npmrc", ".htpasswd", "credentials.json",
                  "*.tfstate", "*.tfstate.*", "*.tfvars")

UPGRADE = "upgrade Orchestraty"
CANNOT_READ = "the team policy cannot be read"
NO_POLICY = ("there is no team policy on the remote's default branch", "orch team init")
NOT_AGREED = ("you have not agreed to this repository's team policy", "orch team on")
PUBLIC_NOT_AGREED = ("the policy now says the repository is public, which you have not agreed to", "orch team on")
SYNC_OFF = ("the policy says sync: off", f"set sync: on in {FILE} on the default branch")
NO_VISIBILITY = ("the policy does not say whether the repository is public",
                 "the policy must say visibility: private or public")
PUBLIC = ("the repository is public, and the policy does not say public_sync: true",
          "set public_sync: true if anyone who can read the repository may read what is shared")
PROBE_PUBLIC = ("anyone can read this repository without signing in, but the policy says visibility: private",
                "say visibility: public and public_sync: true in the policy, or make the repository private")
VERIFY_NOTE = "the policy says signatures: verify, which does nothing yet: records are not signed in this version"
NARROW_NOTE = "the narrowing in config.json cannot be read, so nothing optional is shared"
NEVER_NOTE = "never_branches in config.json cannot be read, so no branch is shared"
_SEPARATOR_RUNS = re.compile(r"[-_./ ]+")


@dataclass(frozen=True)
class Policy:
    """One copy of the policy (as read from a file, or as the person agreed to it), or the
    effective policy that ``effective`` makes of the copies."""

    version: int = VERSION
    sync: bool = False
    visibility: str = ""                 # "" when the policy does not say
    public_sync: bool = False
    claims: bool = False
    task: str = "none"
    unpushed: bool = False
    activity: bool = False
    ticket_keys: tuple = ()
    ticket_issues: bool = False
    share_branches: tuple = ("*",)
    never_branches: tuple = ()
    deny_paths: tuple = ()               # the policy's own: the built-in list is added when matching
    deny_words: tuple = ()
    unlisted: str = "share"
    signatures: str = "off"
    problem: tuple = None                # (why, fix): sync stays off whatever the rest says
    also_share: tuple = ()               # the effective policy only: more branches.share lists a branch must match
    ignored: tuple = field(default=(), compare=False)        # unknown keys under share, granting nothing
    notes: tuple = field(default=(), compare=False)
    blob: str = field(default="", compare=False)             # the copy in force's blob id, when read from git

    def off(self):
        """(why, fix) when this policy keeps sync off, or None when sync runs."""
        if self.problem:
            return self.problem
        if not self.sync:
            return SYNC_OFF
        if not self.visibility:
            return NO_VISIBILITY
        if self.visibility == "public" and not self.public_sync:
            return PUBLIC
        return None

    def sync_on(self):
        return self.off() is None

    def branch_shared(self, branch):
        """Whether a branch may be shared: it matches branches.share (in every copy of an
        effective policy) and no never glob."""
        return (all(remote.matches(branch, globs) for globs in (self.share_branches,) + self.also_share)
                and not remote.matches(branch, self.never_branches))

    def path_denied(self, path):
        """Whether the built-in list or deny.paths denies a repository path (posix). A path too
        long or too deep to be a repository path is denied."""
        if (not isinstance(path, str) or not path or len(path) > grammar.MAX_PATH
                or path.count("/") >= grammar.MAX_SEGMENTS):
            return True
        parts = [p.casefold() for p in path.split("/")]
        for anchored, pattern in self._globs:
            if not anchored:
                if any(_part(name, pattern[0]) for name in parts):
                    return True
            elif max(_reach(pattern, parts), default=0) >= 1:
                return True
        return False

    def word_denied(self, text):
        """Whether a deny word is in a text, any case, - _ . / and space counting as one."""
        if not isinstance(text, str):
            return True
        return bool(self.deny_words) and self._matcher.found(_fold(text))

    def ticket_pattern(self):
        """The pattern that finds tickets of the policy's keys in a text, or None for no key."""
        return grammar.ticket_pattern(self.ticket_keys)

    def normalised(self):
        """The policy as plain JSON values, every setting given: what config.json keeps as the
        copy the person agreed to, and what from_normalised reads back."""
        out = {"version": self.version, "sync": self.sync}
        if self.visibility:
            out["visibility"] = self.visibility
        out.update({
            "public_sync": self.public_sync,
            "share": {"claims": self.claims, "task": self.task, "unpushed": self.unpushed, "activity": self.activity},
            "tickets": {"keys": list(self.ticket_keys), "issues": self.ticket_issues},
            "branches": {"share": list(self.share_branches), "never": list(self.never_branches)},
            "deny": {"paths": list(self.deny_paths), "words": list(self.deny_words)},
            "people": {"unlisted": self.unlisted},
            "signatures": self.signatures,
        })
        return out

    @cached_property
    def _globs(self):
        return tuple(_path_glob(g) or (True, ("**",)) for g in BUILT_IN_PATHS + self.deny_paths)   # unreadable: all

    @cached_property
    def _matcher(self):
        return _word_matcher(tuple(_word(w) for w in self.deny_words))


def parse(data):
    """The policy a file holds (bytes or str). One that cannot be read, or says what this
    version cannot honour, comes back with a problem that keeps sync off."""
    try:
        doc = miniyaml.parse(data, max_bytes=MAX_BYTES)
    except miniyaml.Error as exc:
        return Policy(problem=_bad(exc.message, exc.line).problem)
    return _checked(doc)


def from_normalised(data):
    """The policy a normalised copy holds (as config.json keeps the one the person agreed to)."""
    return _checked(data)


def in_force(shadow, name, url=None, timeout=remote.FETCH_TIMEOUT, ssh_configured=False):
    """The policy in force: the default branch's .orchestraty/policy.yml as the last fetch
    brought it into the shadow (refs/orch/<name>/HEAD), or None when that branch has none.
    With `url`, the default branch alone is fetched first (into a shadow made afresh when git
    finds an object missing); remote.RemoteError says why that failed. A file over 64 KiB is
    never read."""
    ref = f"{remote.REF_ROOT}/{name}/HEAD"
    if url is not None:
        try:
            shadow.fetch_refspecs(url, ["+HEAD:" + ref], timeout=timeout, ssh_configured=ssh_configured)
        except remote.ShadowBroken:
            shadow.mark_broken()
            try:
                shadow.fetch_refspecs(url, ["+HEAD:" + ref], timeout=timeout, ssh_configured=ssh_configured)
            except remote.ShadowBroken as exc:
                raise remote.RemoteError(str(exc)) from None
    try:
        found = shadow.read_file(ref, FILE, MAX_BYTES, with_id=True)
    except remote.TooLarge:
        return Policy(problem=_bad(f"the file is larger than {MAX_BYTES // 1024} KiB").problem)
    if found is None:
        return None
    blob, data = found
    return replace(parse(data), blob=blob)


def effective(force, consented, narrow=None, never_branches=None, anonymous_read=None):
    """The policy a seal follows: never wider than the copy in force, the copy the person
    agreed to, or their narrowing (config.json's ``narrow``, such as {"task": "none"}, and
    ``never_branches``), which can only remove. A share flag is on only if it is on in all
    three, and task is the least of them; a branch must match branches.share in both copies;
    never globs, deny paths and deny words are those of both; and the repository is public if
    either copy says so. A local setting that cannot be read removes all it could.
    `anonymous_read` is the last probe's answer (team.probe): True, with a policy that says
    private, keeps sync off; None changes nothing."""
    if force is None:
        return Policy(problem=NO_POLICY)
    agreed = consented if consented is not None and consented.off() is None else None
    base = agreed if agreed is not None else force
    flags, notes = _narrowing(narrow)
    never, more = _local_never(never_branches)
    public = "public" in (force.visibility, base.visibility)
    agreed_public = agreed is not None and agreed.visibility == "public" and agreed.public_sync
    problem = force.off()
    if problem is None and agreed is None:
        problem = NOT_AGREED
    if problem is None and public and not agreed_public:
        problem = PUBLIC_NOT_AGREED
    if problem is None and anonymous_read is True and not public:
        problem = PROBE_PUBLIC
    return Policy(
        sync=force.sync and base.sync,
        visibility="public" if public else force.visibility,
        public_sync=agreed_public and (force.visibility != "public" or force.public_sync),
        claims=force.claims and base.claims and flags["claims"],
        task=min(force.task, base.task, flags["task"], key=TASKS.index),
        unpushed=force.unpushed and base.unpushed and flags["unpushed"],
        activity=force.activity and base.activity and flags["activity"],
        ticket_keys=tuple(k for k in force.ticket_keys if k in base.ticket_keys),
        ticket_issues=force.ticket_issues and base.ticket_issues,
        share_branches=force.share_branches,
        also_share=force.also_share + ((base.share_branches,) + base.also_share if base is not force else ()),
        never_branches=_union(force.never_branches, base.never_branches, never),
        deny_paths=_union(force.deny_paths, base.deny_paths),
        deny_words=_union(force.deny_words, base.deny_words, key=_word),
        unlisted="refuse" if "refuse" in (force.unlisted, base.unlisted) else "share",
        signatures=force.signatures,
        problem=problem,
        ignored=force.ignored,
        notes=force.notes + notes + more,
        blob=force.blob,
    )


def widened(force, consented):
    """What the copy in force allows that the agreed copy (None: nothing agreed yet) does not,
    one line each: what the person agrees to when they run orch team on again."""
    agreed = consented if consented is not None else Policy()
    out = []
    if force.sync and not agreed.sync:
        out.append("sync turned on")
    if force.visibility == "public" and agreed.visibility != "public":
        out.append(f"visibility: {agreed.visibility or 'not said'} to public")
    if force.claims and not agreed.claims:
        out.append("share.claims turned on")
    if TASKS.index(force.task) > TASKS.index(agreed.task):
        out.append(f"share.task: {agreed.task} to {force.task}")
    for flag in ("unpushed", "activity"):
        if getattr(force, flag) and not getattr(agreed, flag):
            out.append(f"share.{flag} turned on")
    out += [f"tickets.keys: {k} added" for k in force.ticket_keys if k not in agreed.ticket_keys]
    if force.ticket_issues and not agreed.ticket_issues:
        out.append("tickets.issues turned on")
    out += [f"branches.share: {_quote(g)} added" for g in force.share_branches if g not in agreed.share_branches]
    out += [f"branches.never: {_quote(g)} removed" for g in agreed.never_branches if g not in force.never_branches]
    out += [f"deny.paths: {_quote(g)} removed" for g in agreed.deny_paths if g not in force.deny_paths]
    kept = {_word(w) for w in force.deny_words}
    out += [f"deny.words: {_quote(w)} removed" for w in agreed.deny_words if _word(w) not in kept]
    if force.unlisted == "share" and agreed.unlisted == "refuse":
        out.append("people.unlisted: refuse to share")
    return out


# ------------------------------------------------------------------------------- reading a copy

class _Off(Exception):
    def __init__(self, why, fix):
        super().__init__(why)
        self.problem = (why, fix)


def _bad(message, line=None):
    """The policy cannot be read: the fix names the file, the line and what is wrong."""
    return _Off(CANNOT_READ, f"fix {FILE} line {line}: {message}" if line else f"fix {FILE}: {message}")


def _unknown(key, line):
    return _Off(f"the policy has the key {_quote(key)}, which this version does not know",
                f"{UPGRADE}, or fix {FILE} line {line}" if line else UPGRADE)


def _checked(doc):
    try:
        return _read(doc)
    except _Off as off:
        return Policy(problem=off.problem)


def _read(doc):
    if not isinstance(doc, dict):
        raise _bad("the policy is keys, such as version: 1 and sync: on", getattr(doc, "line", None) or 1)
    if "version" not in doc:
        raise _bad("the policy must say version: 1", 1)
    version = doc["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise _bad("version is a whole number: version: 1", _line(doc, "version"))
    if version != VERSION:
        raise _Off(f"the policy is version {str(version)[:12]}, which this version does not know", UPGRADE)
    for key in doc:
        if key not in TOP_KEYS:
            raise _unknown(key, _line(doc, key))
    share = _section(doc, "share", "claims: true")
    tickets = _section(doc, "tickets", "keys: [PAY]")
    branches = _section(doc, "branches", 'share: ["*"]')
    deny = _section(doc, "deny", 'paths: ["secrets/**"]')
    people = _section(doc, "people", "unlisted: share")
    for name, section, keys in (("tickets", tickets, TICKET_KEYS), ("branches", branches, BRANCH_KEYS),
                                ("people", people, PEOPLE_KEYS)):
        for key in section:
            if key not in keys:
                raise _unknown(f"{name}.{key}", _line(section, key))
    for key in deny:
        if key not in DENY_KEYS:
            raise _Off(f"the policy uses deny kind {_quote(key)}, which this version does not know", UPGRADE)
    signatures = doc.get("signatures", "off")
    if signatures is False:
        signatures = "off"                               # a plain `false` means off too
    if signatures not in SIGNATURES:
        raise _bad(f"signatures is {_one_of(SIGNATURES)}", _line(doc, "signatures"))
    if signatures == "require":
        raise _Off("the policy says signatures: require, and this version does not sign records", UPGRADE)
    return Policy(
        sync=_flag(doc, "sync", "sync"),
        visibility=_choice(doc, "visibility", VISIBILITIES, ""),
        public_sync=_flag(doc, "public_sync", "public_sync"),
        claims=_flag(share, "claims", "share.claims"),
        task=_choice(share, "task", TASKS, "none", "share.task"),
        unpushed=_flag(share, "unpushed", "share.unpushed"),
        activity=_flag(share, "activity", "share.activity"),
        ticket_keys=_entries(tickets, "keys", "tickets.keys", "[PAY, OPS]", _ticket_key),
        ticket_issues=_flag(tickets, "issues", "tickets.issues"),
        share_branches=_entries(branches, "share", "branches.share", '["feat/*"]', _branch_glob, ("*",)),
        never_branches=_entries(branches, "never", "branches.never", '["security/*"]', _branch_glob),
        deny_paths=_entries(deny, "paths", "deny.paths", '["secrets/**"]', _path_error),
        deny_words=_entries(deny, "words", "deny.words", '["acme bank"]', _word_error),
        unlisted=_choice(people, "unlisted", UNLISTED, "share", "people.unlisted"),
        signatures=signatures,
        ignored=tuple(f"share.{_shown(k)}" for k in share if k not in SHARE_KEYS),
        notes=(VERIFY_NOTE,) if signatures == "verify" else (),
    )


def _line(mapping, key):
    return getattr(mapping, "lines", {}).get(key)


def _section(doc, key, example):
    value = doc.get(key, {})
    if not isinstance(value, dict):
        raise _bad(f"{key} holds keys, such as {example}", _line(doc, key))
    return value


def _flag(mapping, key, where):
    value = mapping.get(key, False)
    if value is True or value == "on":
        return True
    if value is False or value == "off":
        return False
    raise _bad(f"{where} is true or false (on or off)", _line(mapping, key))


def _choice(mapping, key, choices, default, where=None):
    value = mapping.get(key, default)
    if value == default or (isinstance(value, str) and value in choices):
        return value
    raise _bad(f"{where or key} is {_one_of(choices)}", _line(mapping, key))


def _entries(mapping, key, where, example, check, default=()):
    """A list setting: strings, at most 200 of them, each at most 200 characters and passing
    `check` (which gives what is wrong, or None). Repeats are dropped."""
    if key not in mapping:
        return default
    value, line = mapping[key], _line(mapping, key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(f"{where} is a list, such as {key}: {example}", line)
    if len(value) > MAX_PATTERNS:
        raise _bad(f"{where} holds more than {MAX_PATTERNS} entries", line)
    lines = getattr(value, "lines", [])
    out = []
    for i, item in enumerate(value):
        at = lines[i] if i < len(lines) else line
        if len(item) > MAX_PATTERN:
            raise _bad(f"an entry of {where} is longer than {MAX_PATTERN} characters", at)
        wrong = check(item)
        if wrong:
            raise _bad(f"{where}: {wrong}", at)
        if item not in out:
            out.append(item)
    return tuple(out)


def _ticket_key(key):
    try:
        grammar.ticket_keys([key])
    except ValueError as exc:
        return str(exc)
    return None


def _branch_glob(glob):
    if glob.count("*") > 1:
        return f"{_quote(glob)} has more than one *"
    if not grammar.branch(glob.replace("*", "x")):
        return f"{_quote(glob)} is not a branch name with one * at most"
    return None


def _path_error(glob):
    if _path_glob(glob) is None:
        return (f"{_quote(glob)} is not a path glob: parts joined by /, none empty, . or .., no backslash, at most "
                f"{MAX_PARTS} parts")
    return None


def _word_error(word):
    if not word.isprintable():
        return f"{_quote(word)} holds a character that cannot be shown"
    if not _word(word):
        return f"{_quote(word)} is only separators"
    return None


def _shown(text):
    return "".join(c if c.isprintable() else "?" for c in str(text)[:40])


def _quote(text):
    return "'" + _shown(text) + "'"


def _one_of(choices):
    return ", ".join(choices[:-1]) + " or " + choices[-1]


# ------------------------------------------------------------------------------- the effective copy

def _narrowing(narrow):
    """The share settings the person's narrowing allows, and a note when it cannot be read."""
    full = {"claims": True, "task": TASKS[-1], "unpushed": True, "activity": True}
    if narrow is None:
        return full, ()
    if not isinstance(narrow, dict):
        return {"claims": False, "task": TASKS[0], "unpushed": False, "activity": False}, (NARROW_NOTE,)
    unreadable = False
    for key, value in narrow.items():
        if key == "task":
            ok = isinstance(value, str) and value in TASKS
            full["task"] = value if ok else TASKS[0]
        elif key in SHARE_FLAGS:
            ok = isinstance(value, bool)
            full[key] = value if ok else False
        else:
            ok = True                                    # nothing this version can narrow
        unreadable = unreadable or not ok
    return full, ((NARROW_NOTE,) if unreadable else ())


def _local_never(globs):
    if globs is None:
        return (), ()
    if not isinstance(globs, (list, tuple)) or not all(isinstance(g, str) and not _branch_glob(g) for g in globs):
        return ("*",), (NEVER_NOTE,)
    return tuple(globs), ()


def _union(*lists, key=None):
    out, seen = [], set()
    for item in (i for items in lists for i in items):
        k = key(item) if key else item
        if k not in seen:
            seen.add(k)
            out.append(item)
    return tuple(out)


# ------------------------------------------------------------------------------- matching

def _path_glob(glob):
    """(anchored, parts) of a path glob, casefolded, [ escaped for fnmatch; None when it is not
    one. A / at its start or before its last part anchors it at the repository's top."""
    if "\\" in glob or not glob.isprintable():
        return None
    body = glob[1:] if glob.startswith("/") else glob
    body = body[:-1] if body.endswith("/") else body
    parts = body.split("/")
    if not body or len(parts) > MAX_PARTS or any(p in ("", ".", "..") for p in parts):
        return None
    folded = tuple(p.casefold().replace("[", "[[]") if _wild(p) else p.casefold() for p in parts)
    return glob.startswith("/") or "/" in body, folded


def _wild(part):
    return "*" in part or "?" in part


def _part(name, pattern):
    return fnmatch.fnmatchcase(name, pattern) if _wild(pattern) else name == pattern


def _reach(pattern, parts):
    """The numbers n for which the pattern's parts match parts[:n] (** matching any number of
    parts, none too): part by part, so the time is in proportion to the two lengths multiplied."""
    reach = {0}
    for p in pattern:
        if p == "**":
            reach = set(range(min(reach), len(parts) + 1))
        else:
            reach = {n + 1 for n in reach if n < len(parts) and _part(parts[n], p)}
        if not reach:
            break
    return reach


def _fold(text):
    """A text as deny words are matched: casefolded, each run of - _ . / and space as one space."""
    return _SEPARATOR_RUNS.sub(" ", text.casefold())


def _word(word):
    """A deny word as it is looked for: folded, without separators at either end."""
    return _fold(word).strip(" ")


@lru_cache(maxsize=16)
def _word_matcher(words):
    return _Words(words)


class _Words:
    """All the deny words at once, found in one pass over a text (Aho-Corasick): each check
    takes time in proportion to the text, whatever the words are."""

    def __init__(self, words):
        goto, fail, hit = [{}], [0], [False]
        for word in words:
            node = 0
            for ch in word:
                nxt = goto[node].get(ch)
                if nxt is None:
                    nxt = len(goto)
                    goto.append({})
                    fail.append(0)
                    hit.append(False)
                    goto[node][ch] = nxt
                node = nxt
            hit[node] = True
        queue = deque(goto[0].values())
        while queue:
            node = queue.popleft()
            for ch, nxt in goto[node].items():
                queue.append(nxt)
                back = fail[node]
                while back and ch not in goto[back]:
                    back = fail[back]
                fail[nxt] = goto[back].get(ch, 0)
                hit[nxt] = hit[nxt] or hit[fail[nxt]]
        self.goto, self.fail, self.hit = goto, fail, hit

    def found(self, text):
        goto, fail, hit = self.goto, self.fail, self.hit
        node = 0
        for ch in text:
            while node and ch not in goto[node]:
                node = fail[node]
            node = goto[node].get(ch, 0)
            if hit[node]:
                return True
        return False
