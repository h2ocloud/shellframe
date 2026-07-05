"""
Telegram Bridge for ShellFrame.
Routes one TG bot across multiple PTY sessions with slash-command switching.
Zero external dependencies (uses urllib).
"""

import itertools
import json
import os as _os
import re
import shutil
import subprocess
import sys as _sys
import tempfile
import threading
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from usage_probe import detect_ai as _detect_ai

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

try:
    import board  # shared task-board store (experimental)
except Exception:
    board = None


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
            r'|\[\??[\d; ]+[A-Za-z]'
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
        if re.search(r'\[sf_[^\]]+\].*\[[0-9]+,[0-9]+\].*"[^"]+"', stripped):
            continue
        if re.match(r'^\[[^\]]+\]\s+\[sf_[^\]]+\]\s+\d+:', stripped):
            continue
        if re.match(r'^\[[0-9]+,[0-9]+\]\s+"[^"]+"\s+\d{1,2}:\d{2}\b', stripped):
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


# 高信心 Claude Code / Codex TUI 哨兵：一旦在 marker 區間內出現這些行，代表後面
# 全是終端重繪殘影、評分提示或重複內容（串流抓取把結尾 UI 吃進 start/end 之間）。
# 正常回應絕不會含這些字串，故就地截斷整段尾巴最安全。
_TUI_SENTINEL_RE = re.compile(
    r'(?:how is claude doing this session)'
    r'|(?:\b\d\s*:\s*(?:bad|fine|good|dismiss)\b.*\b\d\s*:\s*(?:bad|fine|good|dismiss)\b)'
    r'|(?:^\s*\d\s*:\s*(?:bad|fine|good|dismiss)\s*$)'
    r'|(?:\besc to interrupt\b)'
    r'|(?:^[✻✢✳∗✽·●⏺•*─\-]{0,2}\s*(?:cooked|worked|saut[eé]ed|churned|baking|brewing|simmering|forging)\s+for\s+\d)',
    re.IGNORECASE)

# Stray reply-marker tokens that can leak into a span when a TUI repaint nests
# a fresh [[TG_REPLY_xxx]] inside an earlier still-open block.
_REPLY_MARKER_TOKEN_RE = re.compile(r'\[\[/?TG_REPLY_[0-9a-fA-F]+\]\]')


def clean_mobile_marker_response(text: str) -> str:
    """Light cleanup for text already isolated by a mobile reply marker.

    Also hard-truncates at the first Claude Code/Codex TUI sentinel line: when
    the terminal repaints after the reply, the rating prompt + duplicated reply
    text can land between the [[TG_REPLY]] start/end markers in the linearized
    PTY stream. Cutting there removes the leak (and the trailing repaint dup).
    """
    lines = []
    seen = set()
    for line in (text or "").splitlines():
        # Drop any residual reply-marker token that leaked into the span
        # (nested repaints can carry a stray [[TG_REPLY_xxx]] / [[/TG_REPLY_xxx]]).
        stripped = _REPLY_MARKER_TOKEN_RE.sub("", line).strip()
        if not stripped:
            continue
        if _TUI_SENTINEL_RE.search(stripped):
            break
        if re.match(r"^\[(TG|LINE)[^\]]*\]:", stripped):
            continue
        if stripped.startswith(("Ran ", "Bash(", "Read ", "Search ", "Explored ", "Edited ", "Write ", "Update ")):
            continue
        if re.match(r'^[-+]\s*(ssh|scp|curl|tmux|cd|mvn|git|python|sleep)\b', stripped):
            continue
        if re.search(r'\b(max_output_tokens|yield_time_ms|session_id|exec_command|write_stdin)\b', stripped):
            continue
        # Global de-dup: a TUI repaints by re-emitting overlapping scroll
        # windows, so the same line lands in the linearized PTY stream several
        # times (non-consecutively). Keep only the first occurrence so a reply
        # longer than the viewport isn't forwarded as a repeated blob.
        if stripped in seen:
            continue
        seen.add(stripped)
        lines.append(stripped)
    return "\n".join(lines).strip()



def split_for_telegram(text: str, limit: int = 3900) -> list:
    """Split text into <=limit-char chunks at line boundaries so long replies
    are sent as multiple Telegram messages instead of being truncated.

    Telegram's hard cap is 4096 chars/message; 3900 leaves headroom for the
    session-label prefix. A single oversized line is hard-split as a last
    resort. Never drops content.
    """
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                chunks.append(buf); buf = ""
            chunks.append(line[:limit]); line = line[limit:]
        piece = ("\n" + line) if buf else line
        if len(buf) + len(piece) > limit:
            chunks.append(buf); buf = line
        else:
            buf += piece
    if buf:
        chunks.append(buf)
    return chunks


def tg_api(token: str, method: str, data=None, timeout: float = 35) -> dict:
    """Telegram Bot API call. Default timeout=35s suits long-poll getUpdates
    (server-side wait=30 + slack). Fire-and-forget calls (sendChatAction,
    completion pings) should pass timeout=3 — a long timeout on a stuck
    socket otherwise backpressures whatever loop dispatched the call."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    if data:
        payload = json.dumps(data).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"ok": False, "description": f"HTTP {e.code}: {body[:200]}"}
    except Exception as e:
        return {"ok": False, "description": str(e)}


_INIT_PROMPT_FILE = _Path(__file__).parent / "INIT_PROMPT.md"
_DEFAULT_USER_PROMPT_PATHS = ["~/.claude/CLAUDE.md"]
_MASTER_TURN_PREAMBLE = (
    "[總控] You are the master tab. Decide by the task itself whether to handle "
    "it directly or to `sfctl delegate <Role> \"<task>\"` a specialist/parallel "
    "worker — judge each case; do not auto-delegate everything, and do not "
    "refuse to delegate when a worker clearly fits. (Full contract is in the "
    "session init prompt — this is just the per-turn reminder.)\n"
    "Grounding — do NOT fabricate: never state the contents of an email, file, "
    "command output, or a worker's result unless you actually read it this turn "
    "(Read / `sfctl peek`). If you haven't verified something, say so and go "
    "read it — never guess amounts, dates, names, or quotes, and never report a "
    "worker's output you didn't actually peek.\n"
    "Refer to workers by tab label, never by sid. Keep any #<tab-label> tags in "
    "the message verbatim (ShellFrame auto-resolves them)."
)

# Built-in defaults for the two user-editable prompts. Users override via
# Settings UI; values persist in ~/.config/shellframe/config.json under
# settings.{ui_prompt, tg_prompt}. Missing or empty values fall through to
# these defaults.
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
    "  • `sfctl list` / `sfctl roster`\n"
    "  • `sfctl delegate <role> \"<task>\"` — create/reuse a configured worker and send a wrapper prompt\n"
    "  • `sfctl new <cmd> --label X --source orchestrator --handoff`\n"
    "  • `sfctl send <sid> \"<text>\"` / `sfctl peek <sid>`\n"
    "  • `sfctl rename <sid> <name>` / `sfctl close <sid> --reason done --handoff`\n\n"
    "Master / worker operating contract:\n"
    "Treat the tab labeled `總控-*` as the master session. The master keeps "
    "the user-facing conversation coherent, decides whether to split work, "
    "dispatches to workers, polls them, merges results, and closes or renames "
    "workers when done. Do not make the user manually coordinate worker tabs. "
    "Do not hard-route user messages by keyword; first understand the request, "
    "then use `sfctl delegate` when the task belongs in a worker.\n"
    "Before substantial work, run `sfctl list` and decide: handle small or "
    "dialog-heavy tasks in the master; use `CDX` workers for coding, repo "
    "edits, local shell operations, tests, ShellFrame fixes, Jenkins/build/"
    "debug work; use `CLD` workers for research, writing, long-context "
    "synthesis, meeting/transcript summarization, Notion/Obsidian knowledge "
    "organization, and ambiguous planning. Use multiple workers only for "
    "genuinely independent subtasks.\n"
    "Name tabs by function first and agent code second, e.g. `RFP調研-CLD`, "
    "`LINE串接-CDX`, `時程信件-CLD`. Start orchestrated workers with "
    "`--source orchestrator --handoff`. The first message to every worker "
    "must include role, goal, inputs/paths, constraints, expected output, "
    "what not to touch, and when to stop. Ask workers to finish with result "
    "summary, changed files or sources checked, verification, blockers, and "
    "whether anything should be added to memory/skill/docs. Poll with "
    "`sfctl peek` every 20–60s and aggregate in the master before replying. "
    "When a worker finishes, keep the tab by default for follow-up; do not "
    "close it unless the user explicitly asks, it is broken/noisy, or tab "
    "pressure is harming the session. Idle reaper will summarize and close "
    "unused workers later.\n"
    "Persistence: this prompt is injected into new AI sessions, and "
    "ShellFrame's manifest persists labels/order/lifecycle metadata. Keep "
    "labels meaningful. Durable workflow learnings should go to shared "
    "project docs or Obsidian first, then mirrored into Codex/Claude memory "
    "only when needed.\n\n"
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
    "Bump `version.json` + CHANGELOG.md for anything user-visible.\n\n"
    "Default coordination: keep this tab as `總控-*` when it is the active "
    "user-facing session. For non-trivial parallel work, run `sfctl list`, "
    "then prefer `sfctl delegate <role> \"<task>\"` to create/reuse the right "
    "worker with its wrapper prompt. No hard keyword routing: understand first, "
    "delegate deliberately. Use `-CDX` for coding/shell/repo work and `-CLD` "
    "for research/writing/knowledge work, poll with `sfctl peek`, then aggregate back here. "
    "Start orchestrated workers with `--source orchestrator --handoff`. Keep "
    "finished worker tabs by default for follow-up; only close them when the "
    "user asks or the tab is broken/noisy. Idle reaper handles unused tabs."
)


def _read_config() -> dict:
    """Read ~/.config/shellframe/config.json without importing main.py."""
    try:
        cfg_file = _Path.home() / ".config" / "shellframe" / "config.json"
        if cfg_file.exists():
            cfg = json.loads(cfg_file.read_text(encoding='utf-8'))
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        pass
    return {}


_SETTINGS_CACHE = {"ts": 0.0, "val": {}}
_SETTINGS_CACHE_TTL = 1.0  # seconds


def _read_settings() -> dict:
    """Read ~/.config/shellframe/config.json settings dict. Empty on failure.

    Cached for _SETTINGS_CACHE_TTL seconds: the flush loop calls this per slot
    every 2s (auto-compact) plus the board check, so an uncached read meant
    8+ file-open+JSON-parse cycles every couple seconds. A 1s TTL keeps UI
    toggles feeling instant while removing that idle-floor I/O."""
    now = time.monotonic()
    if now - _SETTINGS_CACHE["ts"] < _SETTINGS_CACHE_TTL:
        return _SETTINGS_CACHE["val"]
    val = (_read_config().get("settings", {}) or {})
    _SETTINGS_CACHE["ts"] = now
    _SETTINGS_CACHE["val"] = val
    return val


_SETTINGS_WRITE_LOCK = threading.Lock()


def _update_settings(patch: dict) -> bool:
    """Read-modify-write config.json settings with the given key/value patch.
    Returns True on success. Used by TG /voice to switch refine model live."""
    with _SETTINGS_WRITE_LOCK:
        try:
            cfg_file = _Path.home() / ".config" / "shellframe" / "config.json"
            cfg = _read_config()
            settings = cfg.get("settings") or {}
            settings.update(patch)
            cfg["settings"] = settings
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
            _SETTINGS_CACHE["ts"] = 0.0  # invalidate so the write is seen at once
            return True
        except Exception as e:
            _blog(f"  _update_settings failed: {e}\n")
            return False


def master_turn_preamble_enabled() -> bool:
    settings = _read_settings()
    return settings.get("master_turn_preamble_enabled", True) is not False


def get_master_turn_preamble() -> str:
    settings = _read_settings()
    custom = settings.get("master_turn_preamble")
    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    return _MASTER_TURN_PREAMBLE


def show_tg_wrapper() -> bool:
    settings = _read_settings()
    return settings.get("show_tg_wrapper", True) is not False


def is_master_label(label: str) -> bool:
    text = str(label or "").strip()
    folded = text.casefold()
    return (
        text.startswith("總控")
        or folded.startswith("master")
        or folded.startswith("user-facing")
        or "user-facing" in folded
    )


def wrap_master_turn_input(user_text: str) -> str:
    preamble = get_master_turn_preamble()
    return f"{preamble}\n\n---\nUser message: {user_text}"


def get_ui_prompt() -> str:
    """UI-side session init prompt. User config > INIT_PROMPT.md > built-in."""
    settings = _read_settings()
    if "ui_prompt" in settings:
        return (settings.get("ui_prompt") or "").strip()
    disk = _load_init_prompt_raw()
    return disk or DEFAULT_UI_PROMPT


def get_tg_prompt() -> str:
    """TG per-turn preamble. User config > built-in. Empty string = built-in."""
    settings = _read_settings()
    custom = settings.get("tg_prompt")
    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    return DEFAULT_TG_PROMPT


def get_user_prompt_paths() -> list[str]:
    raw = _read_config().get("user_prompt_paths", _DEFAULT_USER_PROMPT_PATHS)
    if not isinstance(raw, list):
        return list(_DEFAULT_USER_PROMPT_PATHS)
    return [str(p).strip() for p in raw if str(p or "").strip()]


def load_user_instructions(max_chars: int | None = None) -> str:
    chunks: list[str] = []
    for raw_path in get_user_prompt_paths():
        try:
            path = _Path(raw_path).expanduser()
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding='utf-8').strip()
            if text:
                chunks.append(f"### {raw_path}\n{text}")
        except Exception:
            continue
    combined = "\n\n".join(chunks).strip()
    if max_chars is not None and max_chars > 0 and len(combined) > max_chars:
        return combined[:max_chars].rstrip()
    return combined


def append_user_instructions(prompt: str, max_chars: int | None = None) -> str:
    user_prompt = load_user_instructions(max_chars=max_chars)
    if not user_prompt:
        return (prompt or "").strip()
    base = (prompt or "").strip()
    if not base:
        return f"## User Instructions\n\n{user_prompt}"
    return f"{base}\n\n## User Instructions\n\n{user_prompt}"


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
    return append_user_instructions(get_ui_prompt())


@dataclass
class TelegramBridgeConfig(BridgeConfigBase):
    bot_token: str = ""
    initial_prompt: str = ""
    stt_backend: str = "auto"   # auto / plugin / local / remote / off

    def __post_init__(self):
        if not self.initial_prompt:
            self.initial_prompt = load_init_prompt()


# ── Precompiled hot-path patterns (item 2) ──
# These run per-line / per-block inside _flush_loop's extraction chain
# (_is_bridge_noise_line, _is_tool_call, _extract_new_text, _detect_menu_prompt).
# `re` caches compiled literals, but hoisting to module constants removes the
# per-call pattern hash + cache lookup on the busy-output path where these fire
# thousands of times per second over wide-char (CJK) terminal content.
_NOISE_TOOLCALL_RE = re.compile(r'^[A-Z][\w\s]*\(.+\)$')
_NOISE_LINES_EXPAND_RE = re.compile(r'^(?:\.\.\. )?\+\d+\s+lines?\s+\(ctrl[+ ]o to expand\)')
_NOISE_MODE_RE = re.compile(r'\b(?:auto mode on|esc to interrupt|shift\+tab to cycle)\b')
_NOISE_THOUGHT_RE = re.compile(r'\b(?:thought|thinking) for \d+s\b')
_NOISE_SESSION_END_RE = re.compile(r'^[✻\*─\-]{1,2}\s*(cooked|worked|sautéed|churned|waddling|baking)\b')
_NOISE_RATING_NUM_RE = re.compile(r'^\d+:\s*\w+')
_NOISE_RATING_OPT_RE = re.compile(r'\d+:\s*(?:bad|fine|good|dismiss)')
_TOOLCALL_PREFIX_RE = re.compile(r'^[A-Z][\w\s]*\(')
_NUMBERED_ITEM_RE = re.compile(r'\d+\.?\s')
_USERNAME_PREFIX_RE = re.compile(r'^(\w+):\s')
_MENU_ITEM_RE = re.compile(r'^(\d+)[\.\)]\s+(.+)$')
_MENU_END_RE = re.compile(r'esc|cancel|tab|enter', re.I)
_MENU_ACTION_RE = re.compile(
    r'Action Required|Would you like|Do you want|approval|approve|permission', re.I)


class SessionSlot:
    """One session registered with the bridge."""

    def __init__(self, sid: str, label: str, write_fn, index: int, peek_fn=None,
                 prepare_fn=None, cmd: str = ""):
        self.sid = sid
        self.label = label
        self.cmd = cmd  # session launch command — AI-vs-shell gate for delivery verify
        self.write_fn = write_fn
        self.peek_fn = peek_fn  # returns recent PTY bytes (last ~1KB ring buffer)
        self.prepare_fn = prepare_fn  # readies the pane for input (exits copy-mode)
        self.index = index
        self.output_lock = threading.Lock()
        # Serializes PTY input writes so concurrent sends (a paste TG split
        # into several messages, or rapid back-to-back messages) can't
        # interleave into one mangled input buffer.
        self.write_lock = threading.Lock()
        self.last_output_time = 0
        self.first_output_time = 0
        self.sent_texts = []
        self.has_user_msg = False
        self.pending_menu = False  # True if last extract found a menu prompt
        self.pending_menu_options = []  # [{"num": "1", "text": "..."}]
        self.last_signal = ""  # last [[SF:...]] state we already pushed (dedup)
        self.awaiting_response = False  # True between user msg and first AI response extraction
        self.pending_raw = ""
        self.expect_marker = False
        self.reply_start_marker = ""
        self.reply_end_marker = ""
        self.marker_prompt = ""         # injected wrapper instruction (to exclude its echo)
        self.last_extraction_ts = 0.0   # time of last successful response extraction
        # Throttle for sendChatAction. TG keeps the typing bubble alive ~5s,
        # so 4s pacing keeps it visible without burning 10× the API calls the
        # old 0.5s flush-tick rhythm produced.
        self.last_typing_ts = 0.0
        # Last AI reply we extracted + sent. Kept so `sfctl history-audit`
        # can diff "what we believe the reply was" vs "what get_clean_history
        # returns to the scroll-up overlay" — the previous fix-by-guesswork
        # cycle (v0.11.60) was wrong precisely because we never measured.
        self.last_extracted_text = ""
        # Rolling window of recent extractions (newest last). Cap small —
        # this is for audit/debug only, not a transcript. Each entry:
        # (ts: float, text: str).
        self.recent_extractions = []
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
        # history 由 3000 降至 800：兼顧長回應擷取與 per-tab 記憶體/掃描成本（撐 10+ tab）
        self.screen = pyte.HistoryScreen(200, 50, history=800)
        self.stream = pyte.Stream(self.screen)
        self._history_offset = 0  # tracks processed history lines
        self.sent_responses = {"Understood.", "Understood"}  # pre-filter system acks
        # Dirty flag for the periodic slow-tick scan (auto-compact). Set by
        # feed_output on every PTY chunk, cleared after a settled screen scan.
        # The Claude token gauge only changes when the session produces output,
        # so a slot with no new bytes needs no re-render — this lets idle slots
        # skip the expensive pyte screen.display rebuild entirely. Starts True
        # so the first scan runs.
        self.scan_dirty = True
        # screen.display cache. pyte's `display` is a property that re-renders
        # every row (cols × rows string build) on each access — the measured
        # flush-loop hotspot. We bump `_feed_gen` on every PTY chunk and cache
        # the rendered rows against it, so multiple reads of an unchanged screen
        # (auto-compact tail + extract in the same tick, repeated menu scans)
        # pay the render once.
        self._feed_gen = 0
        self._display_cache = None
        self._display_cache_gen = -1


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
        self._last_prune_ts = 0.0

        # Voice Apply-gate: transcribed voice waits for an inline Apply tap
        # before being forwarded to the session. token -> {text, user_id, chat_id}
        self._pending_voice = {}
        self._voice_seq = 0

        # ── Perf instrumentation (item 0) ──
        # Cumulative time/count per _flush_loop phase; a 60s summary line is
        # written to the bridge log when settings.perf_debug is on. Left in
        # permanently so future regressions can be re-measured with one flag.
        self._perf = {}                 # name -> [total_seconds, call_count]
        self._perf_enabled = False      # refreshed once per 60s window
        self._perf_window_start = 0.0
        self._perf_ticks = 0

        # ── Adaptive tick wake (item 3) ──
        # feed_output sets this Event so an idle (slow-tick) flush loop wakes
        # immediately when new PTY output lands, instead of waiting out the
        # widened sleep interval.
        self._flush_wake = threading.Event()

    # ── Perf instrumentation helpers ──

    def _perf_t(self):
        """Return a monotonic start stamp when perf_debug is on, else None."""
        return time.monotonic() if self._perf_enabled else None

    def _perf_end(self, name: str, t0):
        """Accumulate elapsed time for phase `name` (no-op when t0 is None)."""
        if t0 is None:
            return
        dt = time.monotonic() - t0
        b = self._perf.get(name)
        if b is None:
            self._perf[name] = [dt, 1]
        else:
            b[0] += dt
            b[1] += 1

    def _perf_maybe_emit(self):
        """Once per 60s: refresh the perf_debug flag and, if on, write a
        summary line (per-phase total ms + call count + mean µs) to the log."""
        now = time.monotonic()
        if self._perf_window_start == 0.0:
            self._perf_window_start = now
            try:
                self._perf_enabled = bool(_read_settings().get("perf_debug", False))
            except Exception:
                self._perf_enabled = False
            return
        if now - self._perf_window_start < 60.0:
            return
        window = now - self._perf_window_start
        if self._perf_enabled and self._perf:
            parts = []
            for name, (tot, cnt) in sorted(
                    self._perf.items(), key=lambda kv: kv[1][0], reverse=True):
                mean_us = (tot / cnt * 1e6) if cnt else 0.0
                parts.append(f"{name}={tot*1e3:.1f}ms/{cnt}x({mean_us:.0f}µs)")
            _blog(f"[perf] window={window:.0f}s ticks={self._perf_ticks} slots={len(self.slots)} "
                  + " ".join(parts) + "\n")
        # Reset window + refresh flag for the next interval.
        self._perf.clear()
        self._perf_ticks = 0
        self._perf_window_start = now
        try:
            self._perf_enabled = bool(_read_settings().get("perf_debug", False))
        except Exception:
            self._perf_enabled = False

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

    def register_session(self, sid: str, label: str, write_fn, peek_fn=None,
                         prepare_fn=None, cmd: str = ""):
        """Register a session tab with the bridge."""
        with self._slots_lock:
            if sid in self.slots:
                self.slots[sid].label = label
                self.slots[sid].write_fn = write_fn
                if peek_fn:
                    self.slots[sid].peek_fn = peek_fn
                if prepare_fn:
                    self.slots[sid].prepare_fn = prepare_fn
                if cmd:
                    self.slots[sid].cmd = cmd
                return
            idx = len(self._slot_order) + 1
            self.slots[sid] = SessionSlot(sid, label, write_fn, idx,
                                          peek_fn=peek_fn, prepare_fn=prepare_fn,
                                          cmd=cmd)
            self._slot_order.append(sid)

    def unregister_session(self, sid: str):
        """Remove a session from the bridge."""
        with self._slots_lock:
            self._remove_slots_locked([sid])

    def _remove_slots_locked(self, sids):
        """Remove slots while self._slots_lock is held."""
        removed = False
        for sid in sids:
            if sid in self.slots:
                self.slots.pop(sid, None)
                removed = True
            if sid in self._slot_order:
                self._slot_order.remove(sid)
                removed = True
        if not removed:
            return
        for i, s in enumerate(self._slot_order):
            if s in self.slots:
                self.slots[s].index = i + 1
        for uid, active_sid in list(self._user_active.items()):
            if active_sid in self.slots:
                continue
            if self._slot_order:
                self._user_active[uid] = self._slot_order[0]
            else:
                del self._user_active[uid]
        default = getattr(self, '_default_active_sid', None)
        if default and default not in self.slots:
            self._default_active_sid = self._slot_order[0] if self._slot_order else ""

    def _prune_stale_slots(self, force: bool = False):
        """Drop bridge slots that main.py no longer considers alive/bridged."""
        now = time.time()
        if not force and now - self._last_prune_ts < 5.0:
            return
        self._last_prune_ts = now
        result = self._sfctl_call("list", timeout=1.2)
        if not result.get("success"):
            return
        sessions = ((result.get("details") or {}).get("sessions") or [])
        live = {
            s.get("sid") for s in sessions
            if s.get("sid") and s.get("alive") and s.get("bridge_enabled", True)
        }
        known = {s.get("sid") for s in sessions if s.get("sid")}
        with self._slots_lock:
            stale = [
                sid for sid in self._slot_order
                if sid not in live and (sid in known or sessions)
            ]
            if stale:
                self._remove_slots_locked(stale)

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
        self._prune_stale_slots()
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
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        self._emit_status({"state": "stopped"})

    def _set_bot_commands(self):
        """Register slash commands with Telegram.

        Order: numbered session switchers FIRST (v0.11.57 — Howard mostly
        opens the picker to swap sessions, so /1 /2 ... should be the
        thumb-reachable top of the menu). Generic ops follow.

        Menu trimmed (v0.11.55): /help, /pause, /resume, /reload removed from
        the visible menu — Howard reported they cluttered the picker without
        being used. Their handlers stay (typed by hand or from old shortcuts
        they still respond), they just aren't suggested.
        """
        commands = []
        # Numbered session switchers FIRST — most-used action on mobile TG
        with self._slots_lock:
            for sid in self._slot_order:
                slot = self.slots[sid]
                commands.append({
                    "command": str(slot.index),
                    "description": f"Switch to {slot.label}",
                })
        # Generic ops after the session list
        commands.extend([
            {"command": "fetch", "description": "Fetch latest AI reply"},
            {"command": "usage", "description": "Current tab AI usage (水位)"},
            {"command": "list", "description": "List sessions + bridge state"},
            {"command": "restart", "description": "Full app restart (sessions preserved)"},
            {"command": "update", "description": "Check & apply ShellFrame updates"},
            {"command": "new", "description": "New session (default: claude)"},
            {"command": "close", "description": "Close current session (with confirm)"},
        ])
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

    def _signal_desktop_notify(self, slot, state: str, reason: str = ""):
        """Post a macOS banner for an explicit agent signal (GREEN/RED/YELLOW)
        so Howard doesn't have to watch Telegram — the desktop pops『做完了／
        要我決策／卡住』. Reuses the osascript path from _maybe_notify_completion;
        unlike completion banners this fires even when ShellFrame is frontmost
        (Howard may be on a different tab) but still respects the
        completion_notifications toggle. Called once per state transition
        (caller dedups via slot.last_signal). macOS only."""
        if _sys.platform != "darwin":
            return
        settings = _read_settings()
        if not settings.get("completion_notifications", True):
            return
        label = (slot.label or slot.sid or "session").replace('"', "'")
        reason = (reason or "").replace('"', "'")
        if state == "GREEN":
            title, body = f"✅ {label} 完成", "任務完成，可回收"
        elif state == "RED":
            title, body = f"🔴 {label} 需要你決策", "點開選單做決定"
        elif state == "YELLOW":
            title, body = f"🟡 {label} 卡住", (reason or "等待外部條件")
        else:
            return
        try:
            import subprocess as _sp
            script = (
                f'display notification "{body}" '
                f'with title "{title}" '
                f'sound name "Glass"'
            )
            _sp.Popen(
                ["osascript", "-e", script],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
        except Exception as e:
            _blog(f"[signal-notify] failed: {e}\n")

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
        # Scan the last few rendered rows (status bar lives at the bottom).
        # Screen is settled here (guards above ensure no in-flight output), so
        # this is a real scan — clear the dirty flag so we don't re-render an
        # unchanged screen every 2s while the slot sits idle. feed_output
        # re-arms it on the next PTY chunk.
        _t_disp = self._perf_t()
        try:
            tail = '\n'.join(self._slot_display(slot)[-8:])
        except Exception:
            return
        finally:
            self._perf_end("screen_display", _t_disp)
        slot.scan_dirty = False
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
        """Send typing indicator to users watching this session.

        Throttled to 4s per slot (TG auto-clears the bubble at ~5s; the old
        unthrottled 0.5s flush-tick rhythm sent 10× the calls needed). Each
        recipient gets its own thread with a short 3s timeout so one slow
        chat can't stack latency onto the rest, and so the caller (flush
        loop / feed_output) never blocks waiting on TG."""
        slot = self.slots.get(sid)
        if not slot:
            return
        now = time.time()
        if now - slot.last_typing_ts < 4.0:
            return
        slot.last_typing_ts = now
        token = self.config.bot_token
        for uid, active_sid in list(self._user_active.items()):
            if active_sid != sid:
                continue
            chat_id = self._user_chat.get(uid)
            if not chat_id:
                continue
            threading.Thread(
                target=tg_api,
                args=(token, "sendChatAction",
                      {"chat_id": chat_id, "action": "typing"}),
                kwargs={"timeout": 3},
                daemon=True,
            ).start()

    # Process owners of on-screen windows that indicate a modal / permission
    # dialog is blocking foreground work. Checked before firing stall warnings
    # so we don't cry wolf on long-running AI tasks.
    _POPUP_OWNERS = frozenset({
        "UserNotificationCenter",   # TCC permission dialogs (Sonoma+)
        "CoreServicesUIAgent",      # quarantine / "are you sure you want to open" / auth
        "SecurityAgent",            # admin password / keychain prompts
        "universalAccessAuthWarn",  # Accessibility prompts
        # loginwindow deliberately excluded: it's always running and frequently
        # owns transparent/system-management windows during normal operation
        # (sleep/wake transitions, screen-lock manager, Touch ID prep), so
        # CGWindowList kCGWindowListOptionOnScreenOnly matches it during any
        # long Claude response → false "popup detected (loginwindow)" alarm
        # whenever the model thinks for >25s. Real lock-screen blocking can't
        # be dismissed remotely anyway, so detecting it has no upside.
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
            slot.pending_raw += raw_text
            if len(slot.pending_raw) > 120000:
                slot.pending_raw = slot.pending_raw[-120000:]
            now_ts = time.time()
            slot.last_output_time = now_ts
            slot.last_chunk_ts = now_ts  # for stall detection (not reset by flush)
            slot.scan_dirty = True       # new bytes → allow next slow-tick scan
            slot._feed_gen += 1          # invalidate the cached screen.display
            if was_empty or slot.first_output_time == 0:
                slot.first_output_time = now_ts
        # Wake the flush loop if it widened its sleep while everything was idle
        # (adaptive tick) so new output is picked up without waiting it out.
        self._flush_wake.set()
        if was_empty and slot.awaiting_response:
            threading.Thread(target=self._send_typing, args=(sid,), daemon=True).start()

    def _slot_display(self, slot):
        """Return slot.screen.display (list of rendered rows), cached against
        the slot's feed generation. Repeated reads of an unchanged screen skip
        pyte's full re-render. Best-effort: a concurrent feed just misses the
        cache, never corrupts (matches the existing lock-free display reads)."""
        gen = slot._feed_gen
        cache = slot._display_cache
        if cache is not None and slot._display_cache_gen == gen:
            return cache
        disp = list(slot.screen.display)
        slot._display_cache = disp
        slot._display_cache_gen = gen
        return disp

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
        if _TOOLCALL_PREFIX_RE.match(text):
            return True
        # Codex style: "Searching the web", "Searched xxx"
        if text.startswith(('Searching ', 'Searched ')):
            return True
        # Tool result prefix
        if text.startswith('⎿'):
            return True
        return False

    @staticmethod
    def _ai_marker_prefix(line: str) -> str:
        """Return the AI marker only when it starts at column 0.

        Codex echoes TG prompts as a wrapped terminal prompt. Continuation
        lines are indented, so bullet-list items from ShellFrame's TG
        preamble look like "  • `sfctl reload` ..." after rendering. If we
        strip indentation before marker detection, those prompt bullets are
        misread as assistant replies and the bridge forwards the preamble
        plus tool/status noise back to Telegram.
        """
        if line != line.lstrip():
            return ""
        for marker in TelegramBridge.AI_MARKERS:
            if line.startswith(marker):
                return marker
        return ""

    @staticmethod
    def _is_bridge_noise_line(text: str) -> bool:
        """Drop ShellFrame/TG prompt echoes and Codex TUI status/tool lines."""
        s = (text or "").strip()
        if not s:
            return True
        lower = s.lower()
        if (
            lower.startswith("[tg] replying to telegram mobile")
            or lower.startswith("you can self-modify shellframe")
            or lower.startswith("asked. apply changes with:")
            or lower.startswith("straightforward asks")
            or lower.startswith("bump `version.json`")
            or "hot-reload bridge_telegram.py" in lower
            or "full restart for main.py" in lower
        ):
            return True
        if _NOISE_TOOLCALL_RE.match(s):
            return True
        if _NOISE_LINES_EXPAND_RE.match(lower):
            return True
        if _NOISE_MODE_RE.search(lower):
            return True
        if _NOISE_THOUGHT_RE.search(lower):
            return True
        if s.startswith(('└', '╰', '⎿')):
            return True
        # Claude Code session-end UI: "✻ Cooked for Xs", "─ Worked for Xs"
        if _NOISE_SESSION_END_RE.match(lower):
            return True
        # Claude Code rating prompt: "How is Claude doing this session?"
        if 'how is claude doing this session' in lower:
            return True
        # Rating options line: "1: Bad    2: Fine    3: Good    0: Dismiss"
        if _NOISE_RATING_NUM_RE.match(s) and _NOISE_RATING_OPT_RE.search(lower):
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
        # Each history line is a StaticDefaultDict mapping col -> Char.
        # 只在「真的有新 history 行」時才走訪（用 islice 取 tail，不再每次把整個
        # deque materialize 成 list）；多數 tick 螢幕內滾動、history 沒增長 → 直接跳過。
        htop = slot.screen.history.top
        hlen = len(htop)
        if slot._history_offset > hlen:
            slot._history_offset = 0  # history 被 deque maxlen 截斷 → 重置
        if slot._history_offset < hlen:
            cols = slot.screen.columns
            for hist_line in itertools.islice(htop, slot._history_offset, hlen):
                text = "".join(hist_line[col].data for col in range(cols)).rstrip()
                all_lines.append(text)
            slot._history_offset = hlen

        # Current screen display
        _t_disp = self._perf_t()
        for line in self._slot_display(slot):
            all_lines.append(line.rstrip())
        self._perf_end("screen_display", _t_disp)

        # Collect response blocks: list of list-of-lines
        blocks = []
        current_block = None

        for line in all_lines:
            stripped = line.rstrip().strip()

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
                if current_block is not None and _NUMBERED_ITEM_RE.match(after_prompt):
                    # This is a numbered menu item — include in current block
                    current_block.append(after_prompt)
                else:
                    if current_block is not None:
                        blocks.append(current_block)
                        current_block = None
                continue

            # Check for AI response marker — starts a new block
            marker_hit = False
            marker = self._ai_marker_prefix(line.rstrip())
            if marker:
                # If we were already collecting, save that block first
                if current_block is not None:
                    blocks.append(current_block)
                current_block = [stripped[len(marker):].strip()]
                marker_hit = True

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
                self._is_bridge_noise_line(l) or
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
                m = _USERNAME_PREFIX_RE.match(sent)
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
            slot.pending_menu_options = []

        return new_texts

    @staticmethod
    def _marker_spans(clean_raw: str, start_m: str, end_m: str):
        """Return [(start_idx, end_idx, inner_text)] for every start→end pair in
        order. A trailing start with no matching end yields end_idx=-1 (an
        in-progress reply still streaming).

        Each end is paired with the NEAREST preceding start (tightest block),
        not the first one seen. A TUI repaint can re-emit a fresh start marker
        while an earlier block is still open in the linearized stream; greedy
        first-start→end pairing would then swallow everything in between
        (duplicate scroll frames, the composer footer). Pairing the last start
        before each end keeps the span to the actual reply."""
        spans = []
        i = 0
        n = len(start_m)
        m = len(end_m)
        while True:
            e = clean_raw.find(end_m, i)
            if e < 0:
                # No more ends. A start after the last consumed end is an
                # in-progress (still-streaming) reply.
                s = clean_raw.find(start_m, i)
                if s >= 0:
                    spans.append((s, -1, ""))
                break
            s = clean_raw.rfind(start_m, i, e)
            if s < 0:
                # Stray end with no preceding start in range — skip past it.
                i = e + m
                continue
            spans.append((s, e, clean_raw[s + n:e]))
            i = e + m
        return spans

    def _pick_marker_reply(self, slot, allow_inprogress: bool):
        """Choose the real marked reply from the raw PTY buffer, robust across
        streaming repaints and the wrapper instruction's own echoed example.

        Collects ALL [[start]]…[[end]] pairs, drops any whose content is part of
        the injected instruction text (the echoed "{start} 和 {end}" example that
        used to leak as a bare 「和」), and returns the LAST complete real reply.
        Returns (reply, has_open) where has_open means a newer reply is still
        streaming (unclosed start) — callers wait for it unless forcing.
        """
        if not slot.expect_marker or not slot.reply_start_marker or not slot.reply_end_marker:
            return "", False
        raw = slot.pending_raw
        if slot.peek_fn:
            try:
                raw += "\n" + (slot.peek_fn() or "")
            except Exception:
                pass
        clean_raw = strip_ansi(raw, sent_texts=[])
        spans = self._marker_spans(
            clean_raw, slot.reply_start_marker, slot.reply_end_marker)
        has_open = any(e < 0 for _, e, _ in spans)
        instr_n = (getattr(slot, "marker_prompt", "") or "").replace(" ", "")
        candidates = []
        for _, e, inner in spans:
            if e < 0:
                continue
            cleaned = clean_mobile_marker_response(inner)
            if not cleaned:
                continue
            # Drop the wrapper-instruction echo: its inner span is part of the
            # instruction prose (e.g. bare 「和」). Real replies are never a
            # substring of the instruction we injected.
            if instr_n and cleaned.replace(" ", "") in instr_n:
                continue
            candidates.append(cleaned)
        if not candidates:
            return "", has_open
        return candidates[-1], has_open

    def _extract_marked_mobile_reply(self, slot) -> str:
        """Extract the marked mobile reply; wait if a newer reply is streaming."""
        reply, has_open = self._pick_marker_reply(slot, allow_inprogress=False)
        # A newer reply is still streaming (unclosed start) → wait for it so we
        # forward the complete version, not a half-painted one.
        if has_open:
            return ""
        return reply

    def _extract_marked_mobile_reply_force(self, slot) -> str:
        """Force-extract the last complete marked reply, ignoring the
        still-streaming guard. Used as fallback after 30s."""
        reply, _ = self._pick_marker_reply(slot, allow_inprogress=True)
        return reply

    # Explicit agent signal marker — a worker prints one of these on its own
    # line to declare this tab's state. Tolerates leading bullet/cursor glyphs
    # (⏺ ❯ › • * -) that Claude/Codex prepend to the first line of a reply.
    # Line-anchored so the wrapper instructions (which mention the markers
    # inline inside prose) never match.
    _SIGNAL_RE = re.compile(
        r'^[\s>❯›⏺•*\-]*\[\[\s*SF\s*:\s*(WORKING|GREEN|RED|YELLOW)\s*'
        r'(?::\s*([^\]]*?))?\s*\]\]\s*$',
        re.IGNORECASE)

    def _detect_and_fire_signal(self, slot, new_lines):
        """Detect a [[SF:STATE]] transition in freshly extracted lines and,
        once per transition, fire its notifications: a macOS desktop banner
        (GREEN/RED/YELLOW) and — for GREEN/YELLOW — a Telegram banner broadcast
        to every known chat. RED's TG side is the existing numbered-menu push.

        Broadcasts rather than routing by active tab because a master-delegated
        worker has no active-chat mapping (has_user_msg stays False) — yet its
        「done / stuck」signal is exactly what Howard wants pushed proactively.
        Returns new_lines with the raw marker line(s) stripped out."""
        sig_state, sig_reason, kept = self._detect_signal_in_lines(new_lines)
        if sig_state and sig_state != getattr(slot, "last_signal", ""):
            slot.last_signal = sig_state
            _blog(f"[signal] {slot.sid} state={sig_state} reason={sig_reason!r}\n")
            # Surface to local HTTP API clients (e.g. OpenClaw) so they can poll
            # GET /events and respond. No-op when the API server is disabled.
            try:
                import api_server
                api_server.EVENT_BUS.push(
                    sid=slot.sid,
                    label=getattr(slot, "label", slot.sid),
                    state=sig_state,
                    reason=sig_reason,
                )
            except Exception as e:
                _blog(f"[signal-api] {slot.sid} failed: {e}\n")
            try:
                self._signal_desktop_notify(slot, sig_state, sig_reason)
            except Exception as e:
                _blog(f"[signal-notify] {slot.sid} failed: {e}\n")
            banner = ""
            if sig_state == "GREEN":
                banner = f"✅ {slot.label} 已完成（可回收）"
            elif sig_state == "YELLOW":
                banner = (f"🟡 {slot.label} 卡住"
                          + (f"：{sig_reason}" if sig_reason else ""))
            if banner:
                for chat_id in set(self._user_chat.values()):
                    try:
                        r = tg_api(self.config.bot_token, "sendMessage",
                                   {"chat_id": chat_id, "text": banner})
                        _blog(f"[signal-tg] {slot.sid} {sig_state} "
                              f"chat={chat_id} ok={r.get('ok')}\n")
                    except Exception as e:
                        _blog(f"[signal-tg] {slot.sid} failed: {e}\n")
        return kept

    def _detect_signal_in_lines(self, lines):
        """Scan a list of freshly extracted output lines for [[SF:STATE]] /
        [[SF:STATE:reason]] markers. Returns (state_upper, reason, kept_lines)
        where kept_lines is the input with all marker-only lines removed (so the
        raw marker is never forwarded to TG as noise). The LAST marker wins —
        WORKING is printed at turn start, GREEN/RED/YELLOW at turn end."""
        state, reason = "", ""
        kept = []
        for line in lines:
            m = self._SIGNAL_RE.match(line)
            if m:
                state = m.group(1).upper()
                reason = (m.group(2) or "").strip()
            else:
                kept.append(line)
        return state, reason, kept

    # Task-board markers (experimental). Agents maintain todo/認領 via inject:
    #   [[SF:TASK:add|title=接 webhook 線|difficulty=medium|notes=...]]
    #   [[SF:TASK:claim|id=ab12cd34]]                  → assignee=<tab label>, in_progress
    #   [[SF:TASK:update|id=ab12cd34|status=done]]
    #   [[SF:TASK:done|id=ab12cd34]]
    #   [[SF:TASK:remove|id=ab12cd34]]
    _BOARD_RE = re.compile(
        r'^[\s>❯›⏺•*\-]*\[\[\s*SF\s*:\s*TASK\s*:\s*([^\]]*?)\s*\]\]\s*$',
        re.IGNORECASE)

    @staticmethod
    def _parse_board_marker(body: str):
        """Parse '<action>|key=value|key=value' → (action, {kv}). None if no action."""
        parts = [p.strip() for p in body.split("|") if p.strip()]
        if not parts:
            return None, {}
        action = parts[0].lower()
        kv = {}
        for p in parts[1:]:
            if "=" in p:
                k, _, v = p.partition("=")
                kv[k.strip().lower()] = v.strip()
        return action, kv

    def _detect_and_apply_board(self, slot, new_lines):
        """Scan freshly extracted lines for [[SF:TASK:...]] markers, apply them
        to the shared board store, and return new_lines with marker lines
        stripped (never forwarded to TG as noise). Gated by the experimental
        flag; a no-op otherwise."""
        if board is None or not new_lines:
            return new_lines
        try:
            if not (_read_settings().get("experimental_board", False)):
                return new_lines
        except Exception:
            return new_lines
        kept = []
        actor = getattr(slot, "label", None) or getattr(slot, "sid", "") or "unassigned"
        for line in new_lines:
            m = self._BOARD_RE.match(line)
            if not m:
                kept.append(line)
                continue
            action, kv = self._parse_board_marker(m.group(1))
            try:
                if action == "add":
                    t = board.add_task(
                        kv.get("title", ""),
                        assignee=kv.get("assignee", "unassigned"),
                        status=kv.get("status", "todo"),
                        difficulty=kv.get("difficulty", "medium"),
                        notes=kv.get("notes", ""))
                    _blog(f"[board] {slot.sid} add {t['id']} {t['title']!r}\n")
                elif action == "claim":
                    board.update_task(kv.get("id", ""),
                                      assignee=kv.get("assignee", actor),
                                      status=kv.get("status", "in_progress"))
                    _blog(f"[board] {slot.sid} claim {kv.get('id')} by {actor}\n")
                elif action == "update":
                    board.update_task(kv.get("id", ""), **{
                        k: v for k, v in kv.items() if k != "id"})
                    _blog(f"[board] {slot.sid} update {kv.get('id')} {kv}\n")
                elif action == "done":
                    board.update_task(kv.get("id", ""), status="done")
                    _blog(f"[board] {slot.sid} done {kv.get('id')}\n")
                elif action == "remove":
                    board.remove_task(kv.get("id", ""))
                    _blog(f"[board] {slot.sid} remove {kv.get('id')}\n")
                else:
                    _blog(f"[board] {slot.sid} unknown action {action!r}\n")
            except Exception as e:
                _blog(f"[board] {slot.sid} marker failed: {e}\n")
        return kept

    def _detect_menu_prompt(self, slot) -> str:
        """Detect a numbered menu prompt waiting for user input.
        Returns formatted menu string or empty if none found."""
        # Scan current screen for consecutive "N. xxx" lines (with optional ❯ cursor).
        # Cursor ❯ may be on any line, not just the first. Codex/Claude both
        # use this shape for approval / action-required prompts.
        lines = [l.rstrip() for l in self._slot_display(slot)]
        screen_text = "\n".join(lines)
        menu_lines = []
        menu_options = []
        for line in lines:
            # Strip leading ❯/› cursor markers and whitespace
            stripped = line.lstrip().lstrip('❯›').lstrip()
            # Match "N. xxx" or "N) xxx"
            m = _MENU_ITEM_RE.match(stripped)
            if m:
                num, label = m.group(1), m.group(2).strip()
                menu_lines.append(f"{num}. {label}")
                menu_options.append({"num": num, "text": label})
            elif menu_lines:
                # Hit end markers — stop collecting
                if 'Esc to cancel' in line or 'Tab to' in line or not line.strip():
                    if len(menu_lines) >= 2:
                        break
                # Non-menu line in middle — reset (false positive)
                if line.strip() and not _MENU_END_RE.search(line):
                    menu_lines = []
                    menu_options = []
        if len(menu_lines) >= 2:
            slot.pending_menu_options = menu_options
            is_action = _MENU_ACTION_RE.search(screen_text)
            title = "待決策：請選一個動作" if is_action else "請選一個選項"
            return f"❓ {title}\n" + "\n".join(menu_lines)
        slot.pending_menu_options = []
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
            marker = self._ai_marker_prefix(line.rstrip())
            if marker:
                if current_block is not None:
                    blocks.append(current_block)
                current_block = [stripped[len(marker):].strip()]
                marker_hit = True
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
                (l and all(c in '─━═│║╭╮╰╯┌┐└┘ |-_' for c in l)) or
                self._is_bridge_noise_line(l)
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
            if self._is_bridge_noise_line(stripped):
                continue
            # Skip prompt-only lines (› / ❯ alone)
            if stripped in ('›', '❯', '> ', '>'):
                continue
            # Skip Claude Code status bar markers
            if stripped.startswith(('? for shortcuts', 'esc to ', '⏵⏵ accept')):
                continue
            # Drop AI markers (• / ⏺) prefix and › prompt prefix to clean output
            marker = self._ai_marker_prefix(line.rstrip())
            if marker:
                stripped = stripped[len(marker):].strip()
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

    # Adaptive flush cadence (item 3)
    FLUSH_INTERVAL_BUSY = 0.5    # any slot has pending output / is awaiting
    FLUSH_INTERVAL_IDLE = 2.0    # everything quiet — widen to cut idle wakeups
    FLUSH_IDLE_TICKS = 6         # consecutive quiet ticks before widening (~3s)
    STALL_PERIOD = 2.0           # stall-watch + prune cadence (wall clock)
    COMPACT_PERIOD = 8.0         # auto-compact screen scan cadence (slow-moving %)

    def _flush_loop(self):
        """Extract new text from virtual terminal and send to TG."""
        tick = 0
        idle_streak = 0
        last_stall_mono = 0.0
        last_compact_mono = 0.0
        while self.active and not self._stop_event.is_set():
            # Adaptive sleep. When quiet for a few ticks, widen to 2s but let
            # feed_output's _flush_wake cut it short the instant new PTY output
            # lands (no added latency). While busy, sleep the fixed 0.5s — we do
            # NOT wait on the event there, or continuous output (which sets it
            # every chunk) would spin the loop with no delay. Clear once per tick
            # so a set during a busy sleep doesn't short-circuit the next idle wait.
            if idle_streak >= self.FLUSH_IDLE_TICKS:
                self._flush_wake.wait(timeout=self.FLUSH_INTERVAL_IDLE)
            else:
                time.sleep(self.FLUSH_INTERVAL_BUSY)
            self._flush_wake.clear()
            tick += 1
            self._perf_ticks += 1
            self._perf_maybe_emit()
            now_mono = time.monotonic()
            # Stall-watch + prune run on a wall-clock cadence (independent of the
            # now-variable sleep interval); auto-compact runs less often still —
            # the token gauge is slow-moving, so an 8s scan is plenty and it's
            # the sole remaining screen.display render on active tabs.
            stall_tick = (now_mono - last_stall_mono) >= self.STALL_PERIOD
            compact_tick = (now_mono - last_compact_mono) >= self.COMPACT_PERIOD
            if stall_tick:
                last_stall_mono = now_mono
            if compact_tick:
                last_compact_mono = now_mono
            slow_tick = stall_tick  # stall + prune share the 2s cadence
            if slow_tick:
                self._prune_stale_slots()
            with self._slots_lock:
                sids = list(self._slot_order)
            # Track whether anything needed attention this tick to drive the
            # adaptive interval. A slot with pending output or an awaited reply
            # counts as busy; all-quiet ticks accumulate toward widening.
            tick_busy = False

            # Stall detection runs first, outside the output_lock path below,
            # because a truly stalled slot has no output activity to flush.
            if slow_tick:
                _t_st = self._perf_t()
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
                self._perf_end("stall_detect", _t_st)

            # Claude auto-compact check — runs outside output_lock so the
            # scan doesn't contend with feed_output. 一個 regex 掃最後 ~8 行 /slot，
            # 降到每 8s 一次（auto-compact 屬慢變化）＋dirty-flag 過濾，是活躍 tab
            # 上唯一殘留的 screen.display render。
            if compact_tick:
                _t_ac = self._perf_t()
                for sid in sids:
                    slot = self.slots.get(sid)
                    if not slot:
                        continue
                    # Dirty-flag gate: a slot with no new output since its last
                    # settled scan can't have changed its token gauge — skip the
                    # pyte screen.display rebuild entirely (the idle CPU floor).
                    if not slot.scan_dirty:
                        continue
                    try:
                        self._maybe_auto_compact(slot)
                    except Exception as e:
                        _blog(f"[auto-compact] {sid} check failed: {e}\n")
                self._perf_end("auto_compact", _t_ac)

            for sid in sids:
                slot = self.slots.get(sid)
                if not slot:
                    continue

                # Refresh the TG typing indicator while the session is waiting
                # on a reply. Kept OUT of `slot.output_lock` so a slow/wedged
                # sendChatAction can never backpressure `feed_output` (PTY
                # ingest also takes that lock — sharing it with a 3-35s HTTPS
                # call was the actual root cause of "typing feels unstable").
                # `_send_typing` itself throttles to 4s and fires per-uid in
                # background threads, so this call returns ~immediately.
                if slot.awaiting_response:
                    self._send_typing(sid)
                    tick_busy = True

                with slot.output_lock:
                    if slot.last_output_time == 0:
                        continue
                    tick_busy = True  # pending output → stay on the fast cadence
                    if not slot.has_user_msg:
                        # Drain old content so it won't be re-extracted later
                        # when a TG message arrives. This advances _history_offset
                        # and marks existing AI blocks as "sent".
                        # Master-delegated workers live here (Howard never DM'd
                        # the tab), so this is the ONLY place their [[SF:...]]
                        # signals can be caught — run signal detection on the
                        # drained lines so done/stuck/decision still notifies.
                        now = time.time()
                        idle = now - slot.last_output_time
                        if idle >= 1.0:
                            _t_ex = self._perf_t()
                            drained = self._extract_new_text(slot)
                            self._perf_end("extract_new_text", _t_ex)
                            try:
                                _t_b = self._perf_t()
                                drained = self._detect_and_apply_board(slot, drained)
                                self._perf_end("detect_board", _t_b)
                                _t_s = self._perf_t()
                                self._detect_and_fire_signal(slot, drained)
                                self._perf_end("detect_signal", _t_s)
                            except Exception as e:
                                _blog(f"[signal] {sid} drain-detect failed: {e}\n")
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

                    if slot.expect_marker:
                        marked_reply = self._extract_marked_mobile_reply(slot)
                        if not marked_reply:
                            # Don't reset timer forever — after 30s force-extract
                            # by stripping tail guard (avoids permanent block when
                            # rating prompt or other noise confuses the tail check).
                            if total < 30.0:
                                slot.last_output_time = now
                                continue
                            marked_reply = self._extract_marked_mobile_reply_force(slot)
                        if not marked_reply:
                            slot.last_output_time = now
                            continue
                        # Drain pyte history so the same screen repaint is not
                        # re-extracted on a later mobile turn.
                        try:
                            self._extract_new_text(slot)
                        except Exception:
                            pass
                        new_lines = [marked_reply]
                        slot.sent_responses.add(marked_reply)
                        slot.pending_raw = ""
                        slot.expect_marker = False
                        slot.reply_start_marker = ""
                        slot.reply_end_marker = ""
                        slot.marker_prompt = ""
                        slot.has_user_msg = False
                    else:
                        # Extract new text via screen diff (only final changes)
                        _t_ex = self._perf_t()
                        new_lines = self._extract_new_text(slot)
                        self._perf_end("extract_new_text", _t_ex)
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
                        # Stash the extracted text for `sfctl history-audit`.
                        # This is the ground-truth "what the AI actually said"
                        # — anything Howard sees in scroll-up that contradicts
                        # this is a buffer-fidelity bug we can now measure.
                        extracted = '\n'.join(new_lines)
                        slot.last_extracted_text = extracted
                        slot.recent_extractions.append((now, extracted))
                        if len(slot.recent_extractions) > 5:
                            slot.recent_extractions = slot.recent_extractions[-5:]
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

                # Debug log — only when there's actually output to forward.
                # Previously dumped the full screen on every empty flush so the
                # 1MB log cap rotated every few minutes and real signals (poll
                # exceptions, conflicts, watchdog hits) got buried.
                if new_lines:
                    log_msg = (f"flush {sid}: new_lines={len(new_lines)} "
                               f"users={dict(self._user_active)} has_msg={slot.has_user_msg}\n")
                    for l in new_lines[:5]:
                        log_msg += f"  [{l}]\n"
                    _blog(log_msg)
                else:
                    continue

                # Apply [[SF:TASK:...]] board markers, then fire desktop + TG
                # banner on a [[SF:STATE]] transition; both strip their raw
                # marker lines out of the forwarded text.
                _t_b = self._perf_t()
                new_lines = self._detect_and_apply_board(slot, new_lines)
                self._perf_end("detect_board", _t_b)
                _t_s = self._perf_t()
                new_lines = self._detect_and_fire_signal(slot, new_lines)
                self._perf_end("detect_signal", _t_s)
                if not new_lines:
                    continue

                clean = '\n'.join(new_lines)
                is_menu_prompt = bool(
                    slot.pending_menu
                    and new_lines
                    and len(new_lines) == 1
                    and new_lines[0].startswith("❓ ")
                )

                # Detect file paths in response for TG file sending
                file_paths = self._extract_file_paths(clean)

                # Tag with session label
                prefix = f"[{slot.label}] " if len(self.slots) > 1 else ""
                msg = prefix + clean

                # Long replies are split into multiple TG messages (≤4096 cap),
                # never truncated. Menu prompts stay single (kept short by design).
                msg_parts = [msg] if is_menu_prompt else split_for_telegram(msg)

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
                    if is_menu_prompt:
                        self._send_choice_menu(chat_id, slot, msg)
                    else:
                        for part in msg_parts:
                            tg_api(self.config.bot_token, "sendMessage", {
                                "chat_id": chat_id,
                                "text": part,
                            })
                    # Send detected files as documents
                    for fp in file_paths:
                        self._send_tg_file(chat_id, fp)

            # Adaptive-cadence bookkeeping: grow the idle streak on fully-quiet
            # ticks, reset the moment any slot needs attention.
            idle_streak = 0 if tick_busy else (idle_streak + 1)

    def _send_choice_menu(self, chat_id: int, slot, text: str):
        """Send a detected CLI approval/menu prompt as Telegram inline buttons."""
        options = list(getattr(slot, 'pending_menu_options', []) or [])
        if not options:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": text + "\n\n回覆數字也可以。",
            })
            return
        keyboard = []
        row = []
        for opt in options[:9]:
            num = str(opt.get("num", "")).strip()
            label = str(opt.get("text", "")).strip()
            if not num:
                continue
            btn_text = f"{num}. {label}"
            if len(btn_text) > 48:
                btn_text = btn_text[:45] + "..."
            row.append({"text": btn_text, "callback_data": f"choice:{slot.sid}:{num}"})
            if len(row) == 1:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        tg_api(self.config.bot_token, "sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {"inline_keyboard": keyboard},
        })

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
        # Exponential backoff on exception path so the bridge recovers
        # quickly from transient wifi blips / TLS resets / sleep-wake events
        # instead of always waiting fixed 5s.
        backoff = 1.0
        BACKOFF_MAX = 15.0
        consecutive_errors = 0
        while self.active and not self._stop_event.is_set():
            try:
                result = tg_api(self.config.bot_token, "getUpdates", {
                    "offset": self._offset,
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query"],
                })
                if self._stop_event.is_set() or not self.active:
                    break
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
                # Successful round-trip — reset backoff if we'd been failing.
                if consecutive_errors:
                    _blog(f"[poll] recovered after {consecutive_errors} consecutive errors\n")
                    consecutive_errors = 0
                    backoff = 1.0
            except Exception as e:
                consecutive_errors += 1
                # Log every failure (was silent before — Howard reported
                # "feels unstable" with no log evidence to investigate).
                # Coalesce noisy repeats: log first 3 verbosely, then every
                # 10th, to avoid drowning the log if the network's truly down.
                if consecutive_errors <= 3 or consecutive_errors % 10 == 0:
                    _blog(f"[poll] exception #{consecutive_errors} "
                          f"({type(e).__name__}): {e} — sleeping {backoff:.1f}s\n")
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

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
        if backend == "remote_first":
            # Prefer the (stronger) remote provider, fall back to on-device
            # local whisper if the remote chain is unreachable/empty.
            text = self._transcribe_plugin(audio_path)
            if text:
                return text
            text = self._transcribe_remote(audio_path)
            if text:
                return text
            return self._transcribe_local(audio_path)
        # auto: plugin → local → remote
        text = self._transcribe_plugin(audio_path)
        if text:
            return text
        text = self._transcribe_local(audio_path)
        if text:
            return text
        return self._transcribe_remote(audio_path)

    # ── Voice transcript refinement (Typeless-style) ──
    # Raw STT output is spoken-language: filler words (嗯/那個/就是/這樣子),
    # repetitions, missing punctuation, and recognition errors. Instead of
    # forwarding that verbatim, run it through a local LLM that rewrites it into
    # the clean text the user *meant* — same intent, no summarizing, no answers.
    # Defaults to the LM Studio / Ollama OpenAI-compatible endpoint on :1234 so
    # it's zero-cost and stays on-device; falls back to the raw transcript on
    # any failure so a refine outage never drops the message.
    _REFINE_DEFAULT_URL = "http://127.0.0.1:1234/v1/chat/completions"
    _REFINE_SYS_CLEAN = (
        "你是語音輸入整理器。使用者剛用語音講了一段話，這是語音轉文字(STT)的逐字稿，"
        "可能有口語贅字(嗯、那個、就是、這樣子、然後)、重複、明顯的同音辨識錯字、缺標點、沒分段。"
        "請輸出整理後的版本：修正明顯辨識錯誤、去除口語贅字與重複、補上完整標點符號"
        "(，。、？！：「」)與適當分行，讓語意通順好讀。嚴格保留原意與所有具體資訊(數字、名稱、路徑、需求細節)，"
        "不要摘要、不要刪減內容、不要加入使用者沒講的東西、不要回答或執行其中的問題。"
        "只輸出整理後的文字本身，使用繁體中文，不要任何前言或解釋。"
    )
    _REFINE_SYS_SUMMARY = (
        "你是語音輸入整理器。使用者剛用語音講了一段話(STT 逐字稿)。"
        "先理解他的真實意圖，再輸出結構化的整理：用一句話點出重點，必要時用條列列出要點/需求/待辦。"
        "修正辨識錯字、去除口語贅字。保留所有具體資訊，不要加入沒講的內容、不要回答問題。"
        "只輸出整理後的文字，使用繁體中文，不要任何前言。"
    )

    def _refine_settings(self) -> dict:
        s = _read_settings()
        return {
            "enabled": s.get("voice_refine", True) is not False,
            "url": (s.get("voice_refine_url") or self._REFINE_DEFAULT_URL).rstrip("/"),
            "model": s.get("voice_refine_model") or "",
            "style": s.get("voice_refine_style") or "clean",
        }

    def _refine_pick_model(self, base_url: str) -> str:
        """Ask the OpenAI-compatible endpoint for a usable chat model id
        (skips embedding models). Returns '' if none/unreachable."""
        try:
            models_url = base_url.rsplit("/chat/completions", 1)[0] + "/models"
            req = urllib.request.Request(models_url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            for m in data.get("data", []):
                mid = m.get("id", "")
                low = mid.lower()
                # Skip non-chat models: embeddings, OCR, and vision-only ids
                # (e.g. deepseek-ocr) which can't do text refinement.
                if mid and not any(x in low for x in ("embed", "ocr", "vision", "-vl", "rerank")):
                    return mid
        except Exception:
            pass
        return ""

    def _refine_transcript(self, text: str) -> str:
        """Rewrite a raw STT transcript into clean intended text via a local
        LLM. Returns the original text unchanged on disable or any failure."""
        cfg = self._refine_settings()
        if not cfg["enabled"] or not (text or "").strip():
            return text
        model = cfg["model"] or self._refine_pick_model(cfg["url"])
        if not model:
            _blog("  refine skipped: no chat model at endpoint\n")
            return text
        system = self._REFINE_SYS_SUMMARY if cfg["style"] == "summary" else self._REFINE_SYS_CLEAN
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
            "stream": False,
        }).encode()
        try:
            req = urllib.request.Request(
                cfg["url"], data=payload,
                headers={"Content-Type": "application/json"})
            # 45s: a reasoning model (e.g. gpt-oss) cold-starts slowly on the
            # first call; the voice flow already showed「轉錄整理中…」so the
            # user is waiting. A timeout just falls back to the raw transcript.
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode())
            refined = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
            # Some local models echo a stray code fence or quotes — peel them.
            refined = re.sub(r'^```[\w]*\n?|\n?```$', '', refined).strip().strip('"').strip()
            if refined:
                _blog(f"  refine ok: {len(text)}→{len(refined)} chars via {model}\n")
                return refined
        except Exception as e:
            _blog(f"  refine failed ({model}): {e}\n")
        return text

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

        if data.startswith("vcancel:"):
            token = data.split(":", 1)[1]
            self._pending_voice.pop(token, None)
            tg_api(self.config.bot_token, "editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": "✕ 已取消（未送出）",
            })
            return

        if data.startswith("vapply:"):
            token = data.split(":", 1)[1]
            pending = self._pending_voice.pop(token, None)
            if not pending:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "⚠ 這則語音已失效（可能重啟過），請重錄。",
                })
                return
            self._user_chat[user_id] = chat_id
            preview = pending["text"][:120] + ('…' if len(pending["text"]) > 120 else '')
            tg_api(self.config.bot_token, "editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": f"✅ 已送出：{preview}",
            })
            # Reuse the full forward pipeline (preamble wrap, menu detect, _send
            # + delivery verify) by replaying the parked text as a normal message.
            synthetic = {"message": {
                "text": pending["text"],
                "from": {"id": pending["user_id"]},
                "chat": {"id": pending["chat_id"]},
            }}
            threading.Thread(
                target=self._handle_update, args=(synthetic,), daemon=True).start()
            return

        if data.startswith("choice:"):
            parts = data.split(":", 2)
            if len(parts) < 3:
                return
            sid, choice = parts[1], parts[2]
            slot = self.slots.get(sid)
            if not slot:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "Session already gone.",
                })
                return
            self._user_chat[user_id] = chat_id
            self._user_active[user_id] = sid
            self._default_active_sid = sid
            slot.pending_menu = False
            slot.pending_menu_options = []
            try:
                slot.write_fn(f"{choice}\r")
                slot.has_user_msg = True
                slot.awaiting_response = True
                slot.last_write_ts = time.time()
                slot.stall_warned = False
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"已送出選項 {choice} → [{slot.label}]",
                })
            except Exception as e:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"❌ Send failed: {e}",
                })
            self._save_state()
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
                refine_on = self._refine_settings()["enabled"]
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "🎙 轉錄整理中…" if refine_on else "🎙 轉錄中…",
                })
                transcribed = self._transcribe_voice(audio_path)
                if transcribed:
                    # Typeless-style: rewrite the raw STT into the clean text the
                    # user meant before forwarding. Falls back to raw on failure.
                    refined = self._refine_transcript(transcribed)
                    # Use refined text as the message text, append 🎙 prefix
                    if text:
                        fwd_text = text + " " + refined
                    else:
                        fwd_text = f"🎙 {refined}"
                    # Apply-gate: STT is imperfect, so don't auto-submit. Park the
                    # transcribed text and show inline Apply/Cancel — only forward
                    # to the session when the user taps ✅ Apply. Target session is
                    # resolved at Apply time so switching tabs meanwhile still works.
                    self._voice_seq += 1
                    token = str(self._voice_seq)
                    self._pending_voice[token] = {
                        "text": fwd_text, "user_id": user_id, "chat_id": chat_id,
                    }
                    body = f"🎙 {refined[:800]}{'…' if len(refined) > 800 else ''}"
                    if refined.strip() != transcribed.strip():
                        raw_preview = transcribed[:200] + ('…' if len(transcribed) > 200 else '')
                        body += f"\n\n原稿：{raw_preview}"
                    body += "\n\n送出到 session？"
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": body,
                        "reply_markup": {"inline_keyboard": [[
                            {"text": "✅ Apply", "callback_data": f"vapply:{token}"},
                            {"text": "✕ Cancel", "callback_data": f"vcancel:{token}"},
                        ]]},
                    })
                    return
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
            if cmd in ('list', 'status', 'pause', 'resume', 'start', 'help', 'reload', 'close', 'new', 'restart', 'update', 'update_now', 'fetch', 'usage', '水位', 'break', 'stop', 'esc', 'interrupt', '中斷', '打斷', 'voice', '語音') or cmd.isdigit():
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
                slot.pending_raw = ""
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

        if not init_prompt and master_turn_preamble_enabled() and is_master_label(slot.label):
            master_preamble = wrap_master_turn_input(forwarded)
            slot.sent_texts.append(master_preamble)
            if wrap_with_preamble and payload != forwarded:
                payload = payload.rsplit(forwarded, 1)[0] + master_preamble
            else:
                payload = master_preamble

        if wrap_with_preamble:
            marker_token = f"TG_REPLY_{uuid.uuid4().hex[:8]}"
            start_marker = f"[[{marker_token}]]"
            end_marker = f"[[/{marker_token}]]"
            marker_prompt = (
                f"最終要回 Telegram 的文字請放在 {start_marker} 和 {end_marker} 之間。"
                "標記外可以思考或操作，但手機只會收到標記內文字。"
            )
            if init_prompt:
                payload = init_prompt + "\n\n" + marker_prompt + "\n\n---\nUser's first message: " + forwarded
            else:
                payload = marker_prompt + "\n\n" + payload
            slot.sent_texts.append(marker_prompt)
            slot.expect_marker = True
            slot.reply_start_marker = start_marker
            slot.reply_end_marker = end_marker
            slot.marker_prompt = marker_prompt
        else:
            slot.expect_marker = False
            slot.reply_start_marker = ""
            slot.reply_end_marker = ""
            slot.marker_prompt = ""

        # Write text first, then Enter after a brief delay.
        # When show_tg_wrapper is on, prefix with a visible tag so the
        # local terminal operator can see that a wrapper was injected.
        show_wrapper = show_tg_wrapper()
        visible_payload = payload
        if wrap_with_preamble and show_wrapper and payload != forwarded:
            tag = "[SF-TG wrapper]"
            visible_payload = f"{tag}\n{payload}"
            slot.sent_texts.append(tag)
        elif wrap_with_preamble and not show_wrapper:
            visible_payload = payload

        def _send():
            # Serialize all PTY writes for this slot. Without this, a paste
            # that Telegram splits into several messages — or two rapid
            # messages — spawn concurrent _send threads whose write+Enter
            # interleave into one mangled buffer (malformed input / tool calls).
            notify_failed = False
            with slot.write_lock:
                # Ready the pane first: a session left in tmux copy-mode
                # (scrolled-back terminal) swallows pasted bytes entirely —
                # the exact "TG 敲了訊息但沒真的送進來" silent drop. The
                # output stall-watchdog can't catch it either, because TUI
                # spinner/clock redraws keep output flowing. prepare_fn
                # (installed by main.py) exits copy-mode when detected.
                if slot.prepare_fn:
                    try:
                        slot.prepare_fn()
                    except Exception:
                        pass
                # Busy guard: writing + Enter while Claude Code is mid-turn
                # makes it abort the in-flight turn with "[Request interrupted]"
                # and submit a mixed/empty buffer (this is the「貼文字變
                # preamble / User message: [Request interrupted]」bug). Wait
                # for the CLI to return to idle (no "esc to interrupt" footer)
                # before injecting. Bounded so a wedged session can't block forever.
                deadline = time.time() + 120.0
                while time.time() < deadline:
                    try:
                        recent = slot.peek_fn() if slot.peek_fn else ""
                    except Exception:
                        recent = ""
                    if not re.search(r'esc to interrupt', recent or "", re.I):
                        break
                    time.sleep(0.5)

                def _inject():
                    # Clear residue left in the input box (aborted turn,
                    # dismissed rating prompt, half-typed text) so the payload
                    # isn't appended to stale content.
                    slot.write_fn("\x15")  # Ctrl-U: kill input line
                    time.sleep(0.05)
                    # Bracketed paste: ingest the (often multi-line) payload
                    # atomically so embedded newlines don't prematurely submit
                    # partial input.
                    slot.write_fn("\x1b[200~" + visible_payload + "\x1b[201~")
                    time.sleep(0.3)
                    _blog(f"[send] {slot.sid} submit CR len={len(visible_payload)}\n")
                    slot.write_fn("\r")
                    time.sleep(0.6)
                    try:
                        after = slot.peek_fn() if slot.peek_fn else ""
                    except Exception:
                        after = ""
                    # Codex can occasionally keep focus on its pasted-content
                    # chip after the first CR. If the chip is still visible and
                    # the CLI did not start a turn, send LF as a conservative
                    # fallback.
                    if (
                        re.search(r'\[Pasted (?:Content|text)[^\]]*\]', after or "", re.I)
                        and not re.search(r'esc to interrupt', after or "", re.I)
                    ):
                        _blog(f"[send] {slot.sid} submit LF fallback after paste chip\n")
                        slot.write_fn("\n")

                inject_t0 = time.time()
                _inject()
                # ── Delivery verification + one retry (fallback 機制) ──
                # AI CLI tabs only: shells ECHO their input, so a slow quiet
                # command (make/ssh) leaves the payload text visible on
                # screen and would false-flag as "not delivered" → retry
                # would paste into the running process's stdin. AI CLIs
                # have a composer + turn footer, where the signals hold.
                # Positive confirmation only: a turn-start footer or an
                # extracted reply. If neither shows AND the payload tail is
                # still sitting on screen (composer residue), the submit
                # didn't land — recover the pane and retry once. Still stuck
                # → tell the TG user instead of dropping silently.
                if _detect_ai(getattr(slot, "cmd", "") or ""):
                    delivered, residue = self._verify_injection(
                        slot, visible_payload, inject_t0)
                    if not delivered and residue:
                        _blog(f"[send] {slot.sid} delivery unconfirmed + residue → retry\n")
                        if slot.prepare_fn:
                            try:
                                slot.prepare_fn()
                            except Exception:
                                pass
                        inject_t0 = time.time()
                        _inject()
                        delivered, residue = self._verify_injection(
                            slot, visible_payload, inject_t0)
                        if not delivered and residue:
                            _blog(f"[send] {slot.sid} delivery FAILED after retry\n")
                            notify_failed = True
            # Notify OUTSIDE write_lock — tg_api can block up to 35s and
            # holding the slot's write lock that long queues every
            # subsequent message for this tab behind a dead HTTPS call.
            if notify_failed:
                try:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": (f"⚠ 訊息可能沒送進「{slot.label}」：重試 1 次後"
                                 "仍未確認送出。原文還留在該分頁輸入框，"
                                 f"可回 /{slot.index} 查看狀態或直接重發。"),
                    })
                except Exception:
                    pass
        threading.Thread(target=_send, daemon=True).start()

    _INJECT_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07')

    def _verify_injection(self, slot, payload, injected_at, window=8.0):
        """(delivered, residue) — 送達驗證。

        delivered=True 只憑強訊號：turn 開始（esc to interrupt footer）或
        「這次注入之後」bridge 抽到新回覆（last_extraction_ts > injected_at
        —— 不能跟 slot.last_write_ts 比：extraction loop 抽到前一輪回覆時
        會把 last_write_ts 歸零，任何舊 extraction 都會假 delivered）。
        residue=True＝驗證窗結束時 payload 尾段仍掛在畫面上且無 turn 訊號
        —— 幾乎確定沒送進去，可安全重試（重試前 Ctrl-U 會清掉殘文，不會
        變成重複送出）。兩者皆 False＝不確定（極短回合／畫面已捲走）：
        不重試、不吵人，交給既有 stall watchdog。

        殘留取樣用 payload「最後一個非空行」而非跨行尾段——composer 摺疊
        顯示時畫面上只有最後一行，跨行取樣會漏判。"""
        tail = ""
        for ln in reversed((payload or "").splitlines()):
            ln_norm = re.sub(r"\s+", "", ln)
            if len(ln_norm) >= 6:
                tail = ln_norm[-24:]
                break
        if not tail:
            tail = re.sub(r"\s+", "", payload or "")[-24:]
        recent = ""
        t0 = time.time()
        while time.time() - t0 < window:
            try:
                recent = slot.peek_fn() if slot.peek_fn else ""
            except Exception:
                recent = ""
            if re.search(r"esc to interrupt", recent or "", re.I):
                return True, False
            if getattr(slot, "last_extraction_ts", 0.0) > injected_at:
                return True, False
            time.sleep(0.5)
        plain = self._INJECT_ANSI_RE.sub('', recent or "")
        residue = bool(tail) and tail in re.sub(r"\s+", "", plain)
        return False, residue

    def _handle_command(self, cmd: str, user_id: int, chat_id: int, text: str = ""):
        """Handle slash commands. `text` is the full message text (for argv parsing)."""

        if cmd in ("list", "status"):
            # /status is folded into /list — show bridge state header + sessions.
            self._prune_stale_slots(force=True)
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

        elif cmd in ("usage", "水位"):
            # Per-tab AI usage water-level. Resolve the active session, then
            # query main.py in a background thread (the codex/claude probe can
            # take several seconds) so the poll loop never blocks. Result goes
            # back to the TG user, never into the agent's conversation.
            active_sid = self.get_active_sid(user_id)
            if not active_sid or active_sid not in self.slots:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "No active session. Use /list to see available sessions.",
                })
                return

            def _do_usage(sid=active_sid, chat_id=chat_id):
                result = self._sfctl_call("usage", {"sid": sid}, timeout=30.0)
                text = result.get("message") or "用量查詢失敗"
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": text,
                })

            threading.Thread(target=_do_usage, daemon=True).start()

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

        elif cmd in ("voice", "語音"):
            # Voice refine control: show/switch the Typeless-style STT cleanup
            # model. `/voice` shows status + available models at the endpoint;
            # `/voice on|off` toggles; `/voice <model>` switches model.
            parts = (text or "").split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            cfg = self._refine_settings()
            if arg.lower() in ("on", "開"):
                _update_settings({"voice_refine": True})
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "🎙 語音整理：已開啟 ✅"})
                return
            if arg.lower() in ("off", "關"):
                _update_settings({"voice_refine": False})
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "🎙 語音整理：已關閉（送原始逐字稿）"})
                return
            if arg:
                # Treat any other arg as a model id to switch to
                _update_settings({"voice_refine_model": arg})
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": f"🎙 語音整理模型 → {arg}"})
                return
            # No arg: show current config + list models at the endpoint
            def _do_voice_status(cfg=cfg, chat_id=chat_id):
                models = []
                try:
                    models_url = cfg["url"].rsplit("/chat/completions", 1)[0] + "/models"
                    req = urllib.request.Request(models_url)
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        data = json.loads(resp.read().decode())
                    models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
                except Exception:
                    pass
                cur = cfg["model"] or "(自動挑選)"
                lines = [
                    "🎙 語音整理設定",
                    f"狀態：{'開 ✅' if cfg['enabled'] else '關'}",
                    f"模型：{cur}",
                    f"端點：{cfg['url']}",
                    "",
                ]
                if models:
                    lines.append("可用模型：")
                    for m in models:
                        lines.append(f"  {'▸' if m == cfg['model'] else '·'} {m}")
                    lines.append("")
                    lines.append("切換：/voice <模型名>　開關：/voice on|off")
                else:
                    lines.append("⚠ 端點連不到或沒模型")
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "\n".join(lines)})
            threading.Thread(target=_do_voice_status, daemon=True).start()

        elif cmd in ("break", "stop", "esc", "interrupt", "中斷", "打斷"):
            # Remote interrupt — press ESC in the active tab to abort the AI's
            # current turn. Claude Code / Codex both interrupt on ESC.
            # prepare_fn first (exit copy-mode) so the ESC lands in the CLI,
            # not on tmux's copy-mode. Single ESC only: a second ESC in Claude
            # opens history navigation instead of interrupting.
            active_sid = self.get_active_sid(user_id)
            if not active_sid or active_sid not in self.slots:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "No active session.",
                })
                return
            slot = self.slots[active_sid]

            def _do_break(slot=slot, chat_id=chat_id):
                try:
                    with slot.write_lock:
                        if slot.prepare_fn:
                            try:
                                slot.prepare_fn()
                            except Exception:
                                pass
                        slot.write_fn("\x1b")
                    _blog(f"[break] {slot.sid} sent ESC (interrupt)\n")
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"⎋ 已送 ESC 中斷「{slot.label}」（/{slot.index}）",
                    })
                except Exception as e:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": f"❌ 中斷失敗: {e}",
                    })
            threading.Thread(target=_do_break, daemon=True).start()

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
            # Don't pin (Howard v0.11.58: the pinned banner in chat is noisy
            # and rarely useful — the message itself is enough; user scrolls
            # if they need to find it).
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": msg_text,
            })

        elif cmd in ("start", "help"):
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": (
                    "ShellFrame Bridge\n\n"
                    "Sessions:\n"
                    "  /list — sessions + bridge state (with last-response preview)\n"
                    "  /fetch — fetch latest AI reply\n"
                    "  /new [cmd] — new session (default: claude)\n"
                    "  /close — close current session (with confirm)\n"
                    "  /1, /2, … — switch session\n"
                    "  /break — 中斷目前分頁 AI（送 ESC；alias /stop /中斷）\n"
                    "  /voice — 語音整理設定/切模型（/voice <模型>、/voice on|off）\n\n"
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
        self._prune_stale_slots()
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
