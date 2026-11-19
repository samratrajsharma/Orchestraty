"""Who this machine shares as, in one repository: a person and a device.

The person is the handle the people file gives the user's git email, or ``unlisted-`` and the
first eight hex digits of the sha256 of that email for someone the file does not list. The
email is ``git config --get user.email`` read in the repository's main worktree, so an includeIf
in the user's git config counts. It is never read with ``git var``, which makes up
user@hostname when no email is set: with no email, team sync stays off for the repository.

The device is ``<state>/team/device.json``, made once: a random id (``secrets.token_hex(6)``,
never anything from the host's name), an optional label, and a secret that never leaves the
machine. The secret keys HMAC-SHA256 for the opaque ids sessions are shared under and for the
fingerprints of values held back from a record. One state folder is one device, so Windows and
each WSL distribution are separate devices; ``Device.new`` gives a copied state folder an id
and a secret of its own.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import namedtuple
from dataclasses import dataclass

from orchestraty import gitio
from orchestraty.team import grammar
from orchestraty.team.people import normalise_email

FIX_EMAIL = "git config user.email you@example.com"
_SECRET = re.compile(r"[0-9a-f]{32}")

Me = namedtuple("Me", "person listed")


class NoEmail(Exception):
    """No git email is set for the repository, so team sync stays off there."""

    fix = FIX_EMAIL

    def __str__(self):
        return f"no git email is set for this repository; fix: {self.fix}"


class DeviceError(Exception):
    """device.json cannot be read."""


def main_worktree(common_dir):
    """The folder git reads a repository's config from as its main worktree does: the folder
    holding .git, or the git folder itself for a bare repository."""
    common_dir = os.path.normpath(common_dir)
    return os.path.dirname(common_dir) if os.path.basename(common_dir) == ".git" else common_dir


def my_email(common_dir):
    """The user's git email for a repository, as git config gives it in the main worktree.
    Raises NoEmail when none is set."""
    try:
        email = gitio.run(["config", "--get", "user.email"], main_worktree(common_dir)).strip()
    except gitio.GitError:
        email = ""
    if not email:
        raise NoEmail()
    return email


def unlisted_handle(email):
    """The handle of someone the people file does not list: unlisted- and eight hex digits of
    the sha256 of their normalised email. The same email gives the same handle everywhere."""
    return "unlisted-" + hashlib.sha256(normalise_email(email).encode("utf-8")).hexdigest()[:8]


def who_am_i(people, email):
    """Me(person, listed): the handle the people file gives `email`, or the unlisted one."""
    handle = people.handle_for(email)
    return Me(handle, True) if handle else Me(unlisted_handle(email), False)


@dataclass(frozen=True)
class Device:
    id: str
    label: str
    secret: str                 # never leaves this machine
    created: int

    @staticmethod
    def path_for(state):
        return os.path.join(state.root, "team", "device.json")

    @classmethod
    def load(cls, state):
        """This state folder's device, or None when it has none. Raises DeviceError for a file
        that is not a device."""
        path = cls.path_for(state)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise DeviceError(f"{path} cannot be read ({type(exc).__name__}): orch team device --new makes a new "
                              "one") from None
        ok = (isinstance(data, dict) and grammar.device(data.get("id")) and isinstance(data.get("secret"), str)
              and _SECRET.fullmatch(data["secret"]) and (data.get("label") == "" or grammar.label(data.get("label")))
              and isinstance(data.get("created"), int) and not isinstance(data.get("created"), bool))
        if not ok:
            raise DeviceError(f"{path} is not a device file: orch team device --new makes a new one")
        return cls(data["id"], data["label"], data["secret"], data["created"])

    @classmethod
    def load_or_create(cls, state, now=None):
        """This state folder's device, made now when it has none."""
        device = cls.load(state)
        if device is None:
            device = cls._make(state, "", now)
        return device

    @classmethod
    def new(cls, state, now=None):
        """A new id and secret for this state folder (its label kept): for a copied state
        folder, or one whose device file is damaged."""
        try:
            old = cls.load(state)
        except DeviceError:
            old = None
        return cls._make(state, old.label if old else "", now)

    @classmethod
    def set_label(cls, state, label):
        """Give the device a label ("" for none). Raises ValueError for one that is not
        lowercase letters and digits in words joined by single dashes, at most 24."""
        if label and not grammar.label(label):
            raise ValueError("a device label is lowercase letters and digits in words joined by single dashes, "
                             "at most 24")
        old = cls.load_or_create(state)
        device = cls(old.id, label, old.secret, old.created)
        device._save(state)
        return device

    @classmethod
    def _make(cls, state, label, now):
        device = cls(secrets.token_hex(6), label, secrets.token_hex(16), int(time.time() if now is None else now))
        device._save(state)
        return device

    def _save(self, state):
        path = self.path_for(state)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)        # private from the start
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"id": self.id, "label": self.label, "secret": self.secret, "created": self.created}, fh)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def _mac(self, value):
        return hmac.new(bytes.fromhex(self.secret), value.encode("utf-8", "surrogateescape"), hashlib.sha256).hexdigest()

    def session_id(self, local):
        """The opaque id a local session is shared under: s- and 12 hex digits of an HMAC of
        its local id, the same every time on this device, telling nothing of the local id."""
        return "s-" + self._mac("session\0" + local)[:12]

    def fingerprint(self, value):
        """Eight hex digits that tell two withheld values apart without keeping either."""
        return self._mac("withheld\0" + value)[:8]
