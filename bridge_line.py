"""
LINE Bridge plugin for ShellFrame.

Runs a small local webhook server, verifies LINE signatures, forwards text
messages into ShellFrame sessions, and pushes AI replies back via LINE
Messaging API. No external dependencies.
"""

import base64
import hashlib
import hmac
import json
import os as _os
import re
import sys as _sys
import tempfile
import threading
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path as _Path
from urllib.parse import parse_qs, urlparse

from bridge_base import BridgeBase, BridgeConfigBase
from bridge_telegram import strip_ansi

_IS_WIN = _sys.platform == "win32"
_TMP_DIR = tempfile.gettempdir() if _IS_WIN else "/tmp"
_LOG_FILE = _os.path.join(_TMP_DIR, "shellframe_line_bridge.log")

DEFAULT_LINE_PROMPT = (
    "[LINE] Replying through ShellFrame LINE bridge. Reply as plain text "
    "suitable for LINE mobile. Keep it concise and scannable, avoid Markdown "
    "tables and decorative dividers, and do not call LINE/TG tools. The "
    "upstream company webhook may poll ShellFrame for response messages, so "
    "make each reply self-contained."
)

# 只放通用路由。客戶／專案專屬的關鍵字不屬於 public repo，而且那正是每台安裝
# 都不一樣的部分——放在使用者 config 的 "line_gateway_routes" 裡（見
# _gateway_routes），會排在內建路由前面。
LINE_GATEWAY_ROUTES = (
    ("LINE-Reminder", (
        "提醒", "打卡", "remind", "reminder", "鬧鐘", "排程", "停止", "關掉", "關閉",
    )),
    ("LINE-Ops", (
        "line", "webhook", "toolhub", "openclaw", "hermes", "水位", "api",
        "token", "credential", "npm", "gmail", "sql", "資料庫",
    )),
    ("LINE-Dev", (
        "shellframe", "gateway", "派工", "更名", "code", "repo", "git", "github",
        "pr", "ci", "bug", "測試", "修", "部署", "程式",
    )),
)

GENERIC_SESSION_LABELS = {
    "bash", "zsh", "sh", "claude", "codex", "sf-codex", "python", "python3",
}


def _llog(msg: str):
    try:
        if not msg.endswith("\n"):
            msg += "\n"
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass


@dataclass
class LineBridgeConfig(BridgeConfigBase):
    channel_access_token: str = ""
    channel_secret: str = ""
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8787
    webhook_path: str = "/line/webhook"
    public_webhook_url: str = ""
    delivery_mode: str = "push"
    poll_path: str = "/line/poll"
    forward_secret: str = ""

    def __post_init__(self):
        if not self.webhook_path.startswith("/"):
            self.webhook_path = "/" + self.webhook_path
        if not self.poll_path.startswith("/"):
            self.poll_path = "/" + self.poll_path
        if self.delivery_mode not in ("push", "poll"):
            self.delivery_mode = "push"
        try:
            self.webhook_port = int(self.webhook_port)
        except (TypeError, ValueError):
            self.webhook_port = 8787


def _read_settings() -> dict:
    try:
        cfg_file = _Path.home() / ".config" / "shellframe" / "config.json"
        if cfg_file.exists():
            return (json.loads(cfg_file.read_text(encoding="utf-8"))
                    .get("settings", {}) or {})
    except Exception:
        pass
    return {}


def _gateway_routes():
    """使用者自訂路由 ＋ 內建通用路由，自訂的優先。

    config.json 的 settings.line_gateway_routes 形狀：
        [{"label": "LINE-Foo", "keywords": ["kw1", "kw2"]}, ...]
    形狀不對的條目直接跳過——路由表壞掉不該讓整條 LINE 通道停擺。
    """
    custom = []
    for item in (_read_settings().get("line_gateway_routes") or []):
        try:
            label = str(item["label"]).strip()
            keywords = tuple(str(k) for k in item["keywords"] if str(k).strip())
        except (TypeError, KeyError, ValueError):
            continue
        if label and keywords:
            custom.append((label, keywords))
    return tuple(custom) + LINE_GATEWAY_ROUTES


def get_line_prompt() -> str:
    """LINE per-turn preamble. User config > built-in. Empty string = user off."""
    settings = _read_settings()
    if "line_prompt" in settings:
        return (settings.get("line_prompt") or "").strip()
    return DEFAULT_LINE_PROMPT


def clean_line_response(text: str) -> str:
    """Drop LINE prompt/input echoes that full-screen TUIs repaint into output."""
    drop_prefixes = (
        "你是從 LINE",
        "請用繁體中文",
        "你可以協助操作 ShellFrame",
        "[cmd] 新增",
        "close 關閉目前 tab",
        "工。不要輸出",
        "credential。",
        "等待確認",
        "任務完成時附",
        "文字回覆。",
        "Find and fix a bug in @filename",
        "Improve documentation in @filename",
        "Explain this codebase",
    )
    drop_contains = (
        "ShellFrame：/list",
        "不要輸出 token",
        "任務完成時附",
        "適合 LINE 閱讀",
        "外部可見或破壞性操作",
    )
    lines = []
    previous = None
    seen = set()
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\[LINE [^\]]+\]:", stripped):
            continue
        if stripped.startswith("›"):
            continue
        if stripped.startswith(drop_prefixes):
            continue
        if re.match(r'^\[[^\]]+\]\s+Ran\s+', stripped):
            continue
        if stripped.startswith(("Ran ", "Bash(", "Read ", "Search ", "Explored ", "Edited ", "Write ", "Update ")):
            continue
        if re.match(r'^[-+]\s*(ssh|scp|curl|tmux|cd|mvn|git|python|sleep)\b', stripped):
            continue
        if re.search(r'\b(max_output_tokens|yield_time_ms|session_id|exec_command|write_stdin)\b', stripped):
            continue
        if re.search(r'\.\.\. \+\d+ lines\b|\(ctrl \+ t to view transcript\)|gpt-[\w.-]+ high', stripped):
            continue
        if any(part in stripped for part in drop_contains):
            continue
        if stripped == previous:
            continue
        normalized = re.sub(r"\s+", " ", stripped)
        if len(normalized) >= 12 and normalized in seen:
            continue
        seen.add(normalized)
        lines.append(stripped)
        previous = stripped
    return "\n".join(lines).strip()


def _extract_marked_reply(raw_text: str, start_marker: str, end_marker: str) -> str:
    if not raw_text or not start_marker or not end_marker:
        return ""
    raw_for_marker = strip_ansi(raw_text, sent_texts=[])
    start_idx = raw_for_marker.rfind(start_marker)
    if start_idx < 0:
        return ""
    end_idx = raw_for_marker.find(end_marker, start_idx + len(start_marker))
    if end_idx < 0:
        return ""
    return raw_for_marker[start_idx + len(start_marker):end_idx]


class LineSessionSlot:
    def __init__(self, sid: str, label: str, write_fn, index: int, peek_fn=None):
        self.sid = sid
        self.label = label
        self.write_fn = write_fn
        self.peek_fn = peek_fn
        self.index = index
        self.output_lock = threading.Lock()
        self.pending = ""
        self.last_output_time = 0.0
        self.has_user_msg = False
        self.sent_texts = []
        self.awaiting_response = False
        self.last_input_time = 0.0
        self.last_activity_time = time.time()
        self.expect_marker = False
        self.reply_start_marker = ""
        self.reply_end_marker = ""
        self.pending_user_id = ""
        self.pending_target_id = ""


class LineBridge(BridgeBase):
    PLATFORM = "line"

    def __init__(self, bridge_id: str, config: LineBridgeConfig, on_status_change=None,
                 on_new_session=None, on_close_session=None, on_consume_init=None,
                 on_rename_session=None, on_session_ready=None,
                 gateway_worker_cmd: str = ""):
        super().__init__(bridge_id, config, write_fn=None, on_status_change=on_status_change)
        self.slots = {}
        self._slot_order = []
        self._slots_lock = threading.Lock()
        self._user_active = {}
        self._user_target = {}
        self._server = None
        self._server_thread = None
        self._flush_thread = None
        self._stop_event = threading.Event()
        self._bot_info = {}
        self._outbox = []
        self._outbox_lock = threading.Lock()
        self._last_user_id = ""
        self._ui_active_sid = ""
        self._on_new_session = on_new_session
        self._on_close_session = on_close_session
        self._on_consume_init = on_consume_init
        self._on_rename_session = on_rename_session
        self._on_session_ready = on_session_ready
        self._gateway_worker_cmd = (
            _os.environ.get("SHELLFRAME_LINE_WORKER_CMD", "").strip()
            or (gateway_worker_cmd or "").strip()
            or "codex"
        )
    def register_session(self, sid: str, label: str, write_fn, peek_fn=None):
        with self._slots_lock:
            if sid in self.slots:
                self.slots[sid].label = label
                self.slots[sid].write_fn = write_fn
                return
            self.slots[sid] = LineSessionSlot(sid, label, write_fn, len(self._slot_order) + 1, peek_fn=peek_fn)
            self._slot_order.append(sid)

    def unregister_session(self, sid: str):
        with self._slots_lock:
            self.slots.pop(sid, None)
            if sid in self._slot_order:
                self._slot_order.remove(sid)
            for i, slot_sid in enumerate(self._slot_order):
                self.slots[slot_sid].index = i + 1
            for uid, active_sid in list(self._user_active.items()):
                if active_sid == sid:
                    if self._slot_order:
                        self._user_active[uid] = self._slot_order[0]
                    else:
                        self._user_active.pop(uid, None)

    def reorder_slots(self, ordered_sids: list):
        with self._slots_lock:
            new_order = [sid for sid in ordered_sids if sid in self.slots]
            for sid in self._slot_order:
                if sid not in new_order and sid in self.slots:
                    new_order.append(sid)
            self._slot_order = new_order
            for i, sid in enumerate(self._slot_order):
                self.slots[sid].index = i + 1

    def start(self):
        if self.active:
            return
        if self.config.delivery_mode == "push" and (
            not self.config.channel_access_token or not self.config.channel_secret
        ):
            self._emit_status({"state": "error", "message": "LINE token and secret are required"})
            return
        if self.config.channel_access_token:
            ok, info = self._get_bot_info()
            if not ok and self.config.delivery_mode == "push":
                self._emit_status({"state": "error", "message": info.get("message", "LINE token check failed")})
                return
            self._bot_info = info if ok else {"displayName": "LINE forward"}
        else:
            self._bot_info = {"displayName": "LINE forward"}
        self._stop_event.clear()
        self.connected = True
        self.active = True
        self.paused = False

        handler = self._make_handler()
        try:
            self._server = ThreadingHTTPServer(
                (self.config.webhook_host, self.config.webhook_port),
                handler,
            )
        except OSError as e:
            self.connected = False
            self.active = False
            self._emit_status({"state": "error", "message": f"webhook server failed: {e}"})
            return

        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()
        self._emit_status({"state": "connected", "bot": self._bot_info.get("displayName", "")})

    def stop(self):
        self.active = False
        self.connected = False
        self._stop_event.set()
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        self._emit_status({"state": "stopped"})

    def feed_output(self, sid: str, raw_text: str):
        if not self.active or self.paused:
            return
        slot = self.slots.get(sid)
        if not slot:
            return
        with slot.output_lock:
            slot.pending += raw_text
            if len(slot.pending) > 120000:
                slot.pending = slot.pending[-120000:]
            now = time.time()
            if slot.last_output_time == 0:
                slot.last_output_time = now
            else:
                slot.last_output_time = now

    def get_status(self) -> dict:
        return {
            "state": "connected" if self.connected else "stopped",
            "active": self.active,
            "paused": self.paused,
            "bot": self._bot_info.get("displayName", ""),
            "sessions": len(self.slots),
            "active_sid": self._status_active_sid(),
            "webhook_url": self.local_webhook_url(),
            "poll_url": self.local_poll_url(),
            "public_webhook_url": self.config.public_webhook_url,
            "allowed_users": list(self.config.allowed_users or []),
            "delivery_mode": self.config.delivery_mode,
            "outbox": len(self._outbox),
        }

    def local_webhook_url(self) -> str:
        host = self.config.webhook_host
        return f"http://{host}:{self.config.webhook_port}{self.config.webhook_path}"

    def local_poll_url(self) -> str:
        host = self.config.webhook_host
        return f"http://{host}:{self.config.webhook_port}{self.config.poll_path}"

    def switch_active_session(self, sid: str):
        if sid not in self.slots:
            raise ValueError("No such LINE session")
        self._ui_active_sid = sid
        for uid in list(self._user_active.keys()):
            self._user_active[uid] = sid

    def _status_active_sid(self) -> str:
        if self._last_user_id:
            sid = self._user_active.get(self._last_user_id)
            if sid in self.slots:
                return sid
        if self._ui_active_sid in self.slots:
            return self._ui_active_sid
        return self._slot_order[0] if self._slot_order else ""

    def _get_bot_info(self):
        try:
            req = urllib.request.Request(
                "https://api.line.me/v2/bot/info",
                headers={"Authorization": f"Bearer {self.config.channel_access_token}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return True, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = str(e)
            return False, {"message": body}
        except Exception as e:
            return False, {"message": str(e)}

    def _line_api(self, path: str, payload: dict, timeout: float = 8):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "https://api.line.me" + path,
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.channel_access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return {"ok": True, "body": raw}
        except Exception as e:
            _llog(f"line api error {path}: {e}")
            return {"ok": False, "error": str(e)}

    def _reply_text(self, reply_token: str, text: str):
        if not reply_token:
            return
        chunks = self._chunks(text)
        if not chunks:
            return
        self._line_api("/v2/bot/message/reply", {
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": chunks[0]}],
        })

    def _push_text(self, target_id: str, text: str):
        if not target_id:
            return
        if self.config.delivery_mode == "poll":
            self._enqueue_outbox(target_id, text)
            return
        for chunk in self._chunks(text):
            self._line_api("/v2/bot/message/push", {
                "to": target_id,
                "messages": [{"type": "text", "text": chunk}],
            }, timeout=5)

    @staticmethod
    def _chunks(text: str, limit: int = 4900):
        text = (text or "").strip()
        if not text:
            return []
        return [text[i:i + limit] for i in range(0, len(text), limit)]

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        digest = hmac.new(
            self.config.channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, signature or "")

    def _forward_auth_ok(self, headers) -> bool:
        secret = (self.config.forward_secret or "").strip()
        if not secret:
            return True
        provided = (
            headers.get("X-ShellFrame-Forward-Key")
            or headers.get("X-ShellFrame-Token")
            or ""
        ).strip()
        auth = headers.get("Authorization", "").strip()
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        return hmac.compare_digest(secret, provided)

    def _enqueue_outbox(self, target_id: str, text: str):
        chunks = self._chunks(text)
        if not chunks:
            return
        now = time.time()
        with self._outbox_lock:
            for chunk in chunks:
                self._outbox.append({
                    "id": str(uuid.uuid4()),
                    "targetId": target_id,
                    "text": chunk,
                    "ts": now,
                    "platform": "line",
                })
            if len(self._outbox) > 500:
                self._outbox = self._outbox[-500:]

    def _poll_outbox(self, target_id: str = "", limit: int = 20):
        try:
            limit = max(1, min(int(limit or 20), 100))
        except (TypeError, ValueError):
            limit = 20
        with self._outbox_lock:
            selected = []
            remaining = []
            for item in self._outbox:
                if len(selected) < limit and (not target_id or item.get("targetId") == target_id):
                    selected.append(item)
                else:
                    remaining.append(item)
            self._outbox = remaining
        return selected

    def _reply_or_enqueue(self, reply_token: str, target_id: str, text: str):
        if self.config.delivery_mode == "poll":
            self._enqueue_outbox(target_id, text)
        else:
            self._reply_text(reply_token, text)

    def _make_handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _send_json(self, code: int, payload: dict):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                if path in ("/", "/health", bridge.config.webhook_path.rstrip("/") + "/health"):
                    self._send_json(200, {
                        "ok": True,
                        "platform": "line",
                        "webhook": bridge.local_webhook_url(),
                        "poll": bridge.local_poll_url(),
                        "active": bridge.active,
                        "delivery_mode": bridge.config.delivery_mode,
                    })
                    return
                if parsed.path == bridge.config.poll_path:
                    if not bridge._forward_auth_ok(self.headers):
                        self._send_json(403, {"ok": False, "message": "bad forward key"})
                        return
                    query = parse_qs(parsed.query)
                    target = (query.get("targetId") or query.get("target_id") or query.get("chatId") or [""])[0]
                    limit = (query.get("limit") or ["20"])[0]
                    self._send_json(200, {
                        "ok": True,
                        "messages": bridge._poll_outbox(target_id=target, limit=limit),
                    })
                    return
                self._send_json(404, {"ok": False, "message": "not found"})

            def do_POST(self):
                if self.path.split("?", 1)[0] != bridge.config.webhook_path:
                    self._send_json(404, {"ok": False, "message": "not found"})
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length)
                signature = self.headers.get("X-Line-Signature", "")
                if signature and bridge.config.channel_secret:
                    if not bridge._verify_signature(body, signature):
                        self._send_json(403, {"ok": False, "message": "bad signature"})
                        return
                elif bridge.config.delivery_mode == "push":
                    self._send_json(403, {"ok": False, "message": "bad signature"})
                    return
                elif not bridge._forward_auth_ok(self.headers):
                    self._send_json(403, {"ok": False, "message": "bad forward key"})
                    return
                poll_target_override = (
                    self.headers.get("X-ShellFrame-Poll-Target")
                    or self.headers.get("X-Poll-Target")
                    or ""
                ).strip()
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception:
                    self._send_json(400, {"ok": False, "message": "bad json"})
                    return
                bridge._handle_webhook(payload, poll_target_override=poll_target_override)
                self._send_json(200, {"ok": True})

        return Handler

    def _handle_webhook(self, payload: dict, poll_target_override: str = ""):
        for event in self._iter_events(payload):
            if event.get("type") != "message":
                continue
            msg = event.get("message") or {}
            if msg.get("type") != "text":
                source = event.get("source") or {}
                target_id = source.get("groupId") or source.get("roomId") or source.get("userId") or ""
                self._reply_or_enqueue(
                    event.get("replyToken", ""),
                    target_id,
                    "ShellFrame LINE bridge currently supports text messages.",
                )
                continue
            source = event.get("source") or {}
            user_id = source.get("userId") or ""
            real_target_id = source.get("groupId") or source.get("roomId") or user_id
            target_id = real_target_id
            if self.config.delivery_mode == "poll" and poll_target_override:
                target_id = poll_target_override
            if not user_id:
                user_id = real_target_id or target_id
            if self.config.allowed_users and user_id not in self.config.allowed_users and real_target_id not in self.config.allowed_users:
                self._reply_or_enqueue(event.get("replyToken", ""), target_id, "This LINE user/chat is not allowed for ShellFrame.")
                continue
            if user_id:
                self._last_user_id = user_id
                self._user_active.setdefault(user_id, self._ui_active_sid or (self._slot_order[0] if self._slot_order else ""))
                self._user_target[user_id] = real_target_id
            self._handle_text(user_id, target_id, event.get("replyToken", ""), msg.get("text", ""))

    def _iter_events(self, payload: dict):
        events = payload.get("events")
        if isinstance(events, list):
            return events
        event = payload.get("event")
        if isinstance(event, dict):
            return [event]
        text = (
            payload.get("text")
            or (payload.get("message") or {}).get("text")
            or payload.get("messageText")
            or payload.get("body")
            or ""
        )
        source = payload.get("source") or {}
        user_id = (
            payload.get("userId") or payload.get("user_id")
            or payload.get("lineUserId") or source.get("userId") or ""
        )
        target_id = (
            payload.get("targetId") or payload.get("target_id")
            or payload.get("groupId") or payload.get("roomId")
            or source.get("groupId") or source.get("roomId") or user_id
        )
        if not text:
            return []
        event_source = {"userId": user_id}
        if target_id and target_id != user_id:
            event_source["groupId"] = target_id
        return [{
            "type": "message",
            "replyToken": payload.get("replyToken", ""),
            "source": event_source,
            "message": {"type": "text", "text": str(text)},
        }]

    def _active_sid(self, user_id: str) -> str:
        sid = self._user_active.get(user_id)
        if sid in self.slots:
            return sid
        if self._ui_active_sid in self.slots:
            return self._ui_active_sid
        return self._slot_order[0] if self._slot_order else ""

    def _route_label(self, text: str) -> str:
        lowered = (text or "").casefold()
        for label, keywords in _gateway_routes():
            if any(keyword.casefold() in lowered for keyword in keywords):
                return label
        return "LINE-Gateway"

    @staticmethod
    def _is_generic_label(label: str, sid: str) -> bool:
        normalized = (label or "").strip().casefold()
        if not normalized:
            return True
        if sid and normalized == sid.casefold():
            return True
        return normalized in GENERIC_SESSION_LABELS

    def _rename_slot(self, sid: str, label: str):
        slot = self.slots.get(sid)
        if not slot or slot.label == label:
            return
        if self._on_rename_session:
            try:
                self._on_rename_session(sid, label)
                return
            except Exception as e:
                _llog(f"line gateway rename failed sid={sid} label={label}: {e}")
        slot.label = label

    def _wait_for_slot(self, sid: str, timeout: float = 8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            slot = self.slots.get(sid)
            if slot:
                return slot
            time.sleep(0.1)
        return self.slots.get(sid)

    def _wait_for_ready(self, sid: str, timeout: float = 25.0) -> bool:
        if not self._on_session_ready:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self._on_session_ready(sid):
                    return True
            except Exception as e:
                _llog(f"line gateway ready check failed sid={sid}: {e}")
                return True
            time.sleep(0.25)
        return False

    def _gateway_sid(self, user_id: str, label: str) -> str:
        wanted = (label or "LINE-Gateway").casefold()
        with self._slots_lock:
            for sid in self._slot_order:
                slot = self.slots.get(sid)
                if slot and (slot.label or "").casefold() == wanted:
                    return sid

        if self._on_new_session:
            try:
                sid = self._on_new_session(self._gateway_worker_cmd)
                if sid and self._wait_for_slot(sid):
                    return sid
            except Exception as e:
                _llog(f"line gateway new session failed label={label}: {e}")
        return self._active_sid(user_id)

    def _handle_text(self, user_id: str, target_id: str, reply_token: str, text: str):
        text = (text or "").strip()
        if not text:
            return
        if text in ("/help", "help"):
            self._reply_or_enqueue(reply_token, target_id, self._help_text())
            return
        if text in ("/list", "list"):
            self._reply_or_enqueue(reply_token, target_id, self._list_text())
            return
        if text.startswith("/") and len(text) > 1 and text[1:].isdigit():
            idx = int(text[1:])
            if 1 <= idx <= len(self._slot_order):
                sid = self._slot_order[idx - 1]
                self._user_active[user_id] = sid
                self._ui_active_sid = sid
                self._reply_or_enqueue(reply_token, target_id, f"Switched to {idx}. {self.slots[sid].label}")
            else:
                self._reply_or_enqueue(reply_token, target_id, "No such session number.")
            return
        if text.startswith("/new"):
            cmd = text[4:].strip() or "claude"
            if not self._on_new_session:
                self._reply_or_enqueue(reply_token, target_id, "New session is not available.")
                return
            sid = self._on_new_session(cmd)
            if sid:
                self._user_active[user_id] = sid
                self._ui_active_sid = sid
                self._reply_or_enqueue(reply_token, target_id, f"Created and switched to {sid}.")
            else:
                self._reply_or_enqueue(reply_token, target_id, "Failed to create session.")
            return
        if text == "/close":
            sid = self._active_sid(user_id)
            if sid and self._on_close_session:
                self._on_close_session(sid)
                self._reply_or_enqueue(reply_token, target_id, f"Closed {sid}.")
            return

        route_label = self._route_label(text)
        sid = self._gateway_sid(user_id, route_label)
        slot = self.slots.get(sid)
        if not slot:
            self._reply_or_enqueue(reply_token, target_id, "No ShellFrame session is available.")
            return
        if slot.awaiting_response:
            self._reply_or_enqueue(reply_token, target_id, f"{slot.label} 還在處理上一則訊息，請稍後再送。")
            return
        self._rename_slot(sid, route_label)
        slot = self.slots.get(sid) or slot
        self._user_active[user_id] = sid
        self._ui_active_sid = sid
        if not self._wait_for_ready(sid):
            self._reply_or_enqueue(
                reply_token,
                target_id,
                f"{slot.label} 已建立，但 agent 尚未進入可輸入狀態；這次訊息未送出，請稍後重送。",
            )
            return
        line_text = f"[LINE {user_id}]: {text}" if self.config.prefix_enabled else text
        init_prompt = ""
        if self._on_consume_init:
            try:
                init_prompt = self._on_consume_init(sid) or ""
            except Exception:
                init_prompt = ""
        payload = line_text
        marker_token = f"LINE_REPLY_{uuid.uuid4().hex[:8]}"
        start_marker = f"[[{marker_token}]]"
        end_marker = f"[[/{marker_token}]]"
        marker_prompt = f"最終要回 LINE 的文字請放在 {start_marker} 和 {end_marker} 之間。"
        gateway_prompt = (
            f"ShellFrame gateway 已派工到 {slot.label}。"
            "請只代表這個 tab 回覆這次 LINE 訊息；不要冒稱其他 agent，也不要回覆 marker 之外的文字。"
        )
        if init_prompt:
            payload = init_prompt + "\n\n" + gateway_prompt + "\n\n" + marker_prompt + "\n\n---\nUser's first LINE message: " + line_text
            slot.sent_texts.append(init_prompt)
        else:
            preamble = get_line_prompt()
            if preamble:
                payload = preamble + "\n\n" + gateway_prompt + "\n\n" + marker_prompt + "\n\n" + line_text
                slot.sent_texts.append(preamble)
        slot.sent_texts.append(line_text)
        if len(slot.sent_texts) > 30:
            slot.sent_texts = slot.sent_texts[-30:]
        with slot.output_lock:
            slot.pending = ""
            slot.last_output_time = 0
        slot.has_user_msg = True
        slot.awaiting_response = True
        slot.last_input_time = time.time()
        slot.last_activity_time = slot.last_input_time
        slot.expect_marker = True
        slot.reply_start_marker = start_marker
        slot.reply_end_marker = end_marker
        slot.pending_user_id = user_id
        slot.pending_target_id = target_id
        # Some full-screen TUIs treat a large paste ending with CR as text entry
        # only. Send Enter as a separate keystroke so LINE messages submit.
        slot.write_fn(payload)
        time.sleep(0.05)
        slot.write_fn("\r")
        if self.config.delivery_mode != "poll":
            self._reply_or_enqueue(reply_token, target_id, f"Sent to {slot.label}.")

    def _help_text(self) -> str:
        return (
            "ShellFrame LINE bridge\n"
            "/list - list sessions\n"
            "/1 /2 ... - switch session\n"
            "/new [cmd] - create session\n"
            "/close - close current session\n"
            "Any other text is sent to the active session."
        )

    def _list_text(self) -> str:
        if not self._slot_order:
            return "No sessions."
        lines = ["ShellFrame sessions:"]
        for sid in self._slot_order:
            slot = self.slots[sid]
            lines.append(f"/{slot.index} {slot.label} ({sid})")
        return "\n".join(lines)

    def _flush_loop(self):
        while self.active and not self._stop_event.is_set():
            try:
                time.sleep(0.5)
                if self.paused:
                    continue
                with self._slots_lock:
                    sids = list(self._slot_order)
                now = time.time()
                for sid in sids:
                    slot = self.slots.get(sid)
                    if not slot:
                        continue
                    with slot.output_lock:
                        if not slot.pending:
                            if slot.expect_marker and slot.peek_fn:
                                try:
                                    screen_raw = slot.peek_fn() or ""
                                except Exception:
                                    screen_raw = ""
                                if _extract_marked_reply(screen_raw, slot.reply_start_marker, slot.reply_end_marker):
                                    slot.pending = screen_raw
                                    slot.last_output_time = 0
                                else:
                                    continue
                            else:
                                continue
                        if not slot.has_user_msg:
                            if now - slot.last_output_time >= 1.0:
                                slot.pending = ""
                                slot.last_output_time = 0
                            continue
                        if now - slot.last_output_time < 2.5:
                            continue
                        raw = slot.pending
                        screen_raw = ""
                        if slot.expect_marker and slot.peek_fn:
                            try:
                                screen_raw = slot.peek_fn() or ""
                            except Exception:
                                pass
                        marked_raw = ""
                        if slot.expect_marker:
                            start_marker = slot.reply_start_marker
                            end_marker = slot.reply_end_marker
                            marked_raw = _extract_marked_reply(screen_raw, start_marker, end_marker)
                            if not marked_raw:
                                marked_raw = _extract_marked_reply(raw, start_marker, end_marker)
                            has_reply_marker = bool(marked_raw)
                            if not has_reply_marker:
                                slot.last_output_time = now
                                continue
                        target_id = slot.pending_target_id
                        slot.pending = ""
                        slot.last_output_time = 0
                        slot.awaiting_response = False
                        slot.expect_marker = False
                        slot.pending_user_id = ""
                        slot.pending_target_id = ""
                        slot.last_activity_time = time.time()
                    clean = clean_line_response(marked_raw or strip_ansi(raw, sent_texts=slot.sent_texts))
                    if slot.reply_start_marker and clean in {"和", "and"}:
                        continue
                    slot.sent_texts.clear()
                    if not clean.strip():
                        continue
                    prefix = f"[{slot.label}] " if len(self.slots) > 1 else ""
                    msg = prefix + clean.strip()
                    if target_id:
                        if self.config.delivery_mode == "poll":
                            self._enqueue_outbox(target_id, msg)
                        else:
                            self._push_text(target_id, msg)
            except Exception as e:
                _llog(f"line flush loop error: {type(e).__name__}: {e}")
