"""
Telegram Bridge for ShellFrame.
Routes one TG bot across multiple PTY sessions with slash-command switching.
Zero external dependencies (uses urllib).
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from bridge_base import BridgeBase, BridgeConfigBase


ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b\[.*?[@-~]|\r')


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub('', text)


def tg_api(token: str, method: str, data=None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if data:
        payload = json.dumps(data).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"ok": False, "description": f"HTTP {e.code}: {body[:200]}"}
    except Exception as e:
        return {"ok": False, "description": str(e)}


@dataclass
class TelegramBridgeConfig(BridgeConfigBase):
    bot_token: str = ""
    initial_prompt: str = (
        "You are now receiving messages bridged from Telegram. "
        "Keep responses concise and mobile-friendly. "
        "Messages from Telegram will be prefixed with [TG username]."
    )


class SessionSlot:
    """One session registered with the bridge."""

    def __init__(self, sid: str, label: str, write_fn, index: int):
        self.sid = sid
        self.label = label
        self.write_fn = write_fn
        self.index = index  # 1-based display index
        self.output_buf = ""
        self.output_lock = threading.Lock()
        self.last_output_time = 0


class TelegramBridge(BridgeBase):
    """
    Multi-session Telegram bridge.
    One bot manages all sessions. Users switch with slash commands.
    """

    PLATFORM = "telegram"

    def __init__(self, bridge_id: str, config: TelegramBridgeConfig, on_status_change=None):
        # write_fn not used directly — each session slot has its own
        super().__init__(bridge_id, config, write_fn=None, on_status_change=on_status_change)
        self.bot_info = {}
        self._thread = None
        self._stop_event = threading.Event()
        self._offset = 0
        self._flush_thread = None

        # Multi-session state
        self.slots = {}            # sid -> SessionSlot
        self._slot_order = []      # ordered list of sids
        self._user_active = {}     # user_id -> sid (current session per user)
        self._user_chat = {}       # user_id -> chat_id
        self._slots_lock = threading.Lock()

    # ── Session management ──

    def register_session(self, sid: str, label: str, write_fn):
        """Register a session tab with the bridge."""
        with self._slots_lock:
            if sid in self.slots:
                self.slots[sid].label = label
                self.slots[sid].write_fn = write_fn
                return
            idx = len(self._slot_order) + 1
            self.slots[sid] = SessionSlot(sid, label, write_fn, idx)
            self._slot_order.append(sid)

    def unregister_session(self, sid: str):
        """Remove a session from the bridge."""
        with self._slots_lock:
            self.slots.pop(sid, None)
            if sid in self._slot_order:
                self._slot_order.remove(sid)
            # Reindex
            for i, s in enumerate(self._slot_order):
                self.slots[s].index = i + 1
            # Update users pointing to this session
            for uid, active_sid in list(self._user_active.items()):
                if active_sid == sid:
                    # Switch to first available
                    if self._slot_order:
                        self._user_active[uid] = self._slot_order[0]
                    else:
                        del self._user_active[uid]

    def get_active_sid(self, user_id: int) -> str:
        """Get the active session for a user. Defaults to first slot."""
        sid = self._user_active.get(user_id)
        if sid and sid in self.slots:
            return sid
        if self._slot_order:
            return self._slot_order[0]
        return ""

    # ── Lifecycle ──

    def start(self):
        if self.active:
            return

        result = tg_api(self.config.bot_token, "getMe")
        if not result.get("ok"):
            self._emit_status({"state": "error", "message": f"Invalid bot token: {result.get('description', 'unknown')}"})
            return

        self.bot_info = result.get("result", {})
        self.connected = True
        self.active = True
        self.paused = False
        self._stop_event.clear()

        # Register slash commands with BotFather-style menu
        self._set_bot_commands()

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

        self._emit_status({"state": "connected", "bot": self.bot_info.get("username", "")})

    def stop(self):
        self.active = False
        self._stop_event.set()
        self.connected = False
        self._emit_status({"state": "stopped"})

    def _set_bot_commands(self):
        """Register slash commands with Telegram."""
        commands = [
            {"command": "list", "description": "List all sessions"},
            {"command": "status", "description": "Show current session & bridge status"},
            {"command": "pause", "description": "Pause bridge (stop forwarding)"},
            {"command": "resume", "description": "Resume bridge"},
        ]
        # Add numbered commands for quick switching
        with self._slots_lock:
            for sid in self._slot_order:
                slot = self.slots[sid]
                commands.append({
                    "command": str(slot.index),
                    "description": f"Switch to {slot.label}",
                })
        tg_api(self.config.bot_token, "setMyCommands", {"commands": commands[:30]})

    def refresh_commands(self):
        """Re-register commands after sessions change."""
        if self.active:
            self._set_bot_commands()

    # ── Output capture (PTY → TG) ──

    def feed_output(self, sid: str, raw_text: str):
        """Feed PTY output from a specific session."""
        if not self.active:
            return
        slot = self.slots.get(sid)
        if not slot:
            return
        with slot.output_lock:
            slot.output_buf += raw_text
            slot.last_output_time = time.time()

    def _flush_loop(self):
        """Flush buffered output from all sessions to TG."""
        while self.active and not self._stop_event.is_set():
            time.sleep(0.5)
            with self._slots_lock:
                sids = list(self._slot_order)

            for sid in sids:
                slot = self.slots.get(sid)
                if not slot:
                    continue

                with slot.output_lock:
                    if not slot.output_buf:
                        continue
                    if time.time() - slot.last_output_time < 2.0:
                        continue
                    text = slot.output_buf
                    slot.output_buf = ""

                clean = strip_ansi(text).strip()
                if not clean or len(clean) < 2:
                    continue

                # Tag with session label
                prefix = f"[{slot.label}] " if len(self.slots) > 1 else ""
                msg = prefix + clean

                if len(msg) > 4000:
                    msg = msg[:4000] + "\n...(truncated)"

                # Send to all users who have this as active session
                sent_to = set()
                for uid, active_sid in list(self._user_active.items()):
                    if active_sid == sid and uid in self._user_chat:
                        chat_id = self._user_chat[uid]
                        if chat_id not in sent_to:
                            tg_api(self.config.bot_token, "sendMessage", {
                                "chat_id": chat_id,
                                "text": msg,
                            })
                            sent_to.add(chat_id)

                # Also send to users with no explicit selection if this is first slot
                if sid == (self._slot_order[0] if self._slot_order else ""):
                    for uid, chat_id in self._user_chat.items():
                        if uid not in self._user_active and chat_id not in sent_to:
                            tg_api(self.config.bot_token, "sendMessage", {
                                "chat_id": chat_id,
                                "text": msg,
                            })

    # ── TG Polling ──

    def _poll_loop(self):
        while self.active and not self._stop_event.is_set():
            try:
                result = tg_api(self.config.bot_token, "getUpdates", {
                    "offset": self._offset,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                })
                if not result.get("ok"):
                    time.sleep(5)
                    continue
                for update in result.get("result", []):
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception:
                time.sleep(5)

    def _handle_update(self, update: dict):
        msg = update.get("message")
        if not msg:
            return

        user = msg.get("from", {})
        user_id = user.get("id", 0)
        chat_id = msg.get("chat", {}).get("id", 0)
        text = msg.get("text", "")
        if not text:
            return

        # Track chat
        self._user_chat[user_id] = chat_id

        # Whitelist check
        if self.config.allowed_users and user_id not in self.config.allowed_users:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": "Access denied.",
            })
            return

        # Auto-resume on message
        if self.paused and self.config.auto_resume_on_message:
            self.paused = False
            self._emit_status({"state": "connected", "bot": self.bot_info.get("username", ""), "auto_resumed": True})

        # ── Slash commands ──
        if text.startswith("/"):
            cmd = text.split()[0][1:].split("@")[0].lower()  # strip /cmd@botname
            self._handle_command(cmd, user_id, chat_id)
            return

        # Skip if paused
        if self.paused:
            return

        # ── Forward message to active session ──
        active_sid = self.get_active_sid(user_id)
        if not active_sid or active_sid not in self.slots:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": "No active session. Use /list to see available sessions.",
            })
            return

        slot = self.slots[active_sid]
        username = user.get("username") or user.get("first_name", "user")

        if self.config.prefix_enabled:
            forwarded = f"[TG @{username}]: {text}"
        else:
            forwarded = text

        slot.write_fn(forwarded + "\r")

    def _handle_command(self, cmd: str, user_id: int, chat_id: int):
        """Handle slash commands."""

        if cmd == "list":
            lines = ["📋 Sessions:\n"]
            active_sid = self.get_active_sid(user_id)
            with self._slots_lock:
                for sid in self._slot_order:
                    slot = self.slots[sid]
                    marker = " ◀" if sid == active_sid else ""
                    lines.append(f"  /{slot.index}  {slot.label}{marker}")
            if not self._slot_order:
                lines.append("  (no sessions)")
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": "\n".join(lines),
            })

        elif cmd == "status":
            active_sid = self.get_active_sid(user_id)
            slot = self.slots.get(active_sid)
            state = "paused ⏸" if self.paused else "connected ●"
            label = slot.label if slot else "none"
            total = len(self.slots)
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"Bridge: {state}\nBot: @{self.bot_info.get('username', '?')}\nActive: {label}\nSessions: {total}",
            })

        elif cmd == "pause":
            self.pause()
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": "Bridge paused ⏸",
            })

        elif cmd == "resume":
            self.resume()
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": "Bridge resumed ●",
            })

        elif cmd.isdigit():
            idx = int(cmd)
            with self._slots_lock:
                if 1 <= idx <= len(self._slot_order):
                    sid = self._slot_order[idx - 1]
                    self._user_active[user_id] = sid
                    slot = self.slots[sid]
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"Switched to {slot.label} (/{slot.index})",
                    })
                else:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"Invalid session number. Use /list to see available sessions.",
                    })

        elif cmd == "start":
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"ShellFrame Bridge\n\n/list — list sessions\n/1, /2, ... — switch session\n/pause — pause bridge\n/resume — resume\n/status — show status",
            })

        else:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"Unknown command /{cmd}. Use /list to see sessions.",
            })

    # ── Status ──

    def get_status(self) -> dict:
        return {
            "bridge_id": self.bridge_id,
            "state": "paused" if self.paused else ("connected" if self.connected else "stopped"),
            "bot": self.bot_info.get("username", ""),
            "bot_name": self.bot_info.get("first_name", ""),
            "paused": self.paused,
            "active": self.active,
            "sessions": len(self.slots),
        }
