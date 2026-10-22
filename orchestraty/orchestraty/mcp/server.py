"""``orch mcp``: Orchestraty's MCP server, over stdio.

JSON-RPC 2.0, one message per line, UTF-8: requests come in on stdin, answers go out on stdout,
and nothing else ever goes to stdout (logs go to stderr). The server answers ``initialize``,
``ping``, ``tools/list`` and ``tools/call``; it takes notifications (``notifications/initialized``,
``notifications/cancelled`` and any other) without an answer, and answers any other request with
"method not found". A line that is not JSON gets a parse error, a message that is not a JSON-RPC
request "invalid request", and a call that does not fit the method or the tool's input schema
(an unknown tool, a missing, unknown or mistyped argument) "invalid params". When a tool cannot
answer (no session works in this folder, say), its result says why and is marked ``isError``.

It reads the record (read-only) and appends declarations, claims and releases to orch's spool;
it starts no coordinator, opens no socket and calls nothing on the network. It ends when stdin
closes.
"""
import json
import os
import sys
import time
import traceback

from orchestraty import __version__
from orchestraty.delivery import notes as templates
from orchestraty.mcp import tools
from orchestraty.paths import State

# The protocol versions this server speaks, newest first. A client asking for one of them gets
# it; any other client gets the newest, and decides whether it can go on.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
STRUCTURED_SINCE = "2025-06-18"         # structuredContent; for older clients the JSON is a second text block

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603

INSTRUCTIONS = ("Orchestraty sees the other coding-agent sessions on this repository and what they change. Call "
                "check_before_edit before you edit code other sessions may share, declare before a larger change, "
                "and claim what other sessions must leave to you. "
                "Answers cite files, functions and rules, as of the coordinator's last pass.")


class InvalidParams(Exception):
    pass


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _valid_id(rid):
    return isinstance(rid, (str, int, float)) and not isinstance(rid, bool)


def _not_json(constant):
    raise ValueError(f"{constant} is not JSON")           # NaN and Infinity: Python reads them, JSON has none


class Server:
    """Answers one parsed message at a time; knows nothing of stdin and stdout."""

    def __init__(self, state, folder, log=None, now=None):
        self.state, self.folder = state, folder
        self.log = log or (lambda text: None)
        self.now = now                                   # tests pin the clock; None: the real one
        self.version = PROTOCOL_VERSIONS[0]
        self.methods = {"initialize": self.initialize, "ping": self.ping, "tools/list": self.tools_list,
                        "tools/call": self.tools_call}

    def handle(self, message):
        """The answer to one message (an object, or a batch: a list of them), or None."""
        if isinstance(message, list):
            if not message:
                return _error(None, INVALID_REQUEST, "empty batch")
            answers = [a for a in (self.one(m) for m in message) if a is not None]
            return answers or None
        return self.one(message)

    def one(self, m):
        if not isinstance(m, dict) or m.get("jsonrpc") != "2.0":
            rid = m.get("id") if isinstance(m, dict) and _valid_id(m.get("id")) else None
            return _error(rid, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        if "method" not in m:
            if "id" in m and ("result" in m or "error" in m):
                return None                              # a response: this server sends no requests to match it to
            return _error(m.get("id") if _valid_id(m.get("id")) else None, INVALID_REQUEST, "no method")
        method, notification = m["method"], "id" not in m
        if notification:
            return None                                  # notifications/initialized, cancelled, any other: no answer
        rid = m["id"]
        if not _valid_id(rid):
            return _error(None, INVALID_REQUEST, "the id must be a string or a number")
        if not isinstance(method, str):
            return _error(rid, INVALID_REQUEST, "the method must be a string")
        params = m.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, (dict, list)):
            return _error(rid, INVALID_REQUEST, "params must be an object")
        handler = self.methods.get(method)
        if handler is None:
            return _error(rid, METHOD_NOT_FOUND, f"method not found: {templates.clean(method, 60)}")
        if not isinstance(params, dict):
            return _error(rid, INVALID_PARAMS, "params must be an object")
        try:
            return {"jsonrpc": "2.0", "id": rid, "result": handler(params)}
        except InvalidParams as exc:
            return _error(rid, INVALID_PARAMS, str(exc))
        except Exception:                                # a bug: the client hears of it, the details go to stderr
            self.log(traceback.format_exc().rstrip())
            return _error(rid, INTERNAL_ERROR, "internal error; the details are in the server's log (stderr)")

    def initialize(self, params):
        asked = params.get("protocolVersion")
        if not isinstance(asked, str):
            raise InvalidParams("protocolVersion must be a string")
        self.version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        who = f"{templates.clean(client.get('name', '?'), 60)} {templates.clean(client.get('version', ''), 30)}"
        self.log(f"initialize: {who}, protocol {templates.clean(asked, 30)} -> {self.version}")
        return {"protocolVersion": self.version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "orchestraty", "title": "Orchestraty", "version": __version__},
                "instructions": INSTRUCTIONS}

    def ping(self, params):
        return {}

    def tools_list(self, params):
        return {"tools": tools.listing()}

    def tools_call(self, params):
        name, args = params.get("name"), params.get("arguments")
        if not isinstance(name, str):
            raise InvalidParams("name must be a string")
        started = time.monotonic()
        try:
            data, text = tools.call(self.state, self.folder, name, args, now=self.now)
        except tools.ArgumentError as exc:
            self.log(f"tools/call {templates.clean(name, 60)}: invalid params: {exc}")
            raise InvalidParams(str(exc))
        except tools.ToolError as exc:
            self.log(f"tools/call {name}: no answer: {exc}")
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        self.log(f"tools/call {name}: {1000 * (time.monotonic() - started):.0f} ms")
        result = {"content": [{"type": "text", "text": text}]}
        if self.version >= STRUCTURED_SINCE:
            result["structuredContent"] = data
        else:
            result["content"].append({"type": "text", "text": json.dumps(data, sort_keys=True)})
        return result


def serve(server, stdin, stdout):
    """Read messages from `stdin` (binary, one per line) and write the answers to `stdout`
    (binary) until stdin closes."""
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"), parse_constant=_not_json)
        except (UnicodeDecodeError, ValueError, RecursionError):
            answer = _error(None, PARSE_ERROR, "parse error: each line must be one JSON message")
        else:
            answer = server.handle(message)
        if answer is not None:
            stdout.write(json.dumps(answer, separators=(",", ":")).encode("utf-8") + b"\n")
            stdout.flush()


def _protocol_stdout():
    """This process's stdout, kept for protocol messages only. From here on, print() and any
    child process writing to the stdout it inherits write to stderr instead."""
    sys.stdout.flush()
    try:
        keep = os.dup(1)
        os.dup2(2, 1)
    except OSError:
        out = sys.stdout.buffer
    else:
        if os.name == "nt":
            import msvcrt
            msvcrt.setmode(keep, os.O_BINARY)
        out = open(keep, "wb")
    sys.stdout = sys.stderr
    return out


def main(folder=None):
    """``orch mcp``: serve this process's stdin and stdout until stdin closes."""
    out = _protocol_stdout()
    folder = os.path.abspath(folder or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())

    def log(text):
        if sys.stderr is None:
            return
        try:
            sys.stderr.write(f"orch mcp: {text}\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
    log(f"serving {folder}, state in {State().root}")
    try:
        serve(Server(State(), folder, log), sys.stdin.buffer, out)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0
