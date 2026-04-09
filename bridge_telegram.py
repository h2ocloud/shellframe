"""
Telegram Bridge for ShellFrame.
Routes one TG bot across multiple PTY sessions with slash-command switching.
Zero external dependencies (uses urllib).
"""

import json
import re
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

    def __init__(self, bridge_id: str, config: TelegramBridgeConfig, on_status_change=None, on_reload=None, on_close_session=None):
        # write_fn not used directly — each session slot has its own
        super().__init__(bridge_id, config, write_fn=None, on_status_change=on_status_change)
        self.bot_info = {}
        self._thread = None
        self._stop_event = threading.Event()
        self._on_reload = on_reload  # callback for hot-reload from TG
        self._on_close_session = on_close_session  # callback(sid) to close a session
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

        return new_texts

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
                        self._send_typing(sid)
                        continue

                    # Extract new text via screen diff (only final changes)
                    new_lines = self._extract_new_text(slot)
                    slot.sent_texts.clear()
                    slot.last_output_time = 0
                    slot.first_output_time = 0
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

        # ── Handle photo / document / file messages ──
        file_paths = []
        has_photo = bool(msg.get("photo"))
        has_doc = bool(msg.get("document"))
        with open('/tmp/shellframe_bridge.log', 'a') as _f:
            _f.write(f"_handle_update: text={text!r} caption={caption!r} photo={has_photo} doc={has_doc}\n")
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
            if cmd in ('list', 'status', 'pause', 'resume', 'start', 'reload', 'close', 'new') or cmd.isdigit():
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
            if is_cli_cmd:
                parts.append(text)
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

        elif cmd == "start":
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"ShellFrame Bridge\n\n/list — list sessions\n/new [cmd] — new session (default: claude)\n/close — close current session\n/1, /2, ... — switch session\n/pause — pause bridge\n/resume — resume\n/reload — hot-reload bridge code\n/status — show status",
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
