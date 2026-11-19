"""The people file, ``.orchestraty/people.yml``: who is who on the team.

It gives each teammate a handle and lists the git emails their commits carry, the CODEOWNERS
logins and teams that mean them, the keys their records will be signed with (read and checked
now, used once records are signed) and a name shown to people only, never to an agent::

    version: 1
    people:
      - handle: alice
        emails: [alice@example.com, 1234+alice@users.noreply.github.com]
        logins: ["@alice"]
        teams: ["@acme/payments"]
        keys: ["ssh-ed25519 AAAA... alice@laptop"]
        name: Alice Example

The copy in force is the one on the remote's default branch, reviewed like code. The file is
refused whole, with the line, when anything in it is wrong: identity is what everything else
rests on, so a mistake turns team sync off rather than guessing who is who. Emails are matched
after strip() and lower(), and GitHub's ``12345+alice@users.noreply.github.com`` matches
``alice@users.noreply.github.com``; nothing else is folded.
"""
import base64
import binascii
import re
from dataclasses import dataclass

from orchestraty.team import grammar, miniyaml

MAX_BYTES = 256 * 1024
MAX_PEOPLE = 2000
MAX_EMAILS, MAX_TOKENS, MAX_KEYS = 20, 50, 10
MAX_NAME = 80
MAX_EMAIL = 254
NOREPLY = "@users.noreply.github.com"
TOP_KEYS = ("version", "people")
ENTRY_KEYS = ("handle", "emails", "logins", "teams", "keys", "name")
KEY_TYPES = ("ssh-ed25519", "sk-ssh-ed25519@openssh.com", "ecdsa-sha2-nistp256", "ssh-rsa")
LOGIN = re.compile(r"@[A-Za-z0-9-]{1,39}")
TEAM = re.compile(r"@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_NOREPLY_ID = re.compile(r"[0-9]+\+(.+)")


class Error(ValueError):
    """The people file is refused: `message` says why, `line` where (None for the whole file)."""

    def __init__(self, message, line=None):
        super().__init__(f"line {line}: {message}" if line else message)
        self.message, self.line = message, line


def normalise_email(email):
    """An email as emails are compared: stripped and lowercase."""
    return email.strip().lower()


def email_key(email):
    """What two emails must share to be the same person's: the normalised email, with a GitHub
    noreply email's numeric id dropped (1234+alice@users.noreply.github.com is alice's)."""
    email = normalise_email(email)
    if email.endswith(NOREPLY):
        m = _NOREPLY_ID.fullmatch(email[:-len(NOREPLY)])
        if m:
            return m.group(1) + NOREPLY
    return email


@dataclass(frozen=True)
class Person:
    handle: str
    emails: tuple
    logins: tuple = ()
    teams: tuple = ()
    keys: tuple = ()                 # (type, base64 body): the key's comment is dropped
    name: str = ""                   # for people only, never for an agent


class People:
    """The people of one repository, looked up by email, handle or CODEOWNERS token."""

    def __init__(self, persons):
        self.persons = tuple(persons)
        self._handles = {p.handle: p for p in self.persons}
        self._emails = {email_key(e): p.handle for p in self.persons for e in p.emails}

    @classmethod
    def empty(cls):
        """No people file: everyone is unlisted."""
        return cls(())

    @classmethod
    def parse(cls, data):
        """The people a file lists. Raises Error, naming the line, when anything is wrong."""
        try:
            doc = miniyaml.parse(data, max_bytes=MAX_BYTES)
        except miniyaml.Error as exc:
            raise Error(exc.message, exc.line) from None
        if not isinstance(doc, miniyaml.Map):
            raise Error("the people file is keys: version and people", 1)
        _known(doc, TOP_KEYS)
        if doc.get("version") != 1:
            raise Error("version must be 1", doc.lines.get("version", 1))
        entries = doc.get("people")
        if not isinstance(entries, miniyaml.Seq):
            raise Error("people must be a list of people", doc.lines.get("people", 1))
        if len(entries) > MAX_PEOPLE:
            raise Error(f"the file lists more than {MAX_PEOPLE} people", doc.lines["people"])
        persons, handles, emails = [], set(), {}
        for entry, line in zip(entries, entries.lines):
            person = _person(entry, line)
            if person.handle in handles:
                raise Error(f"the handle {person.handle} is given twice", line)
            handles.add(person.handle)
            for email in person.emails:
                key = email_key(email)
                if key in emails and emails[key] != person.handle:
                    raise Error(f"the email {email} is listed for {emails[key]} too", entry.lines["emails"])
                emails[key] = person.handle
            persons.append(person)
        return cls(persons)

    def __iter__(self):
        return iter(self.persons)

    def __len__(self):
        return len(self.persons)

    def __contains__(self, handle):
        return handle in self._handles

    def person(self, handle):
        return self._handles.get(handle)

    def emails(self, handle):
        p = self._handles.get(handle)
        return p.emails if p else ()

    def handle_for(self, email):
        """The handle of the person whose email this is, or None."""
        return self._emails.get(email_key(email)) if isinstance(email, str) else None

    def resolve(self, token):
        """The handles a CODEOWNERS owner means: a team (@org/team), a login (@name) or an
        email. Logins and teams are compared without case, as GitHub does."""
        if not isinstance(token, str):
            return []
        if token.startswith("@"):
            wanted = token.lower()
            field = "teams" if "/" in token else "logins"
            return sorted(p.handle for p in self.persons if wanted in (t.lower() for t in getattr(p, field)))
        handle = self.handle_for(token) if "@" in token else None
        return [handle] if handle else []


def _known(mapping, keys):
    for key in mapping:
        if key not in keys:
            raise Error(f"unknown key {key}: a people file may hold only {', '.join(keys)}", mapping.lines[key])


def _strings(entry, key, limit):
    """A list field of an entry: a list of strings, at most `limit` of them."""
    value = entry.get(key, [])
    line = entry.lines.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Error(f"{key} must be a list of {key}", line)
    if len(value) > limit:
        raise Error(f"more than {limit} {key}", line)
    return value, line


def _person(entry, line):
    if not isinstance(entry, miniyaml.Map):
        raise Error("each person is keys: handle, emails and the others", line)
    _known(entry, ENTRY_KEYS)
    handle = entry.get("handle")
    if not grammar.handle(handle):
        raise Error("a handle is lowercase letters and digits in words joined by single dashes, at most 39, "
                    "and does not start with unlisted-", entry.lines.get("handle", line))
    emails, at = _strings(entry, "emails", MAX_EMAILS)
    if not emails:
        raise Error(f"{handle} has no email", at or line)
    for email in emails:
        if len(email) > MAX_EMAIL or not _EMAIL.fullmatch(email) or not email.isprintable():
            raise Error(f"not an email: {email[:60]!r}", at)
    logins, at = _strings(entry, "logins", MAX_TOKENS)
    if not all(LOGIN.fullmatch(t) for t in logins):
        raise Error("a login is @ and a GitHub name: letters, digits and -", at)
    teams, at = _strings(entry, "teams", MAX_TOKENS)
    if not all(TEAM.fullmatch(t) for t in teams):
        raise Error("a team is @org/team, in lowercase", at)
    keys, at = _strings(entry, "keys", MAX_KEYS)
    parsed = tuple(_key(k, at) for k in keys)
    name = entry.get("name", "")
    if not isinstance(name, str) or len(name) > MAX_NAME or not name.isprintable():
        raise Error(f"a name is one line of printable text, at most {MAX_NAME} characters", entry.lines.get("name"))
    seen, unique = set(), []
    for email in emails:
        if email_key(email) not in seen:
            seen.add(email_key(email))
            unique.append(normalise_email(email))
    return Person(handle, tuple(unique), tuple(logins), tuple(teams), parsed, name)


def _key(text, line):
    """(type, base64 body) of a public key; its comment is dropped."""
    parts = text.split()
    if len(parts) < 2 or parts[0] not in KEY_TYPES:
        raise Error(f"a key is one of {', '.join(KEY_TYPES)} and its base64 body", line)
    body = parts[1]
    if not _BASE64.fullmatch(body):
        raise Error("a key's body is base64", line)
    try:
        raw = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        raise Error("a key's body is base64", line) from None
    if len(raw) < 16:
        raise Error("a key's body is too short to be a key", line)
    return parts[0], body
