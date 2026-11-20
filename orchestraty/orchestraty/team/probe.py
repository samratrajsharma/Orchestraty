"""Whether anyone can read a repository without signing in: the anonymous-read probe.

An offline tool cannot know whether a repository is public. So the team policy must say
(``visibility``), and this probe looks for a contradiction: it asks the remote for its branch list
over https with no credentials at all. It runs only from ``orch team on`` and, once a week, from
the team fetch; never from a hook or from doctor, which show the last answer kept in
``shadows/<key>.team.json``.

* ``git@host:owner/repo(.git)`` and ``ssh://git@host/owner/repo`` are asked as
  ``https://host/owner/repo.git``, and an http or https URL as it is, without its user name or
  token, which are never sent nor kept. A file:// URL, a local path or any other is not asked.
* git runs with no credential helper, no askpass program and no terminal prompt, only http and
  https, gives up a stalled transfer, and has 30 seconds.
* The answer is True when the branch list comes back, False when the remote asks for credentials
  or says there is no such repository, and None for anything else (no network, no answer, an
  unknown host, a URL not asked). None changes nothing.
"""
import re
import subprocess
import tempfile

from orchestraty import remote
from orchestraty.team import teamfile

TIMEOUT = 30
WEEK = 7 * 24 * 3600
_HTTP = re.compile(r"(https?)://([^/\s]+)(/\S*)?")
_SSH = re.compile(r"(?:ssh|git\+ssh|ssh\+git)://([^/\s]+)(/\S+)")
_SCP = re.compile(r"(?:[^@/\s:]+@)?([A-Za-z0-9][A-Za-z0-9.-]*):(?!//)([^\s]+)")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]{1,5})?")
_REFUSED = re.compile(r"unable to get password|Authentication failed|could not read Username|terminal prompts disabled|"
                      r"Repository not found|\b40[134]\b")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def https_form(url):
    """The URL the probe asks for `url`, without user name or token, or None when it is not asked."""
    url = (url or "").strip()
    m = _HTTP.fullmatch(url)
    if m:
        host = m.group(2).rsplit("@", 1)[-1]
        return f"{m.group(1)}://{host}{m.group(3) or ''}" if _HOST.fullmatch(host) else None
    m = _SSH.fullmatch(url)
    if m:
        host = m.group(1).rsplit("@", 1)[-1].split(":", 1)[0]          # an ssh port is no https port
        return _https(host, m.group(2))
    if "://" in url or url.startswith(("/", ".", "~")) or remote._DRIVE.match(url):
        return None
    m = _SCP.fullmatch(url)
    return _https(m.group(1), m.group(2)) if m else None


def _https(host, path):
    if not _HOST.fullmatch(host):
        return None
    path = "/" + path.lstrip("/")
    return f"https://{host}{path if path.endswith('.git') else path + '.git'}"


def anonymous_read(url, timeout=TIMEOUT):
    """True when anyone can list the repository's branches without credentials, False when the
    remote refuses, None when it cannot tell (or `url` is not asked)."""
    target = https_form(url)
    if target is None:
        return None
    argv = ["git", "-c", "credential.helper=", "-c", "core.askPass=", "-c", "credential.interactive=never",
            "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=20", "ls-remote", "--heads", target]
    env = remote.fetch_env()
    env.update(GIT_ALLOW_PROTOCOL="http:https", GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", GCM_INTERACTIVE="never")
    folder = tempfile.gettempdir()                     # outside any repository: no repository's config applies
    env["GIT_CEILING_DIRECTORIES"] = folder
    try:
        proc = subprocess.run(argv, cwd=folder, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 0:
        return True
    return False if _REFUSED.search(proc.stderr.decode("utf-8", "replace")) else None


def check(shadow, url, now, timeout=TIMEOUT):
    """Ask now, and keep the answer and its time beside the shadow. Returns the answer."""
    answer = anonymous_read(url, timeout)
    teamfile.update(shadow, probe={"anonymous_read": answer, "checked": int(now)})
    return answer


def last(shadow):
    """(answer, when) of the last probe kept, (None, 0) before any."""
    kept = teamfile.read(shadow).get("probe")
    if not isinstance(kept, dict):
        return None, 0
    answer, when = kept.get("anonymous_read"), kept.get("checked")
    answer = answer if answer in (True, False) else None
    return answer, when if isinstance(when, int) and not isinstance(when, bool) else 0


def due(shadow, now):
    """Whether the weekly probe is due."""
    _, when = last(shadow)
    return not when or now - when >= WEEK
