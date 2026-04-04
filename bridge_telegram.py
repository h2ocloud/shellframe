"""
Telegram Bridge for ShellFrame.
Bridges a TG bot ↔ PTY session. Zero external dependencies (uses urllib).
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from bridge_base import BridgeBase, BridgeConfigBase


ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b\[.*?[@-~]|\r')


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes and carriage returns."""
    return ANSI_RE.sub('', text)


def tg_api(token: str, method: str, data=None) -> dict:
    """Call Telegram Bot API."""
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


class TelegramBridge(BridgeBase):
    """Bridges a Telegram bot to a ShellFrame PTY session."""

    PLATFORM = "telegram"

    def __init__(self, bridge_id: str, config: TelegramBridgeConfig, write_fn, on_status_change=None):
        super().__init__(bridge_id, config, write_fn, on_status_change)
        self.bot_info = {}
        self._thread = None
        self._stop_event = threading.Event()
        self._offset = 0

        # Output capture
        self._output_buf = ""
        self._output_lock = threading.Lock()
        self._last_output_time = 0
        self._flush_thread = None
        self._current_chat_id = None  # last chat that sent a message

    # ── Lifecycle ──

    def start(self):
        """Start the bridge (verify bot + begin polling)."""
        if self.active:
            return

        # Verify bot token
        result = tg_api(self.config.bot_token, "getMe")
        if not result.get("ok"):
            self._emit_status({"state": "error", "message": f"Invalid bot token: {result.get('description', 'unknown')}"})
            return

        self.bot_info = result.get("result", {})
        self.connected = True
        self.active = True
        self.paused = False
        self._stop_event.clear()

        # Send initial prompt to PTY
        if self.config.initial_prompt:
            self.write_fn(self.config.initial_prompt + "\n")

        # Start polling thread
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

        # Start output flush thread
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

        self._emit_status({"state": "connected", "bot": self.bot_info.get("username", "")})

    def stop(self):
        """Stop the bridge."""
        self.active = False
        self._stop_event.set()
        self.connected = False
        self._emit_status({"state": "stopped"})

    def pause(self):
        """Pause bridge (stop forwarding TG → PTY, but keep polling)."""
        self.paused = True
        self._emit_status({"state": "paused", "bot": self.bot_info.get("username", "")})

    def resume(self):
        """Resume bridge."""
        self.paused = False
        self._emit_status({"state": "connected", "bot": self.bot_info.get("username", "")})

    def toggle_pause(self):
        if self.paused:
            self.resume()
        else:
            self.pause()
        return not self.paused  # return new active state

    # ── Output capture (PTY → TG) ──

    def feed_output(self, raw_text: str):
        """Feed PTY output to the bridge for forwarding to TG."""
        if not self.active or not self._current_chat_id:
            return
        with self._output_lock:
            self._output_buf += raw_text
            self._last_output_time = time.time()

    def _flush_loop(self):
        """Flush buffered output to TG after 2s of idle."""
        while self.active and not self._stop_event.is_set():
            time.sleep(0.5)
            with self._output_lock:
                if not self._output_buf:
                    continue
                if time.time() - self._last_output_time < 2.0:
                    continue
                text = self._output_buf
                self._output_buf = ""

            # Clean and send
            clean = strip_ansi(text).strip()
            if not clean or len(clean) < 2:
                continue

            # Truncate very long messages (TG limit ~4096)
            if len(clean) > 4000:
                clean = clean[:4000] + "\n...(truncated)"

            if self._current_chat_id:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": self._current_chat_id,
                    "text": clean,
                })

    # ── TG Polling ──

    def _poll_loop(self):
        """Long-poll Telegram for updates."""
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
        """Process a single TG update."""
        msg = update.get("message")
        if not msg:
            return

        user = msg.get("from", {})
        user_id = user.get("id", 0)
        chat_id = msg.get("chat", {}).get("id", 0)
        text = msg.get("text", "")

        if not text:
            return

        # Check whitelist (empty = allow all)
        if self.config.allowed_users and user_id not in self.config.allowed_users:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": "Access denied. Contact the admin to be added to the allowlist.",
            })
            return

        # Auto-resume if paused and auto_resume is enabled
        if self.paused and self.config.auto_resume_on_message:
            self.paused = False
            self._emit_status({"state": "connected", "bot": self.bot_info.get("username", ""), "auto_resumed": True})

        # Track current chat for responses
        self._current_chat_id = chat_id

        # Skip if paused (and auto_resume is disabled)
        if self.paused:
            return

        # Format and forward to PTY
        username = user.get("username") or user.get("first_name", "user")
        if self.config.prefix_enabled:
            forwarded = f"[TG @{username}]: {text}"
        else:
            forwarded = text

        self.write_fn(forwarded + "\n")

    # ── Status ──

    def get_status(self) -> dict:
        return {
            "bridge_id": self.bridge_id,
            "state": "paused" if self.paused else ("connected" if self.connected else "stopped"),
            "bot": self.bot_info.get("username", ""),
            "bot_name": self.bot_info.get("first_name", ""),
            "paused": self.paused,
            "active": self.active,
        }

    def _emit_status(self, status: dict):
        if self.on_status_change:
            try:
                self.on_status_change(self.bridge_id, status)
            except:
                pass
