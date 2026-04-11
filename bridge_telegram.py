"""
Telegram Bridge for ShellFrame.
Routes one TG bot across multiple PTY sessions with slash-command switching.
Zero external dependencies (uses urllib).
"""

import json
import re
import shutil
import subprocess
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


_INIT_PROMPT_FILE = _Path(__file__).parent / "INIT_PROMPT.md"


def load_init_prompt() -> str:
    """Load initial prompt from INIT_PROMPT.md. Always reads fresh from disk."""
    try:
        return _INIT_PROMPT_FILE.read_text().strip()
    except Exception:
        return (
            "You are running inside ShellFrame with a Telegram bridge. "
            "User messages appear as 'username: message'. Reply as plain text only. "
            "Run `sfctl reload` after modifying bridge code. "
            "Acknowledge briefly and wait for the user's first message."
        )


@dataclass
class TelegramBridgeConfig(BridgeConfigBase):
    bot_token: str = ""
    initial_prompt: str = ""
    stt_backend: str = "auto"   # auto / plugin / local / remote / off

    def __post_init__(self):
        if not self.initial_prompt:
            self.initial_prompt = load_init_prompt()


class SessionSlot:
    """One session registered with the bridge."""

    def __init__(self, sid: str, label: str, write_fn, index: int, peek_fn=None):
        self.sid = sid
        self.label = label
        self.write_fn = write_fn
        self.peek_fn = peek_fn  # returns recent PTY bytes (last ~1KB ring buffer)
        self.index = index
        self.output_lock = threading.Lock()
        self.last_output_time = 0
        self.first_output_time = 0
        self.sent_texts = []
        self.has_user_msg = False
        self.pending_menu = False  # True if last extract found a menu prompt
        self.awaiting_response = False  # True between user msg and first AI response extraction
        self.last_extraction_ts = 0.0   # time of last successful response extraction
        # Stall detection: warn when we wrote to the session but got no
        # meaningful output for ~15s. Common cause: macOS TCC permission
        # dialog blocking the CLI in the background.
        self.last_write_ts = 0.0        # time of last TG → PTY write
        self.last_chunk_ts = 0.0        # time of last PTY chunk (NOT reset by extraction)
        self.stall_warned = False
        # Virtual terminal for screen-based text extraction
        # Use HistoryScreen to keep scrollback — 50-line screen loses long responses
        self.screen = pyte.HistoryScreen(200, 50, history=10000)
        self.stream = pyte.Stream(self.screen)
        self._history_offset = 0  # tracks processed history lines
        self.sent_responses = {"Understood.", "Understood"}  # pre-filter system acks


class TelegramBridge(BridgeBase):
    """
    Multi-session Telegram bridge.
    One bot manages all sessions. Users switch with slash commands.
    """

    PLATFORM = "telegram"

    def __init__(self, bridge_id: str, config: TelegramBridgeConfig, on_status_change=None, on_reload=None, on_close_session=None, on_restart=None, on_check_update=None):
        # write_fn not used directly — each session slot has its own
        super().__init__(bridge_id, config, write_fn=None, on_status_change=on_status_change)
        self.bot_info = {}
        self._thread = None
        self._stop_event = threading.Event()
        self._on_reload = on_reload  # callback for hot-reload from TG
        self._on_close_session = on_close_session  # callback(sid) to close a session
        self._on_restart = on_restart  # callback for full app restart from TG
        self._on_check_update = on_check_update  # callback for update check from TG
        self._offset = 0
        self._flush_thread = None

        # Multi-session state
        self.slots = {}            # sid -> SessionSlot
        self._slot_order = []      # ordered list of sids
        self._user_active = {}     # user_id -> sid (current session per user)
        self._user_chat = {}       # user_id -> chat_id
        self._slots_lock = threading.Lock()

    # ── IPC with main.py (via sfctl file mechanism) ──

    _CMD_FILE = "/tmp/shellframe_cmd.json"
    _RESULT_FILE = "/tmp/shellframe_result.json"

    def _sfctl_call(self, cmd: str, args: dict = None, timeout: float = 5.0) -> dict:
        """Send a command to main.py via sfctl IPC and wait for result."""
        import os as _os
        # Clean stale result
        try:
            _os.unlink(self._RESULT_FILE)
        except OSError:
            pass
        # Write command
        with open(self._CMD_FILE, 'w') as f:
            json.dump({"cmd": cmd, "args": args or {}, "ts": time.time()}, f)
        # Wait for result
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.2)
            if _os.path.exists(self._RESULT_FILE):
                try:
                    with open(self._RESULT_FILE) as f:
                        result = json.load(f)
                    _os.unlink(self._RESULT_FILE)
                    return result
                except (json.JSONDecodeError, IOError):
                    pass
        return {"success": False, "message": "Timeout waiting for main.py"}

    # ── Session management ──

    def register_session(self, sid: str, label: str, write_fn, peek_fn=None):
        """Register a session tab with the bridge."""
        with self._slots_lock:
            if sid in self.slots:
                self.slots[sid].label = label
                self.slots[sid].write_fn = write_fn
                if peek_fn:
                    self.slots[sid].peek_fn = peek_fn
                return
            idx = len(self._slot_order) + 1
            self.slots[sid] = SessionSlot(sid, label, write_fn, idx, peek_fn=peek_fn)
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

    def reorder_slots(self, ordered_sids: list):
        """Reorder session slots to match the given sid list. Reindexes /1, /2, etc."""
        with self._slots_lock:
            # Keep only sids that exist in slots
            new_order = [s for s in ordered_sids if s in self.slots]
            # Append any existing sids not in the new order (safety)
            for s in self._slot_order:
                if s not in new_order and s in self.slots:
                    new_order.append(s)
            self._slot_order = new_order
            for i, s in enumerate(self._slot_order):
                self.slots[s].index = i + 1

    def get_active_sid(self, user_id: int) -> str:
        """Get the active session for a user. Defaults to UI-selected or first slot."""
        sid = self._user_active.get(user_id)
        if sid and sid in self.slots:
            return sid
        default = getattr(self, '_default_active_sid', None)
        if default and default in self.slots:
            return default
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
            {"command": "reload", "description": "Hot-reload bridge code"},
            {"command": "new", "description": "New session (default: claude)"},
            {"command": "close", "description": "Close current session"},
        ]
        # Add numbered commands for quick switching
        with self._slots_lock:
            for sid in self._slot_order:
                slot = self.slots[sid]
                commands.append({
                    "command": str(slot.index),
                    "description": f"Switch to {slot.label}",
                })
        # The claude-plugins-official telegram plugin shares this bot token
        # and continuously overwrites the all_private_chats scope with its
        # own /start /help /status commands. We can't win that race at the
        # same scope level, so we set per-chat scope (botCommandScopeChat)
        # which is the HIGHEST priority for any specific chat. This ensures
        # our commands always show up for allowed users regardless of what
        # the plugin does to all_private_chats.
        cmds = commands[:30]
        tg_api(self.config.bot_token, "setMyCommands", {"commands": cmds})
        for uid in (self.config.allowed_users or []):
            tg_api(self.config.bot_token, "setMyCommands", {
                "commands": cmds,
                "scope": {"type": "chat", "chat_id": uid},
            })
        # Force the chat menu button to be the "commands" list. Without this
        # the TG client on some platforms (esp. iOS) can get stuck showing an
        # empty / stale menu, even when setMyCommands has succeeded.
        tg_api(self.config.bot_token, "setChatMenuButton", {
            "menu_button": {"type": "commands"},
        })

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

    def _warn_stalled(self, sid: str, age_s: int):
        """Notify user that a session hasn't responded — usually a macOS
        permission dialog blocking the CLI in the background."""
        slot = self.slots.get(sid)
        if not slot:
            return
        label = slot.label or sid
        msg = (f"⚠️ [{label}] no reply for ~{age_s}s\n"
               f"Likely a macOS permission popup blocking the CLI in the "
               f"background. Bring shellframe to the front and check.")

        # 1) TG warning to users who have this session active (or any user if
        #    it's the default-active slot)
        target_chats = set()
        for uid, active_sid in list(self._user_active.items()):
            if active_sid == sid and uid in self._user_chat:
                target_chats.add(self._user_chat[uid])
        if not target_chats and self._slot_order and sid == self._slot_order[0]:
            for chat_id in self._user_chat.values():
                target_chats.add(chat_id)
        for chat_id in target_chats:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": msg,
            })

        # 2) Local macOS Notification Center bubble (silent no-op on other OSes)
        try:
            import subprocess as _sp
            note = (f'display notification "Session {label} stalled — '
                    f'check for a permission popup" '
                    f'with title "shellframe" sound name "Ping"')
            _sp.run(["osascript", "-e", note],
                    capture_output=True, timeout=3)
        except (FileNotFoundError, OSError, Exception):
            pass

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
            now_ts = time.time()
            slot.last_output_time = now_ts
            slot.last_chunk_ts = now_ts  # for stall detection (not reset by flush)
            if was_empty or slot.first_output_time == 0:
                slot.first_output_time = now_ts
        if was_empty and slot.awaiting_response:
            threading.Thread(target=self._send_typing, args=(sid,), daemon=True).start()

    # AI response markers used by CLI tools
    AI_MARKERS = ('• ', '⏺ ', '⏺')
    # Prompt markers that signal end of AI response / start of user input
    PROMPT_MARKERS = ('› ', '❯ ', '> ', '›', '❯')

    # Responses that are system-prompt acks, not real replies
    _FILTERED_RESPONSES = {"Understood.", "Understood"}

    # All 48 spinner verbs from Claude Code source (Spinner.tsx)
    _SPINNER_VERBS = {
        'Accomplishing', 'Actioning', 'Actualizing', 'Baking', 'Brewing',
        'Calculating', 'Cerebrating', 'Churning', 'Clauding', 'Coalescing',
        'Cogitating', 'Computing', 'Conjuring', 'Considering', 'Cooking',
        'Crafting', 'Creating', 'Crunching', 'Deliberating', 'Determining',
        'Doing', 'Effecting', 'Finagling', 'Forging', 'Forming',
        'Generating', 'Hatching', 'Herding', 'Honking', 'Hustling',
        'Ideating', 'Inferring', 'Manifesting', 'Marinating', 'Moseying',
        'Mulling', 'Mustering', 'Musing', 'Noodling', 'Percolating',
        'Pondering', 'Processing', 'Puttering', 'Reticulating', 'Ruminating',
        'Schlepping', 'Shucking', 'Simmering', 'Smooshing', 'Spinning',
        'Stewing', 'Synthesizing', 'Thinking', 'Transmuting', 'Vibing',
        'Working',
        # Codex-specific (observed)
        'Channelling', 'Undulating', 'Gitifying', 'Unfurling', 'Sautéing',
    }

    @staticmethod
    def _is_tool_call(text):
        """Detect tool calls: ToolName(params) pattern."""
        # Pattern: starts with capitalized word(s) followed by (
        # e.g., "Web Search(...)", "Fetch(https://...)", "Read(/path/...)"
        if re.match(r'^[A-Z][\w\s]*\(', text):
            return True
        # Codex style: "Searching the web", "Searched xxx"
        if text.startswith(('Searching ', 'Searched ')):
            return True
        # Tool result prefix
        if text.startswith('⎿'):
            return True
        return False

    def _extract_new_text(self, slot):
        """Scan screen + scrollback history for AI responses not yet sent.

        Logic:
        1. Combine scrollback history (lines scrolled off top) + current screen
        2. Find a line starting with AI_MARKERS (• / ⏺) = start of response block
        3. Collect ALL subsequent lines until hitting a prompt marker (› / ❯) or another AI marker
        4. Join collected lines as one response; skip if already in sent_responses
        """
        # Build full line list: unprocessed history + current display
        all_lines = []

        # History lines that scrolled off the top (pyte.HistoryScreen)
        # Each history line is a StaticDefaultDict mapping col -> Char
        history = list(slot.screen.history.top)
        cols = slot.screen.columns
        for hist_line in history[slot._history_offset:]:
            text = "".join(hist_line[col].data for col in range(cols)).rstrip()
            all_lines.append(text)
        slot._history_offset = len(history)

        # Current screen display
        for line in slot.screen.display:
            all_lines.append(line.rstrip())

        # Collect response blocks: list of list-of-lines
        blocks = []
        current_block = None

        for line in all_lines:
            stripped = line.rstrip().strip()
            raw_lstripped = line.lstrip()

            # Skip spinner/status lines (6 spinner chars from Claude Code source)
            if any(stripped.startswith(s) for s in ('✻ ', '✢ ', '✳ ', '∗ ', '✽ ', '· ')):
                continue
            # Skip standalone spinner verb lines (e.g., "Simmering…")
            first_word = stripped.split('…')[0].split('(')[0].split(' ')[0].rstrip('.')
            if first_word in self._SPINNER_VERBS:
                continue

            # Check for prompt markers — ends current block
            # But numbered menu items (› 1. xxx) should be included in the block
            if stripped.startswith(self.PROMPT_MARKERS):
                after_prompt = stripped.lstrip('›❯ ')
                if current_block is not None and re.match(r'\d+\.?\s', after_prompt):
                    # This is a numbered menu item — include in current block
                    current_block.append(after_prompt)
                else:
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

            # Remove decoration lines and tool result lines within block
            block_lines = [l for l in block_lines if not (
                (l and all(c in '─━═│║╭╮╰╯┌┐└┘ |-_' for c in l)) or
                l.strip().startswith('⎿') or
                l.strip().startswith('Sources:') or
                l.strip().startswith('- http')
            )]
            # Re-trim
            while block_lines and not block_lines[-1]:
                block_lines.pop()
            while block_lines and not block_lines[0]:
                block_lines.pop(0)
            if not block_lines:
                continue

            text = '\n'.join(block_lines)

            # Strip AI echo of username prefix (e.g., "Howard: response" → "response")
            # Some AI tools mimic the input prefix format in their responses
            for sent in slot.sent_texts:
                # Extract username prefix pattern from sent text (e.g., "Howard: ")
                m = re.match(r'^(\w+):\s', sent)
                if m:
                    prefix = m.group(0)  # "Howard: "
                    if text.startswith(prefix):
                        text = text[len(prefix):]
                        block_lines[0] = block_lines[0][len(prefix):]
                    break

            # Skip filtered responses (system acks, tool-use status)
            first_line = block_lines[0].strip() if block_lines else ""
            if text.strip() in self._FILTERED_RESPONSES:
                slot.sent_responses.add(text)
                continue
            # Skip tool calls (ToolName(params) pattern)
            if self._is_tool_call(first_line):
                slot.sent_responses.add(text)
                continue

            # Skip if already sent or is a superset of previously sent
            if text in slot.sent_responses:
                continue
            # Check if this is an expanded version of something already sent
            already_sent = False
            for prev in list(slot.sent_responses):
                if prev in text:
                    # This is a longer version — remove old, send new
                    slot.sent_responses.discard(prev)
                    break
                if text in prev:
                    # This is a shorter version of something already sent
                    already_sent = True
                    break
            if already_sent:
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

        # If no normal responses extracted, check for a pending menu prompt
        # (e.g., Claude permission dialog: ❯ 1. Yes / 2. No)
        if not new_texts:
            menu = self._detect_menu_prompt(slot)
            if menu and menu not in slot.sent_responses:
                slot.sent_responses.add(menu)
                slot.pending_menu = True
                new_texts.append(menu)
        else:
            slot.pending_menu = False

        return new_texts

    def _detect_menu_prompt(self, slot) -> str:
        """Detect a numbered menu prompt waiting for user input.
        Returns formatted menu string or empty if none found."""
        # Scan current screen for consecutive "N. xxx" lines (with optional ❯ cursor)
        # Cursor ❯ may be on any line, not just the first
        lines = [l.rstrip() for l in slot.screen.display]
        menu_lines = []
        for line in lines:
            # Strip leading ❯/› cursor markers and whitespace
            stripped = line.lstrip().lstrip('❯›').lstrip()
            # Match "N. xxx" or "N) xxx"
            m = re.match(r'^(\d+)[\.\)]\s+(.+)$', stripped)
            if m:
                menu_lines.append(f"{m.group(1)}. {m.group(2)}")
            elif menu_lines:
                # Hit end markers — stop collecting
                if 'Esc to cancel' in line or 'Tab to' in line or not line.strip():
                    if len(menu_lines) >= 2:
                        break
                # Non-menu line in middle — reset (false positive)
                if line.strip() and not re.search(r'esc|cancel|tab', line, re.I):
                    menu_lines = []
        if len(menu_lines) >= 2:
            return "❓ Choose an option:\n" + "\n".join(menu_lines) + "\n\nReply with the number (1, 2, ...)"
        return ""

    @staticmethod
    def _tmux_capture(sid: str, history_lines: int = 3000) -> str:
        """Capture a tmux pane's rendered scrollback as plain text. Returns ''
        if tmux unavailable or session missing. Uses sf_<sid> naming convention."""
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-p", "-J",
                 "-t", f"sf_{sid}",
                 "-S", f"-{history_lines}"],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                return r.stdout
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            pass
        return ""

    def _peek_last_response(self, slot) -> str:
        """Read-only peek at the last AI response block on screen (no state mutation).

        Strategy:
        1. Prefer tmux capture-pane (battle-tested renderer, handles all TUI cases)
        2. Else scan pyte screen+history for AI marker blocks (• / ⏺)
        3. Fallback: return last ~12 meaningful lines from whichever source
        4. Last resort: read raw PTY ring buffer via peek_fn
        """
        all_lines = []
        # Prefer tmux capture — it gives clean rendered text including scrollback
        captured = self._tmux_capture(slot.sid)
        if captured:
            all_lines = captured.split('\n')
        if not all_lines:
            history = list(slot.screen.history.top)
            cols = slot.screen.columns
            for hist_line in history[-200:]:
                text = "".join(hist_line[col].data for col in range(cols)).rstrip()
                all_lines.append(text)
            for line in slot.screen.display:
                all_lines.append(line.rstrip())

        # Find AI response blocks (same logic as _extract_new_text)
        blocks = []
        current_block = None
        for line in all_lines:
            stripped = line.strip()
            first_word = stripped.split('…')[0].split('(')[0].split(' ')[0].rstrip('.')
            if first_word in self._SPINNER_VERBS:
                continue
            if stripped.startswith(('› ', '❯ ', '›', '❯')):
                if current_block is not None:
                    blocks.append(current_block)
                    current_block = None
                continue

            # Check all AI markers
            marker_hit = False
            for marker in self.AI_MARKERS:
                if stripped.startswith(marker):
                    if current_block is not None:
                        blocks.append(current_block)
                    current_block = [stripped[len(marker):].strip()]
                    marker_hit = True
                    break
            if not marker_hit and current_block is not None:
                current_block.append(stripped)

        if current_block is not None:
            blocks.append(current_block)

        if blocks:
            # Take the last block, clean up
            last = blocks[-1]
            while last and not last[-1]:
                last.pop()
            while last and not last[0]:
                last.pop(0)
            last = [l for l in last if not (
                l and all(c in '─━═│║╭╮╰╯┌┐└┘ |-_' for c in l)
            )]
            text = '\n'.join(last).strip()
            if text and text not in self._FILTERED_RESPONSES and not self._is_tool_call(last[0].strip() if last else ""):
                return text

        # Fallback 1: scan all_lines for any meaningful content (no AI markers found)
        meaningful = self._extract_meaningful_lines(all_lines)
        if meaningful:
            return '\n'.join(meaningful[-12:])

        # Fallback 2: raw PTY buffer (when pyte screen is empty)
        if slot.peek_fn:
            raw = slot.peek_fn()
            if raw:
                clean = strip_ansi(raw)
                if clean.strip():
                    lines = self._extract_meaningful_lines(clean.split('\n'))
                    if lines:
                        return '\n'.join(lines[-12:])
        return ""

    def _extract_meaningful_lines(self, lines):
        """Filter screen lines to keep only meaningful conversation content.
        Drops: empty, spinners, prompts, tool-call status, decoration boxes."""
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Skip pure decoration / box-drawing
            if all(c in '─━═│║╭╮╰╯┌┐└┘ |-_' for c in stripped):
                continue
            # Skip spinner verbs ("Cooking…", "Thinking…")
            first_word = stripped.split('…')[0].split('(')[0].split(' ')[0].rstrip('.')
            if first_word in self._SPINNER_VERBS:
                continue
            # Skip prompt-only lines (› / ❯ alone)
            if stripped in ('›', '❯', '> ', '>'):
                continue
            # Skip Claude Code status bar markers
            if stripped.startswith(('? for shortcuts', 'esc to ', '⏵⏵ accept')):
                continue
            # Drop AI markers (• / ⏺) prefix and › prompt prefix to clean output
            for marker in self.AI_MARKERS:
                if stripped.startswith(marker):
                    stripped = stripped[len(marker):].strip()
                    break
            if stripped.startswith('› '):
                stripped = stripped[2:]
            elif stripped.startswith('❯ '):
                stripped = stripped[2:]
            if not stripped:
                continue
            result.append(stripped)
        return result

    # Stall thresholds
    STALL_WRITE_MIN_AGE = 15.0   # TG msg must be at least this old to consider stalling
    STALL_SILENCE_MIN = 10.0     # PTY must have been silent at least this long

    def _flush_loop(self):
        """Extract new text from virtual terminal and send to TG."""
        while self.active and not self._stop_event.is_set():
            time.sleep(0.5)
            with self._slots_lock:
                sids = list(self._slot_order)

            # Stall detection runs first, outside the output_lock path below,
            # because a truly stalled slot has no output activity to flush.
            now_stall = time.time()
            for sid in sids:
                slot = self.slots.get(sid)
                if not slot or slot.stall_warned or slot.last_write_ts <= 0:
                    continue
                write_age = now_stall - slot.last_write_ts
                silence = now_stall - slot.last_chunk_ts if slot.last_chunk_ts > 0 else write_age
                if write_age > self.STALL_WRITE_MIN_AGE and silence > self.STALL_SILENCE_MIN:
                    slot.stall_warned = True
                    threading.Thread(
                        target=self._warn_stalled,
                        args=(sid, int(write_age)),
                        daemon=True,
                    ).start()

            for sid in sids:
                slot = self.slots.get(sid)
                if not slot:
                    continue

                with slot.output_lock:
                    if slot.last_output_time == 0:
                        continue
                    if not slot.has_user_msg:
                        # Drain old content so it won't be re-extracted later
                        # when a TG message arrives. This advances _history_offset
                        # and marks existing AI blocks as "sent".
                        now = time.time()
                        idle = now - slot.last_output_time
                        if idle >= 1.0:
                            self._extract_new_text(slot)
                            slot.last_output_time = 0
                            slot.first_output_time = 0
                        continue
                    now = time.time()
                    idle = now - slot.last_output_time
                    total = now - slot.first_output_time
                    # Wait for 3s idle OR 120s total before extracting
                    # Claude can take 2+ minutes for long responses
                    if idle < 3.0 and total < 120.0:
                        # Only animate typing while we're actually waiting on a
                        # fresh user message — Claude's TUI status bar refreshes
                        # otherwise keep last_output_time hot forever and the
                        # typing indicator would never stop.
                        if slot.awaiting_response:
                            self._send_typing(sid)
                        continue

                    # Extract new text via screen diff (only final changes)
                    new_lines = self._extract_new_text(slot)
                    slot.sent_texts.clear()
                    slot.last_output_time = 0
                    slot.first_output_time = 0
                    # Response extracted → close the stall-watch window
                    if new_lines:
                        slot.last_write_ts = 0.0
                        slot.stall_warned = False
                        slot.awaiting_response = False  # response delivered, stop typing
                        slot.last_extraction_ts = now
                    # Keep has_user_msg=True so subsequent responses still get
                    # forwarded.  It resets only when a NEW user message arrives
                    # (the _handle_update path sets it fresh each time).

                # Debug log
                with open('/tmp/shellframe_bridge.log', 'a') as f:
                    f.write(f"flush {sid}: new_lines={len(new_lines)} "
                            f"users={dict(self._user_active)} has_msg={slot.has_user_msg}\n")
                    for l in new_lines[:5]:
                        f.write(f"  [{l}]\n")
                    if not new_lines:
                        # Log screen content for debugging
                        screen_lines = [l.rstrip() for l in slot.screen.display if l.rstrip()]
                        f.write(f"  screen({len(screen_lines)}): {[l[:60] for l in screen_lines[-5:]]}\n")

                if not new_lines:
                    continue

                clean = '\n'.join(new_lines)

                # Detect file paths in response for TG file sending
                file_paths = self._extract_file_paths(clean)

                # Tag with session label
                prefix = f"[{slot.label}] " if len(self.slots) > 1 else ""
                msg = prefix + clean

                if len(msg) > 4000:
                    msg = msg[:4000] + "\n...(truncated)"

                # Collect target chat_ids
                target_chats = set()
                for uid, active_sid in list(self._user_active.items()):
                    if active_sid == sid and uid in self._user_chat:
                        target_chats.add(self._user_chat[uid])
                # Also send to users with no explicit selection if this is first slot
                if sid == (self._slot_order[0] if self._slot_order else ""):
                    for uid, chat_id in self._user_chat.items():
                        if uid not in self._user_active:
                            target_chats.add(chat_id)

                for chat_id in target_chats:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": msg,
                    })
                    # Send detected files as documents
                    for fp in file_paths:
                        self._send_tg_file(chat_id, fp)

    # ── File detection & sending ──

    # File extensions worth sending to TG
    _SENDABLE_EXTS = {
        '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.zip', '.tar', '.gz', '.7z', '.rar',
        '.txt', '.csv', '.json', '.xml', '.yaml', '.yml',
        '.mp3', '.mp4', '.wav', '.ogg', '.webm',
        '.py', '.js', '.ts', '.html', '.css', '.md', '.sh',
    }

    _FILE_PATH_RE = re.compile(
        r'(?:^|\s|`)'                        # preceded by whitespace or backtick
        r'((?:/[\w.\-]+)+(?:\.\w{1,10})?'    # absolute path: /foo/bar/baz.ext
        r'|~(?:/[\w.\-]+)+(?:\.\w{1,10})?)'  # or ~/foo/bar.ext
        r'(?=\s|`|$|[)\]},;:])'              # followed by whitespace, backtick, or end
    )

    def _extract_file_paths(self, text: str) -> list:
        """Find real file paths in AI response text that exist on disk."""
        paths = []
        seen = set()
        for m in self._FILE_PATH_RE.finditer(text):
            raw = m.group(1)
            expanded = _os.path.expanduser(raw)
            if expanded in seen:
                continue
            seen.add(expanded)
            if not _os.path.isfile(expanded):
                continue
            ext = _Path(expanded).suffix.lower()
            if ext not in self._SENDABLE_EXTS:
                continue
            # Skip very large files (>50MB TG limit)
            try:
                if _os.path.getsize(expanded) > 50 * 1024 * 1024:
                    continue
            except OSError:
                continue
            paths.append(expanded)
        return paths

    def _send_tg_file(self, chat_id: int, file_path: str):
        """Send a local file to TG chat as document (or photo for images)."""
        import mimetypes
        try:
            fname = _Path(file_path).name
            mime = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
            ext = _Path(file_path).suffix.lower()

            # Use sendPhoto for images, sendDocument for everything else
            is_image = ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')
            method = "sendPhoto" if is_image else "sendDocument"
            field = "photo" if is_image else "document"

            # Multipart upload
            import uuid
            boundary = uuid.uuid4().hex
            with open(file_path, 'rb') as f:
                file_data = f.read()

            body = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
                f'{chat_id}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{field}"; filename="{fname}"\r\n'
                f'Content-Type: {mime}\r\n\r\n'
            ).encode() + file_data + f'\r\n--{boundary}--\r\n'.encode()

            url = f"https://api.telegram.org/bot{self.config.bot_token}/{method}"
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
        except Exception as e:
            with open('/tmp/shellframe_bridge.log', 'a') as f:
                f.write(f"_send_tg_file error: {file_path} -> {e}\n")

    # ── TG Polling ──
    # Offset persistence — survives full app restarts so /restart and
    # /update_now don't re-process themselves on the new instance.
    _OFFSET_FILE = _Path.home() / ".config" / "shellframe" / "tg_offset.json"

    @classmethod
    def _load_offset(cls) -> int:
        try:
            data = json.loads(cls._OFFSET_FILE.read_text())
            return int(data.get("offset", 0))
        except Exception:
            return 0

    def _save_offset(self):
        try:
            self._OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._OFFSET_FILE.write_text(json.dumps({"offset": self._offset}))
        except Exception:
            pass

    def _poll_loop(self):
        # Restore offset from disk on first run (handles full app restart)
        if self._offset == 0:
            self._offset = self._load_offset()
        first_batch = True
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
                updates = result.get("result", [])
                for update in updates:
                    self._offset = update["update_id"] + 1
                    self._save_offset()  # persist BEFORE handling so restart can't re-process
                    # Safety net: on the very first poll batch after startup,
                    # skip self-restart commands. Prevents infinite restart loops
                    # when the previous instance died before saving the offset.
                    if first_batch:
                        msg = update.get("message", {})
                        text = (msg.get("text") or "").strip().lower()
                        cmd = text.split()[0] if text else ""
                        if cmd in ("/restart", "/update_now", "/reload"):
                            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                                _f.write(f"  startup safety: skipping {cmd}\n")
                            continue
                    self._handle_update(update)
                first_batch = False
            except Exception:
                time.sleep(5)

    # ── STT (Speech-to-Text) ──
    # Pluggable provider chain. Two built-in backends:
    #
    #   1. Local: whisper.cpp via `whisper-cli` binary + ggml model
    #   2. Remote HTTP: any whisper-compatible server (see provider schema below)
    #
    # Providers come from config.bridge.stt_providers — a list of dicts. Each
    # provider entry describes how to talk to one HTTP endpoint:
    #
    #   {
    #     "name":   "label for logs / UI",            (required)
    #     "url":    "http://host:port/transcribe",    (required)
    #     "health": "http://host:port/health",        (optional, default = url root)
    #     "field":  "audio" | "file",                 (multipart field name; default "audio")
    #     "query":  {"language": "zh"},               (optional URL params)
    #     "result_keys": ["text", "transcript"],      (optional response keys to try)
    #   }
    #
    # The repo ships ZERO providers — users add their own via Settings UI
    # (or via config.json directly). For a plugin-style integration, drop a
    # python module at ~/.config/shellframe/stt_plugin.py exporting
    # `transcribe(audio_path: str) -> str`; it's tried before the HTTP chain.
    LOCAL_MODEL_DIR = _Path.home() / ".local" / "share" / "shellframe" / "whisper-models"
    LOCAL_MODEL_NAME = "ggml-base.bin"  # ~150MB, decent quality, fast on Apple Silicon
    LOCAL_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
    PLUGIN_FILE = _Path.home() / ".config" / "shellframe" / "stt_plugin.py"

    @classmethod
    def _stt_providers_from_config(cls) -> list:
        """Read provider chain from config.bridge.stt_providers, applying defaults."""
        try:
            from main import load_config
            cfg = load_config()
        except Exception:
            cfg = {}
        raw = (cfg.get("bridge", {}) or {}).get("stt_providers") or []
        normalized = []
        for p in raw:
            if not isinstance(p, dict) or not p.get("url"):
                continue
            normalized.append({
                "name": p.get("name") or p["url"],
                "url": p["url"].rstrip("/"),
                "health": p.get("health") or p["url"],
                "field": p.get("field") or "audio",
                "query": p.get("query") or None,
                "result_keys": p.get("result_keys") or ["text", "transcript"],
            })
        return normalized

    @classmethod
    def _stt_local_binary(cls):
        """Return path to whisper-cli binary if installed, else ''."""
        for name in ("whisper-cli", "whisper-cpp", "main"):
            p = shutil.which(name)
            if p:
                return p
        return ""

    @classmethod
    def _stt_local_model_path(cls):
        """Return path to local whisper model if downloaded, else ''."""
        p = cls.LOCAL_MODEL_DIR / cls.LOCAL_MODEL_NAME
        return str(p) if p.exists() else ""

    @classmethod
    def stt_status(cls, remote_url: str = "") -> dict:
        """Diagnostic: return state of local + plugin + remote provider chain."""
        local_bin = cls._stt_local_binary()
        local_model = cls._stt_local_model_path()
        local_ok = bool(local_bin and local_model)
        plugin_ok = cls.PLUGIN_FILE.exists()

        providers = cls._stt_providers_from_config()
        # Allow overriding provider chain with a single URL (legacy / quick test)
        if remote_url:
            providers = [{
                "name": "custom",
                "url": remote_url.rstrip("/"),
                "health": remote_url.rstrip("/"),
                "field": "audio",
                "query": None,
            }] + providers

        endpoints_status = []
        first_ok = None
        for ep in providers:
            ep_ok = False
            ep_err = ""
            try:
                req = urllib.request.Request(ep["health"])
                with urllib.request.urlopen(req, timeout=3) as resp:
                    ep_ok = 200 <= resp.status < 500
            except Exception as e:
                ep_err = str(e)
            endpoints_status.append({
                "name": ep["name"],
                "url": ep["url"],
                "ready": ep_ok,
                "error": ep_err,
            })
            if ep_ok and first_ok is None:
                first_ok = ep

        return {
            "local": {
                "binary": local_bin,
                "model": local_model,
                "ready": local_ok,
            },
            "plugin": {
                "path": str(cls.PLUGIN_FILE),
                "ready": plugin_ok,
            },
            "remote": {
                "url": first_ok["url"] if first_ok else "",
                "active": first_ok["name"] if first_ok else "",
                "ready": first_ok is not None,
                "endpoints": endpoints_status,
                "configured": len(providers),
                "error": "" if first_ok else ("no providers configured" if not providers else "all unreachable"),
            },
        }

    def _transcribe_local(self, audio_path: str) -> str:
        """Run whisper-cli locally on the audio file. Returns '' on failure."""
        binary = self._stt_local_binary()
        model = self._stt_local_model_path()
        if not binary or not model:
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  local STT skipped: binary={binary!r} model={model!r}\n")
            return ""
        try:
            # Convert ogg/opus to 16kHz mono WAV via ffmpeg (whisper.cpp wants WAV)
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                with open('/tmp/shellframe_bridge.log', 'a') as _f:
                    _f.write(f"  local STT: ffmpeg not found\n")
                return ""
            wav_path = audio_path.rsplit(".", 1)[0] + ".wav"
            r = subprocess.run(
                [ffmpeg, "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0 or not _Path(wav_path).exists():
                with open('/tmp/shellframe_bridge.log', 'a') as _f:
                    _f.write(f"  ffmpeg convert failed: {r.stderr[:200]}\n")
                return ""

            # Run whisper-cli — output plain text to stdout
            r = subprocess.run(
                [binary, "-m", model, "-f", wav_path, "-l", "auto",
                 "-nt", "-np", "--output-txt", "false"],
                capture_output=True, text=True, timeout=180,
            )
            # whisper-cli prints transcription lines mixed with status — strip
            # to just the recognized text. Lines starting with '[' are timestamps.
            lines = []
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("whisper_") or line.startswith("system_info"):
                    continue
                # Lines like "[00:00:00.000 --> 00:00:02.500]   Hello world"
                if line.startswith("["):
                    parts = line.split("]", 1)
                    if len(parts) == 2:
                        lines.append(parts[1].strip())
                else:
                    lines.append(line)
            text = " ".join(lines).strip()
            try:
                _Path(wav_path).unlink()
            except Exception:
                pass
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  local STT transcribed: {len(text)} chars\n")
            return text
        except Exception as e:
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  local STT failed: {e}\n")
            return ""

    def _transcribe_plugin(self, audio_path: str) -> str:
        """Run a user-provided STT plugin if installed.

        Plugin contract: ~/.config/shellframe/stt_plugin.py exports
        `transcribe(audio_path: str) -> str` returning the recognized text
        (or empty string on failure)."""
        if not self.PLUGIN_FILE.exists():
            return ""
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("sf_stt_plugin", str(self.PLUGIN_FILE))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not hasattr(mod, "transcribe"):
                with open('/tmp/shellframe_bridge.log', 'a') as _f:
                    _f.write(f"  STT plugin missing transcribe(): {self.PLUGIN_FILE}\n")
                return ""
            text = (mod.transcribe(audio_path) or "").strip()
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  STT plugin transcribed: {len(text)} chars\n")
            return text
        except Exception as e:
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  STT plugin failed: {e}\n")
            return ""

    def _transcribe_remote(self, audio_path: str, url: str = "") -> str:
        """Try the configured remote provider chain in order.
        Returns transcribed text, or '' if all providers fail."""
        # Provider chain: config first, optionally prepended with a quick override URL
        chain = self._stt_providers_from_config()
        if url:
            chain = [{
                "name": "override",
                "url": url.rstrip("/"),
                "health": url.rstrip("/"),
                "field": "audio",
                "query": None,
                "result_keys": ["text", "transcript"],
            }] + chain

        if not chain:
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  remote STT: no providers configured\n")
            return ""

        import uuid, mimetypes, urllib.parse
        fname = _Path(audio_path).name
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        with open(audio_path, "rb") as f:
            file_data = f.read()

        last_err = ""
        for ep in chain:
            name = ep["name"]
            try:
                boundary = f"----sf{uuid.uuid4().hex}"
                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{ep["field"]}"; filename="{fname}"\r\n'
                    f"Content-Type: {ctype}\r\n\r\n"
                ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

                target = ep["url"]
                if ep.get("query"):
                    sep = "&" if "?" in target else "?"
                    target = target + sep + urllib.parse.urlencode(ep["query"])

                req = urllib.request.Request(
                    target,
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                # Try the provider's preferred result keys
                text = ""
                for k in ep.get("result_keys", ["text", "transcript"]):
                    if result.get(k):
                        text = str(result[k]).strip()
                        break
                with open('/tmp/shellframe_bridge.log', 'a') as _f:
                    _f.write(f"  remote STT [{name}] transcribed: {len(text)} chars\n")
                if text:
                    return text
                last_err = f"{name}: empty response"
            except Exception as e:
                last_err = f"{name}: {e}"
                with open('/tmp/shellframe_bridge.log', 'a') as _f:
                    _f.write(f"  remote STT [{name}] failed: {e}\n")
                continue
        with open('/tmp/shellframe_bridge.log', 'a') as _f:
            _f.write(f"  all remote STT providers failed; last={last_err}\n")
        return ""

    def _transcribe_voice(self, audio_path: str) -> str:
        """Transcribe audio using configured backend. Returns '' on failure.

        Backend strategy from config.stt_backend:
          - 'auto'   (default): plugin → local → remote chain
          - 'plugin': user plugin only
          - 'local':  local whisper-cli only
          - 'remote': remote provider chain only
          - 'off':    disabled
        """
        backend = getattr(self.config, "stt_backend", "auto") or "auto"

        if backend == "off":
            return ""
        if backend == "plugin":
            return self._transcribe_plugin(audio_path)
        if backend == "local":
            return self._transcribe_local(audio_path)
        if backend == "remote":
            return self._transcribe_remote(audio_path)
        # auto: plugin → local → remote
        text = self._transcribe_plugin(audio_path)
        if text:
            return text
        text = self._transcribe_local(audio_path)
        if text:
            return text
        return self._transcribe_remote(audio_path)

    def _download_tg_file(self, file_id: str, ext: str = "") -> str:
        """Download a Telegram file by file_id, save to CLAUDE_TMP. Returns local path or ''."""
        try:
            result = tg_api(self.config.bot_token, "getFile", {"file_id": file_id})
            if not result.get("ok"):
                return ""
            file_path = result["result"].get("file_path", "")
            if not file_path:
                return ""
            # Determine extension from TG file path if not provided
            if not ext:
                ext = _Path(file_path).suffix or ".bin"
            elif not ext.startswith("."):
                ext = "." + ext
            url = f"https://api.telegram.org/file/bot{self.config.bot_token}/{file_path}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tmp_dir = _Path.home() / ".claude" / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            local = tmp_dir / f"tg_{ts}{ext}"
            local.write_bytes(data)
            return str(local)
        except Exception:
            return ""

    def _handle_update(self, update: dict):
        msg = update.get("message")
        if not msg:
            return

        user = msg.get("from", {})
        user_id = user.get("id", 0)
        chat_id = msg.get("chat", {}).get("id", 0)
        text = msg.get("text", "")
        caption = msg.get("caption", "")

        # Track chat
        self._user_chat[user_id] = chat_id

        # ── Handle photo / document / voice / file messages ──
        file_paths = []
        has_photo = bool(msg.get("photo"))
        has_doc = bool(msg.get("document"))
        has_voice = bool(msg.get("voice"))       # TG voice note (ogg/opus)
        has_audio = bool(msg.get("audio"))       # TG audio file
        with open('/tmp/shellframe_bridge.log', 'a') as _f:
            _f.write(f"_handle_update: text={text!r} caption={caption!r} photo={has_photo} doc={has_doc} voice={has_voice} audio={has_audio}\n")
        if has_photo:
            # TG sends multiple sizes; pick the largest (last)
            photo = msg["photo"][-1]
            path = self._download_tg_file(photo["file_id"], ".png")
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  photo download: file_id={photo['file_id']} path={path!r}\n")
            if path:
                file_paths.append(path)
        if has_doc:
            doc = msg["document"]
            fname = doc.get("file_name", "file")
            ext = _Path(fname).suffix or ".bin"
            path = self._download_tg_file(doc["file_id"], ext)
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  doc download: fname={fname} path={path!r}\n")
            if path:
                file_paths.append(path)

        # ── Voice / audio → transcribe via local STT ──
        if has_voice or has_audio:
            media = msg.get("voice") or msg.get("audio")
            ext = ".oga" if has_voice else (_Path(media.get("file_name", "")).suffix or ".mp3")
            audio_path = self._download_tg_file(media["file_id"], ext)
            with open('/tmp/shellframe_bridge.log', 'a') as _f:
                _f.write(f"  voice download: path={audio_path!r}\n")
            if audio_path:
                # Acknowledge receipt immediately so user knows we're processing
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "🎙 轉錄中…",
                })
                transcribed = self._transcribe_voice(audio_path)
                if transcribed:
                    # Use transcribed text as the message text, append 🎙 prefix
                    if text:
                        text = text + " " + transcribed
                    else:
                        text = f"🎙 {transcribed}"
                    # Confirm transcription to user
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"✓ {transcribed[:200]}{'…' if len(transcribed) > 200 else ''}",
                    })
                else:
                    # Build a helpful diagnostic so the user knows WHY it failed
                    status = self.stt_status()
                    backend = getattr(self.config, "stt_backend", "auto") or "auto"
                    plugin_ok = status.get("plugin", {}).get("ready", False)
                    local_ok = status.get("local", {}).get("ready", False)
                    remote = status.get("remote", {}) or {}
                    remote_ok = remote.get("ready", False)
                    eps = remote.get("endpoints", []) or []
                    lines = ["⚠ 語音轉錄失敗", f"Mode: {backend}"]
                    lines.append(f"Plugin: {'✓' if plugin_ok else '✗'}")
                    lines.append(f"Local:  {'✓' if local_ok else '✗ not installed'}")
                    if not eps:
                        lines.append("Remote: ✗ no providers configured")
                    else:
                        for ep in eps:
                            mark = '✓' if ep.get("ready") else '✗'
                            lines.append(f"  {mark} {ep.get('name')}")
                    if not (plugin_ok or local_ok or remote_ok):
                        lines.append("")
                        lines.append("💡 設定 → Telegram Bridge → 🎙 STT")
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": "\n".join(lines),
                    })
                    return

        # If message has only files (no text), we still need to proceed
        if not text and not file_paths:
            return

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

        # Use caption as text if no text but has caption (photo/doc with caption)
        if not text and caption:
            text = caption

        # ── Slash commands (text-only, no files) ──
        if text and text.startswith("/") and not file_paths:
            cmd = text.split()[0][1:].split("@")[0].lower()
            # Bridge-own commands
            if cmd in ('list', 'status', 'pause', 'resume', 'start', 'reload', 'close', 'new', 'restart', 'update', 'update_now') or cmd.isdigit():
                self._handle_command(cmd, user_id, chat_id)
                return
            # Everything else: forward as CLI slash command (e.g., /model, /skills, /compact)
            # Don't add prefix — send the raw slash command to the CLI

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

        # Build the message to forward
        # Append file paths so the CLI tool can read them
        parts = []
        if text:
            is_cli_cmd = text.startswith("/")
            # If session has a pending menu and user replied with just a digit,
            # send raw without prefix so the CLI selects the option
            is_menu_choice = (
                slot.pending_menu
                and text.strip().isdigit()
                and 1 <= int(text.strip()) <= 9
            )
            if is_cli_cmd:
                parts.append(text)
            elif is_menu_choice:
                parts.append(text.strip())
                slot.pending_menu = False
            elif self.config.prefix_enabled:
                parts.append(f"{username}: {text}")
            else:
                parts.append(text)
        for fp in file_paths:
            parts.append(fp)
        forwarded = " ".join(parts)

        if not forwarded.strip():
            return

        # Confirm file receipt to TG user
        if file_paths:
            count = len(file_paths)
            names = ", ".join(_Path(p).name for p in file_paths)
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"📎 {count} file{'s' if count > 1 else ''} received: {names}",
            })

        # Mark that this session has received a real user message
        # Clear any pre-existing buffer (system prompt responses, etc.)
        if not slot.has_user_msg:
            with slot.output_lock:
                slot.output_buf = ""
        slot.has_user_msg = True
        slot.awaiting_response = True  # arm typing indicator + flush extraction
        # Track what we send so we can filter echo from output
        slot.sent_texts.append(forwarded)
        # Keep only last 10 sent texts
        if len(slot.sent_texts) > 10:
            slot.sent_texts = slot.sent_texts[-10:]

        # Mark the start of a write → reply watch cycle for stall detection
        slot.last_write_ts = time.time()
        slot.stall_warned = False

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
                slots_snapshot = [(sid, self.slots[sid]) for sid in self._slot_order]
            for sid, slot in slots_snapshot:
                marker = " ◀ active" if sid == active_sid else ""
                lines.append(f"\n/{slot.index}  {slot.label}{marker}")
                preview = self._peek_last_response(slot)
                if preview:
                    # Compact preview: first 3 lines, max 200 chars
                    plines = [l for l in preview.split('\n') if l.strip()][:3]
                    snippet = '\n'.join(f"   {l}" for l in plines)
                    if len(snippet) > 220:
                        snippet = snippet[:220] + "…"
                    lines.append(snippet)
                else:
                    lines.append("   (no recent activity)")
            if not slots_snapshot:
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
                    # Peek at last AI response before sending switch msg
                    last_resp = self._peek_last_response(slot)
                    switch_msg = f"Switched to {slot.label} (/{slot.index})"
                    if last_resp:
                        preview = last_resp[:3000] + "\n...(truncated)" if len(last_resp) > 3000 else last_resp
                        switch_msg += f"\n\n💬 Last AI response:\n{preview}"
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": switch_msg,
                    })
                else:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"Invalid session number. Use /list to see available sessions.",
                    })

        elif cmd == "reload":
            if self._on_reload:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "🔄 Hot-reloading bridge module...",
                })
                # Run reload in a thread (it stops/restarts the bridge)
                def _do_reload():
                    try:
                        result = self._on_reload()
                        if isinstance(result, str):
                            result = json.loads(result)
                        msg = result.get("message", "done") if isinstance(result, dict) else str(result)
                        tg_api(self.config.bot_token, "sendMessage", {
                            "chat_id": chat_id,
                            "text": f"✅ {msg}",
                        })
                    except Exception as e:
                        tg_api(self.config.bot_token, "sendMessage", {
                            "chat_id": chat_id,
                            "text": f"❌ Reload failed: {e}",
                        })
                threading.Thread(target=_do_reload, daemon=True).start()
            else:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "Reload not available (no callback registered).",
                })

        elif cmd == "close":
            active_sid = self.get_active_sid(user_id)
            if not active_sid:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "No active session to close.",
                })
                return
            slot = self.slots.get(active_sid)
            label = slot.label if slot else active_sid
            if len(self.slots) <= 1:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "Can't close the last session.",
                })
                return
            def _do_close():
                result = self._sfctl_call("close_session", {"sid": active_sid})
                if result.get("success"):
                    new_sid = self.get_active_sid(user_id)
                    new_label = self.slots[new_sid].label if new_sid and new_sid in self.slots else "none"
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"✕ Closed {label}\nSwitched to {new_label}",
                    })
                else:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"❌ {result.get('message', 'Close failed')}",
                    })
            threading.Thread(target=_do_close, daemon=True).start()

        elif cmd == "new":
            # Get preset from args if provided, default to "claude"
            parts = text.split(maxsplit=1) if text else []
            preset_cmd = parts[1] if len(parts) > 1 else "claude"
            def _do_new():
                result = self._sfctl_call("new_session", {"cmd": preset_cmd})
                if result.get("success"):
                    new_sid = result.get("details", {}).get("sid", "?")
                    # Auto-switch user to new session
                    self._user_active[user_id] = new_sid
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"✚ Created new session: {preset_cmd}\nSwitched to it. Use /list to see all.",
                    })
                else:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"❌ {result.get('message', 'Create failed')}",
                    })
            threading.Thread(target=_do_new, daemon=True).start()

        elif cmd == "restart":
            if not self._on_restart:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "Restart not available.",
                })
                return
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": "♻️ 重啟 ShellFrame 中… session 會自動 reattach",
            })
            def _do_restart():
                try:
                    self._on_restart()
                except Exception as e:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": f"❌ Restart failed: {e}",
                    })
            threading.Thread(target=_do_restart, daemon=True).start()

        elif cmd == "update":
            if not self._on_check_update:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "Update check not available.",
                })
                return
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": "🔍 檢查更新中…",
            })
            def _do_update():
                try:
                    info = self._on_check_update()
                    if isinstance(info, str):
                        info = json.loads(info)
                    local = info.get("local", "?")
                    remote = info.get("remote", "?")
                    if info.get("update_available"):
                        msg = f"⬆️ 有新版本\n本地: v{local}\n遠端: v{remote}\n\n回 /update_now 套用"
                    else:
                        msg = f"✅ 已是最新版 (v{local})"
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": msg,
                    })
                except Exception as e:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": f"❌ Update check failed: {e}",
                    })
            threading.Thread(target=_do_update, daemon=True).start()

        elif cmd == "update_now":
            if not self._on_restart or not self._on_check_update:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "Update not available.",
                })
                return
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": "⬇️ 拉取更新中…",
            })
            def _do_update_now():
                try:
                    # Run git pull via the shared do_update mechanism
                    result = self._sfctl_call("do_update", {})
                    if not result.get("success"):
                        tg_api(self.config.bot_token, "sendMessage", {
                            "chat_id": chat_id,
                            "text": f"❌ {result.get('message', 'Update failed')}",
                        })
                        return
                    details = result.get("details", {})
                    new_ver = details.get("version", "?")
                    needs_restart = details.get("needs_restart", False)
                    if needs_restart:
                        tg_api(self.config.bot_token, "sendMessage", {
                            "chat_id": chat_id,
                            "text": f"✅ 拉到 v{new_ver} — 觸發重啟（session 會保留）",
                        })
                        self._on_restart()
                    else:
                        tg_api(self.config.bot_token, "sendMessage", {
                            "chat_id": chat_id,
                            "text": f"✅ 拉到 v{new_ver} — 純 UI 改動，下次 reload 即可",
                        })
                except Exception as e:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": f"❌ Update failed: {e}",
                    })
            threading.Thread(target=_do_update_now, daemon=True).start()

        elif cmd == "start":
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"ShellFrame Bridge\n\n/list — list sessions\n/new [cmd] — new session (default: claude)\n/close — close current session\n/1, /2, ... — switch session\n/pause — pause bridge\n/resume — resume\n/reload — hot-reload bridge code\n/restart — full app restart (sessions preserved)\n/update — check for updates\n/update_now — pull + restart if needed\n/status — show status",
            })

        else:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"Unknown command /{cmd}. Use /list to see sessions.",
            })

    # ── Status ──

    def get_primary_active_sid(self) -> str:
        """Return the active session sid for the primary (first) TG user."""
        if self._user_active:
            return next(iter(self._user_active.values()))
        default = getattr(self, '_default_active_sid', None)
        if default and default in self.slots:
            return default
        return self._slot_order[0] if self._slot_order else ""

    def switch_active_session(self, sid: str):
        """Switch all TG users to the given session and notify them."""
        if sid not in self.slots:
            return
        slot = self.slots[sid]
        # Store default active session (used when no user has interacted yet)
        self._default_active_sid = sid
        for uid in list(self._user_active):
            self._user_active[uid] = sid
        # Also set for users with no explicit selection
        for uid in self._user_chat:
            self._user_active[uid] = sid
        # Notify TG
        last_resp = self._peek_last_response(slot)
        switch_msg = f"Switched to {slot.label} (/{slot.index})"
        if last_resp:
            preview = last_resp[:3000] + "\n...(truncated)" if len(last_resp) > 3000 else last_resp
            switch_msg += f"\n\n💬 Last AI response:\n{preview}"
        for chat_id in set(self._user_chat.values()):
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": switch_msg,
            })

    def get_status(self) -> dict:
        return {
            "bridge_id": self.bridge_id,
            "state": "paused" if self.paused else ("connected" if self.connected else "stopped"),
            "bot": self.bot_info.get("username", ""),
            "bot_name": self.bot_info.get("first_name", ""),
            "paused": self.paused,
            "active": self.active,
            "sessions": len(self.slots),
            "active_sid": self.get_primary_active_sid(),
        }
