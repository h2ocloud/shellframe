"""
api_server — optional localhost HTTP API for ShellFrame.

Opt-in. Disabled unless config["api_server"]["enabled"] is true. Wraps the
existing _execute_sfctl dispatch so a *local* external agent (e.g. OpenClaw /
龍蝦) can drive ShellFrame over HTTP/JSON:

  - read tabs            GET  /sessions, GET /status, GET /roster
  - peek a tab's output  GET  /sessions/{sid}/peek
  - send a prompt        POST /sessions/{sid}/send
  - delegate / open      POST /delegate, POST /sessions
  - rename / close       POST /sessions/{sid}/rename, DELETE /sessions/{sid}
  - bidirectional        GET  /events  (poll agent RED/YELLOW signals)

Security model (all enforced before any handler runs):
  - bound to host (default 127.0.0.1 — loopback only)
  - IP whitelist (default ["127.0.0.1", "::1"]; supports CIDR; "0.0.0.0/0" = open)
  - Bearer token required on every /api endpoint (X-API-Token or Authorization)

Docs: OpenAPI 3.0 spec at GET /openapi.json, Swagger UI at GET /docs.
Those two plus /health are token-free (still IP-gated) so a browser can load them.

This module imports nothing from main.py — it receives a callable
(`execute_fn = app._execute_sfctl`) so there is no circular import. The host
calls start() once; the bridge pushes agent events via EVENT_BUS.push().
"""

import ipaddress
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


# ---------------------------------------------------------------------------
# Bidirectional event bus — agent tabs raise RED/YELLOW signals; 龍蝦 polls them.
# ---------------------------------------------------------------------------
class _EventBus:
    """Thread-safe monotonically-ordered ring buffer of agent events.

    main.py / bridge_telegram push {sid,label,state,reason} when a tab signals.
    Clients poll GET /events?since=<cursor> and reply via POST .../send.
    """

    def __init__(self, maxlen: int = 500):
        self._lock = threading.Lock()
        self._items = []          # list[dict] each with monotonically increasing "id"
        self._next_id = 1
        self._maxlen = maxlen

    def push(self, **fields) -> dict:
        with self._lock:
            ev = dict(fields)
            ev["id"] = self._next_id
            ev["ts"] = ev.get("ts") or time.time()
            self._next_id += 1
            self._items.append(ev)
            if len(self._items) > self._maxlen:
                self._items = self._items[-self._maxlen:]
            return ev

    def since(self, cursor: int) -> tuple[list, int]:
        with self._lock:
            out = [e for e in self._items if e["id"] > cursor]
            tail = self._items[-1]["id"] if self._items else cursor
            return out, tail


EVENT_BUS = _EventBus()


# ---------------------------------------------------------------------------
# OpenAPI 3.0 spec (served as-is; Swagger UI renders it).
# ---------------------------------------------------------------------------
def _openapi_spec(version: str) -> dict:
    sid = {"name": "sid", "in": "path", "required": True, "schema": {"type": "string"},
           "description": "Session id, e.g. s3"}
    bearer = {"BearerAuth": []}
    ok = {"description": "ShellFrame result envelope",
          "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Result"}}}}
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "ShellFrame Local API",
            "version": version,
            "description": "Local HTTP control surface for ShellFrame. Loopback + "
                           "token + IP whitelist. Lets a local agent drive tabs, "
                           "send prompts, and respond to agent signals.",
        },
        "servers": [{"url": "/"}],
        "security": [bearer],
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer",
                               "description": "Token from config api_server.token. "
                                              "Also accepted as X-API-Token header."}
            },
            "schemas": {
                "Result": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "message": {"type": "string"},
                        "details": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["success"],
                },
            },
        },
        "paths": {
            "/health": {"get": {"summary": "Liveness probe", "security": [],
                                 "responses": {"200": {"description": "alive"}}}},
            "/sessions": {
                "get": {"summary": "List sessions (tabs)", "responses": {"200": ok}},
                "post": {
                    "summary": "Open a new session",
                    "requestBody": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "cmd": {"type": "string", "description": "Command to launch, e.g. 'claude' or 'bash'"},
                            "label": {"type": "string"},
                        }}}}},
                    "responses": {"200": ok},
                },
            },
            "/sessions/{sid}": {
                "delete": {"summary": "Close a session", "parameters": [sid],
                           "responses": {"200": ok}},
            },
            "/sessions/{sid}/peek": {
                "get": {
                    "summary": "Read a tab's recent output",
                    "parameters": [sid, {"name": "lines", "in": "query",
                                          "schema": {"type": "integer", "default": 200}}],
                    "responses": {"200": ok},
                },
            },
            "/sessions/{sid}/send": {
                "post": {
                    "summary": "Send (inject) a prompt into a tab",
                    "parameters": [sid],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "submit": {"type": "boolean", "default": True,
                                       "description": "Press Enter after typing"},
                        },
                        "required": ["text"]}}}},
                    "responses": {"200": ok},
                },
            },
            "/sessions/{sid}/rename": {
                "post": {
                    "summary": "Rename a tab",
                    "parameters": [sid],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "properties": {"name": {"type": "string"}},
                        "required": ["name"]}}}},
                    "responses": {"200": ok},
                },
            },
            "/sessions/{sid}/glasses-allow": {
                "post": {
                    "summary": "Let the Agent Relay glasses bridge inject into this tab",
                    "description": "Fail-closed allow list. Every tab runs with "
                                   "--dangerously-skip-permissions, so this grants "
                                   "voice-driven arbitrary execution on this machine. "
                                   "One sid per call on purpose; there is no enable-all.",
                    "parameters": [sid], "responses": {"200": ok},
                },
            },
            "/sessions/{sid}/glasses-deny": {
                "post": {"summary": "Take the glasses permission back for this tab",
                         "parameters": [sid], "responses": {"200": ok}},
            },
            "/glasses": {
                "get": {"summary": "Agent Relay bridge state + which tabs are open to the glasses",
                        "responses": {"200": ok}},
            },
            "/status": {"get": {"summary": "Roster + live tab states", "responses": {"200": ok}}},
            "/roster": {"get": {"summary": "Configured worker roles", "responses": {"200": ok}}},
            "/delegate": {
                "post": {
                    "summary": "Delegate a task to a roster role (opens/uses a worker)",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"role": {"type": "string"}, "task": {"type": "string"}},
                        "required": ["role", "task"]}}}},
                    "responses": {"200": ok},
                },
            },
            "/events": {
                "get": {
                    "summary": "Poll agent signal events (RED/YELLOW/GREEN) since a cursor",
                    "description": "Tabs raise [[SF:RED/YELLOW]] when they need a "
                                   "decision or are blocked. Poll here, then reply via "
                                   "POST /sessions/{sid}/send. Returns the new cursor.",
                    "parameters": [{"name": "since", "in": "query",
                                    "schema": {"type": "integer", "default": 0}}],
                    "responses": {"200": {"description": "events + cursor",
                                          "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean"},
                            "cursor": {"type": "integer"},
                            "events": {"type": "array", "items": {"type": "object"}},
                        }}}}}},
                },
            },
        },
    }


_SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>ShellFrame API</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"/>
</head><body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
window.onload = function() {
  window.ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    presets: [SwaggerUIBundle.presets.apis],
    layout: "BaseLayout",
  });
};
</script></body></html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
def _make_handler(execute_fn, token: str, allowed_nets, version: str):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"ShellFrame/{version}"

        def log_message(self, *a):
            pass  # stay quiet; ShellFrame has its own logging

        # -- helpers ---------------------------------------------------------
        def _client_ip(self):
            return self.client_address[0]

        def _ip_ok(self) -> bool:
            try:
                ip = ipaddress.ip_address(self._client_ip())
            except ValueError:
                return False
            return any(ip in net for net in allowed_nets)

        def _token_ok(self) -> bool:
            if not token:
                return False  # enabled but no token configured → deny (fail closed)
            hdr = self.headers.get("X-API-Token") or ""
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                hdr = hdr or auth[7:].strip()
            return _consteq(hdr, token)

        def _send(self, code: int, obj, ctype="application/json"):
            if isinstance(obj, (dict, list)):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            else:
                body = obj.encode("utf-8") if isinstance(obj, str) else obj
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _body_json(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        def _exec(self, cmd, args):
            try:
                return execute_fn(cmd, args or {})
            except Exception as e:  # never leak a stack trace to the wire
                return {"success": False, "message": f"{cmd} failed: {e}"}

        # -- routing ---------------------------------------------------------
        def _guard(self, public: bool) -> bool:
            if not self._ip_ok():
                self._send(403, {"success": False, "message": "IP not allowed"})
                return False
            if not public and not self._token_ok():
                self.send_response(401)
                self.send_header("WWW-Authenticate", "Bearer")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            path = u.path.rstrip("/") or "/"
            q = parse_qs(u.query)

            # public (IP-gated, no token) — docs + health
            if path in ("/health", "/"):
                if not self._guard(public=True):
                    return
                return self._send(200, {"success": True, "service": "shellframe",
                                        "version": version})
            if path == "/openapi.json":
                if not self._guard(public=True):
                    return
                return self._send(200, _openapi_spec(version))
            if path == "/docs":
                if not self._guard(public=True):
                    return
                return self._send(200, _SWAGGER_HTML, ctype="text/html")

            if not self._guard(public=False):
                return

            if path == "/sessions":
                return self._send(200, self._exec("list", {}))
            if path == "/status":
                return self._send(200, self._exec("status", {}))
            if path == "/roster":
                return self._send(200, self._exec("roster", {}))
            if path == "/glasses":
                return self._send(200, self._exec("glasses_status", {}))
            if path == "/events":
                try:
                    cursor = int((q.get("since") or ["0"])[0])
                except ValueError:
                    cursor = 0
                events, tail = EVENT_BUS.since(cursor)
                return self._send(200, {"success": True, "cursor": tail, "events": events})

            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "peek":
                try:
                    lines = int((q.get("lines") or ["200"])[0])
                except ValueError:
                    lines = 200
                return self._send(200, self._exec("peek", {"sid": parts[1], "lines": lines}))

            return self._send(404, {"success": False, "message": "no such route"})

        def do_POST(self):
            if not self._guard(public=False):
                return
            path = urlparse(self.path).path.rstrip("/") or "/"
            body = self._body_json()
            parts = path.strip("/").split("/")

            if path == "/sessions":
                return self._send(200, self._exec("new_session", {
                    "cmd": body.get("cmd", ""), "label": body.get("label", "")}))
            if path == "/delegate":
                return self._send(200, self._exec("delegate", {
                    "role": body.get("role", ""), "task": body.get("task", "")}))

            if len(parts) == 3 and parts[0] == "sessions":
                sid, action = parts[1], parts[2]
                if action == "send":
                    return self._send(200, self._exec("send", {
                        "sid": sid, "text": body.get("text", ""),
                        "submit": body.get("submit", True)}))
                if action == "rename":
                    return self._send(200, self._exec("rename", {
                        "sid": sid, "name": body.get("name", "")}))
                # Opening a tab to the glasses is at most as powerful as
                # /sessions/{sid}/send, which this same token already grants.
                if action in ("glasses-allow", "glasses-deny"):
                    return self._send(200, self._exec("glasses", {
                        "action": "allow" if action.endswith("allow") else "deny",
                        "sids": [sid], "source": "api"}))

            return self._send(404, {"success": False, "message": "no such route"})

        def do_DELETE(self):
            if not self._guard(public=False):
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "sessions":
                return self._send(200, self._exec("close_session", {"sid": parts[1]}))
            return self._send(404, {"success": False, "message": "no such route"})

    return Handler


def _consteq(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(str(a), str(b))


def _parse_nets(allowed_ips):
    nets = []
    for raw in (allowed_ips or ["127.0.0.1", "::1"]):
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return nets


def start(execute_fn, *, host="127.0.0.1", port=8765, token="",
          allowed_ips=None, version="0", log=None):
    """Start the API server in a daemon thread. Returns (server, thread).

    execute_fn(cmd, args) -> dict  is app._execute_sfctl.
    Raises nothing fatal: on bind failure it logs and returns (None, None).
    """
    nets = _parse_nets(allowed_ips)
    handler = _make_handler(execute_fn, token, nets, version)
    try:
        httpd = ThreadingHTTPServer((host, int(port)), handler)
    except OSError as e:
        if log:
            log(f"[api] bind {host}:{port} failed: {e}")
        return None, None
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True, name="sf-api")
    t.start()
    if log:
        log(f"[api] listening on http://{host}:{port}  docs=/docs  "
            f"allow={[str(n) for n in nets]}")
    return httpd, t
