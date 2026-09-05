"""
frame_link — cross-machine ShellFrame pairing + peer link ("Frame Link").

Two ShellFrame instances pair once with a short-lived one-time code, then talk
to each other over HTTP — LAN or public internet — to:

  - list / peek / inject into each other's tabs
  - exchange chat messages
  - transfer files (received files land in ~/Downloads/ShellFrame/<peer>/)

Design notes (why it looks like this):

  * stdlib only, same ethos as api_server.py. No TLS dependency, so requests
    are NOT encrypted — they are authenticated + integrity-protected with
    HMAC-SHA256 (timestamp + nonce against replay, responses signed too).
    Pairing on a trusted network first is recommended; after pairing the
    long-term secret never travels on the wire.

  * Pairing code never travels in cleartext. The joiner proves knowledge of
    the code FIRST (HMAC proof over both nonces); only then does the host
    reveal its own proof. Both sides then independently derive a 256-bit
    long-term secret from the code + the handshake transcript. The code is
    single-use, expires after PAIR_TTL seconds and dies after
    PAIR_MAX_ATTEMPTS bad proofs.

  * Public internet / NAT: full duplex needs only ONE reachable side. Every
    peer keeps a per-peer outbox; the side that cannot be reached polls the
    reachable side (GET /link/events) with signed requests and applies what
    it finds (messages, tab injections, file offers → it downloads the staged
    file by id). Direct push is used whenever the target is reachable.

  * This module imports nothing from main.py — it receives callables
    (get_config/update_config/execute_fn/notify) so there is no circular
    import, mirroring api_server.py.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

PAIR_TTL = 120               # seconds a pairing code stays valid
PAIR_MAX_ATTEMPTS = 5        # bad proofs before the code dies
SIG_WINDOW = 90              # accepted clock skew for signed requests (s)
NONCE_TTL = 300              # replay-cache retention (s)
MAX_FILE_BYTES = 512 * 1024 * 1024
POLL_INTERVAL = 15           # peer ping + outbox pull cadence (s)
INBOX_KEEP = 200             # rolling inbox log entries
OUTBOX_KEEP = 200            # queued events per peer

# No I/L/O/0/1 — the code gets read out loud / retyped between machines.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 10               # 10 chars of 32-alphabet ≈ 50 bits

STATE_DIR = Path.home() / ".local" / "state" / "shellframe"
FILES_DIR = STATE_DIR / "frame_link_files"      # staged outbox files
INBOX_FILE = STATE_DIR / "frame_link_inbox.json"
OUTBOX_FILE = STATE_DIR / "frame_link_outbox.json"
DOWNLOADS_DIR = Path.home() / "Downloads" / "ShellFrame"
PAIR_URL_SCHEME = "shellframe"          # shellframe://pair?d=<base64url json> (QR + deep link)


def build_pair_url(payload: dict) -> str:
    """Pairing QR / deep-link content: the same string is drawn as a QR on the
    desktop and read by the phone app's camera scanner or a tapped link."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    b = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{PAIR_URL_SCHEME}://pair?d={b}"


def parse_pair_url(url: str):
    """Inverse of build_pair_url. Returns the payload dict or None."""
    m = re.match(r"^\s*" + PAIR_URL_SCHEME + r"://pair\?(?:[^#]*&)?d=([A-Za-z0-9_-]+)", url or "")
    if not m:
        return None
    b = m.group(1)
    b += "=" * (-len(b) % 4)
    try:
        obj = json.loads(base64.urlsafe_b64decode(b).decode("utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) and obj.get("code") else None


def _now() -> float:
    return time.time()


def _consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a), str(b))


def _hmac_hex(key: bytes, msg: bytes) -> str:
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def normalize_code(code: str) -> str:
    """User-typed pairing code → canonical form (drop separators, upcase)."""
    return re.sub(r"[^A-Z2-9]", "", (code or "").upper())


def generate_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    return f"{raw[:5]}-{raw[5:]}"


def derive_secret(code_norm: str, joiner_nonce: str, host_nonce: str,
                  joiner_id: str, host_id: str) -> str:
    """Both sides compute this independently; it never travels on the wire."""
    transcript = f"sf-link-secret|{joiner_nonce}|{host_nonce}|{joiner_id}|{host_id}"
    return _hmac_hex(code_norm.encode(), transcript.encode())


def _proof(code_norm: str, role: str, joiner_nonce: str, host_nonce: str,
           extra: str = "") -> str:
    """extra: host 的 proof 綁配對模式（duplex/host_controls/joiner_controls），
    中間人竄改 mode 會讓 joiner 驗不過。"""
    return _hmac_hex(code_norm.encode(),
                     f"sf-pair-{role}|{joiner_nonce}|{host_nonce}|{extra}".encode())


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "file").strip() or "file"
    base = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "_", base)
    return base[:200]


def _safe_dirname(name: str) -> str:
    out = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "_", (name or "peer").strip()) or "peer"
    return out[:80]


def local_addresses() -> list:
    """Best-effort list of this machine's non-loopback IPv4 addresses."""
    addrs = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))       # no packet is actually sent (UDP)
            addrs.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in addrs:
                addrs.append(ip)
    except OSError:
        pass
    return addrs


class _JsonStore:
    """Tiny locked JSON file persistence (inbox log / outbox queues)."""

    def __init__(self, path: Path, default):
        self._path = path
        self._lock = threading.Lock()
        self._default = default

    def load(self):
        with self._lock:
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return json.loads(json.dumps(self._default))

    def save(self, data):
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self._path)
            except Exception:
                pass


class FrameLink:
    def __init__(self, *, get_config, update_config, execute_fn,
                 notify=None, log=None, version="0",
                 state_dir=None, downloads_dir=None):
        """
        get_config()            -> full ShellFrame config dict
        update_config(mutator)  -> atomic read-modify-write of the config
        execute_fn(cmd, args)   -> main.py _execute_sfctl (list / peek / send)
        notify(event: dict)     -> push an event to the UI (may be None)
        state_dir/downloads_dir -> overridable so tests can run two instances
                                   in one process without sharing files
        """
        self._get_config = get_config
        self._update_config = update_config
        self._execute = execute_fn
        self._notify_cb = notify or (lambda ev: None)
        self._log = log or (lambda m: None)
        self._version = version

        self._httpd = None
        self._poll_stop = threading.Event()
        self._poll_thread = None

        self._pairing = None            # active host-side pairing window
        self._pairing_lock = threading.Lock()
        self._nonces = {}               # nonce -> ts (replay cache)
        self._nonce_lock = threading.Lock()
        self._peer_status = {}          # peer_id -> {reachable,last_ok,last_err}
        self._cursor_lock = threading.Lock()

        # Raw-output ring buffers for the seamless remote-tab view. Only tabs a
        # peer is actively streaming (a /link/stream request in the last 30 s)
        # get buffered, so idle operation costs nothing.
        self._streams = {}              # sid -> {seq,min_seq,chunks,bytes,watch_ts}
        self._streams_lock = threading.Lock()

        self._state_dir = Path(state_dir) if state_dir else STATE_DIR
        self._files_dir = self._state_dir / "frame_link_files"
        self._downloads_dir = Path(downloads_dir) if downloads_dir else DOWNLOADS_DIR
        self._inbox = _JsonStore(self._state_dir / "frame_link_inbox.json", [])
        # peer_id -> {"next_id":1,"events":[]}
        self._outbox = _JsonStore(self._state_dir / "frame_link_outbox.json", {})

    # ── config helpers ────────────────────────────────────────────────────
    def _cfg(self) -> dict:
        try:
            return (self._get_config() or {}).get("frame_link") or {}
        except Exception:
            return {}

    def _mutate_cfg(self, fn):
        def mutator(full):
            block = full.setdefault("frame_link", {})
            fn(block)
        self._update_config(mutator)

    def frame_id(self) -> str:
        return self._cfg().get("frame_id", "")

    def frame_name(self) -> str:
        return self._cfg().get("frame_name") or socket.gethostname().split(".")[0]

    def peers(self) -> dict:
        return dict(self._cfg().get("peers") or {})

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self) -> bool:
        cfg = self._cfg()
        if not cfg.get("enabled") or self._httpd:
            return bool(self._httpd)
        host = cfg.get("listen_host", "0.0.0.0")
        port = int(cfg.get("listen_port", 8767))
        handler = self._make_handler()
        try:
            httpd = ThreadingHTTPServer((host, port), handler)
        except OSError as e:
            self._log(f"[link] bind {host}:{port} failed: {e}")
            return False
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True,
                         name="sf-link").start()
        self._httpd = httpd
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True,
                                             name="sf-link-poll")
        self._poll_thread.start()
        self._log(f"[link] listening on {host}:{port}")
        return True

    def stop(self):
        self._poll_stop.set()
        httpd, self._httpd = self._httpd, None
        if httpd:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass

    def running(self) -> bool:
        return bool(self._httpd)

    def status(self) -> dict:
        cfg = self._cfg()
        peers = []
        for pid, p in (cfg.get("peers") or {}).items():
            st = self._peer_status.get(pid, {})
            mode = p.get("mode", "duplex")
            peers.append({
                "id": pid,
                "name": p.get("name", pid[:8]),
                "host": p.get("host", ""),
                "port": p.get("port", 0),
                "reachable": bool(st.get("reachable")),
                "last_ok": st.get("last_ok", 0),
                "last_err": st.get("last_err", ""),
                "mode": mode,                                  # 本機視角
                "can_control": mode in ("duplex", "master"),   # 我能操作對方
            })
        with self._pairing_lock:
            pairing = None
            if self._pairing and self._pairing["expires"] > _now():
                pairing = {"active": True,
                           "expires_in": int(self._pairing["expires"] - _now())}
        return {
            "enabled": bool(cfg.get("enabled")),
            "running": self.running(),
            "frame_id": cfg.get("frame_id", ""),
            "frame_name": self.frame_name(),
            "listen_host": cfg.get("listen_host", "0.0.0.0"),
            "listen_port": int(cfg.get("listen_port", 8767)),
            "addresses": local_addresses(),
            "peers": peers,
            "pairing": pairing,
        }

    # ── pairing: host side ────────────────────────────────────────────────
    def pairing_begin(self, mode: str = "duplex") -> dict:
        """Open a PAIR_TTL pairing window and hand the UI a one-time code.

        mode（產碼端視角，會綁進 host proof）：
          duplex          雙向，彼此都能操作對方的 session
          host_controls   單向：這台當主，只有這台能操作對方
          joiner_controls 單向：這台當從，只有對方能操作這台
        訊息與檔案互傳不受 mode 限制。"""
        if mode not in ("duplex", "host_controls", "joiner_controls"):
            mode = "duplex"
        if not self.running():
            started = False
            if self._cfg().get("enabled"):
                started = self.start()
            if not started:
                return {"success": False,
                        "message": "Frame Link listener not running（先啟用跨機連線）"}
        code = generate_code()
        with self._pairing_lock:
            self._pairing = {
                "code": normalize_code(code),
                "expires": _now() + PAIR_TTL,
                "attempts": 0,
                "mode": mode,
                "handshakes": {},   # joiner_nonce -> host_nonce
            }
        cfg = self._cfg()
        port = int(cfg.get("listen_port", 8767))
        hosts = local_addresses()
        payload = {"v": 1, "fid": cfg.get("frame_id", ""), "name": self.frame_name(),
                   "hosts": hosts, "port": port, "code": code, "mode": mode}
        return {
            "success": True,
            "code": code,
            "port": port,
            "addresses": hosts,
            "pair_url": build_pair_url(payload),
            "expires_in": PAIR_TTL,
        }

    def pairing_cancel(self):
        with self._pairing_lock:
            self._pairing = None
        return {"success": True}

    def _pairing_active(self):
        with self._pairing_lock:
            p = self._pairing
            if not p:
                return None
            if p["expires"] <= _now() or p["attempts"] >= PAIR_MAX_ATTEMPTS:
                self._pairing = None
                return None
            return p

    def _handle_pair_start(self, body: dict) -> tuple:
        p = self._pairing_active()
        if not p:
            return 404, {"success": False, "message": "no active pairing window"}
        joiner_nonce = str(body.get("joiner_nonce") or "")
        if not re.fullmatch(r"[0-9a-f]{32,64}", joiner_nonce):
            return 400, {"success": False, "message": "bad joiner_nonce"}
        host_nonce = secrets.token_hex(16)
        with self._pairing_lock:
            if self._pairing is not p:
                return 404, {"success": False, "message": "pairing closed"}
            hs = p["handshakes"]
            if len(hs) >= 16:            # don't let a scanner grow this map
                hs.clear()
            hs[joiner_nonce] = host_nonce
        return 200, {"success": True, "host_nonce": host_nonce}

    def _handle_pair_finish(self, body: dict, client_ip: str) -> tuple:
        p = self._pairing_active()
        if not p:
            return 404, {"success": False, "message": "no active pairing window"}
        joiner_nonce = str(body.get("joiner_nonce") or "")
        proof_j = str(body.get("proof") or "")
        joiner_id = str(body.get("joiner_id") or "")
        joiner_name = str(body.get("joiner_name") or "")[:60] or "peer"
        joiner_port = int(body.get("joiner_port") or 0)
        with self._pairing_lock:
            if self._pairing is not p:
                return 404, {"success": False, "message": "pairing closed"}
            host_nonce = p["handshakes"].get(joiner_nonce, "")
        if not host_nonce or not re.fullmatch(r"[0-9a-f-]{8,64}", joiner_id or ""):
            return 400, {"success": False, "message": "unknown handshake"}
        expected = _proof(p["code"], "join", joiner_nonce, host_nonce)
        if not _consteq(proof_j, expected):
            with self._pairing_lock:
                if self._pairing is p:
                    p["attempts"] += 1
                    if p["attempts"] >= PAIR_MAX_ATTEMPTS:
                        self._pairing = None
            self._log(f"[link] pairing proof mismatch from {client_ip}")
            return 403, {"success": False, "message": "bad pairing code"}

        cfg = self._cfg()
        host_id = cfg.get("frame_id", "")
        secret = derive_secret(p["code"], joiner_nonce, host_nonce,
                               joiner_id, host_id)
        mode = p.get("mode", "duplex")
        # 本機視角的角色：master=我能操作對方、對方不能操作我；slave=反之。
        my_mode = {"duplex": "duplex", "host_controls": "master",
                   "joiner_controls": "slave"}[mode]
        # Joiner is often behind NAT: keep whatever endpoint it advertised
        # (may be unreachable — the poller marks it and the outbox covers it).
        self._store_peer(joiner_id, {
            "name": joiner_name,
            "host": client_ip if joiner_port else "",
            "port": joiner_port,
            "secret": secret,
            "mode": my_mode,
            "added": _now(),
        })
        with self._pairing_lock:
            self._pairing = None        # single use
        proof_h = _proof(p["code"], "host", joiner_nonce, host_nonce, mode)
        self._notify({"kind": "paired", "peer_id": joiner_id,
                      "peer_name": joiner_name})
        return 200, {"success": True, "proof": proof_h, "mode": mode,
                     "host_id": host_id, "host_name": self.frame_name(),
                     "host_port": int(cfg.get("listen_port", 8767))}

    # ── pairing: joiner side ──────────────────────────────────────────────
    def join(self, host: str, port: int, code: str) -> dict:
        host = (host or "").strip()
        code_norm = normalize_code(code)
        if not host or not code_norm:
            return {"success": False, "message": "host 與配對碼必填"}
        if len(code_norm) < 8:
            return {"success": False, "message": "配對碼格式不對"}
        port = int(port or 8767)
        cfg = self._cfg()
        my_id = cfg.get("frame_id", "")
        joiner_nonce = secrets.token_hex(16)
        base = f"http://{host}:{port}"
        try:
            r1 = self._plain_post(f"{base}/link/pair/start",
                                  {"joiner_nonce": joiner_nonce})
        except Exception as e:
            return {"success": False, "message": f"連不到 {host}:{port} — {e}"}
        if not r1.get("success"):
            return {"success": False,
                    "message": r1.get("message", "對方沒有開配對窗口")}
        host_nonce = str(r1.get("host_nonce") or "")
        if not re.fullmatch(r"[0-9a-f]{32,64}", host_nonce):
            return {"success": False, "message": "handshake 回應異常"}
        proof_j = _proof(code_norm, "join", joiner_nonce, host_nonce)
        try:
            r2 = self._plain_post(f"{base}/link/pair/finish", {
                "joiner_nonce": joiner_nonce,
                "proof": proof_j,
                "joiner_id": my_id,
                "joiner_name": self.frame_name(),
                "joiner_port": int(cfg.get("listen_port", 8767))
                               if cfg.get("enabled") else 0,
            })
        except Exception as e:
            return {"success": False, "message": f"配對失敗 — {e}"}
        if not r2.get("success"):
            return {"success": False, "message": r2.get("message", "配對被拒絕")}
        # Mutual auth: the host must prove it knew the code too, otherwise a
        # fake host could collect our proof and pair us to itself. The mode is
        # bound into the host proof so a middleman can't flip 單向/雙向.
        host_id = str(r2.get("host_id") or "")
        wire_mode = str(r2.get("mode") or "duplex")
        if wire_mode not in ("duplex", "host_controls", "joiner_controls"):
            return {"success": False, "message": "未知的配對模式（版本不相容？）"}
        expected_h = _proof(code_norm, "host", joiner_nonce, host_nonce, wire_mode)
        if not _consteq(str(r2.get("proof") or ""), expected_h) or not host_id:
            return {"success": False, "message": "對方無法證明持有配對碼（可能是假冒端點或版本不相容）"}
        secret = derive_secret(code_norm, joiner_nonce, host_nonce,
                               my_id, host_id)
        # joiner 視角：host_controls → 對方是主、我是從（slave）
        my_mode = {"duplex": "duplex", "host_controls": "slave",
                   "joiner_controls": "master"}[wire_mode]
        peer = {
            "name": str(r2.get("host_name") or host)[:60],
            "host": host,
            "port": int(r2.get("host_port") or port),
            "secret": secret,
            "mode": my_mode,
            "added": _now(),
        }
        self._store_peer(host_id, peer)
        self._peer_status[host_id] = {"reachable": True, "last_ok": _now(),
                                      "last_err": ""}
        self._notify({"kind": "paired", "peer_id": host_id,
                      "peer_name": peer["name"]})
        return {"success": True, "peer_id": host_id, "peer_name": peer["name"]}

    def _store_peer(self, peer_id: str, entry: dict):
        def fn(block):
            peers = block.setdefault("peers", {})
            peers[peer_id] = entry
        self._mutate_cfg(fn)

    def unpair(self, peer_id: str) -> dict:
        def fn(block):
            (block.get("peers") or {}).pop(peer_id, None)
        self._mutate_cfg(fn)
        self._peer_status.pop(peer_id, None)
        out = self._outbox.load()
        if out.pop(peer_id, None) is not None:
            self._outbox.save(out)
        # 順手清掉這個 peer 在 cursor 檔的殘留（否則依歷來配對數單調成長）
        try:
            cstore = _JsonStore(self._state_dir / "frame_link_cursors.json", {})
            cur = cstore.load()
            if cur.pop(peer_id, None) is not None:
                cstore.save(cur)
        except Exception:
            pass
        return {"success": True}

    def update_peer(self, peer_id: str, host: str, port: int) -> dict:
        peers = self.peers()
        if peer_id not in peers:
            return {"success": False, "message": "no such peer"}
        def fn(block):
            p = (block.get("peers") or {}).get(peer_id)
            if p is not None:
                p["host"] = (host or "").strip()
                p["port"] = int(port or 8767)
        self._mutate_cfg(fn)
        return {"success": True}

    # ── signing ───────────────────────────────────────────────────────────
    @staticmethod
    def _string_to_sign(method: str, path_qs: str, ts: str, nonce: str,
                        body_hash: str) -> bytes:
        return "\n".join([method.upper(), path_qs, ts, nonce, body_hash]).encode()

    def _verify_request(self, handler, body: bytes):
        """Returns (peer_id, peer_dict, request_nonce) or (None, error_msg, None)."""
        peer_id = handler.headers.get("X-SF-Peer") or ""
        ts = handler.headers.get("X-SF-Ts") or ""
        nonce = handler.headers.get("X-SF-Nonce") or ""
        sig = handler.headers.get("X-SF-Sign") or ""
        peer = self.peers().get(peer_id)
        if not peer:
            return None, "unknown peer", None
        try:
            skew = abs(_now() - float(ts))
        except ValueError:
            return None, "bad timestamp", None
        if skew > SIG_WINDOW:
            return None, "timestamp outside window (clock skew?)", None
        if not re.fullmatch(r"[0-9a-f]{16,64}", nonce or ""):
            return None, "bad nonce", None
        with self._nonce_lock:
            cutoff = _now() - NONCE_TTL
            if len(self._nonces) > 4096:
                self._nonces = {n: t for n, t in self._nonces.items() if t > cutoff}
            if nonce in self._nonces:
                return None, "replayed nonce", None
            self._nonces[nonce] = _now()
        expected = _hmac_hex(
            bytes.fromhex(peer["secret"]),
            self._string_to_sign(handler.command, handler.path, ts, nonce,
                                 _sha256_hex(body)))
        if not _consteq(sig, expected):
            return None, "bad signature", None
        return peer_id, peer, nonce

    def _sign_response(self, peer: dict, nonce: str, body: bytes) -> str:
        return _hmac_hex(bytes.fromhex(peer["secret"]),
                         f"resp\n{nonce}\n{_sha256_hex(body)}".encode())

    def _peer_may_control(self, peer_id: str) -> bool:
        """單向（主從）配對的權限閘：對方能不能看/操作這台的 session。
        mode 是本機視角——master＝只有我能操作對方 → 對方對我 deny。
        訊息、檔案、ping、events 不在此限。"""
        mode = (self.peers().get(peer_id) or {}).get("mode", "duplex")
        return mode in ("duplex", "slave")

    # ── raw output streaming（無縫遠端分頁）────────────────────────────────
    STREAM_WATCH_TTL = 30       # 沒人拉 30s 就停止緩衝該分頁
    STREAM_MAX_BYTES = 262144   # 每分頁 ring buffer 上限

    def feed_output(self, sid: str, data: str):
        """main.py 的 output pusher 每個 chunk 都會呼叫——必須極快。
        只有最近有 /link/stream 請求的分頁才緩衝，平時零成本。"""
        st = self._streams.get(sid)
        if st is None:
            return
        with self._streams_lock:
            st = self._streams.get(sid)
            if st is None:
                return
            if _now() - st["watch_ts"] > self.STREAM_WATCH_TTL:
                del self._streams[sid]
                return
            st["chunks"].append((st["seq"], data))
            st["seq"] += 1
            st["bytes"] += len(data)
            while st["bytes"] > self.STREAM_MAX_BYTES and st["chunks"]:
                old_seq, old = st["chunks"].pop(0)
                st["bytes"] -= len(old)
                st["min_seq"] = old_seq + 1

    def _stream_read(self, sid: str, since: int) -> dict:
        # Attach (since<0): hand back an accurate snapshot of the current screen
        # (tmux capture with ANSI) so alt-screen TUIs paint correctly, plus the
        # seq to stream from. Capture is done OUTSIDE the lock (it forks tmux).
        if since < 0:
            with self._streams_lock:
                seq = self._stream_open_locked(sid)
            snap = ""
            try:
                res = self._execute("raw_screen", {"sid": sid}) or {}
                snap = (res.get("details") or {}).get("screen", "") if res.get("success") else ""
            except Exception:
                snap = ""
            return {"success": True, "seq": seq, "data": "", "snapshot": snap,
                    "reset": True}
        with self._streams_lock:
            st = self._streams.get(sid)
            if st is None:
                seq = self._stream_open_locked(sid)
                return {"success": True, "seq": seq, "data": "", "reset": True}
            st["watch_ts"] = _now()
            if since < st["min_seq"]:
                # buffer 已捲走 client 要的區段 → 請 client 重新 attach 畫面
                return {"success": True, "seq": st["seq"], "data": "",
                        "reset": True}
            data = "".join(d for s, d in st["chunks"] if s >= since)
            return {"success": True, "seq": st["seq"], "data": data}

    def _stream_open_locked(self, sid: str) -> int:
        st = self._streams.get(sid)
        if st is None:
            st = {"seq": 0, "min_seq": 0, "chunks": [], "bytes": 0,
                  "watch_ts": _now()}
            self._streams[sid] = st
        else:
            st["watch_ts"] = _now()
        return st["seq"]

    # ── HTTP server ───────────────────────────────────────────────────────
    def _make_handler(self):
        link = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = f"ShellFrameLink/{link._version}"

            def log_message(self, *a):
                pass

            def _send(self, code: int, obj, sign_for=None, nonce=""):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8") \
                    if isinstance(obj, (dict, list)) else obj
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8"
                                 if isinstance(obj, (dict, list))
                                 else "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                if sign_for:
                    self.send_header("X-SF-Sign",
                                     link._sign_response(sign_for, nonce, body))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _body(self, cap=2 * 1024 * 1024) -> bytes:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if n <= 0 or n > cap:
                    return b""
                return self.rfile.read(n)

            def _body_json(self, raw: bytes) -> dict:
                try:
                    return json.loads(raw.decode("utf-8") or "{}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return {}

            def do_POST(self):
                path = urlparse(self.path).path.rstrip("/")
                # Pairing routes: only alive while a pairing window is open,
                # guarded by the code proofs — everything else must be signed.
                if path == "/link/pair/start":
                    raw = self._body()
                    code, obj = link._handle_pair_start(self._body_json(raw))
                    return self._send(code, obj)
                if path == "/link/pair/finish":
                    raw = self._body()
                    code, obj = link._handle_pair_finish(
                        self._body_json(raw), self.client_address[0])
                    return self._send(code, obj)

                if path == "/link/file":
                    return self._recv_file()

                raw = self._body()
                peer_id, peer, nonce = link._verify_request(self, raw)
                if not peer_id:
                    return self._send(403, {"success": False, "message": peer})
                body = self._body_json(raw)

                if path == "/link/send":
                    if not link._peer_may_control(peer_id):
                        return self._send(403, {"success": False,
                            "message": "單向配對：這台是主控端，對方無權操作"},
                            sign_for=peer, nonce=nonce)
                    res = link._local_send(body.get("sid", ""),
                                           body.get("text", ""),
                                           bool(body.get("submit", True)),
                                           source_peer=peer_id)
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/input":
                    if not link._peer_may_control(peer_id):
                        return self._send(403, {"success": False,
                            "message": "單向配對：對方無權操作這台"},
                            sign_for=peer, nonce=nonce)
                    res = link._execute("raw_input", {
                        "sid": body.get("sid", ""),
                        "data": body.get("data", "")}) or {}
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/resize":
                    if not link._peer_may_control(peer_id):
                        return self._send(403, {"success": False,
                            "message": "單向配對：對方無權操作這台"},
                            sign_for=peer, nonce=nonce)
                    res = link._execute("resize_pty", {
                        "sid": body.get("sid", ""),
                        "cols": body.get("cols", 0),
                        "rows": body.get("rows", 0)}) or {}
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/new":
                    if not link._peer_may_control(peer_id):
                        return self._send(403, {"success": False,
                            "message": "單向配對：對方無權操作這台"},
                            sign_for=peer, nonce=nonce)
                    res = link._execute("new_session", {
                        "cmd": body.get("cmd", "claude"),
                        "source": "frame_link"}) or {}
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/close":
                    if not link._peer_may_control(peer_id):
                        return self._send(403, {"success": False,
                            "message": "單向配對：對方無權操作這台"},
                            sign_for=peer, nonce=nonce)
                    res = link._execute("close_session", {
                        "sid": body.get("sid", ""),
                        "reason": "frame_link"}) or {}
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/paste":
                    # 遠端檢視端貼上的圖片：落地成檔 → 用 bracketed paste 把路徑
                    # 注入對方的 session（跟本機貼圖同一機制）。
                    if not link._peer_may_control(peer_id):
                        return self._send(403, {"success": False,
                            "message": "單向配對：對方無權操作這台"},
                            sign_for=peer, nonce=nonce)
                    saved = link._execute("save_paste", {
                        "filename": body.get("filename", "paste.png"),
                        "data_b64": body.get("data_b64", "")}) or {}
                    if not saved.get("success"):
                        return self._send(200, saved, sign_for=peer, nonce=nonce)
                    rpath = (saved.get("details") or {}).get("path", "")
                    link._execute("raw_input", {
                        "sid": body.get("sid", ""),
                        "data": " \x1b[200~" + rpath + "\x1b[201~"})
                    return self._send(200, {"success": True, "path": rpath},
                                      sign_for=peer, nonce=nonce)
                if path == "/link/message":
                    res = link._local_message(peer_id, str(body.get("text", "")))
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                return self._send(404, {"success": False, "message": "no such route"})

            def _recv_file(self):
                # Streamed: verify signature over headers + declared sha256,
                # never buffer the whole file in RAM.
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if n <= 0 or n > MAX_FILE_BYTES:
                    return self._send(413, {"success": False,
                                            "message": f"file too large (cap {MAX_FILE_BYTES})"})
                claimed_hash = self.headers.get("X-SF-Body-Sha256") or ""
                # Signature covers the claimed hash instead of the body bytes;
                # we verify the body against the claimed hash while streaming.
                peer_id, peer, nonce = link._verify_request(self, claimed_hash.encode())
                if not peer_id:
                    return self._send(403, {"success": False, "message": peer})
                fname = _safe_filename(self.headers.get("X-SF-Filename") or "file.bin")
                dest_dir = link._downloads_dir / _safe_dirname(
                    (link.peers().get(peer_id) or {}).get("name", peer_id[:8]))
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / fname
                i = 1
                while dest.exists():
                    stem, dot, ext = fname.partition(".")
                    dest = dest_dir / (f"{stem} ({i}){dot}{ext}" if dot else f"{fname} ({i})")
                    i += 1
                h = hashlib.sha256()
                tmp = dest.with_name(dest.name + ".part")
                try:
                    with open(tmp, "wb") as f:
                        left = n
                        while left > 0:
                            chunk = self.rfile.read(min(65536, left))
                            if not chunk:
                                break
                            h.update(chunk)
                            f.write(chunk)
                            left -= len(chunk)
                    if left != 0 or not _consteq(h.hexdigest(), claimed_hash):
                        tmp.unlink(missing_ok=True)
                        return self._send(400, {"success": False,
                                                "message": "hash mismatch / truncated"})
                    tmp.replace(dest)
                except OSError as e:
                    tmp.unlink(missing_ok=True)
                    return self._send(500, {"success": False, "message": str(e)})
                link._record_inbox({"kind": "file", "peer_id": peer_id,
                                    "name": dest.name, "path": str(dest),
                                    "size": n})
                res = {"success": True, "saved": str(dest)}
                return self._send(200, res, sign_for=peer, nonce=nonce)

            def do_GET(self):
                u = urlparse(self.path)
                path = u.path.rstrip("/")
                q = parse_qs(u.query)
                peer_id, peer, nonce = link._verify_request(self, b"")
                if not peer_id:
                    return self._send(403, {"success": False, "message": peer})

                if path == "/link/ping":
                    return self._send(200, {
                        "success": True, "frame_id": link.frame_id(),
                        "name": link.frame_name(), "version": link._version,
                        "ts": _now()}, sign_for=peer, nonce=nonce)
                if path == "/link/info":
                    if not link._peer_may_control(peer_id):
                        return self._send(200, {"success": True, "message": "0 sessions",
                            "details": {"sessions": []}, "frame_name": link.frame_name(),
                            "no_control": True}, sign_for=peer, nonce=nonce)
                    res = link._execute("list", {}) or {}
                    res["frame_name"] = link.frame_name()
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/peek":
                    if not link._peer_may_control(peer_id):
                        return self._send(403, {"success": False,
                            "message": "單向配對：對方無權查看這台"},
                            sign_for=peer, nonce=nonce)
                    sid = (q.get("sid") or [""])[0]
                    try:
                        lines = int((q.get("lines") or ["120"])[0])
                    except ValueError:
                        lines = 120
                    res = link._execute("peek", {"sid": sid, "lines": lines}) or {}
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/stream":
                    if not link._peer_may_control(peer_id):
                        return self._send(403, {"success": False,
                            "message": "單向配對：對方無權查看這台"},
                            sign_for=peer, nonce=nonce)
                    sid = (q.get("sid") or [""])[0]
                    try:
                        since = int((q.get("since") or ["-1"])[0])
                    except ValueError:
                        since = -1
                    res = link._stream_read(sid, since)
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/events":
                    try:
                        since = int((q.get("since") or ["0"])[0])
                    except ValueError:
                        since = 0
                    res = link._drain_outbox(peer_id, since)
                    return self._send(200, res, sign_for=peer, nonce=nonce)
                if path == "/link/outbox/file":
                    fid = (q.get("id") or [""])[0]
                    return self._serve_staged(peer_id, peer, nonce, fid)
                return self._send(404, {"success": False, "message": "no such route"})

            def _serve_staged(self, peer_id, peer, nonce, fid):
                if not re.fullmatch(r"[0-9a-f]{16,64}", fid or ""):
                    return self._send(400, {"success": False, "message": "bad id"})
                path = link._files_dir / peer_id / fid
                if not path.is_file():
                    return self._send(404, {"success": False, "message": "no such file"})
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-SF-Body-Sha256", _sha256_hex(data))
                self.send_header("X-SF-Sign",
                                 link._sign_response(peer, nonce,
                                                     _sha256_hex(data).encode()))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        return Handler

    # ── local actions on behalf of a peer ─────────────────────────────────
    def _local_send(self, sid: str, text: str, submit: bool,
                    source_peer: str) -> dict:
        peer_name = (self.peers().get(source_peer) or {}).get("name", "peer")
        res = self._execute("send", {"sid": sid, "text": text,
                                     "submit": submit}) or {}
        self._record_inbox({"kind": "remote_send", "peer_id": source_peer,
                            "peer_name": peer_name, "sid": sid,
                            "chars": len(text),
                            "ok": bool(res.get("success"))})
        return res

    def _local_message(self, peer_id: str, text: str) -> dict:
        text = text[:8000]
        peer_name = (self.peers().get(peer_id) or {}).get("name", "peer")
        self._record_inbox({"kind": "message", "peer_id": peer_id,
                            "peer_name": peer_name, "text": text})
        return {"success": True}

    def _record_inbox(self, ev: dict):
        ev = dict(ev)
        ev["ts"] = _now()
        ev["dir"] = ev.get("dir", "in")
        log = self._inbox.load()
        log.append(ev)
        self._inbox.save(log[-INBOX_KEEP:])
        self._notify(ev)

    def recent_events(self, limit: int = 100) -> list:
        return self._inbox.load()[-limit:]

    def _notify(self, ev: dict):
        try:
            self._notify_cb(ev)
        except Exception:
            pass

    # ── outbox (store-and-forward for unreachable peers) ─────────────────
    def _enqueue(self, peer_id: str, ev: dict) -> dict:
        with self._cursor_lock:
            out = self._outbox.load()
            box = out.setdefault(peer_id, {"next_id": 1, "events": []})
            ev = dict(ev)
            ev["id"] = box["next_id"]
            ev["ts"] = _now()
            box["next_id"] += 1
            box["events"] = (box["events"] + [ev])[-OUTBOX_KEEP:]
            self._outbox.save(out)
        return {"success": True, "queued": True, "id": ev["id"]}

    def _drain_outbox(self, peer_id: str, since: int) -> dict:
        with self._cursor_lock:
            out = self._outbox.load()
            box = out.get(peer_id) or {"next_id": 1, "events": []}
            # Events at or below the cursor are confirmed delivered — drop
            # them and delete their staged files.
            keep, dropped = [], []
            for ev in box["events"]:
                (keep if ev["id"] > since else dropped).append(ev)
            if dropped:
                box["events"] = keep
                out[peer_id] = box
                self._outbox.save(out)
                for ev in dropped:
                    if ev.get("type") == "file_offer":
                        try:
                            (self._files_dir / peer_id / ev["file_id"]).unlink(missing_ok=True)
                        except OSError:
                            pass
            cursor = box["next_id"] - 1
        return {"success": True, "cursor": cursor, "events": keep}

    # ── client side (signed requests to peers) ────────────────────────────
    def _plain_post(self, url: str, obj: dict) -> dict:
        data = json.dumps(obj).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
        # urllib raises HTTPError on non-2xx; callers surface body message
        # via the except path below in _request-style helpers.

    def _peer_base(self, peer: dict) -> str:
        return f"http://{peer.get('host')}:{peer.get('port')}"

    def _signed_request(self, peer: dict, method: str, path_qs: str,
                        body: bytes = b"", extra_headers: dict = None,
                        timeout: float = 8.0, sign_payload: bytes = None,
                        raw_response: bool = False):
        """sign_payload: what the signature covers (defaults to body) —
        file uploads sign the sha256 header instead of the streamed bytes."""
        ts = str(_now())
        nonce = secrets.token_hex(16)
        payload = body if sign_payload is None else sign_payload
        sig = _hmac_hex(bytes.fromhex(peer["secret"]),
                        self._string_to_sign(method, path_qs, ts, nonce,
                                             _sha256_hex(payload)))
        headers = {
            "X-SF-Peer": self.frame_id(),
            "X-SF-Ts": ts,
            "X-SF-Nonce": nonce,
            "X-SF-Sign": sig,
        }
        if body:
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        url = self._peer_base(peer) + path_qs
        req = urllib.request.Request(url, data=body or None, method=method,
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            resp_sig = resp.headers.get("X-SF-Sign") or ""
            if raw_response:
                # binary: signature covers the sha256 hex of the payload
                expected = self._sign_response(
                    peer, nonce, _sha256_hex(raw).encode())
                if not _consteq(resp_sig, expected):
                    raise ValueError("response signature mismatch")
                return raw, resp.headers
            expected = self._sign_response(peer, nonce, raw)
            if not _consteq(resp_sig, expected):
                raise ValueError("response signature mismatch")
            return json.loads(raw.decode("utf-8"))

    def _peer_or_err(self, peer_id: str):
        peer = self.peers().get(peer_id)
        if not peer:
            return None, {"success": False, "message": "no such peer"}
        if not peer.get("host") or not peer.get("port"):
            return None, {"success": False,
                          "message": "peer 沒有可連的位址（等對方來拉，或編輯位址）"}
        return peer, None

    def _mark_status(self, peer_id: str, ok: bool, err: str = ""):
        prev = self._peer_status.get(peer_id, {})
        cur = {"reachable": ok,
               "last_ok": _now() if ok else prev.get("last_ok", 0),
               "last_err": "" if ok else str(err)[:200]}
        self._peer_status[peer_id] = cur
        if prev.get("reachable") != ok:
            self._notify({"kind": "peer_status", "peer_id": peer_id,
                          "reachable": ok})

    def ping_peer(self, peer_id: str) -> dict:
        peer, err = self._peer_or_err(peer_id)
        if err:
            return err
        try:
            res = self._signed_request(peer, "GET", "/link/ping", timeout=5)
            self._mark_status(peer_id, bool(res.get("success")))
            return res
        except Exception as e:
            self._mark_status(peer_id, False, str(e))
            return {"success": False, "message": str(e)}

    def remote_info(self, peer_id: str) -> dict:
        peer, err = self._peer_or_err(peer_id)
        if err:
            return err
        try:
            res = self._signed_request(peer, "GET", "/link/info")
            self._mark_status(peer_id, True)
            return res
        except Exception as e:
            self._mark_status(peer_id, False, str(e))
            return {"success": False, "message": str(e)}

    def remote_peek(self, peer_id: str, sid: str, lines: int = 120) -> dict:
        peer, err = self._peer_or_err(peer_id)
        if err:
            return err
        try:
            path = f"/link/peek?sid={quote(sid)}&lines={int(lines)}"
            return self._signed_request(peer, "GET", path)
        except Exception as e:
            self._mark_status(peer_id, False, str(e))
            return {"success": False, "message": str(e)}

    def remote_stream(self, peer_id: str, sid: str, since: int = -1) -> dict:
        """Incremental raw-output pull for the seamless remote view.
        since=-1 → (re)attach: server returns current seq, empty data."""
        peer, err = self._peer_or_err(peer_id)
        if err:
            return err
        try:
            path = f"/link/stream?sid={quote(sid)}&since={int(since)}"
            res = self._signed_request(peer, "GET", path, timeout=6)
            self._mark_status(peer_id, True)
            return res
        except Exception as e:
            self._mark_status(peer_id, False, str(e))
            return {"success": False, "message": str(e)}

    def remote_input(self, peer_id: str, sid: str, data: str) -> dict:
        """Raw keystrokes → remote PTY (seamless terminal). Fire-and-forget-ish:
        no outbox fallback — typing into an offline peer makes no sense."""
        peer = self.peers().get(peer_id)
        if not peer or not peer.get("host") or not peer.get("port"):
            return {"success": False, "message": "peer 不可直連"}
        try:
            body = json.dumps({"sid": sid, "data": data}).encode()
            return self._signed_request(peer, "POST", "/link/input", body,
                                        timeout=5)
        except Exception as e:
            self._mark_status(peer_id, False, str(e))
            return {"success": False, "message": str(e)}

    def remote_resize(self, peer_id: str, sid: str, cols: int, rows: int) -> dict:
        peer = self.peers().get(peer_id)
        if not peer or not peer.get("host") or not peer.get("port"):
            return {"success": False, "message": "peer 不可直連"}
        try:
            body = json.dumps({"sid": sid, "cols": int(cols),
                               "rows": int(rows)}).encode()
            return self._signed_request(peer, "POST", "/link/resize", body,
                                        timeout=5)
        except Exception as e:
            return {"success": False, "message": str(e)}

    def remote_paste(self, peer_id: str, sid: str, data_url: str,
                     filename: str = "paste.png") -> dict:
        peer = self.peers().get(peer_id)
        if not peer or not peer.get("host") or not peer.get("port"):
            return {"success": False, "message": "peer 不可直連"}
        try:
            body = json.dumps({"sid": sid, "filename": filename,
                               "data_b64": data_url}).encode()
            return self._signed_request(peer, "POST", "/link/paste", body,
                                        timeout=30)
        except Exception as e:
            return {"success": False, "message": str(e)}

    def remote_new(self, peer_id: str, cmd: str = "claude") -> dict:
        peer, err = self._peer_or_err(peer_id)
        if err:
            return err
        try:
            body = json.dumps({"cmd": cmd or "claude"}).encode()
            res = self._signed_request(peer, "POST", "/link/new", body, timeout=15)
            self._mark_status(peer_id, True)
            return res
        except Exception as e:
            self._mark_status(peer_id, False, str(e))
            return {"success": False, "message": str(e)}

    def remote_close(self, peer_id: str, sid: str) -> dict:
        peer, err = self._peer_or_err(peer_id)
        if err:
            return err
        try:
            body = json.dumps({"sid": sid}).encode()
            res = self._signed_request(peer, "POST", "/link/close", body, timeout=10)
            self._mark_status(peer_id, True)
            return res
        except Exception as e:
            self._mark_status(peer_id, False, str(e))
            return {"success": False, "message": str(e)}

    def remote_send(self, peer_id: str, sid: str, text: str,
                    submit: bool = True) -> dict:
        peer = self.peers().get(peer_id)
        if not peer:
            return {"success": False, "message": "no such peer"}
        body = json.dumps({"sid": sid, "text": text, "submit": submit}).encode()
        if peer.get("host") and peer.get("port"):
            try:
                res = self._signed_request(peer, "POST", "/link/send", body)
                self._mark_status(peer_id, True)
                self._record_inbox({"kind": "remote_send", "dir": "out",
                                    "peer_id": peer_id, "sid": sid,
                                    "chars": len(text),
                                    "ok": bool(res.get("success"))})
                return res
            except Exception as e:
                self._mark_status(peer_id, False, str(e))
        return self._enqueue(peer_id, {"type": "send", "sid": sid,
                                       "text": text, "submit": submit})

    def send_message(self, peer_id: str, text: str) -> dict:
        peer = self.peers().get(peer_id)
        if not peer:
            return {"success": False, "message": "no such peer"}
        text = (text or "")[:8000]
        rec = {"kind": "message", "dir": "out", "peer_id": peer_id,
               "peer_name": peer.get("name", ""), "text": text}
        if peer.get("host") and peer.get("port"):
            try:
                body = json.dumps({"text": text}).encode()
                res = self._signed_request(peer, "POST", "/link/message", body)
                self._mark_status(peer_id, True)
                self._record_inbox(rec)
                return res
            except Exception as e:
                self._mark_status(peer_id, False, str(e))
        out = self._enqueue(peer_id, {"type": "message", "text": text})
        rec["queued"] = True
        self._record_inbox(rec)
        return out

    def send_file(self, peer_id: str, path: str) -> dict:
        peer = self.peers().get(peer_id)
        if not peer:
            return {"success": False, "message": "no such peer"}
        src = Path(os.path.expanduser(path or "")).resolve()
        if not src.is_file():
            return {"success": False, "message": f"檔案不存在：{src}"}
        size = src.stat().st_size
        if size > MAX_FILE_BYTES:
            return {"success": False, "message": f"檔案超過上限 {MAX_FILE_BYTES // (1024*1024)}MB"}
        data = src.read_bytes()
        digest = _sha256_hex(data)
        if peer.get("host") and peer.get("port"):
            try:
                res = self._signed_request(
                    peer, "POST", "/link/file", body=data,
                    sign_payload=digest.encode(),
                    extra_headers={"X-SF-Filename": src.name,
                                   "X-SF-Body-Sha256": digest,
                                   "Content-Type": "application/octet-stream"},
                    timeout=max(30.0, size / (256 * 1024)))
                self._mark_status(peer_id, True)
                self._record_inbox({"kind": "file", "dir": "out",
                                    "peer_id": peer_id, "name": src.name,
                                    "size": size,
                                    "ok": bool(res.get("success"))})
                return res
            except Exception as e:
                self._mark_status(peer_id, False, str(e))
        # Stage a copy + offer; the peer pulls it next poll.
        fid = secrets.token_hex(16)
        stage_dir = self._files_dir / peer_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, stage_dir / fid)
        res = self._enqueue(peer_id, {"type": "file_offer", "file_id": fid,
                                      "name": src.name, "size": size,
                                      "sha256": digest})
        self._record_inbox({"kind": "file", "dir": "out", "peer_id": peer_id,
                            "name": src.name, "size": size, "queued": True})
        return res

    # ── poll loop: reachability + pull events from reachable peers ───────
    def poll_once(self, cursors: dict) -> bool:
        """One poll pass over all addressable peers. Mutates `cursors`
        (peer_id -> last confirmed event id); returns True when it changed.
        Split out of the loop so tests can drive it synchronously."""
        changed = False
        for peer_id, peer in self.peers().items():
            if self._poll_stop.is_set():
                break
            if not peer.get("host") or not peer.get("port"):
                continue
            try:
                since = int(cursors.get(peer_id, 0))
                res = self._signed_request(
                    peer, "GET", f"/link/events?since={since}", timeout=6)
                self._mark_status(peer_id, True)
                for ev in res.get("events") or []:
                    self._apply_pulled(peer_id, peer, ev)
                new_cursor = int(res.get("cursor") or since)
                if new_cursor != since:
                    cursors[peer_id] = new_cursor
                    changed = True
            except Exception as e:
                self._mark_status(peer_id, False, str(e))
        return changed

    def _sweep_streams(self):
        """丟掉已過 watch TTL 的 stream buffer——lazy 清理只在該 sid 又有 output
        或又被拉時才發生，靜默切走的分頁得靠這個週期性掃除。"""
        cutoff = _now() - self.STREAM_WATCH_TTL
        with self._streams_lock:
            for sid in [s for s, st in self._streams.items()
                        if st["watch_ts"] < cutoff]:
                del self._streams[sid]

    def _poll_loop(self):
        cursors_store = _JsonStore(self._state_dir / "frame_link_cursors.json", {})
        while not self._poll_stop.is_set():
            try:
                cursors = cursors_store.load()
                if self.poll_once(cursors):
                    cursors_store.save(cursors)
                self._sweep_streams()
            except Exception as e:
                self._log(f"[link] poll loop error: {e}")
            self._poll_stop.wait(POLL_INTERVAL)

    def _apply_pulled(self, peer_id: str, peer: dict, ev: dict):
        kind = ev.get("type")
        if kind == "message":
            self._local_message(peer_id, str(ev.get("text", "")))
        elif kind == "send":
            self._local_send(str(ev.get("sid", "")), str(ev.get("text", "")),
                             bool(ev.get("submit", True)), source_peer=peer_id)
        elif kind == "file_offer":
            fid = str(ev.get("file_id", ""))
            try:
                raw, headers = self._signed_request(
                    peer, "GET", f"/link/outbox/file?id={quote(fid)}",
                    timeout=120, raw_response=True)
            except Exception as e:
                self._log(f"[link] file pull failed: {e}")
                return
            if ev.get("sha256") and not _consteq(_sha256_hex(raw), ev["sha256"]):
                self._log("[link] pulled file hash mismatch — dropped")
                return
            fname = _safe_filename(str(ev.get("name") or "file.bin"))
            dest_dir = self._downloads_dir / _safe_dirname(peer.get("name", peer_id[:8]))
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / fname
            i = 1
            while dest.exists():
                stem, dot, ext = fname.partition(".")
                dest = dest_dir / (f"{stem} ({i}){dot}{ext}" if dot else f"{fname} ({i})")
                i += 1
            dest.write_bytes(raw)
            self._record_inbox({"kind": "file", "peer_id": peer_id,
                                "name": dest.name, "path": str(dest),
                                "size": len(raw)})
