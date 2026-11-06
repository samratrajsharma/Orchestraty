"""orch ui: the dashboard, served to the browser of the person who started it.

It binds 127.0.0.1 only, on a free port unless ``--port`` names one, and prints a link with
a token made for this launch (``secrets.token_urlsafe(32)``). The first visit to that link
sets a cookie (``HttpOnly; SameSite=Strict; Path=/``) and redirects to the page; the link
works once. Every request without the cookie gets a 403, and so does every request whose
``Host`` is not ``127.0.0.1:<port>`` or ``localhost:<port>`` (DNS rebinding). Requests that
change something are POST only, and also need the header ``X-Orch-UI: 1`` and an ``Origin``
equal to the page's own. Every response carries a Content-Security-Policy that allows only
this server, ``nosniff`` and ``no-referrer``; ``/api/`` responses are never stored. Only the
files listed in ``STATIC`` are served, by exact path.

The API answers in JSON, always with a ``schema`` number:

| Request                                      | Answer                                                     |
|----------------------------------------------|------------------------------------------------------------|
| GET /api/status?ended=0|1                    | orch status --json as it is, and {"ui": {version, demo}}   |
| GET /api/sessions/<id>                       | a session, its changes, notes, last events and push line   |
| GET /api/findings?state=open|resolved|all    | the findings, with their detail and notes                  |
| GET /api/findings/<key>                      | one finding                                                |
| GET /api/notes?session=&kind=&state=         | the notes, with time to awareness and its median           |
| GET /api/push                                | the push lines, with their worktree and session            |
| GET /api/health                              | the coordinator, the record and the state folder           |
| GET /api/config, POST /api/config            | the switches and watched repositories; set steer or wake   |
| POST /api/label                              | the user's verdict on a finding, as orch label gives it    |
| POST /api/claims, POST /api/release          | claim a file, folder or function for a session; end a claim|
| POST /api/doctor                             | orch doctor's checks, without timing a first index          |
| GET /api/demo, POST /api/demo/next           | the sandbox's steps; run the next (orch ui --demo only)    |

It reads the state folder through ``ui.data`` (the record read-only, per request); the
switches go through the same code as orch config, and labels, claims and releases are lines in
orch's spool for the coordinator to store, as orch label, orch claim and orch release write them. JSON comes with an ETag, and an unchanged
answer gets a 304. The ETag leaves out the seconds since each session's last event, which the
page works out from the time of that event, so a status that only grew older is not sent
again.
"""
import atexit
import hashlib
import hmac
import http.cookies
import json
import os
import secrets
import signal
import socket
import socketserver
import sqlite3
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from orchestraty.paths import State
from orchestraty.ui import data

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")
SECURITY_HEADERS = (("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                    ("Referrer-Policy", "no-referrer"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/mark.svg": ("mark.svg", "image/svg+xml"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml"),
          "/DoppioOne-Regular.woff2": ("DoppioOne-Regular.woff2", "font/woff2"),
          "/DoppioOne-OFL.txt": ("DoppioOne-OFL.txt", "text/plain; charset=utf-8")}
JSON_TYPE = "application/json; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"
MAX_BODY = 4096
VOLATILE = ("seconds_since_last_event",)          # left out of ETags: the page works them out
SWITCHES = ("steer", "wake")

NO_HOST = "Forbidden: this server answers only to 127.0.0.1 and localhost on its own port."
NO_COOKIE = "Forbidden: open the link that orch ui printed in your terminal."
WRONG = "Forbidden: this is not the link orch ui printed. Open the one in your terminal."
USED = ("Forbidden: this link was used already. To open the dashboard in another browser, stop orch ui "
        "(Ctrl+C) and run it again (with --no-browser to open the new link yourself).")
DEMO_WAKE = "In the demo this changes only the demo's own state folder; no Claude Code settings are touched."
DEMO_DOCTOR = "The demo runs in a sandbox: run orch doctor in a terminal to check this computer."


def _dump(payload):
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _without(value, keys):
    if isinstance(value, dict):
        return {k: _without(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [_without(v, keys) for v in value]
    return value


def _etag(body, weak=False):
    return ('W/"%s"' if weak else '"%s"') % hashlib.sha256(body).hexdigest()[:32]


class BadRequest(Exception):
    """A request the API cannot answer as asked (400)."""


def _err(message):
    return {"schema": data.SCHEMA, "error": message}


def _same_secret(given, secret):
    return hmac.compare_digest(given.encode("utf-8", "surrogatepass"), secret.encode("ascii"))


class Server(ThreadingHTTPServer):
    """The HTTP server, with the secrets of this launch."""

    daemon_threads = True
    allow_reuse_address = os.name != "nt"          # on Windows it would let another program take the port

    def __init__(self, state, port=0):
        self.state = state
        self.demo = None
        self.token = secrets.token_urlsafe(32)        # the link's
        self.secret = secrets.token_urlsafe(32)       # the cookie's
        self._redeemed = False
        self._lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)
        self.port = self.server_address[1]
        self.cookie = f"orch_ui_{self.port}"          # cookies ignore the port: one name per server
        self.hosts = (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)      # not HTTPServer's, which looks the host name up
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def redeem(self, token):
        """"ok" the first time the right token comes, "used" after that, "wrong" for any other."""
        with self._lock:
            if not _same_secret(token, self.token):
                return "wrong"
            if self._redeemed:
                return "used"
            self._redeemed = True
            return "ok"

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return                                     # the browser went away mid-answer
        print(f"orch ui: a request failed: {type(exc).__name__}: {exc}", file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    server_version = "orch-ui"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60                                       # an idle kept-alive connection is closed

    def log_message(self, format, *args):
        pass                                           # quiet: the page asks every two seconds

    # ------------------------------------------------------------------ every response
    def send_header(self, keyword, value):
        if not hasattr(self, "_sent"):
            self._sent = set()
        self._sent.add(keyword.lower())
        super().send_header(keyword, value)

    def end_headers(self):
        for keyword, value in SECURITY_HEADERS:
            self.send_header(keyword, value)
        if "cache-control" not in getattr(self, "_sent", ()) and \
                urlsplit(getattr(self, "path", "") or "").path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()
        self._sent = set()

    def _drain(self):
        """Read what is left of a POST's body (up to MAX_BODY) before answering it. Closing a
        connection with unread data resets it, and the client may then lose the answer."""
        try:
            left = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < left <= MAX_BODY:
            try:
                self.rfile.read(left)
            except OSError:
                pass

    def _respond(self, code, body=b"", ctype=None, headers=()):
        if self.command == "POST" and not getattr(self, "_body_read", False):
            self._body_read = True
            self._drain()                              # a refusal comes before the body was read
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        for keyword, value in headers:
            self.send_header(keyword, value)
        if self.command == "POST":
            self.send_header("Connection", "close")
        if code != 304:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if code != 304 and body:
            self.wfile.write(body)

    def _text(self, code, text):
        self._respond(code, (text + "\n").encode("utf-8"), TEXT_TYPE, [("Cache-Control", "no-store")])

    def _send(self, payload, code=200):
        body = _dump(payload)
        if code != 200:
            return self._respond(code, body, JSON_TYPE)
        tag = _etag(_dump(_without(payload, VOLATILE)), weak=True)
        if self._not_modified(tag):
            return self._respond(304, headers=[("ETag", tag)])
        return self._respond(200, body, JSON_TYPE, [("ETag", tag)])

    def _error(self, code, message):
        self._send(_err(message), code)

    def _not_modified(self, tag):
        header = self.headers.get("If-None-Match")
        if not header:
            return False
        bare = tag[2:] if tag.startswith("W/") else tag
        for part in header.split(","):
            part = part.strip()
            if part == "*" or (part[2:] if part.startswith("W/") else part) == bare:
                return True
        return False

    # ------------------------------------------------------------------ who may ask
    def _host(self):
        """The Host header when it names this server, else None."""
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return None
        host = hosts[0].strip().lower()
        return host if host in self.server.hosts else None

    def _has_cookie(self):
        try:
            jar = http.cookies.SimpleCookie("; ".join(self.headers.get_all("Cookie") or []))
        except http.cookies.CookieError:
            return False
        morsel = jar.get(self.server.cookie)
        return morsel is not None and _same_secret(morsel.value, self.server.secret)

    def _redeem(self, token):
        outcome = self.server.redeem(token)
        if outcome == "ok":
            cookie = f"{self.server.cookie}={self.server.secret}; HttpOnly; SameSite=Strict; Path=/"
            return self._respond(303, headers=[("Set-Cookie", cookie), ("Location", "/"), ("Cache-Control", "no-store")])
        if self._has_cookie():                         # this browser came through the link before
            return self._respond(303, headers=[("Location", "/"), ("Cache-Control", "no-store")])
        return self._text(403, USED if outcome == "used" else WRONG)

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        if self._host() is None:
            return self._text(403, NO_HOST)
        url = urlsplit(self.path)
        query = parse_qs(url.query, keep_blank_values=True)
        if url.path == "/" and "token" in query:
            return self._redeem(query["token"][-1])
        if not self._has_cookie():
            return self._text(403, NO_COOKIE)
        if url.path in STATIC:
            return self._static(*STATIC[url.path])
        if url.path.startswith("/api/"):
            code, payload = self._answer(self._api, url.path, {k: v[-1] for k, v in query.items()})
            return self._send(payload, code)
        return self._text(404, "Not found.")

    def _answer(self, build, *args):
        """(status, payload) from build(*args), with the API's errors as payloads."""
        try:
            return build(*args)
        except BadRequest as exc:
            return 400, _err(str(exc))
        except sqlite3.Error as exc:
            return 503, _err(f"the record could not be read now ({exc}); try again")
        except Exception as exc:
            print(f"orch ui: {self.command} {urlsplit(self.path).path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 500, _err(f"{type(exc).__name__}: {exc}")

    def _static(self, name, ctype):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as fh:
                body = fh.read()
        except OSError:
            return self._text(404, "Not found.")
        tag = _etag(body)
        if self._not_modified(tag):
            return self._respond(304, headers=[("ETag", tag), ("Cache-Control", "no-cache")])
        return self._respond(200, body, ctype, [("ETag", tag), ("Cache-Control", "no-cache")])

    def _api(self, path, query):
        """(status, payload) for one GET under /api/."""
        state, demo = self.server.state, self.server.demo is not None

        def arg(name, allowed=None):
            value = query.get(name) or None
            if value is not None and allowed is not None and value not in allowed:
                raise BadRequest(f"{name} must be one of: {', '.join(allowed)}")
            return value

        def found(payload, what):
            return (200, payload) if payload is not None else (404, _err(f"no such {what}"))

        if path == "/api/status":
            return 200, data.status_payload(state, include_ended=arg("ended", ("0", "1")) == "1", demo=demo)
        if path.startswith("/api/sessions/"):
            return found(data.session_payload(state, unquote(path[len("/api/sessions/"):])), "session")
        if path == "/api/findings":
            return 200, data.findings_payload(state, arg("state", tuple(data.FINDING_STATES)) or "open")
        if path.startswith("/api/findings/"):
            return found(data.finding_payload(state, unquote(path[len("/api/findings/"):])), "finding")
        if path == "/api/notes":
            return 200, data.notes_payload(state, session=arg("session"), kind=arg("kind", data.NOTE_KINDS),
                                           note_state=arg("state", data.NOTE_STATES))
        if path == "/api/push":
            return 200, data.push_payload(state)
        if path == "/api/health":
            return 200, data.health_payload(state, demo=demo)
        if path == "/api/config":
            return 200, data.config_payload(state)
        if path == "/api/demo":
            return (200, self.server.demo.payload()) if demo else (404, _err("not a demo: run orch ui --demo"))
        return 404, _err("no such endpoint")

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        self.close_connection = True                   # a body left unread must not reach the next request
        host = self._host()
        if host is None:
            return self._text(403, NO_HOST)
        if not self._has_cookie():
            return self._text(403, NO_COOKIE)
        if self.headers.get("X-Orch-UI") != "1":
            return self._error(403, "a change needs the X-Orch-UI: 1 header")
        if self.headers.get("Origin") != "http://" + host:
            return self._error(403, "a change must come from this page")
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._error(415, "send JSON")
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self._error(411, "say how long the body is")
        if not 0 <= length <= MAX_BODY:
            return self._error(413, "the body is too long")
        self._body_read = True
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._error(400, "the body is not JSON")
        if not isinstance(body, dict):
            return self._error(400, "send a JSON object")
        code, payload = self._answer(self._post, urlsplit(self.path).path, body)
        return self._send(payload, code)

    def _post(self, path, body):
        """(status, payload) for one POST under /api/."""
        if path == "/api/config":
            return self._set_switch(body)
        if path == "/api/label":
            return self._label(body)
        if path == "/api/claims":
            return self._claim(body)
        if path == "/api/release":
            return self._release(body)
        if path == "/api/doctor":
            return self._doctor()
        if path == "/api/demo/next":
            return self._demo_next()
        return 404, _err("no such endpoint")

    def _recorded(self):
        """How soon the coordinator stores a spool line; in the demo, it is stepped here."""
        from orchestraty import cli
        if self.server.demo is not None:
            self.server.demo.catch_up()
            return "The coordinator records it now."
        return cli._recorded_when(self.server.state)

    def _label(self, body):
        """{"finding": key, "verdict": "real"|"harmless"|"false", "why": text}, as orch label takes it."""
        from orchestraty import feedback
        key, verdict, why = body.get("finding"), body.get("verdict"), body.get("why", "")
        if not isinstance(key, str) or not key or len(key) > 2000 or verdict not in feedback.VERDICTS                 or not isinstance(why, str) or set(body) - {"finding", "verdict", "why"}:
            raise BadRequest('send {"finding": <key>, "verdict": "real", "harmless" or "false", "why": <text>}')
        with data._record(self.server.state) as record:
            if record is None or record.finding(key) is None:
                return 404, _err("no such finding")
        feedback.label(self.server.state, key, verdict, why)
        return 200, {"schema": data.SCHEMA, "finding": key, "verdict": verdict,
                     "lines": [f"Labelled {verdict}.", self._recorded()]}

    def _claim(self, body):
        """{"session": id, "target": path or function, "minutes": n, "hard": bool}, as orch claim
        resolves and records it: the target is read from the session's worktree."""
        from orchestraty import claims, config
        sid, target = body.get("session"), body.get("target")
        minutes, hard = body.get("minutes"), body.get("hard", False)
        if not isinstance(sid, str) or not sid or not isinstance(target, str) or not target.strip()                 or len(target) > 300 or not isinstance(hard, bool)                 or (minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int)
                                             or not 1 <= minutes <= 1440))                 or set(body) - {"session", "target", "minutes", "hard"}:
            raise BadRequest('send {"session": <id>, "target": <file, folder or function>, "minutes": 1 to 1440, '
                             '"hard": true or false}')
        state = self.server.state
        with data._record(state) as record:
            s = record.session(sid) if record else None
            repos = {r["id"]: r["common_dir"] for r in record.repos()} if record else {}
        if s is None or s["state"] != "live" or s["agent"] == "cloud":
            return 404, _err("no live session has this id")
        top, common_dir = s["worktree"], repos.get(s["repo_id"], "")
        cfg = config.load(state)
        if not top or not common_dir or not config.is_watched(cfg, common_dir):
            raise BadRequest("this session's repository is not watched: run orch watch in it first")
        try:
            kind, resolved, file = claims.resolve(top, top, target.strip())
        except claims.Refused as exc:
            raise BadRequest(str(exc)) from None
        minutes = minutes or claims.default_minutes(cfg)
        claims.append_claim(state, sid, common_dir, kind, resolved, file, hard, minutes)
        what = claims.describe({"kind": kind, "target": resolved, "file": file})
        return 200, {"schema": data.SCHEMA, "claimed": {"session": sid, "kind": kind, "target": resolved, "file": file,
                                                         "hard": hard, "minutes": minutes, "what": what},
                     "lines": [f"Claimed {what} for {minutes} minutes" + (", hard." if hard else "."), self._recorded()]}

    def _release(self, body):
        """{"id": n}: end the claim with that number, as orch release #n does."""
        from orchestraty import claims, cli
        cid = body.get("id")
        if isinstance(cid, bool) or not isinstance(cid, int) or cid < 1 or set(body) - {"id"}:
            raise BadRequest('send {"id": <the number of the claim>}')
        chosen = [(r, c) for r, _, c in cli._claims_now(self.server.state)[1] if c["id"] == cid]
        if not chosen:
            return 404, _err(f"no claim {cid} holds")
        repo, claim = chosen[0]
        claims.append_release(self.server.state, repo["common_dir"], claim_id=cid)
        return 200, {"schema": data.SCHEMA, "released": cid,
                     "lines": [f"Released {claims.describe(claim)}.", self._recorded()]}

    def _doctor(self):
        """orch doctor's checks for this state folder, without timing a first index. In the demo
        they would mix the sandbox with this computer's agents, so it says to run orch doctor."""
        from orchestraty import doctor
        if self.server.demo is not None:
            return 200, {"schema": data.SCHEMA, "checks": [], "problems": 0, "lines": [DEMO_DOCTOR]}
        found = [{"kind": c.kind, "text": c.text, "fix": c.fix}
                 for c in doctor.checks(self.server.state, os.getcwd(), index=False)]
        problems = sum(1 for c in found if c["kind"] == "fix")
        return 200, {"schema": data.SCHEMA, "checks": found, "problems": problems,
                     "lines": ["No problems found." if not problems else f"{problems} problem(s): each says its fix."]}

    def _demo_next(self):
        from orchestraty.ui import demo
        if self.server.demo is None:
            return 404, _err("not a demo: run orch ui --demo")
        try:
            return 200, self.server.demo.next_step()
        except (demo.Busy, demo.Finished) as exc:
            return 409, _err(str(exc))

    def _set_switch(self, body):
        """{"steer": "on"|"off"} or {"wake": "on"|"off"}, checked as orch config checks them."""
        from orchestraty import cli, config
        if len(body) != 1 or next(iter(body)) not in SWITCHES or next(iter(body.values())) not in ("on", "off"):
            raise BadRequest('send {"steer": "on"} or "off", or {"wake": "on"} or "off"')
        [(name, value)] = body.items()
        lines, refused = cli.set_switch(self.server.state, name, value == "on")
        if self.server.demo is not None and name == "wake":
            lines = lines[:1] + [DEMO_WAKE]                # the demo has no hooks to reinstall
        cfg = config.load(self.server.state)
        return 200, {"schema": data.SCHEMA, "steer": cfg["steer"], "wake": cfg["wake"], "lines": lines,
                     "error": f"orch config: {refused}" if refused else None}


# --------------------------------------------------------------------------------------- #
# Running it
# --------------------------------------------------------------------------------------- #
def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _open(url):
    try:
        opened = webbrowser.open(url)
    except Exception:                                  # no browser registered, or it failed to start
        opened = False
    if not opened:
        print("No browser could be opened: open the link above yourself.", flush=True)


def serve(server, browser=True, out=None):
    """Print the link, open it in the browser, and serve until Ctrl+C."""
    out = out or sys.stdout
    print(f"orch ui: {server.url}", file=out)
    if server.demo is not None:
        print(f"Sandbox demo in {server.demo.root}: removed when orch ui stops.", file=out)
    print("The link works once, in one browser on this computer (the server listens on 127.0.0.1 only). "
          "Ctrl+C stops it.", file=out, flush=True)
    if threading.current_thread() is threading.main_thread():
        for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):       # closing the terminal stops it cleanly too
            sig = getattr(signal, name, None)
            if sig is not None and signal.getsignal(sig) == signal.SIG_DFL:
                signal.signal(sig, _interrupt)
    if browser:
        threading.Thread(target=_open, args=(server.url,), name="open-browser", daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("orch ui: stopped.", file=out)
    finally:
        server.server_close()
    return 0


def main(port=0, browser=True, demo=False):
    sandbox = None
    if demo:
        from orchestraty.ui.demo import Demo
        try:
            sandbox = Demo()
        except Exception as exc:
            print(f"orch ui --demo: no sandbox: {exc}", file=sys.stderr)
            return 2
        atexit.register(sandbox.close)                 # also when something unexpected ends the process
    try:
        try:
            server = Server(sandbox.state if sandbox else State(), port)
        except OSError as exc:
            print(f"orch ui: cannot listen on 127.0.0.1:{port}: {exc.strerror or exc}", file=sys.stderr)
            return 2
        server.demo = sandbox
        return serve(server, browser)
    finally:
        if sandbox is not None:
            sandbox.close()
