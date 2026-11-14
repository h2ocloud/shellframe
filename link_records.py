"""What Frame Link keeps: its JSON files (inbox log, outbox queues, cursors) and its lines in the
log about who asked for what.

The log lines (v0.44.5): a tablet's messages once reached neither paired computer and neither kept
a trace, because a request that failed verification was answered 403 and forgotten. So:
  - a refused request: its route, the peer it claims to be (id prefix and name) and why, at most
    once a minute per peer and reason, since a phone retries every few seconds;
  - an accepted POST: its route, the peer and the tab, except /link/input and /link/resize, which
    come per keystroke and per resize.
Signatures, nonces and message text never appear.
"""
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

QUIET_S = 60
NOT_LOGGED = ("/link/input", "/link/resize")


class JsonStore:
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


class LinkAudit:
    def __init__(self, log, peers):
        self._log, self._peers = log, peers          # FrameLink._log, FrameLink.peers
        self._last = {}                              # (peer, reason) -> when it was last logged
        self._lock = threading.Lock()

    def label(self, peer_id: str) -> str:
        name = (self._peers().get(peer_id) or {}).get("name", "")
        return (peer_id[:8] or "?") + (f" ({name})" if name else "")

    def refused(self, handler, why: str):
        claimed = handler.headers.get("X-SF-Peer") or ""
        key, now = (claimed, why), time.time()
        with self._lock:
            if now - self._last.get(key, 0) < QUIET_S:
                return
            self._last[key] = now
        self._log(f"[link] refused {handler.command} {urlparse(handler.path).path} "
                  f"from peer {self.label(claimed)}: {why}")

    def accepted(self, path: str, peer_id: str, body: dict):
        if path in NOT_LOGGED:
            return
        sid = str(body.get("sid") or body.get("name") or "")[:40]
        self._log(f"[link] {path} from peer {self.label(peer_id)}" + (f" → {sid}" if sid else ""))
