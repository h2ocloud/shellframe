#!/usr/bin/env python3
"""
relay_server — tiny store-and-forward HTTP relay for Frame Link peers behind NAT.

Same idea as the Telegram bridge: the computer never needs an inbound port. It
long-polls the relay for work (outbound only), replays each envelope against
its own Frame Link listener over loopback, and posts the answer back. A phone
(or another ShellFrame) that cannot reach the computer directly submits
envelopes to the relay and waits for the answer.

The relay is deliberately dumb: it moves opaque, HMAC-signed Frame Link
envelopes and cannot forge either direction (requests and responses are signed
end-to-end by the pairing secret it never sees). It DOES see the plaintext
inside — Frame Link has no encryption — so run your own relay, put TLS in
front of it (Caddy / nginx / Cloudflare), and treat the relay host like you
treat the computer itself.

Routes (all under one shared bearer token, `--token` / RELAY_TOKEN):

  GET  /health                          liveness (no token)
  POST /r/<frame_id>/call               client → {method,path,headers,body_b64,wait}
                                        ← {status,headers,body_b64} | 404 offline | 504 timeout
  GET  /r/<frame_id>/pull?wait=25       computer long-poll ← {"requests":[{id,method,path,headers,body_b64}]}
  POST /r/<frame_id>/reply/<req_id>     computer → {status,headers,body_b64}

Run:  python3 relay_server.py --port 8790 --token <long-random-string>
      (or RELAY_TOKEN=... ; bind 127.0.0.1 and reverse-proxy TLS to it)

stdlib only, in-memory, no persistence: a restart just drops in-flight calls.
"""

import argparse
import json
import os
import secrets
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

MAX_BODY = 8 * 1024 * 1024        # per envelope (voice clips fit; file transfer doesn't — by design)
QUEUE_MAX = 64                    # pending requests per frame
REQ_TTL = 60.0                    # a request nobody pulled for this long is dropped
ONLINE_WINDOW = 45.0              # frame counts as online if it pulled within this window
MAX_WAIT = 60


class RelayState:
    def __init__(self):
        self.lock = threading.Lock()
        self.frames = {}          # fid -> {"queue": deque, "cond": Condition, "last_pull": ts}
        self.pending = {}         # rid -> {"event": Event, "resp": dict|None, "fid": fid, "ts": ts}

    def frame(self, fid):
        with self.lock:
            f = self.frames.get(fid)
            if f is None:
                f = {"queue": deque(), "cond": threading.Condition(self.lock), "last_pull": 0.0}
                self.frames[fid] = f
            return f

    def online(self, fid) -> bool:
        with self.lock:
            f = self.frames.get(fid)
            return bool(f and time.time() - f["last_pull"] < ONLINE_WINDOW)

    def submit(self, fid, envelope, wait):
        f = self.frame(fid)
        rid = secrets.token_hex(12)
        ev = threading.Event()
        with self.lock:
            if len(f["queue"]) >= QUEUE_MAX:
                return None, (429, {"success": False, "message": "relay queue full"})
            self.pending[rid] = {"event": ev, "resp": None, "fid": fid, "ts": time.time()}
            f["queue"].append({"id": rid, "ts": time.time(), **envelope})
            f["cond"].notify_all()
        if not ev.wait(timeout=wait):
            with self.lock:
                self.pending.pop(rid, None)
                # drop it from the queue if the computer never took it
                f["queue"] = deque(x for x in f["queue"] if x["id"] != rid)
            return None, (504, {"success": False, "message": "peer did not answer in time"})
        with self.lock:
            item = self.pending.pop(rid, None)
        return (item or {}).get("resp"), None

    def pull(self, fid, wait):
        f = self.frame(fid)
        deadline = time.time() + wait
        with self.lock:
            f["last_pull"] = time.time()
            while True:
                now = time.time()
                # expire stale requests
                while f["queue"] and now - f["queue"][0]["ts"] > REQ_TTL:
                    old = f["queue"].popleft()
                    self.pending.pop(old["id"], None)
                if f["queue"]:
                    out = list(f["queue"])
                    f["queue"].clear()
                    f["last_pull"] = time.time()
                    return out
                remaining = deadline - now
                if remaining <= 0:
                    f["last_pull"] = time.time()
                    return []
                f["cond"].wait(timeout=min(remaining, 5.0))

    def reply(self, fid, rid, resp) -> bool:
        with self.lock:
            item = self.pending.get(rid)
            if not item or item["fid"] != fid:
                return False
            item["resp"] = resp
            item["event"].set()
            return True

    def stats(self):
        with self.lock:
            now = time.time()
            return {
                "frames_online": sum(1 for f in self.frames.values() if now - f["last_pull"] < ONLINE_WINDOW),
                "frames_seen": len(self.frames),
                "pending": len(self.pending),
            }


def make_handler(state: RelayState, token: str):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ShellFrameRelay/1"

        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return {}
            if n > MAX_BODY:
                return None
            try:
                return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        def _auth(self) -> bool:
            if not token:
                return False
            auth = self.headers.get("Authorization") or ""
            got = auth[7:].strip() if auth.lower().startswith("bearer ") else (self.headers.get("X-Relay-Token") or "")
            return secrets.compare_digest(got, token)

        def _parts(self):
            u = urlparse(self.path)
            return u.path.rstrip("/").strip("/").split("/"), parse_qs(u.query)

        def do_GET(self):
            parts, q = self._parts()
            if parts == ["health"] or parts == [""]:
                return self._send(200, {"success": True, "service": "shellframe-relay", **state.stats()})
            if len(parts) == 3 and parts[0] == "r" and parts[2] == "pull":
                if not self._auth():
                    return self._send(401, {"success": False, "message": "bad relay token"})
                try:
                    wait = min(MAX_WAIT, max(0, int((q.get("wait") or ["25"])[0])))
                except ValueError:
                    wait = 25
                reqs = state.pull(parts[1], wait)
                return self._send(200, {"success": True, "requests": reqs})
            return self._send(404, {"success": False, "message": "no such route"})

        def do_POST(self):
            parts, q = self._parts()
            if len(parts) >= 3 and parts[0] == "r":
                if not self._auth():
                    return self._send(401, {"success": False, "message": "bad relay token"})
                body = self._body()
                if body is None:
                    return self._send(413, {"success": False, "message": f"envelope too large (cap {MAX_BODY})"})
                fid = parts[1]
                if len(parts) == 3 and parts[2] == "call":
                    if not state.online(fid):
                        return self._send(404, {"success": False, "message": "peer offline (never pulled recently)"})
                    method = str(body.get("method") or "GET").upper()
                    path = str(body.get("path") or "/")
                    if not path.startswith("/link/"):
                        return self._send(400, {"success": False, "message": "only /link/* is relayed"})
                    try:
                        wait = min(MAX_WAIT, max(3, int(body.get("wait") or 20)))
                    except (TypeError, ValueError):
                        wait = 20
                    env = {"method": method, "path": path,
                           "headers": {str(k): str(v) for k, v in (body.get("headers") or {}).items()},
                           "body_b64": str(body.get("body_b64") or "")}
                    resp, err = state.submit(fid, env, wait)
                    if err:
                        return self._send(*err)
                    return self._send(200, resp or {"status": 502, "headers": {}, "body_b64": ""})
                if len(parts) == 4 and parts[2] == "reply":
                    ok = state.reply(fid, parts[3], {
                        "status": int(body.get("status") or 502),
                        "headers": {str(k): str(v) for k, v in (body.get("headers") or {}).items()},
                        "body_b64": str(body.get("body_b64") or ""),
                    })
                    return self._send(200 if ok else 404, {"success": ok})
            return self._send(404, {"success": False, "message": "no such route"})

    return Handler


def create_server(host="127.0.0.1", port=8790, token=""):
    state = RelayState()
    httpd = ThreadingHTTPServer((host, int(port)), make_handler(state, token))
    httpd.daemon_threads = True
    return httpd, state


def main(argv=None):
    ap = argparse.ArgumentParser(description="ShellFrame Frame Link relay")
    ap.add_argument("--host", default=os.environ.get("RELAY_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("RELAY_PORT", "8790")))
    ap.add_argument("--token", default=os.environ.get("RELAY_TOKEN", ""))
    a = ap.parse_args(argv)
    if not a.token:
        print("refusing to start without --token / RELAY_TOKEN (generate one: python3 -c "
              "'import secrets;print(secrets.token_urlsafe(32))')", file=sys.stderr)
        return 2
    httpd, _ = create_server(a.host, a.port, a.token)
    print(f"[relay] listening on {a.host}:{a.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
