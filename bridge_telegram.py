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


# Aggressive terminal escape stripping
ANSI_RE = re.compile(
    r'\x1b\[[\d;?]*[A-Za-z~]'  # CSI sequences incl. DEC private mode (?25h, ?2026h, etc.)
    r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'  # OSC sequences (title bar)
    r'|\x1b[()][A-Z0-9]'       # charset
    r'|\x1b[78=>NOMDEHc]'       # single-char escapes
    r'|\r'                      # carriage return
    r'|\x07'                    # bell
    r'|\x08'                    # backspace
    r'|\[[\??\d;]+[A-Za-z]'     # bare CSI without ESC (sometimes leaked as raw text)
, re.DOTALL)

# Spinner chars (braille + star variants + Claude Code animations)
SPINNER_RE = re.compile(r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠛⠿✢✳✶✻✽·⏺⏵▐▛▜▝▘█]+')
# Loading animation words
LOADING_RE = re.compile(r'(?:Channelling|Undulating|Gitifying|Thinking|Initializing)(?:…|\.\.\.)?')
# Box drawing and TUI chrome
TUI_RE = re.compile(r'[╭╮╰╯│─┌┐└┘┤├┬┴┼═║╔╗╚╝╠╣╦╩╬]+')
# MCP / plugin error lines
MCP_RE = re.compile(r'plugin:.*MCP|MCP server failed|reply failed|allowlisted|/telegram:access|sendChatAction')

# Status bar patterns (Claude Code, Codex, etc.)
STATUS_RE = re.compile(
    r'›\s*(?:Write tests|Working|Thinking|Reading|Searching|Editing|Running|Use /\w+).*$'
    r'|(?:gpt-[\d.]+|claude-[\w.-]+|sonnet|opus|haiku)\s+\w+\s*·\s*\d+%\s*left.*$'
    r'|•\s*Working\([\ds]+.*?\)'
    r'|\bWor(?:k(?:i(?:n(?:g)?)?)?)?(?=\s|$)'  # partial "Working" fragments
    r'|bypass\s*permissions?\s*on.*$'  # Claude Code permission prompt
    r'|shift\+tab\s*to\s*cycle.*$'
    r'|esc\s*to\s*interrupt.*$'
    r'|Use /\w+ to .*$'  # CLI hints like "Use /skills to list..."
    r'|Tip:.*$'  # Codex tips
    r'|\d+%\s*left\s*·\s*/'
    r'|esc to interrupt'
, re.MULTILINE)


def strip_ansi(text, sent_texts=None):
    """Remove ANSI escapes, spinners, status bar noise, and echo of sent text."""
    text = ANSI_RE.sub('', text)
    text = SPINNER_RE.sub('', text)
    text = LOADING_RE.sub('', text)
    text = TUI_RE.sub('', text)
    text = MCP_RE.sub('', text)
    text = STATUS_RE.sub('', text)
    text = re.sub(r'\][\d;]+[^\n]*?\\', '', text)
    text = re.sub(r'\[[\d;?]+[^\n]*?\\', '', text)
    text = re.sub(r'[•›]\s*$', '', text, flags=re.MULTILINE)

    lines = []
    for l in text.split('\n'):
        stripped = l.strip()
        if not stripped or stripped in ('›', '•', '\\', 'M', '/', '⎿'):
            continue
        # Skip decoration-only lines (dashes, boxes, etc.)
        if len(stripped) > 2 and all(c in '─━═│║╭╮╰╯┌┐└┘ |-_' for c in stripped):
            continue
        if stripped.startswith('› '):
            stripped = stripped[2:]
        elif stripped.startswith('• '):
            stripped = stripped[2:]

        # Filter echo of user messages (both old and new format)
        if stripped.startswith('[TG @') or stripped.startswith('[TG@'):
            continue
        # Filter system prompt acknowledgments + Claude Code noise
        if any(kw in stripped.lower() for kw in [
            'keep replies concise', 'mobile-friendly', 'sender prefix', 'treat [tg',
            'reply directly', 'do not use any telegram', 'shellframe bridge',
            'not use any telegram tools', 'respond normally', 'never use mcp',
            'telegram channel isn\'t set up', '/telegram:access',
            'allowlisted', 'reply failed', 'mcp server failed',
            'welcome back', 'run /init', 'recent activity', 'no recent activity',
            'tips for getting started', 'claude code v',
            'claude max', 'organization', '/mcp',
            'i\'m ready and listening', 'ready and listening for messages',
            'plain text in this terminal',
        ]):
            continue
        if sent_texts:
            is_echo = False
            for sent in sent_texts:
                # Fuzzy match: normalize whitespace and check substring
                norm_line = ' '.join(stripped.split())
                norm_sent = ' '.join(sent.split())
                if len(norm_line) > 5 and (norm_line in norm_sent or norm_sent[:30] in norm_line):
                    is_echo = True
                    break
            if is_echo:
                continue

        lines.append(stripped)
    return '\n'.join(lines)


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
        "IMPORTANT RULES: "
        "1. Messages from remote users will appear as 'username: message'. "
        "2. Reply ONLY as plain text in this terminal. NEVER use MCP tools, plugins, or any telegram/reply tools. "
        "3. Keep responses concise. "
        "4. If you see a message like 'Howard: hello', just reply directly with text."
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
        self.sent_texts = []  # track what we sent to PTY (for echo filtering)


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

        # Notify allowed users that bridge is connected
        sessions_info = ', '.join(self.slots[s].label for s in self._slot_order) if self._slot_order else 'none'
        connect_msg = f"🔗 ShellFrame Bridge connected\nBot: @{self.bot_info.get('username', '?')}\nSessions: {sessions_info}\n\n/list to see sessions, /1 /2 to switch"
        for uid in (self.config.allowed_users or []):
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": uid,
                "text": connect_msg,
            })

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

    def _send_typing(self, sid: str):
        """Send typing indicator to users watching this session."""
        for uid, active_sid in list(self._user_active.items()):
            if active_sid == sid and uid in self._user_chat:
                tg_api(self.config.bot_token, "sendChatAction", {
                    "chat_id": self._user_chat[uid],
                    "action": "typing",
                })

    def feed_output(self, sid: str, raw_text: str):
        """Feed PTY output from a specific session."""
        if not self.active:
            return
        slot = self.slots.get(sid)
        if not slot:
            return
        with slot.output_lock:
            was_empty = not slot.output_buf
            slot.output_buf += raw_text
            slot.last_output_time = time.time()
        # Send typing on first output chunk
        if was_empty:
            threading.Thread(target=self._send_typing, args=(sid,), daemon=True).start()

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
                    if time.time() - slot.last_output_time < 4.0:
                        # Still receiving output — refresh typing indicator
                        self._send_typing(sid)
                        continue
                    text = slot.output_buf
                    slot.output_buf = ""

                clean = strip_ansi(text, sent_texts=slot.sent_texts).strip()
                # Clear sent_texts after filtering
                slot.sent_texts.clear()
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
            forwarded = f"{username}: {text}"
        else:
            forwarded = text

        # Track what we send so we can filter echo from output
        slot.sent_texts.append(forwarded)
        # Keep only last 10 sent texts
        if len(slot.sent_texts) > 10:
            slot.sent_texts = slot.sent_texts[-10:]

        # Write text first, then Enter after a brief delay
        def _send():
            slot.write_fn(forwarded)
            time.sleep(0.3)
            slot.write_fn("\r")
        threading.Thread(target=_send, daemon=True).start()

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
                    # Send system prompt to new session so AI knows context
                    if self.config.initial_prompt:
                        slot.sent_texts.append(self.config.initial_prompt)
                        def _send_prompt(s=slot, text=self.config.initial_prompt):
                            s.write_fn(text)
                            time.sleep(0.3)
                            s.write_fn("\r")
                        threading.Thread(target=_send_prompt, daemon=True).start()
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
