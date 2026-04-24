"""
Telegram Bridge for ShellFrame.
Routes one TG bot across multiple PTY sessions with slash-command switching.
Zero external dependencies (uses urllib).
"""

import json
import os as _os
import re
import shutil
import subprocess
import sys as _sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Cross-platform temp dir — keep /tmp on Unix for continuity with existing
# installs, fall back to %TEMP% on Windows
_IS_WIN = _sys.platform == "win32"
_TMP_DIR = tempfile.gettempdir() if _IS_WIN else "/tmp"
_LOG_FILE = _os.path.join(_TMP_DIR, "shellframe_bridge.log")
_LOG_MAX = 1 * 1024 * 1024  # 1MB cap — auto-truncate to prevent unbounded growth
_log_write_count = 0

def _blog(msg: str):
    """Append to bridge log with auto-truncation. Best-effort."""
    global _log_write_count
    try:
        if not msg.endswith('\n'):
            msg = msg + '\n'
        with open(_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(msg)
        _log_write_count += 1
        if _log_write_count % 200 == 0:  # check size every 200 writes
            try:
                if _os.path.getsize(_LOG_FILE) > _LOG_MAX:
                    with open(_LOG_FILE, 'r', encoding='utf-8') as f:
                        content = f.read()
                    with open(_LOG_FILE, 'w', encoding='utf-8') as f:
                        f.write(content[len(content) // 2:])
            except Exception:
                pass
    except Exception:
        pass

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
        with open(_FILTERS_FILE, encoding='utf-8') as f:
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
                with open(_FILTERS_FILE, 'w', encoding='utf-8') as f:
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

# Built-in defaults for the two user-editable prompts. Users override via
# Settings UI; values persist in ~/.config/shellframe/config.json under
# settings.{ui_prompt, tg_prompt}. A MISSING key falls through to these
# defaults; an EMPTY string means the user explicitly turned it off.
DEFAULT_UI_PROMPT = (
    "You are running inside ShellFrame — a tabbed GUI terminal "
    "(pywebview + xterm.js + PTY via tmux). Source lives at "
    "`~/.local/apps/shellframe/`; you can edit it directly when the user "
    "asks for a feature / fix / tune.\n\n"
    "Apply changes with:\n"
    "  • `sfctl reload` — hot-reload bridge_telegram.py / filters.json\n"
    "  • `sfctl restart` — full restart for main.py / web/index.html / "
    "sfctl.py (tmux sessions survive)\n"
    "Bump `version.json` + CHANGELOG.md for anything user-visible.\n\n"
    "Orchestration from this session via `sfctl`:\n"
    "  • `sfctl list` / `sfctl new <cmd>` / `sfctl send <sid> \"<text>\"`\n"
    "  • `sfctl peek <sid>` / `sfctl rename <sid> <name>` / `sfctl close <sid>`\n"
    "Master-session pattern: for parallelizable tasks, spawn workers with "
    "`sfctl new`, dispatch with `sfctl send`, poll every 20–60s with "
    "`sfctl peek`, aggregate results, then `sfctl close`.\n\n"
    "Files pasted via Cmd+V save to `~/.claude/tmp/` and the path is "
    "appended to your input — you can Read them directly.\n\n"
    "Acknowledge briefly and wait for the user's first message."
)

DEFAULT_TG_PROMPT = (
    "[TG] Replying to Telegram mobile — keep responses short and "
    "skimmable: bullets > paragraphs, fence code blocks, no tables (TG "
    "can't render them), no ASCII-art dividers. Long reply → lead with a "
    "one-line takeaway the user can stop at.\n\n"
    "You can self-modify shellframe at `~/.local/apps/shellframe/` when "
    "asked. Apply changes with:\n"
    "  • `sfctl reload` — hot-reload bridge_telegram.py / filters.json\n"
    "  • `sfctl restart` — full restart for main.py / web/index.html / "
    "sfctl.py (tmux sessions survive)\n"
    "Straightforward asks — just edit + reload, don't ask permission. "
    "Bump `version.json` + CHANGELOG.md for anything user-visible."
)


def _read_settings() -> dict:
    """Read ~/.config/shellframe/config.json settings dict. Empty on failure."""
    try:
        cfg_file = _Path.home() / ".config" / "shellframe" / "config.json"
        if cfg_file.exists():
            return (json.loads(cfg_file.read_text(encoding='utf-8'))
                    .get("settings", {}) or {})
    except Exception:
        pass
    return {}


def get_ui_prompt() -> str:
    """UI-side session init prompt. User config > INIT_PROMPT.md > built-in."""
    settings = _read_settings()
    if "ui_prompt" in settings:
        return (settings.get("ui_prompt") or "").strip()
    disk = _load_init_prompt_raw()
    return disk or DEFAULT_UI_PROMPT


def get_tg_prompt() -> str:
    """TG per-turn preamble. User config > built-in. Empty string = user off."""
    settings = _read_settings()
    if "tg_prompt" in settings:
        return (settings.get("tg_prompt") or "").strip()
    return DEFAULT_TG_PROMPT


def _load_init_prompt_raw() -> str:
    """Raw INIT_PROMPT.md read. Kept for migration / backward compat until
    all callers switch to get_ui_prompt(). Empty on failure."""
    try:
        return _INIT_PROMPT_FILE.read_text(encoding='utf-8').strip()
    except Exception:
        return ""


def load_init_prompt() -> str:
    """Back-compat alias. Returns the resolved UI prompt (config > disk >
    built-in default) so existing callers keep working without edits."""
    return get_ui_prompt()


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
        # Claude Code auto-compact: last time we auto-fired /compact on this
        # slot. Used as cooldown so we don't spam the command while context
        # is still settling after a previous compact.
        self.last_compact_ts = 0.0
        # Completion notification: last time we posted a macOS banner for
        # this slot's AI reply. Cooldown prevents multi-chunk spam.
        self.last_notify_ts = 0.0
        # Virtual terminal for screen-based text extraction
        # Use HistoryScreen to keep scrollback — 50-line screen loses long responses
        # Capped at 3000 lines (~180MB worst case per session) to prevent memory bloat
        self.screen = pyte.HistoryScreen(200, 50, history=3000)
        self.stream = pyte.Stream(self.screen)
        self._history_offset = 0  # tracks processed history lines
        self.sent_responses = {"Understood.", "Understood"}  # pre-filter system acks


class TelegramBridge(BridgeBase):
    """
    Multi-session Telegram bridge.
    One bot manages all sessions. Users switch with slash commands.
    """

    PLATFORM = "telegram"

    def __init__(self, bridge_id: str, config: TelegramBridgeConfig, on_status_change=None, on_reload=None, on_close_session=None, on_restart=None, on_check_update=None, on_new_session=None, on_consume_init=None):
        # write_fn not used directly — each session slot has its own
        super().__init__(bridge_id, config, write_fn=None, on_status_change=on_status_change)
        self.bot_info = {}
        self._thread = None
        self._stop_event = threading.Event()
        self._on_reload = on_reload  # callback for hot-reload from TG
        self._on_close_session = on_close_session  # callback(sid) to close a session
        self._on_restart = on_restart  # callback for full app restart from TG
        self._on_check_update = on_check_update  # callback for update check from TG
        self._on_new_session = on_new_session  # callback(cmd) -> sid, create new session
        self._on_consume_init = on_consume_init  # callback(sid) -> str, init prompt if ready
        self._offset = 0
        self._flush_thread = None
        self._watchdog_thread = None
        self._last_poll_tick = 0.0

        # Multi-session state
        self.slots = {}            # sid -> SessionSlot
        self._slot_order = []      # ordered list of sids
        self._user_active = {}     # user_id -> sid (current session per user)
        self._user_chat = {}       # user_id -> chat_id
        self._slots_lock = threading.Lock()

    # ── IPC with main.py (via sfctl file mechanism) ──

    _CMD_FILE = _os.path.join(_TMP_DIR, "shellframe_cmd.json")
    _RESULT_FILE = _os.path.join(_TMP_DIR, "shellframe_result.json")

    def _sfctl_call(self, cmd: str, args: dict = None, timeout: float = 5.0) -> dict:
        """Send a command to main.py via sfctl IPC and wait for result."""
        import os as _os
        # Clean stale result
        try:
            _os.unlink(self._RESULT_FILE)
        except OSError:
            pass
        # Write command
        with open(self._CMD_FILE, 'w', encoding='utf-8') as f:
            json.dump({"cmd": cmd, "args": args or {}, "ts": time.time()}, f, ensure_ascii=False)
        # Wait for result
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.2)
            if _os.path.exists(self._RESULT_FILE):
                try:
                    with open(self._RESULT_FILE, encoding='utf-8') as f:
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

        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

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
            {"command": "fetch", "description": "Fetch latest AI reply & pin it"},
            {"command": "help", "description": "Show all available commands"},
            {"command": "list", "description": "List sessions + bridge state"},
            {"command": "pause", "description": "Pause bridge (stop forwarding)"},
            {"command": "resume", "description": "Resume bridge"},
            {"command": "reload", "description": "Hot-reload bridge code"},
            {"command": "restart", "description": "Full app restart (sessions preserved)"},
            {"command": "update", "description": "Check & apply ShellFrame updates"},
            {"command": "new", "description": "New session (default: claude)"},
            {"command": "close", "description": "Close current session (with confirm)"},
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

    # Cooldown so multi-chunk extractions don't stack notifications.
    _COMPLETE_NOTIFY_COOLDOWN = 30.0

    def _maybe_notify_completion(self, slot):
        """Post a macOS banner when an AI session finishes a reply AND
        shellframe isn't in the foreground. Lets the user walk away (⌘H /
        ⌃⌥Space hidden) and come back when work's done.

        macOS only. Gated by settings.completion_notifications (default on).
        Click handler: osascript-originated banners reliably activate the
        sender .app bundle on click, so tapping it raises shellframe.
        """
        if _sys.platform != "darwin":
            return
        settings = _read_settings()
        if not settings.get("completion_notifications", True):
            return
        now = time.time()
        if now - getattr(slot, "last_notify_ts", 0.0) < self._COMPLETE_NOTIFY_COOLDOWN:
            return
        # Skip when shellframe has user attention. isActive() is the simple
        # "app is frontmost + not hidden" check.
        try:
            from AppKit import NSApp
            if NSApp is not None and NSApp.isActive():
                return
        except Exception:
            pass
        slot.last_notify_ts = now
        label = (slot.label or slot.sid or "session").replace('"', "'")
        try:
            import subprocess as _sp
            # Keep the script simple — escaping anything beyond quotes in
            # osascript strings is fragile. Fixed copy.
            script = (
                f'display notification "AI reply ready — click to view" '
                f'with title "ShellFrame" '
                f'subtitle "{label}" '
                f'sound name "Glass"'
            )
            _sp.Popen(
                ["osascript", "-e", script],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
        except Exception as e:
            _blog(f"[notify] failed: {e}\n")

    def _maybe_auto_compact(self, slot):
        """If this slot is a Claude Code session running low on context,
        auto-send `/compact` to summarise + free tokens. Gated behind
        settings.claude_auto_compact (default on) and a user-tunable
        percent threshold (default 15).

        Detection: matches the Claude status bar's "<model> … <N>% left"
        line in pyte screen's last rows. The model name in the same line
        (sonnet / opus / haiku / claude-…) is our signal that this is
        actually Claude Code and not some other CLI. We fire only while
        the slot is idle (no in-flight response, no recent PTY chunk),
        and obey a cooldown so post-compact settling doesn't re-trigger.
        """
        settings = _read_settings()
        if not settings.get('claude_auto_compact', True):
            return
        try:
            threshold = int(settings.get(
                'claude_auto_compact_threshold',
                self._AUTO_COMPACT_DEFAULT_THRESHOLD,
            ))
        except (TypeError, ValueError):
            threshold = self._AUTO_COMPACT_DEFAULT_THRESHOLD
        now = time.time()
        if now - getattr(slot, 'last_compact_ts', 0.0) < self._AUTO_COMPACT_COOLDOWN:
            return
        # Don't step on an in-flight response — /compact would land as the
        # user's next message. Also require 2s of PTY silence so we're sure
        # the TUI is at an input prompt, not mid-render.
        if slot.awaiting_response:
            return
        if slot.last_chunk_ts > 0 and now - slot.last_chunk_ts < 2.0:
            return
        # Scan the last few rendered rows (status bar lives at the bottom)
        try:
            tail = '\n'.join(slot.screen.display[-8:])
        except Exception:
            return
        m = self._CLAUDE_TOKEN_RE.search(tail)
        if not m:
            return
        try:
            pct_left = int(m.group(1))
        except (TypeError, ValueError):
            return
        if pct_left > threshold:
            return
        _blog(f"[auto-compact] sid={slot.sid} label={slot.label!r} "
              f"pct_left={pct_left} threshold={threshold} — sending /compact\n")
        slot.last_compact_ts = now
        try:
            slot.write_fn('/compact\r')
        except Exception as e:
            _blog(f"[auto-compact] write failed: {e}\n")

    def _send_typing(self, sid: str):
        """Send typing indicator to users watching this session."""
        for uid, active_sid in list(self._user_active.items()):
            if active_sid == sid and uid in self._user_chat:
                tg_api(self.config.bot_token, "sendChatAction", {
                    "chat_id": self._user_chat[uid],
                    "action": "typing",
                })

    # Process owners of on-screen windows that indicate a modal / permission
    # dialog is blocking foreground work. Checked before firing stall warnings
    # so we don't cry wolf on long-running AI tasks.
    _POPUP_OWNERS = frozenset({
        "UserNotificationCenter",   # TCC permission dialogs (Sonoma+)
        "CoreServicesUIAgent",      # quarantine / "are you sure you want to open" / auth
        "SecurityAgent",            # admin password / keychain prompts
        "loginwindow",              # some password prompts
        "universalAccessAuthWarn",  # Accessibility prompts
    })

    def _detect_blocking_popup(self):
        """Return owner name of a visible system popup, or None.

        Uses CGWindowListCopyWindowInfo (no Accessibility/Screen Recording
        permission required for owner names). Returns None on non-macOS or
        if Quartz is unavailable so callers fall back to silence, not noise.
        """
        if _sys.platform != "darwin":
            return None
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGWindowListOptionOnScreenOnly,
                kCGNullWindowID,
            )
        except Exception:
            return None
        try:
            wins = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID,
            ) or []
        except Exception:
            return None
        for w in wins:
            owner = w.get("kCGWindowOwnerName", "")
            if owner in self._POPUP_OWNERS:
                return owner
        return None

    def _warn_stalled(self, sid: str, age_s: int):
        """Notify user that a session hasn't responded — but only when we
        can actually see a blocking popup. A plain long-running task should
        not trigger noise."""
        slot = self.slots.get(sid)
        if not slot:
            return
        label = slot.label or sid

        popup_owner = self._detect_blocking_popup()
        if not popup_owner:
            _blog(f"[stall] {label} no reply ~{age_s}s — no popup detected, staying silent\n")
            return

        msg = (f"⚠️ [{label}] no reply for ~{age_s}s\n"
               f"macOS popup detected ({popup_owner}) — bring shellframe to "
               f"the front and dismiss it.")

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
                    f'{popup_owner} popup is blocking" '
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

    # Per-turn TG preamble is loaded dynamically from config via
    # get_tg_prompt() — users edit it in Settings → TG Bridge → Per-turn
    # preamble. See module-level DEFAULT_TG_PROMPT for the built-in text.

    # Claude Code status-bar token gauge:
    #   Sonnet 4.6 (1M context) · Claude Max · 12% left
    #   Opus 4.7 … · 6% left
    #   claude-3-5-sonnet … · 4% left
    # We don't bind to colour / unicode bullets so the bar's many cosmetic
    # variations (different plans, 1M vs 200k context, different models)
    # all fall under one capture.
    _CLAUDE_TOKEN_RE = re.compile(
        r'(?:sonnet|opus|haiku|claude[-\s])[^%\n]{0,160}?(\d+)\s*%\s*left',
        re.IGNORECASE,
    )
    # Default threshold + cooldown for Auto /compact (overridable via
    # config.settings.claude_auto_compact_threshold).
    _AUTO_COMPACT_DEFAULT_THRESHOLD = 15  # %
    _AUTO_COMPACT_COOLDOWN = 90.0         # seconds

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

            # Skip echo of sent text. Three detection modes:
            #   1. reply is entirely nested inside a sent text (nr in ns)
            #   2. sent text starts the reply (ns[:25] in nr) — catches the
            #      "Howard: xxx" prefix echo
            #   3. reply contains a long contiguous chunk from a sent text
            #      (>= ECHO_CHUNK_MIN chars) — catches preamble drift where
            #      the AI emits "...sfctl restart — full restart for main.py
            #      / web/index.html..." in the middle of its reply. Mode 1/2
            #      miss this because the reply is larger than any single sent
            #      text and doesn't start at the preamble's first 25 chars.
            ECHO_CHUNK_MIN = 30
            is_echo = False
            nr = text.replace(' ', '').replace('\n', '').lower()
            for sent in slot.sent_texts:
                ns = sent.replace(' ', '').lower()
                if not ns or len(nr) <= 3:
                    continue
                if nr in ns or ns[:25] in nr:
                    is_echo = True
                    break
                # Sliding-window substring match for longer sent texts.
                if len(ns) >= ECHO_CHUNK_MIN:
                    step = 5
                    for i in range(0, len(ns) - ECHO_CHUNK_MIN + 1, step):
                        if ns[i:i + ECHO_CHUNK_MIN] in nr:
                            is_echo = True
                            break
                    if is_echo:
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
        if tmux unavailable or session missing. Uses sf_<sid> naming convention.
        On Windows tmux doesn't exist — return immediately so the caller falls
        back to pyte parsing."""
        if _IS_WIN or not shutil.which("tmux"):
            return ""
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

            # Claude auto-compact check — runs outside output_lock so the
            # scan doesn't contend with feed_output. Cheap: one regex on the
            # last ~8 rendered lines per slot per 0.5s tick.
            for sid in sids:
                slot = self.slots.get(sid)
                if not slot:
                    continue
                try:
                    self._maybe_auto_compact(slot)
                except Exception as e:
                    _blog(f"[auto-compact] {sid} check failed: {e}\n")

            for sid in sids:
                slot = self.slots.get(sid)
                if not slot:
                    continue

                with slot.output_lock:
                    # Refresh the TG typing indicator on every tick while the
                    # session is waiting on a reply. TG auto-clears the bubble
                    # after ~5s, so we MUST refresh at least that often; the
                    # old code only refreshed inside the `idle < 3.0` branch,
                    # which meant long silent "thinking" stretches blanked the
                    # indicator even though the reply was still pending. Runs
                    # regardless of last_output_time so typing also shows in
                    # the pre-first-chunk window right after user submit.
                    if slot.awaiting_response:
                        self._send_typing(sid)

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
                        continue

                    # Extract new text via screen diff (only final changes)
                    new_lines = self._extract_new_text(slot)
                    slot.sent_texts.clear()
                    slot.last_output_time = 0
                    slot.first_output_time = 0
                    # Response extracted → close the stall-watch window
                    if new_lines:
                        was_awaiting = slot.awaiting_response
                        slot.last_write_ts = 0.0
                        slot.stall_warned = False
                        slot.awaiting_response = False  # response delivered, stop typing
                        slot.last_extraction_ts = now
                        # Notify user if shellframe isn't in front. Only fire
                        # when the slot was actively awaiting a response —
                        # otherwise late-arriving background output (status
                        # bar refreshes, scrollback tail) would trigger.
                        if was_awaiting:
                            try:
                                self._maybe_notify_completion(slot)
                            except Exception as e:
                                _blog(f"[notify] scheduling failed: {e}\n")
                    # Keep has_user_msg=True so subsequent responses still get
                    # forwarded.  It resets only when a NEW user message arrives
                    # (the _handle_update path sets it fresh each time).

                # Debug log
                log_msg = f"flush {sid}: new_lines={len(new_lines)} users={dict(self._user_active)} has_msg={slot.has_user_msg}\n"
                for l in new_lines[:5]:
                    log_msg += f"  [{l}]\n"
                if not new_lines:
                    screen_lines = [l.rstrip() for l in slot.screen.display if l.rstrip()]
                    log_msg += f"  screen({len(screen_lines)}): {[l[:60] for l in screen_lines[-5:]]}\n"
                _blog(log_msg)

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
            _blog(f"_send_tg_file error: {file_path} -> {e}\n")

    # ── TG Polling ──
    # Persistent state — survives full app restarts.
    # Holds: the getUpdates offset (so /restart doesn't re-process itself) AND
    # per-user active-session routing (_user_active) so TG users return to the
    # same session after restart instead of defaulting to _slot_order[0].
    _OFFSET_FILE = _Path.home() / ".config" / "shellframe" / "tg_offset.json"

    @classmethod
    def _load_persisted(cls) -> dict:
        try:
            return json.loads(cls._OFFSET_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}

    @classmethod
    def _load_offset(cls) -> int:
        return int(cls._load_persisted().get("offset", 0) or 0)

    def _save_offset(self):
        """Persist offset + user routing state. Called on every update handled
        and also from mutation sites (via _save_state)."""
        self._save_state()

    def _save_state(self):
        try:
            self._OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
            # int keys need str conversion for JSON
            data = {
                "offset": self._offset,
                "user_active": {str(uid): sid for uid, sid in self._user_active.items()},
                "user_chat": {str(uid): cid for uid, cid in self._user_chat.items()},
                "default_active_sid": getattr(self, '_default_active_sid', None),
            }
            self._OFFSET_FILE.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding='utf-8',
            )
        except Exception:
            pass

    def _restore_user_routing(self):
        """Called from _poll_loop entry once slots are registered. Restores
        user_active + user_chat mappings from disk, filtering out sids that
        no longer exist."""
        try:
            data = self._load_persisted()
            saved = data.get("user_active", {}) or {}
            saved_chat = data.get("user_chat", {}) or {}
            saved_default = data.get("default_active_sid")
            slot_keys = list(self.slots.keys())
            _blog(f"[restore] slots={slot_keys} saved_user_active={saved} "
                  f"saved_chat={saved_chat} saved_default={saved_default!r}\n")
            restored = {}
            for uid_str, sid in saved.items():
                try:
                    uid = int(uid_str)
                except (TypeError, ValueError):
                    continue
                if sid in self.slots and uid not in self._user_active:
                    self._user_active[uid] = sid
                    restored[uid] = sid
            # Restore user_chat independently — TG typing indicator + flush
            # forwarding both need uid → chat_id mapping available before the
            # user sends their first post-restart message (otherwise typing is
            # silently no-op'd while the AI still mid-reply on a long task).
            for uid_str, cid in saved_chat.items():
                try:
                    uid = int(uid_str)
                except (TypeError, ValueError):
                    continue
                if uid not in self._user_chat and cid:
                    self._user_chat[uid] = cid
            if saved_default and saved_default in self.slots and not getattr(self, '_default_active_sid', None):
                self._default_active_sid = saved_default
            _blog(f"[restore] applied restored={restored} user_chat={dict(self._user_chat)} "
                  f"default={getattr(self, '_default_active_sid', None)!r}\n")
        except Exception as e:
            _blog(f"[restore] FAILED: {e}\n")

    def _poll_loop(self):
        # Restore offset from disk on first run (handles full app restart)
        if self._offset == 0:
            self._offset = self._load_offset()
        # Restore per-user active-session routing from disk. Without this call
        # full restarts fall through to _slot_order[0] and TG users always end
        # up on the first session regardless of where they were.
        self._restore_user_routing()
        first_batch = True
        self._last_poll_tick = time.time()
        conflict_warned = False
        while self.active and not self._stop_event.is_set():
            try:
                result = tg_api(self.config.bot_token, "getUpdates", {
                    "offset": self._offset,
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query"],
                })
                # Mark liveness regardless of ok — we at least got a network round-trip
                self._last_poll_tick = time.time()
                if not result.get("ok"):
                    desc = str(result.get("description", ""))
                    # HTTP 409: another getUpdates poller is running — same bot
                    # token on another machine. Surface it loudly so the user
                    # knows their messages are being eaten by the other poller.
                    if "409" in desc or "Conflict" in desc:
                        if not conflict_warned:
                            _blog(f"[poll] 409 Conflict — another poller has the bot: {desc}\n")
                            self._emit_status({
                                "state": "error",
                                "message": "Another process is polling this bot. Stop the other shellframe/bot instance or use a different token.",
                                "conflict": True,
                            })
                            # Notify allowed users via TG (best-effort — may be
                            # intercepted by the other instance, but try anyway)
                            for uid in (self.config.allowed_users or []):
                                try:
                                    tg_api(self.config.bot_token, "sendMessage", {
                                        "chat_id": self._user_chat.get(uid, uid),
                                        "text": "⚠️ Bot conflict: another ShellFrame/bot is polling this token. Messages will be flaky until the other instance stops.",
                                    })
                                except Exception:
                                    pass
                            conflict_warned = True
                        time.sleep(30)  # back off — don't spam Telegram with conflicting requests
                        continue
                    time.sleep(5)
                    continue
                if conflict_warned:
                    # Recovered — other poller stopped
                    _blog("[poll] conflict cleared\n")
                    self._emit_status({"state": "connected", "bot": self.bot_info.get("username", "")})
                    conflict_warned = False
                updates = result.get("result", [])
                for update in updates:
                    self._offset = update["update_id"] + 1
                    # Save BEFORE handling so a mid-update restart can't
                    # re-process the same message. We save AGAIN after
                    # handling so any /N switch / auto-track of _user_active
                    # is flushed to disk promptly (previously it only
                    # persisted on the NEXT poll iteration → up to 30s of
                    # stale state if the user restarted right after switching).
                    self._save_offset()
                    # Safety net: on the very first poll batch after startup,
                    # skip self-restart commands. Prevents infinite restart loops
                    # when the previous instance died before saving the offset.
                    if first_batch:
                        msg = update.get("message", {})
                        text = (msg.get("text") or "").strip().lower()
                        cmd = text.split()[0] if text else ""
                        if cmd in ("/restart", "/update_now", "/reload"):
                            _blog(f"  startup safety: skipping {cmd}\n")
                            continue
                    self._handle_update(update)
                    # Save AGAIN post-handle so /N switches, auto-track, and
                    # first-message routing land on disk immediately instead
                    # of waiting up to 30s for the next getUpdates cycle.
                    self._save_offset()
                first_batch = False
            except Exception:
                time.sleep(5)

    def _watchdog_loop(self):
        """Monitor poll liveness. If `_last_poll_tick` goes stale (>120s with no
        network round-trip), the poll thread is wedged — trigger a hot-reload to
        reset it so /reload and /restart from TG keep working even after a bad
        network blip or system sleep."""
        STALL_THRESHOLD = 60.0  # halved from 120s so /reload recovers within ~1 min of poll wedge
        while self.active and not self._stop_event.is_set():
            # Check every 30s — cheap, never itself hangs
            for _ in range(30):
                if self._stop_event.is_set() or not self.active:
                    return
                time.sleep(1)
            try:
                age = time.time() - getattr(self, '_last_poll_tick', time.time())
                if age > STALL_THRESHOLD:
                    _blog(f"[watchdog] poll stalled {age:.0f}s — triggering self-reload\n")
                    if self._on_reload:
                        # Run in a new thread so watchdog doesn't block
                        threading.Thread(target=self._on_reload, daemon=True).start()
                        # After triggering reload, stop this watchdog — the new
                        # bridge instance spawns its own watchdog.
                        return
            except Exception as e:
                _blog(f"[watchdog] exception: {e}\n")

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
    # Secondary search paths. Users who've already set up whisper.cpp for other
    # tools (yt-notion, manual transcribing) usually keep the model under
    # ~/.cache/whisper-models — reuse it instead of forcing a second download.
    LOCAL_MODEL_FALLBACKS = (
        _Path.home() / ".cache" / "whisper-models" / "ggml-base.bin",
        _Path("/opt/homebrew/share/whisper-cpp/ggml-base.bin"),
    )
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
        """Return path to local whisper model. Checks the shellframe-owned dir
        first, then common shared locations (yt-notion / brew / etc.), so a
        pre-existing download isn't redundantly duplicated."""
        primary = cls.LOCAL_MODEL_DIR / cls.LOCAL_MODEL_NAME
        if primary.exists():
            return str(primary)
        for fb in cls.LOCAL_MODEL_FALLBACKS:
            if fb.exists():
                return str(fb)
        return ""

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
            _blog(f"  local STT skipped: binary={binary!r} model={model!r}\n")
            return ""
        try:
            # Convert ogg/opus to 16kHz mono WAV via ffmpeg (whisper.cpp wants WAV)
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                _blog(f"  local STT: ffmpeg not found\n")
                return ""
            wav_path = audio_path.rsplit(".", 1)[0] + ".wav"
            r = subprocess.run(
                [ffmpeg, "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0 or not _Path(wav_path).exists():
                _blog(f"  ffmpeg convert failed: {r.stderr[:200]}\n")
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
            _blog(f"  local STT transcribed: {len(text)} chars\n")
            return text
        except Exception as e:
            _blog(f"  local STT failed: {e}\n")
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
                _blog(f"  STT plugin missing transcribe(): {self.PLUGIN_FILE}\n")
                return ""
            text = (mod.transcribe(audio_path) or "").strip()
            _blog(f"  STT plugin transcribed: {len(text)} chars\n")
            return text
        except Exception as e:
            _blog(f"  STT plugin failed: {e}\n")
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
            _blog(f"  remote STT: no providers configured\n")
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
                _blog(f"  remote STT [{name}] transcribed: {len(text)} chars\n")
                if text:
                    return text
                last_err = f"{name}: empty response"
            except Exception as e:
                last_err = f"{name}: {e}"
                _blog(f"  remote STT [{name}] failed: {e}\n")
                continue
        _blog(f"  all remote STT providers failed; last={last_err}\n")
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

    @staticmethod
    def _load_presets() -> list:
        """Load presets from main config.json (for inline keyboard pickers)."""
        try:
            from main import load_config
            cfg = load_config()
            return cfg.get("presets", []) or []
        except Exception:
            return []

    def _handle_callback_query(self, cq: dict):
        """Handle inline keyboard button taps."""
        cq_id = cq.get("id", "")
        data = cq.get("data", "")
        user = cq.get("from", {})
        user_id = user.get("id", 0)
        message = cq.get("message", {}) or {}
        chat_id = message.get("chat", {}).get("id", 0)
        message_id = message.get("message_id", 0)

        _blog(f"_handle_callback_query: data={data!r} user={user_id}\n")

        # Whitelist check
        if self.config.allowed_users and user_id not in self.config.allowed_users:
            tg_api(self.config.bot_token, "answerCallbackQuery", {
                "callback_query_id": cq_id, "text": "Access denied"})
            return

        # Always ack the callback so TG stops the spinner
        tg_api(self.config.bot_token, "answerCallbackQuery", {"callback_query_id": cq_id})

        if data.startswith("new:"):
            choice = data[4:]
            if choice == "cancel":
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "✕ 已取消",
                })
                return
            # Look up preset by name
            presets = self._load_presets()
            preset = next((p for p in presets if p.get("name") == choice), None)
            if not preset:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": f"❌ Preset not found: {choice}",
                })
                return
            preset_cmd = preset.get("cmd", "")
            if not preset_cmd:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": f"❌ Preset has no cmd",
                })
                return
            # Track chat (callback message has chat too)
            self._user_chat[user_id] = chat_id
            # Create the session
            def _do_create():
                new_sid = ""
                err = ""
                if self._on_new_session:
                    try:
                        new_sid = self._on_new_session(preset_cmd)
                    except Exception as e:
                        err = str(e)
                else:
                    result = self._sfctl_call("new_session", {"cmd": preset_cmd})
                    if result.get("success"):
                        new_sid = result.get("details", {}).get("sid", "")
                    else:
                        err = result.get("message", "")
                if new_sid:
                    self._user_active[user_id] = new_sid
                    self._default_active_sid = new_sid
                    tg_api(self.config.bot_token, "editMessageText", {
                        "chat_id": chat_id, "message_id": message_id,
                        "text": f"✚ {preset.get('icon', '▶')} {preset.get('name')} 已建立\n切到此 session（/list 可看全部）",
                    })
                else:
                    tg_api(self.config.bot_token, "editMessageText", {
                        "chat_id": chat_id, "message_id": message_id,
                        "text": f"❌ Create failed: {err or 'unknown error'}",
                    })
            threading.Thread(target=_do_create, daemon=True).start()
            return

        if data.startswith("close:"):
            parts = data.split(":", 2)
            choice = parts[1] if len(parts) > 1 else ""
            if choice == "no":
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "✕ 取消",
                })
                return
            if choice == "yes":
                target_sid = parts[2] if len(parts) > 2 else self.get_active_sid(user_id)
                if not target_sid or target_sid not in self.slots:
                    tg_api(self.config.bot_token, "editMessageText", {
                        "chat_id": chat_id, "message_id": message_id,
                        "text": "Session already gone.",
                    })
                    return
                label = self.slots[target_sid].label
                def _do_close():
                    ok = False
                    err = ""
                    if self._on_close_session:
                        try:
                            self._on_close_session(target_sid)
                            ok = True
                        except Exception as e:
                            err = str(e)
                    else:
                        result = self._sfctl_call("close_session", {"sid": target_sid})
                        ok = result.get("success", False)
                        err = result.get("message", "")
                    if ok:
                        new_sid = self.get_active_sid(user_id)
                        new_label = self.slots[new_sid].label if new_sid and new_sid in self.slots else "none"
                        tg_api(self.config.bot_token, "editMessageText", {
                            "chat_id": chat_id, "message_id": message_id,
                            "text": f"✕ Closed {label}\nSwitched to {new_label}",
                        })
                    else:
                        tg_api(self.config.bot_token, "editMessageText", {
                            "chat_id": chat_id, "message_id": message_id,
                            "text": f"❌ Close failed: {err or 'unknown error'}",
                        })
                threading.Thread(target=_do_close, daemon=True).start()
                return

        if data.startswith("update:"):
            choice = data[7:]
            if choice == "no":
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "✕ 取消",
                })
                return
            if choice == "now":
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "⬇️ 拉取更新中…",
                })
                self._user_chat[user_id] = chat_id
                self._apply_update(chat_id)
                return

    def _apply_update(self, chat_id: int):
        """Pull + restart if needed. Shared by /update inline button and the
        back-compat /update_now command."""
        if not self._on_restart or not self._on_check_update:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": "Update not available.",
            })
            return
        def _do():
            try:
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
        threading.Thread(target=_do, daemon=True).start()

    def _handle_update(self, update: dict):
        # Inline keyboard button taps come as callback_query, not message
        cq = update.get("callback_query")
        if cq:
            self._handle_callback_query(cq)
            return

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
        _blog(f"_handle_update: text={text!r} caption={caption!r} photo={has_photo} doc={has_doc} voice={has_voice} audio={has_audio}\n")
        if has_photo:
            # TG sends multiple sizes; pick the largest (last)
            photo = msg["photo"][-1]
            path = self._download_tg_file(photo["file_id"], ".png")
            _blog(f"  photo download: file_id={photo['file_id']} path={path!r}\n")
            if path:
                file_paths.append(path)
        if has_doc:
            doc = msg["document"]
            fname = doc.get("file_name", "file")
            ext = _Path(fname).suffix or ".bin"
            path = self._download_tg_file(doc["file_id"], ext)
            _blog(f"  doc download: fname={fname} path={path!r}\n")
            if path:
                file_paths.append(path)

        # ── Voice / audio → transcribe via local STT ──
        if has_voice or has_audio:
            media = msg.get("voice") or msg.get("audio")
            ext = ".oga" if has_voice else (_Path(media.get("file_name", "")).suffix or ".mp3")
            audio_path = self._download_tg_file(media["file_id"], ext)
            _blog(f"  voice download: path={audio_path!r}\n")
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
            if cmd in ('list', 'status', 'pause', 'resume', 'start', 'help', 'reload', 'close', 'new', 'restart', 'update', 'update_now', 'fetch') or cmd.isdigit():
                # Instant visual ACK — react with 👀 so user sees the bot
                # received the command even before any sendMessage goes out.
                # Non-blocking: reaction failures don't block command dispatch.
                message_id = msg.get("message_id")
                if message_id:
                    threading.Thread(
                        target=lambda: tg_api(self.config.bot_token, "setMessageReaction", {
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "reaction": [{"type": "emoji", "emoji": "👀"}],
                        }),
                        daemon=True,
                    ).start()
                self._handle_command(cmd, user_id, chat_id, text)
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
        wrap_with_preamble = False
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
                wrap_with_preamble = True
            else:
                parts.append(text)
                wrap_with_preamble = True
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
        # Cap sent_texts at 30 (was 10). Each user msg appends up to 2
        # entries (the forwarded text AND the TG preamble wrap), so at cap
        # 10 the history covered only ~5 user turns — on a chatty session
        # the AI could echo a preamble fragment long after that preamble
        # rotated out of sent_texts, and the echo filter missed it.
        if len(slot.sent_texts) > 30:
            slot.sent_texts = slot.sent_texts[-30:]

        # Mark the start of a write → reply watch cycle for stall detection
        slot.last_write_ts = time.time()
        slot.stall_warned = False

        # Inject init prompt if CLI just became ready (first user message path).
        # Mirrors write_input's web-UI injection so TG-created AI sessions get
        # the same system prompt.
        init_prompt = ""
        if self._on_consume_init:
            try:
                init_prompt = self._on_consume_init(active_sid) or ""
            except Exception:
                init_prompt = ""
        if init_prompt:
            slot.sent_texts.append(init_prompt)
            payload = init_prompt + "\n\n---\nUser's first message: " + forwarded
        elif wrap_with_preamble:
            preamble = get_tg_prompt()
            if preamble:
                # Record preamble in sent_texts so echo-filter + prefix-strip
                # continue to work normally on the real `forwarded` text.
                slot.sent_texts.append(preamble)
                payload = preamble + "\n\n" + forwarded
            else:
                payload = forwarded
        else:
            payload = forwarded

        # Write text first, then Enter after a brief delay
        def _send():
            slot.write_fn(payload)
            time.sleep(0.3)
            slot.write_fn("\r")
        threading.Thread(target=_send, daemon=True).start()

    def _handle_command(self, cmd: str, user_id: int, chat_id: int, text: str = ""):
        """Handle slash commands. `text` is the full message text (for argv parsing)."""

        if cmd in ("list", "status"):
            # /status is folded into /list — show bridge state header + sessions.
            state = "paused ⏸" if self.paused else "connected ●"
            bot = self.bot_info.get("username", "?")
            active_sid = self.get_active_sid(user_id)
            with self._slots_lock:
                slots_snapshot = [(sid, self.slots[sid]) for sid in self._slot_order]
            lines = [f"📋 ShellFrame — {state} @ @{bot}", ""]
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
            # Confirm first — close is destructive (kills the PTY + tmux session).
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"Close {label} ({active_sid})?\nThis kills the session — any unsaved CLI state is lost.",
                "reply_markup": {"inline_keyboard": [[
                    {"text": "✕ Close", "callback_data": f"close:yes:{active_sid}"},
                    {"text": "Cancel", "callback_data": "close:no"},
                ]]},
            })

        elif cmd == "new":
            # Parse args from message text
            parts = text.split(maxsplit=1) if text else []
            if len(parts) <= 1:
                # No args → show preset picker as inline keyboard
                presets = self._load_presets()
                if presets:
                    keyboard = []
                    # 2 columns of preset buttons
                    row = []
                    for i, p in enumerate(presets):
                        icon = p.get("icon", "▶")
                        name = p.get("name", "preset")
                        row.append({
                            "text": f"{icon} {name}",
                            "callback_data": f"new:{name}",
                        })
                        if len(row) == 2:
                            keyboard.append(row)
                            row = []
                    if row:
                        keyboard.append(row)
                    keyboard.append([{"text": "❌ Cancel", "callback_data": "new:cancel"}])
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": "✚ 選擇 preset，或直接回 `/new <command>` 開自訂指令：",
                        "reply_markup": {"inline_keyboard": keyboard},
                    })
                    return
                # Fallback: no presets configured, default to claude
                preset_cmd = "claude"
            else:
                preset_cmd = parts[1]
            def _do_new():
                new_sid = ""
                err = ""
                # Direct callback (same-process) is more reliable than file IPC
                if self._on_new_session:
                    try:
                        new_sid = self._on_new_session(preset_cmd)
                    except Exception as e:
                        err = str(e)
                else:
                    result = self._sfctl_call("new_session", {"cmd": preset_cmd})
                    if result.get("success"):
                        new_sid = result.get("details", {}).get("sid", "")
                    else:
                        err = result.get("message", "")
                if new_sid:
                    # Auto-switch user to new session
                    self._user_active[user_id] = new_sid
                    self._default_active_sid = new_sid
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"✚ Created new session: {preset_cmd}\nSwitched to it. Use /list to see all.",
                    })
                else:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"❌ Create failed: {err or 'unknown error'}",
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

        elif cmd in ("update", "update_now"):
            # /update_now is kept as a back-compat alias — it still goes straight
            # to the pull+apply path without the confirm step.
            if not self._on_check_update:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "Update check not available.",
                })
                return
            if cmd == "update_now":
                # Skip the check step — go straight to apply (old behaviour)
                self._apply_update(chat_id)
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
                        tg_api(self.config.bot_token, "sendMessage", {
                            "chat_id": chat_id,
                            "text": f"⬆️ 有新版本\n本地: v{local}\n遠端: v{remote}",
                            "reply_markup": {"inline_keyboard": [[
                                {"text": "⬇️ Update Now", "callback_data": "update:now"},
                                {"text": "Cancel", "callback_data": "update:no"},
                            ]]},
                        })
                    else:
                        tg_api(self.config.bot_token, "sendMessage", {
                            "chat_id": chat_id, "text": f"✅ 已是最新版 (v{local})",
                        })
                except Exception as e:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": f"❌ Update check failed: {e}",
                    })
            threading.Thread(target=_do_update, daemon=True).start()

        elif cmd == "fetch":
            active_sid = self.get_active_sid(user_id)
            if not active_sid or active_sid not in self.slots:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "No active session.",
                })
                return
            slot = self.slots[active_sid]
            reply_text = self._peek_last_response(slot)
            if not reply_text:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "No AI reply found in current session.",
                })
                return
            # Truncate if needed (TG max message = 4096 chars)
            if len(reply_text) > 4000:
                reply_text = reply_text[:4000] + "\n…(truncated)"
            header = f"📌 {slot.label} (/{slot.index})"
            msg_text = f"{header}\n\n{reply_text}"
            resp = tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": msg_text,
            })
            # Pin the message
            if resp and resp.get("ok"):
                msg_id = resp["result"]["message_id"]
                tg_api(self.config.bot_token, "pinChatMessage", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "disable_notification": True,
                })

        elif cmd in ("start", "help"):
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": (
                    "ShellFrame Bridge\n\n"
                    "Sessions:\n"
                    "  /list — sessions + bridge state (with last-response preview)\n"
                    "  /fetch — fetch latest AI reply & pin it\n"
                    "  /new [cmd] — new session (default: claude)\n"
                    "  /close — close current session (with confirm)\n"
                    "  /1, /2, … — switch session\n\n"
                    "Bridge control:\n"
                    "  /pause — pause bridge (bot ignores non-slash messages)\n"
                    "  /resume — resume\n\n"
                    "App control:\n"
                    "  /reload — hot-reload bridge code (picks up bridge_telegram.py changes)\n"
                    "  /restart — full app restart (sessions persist via tmux)\n"
                    "  /update — check for updates; inline button to apply\n\n"
                    "Any other /slashcommand is forwarded to the active session as raw CLI input."
                ),
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
