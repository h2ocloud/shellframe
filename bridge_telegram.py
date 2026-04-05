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

import pyte

from bridge_base import BridgeBase, BridgeConfigBase


# ── Dynamic filter system ──
# Loads rules from filters.json (local or remote), falls back to hardcoded defaults.

import os as _os
from pathlib import Path as _Path

_FILTERS_FILE = _Path(__file__).parent / "filters.json"
_FILTERS_URL = "https://raw.githubusercontent.com/h2ocloud/shellframe/main/filters.json"
_filters_cache = None


def _load_filters():
    """Load filter rules from local file, fetch remote if newer."""
    global _filters_cache
    if _filters_cache:
        return _filters_cache

    # Try local file
    try:
        with open(_FILTERS_FILE) as f:
            _filters_cache = json.load(f)
    except:
        _filters_cache = {}

    # Background: fetch remote and update local if version is newer
    def _fetch_remote():
        global _filters_cache
        try:
            req = urllib.request.Request(_FILTERS_URL, headers={"User-Agent": "shellframe"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote = json.loads(resp.read().decode())
            if remote.get("version", 0) > _filters_cache.get("version", 0):
                _filters_cache = remote
                with open(_FILTERS_FILE, 'w') as f:
                    json.dump(remote, f, indent=2, ensure_ascii=False)
        except:
            pass
    threading.Thread(target=_fetch_remote, daemon=True).start()

    return _filters_cache


def _build_regex():
    """Build compiled regexes from filter rules."""
    f = _load_filters()

    spinner_chars = f.get("spinner_chars", "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠛⠿✢✳✶✻✽·⏺⏵▐▛▜▝▘█")
    loading_words = f.get("loading_words", ["Channelling", "Undulating", "Gitifying", "Thinking", "Initializing"])
    box_chars = f.get("box_drawing_chars", "╭╮╰╯│─┌┐└┘┤├┬┴┼═║╔╗╚╝╠╣╦╩╬")
    mcp_pats = f.get("mcp_patterns", ["plugin:.*MCP", "MCP server failed", "reply failed", "allowlisted"])
    status_pats = f.get("status_bar_patterns", [])
    osc_pats = f.get("osc_cleanup_patterns", [])

    return {
        "ansi": re.compile(
            r'\x1b\[[\d;?]*[A-Za-z~]'
            r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'
            r'|\x1b[()][A-Z0-9]'
            r'|\x1b[78=>NOMDEHc]'
            r'|\r|\x07|\x08'
            r'|\[[\??\d;]+[A-Za-z]'
        , re.DOTALL),
        "spinner": re.compile(f'[{re.escape(spinner_chars)}]+'),
        "loading": re.compile(
            '(?:' + '|'.join(re.escape(w) for w in loading_words) + r')(?:…|\.\.\.)?'
            r'|[A-Z]\w{2,}(?:ing|ling|ting|ning|ring)(?:…|\.\.\.)'  # catch any Xxxing… (incl. accented chars)
        ),
        "tui": re.compile(f'[{re.escape(box_chars)}]+'),
        "mcp": re.compile('|'.join(mcp_pats)),
        "status": re.compile('|'.join(status_pats), re.MULTILINE) if status_pats else None,
        "osc": [re.compile(p) for p in osc_pats],
        "echo_keywords": f.get("echo_keywords", []),
        "skip_chars": set(f.get("skip_line_chars", "›•\\/⎿M")),
        "decoration_chars": set(f.get("decoration_chars", "─━═│║╭╮╰╯┌┐└┘ |-_")),
    }


_compiled = None

def _get_compiled():
    global _compiled
    if not _compiled:
        _compiled = _build_regex()
    return _compiled


def reload_filters():
    """Force reload filters from disk/remote."""
    global _filters_cache, _compiled
    _filters_cache = None
    _compiled = None
    _load_filters()


def strip_ansi(text, sent_texts=None):
    """Extract AI response from terminal output.

    Strategy:
    1. Try marker extraction (>>> response <<<) - most reliable
    2. Fallback: regex strip + keyword filter
    """
    c = _get_compiled()

    # Strip ANSI first
    clean = c["ansi"].sub('', text)
    clean = c["spinner"].sub('', clean)

    # Strategy 1: Marker extraction (>>> ... <<<)
    marker_match = re.search(r'>>>\s*(.*?)\s*<<<', clean, re.DOTALL)
    if marker_match:
        return marker_match.group(1).strip()

    # Strategy 2: Fallback regex cleaning
    clean = c["loading"].sub('', clean)
    clean = c["tui"].sub('', clean)
    clean = c["mcp"].sub('', clean)
    if c["status"]:
        clean = c["status"].sub('', clean)
    for osc_re in c["osc"]:
        clean = osc_re.sub('', clean)
    # Remove thinking indicators
    clean = re.sub(r'\(thinking\)', '', clean)
    clean = re.sub(r'\(thought for \d+s?\)', '', clean)
    clean = re.sub(r'[•›]\s*$', '', clean, flags=re.MULTILINE)

    lines = []
    for l in clean.split('\n'):
        stripped = l.strip()
        if not stripped or stripped in c["skip_chars"] or len(stripped) <= 1:
            continue
        if len(stripped) > 2 and all(ch in c["decoration_chars"] for ch in stripped):
            continue
        if stripped.startswith('› '):
            stripped = stripped[2:]
        elif stripped.startswith('• '):
            stripped = stripped[2:]
        elif stripped.startswith('\u23fa '):  # ⏺
            stripped = stripped[2:]

        if stripped.startswith('[TG @') or stripped.startswith('[TG@'):
            continue
        lower = stripped.lower()
        lower_nospace = lower.replace(' ', '')
        if any(kw in lower or kw.replace(' ', '') in lower_nospace for kw in c["echo_keywords"]):
            continue
        if sent_texts:
            is_echo = False
            for sent in sent_texts:
                norm_line = stripped.replace(' ', '').lower()
                norm_sent = sent.replace(' ', '').lower()
                if len(norm_line) > 3 and (norm_line in norm_sent or norm_sent[:25] in norm_line):
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
        self.index = index
        self.output_lock = threading.Lock()
        self.last_output_time = 0
        self.first_output_time = 0
        self.sent_texts = []
        self.has_user_msg = False
        # Virtual terminal for screen-based text extraction
        self.screen = pyte.Screen(200, 50)
        self.stream = pyte.Stream(self.screen)
        self.prev_screen = [""] * 50  # previous screen snapshot
        self.sent_responses = {"Understood.", "Understood"}  # pre-filter system acks


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
        """Feed PTY output through virtual terminal for screen-based extraction."""
        if not self.active:
            return
        slot = self.slots.get(sid)
        if not slot:
            return
        with slot.output_lock:
            was_empty = slot.last_output_time == 0
            # Feed into pyte virtual terminal
            try:
                slot.stream.feed(raw_text)
            except Exception:
                pass
            slot.last_output_time = time.time()
            if was_empty or slot.first_output_time == 0:
                slot.first_output_time = time.time()
        if was_empty:
            threading.Thread(target=self._send_typing, args=(sid,), daemon=True).start()

    # AI response markers used by CLI tools
    AI_MARKERS = ('• ', '⏺ ', '⏺')

    # Responses that are system-prompt acks or tool-use status, not real replies
    _FILTERED_RESPONSES = {"Understood.", "Understood"}
    _FILTERED_PREFIXES = (
        "Searching the web", "Searched ", "Reading ", "Writing ",
        "Editing ", "Running ", "Working (", "Calling ",
        "Creating ", "Deleting ", "Updating ", "Fetching ",
        "Analyzing ", "Scanning ", "Checking ", "Installing ",
        "Building ", "Compiling ", "Downloading ", "Uploading ",
    )

    def _extract_new_text(self, slot):
        """Scan screen for AI responses not yet sent.

        Logic:
        1. Find a line starting with AI_MARKERS (• / ⏺) = start of response block
        2. Collect ALL subsequent lines (including empty) until hitting a
           prompt marker (› / ❯) or another AI marker (next response)
        3. Join collected lines as one response; skip if already in sent_responses
        """
        # Collect response blocks: list of list-of-lines
        blocks = []
        current_block = None

        for line in slot.screen.display:
            stripped = line.rstrip().strip()
            raw_lstripped = line.lstrip()

            # Check for prompt markers — ends current block
            if stripped.startswith(('› ', '❯ ', '›', '❯')):
                if current_block is not None:
                    blocks.append(current_block)
                    current_block = None
                continue

            # Check for AI response marker — starts a new block
            marker_hit = False
            for marker in self.AI_MARKERS:
                if stripped.startswith(marker):
                    # If we were already collecting, save that block first
                    if current_block is not None:
                        blocks.append(current_block)
                    current_block = [stripped[len(marker):].strip()]
                    marker_hit = True
                    break

            if marker_hit:
                continue

            # If we're inside a response block, collect the line (even if empty)
            if current_block is not None:
                current_block.append(stripped)

        # Don't forget the last block
        if current_block is not None:
            blocks.append(current_block)

        new_texts = []
        for block_lines in blocks:
            # Strip trailing empty lines
            while block_lines and not block_lines[-1]:
                block_lines.pop()
            # Strip leading empty lines
            while block_lines and not block_lines[0]:
                block_lines.pop(0)

            if not block_lines:
                continue

            # Remove decoration lines within block
            block_lines = [l for l in block_lines if not (l and all(c in '─━═│║╭╮╰╯┌┐└┘ |-_' for c in l))]
            # Re-trim
            while block_lines and not block_lines[-1]:
                block_lines.pop()
            while block_lines and not block_lines[0]:
                block_lines.pop(0)
            if not block_lines:
                continue

            text = '\n'.join(block_lines)

            # Skip filtered responses (system acks, tool-use status)
            first_line = block_lines[0].strip() if block_lines else ""
            if text.strip() in self._FILTERED_RESPONSES:
                slot.sent_responses.add(text)
                continue
            if any(first_line.startswith(p) for p in self._FILTERED_PREFIXES):
                slot.sent_responses.add(text)
                continue

            # Skip if already sent (use full block text as key)
            if text in slot.sent_responses:
                continue

            # Skip echo of sent text
            is_echo = False
            nr = text.replace(' ', '').replace('\n', '').lower()
            for sent in slot.sent_texts:
                ns = sent.replace(' ', '').lower()
                if len(nr) > 3 and (nr in ns or ns[:25] in nr):
                    is_echo = True
                    break
            if is_echo:
                continue

            new_texts.append(text)

        # Mark as sent
        for text in new_texts:
            slot.sent_responses.add(text)
        # Keep sent_responses from growing forever (last 200)
        if len(slot.sent_responses) > 200:
            slot.sent_responses = set(list(slot.sent_responses)[-100:])

        return new_texts

    def _flush_loop(self):
        """Extract new text from virtual terminal and send to TG."""
        while self.active and not self._stop_event.is_set():
            time.sleep(0.5)
            with self._slots_lock:
                sids = list(self._slot_order)

            for sid in sids:
                slot = self.slots.get(sid)
                if not slot:
                    continue

                with slot.output_lock:
                    if slot.last_output_time == 0:
                        continue
                    if not slot.has_user_msg:
                        # Reset screen snapshot (discard pre-message content)
                        slot.prev_screen = [line.rstrip() for line in slot.screen.display]
                        continue
                    now = time.time()
                    idle = now - slot.last_output_time
                    total = now - slot.first_output_time
                    if idle < 3.0 and total < 15.0:
                        self._send_typing(sid)
                        continue

                    # Extract new text via screen diff (only final changes)
                    new_lines = self._extract_new_text(slot)
                    slot.sent_texts.clear()
                    slot.last_output_time = 0
                    slot.first_output_time = 0

                # Debug log
                with open('/tmp/shellframe_bridge.log', 'a') as f:
                    f.write(f"flush {sid}: new_lines={len(new_lines)} "
                            f"users={dict(self._user_active)} has_msg={slot.has_user_msg}\n")
                    for l in new_lines[:5]:
                        f.write(f"  [{l}]\n")

                if not new_lines:
                    continue

                clean = '\n'.join(new_lines)

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
        # Ensure user is tracked in _user_active (so flush/typing can find them)
        if active_sid and user_id not in self._user_active:
            self._user_active[user_id] = active_sid
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

        # Mark that this session has received a real user message
        # Clear any pre-existing buffer (system prompt responses, etc.)
        if not slot.has_user_msg:
            with slot.output_lock:
                slot.output_buf = ""
        slot.has_user_msg = True
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
