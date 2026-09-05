"""
link_relay — the computer's side of the Frame Link relay (see relay_server.py).

Outbound-only, like the Telegram bridge's getUpdates loop: long-poll the relay
for envelopes addressed to this frame_id, replay each one against our own
Frame Link listener over loopback (so signing, control gating and every route
behave exactly as for a direct request), and post the answer back.

Also provides `relay_call()` — the client half — so a ShellFrame can reach a
peer that is only reachable through a relay (the same envelope a phone sends).
"""

import base64
import http.client
import json
import threading
import time
import urllib.error
import urllib.request

PULL_WAIT = 25          # seconds the relay may hold our long-poll
LOCAL_TIMEOUT = 130     # a relayed /link/voice can wait on STT


def _b64(data: bytes) -> str:
    return base64.b64encode(data or b"").decode("ascii")


def _unb64(s: str) -> bytes:
    try:
        return base64.b64decode(s or "")
    except Exception:
        return b""


def relay_call(relay_url: str, token: str, frame_id: str, method: str, path_qs: str,
               headers: dict, body: bytes = b"", timeout: float = 20.0):
    """Submit one envelope for `frame_id` and wait for its answer.
    Returns (status, headers_lowercased, body_bytes). Raises on relay-level errors."""
    url = f"{relay_url.rstrip('/')}/r/{frame_id}/call"
    env = {"method": method.upper(), "path": path_qs,
           "headers": dict(headers or {}), "body_b64": _b64(body),
           "wait": int(max(5, min(60, timeout + 4)))}
    req = urllib.request.Request(url, data=json.dumps(env).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 12) as resp:
            obj = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8") or "{}").get("message", "")
        except Exception:
            msg = ""
        hint = {401: "relay token 不對", 404: "對方沒有連上 relay", 504: "對方沒有在時限內回應"}.get(e.code, "")
        raise RuntimeError(f"relay HTTP {e.code}: {hint or msg}") from None
    hdrs = {str(k).lower(): str(v) for k, v in (obj.get("headers") or {}).items()}
    return int(obj.get("status") or 502), hdrs, _unb64(obj.get("body_b64", ""))


class RelayClient:
    """Long-poll worker. `local_addr` = (host, port) of our own listener."""

    def __init__(self, get_settings, local_addr, log=None, frame_id=""):
        """get_settings() -> (relay_url, token) — read live so config edits apply
        without a restart of the poll thread."""
        self._get_settings = get_settings
        self._local_addr = local_addr
        self._frame_id = frame_id
        self._log = log or (lambda m: None)
        self._stop = threading.Event()
        self._thread = None
        self.connected = False
        self.last_err = ""
        self.last_ok = 0.0
        self.served = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sf-link-relay")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def status(self) -> dict:
        url, _ = self._get_settings()
        return {"configured": bool(url), "connected": self.connected,
                "last_ok": self.last_ok, "last_err": self.last_err, "served": self.served}

    # ── loop ────────────────────────────────────────────────────────────
    def _loop(self):
        backoff = 1.0
        while not self._stop.is_set():
            url, token = self._get_settings()
            if not url or not token:
                self.connected = False
                self._stop.wait(5)
                continue
            try:
                reqs = self._pull(url, token)
                if self._stop.is_set():
                    break            # stopped mid-poll: don't serve, let the relay time out
                self.connected = True
                self.last_ok = time.time()
                self.last_err = ""
                backoff = 1.0
                for env in reqs:
                    threading.Thread(target=self._serve, args=(url, token, env),
                                     daemon=True, name="sf-link-relay-req").start()
            except Exception as e:
                self.connected = False
                self.last_err = str(e)[:200]
                self._log(f"[relay] pull failed: {e} (retry in {backoff:.0f}s)")
                self._stop.wait(backoff)
                backoff = min(30.0, backoff * 2)

    def _pull(self, url, token):
        req = urllib.request.Request(
            f"{url.rstrip('/')}/r/{self._frame_id}/pull?wait={PULL_WAIT}",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=PULL_WAIT + 15) as resp:
            obj = json.loads(resp.read().decode("utf-8") or "{}")
        if not obj.get("success", True):
            raise RuntimeError(obj.get("message", "pull rejected"))
        return obj.get("requests") or []

    def _serve(self, url, token, env):
        rid = str(env.get("id") or "")
        status, headers, body = self._replay_local(env)
        payload = {"status": status, "headers": headers, "body_b64": _b64(body)}
        req = urllib.request.Request(
            f"{url.rstrip('/')}/r/{self._frame_id}/reply/{rid}",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=15):
                pass
            self.served += 1
        except Exception as e:
            self._log(f"[relay] reply {rid} failed: {e}")

    def _replay_local(self, env):
        """Replay the envelope against our own listener, byte-identical path+headers
        so the HMAC verifies. Only /link/* is allowed through."""
        method = str(env.get("method") or "GET").upper()
        path = str(env.get("path") or "/")
        if not path.startswith("/link/"):
            return 400, {}, json.dumps({"success": False, "message": "only /link/* is relayed"}).encode()
        body = _unb64(env.get("body_b64", ""))
        headers = {str(k): str(v) for k, v in (env.get("headers") or {}).items()}
        headers.pop("Host", None)
        headers.pop("Content-Length", None)
        headers["X-SF-Via"] = "relay"
        host, port = self._local_addr()
        try:
            conn = http.client.HTTPConnection(host, int(port), timeout=LOCAL_TIMEOUT)
            conn.request(method, path, body=body if body else None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            out_headers = {}
            for k, v in resp.getheaders():
                if k.lower() in ("x-sf-sign", "content-type", "x-sf-body-sha256"):
                    out_headers[k] = v
            conn.close()
            return resp.status, out_headers, data
        except Exception as e:
            return 502, {}, json.dumps({"success": False, "message": f"local replay failed: {e}"}).encode()
