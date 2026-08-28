"""
Telegram Bridge for ShellFrame.
Routes one TG bot across multiple PTY sessions with slash-command switching.
Zero external dependencies (uses urllib).
"""

import itertools
import json
import os as _os
import queue as _queue
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
    # 回歸守則：`.*` 開頭且無 ^ 錨點的 pattern 在長無空白行（base64/JWT/
    # minified 輸出）上是 O(N²) 災難性回溯——實測 `.*\d+%\s*left.*` 對一行
    # 40KB base64 要 9 秒，flush loop 整條卡死（2026-07-07「TG 收不到」事故）。
    # filters 可能來自遠端/使用者編輯，這裡一律自動補 ^（MULTILINE 下 regex
    # 引擎只在行首嘗試 → 線性）。
    status_pats = ['^' + p if p.startswith('.*') else p
                   for p in f.get("status_bar_patterns", [])]
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
            r'|[A-Z]\w{2,40}(?:ing|ling|ting|ning|ring)(?:…|\.\.\.)'  # catch any Xxxing… (incl. accented chars); 上界 40 防長 token 回溯
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
    """Clean terminal output down to the lines that look like AI prose.

    ⚠ 2026-08-17 P0-1：這裡曾有一段 legacy「Strategy 1」——
        m = re.search(r'>>>\\s*(.*?)\\s*<<<', clean, re.DOTALL)
        if m: return m.group(1).strip()
    它是舊 `>>> response <<<` marker 方案的殘骸（現行 marker 是
    `[[TG_REPLY_<uuid8>]]`），已無人使用卻保有破壞性副作用：只要 120KB
    pending_raw 裡任何位置出現一組 `>>>` … `<<<`（Python REPL 提示、
    bash here-string `cmd <<< "x"`、git conflict、diff 輸出——agent 畫面
    上極常見），整個 buffer 就只剩那一小段，`[[TG_REPLY_…]]` 區塊被完全
    抹除 → marker 路徑永遠抽不到 → marker_forwarded 永遠 False → fallback
    又卡在 turn_ended 前提 → **完全靜默、零 log 的永久失聯**（tab 11/s87
    「愛回不回」根因）。刪掉同時省下每次 marker scan 一次 120KB 的 DOTALL
    全 buffer 搜尋。**不要再加回來。**
    """
    c = _get_compiled()

    # Strip ANSI first
    clean = c["ansi"].sub('', text)
    clean = c["spinner"].sub('', clean)

    # Regex cleaning + keyword filter
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
    # turn 結束 footer：動詞會輪換（Cooked/Crunched/Cogitated/…），不能列舉——
    # 認「<verb>ed/ing for <時長>」到行尾的形狀（2026-07-24 Crunched/Cogitated 漏網）
    r'|(?:^[✻✢✳∗✽·●⏺•*─\-\s]{0,3}\w+(?:ed|ing)\s+for\s+(?:\d+[hm]\s*)*\d+(?:\.\d+)?s?\s*(?:[·(].*)?$)'
    r'|(?:new task\?\s*/clear)|(?:/clear to save)',
    re.IGNORECASE)

# Stray reply-marker tokens that can leak into a span when a TUI repaint nests
# a fresh [[TG_REPLY_xxx]] inside an earlier still-open block.
_REPLY_MARKER_TOKEN_RE = re.compile(
    r'(?:\[\[|<<)/?TG_REPLY_[0-9a-fA-F]+(?:\]\]|>>)')

# 模型偶爾把 [[TG_REPLY_x]] 寫成 <<TG_REPLY_x>>——而且是**黏性**的：同一個
# session 一旦寫過一次，往後每回合都照抄自己上一輪的寫法，該分頁的回覆從此
# 永遠配不到 marker，只能等 30s fallback 兜底（使用者體感＝「回覆解析不
# 到 / 要自己 /fetch」）。token 本身是 8 位 hex 亂數，換個括號不可能撞到別
# 的東西，所以抽取前先把別名正規化回官方寫法。
_REPLY_MARKER_ALT_RE = re.compile(r'<<(/?TG_REPLY_[0-9a-fA-F]+)>>')


def normalize_reply_markers(text: str) -> str:
    """`<<TG_REPLY_x>>` → `[[TG_REPLY_x]]`（見 _REPLY_MARKER_ALT_RE）。"""
    return _REPLY_MARKER_ALT_RE.sub(r'[[\1]]', text or "")


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

# 自動派工關閉時的中性版：不推派工，其餘規則（grounding、tab label）保留。
_MASTER_TURN_PREAMBLE_MANUAL = (
    "[總控] You are the master tab. Auto-delegation is OFF in ShellFrame "
    "settings — handle tasks in this tab by default, and use `sfctl delegate` "
    "/ `sfctl new` only when the user explicitly asks for a worker or parallel "
    "tab（如「派工」「開分頁」「開 worker」）.\n"
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

_TG_PROMPT_BASE = (
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
)

# 派工協調段落——受「自動派工」開關管（v0.29.10）。開關關閉時這段曾照樣
# 每回合注入（它藏在 TG prompt 裡、不在 master preamble），是「自動派工
# 關掉還是會派工」的根因。
_TG_COORD_DELEGATE = (
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

_TG_COORD_MANUAL = (
    "Coordination: auto-delegation is OFF in ShellFrame settings — handle the "
    "work in THIS tab by default. Use `sfctl delegate` / `sfctl new` only when "
    "the user explicitly asks for a worker / parallel tab（如「派工」「開分頁」"
    "「開 worker」）. `sfctl list` / `sfctl peek` for answering questions about "
    "other tabs is always fine."
)

DEFAULT_TG_PROMPT = _TG_PROMPT_BASE + _TG_COORD_DELEGATE


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


# ── Line-oriented agents: instructions must not look like user messages ──
#
# A TUI agent (claude, codex) receives our per-turn wrapper as one bracketed
# paste and treats it as context for the turn. An agent whose channel is a
# line-oriented REPL cannot: every injected instruction arrives looking exactly
# like something the user typed, so it *answers* each rule ("got it, noted!")
# and only reaches the real question at the end. Framing the instruction part
# lets such an agent route it to its system prompt instead. Agents that don't
# understand the markers never see them — the gate is the launch command.
SYSTEM_DIRECTIVE_START = "<<<SF:SYSTEM>>>"
SYSTEM_DIRECTIVE_END = "<<<SF:/SYSTEM>>>"
_DEFAULT_DIRECTIVE_AGENTS = ("sparkagent",)


def system_directive_agents() -> tuple:
    """Launch-command substrings whose agents want framed instructions."""
    configured = _read_settings().get("system_directive_agents")
    if isinstance(configured, list):
        return tuple(str(x).lower() for x in configured if str(x).strip())
    return _DEFAULT_DIRECTIVE_AGENTS


def wants_system_directive(cmd: str) -> bool:
    c = (cmd or "").lower()
    return any(name in c for name in system_directive_agents())


def frame_system_directive(payload: str, user_text: str) -> str:
    """Wrap everything preceding ``user_text`` as a system directive block.

    Returns ``payload`` unchanged when there is nothing to frame, so a plain
    forwarded message never grows markers.
    """
    if not user_text or user_text not in payload:
        return payload
    head, _, tail = payload.rpartition(user_text)
    instructions = head.strip()
    if not instructions:
        return payload
    return (
        f"{SYSTEM_DIRECTIVE_START}\n{instructions}\n{SYSTEM_DIRECTIVE_END}"
        f"\n\n{user_text}{tail}"
    )


def master_turn_preamble_enabled() -> bool:
    settings = _read_settings()
    return settings.get("master_turn_preamble_enabled", True) is not False


def get_master_turn_preamble() -> str:
    """Master per-turn preamble。自訂文字與內建版的「主動評估派工」段落都
    受「自動派工」開關管：關閉時改用中性版（保留 grounding／tab label 規
    則，但明講只在使用者要求時派工）——否則自訂的 Delegation Protocol 文
    字會繞過開關繼續推派工。"""
    if not auto_delegate_enabled():
        return _MASTER_TURN_PREAMBLE_MANUAL
    settings = _read_settings()
    custom = settings.get("master_turn_preamble")
    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    return _MASTER_TURN_PREAMBLE


def show_tg_wrapper() -> bool:
    settings = _read_settings()
    return settings.get("show_tg_wrapper", True) is not False


def voice_apply_gate() -> bool:
    """語音轉錄後是否要先跳「✅ Apply」確認按鈕再送進 session。預設 True
    （STT 有誤差，確認過再送較安全）。關閉時轉錄完直接自動送出。"""
    return _read_settings().get("voice_apply_gate", True) is not False


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


def auto_delegate_enabled() -> bool:
    """設定頁「自動派工（實驗性）」開關，預設關。v0.29.10 前這個鍵沒有任何
    後端 consumer（死開關），而派工協調指令藏在 TG prompt 裡每回合照灌。"""
    return _read_settings().get("auto_delegate_enabled", False) is True


def get_tg_prompt() -> str:
    """TG per-turn preamble. User config > built-in. Empty string = built-in.

    內建版的協調段落受「自動派工」開關管：關閉時改為「在本分頁處理、
    僅在使用者明確要求時派工」。自訂 tg_prompt 一律原文照用（使用者
    自己寫的內容不做手術）。"""
    settings = _read_settings()
    custom = settings.get("tg_prompt")
    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    coord = _TG_COORD_DELEGATE if auto_delegate_enabled() else _TG_COORD_MANUAL
    return _TG_PROMPT_BASE + coord


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
_NOISE_SESSION_END_RE = re.compile(
    # 動詞輪換＋符號不固定（✻✳✢…）——認「<verb>ed/ing for <時長>」形狀
    r'^[✻✢✳∗✽·●⏺•*─\-\s]{0,3}[a-z]+(?:ed|ing)\s+for\s+(?:\d+[hm]\s*)*\d+(?:\.\d+)?s?\b')
_NOISE_RATING_NUM_RE = re.compile(r'^\d+:\s*\w+')
_NOISE_RATING_OPT_RE = re.compile(r'\d+:\s*(?:bad|fine|good|dismiss)')
_TOOLCALL_PREFIX_RE = re.compile(r'^[A-Z][\w\s]*\(')
_NUMBERED_ITEM_RE = re.compile(r'\d+\.?\s')
_USERNAME_PREFIX_RE = re.compile(r'^(\w+):\s')
_MENU_ITEM_RE = re.compile(r'^(\d+)[\.\)]\s+(.+)$')
_MENU_END_RE = re.compile(r'esc|cancel|tab|enter', re.I)
# Picker chrome（如 /model 的「◉ xHigh effort ←/→ to adjust」）不是選項也不是
# 結尾，但也不該把已收集的選項 reset 掉——那正是 /model 選單偵測不到的原因。
_MENU_CHROME_RE = re.compile(r'[◉○←→]|to adjust', re.I)
_MENU_ACTION_RE = re.compile(
    r'Action Required|Would you like|Do you want|approval|approve|permission', re.I)

# Rate-limit detection patterns (read from live screen, not from extract path)
_RATE_LIMIT_RE = re.compile(
    r"hit your (?:session|usage) limit|/rate-limit-options|/usage-credits to finish",
    re.I)
_RATE_LIMIT_RESET_RE = re.compile(r'resets?\s+([^\n·]+?)(?:\s*[\|·]|$)', re.I | re.MULTILINE)


class _OrderedSet:
    """Insertion-ordered set — `slot.sent_responses` 的容器。

    為什麼不是 `set`：去重集合同時要 O(1) membership **和確定的順序**。
    舊版是 plain set，兩處行為因此不確定：
      1. 溢位裁切 `set(list(s)[-100:])` 的「最後 100 筆」是任意迭代順序，
         可能丟掉最新的回覆、留下遠古的 → 舊內容重被判定為「沒送過」。
      2. superset/subset 迴圈 `for prev in list(s): … break` 先撞到誰是隨機的。
    P0-2 修好 pyte history 失明後會**刻意重掃 scrollback 尾端 64 行**，
    完全依賴這個集合擋重複；集合不可靠就會把「靜默丟訊」換成「隨機重複洗版」
    （SA A8 風險表明列，實作順序不可顛倒的理由）。dict 天生保序且 O(1)。
    """

    __slots__ = ("_d",)

    def __init__(self, items=()):
        self._d = dict.fromkeys(items)

    def add(self, item):
        self._d[item] = None

    def discard(self, item):
        self._d.pop(item, None)

    def __contains__(self, item):
        return item in self._d

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __repr__(self):
        return f"_OrderedSet({list(self._d)!r})"


# Virtual-terminal geometry for the per-slot pyte screen. ROWS must track the
# real PTY height: pyte only ever paints the rows the CLI addresses, so a
# screen taller than the terminal keeps whatever was last painted on the rows
# below the viewport — stale "ghost" text that never gets cleared. `_live_tail`
# then samples ghosts instead of the live footer and every screen-based signal
# (delivery verify, busy guard, stall watchdog) goes blind on that tab.
# COLS stays generous: the CLI already wrapped its output at the real width, so
# a wider virtual screen only leaves unused space on the right, while a screen
# narrower than the real terminal would wrap text the terminal did not.
_SCREEN_ROWS_DEFAULT = 50
_SCREEN_ROWS_MIN, _SCREEN_ROWS_MAX = 10, 200
_SCREEN_COLS_MIN, _SCREEN_COLS_MAX = 200, 500


def _screen_dims(cols, rows):
    """(cols, rows) for a slot's pyte screen, clamped to sane bounds.

    Unknown/absurd height falls back to the historical 50 rows — better a few
    ghost rows than a screen shorter than the viewport (which would clip the
    live footer outright)."""
    try:
        rows = int(rows or 0)
    except (TypeError, ValueError):
        rows = 0
    if not (_SCREEN_ROWS_MIN <= rows <= _SCREEN_ROWS_MAX):
        rows = _SCREEN_ROWS_DEFAULT
    try:
        cols = int(cols or 0)
    except (TypeError, ValueError):
        cols = 0
    cols = max(_SCREEN_COLS_MIN, min(cols, _SCREEN_COLS_MAX))
    return cols, rows


class SessionSlot:
    """One session registered with the bridge."""

    def __init__(self, sid: str, label: str, write_fn, index: int, peek_fn=None,
                 prepare_fn=None, cmd: str = "", cols: int = 0, rows: int = 0):
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
        # Marker-scan throttle (flush-loop hot path). One failed scan costs a
        # full strip_ansi over pending_raw (≤120KB ≈ 45ms) — re-arm only when
        # BOTH the rescan interval elapsed AND new PTY bytes arrived
        # (_feed_gen advanced). Unthrottled per-tick rescans after the 120s
        # force window were the 2026-07-06 96%-CPU regression.
        self.marker_next_scan_ts = 0.0
        self.marker_scan_gen = -1
        # marker 抽取失敗時，fallback 純文字轉發的 _peek 節流（每 3s 最多一次）。
        self._fb_next_ts = 0.0
        # 這則使用者訊息送出的時刻——fallback 的等待時鐘用它，而非
        # first_output_time（後者在忙碌分頁會停在很久以前，害 total 變幾萬秒、
        # fallback 一送新訊息就誤觸把上一則回覆重送。v0.29.22）。
        self.msg_sent_ts = 0.0
        # 這個「訊息 epoch」內是否已轉發過至少一個 marker block。用來支援
        # follow-up 連續訊息（第一則回覆後保持監聽、每個新 block 各轉發一次），
        # 並讓「模型漏 marker」的 fallback 只在完全沒轉發過 marker 時才觸發
        # （避免對已用 marker 的分頁重複發 peek）。新使用者訊息時重置。
        self.marker_forwarded = False
        # True while a TG message is waiting in the busy-guard queue / being
        # written into the PTY. /fetch reads it to tell the user their message
        # is queued (not lost) instead of silently showing the previous reply.
        self.inject_pending = False
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
        # Rate-limit detection: True once we have fired the TG alert for the
        # current rate-limit episode; cleared when the screen no longer shows
        # rate-limit signals so the next episode re-notifies.
        self.rate_limit_notified = False
        # Virtual terminal for screen-based text extraction
        # Use HistoryScreen to keep scrollback — 50-line screen loses long responses
        # history 由 3000 降至 800：兼顧長回應擷取與 per-tab 記憶體/掃描成本（撐 10+ tab）
        # Height mirrors the real PTY (see _screen_dims) so no row below the
        # viewport can hold ghost text; resize_session() keeps it in sync.
        # 這個分頁是否曾經被確認「CLI 已就緒」——只擋第一次注入，之後不再
        # 每則訊息都去 capture-pane。
        self.ready_confirmed = False
        self.screen_cols, self.screen_rows = _screen_dims(cols, rows)
        self.screen = pyte.HistoryScreen(self.screen_cols, self.screen_rows,
                                         history=800)
        self.stream = pyte.Stream(self.screen)
        self._history_offset = 0  # tracks processed history lines
        # 飽和後重掃 scrollback 尾端的 dirty gate：上次重掃時的 _feed_gen。
        # 用 feed_gen 而非「最後一行內容」——後者在空白行時有盲點（M-1）。
        self._hist_scan_gen = -1
        # perf_debug 診斷用：上次 _pick_marker_reply 清洗後 marker 還在不在
        self._dbg_clean_has = None
        self.sent_responses = _OrderedSet(("Understood.", "Understood"))  # pre-filter acks
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
        # 送達回執不需要 slot 欄位：_send() 以閉包捕獲當時那則訊息的
        # origin_msg_id，天生就是「舊的那則停在它最後的狀態」（SA A3.5 的意圖），
        # 且沒有跨執行緒競態。曾經宣告過 slot.pending_reaction，全檔零讀取＝
        # 死狀態，已移除（QA m-1）。
        # ── 長回合心跳 ──（狀態全部是 float/int/str，閘門 O(1)、不碰 buffer）
        self._hb_next_ts = 0.0      # 下一次允許發心跳的時間（指數退避）
        self._hb_count = 0          # 這個 epoch 閘門觸發過幾次（算退避倍率）
        self._hb_interval = 0.0     # 閘門上次用的退避間隔（內容去重的時間基準）
        self._hb_last_hash = ""     # 內容 hash，一樣且未滿 2×間隔才跳過
        self._hb_last_sent_ts = 0.0  # 上次**實際送出**的時刻（不是閘門觸發時刻）
        self._hb_quiet = False      # /quiet：這個 epoch 使用者要求安靜
        # ── 進行中預覽 ──
        self._preview_count = 0     # 每個 epoch 最多 PREVIEW_MAX 次
        self._preview_gen = -1      # buffer 沒新 bytes 就不重取（_feed_gen gate）
        self._preview_last = ""     # 與上次相同就跳過
        # 送出失敗 ⚠ 警告的節流閘（M-4）：下次允許發警告的時刻
        self._send_fail_warn_ts = 0.0


class TelegramBridge(BridgeBase):
    """
    Multi-session Telegram bridge.
    One bot manages all sessions. Users switch with slash commands.
    """

    PLATFORM = "telegram"

    def __init__(self, bridge_id: str, config: TelegramBridgeConfig, on_status_change=None, on_reload=None, on_close_session=None, on_restart=None, on_check_update=None, on_new_session=None, on_consume_init=None, on_model_info=None, on_agent_status=None, on_input_blocked=None):
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
        self._on_model_info = on_model_info  # callback(sid) -> {name,effort,provider}|None
        # callback(sid) -> (status_dict, age_s)|None — **唯讀** StatusTracker 快取。
        # 心跳的狀態行用它，禁止在裡面呼叫 status_for()（會觸發 transcript 解析）。
        # main.py 尚未重啟時是 None，心跳自動降級成只有第一行，不會失效。
        self._on_agent_status = on_agent_status
        # callback(sid) -> str：分頁正卡在會吃掉輸入的啟動對話框時回傳原因，
        # 否則回空字串。只在「這個分頁的第一次注入」用（見 _wait_input_safe）。
        # 刻意做成**偵測危險狀態**而不是「偵測就緒」：就緒訊號抓不到只會退回
        # 舊行為，抓錯就緒卻會把正常分頁的訊息全擋掉（實測 _AI_READY_RE 對
        # Claude Code 2.x 的 ❯ composer 根本配不到，做成就緒閘門會災難）。
        self._on_input_blocked = on_input_blocked
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

        # 送達回執：全域開關（連續失敗後關掉，reload/restart 才復原）
        # 與 per-chat 連續失敗計數。
        self._reaction_disabled = False
        self._reaction_fail = {}   # chat_id -> 連續失敗次數

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

        # ── Inbound update dispatch queue (v0.29.7) ──
        # _handle_update used to run INLINE in _poll_loop. Two silent-drop
        # modes followed (回報：「TG 傳入有時候收不到」)：
        #   1. slow inline work (voice STT / photo download can take 60s+)
        #      froze getUpdates → watchdog declared the poll wedged →
        #      self-reload killed the in-flight message (offset already saved
        #      = never re-fetched);
        #   2. any exception in _handle_update bubbled to the poll-loop
        #      except → message lost with only a [poll] log line.
        # Now the poll loop only enqueues; a dedicated FIFO worker handles
        # updates one at a time (preserves per-batch ordering) and a crash
        # notifies the sender instead of vanishing.
        self._update_queue = _queue.Queue()

    # ── Perf instrumentation helpers ──

    def _perf_t(self):
        """Return a monotonic start stamp when perf_debug is on, else None.

        getattr 而非直接取值：測試大量用 `object.__new__(TelegramBridge)` 造
        假 bridge，`__init__` 沒跑過。計時是純觀測，缺欄位時安靜關掉即可，
        不該讓被測路徑炸掉。"""
        return time.monotonic() if getattr(self, "_perf_enabled", False) else None

    def _perf_end(self, name: str, t0):
        """Accumulate elapsed time for phase `name` (no-op when t0 is None)."""
        if t0 is None:
            return
        dt = time.monotonic() - t0
        perf = getattr(self, "_perf", None)
        if perf is None:
            return
        b = perf.get(name)
        if b is None:
            perf[name] = [dt, 1]
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
                         prepare_fn=None, cmd: str = "", cols: int = 0,
                         rows: int = 0):
        """Register a session tab with the bridge.

        cols/rows are the session's live PTY geometry — the virtual screen is
        built (or re-sized) to match, see _screen_dims."""
        if sid in self.slots and rows:
            # Re-register (restart / re-attach) also carries fresh geometry.
            # Only when the caller actually knows it — a geometry-less
            # re-register must not reset a correctly sized screen back to the
            # 200x50 default.
            self.resize_session(sid, cols, rows)
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
                                          cmd=cmd, cols=cols, rows=rows)
            self._slot_order.append(sid)

    def resize_session(self, sid: str, cols: int, rows: int):
        """PTY resized → rebuild the slot's virtual screen at the new geometry.

        Not pyte's own screen.resize(): shrinking there deletes rows from the
        TOP (50→31 keeps rows 19-49), i.e. it throws the live viewport away and
        *keeps* the ghost rows — the exact opposite of what we need. Swapping in
        a fresh screen is safe because the same resize sends SIGWINCH to the
        CLI, which repaints the whole viewport within a second; scrollback is
        carried over so long-reply extraction keeps its history.
        """
        slot = self.slots.get(sid)
        if slot is None:
            return
        new_cols, new_rows = _screen_dims(cols, rows)
        if (new_cols == getattr(slot, "screen_cols", 0)
                and new_rows == getattr(slot, "screen_rows", 0)):
            return
        with slot.output_lock:
            old = slot.screen
            screen = pyte.HistoryScreen(new_cols, new_rows, history=800)
            try:
                screen.history.top.extend(old.history.top)
            except Exception:
                pass
            slot.screen = screen
            slot.stream = pyte.Stream(screen)
            slot.screen_cols, slot.screen_rows = new_cols, new_rows
            slot._feed_gen += 1
            slot._display_cache = None
            slot.scan_dirty = True
        _blog(f"[resize] {sid} virtual screen → {new_cols}x{new_rows}\n")

    def unregister_session(self, sid: str):
        """Remove a session from the bridge."""
        with self._slots_lock:
            self._remove_slots_locked([sid])

    def _remove_slots_locked(self, sids):
        """Remove slots while self._slots_lock is held."""
        removed = False
        gone_labels = {}
        for sid in sids:
            if sid in self.slots:
                slot = self.slots[sid]
                gone_labels[sid] = slot.label
                # 分頁死掉時把最後畫面留在 log 裡：tmux session 一沒，
                # 現場就完全蒸發，事後只剩「分頁不見了」查不出死因
                # （2026-08-28 s169 開了 3 分鐘就消失，無跡可循）。
                try:
                    tail = (self._live_tail(slot, rows=8) or "").strip()
                except Exception:
                    tail = ""
                _blog(f"[slot-gone] {sid} '{slot.label}' last screen:\n"
                      f"{tail[-800:]}\n")
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
        # 使用者手上的分頁消失 → 改指第一格，但**必須講**（見
        # _notify_active_slot_gone）：靜默改指等於下一則工作指令悄悄落到
        # 別的分頁，手機端只看得到 👀/🫡。
        reroutes = []
        for uid, active_sid in list(self._user_active.items()):
            if active_sid in self.slots:
                continue
            gone = gone_labels.get(active_sid, active_sid)
            if self._slot_order:
                self._user_active[uid] = self._slot_order[0]
                reroutes.append((uid, gone, self._slot_order[0]))
            else:
                del self._user_active[uid]
                reroutes.append((uid, gone, ""))
        if reroutes:
            threading.Thread(target=self._notify_active_slot_gone,
                             args=(reroutes,), daemon=True).start()
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

    def _notify_active_slot_gone(self, reroutes):
        """分頁在使用者手上消失（CLI 退出／分頁被關）→ 明講訊息改送去哪。

        舊版靜默把 _user_active 改指 _slot_order[0]：手機端只看得到 👀／🫡，
        下一則工作指令就悄悄落進別的分頁。Howard 2026-08-28 實例：新開的
        分頁 3 分鐘後死掉，接著丟的「台壽展場案 API 規格」任務跑進「雜事」，
        他是 /10 打不開才發現分頁不見了。跑在背景 thread：呼叫端還握著
        _slots_lock，而 tg_api 可能卡到 35s。"""
        for uid, gone_label, new_sid in reroutes:
            chat_id = self._user_chat.get(uid)
            if not chat_id:
                continue
            slot = self.slots.get(new_sid) if new_sid else None
            if slot is not None:
                body = (f"⚠ 分頁「{gone_label}」已經結束（CLI 退出或分頁被關）。\n"
                        f"之後的訊息會自動送到「{slot.label}」(/{slot.index})，"
                        f"要換請用下面的編號。\n\n{self._slot_menu_text(uid)}")
            else:
                body = (f"⚠ 分頁「{gone_label}」已經結束，"
                        "目前沒有其他分頁可以接手。")
            try:
                tg_api(self.config.bot_token, "sendMessage",
                       {"chat_id": chat_id, "text": body}, timeout=10)
            except Exception:
                pass

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

        self._dispatch_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatch_thread.start()

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
        self._persist_pending_updates()   # P0-7
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        self._emit_status({"state": "stopped"})

    def _set_bot_commands(self):
        """Register slash commands with Telegram.

        Order: numbered session switchers FIRST (v0.11.57 — the user mostly
        opens the picker to swap sessions, so /1 /2 ... should be the
        thumb-reachable top of the menu). Generic ops follow.

        Menu trimmed (v0.11.55): /help, /pause, /resume, /reload removed from
        the visible menu — reported they cluttered the picker without
        being used. Their handlers stay (typed by hand or from old shortcuts
        they still respond), they just aren't suggested.
        """
        commands = []
        # Numbered session switchers FIRST — most-used action on mobile TG
        with self._slots_lock:
            for sid in self._slot_order:
                slot = self.slots[sid]
                # Description max 256 chars; label+model comfortably fits.
                desc = f"Switch to {slot.label}{self._slot_model_suffix(slot)}"
                commands.append({
                    "command": str(slot.index),
                    "description": desc[:256],
                })
        # Generic ops after the session list
        commands.extend([
            {"command": "fetch", "description": "Fetch latest AI reply"},
            {"command": "usage", "description": "Current tab AI usage (水位)"},
            {"command": "list", "description": "List sessions + bridge state"},
            {"command": "restart", "description": "Full app restart (sessions preserved)"},
            {"command": "update", "description": "Check & apply ShellFrame updates"},
            {"command": "new", "description": "New session (default: claude)"},
            {"command": "rename", "description": "Rename tab: /rename <新名> 或 /rename <編號> <新名>"},
            {"command": "effort", "description": "調推理深度（claude/codex，inline 按鈕）"},
            {"command": "quiet", "description": "這一輪別再提醒進度（心跳靜音）"},
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

    def _slot_model_suffix(self, slot):
        """' · Opus 4.8 · xhigh' for a slot (mirrors the desktop sidebar
        badge), or '' when unknown / non-AI. Cheap; main.py mtime-caches."""
        if not self._on_model_info:
            return ""
        try:
            mi = self._on_model_info(slot.sid)
        except Exception:
            mi = None
        if not mi or not mi.get("name"):
            return ""
        eff = mi.get("effort")
        return f" · {mi['name']} · {eff}" if eff else f" · {mi['name']}"

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
        so the user doesn't have to watch Telegram — the desktop pops『做完了／
        要我決策／卡住』. Reuses the osascript path from _maybe_notify_completion;
        unlike completion banners this fires even when ShellFrame is frontmost
        (the user may be on a different tab) but still respects the
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
        "CoreServicesUIAgent",      # quarantine / "are you sure you want to open" / auth
        "SecurityAgent",            # admin password / keychain prompts
        "universalAccessAuthWarn",  # Accessibility prompts
        # UserNotificationCenter deliberately EXCLUDED (回報 2026-07-06:
        # 常收到「popup detected (UserNotificationCenter)」誤報). It owns EVERY
        # macOS notification banner — Slack/Mail/Calendar/etc. — not just TCC
        # dialogs. A transient banner appearing while a session waits fired a
        # false "blocking popup" alarm; banners don't block foreground work and
        # "去把它關掉" is useless when away. The genuinely-modal owners below
        # actually block and are effectively never spurious.
        # loginwindow deliberately excluded: it's always running and frequently
        # owns transparent/system-management windows during normal operation
        # (sleep/wake transitions, screen-lock manager, Touch ID prep), so
        # CGWindowList kCGWindowListOptionOnScreenOnly matches it during any
        # long Claude response → false "popup detected (loginwindow)" alarm
        # whenever the model thinks for >25s. Real lock-screen blocking can't
        # be dismissed remotely anyway, so detecting it has no upside.
    })

    # A real modal dialog has visible bounds; system-management windows
    # (loginwindow's 0x0, transparent transition surfaces) do not. Require a
    # dialog-sized visible window before treating an owner match as blocking.
    _POPUP_MIN_W = 120
    _POPUP_MIN_H = 60

    # ── 送達回執（reaction 狀態機，A3）──
    # setMessageReaction 只吃 Telegram 固定的 emoji 白名單。任務單提的 ✅
    # **不在白名單、會被拒**（SA A3.3）。👀 repo 早在 slash command 路徑用了、
    # 已驗證；🫡 於 2026-08-17 用真實 bot token 實測 setMessageReaction 回
    # {"ok":true}。用 reaction 而不是新訊息，是因為它就地標在使用者自己那則
    # 訊息上：不佔對話列、不推播、不洗版；而且「bot 只能有一個 reaction、
    # 後設的取代先設的」天生就是一個狀態機，不需要額外去重。
    REACTION_SEEN = "👀"        # T0：已收下，準備注入
    REACTION_DELIVERED = "🫡"   # T1：確認送進 session 了
    _REACTION_FAIL_LIMIT = 3

    def _set_reaction(self, chat_id, message_id, emoji):
        """設 / 清一則訊息上的 reaction。emoji 傳 None 或 '' = 清空（T2）。

        硬性要求（repo 血案）：timeout=5、且**永遠在 slot.write_lock 之外**
        呼叫——tg_api 最長可以卡 35 秒，握著 write_lock 會把該分頁後續所有
        訊息排在一個死掉的 HTTPS 後面。

        perf 計時：**刻意不進 `_perf_*`**（SA A3.6 明文如此）。這是純網路往返、
        在背景 thread、不在 flush loop；一次 HTTPS 300ms 若計進 60s 摘要，
        會直接吃掉整條 150ms 的預算、把真正的 CPU 迴歸淹掉。改以 `[reaction]`
        log 行記錄延遲與失敗。"""
        if getattr(self, "_reaction_disabled", False) or not chat_id or not message_id:
            return False
        data = {"chat_id": chat_id, "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": emoji}] if emoji else []}
        t0 = time.time()
        resp = tg_api(self.config.bot_token, "setMessageReaction", data, timeout=5)
        if resp.get("ok"):
            self._reaction_fail.pop(chat_id, None)
            # 成功也記一行——QA 2026-08-17 指出新功能在 production 的觸發次數
            # 全部是 0、無從驗證。每則使用者訊息最多 2 行，量很小，換到的是
            # 「這個功能到底有沒有在動」可被稽核。
            _blog(f"[reaction] {chat_id} msg={message_id} "
                  f"{emoji or 'clear'} ok ({(time.time() - t0) * 1000:.0f}ms)\n")
            return True
        desc = str(resp.get("description") or "")
        n = self._reaction_fail.get(chat_id, 0) + 1
        self._reaction_fail[chat_id] = n
        _blog(f"[reaction] {chat_id} {emoji or 'clear'} failed ({n}): {desc[:120]}\n")
        # 單則失敗不退回文字（沒有告知價值，退回文字反而製造雜訊）；
        # 同一 chat 連續失敗 3 次才一次性告知並全域停用。
        if n >= self._REACTION_FAIL_LIMIT and not self._reaction_disabled:
            self._reaction_disabled = True
            _blog("[reaction] disabled (TG API keeps rejecting)\n")
            try:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": ("送達回執（訊息上的 👀 / 🫡 標記）不可用——Telegram API "
                             "連續拒絕，已停用。訊息本身仍正常轉送。"),
                }, timeout=5)
            except Exception:
                pass
        return False

    def _react_async(self, chat_id, message_id, emoji):
        """背景 thread 版的 _set_reaction。不阻塞任何路徑。"""
        if getattr(self, "_reaction_disabled", False) or not chat_id or not message_id:
            return
        threading.Thread(target=self._set_reaction,
                         args=(chat_id, message_id, emoji), daemon=True).start()

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
            if owner not in self._POPUP_OWNERS:
                continue
            # Must be a real, visible, dialog-sized window — not a 0x0 or
            # transparent system-management surface.
            if float(w.get("kCGWindowAlpha", 1.0)) <= 0.0:
                continue
            b = w.get("kCGWindowBounds", {}) or {}
            if (b.get("Width", 0) >= self._POPUP_MIN_W
                    and b.get("Height", 0) >= self._POPUP_MIN_H):
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
            if len(slot.pending_raw) > self._PENDING_RAW_MAX:
                keep = slot.pending_raw[-self._PENDING_RAW_MAX:]
                # 截斷時**保住 start marker**（v0.29.37）。長輸出（研究報告、
                # 長 build log）會把 buffer 撐到上限，開頭的 [[TG_REPLY_x]]
                # 被擠出去 → span 永遠配不出來 → 每則回覆都得等 30s fallback
                # 兜底（回報「愛回不回」的其中一條），而且每 3s 還要對
                # 120KB 白跑一次 strip_ansi（實測 31ms／次，log 中 81% 的
                # marker-miss 都是 raw=False）。把它接回保留區開頭，span 就
                # 還能配對——這是效能與功能同一個修法。
                sm = slot.reply_start_marker
                if sm and sm not in keep and sm in slot.pending_raw:
                    keep = sm + "\n" + keep
                slot.pending_raw = keep
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

    # deque 飽和後每次重掃的 scrollback 尾端行數。
    # 成本：QA 用同一個飽和 slot 做 A/B（64 vs 0，各 200 次呼叫）實測
    # **+1.09 ms/次**（不是我原本註解寫的 0.65ms——那是只算 history 行 join、
    # 漏掉多出來 64 行流進 block 解析的成本）。呼叫頻率中位數 12 次/60s、
    # 最高 27 次/60s → +13～29 ms/60s，在 50ms 單項紅線內。
    _HISTORY_SATURATED_TAIL = 64

    def _extract_new_text(self, slot):
        """Scan screen + scrollback history for AI responses not yet sent.

        Logic:
        1. Combine scrollback history (lines scrolled off top) + current screen
        2. Find a line starting with AI_MARKERS (• / ⏺) = start of response block
        3. Collect ALL subsequent lines until hitting a prompt marker (› / ❯) or another AI marker
        4. Join collected lines as one response; skip if already in sent_responses
        """
        # `sfctl reload` 時 main.py 會用快照把 sent_responses 還原成 plain set
        # （hot_reload_bridge），保序性會悄悄消失。這裡一次 isinstance（~50ns）
        # 把它正規化回 _OrderedSet，免得 reload 後 P0-2 的重掃失去可靠去重。
        if not isinstance(slot.sent_responses, _OrderedSet):
            slot.sent_responses = _OrderedSet(slot.sent_responses)

        # Build full line list: unprocessed history + current display
        all_lines = []

        # History lines that scrolled off the top (pyte.HistoryScreen)
        # Each history line is a StaticDefaultDict mapping col -> Char.
        # 只在「真的有新 history 行」時才走訪（用 islice 取 tail，不再每次把整個
        # deque materialize 成 list）；多數 tick 螢幕內滾動、history 沒增長 → 直接跳過。
        htop = slot.screen.history.top
        hlen = len(htop)
        cols = slot.screen.columns
        if slot._history_offset > hlen:
            slot._history_offset = 0  # history 被 deque maxlen 截斷 → 重置
        start = slot._history_offset
        # ── P0-2：deque 飽和 → scrollback 永久失明 ──
        # history.top 是 deque(maxlen=800)。**滿了之後 len() 恆為 800**，舊行從
        # 左邊被擠掉、長度不變 → `_history_offset` 卡死在 800，`> hlen` 為假
        # （相等）、`< hlen` 也為假 → 這個 slot 此後永遠不再掃任何 scrollback，
        # 只剩 50 行 live screen 可抽，兩次 flush tick 之間捲過去的回覆永久遺失。
        # 長壽命分頁（跑了兩天的 s87）必然早就飽和。修法：飽和後改為固定重掃
        # 尾端 K 行，重複內容交給 sent_responses（_OrderedSet，見 P1-13）擋。
        #
        # dirty gate 用 `_feed_gen`（每個 PTY chunk +1），**不是**「history 最後
        # 一行的 signature」。M-1：signature 版有空白行盲點——TUI 捲出去的最後
        # 一行常常是空白，前後兩次都是 ''，重掃就被整個跳過，其間捲過去超出 64
        # 行窗的內容永久遺失（靜默丟訊沒根治）。`_feed_gen` 單調遞增、無盲點、
        # O(1)，而且是 repo 既有的 dirty-gate 慣用法（marker_scan_gen 同款）。
        maxlen = getattr(htop, "maxlen", None) or 0
        if maxlen and hlen >= maxlen:
            gen = getattr(slot, "_feed_gen", 0)
            if gen != slot._hist_scan_gen:
                slot._hist_scan_gen = gen
                start = min(start, max(0, hlen - self._HISTORY_SATURATED_TAIL))
        if start < hlen:
            for hist_line in itertools.islice(htop, start, hlen):
                text = "".join(hist_line[col].data for col in range(cols)).rstrip()
                all_lines.append(text)
            slot._history_offset = hlen

        # Current screen display
        _t_disp = self._perf_t()
        for line in self._slot_display(slot):
            all_lines.append(line.rstrip())
        self._perf_end("screen_display", _t_disp)

        # Collect response blocks: list of (list-of-lines, closed).
        # `closed` = 這個 block 已被提示行／下一個 AI marker 終結。**只有結尾那個
        # block 可能是 closed=False**，代表內容還在增長——B-1 的判定關鍵。
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
                        blocks.append((current_block, True))
                        current_block = None
                continue

            # Check for AI response marker — starts a new block
            marker_hit = False
            marker = self._ai_marker_prefix(line.rstrip())
            if marker:
                # If we were already collecting, save that block first
                if current_block is not None:
                    blocks.append((current_block, True))
                current_block = [stripped[len(marker):].strip()]
                marker_hit = True

            if marker_hit:
                continue

            # If we're inside a response block, collect the line (even if empty)
            if current_block is not None:
                current_block.append(stripped)

        # Don't forget the last block — 沒有被任何東西終結，內容可能還在長
        if current_block is not None:
            blocks.append((current_block, False))

        new_texts = []
        for block_lines, block_closed in blocks:
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

            # Strip AI echo of username prefix (e.g., "Name: response" → "response")
            # Some AI tools mimic the input prefix format in their responses
            for sent in slot.sent_texts:
                # Extract username prefix pattern from sent text (e.g., "Name: ")
                m = _USERNAME_PREFIX_RE.match(sent)
                if m:
                    prefix = m.group(0)  # "Name: "
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
            # Check if this is an expanded version of something already sent.
            # P1-13：舊版在同一個迴圈裡對兩種關係各 `break` 一次，先撞到哪一種
            # 取決於 set 的迭代順序 → 同一份輸入可能這次轉發、下次不轉發。改成
            # 先掃完再決策，且明訂優先序：**「已被某則送過的內容包含」優先**
            # （使用者已經看過這段字，重送就是洗版），否則把所有被 text 包含的
            # 舊短版本一次清掉。結果與迭代順序無關。
            already_sent = False
            supersets = []
            for prev in slot.sent_responses:
                if text in prev:
                    already_sent = True
                    break
                if prev in text:
                    supersets.append(prev)
            if already_sent:
                continue
            # ── B-1（QA Blocker）：內容還在增長時，不要把每一版都當「加長版、該送」──
            # 飽和分頁重掃 scrollback 尾端時，一個**沒有被提示行終結**的 AI block
            # 每輪會多吃進上一輪新增的幾行 → 每輪產生一份比上輪更長的文字。
            # 舊碼走到這裡會判定「這是加長版」→ 丟掉舊的短版本 → 送新的，
            # 於是同一段內容被轉發 64 次、每則遞增一行（長 build / `tail -f` /
            # 訓練 log 這類純捲動 shell 分頁）。去重集合完全沒有機會擋，因為
            # 每一版都是全新字串。
            # 修法：block 尚未終結（closed=False）＝仍在增長，這一版不送、
            # **也不動去重集合**——等它被提示行終結、或停止增長後再送最終完整版。
            # 已終結的 block 走原本的展開邏輯，Claude Code TUI 那條正確路徑
            # （實測 0→1）完全不受影響。
            if supersets and not block_closed:
                continue
            for prev in supersets:
                slot.sent_responses.discard(prev)

            # Skip echo of sent text. Three detection modes:
            #   1. reply is entirely nested inside a sent text (nr in ns)
            #   2. sent text starts the reply (ns[:25] in nr) — catches the
            #      "the user: xxx" prefix echo
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
        # Keep sent_responses from growing forever (last 200). 保序容器下這才
        # 真的是「最近加入的 100 筆」——plain set 版的 list(s)[-100:] 是任意
        # 順序，會把最新回覆丟掉、留下遠古的（P0-2 重掃 scrollback 後就會
        # 變成隨機重複轉發）。
        if len(slot.sent_responses) > 200:
            slot.sent_responses = _OrderedSet(list(slot.sent_responses)[-100:])

        # If no normal responses extracted, check for a pending menu prompt
        # (e.g., Claude permission dialog: ❯ 1. Yes / 2. No).
        # Skip when a rate-limit screen is up — the dedicated _detect_rate_limit
        # path (slow_tick) already notifies; firing the generic menu here too
        # would produce a double notification and a confusing 1./2. menu without
        # context (rate-limit options look like an ordinary numbered menu).
        if not new_texts:
            rate_limit_active = _RATE_LIMIT_RE.search(
                "\n".join(l.rstrip() for l in self._slot_display(slot)))
            if not rate_limit_active:
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
        clean_raw = normalize_reply_markers(strip_ansi(raw, sent_texts=[]))
        # A6.0 診斷（僅 perf_debug=on）：記下「清洗後 marker 還在不在」，供
        # _try_marker_extract 的失敗分支印 [marker-miss]。放這裡是因為 clean_raw
        # 只存在於本函式內，重算一次 strip_ansi 要 30ms（120KB）。
        if getattr(self, "_perf_enabled", False):
            slot._dbg_clean_has = slot.reply_start_marker in clean_raw
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

    # pending_raw 上限。超過就保留尾端（並保住 start marker，見 feed_output）。
    _PENDING_RAW_MAX = 120000

    # Minimum spacing between marker scans for one slot. A scan is a full
    # strip_ansi over pending_raw (≤120KB ≈ 45ms measured) — per-tick (0.5s)
    # rescans burn ~18% CPU per stuck slot (2026-07-06 regression).
    _MARKER_RESCAN_INTERVAL = 3.0
    # 預檢用的尾端視窗（strip_ansi 16KB 實測 4.2ms，仍比全量 31ms 省 7 倍）
    _MARKER_PROBE_TAIL = 16384
    # 預檢確認「marker 真的不在」時的重掃間隔。拉長但不放棄。
    _MARKER_PROBE_BACKOFF = 15.0

    # 串流中（未閉合 start marker）時，最多壓住已完成 block 多久。時鐘從
    # slot.msg_sent_ts 起算，見 _try_marker_extract 的 M-3 註解。
    _OPEN_SPAN_WAIT_S = 30.0

    def _is_forward_noise_line(self, s: str) -> bool:
        """轉發前最後防線：marker token 行、turn 結束 footer、含分頁標題的
        分隔線——這些是畫面 chrome 不是回覆內容（回報 2026-07-24 截圖：
        [[TG_REPLY]]／✳ Cogitated for…／「──── 標題 ──」全混進 TG 訊息）。"""
        s = (s or "").strip()
        if "TG_REPLY_" in s:
            return True
        if _TUI_SENTINEL_RE.search(s):
            return True
        rule_chars = sum(c in "─━—═-" for c in s)
        return rule_chars >= 6 and rule_chars >= len(s.replace(" ", "")) * 0.5

    def _try_marker_extract(self, slot, now: float, total: float) -> str:
        """Throttled, dirty-gated marker extraction for the flush-loop hot path.

        Runs ONE _pick_marker_reply pass (the old code ran it twice — once
        normal, once force). A failed scan re-arms only after
        _MARKER_RESCAN_INTERVAL AND once new PTY bytes landed (_feed_gen
        advanced): rescanning an unchanged buffer can't find a marker that
        wasn't there. Streaming guard preserved: an unclosed start marker
        means a newer reply is still painting — wait for it, unless we've
        been waiting `total` ≥ 30s (force: take the last complete reply)."""
        if now < slot.marker_next_scan_ts:
            return ""
        gen = getattr(slot, "_feed_gen", 0)
        if gen == slot.marker_scan_gen:
            return ""
        # 便宜預檢（v0.29.37）：`_pick_marker_reply` 要對整個 pending_raw 跑
        # strip_ansi——實測 120KB＝**31ms**，而 log 顯示 81% 的掃描是
        # `raw=False`（marker 根本不在 buffer）＝31ms 註定白花。這裡先用
        # str.find 確認 marker 痕跡（實測 0.016ms，快 1900 倍）。
        # **不直接放棄**：找不到時改用「較長節流」而非跳過，因為 TUI 有機會把
        # ANSI 插進 marker 中間讓 find 失手——寧可延遲重掃，也不製造新的靜默
        # 丟訊（那是這個檔案修了一整輪的東西）。marker token 的 `TG_REPLY_`
        # 前綴夠短，被 ANSI 打斷的機率低。
        probe = (slot.reply_start_marker or "")[:9]
        if probe and probe not in slot.pending_raw:
            tail = slot.pending_raw[-self._MARKER_PROBE_TAIL:]
            if probe not in strip_ansi(tail, sent_texts=[]):
                slot.marker_next_scan_ts = now + self._MARKER_PROBE_BACKOFF
                slot.marker_scan_gen = gen
                return ""
        reply, has_open = self._pick_marker_reply(slot, allow_inprogress=False)
        miss_reason = ""
        # M-3（SA A5.2 指名）：未閉合 span 的強制等待，時鐘要以「**這則使用者
        # 訊息**送出」為起點，不能用 total。`total = now - first_output_time`，
        # 而 first_output_time 每次 flush 後歸零 → 持續輸出的分頁 total 幾乎
        # 永遠 < 30 → 只要 buffer 裡存在一個未閉合的 [[TG_REPLY_x]]（TUI 重繪
        # 很容易製造），**已經寫完的 block 會被無限期壓住**。這是「愛回不回」
        # 的其中一條路徑。理由與 BT 的 msg_sent_ts 註解相同。
        waited_since_msg = now - (getattr(slot, "msg_sent_ts", 0.0) or now)
        if has_open and waited_since_msg < self._OPEN_SPAN_WAIT_S:
            reply = ""
            miss_reason = "open-span-wait"
        # follow-up 支援：_pick_marker_reply 回「最後一個完整 block」，第一則
        # 轉發後它會一直回同一個 → 若已在 sent_responses 就當「沒有新的」，
        # 走節流等下一個真正的新 block（feed_gen 前進才會重掃）。
        if reply and reply in getattr(slot, "sent_responses", ()):
            reply = ""
            miss_reason = "dup"
        if not reply:
            # A6.0 步驟 0 診斷：分辨「strip_ansi 吃掉 marker」(raw=True clean=False)
            # /「120KB 驅逐或模型沒吐」(raw=False)/「span 配對或清洗過濾」
            # (raw=True clean=True) 三種假說。僅 perf_debug=on 時兩次 str.find
            # （120KB ≈ 0.032ms × 2），off 時只有一次 bool 判斷。
            if getattr(self, "_perf_enabled", False):
                raw_has = slot.reply_start_marker in slot.pending_raw
                clean_has = getattr(slot, "_dbg_clean_has", None)
                _blog(f"[marker-miss] {slot.sid} raw={raw_has} clean={clean_has} "
                      f"rawlen={len(slot.pending_raw)} gen={gen} open={has_open}"
                      f" why={miss_reason or 'no-span'}\n")
            slot.marker_next_scan_ts = now + self._MARKER_RESCAN_INTERVAL
            slot.marker_scan_gen = gen
            return ""
        slot.marker_next_scan_ts = 0.0
        slot.marker_scan_gen = -1
        return reply

    # 模型沒吐出 [[TG_REPLY]] marker 時，等 turn 結束後這麼久就 fallback 轉發
    # 純文字（而非無限等一個永遠不會出現的 marker）。給足時間讓真的有 marker
    # 的慢回覆先正常走 marker 路徑。
    _MARKER_FALLBACK_SECS = 30.0

    def _marker_fallback_text(self, slot) -> str:
        """marker 抽取失敗時的兜底回覆文字：等同 /fetch 讀的最後一則 AI 回應
        （_peek_last_response），但清掉任何殘留的 [[TG_REPLY_xxx]] token 與
        wrapper 指示回顯，避免把 marker 碎片或指示原文送給使用者。回 '' 表示
        連畫面上都沒有可轉發的內容（此時維持原本的等待，不亂送）。"""
        try:
            raw = self._peek_last_response(slot) or ""
        except Exception:
            return ""
        if not raw.strip():
            return ""
        cleaned = _REPLY_MARKER_TOKEN_RE.sub("", raw)
        # 指示比對基準也要先去掉 marker token（畫面上的指示回顯已被上面清掉
        # token），否則兩邊對不上、指示原文會被誤當回覆送出。
        instr = _REPLY_MARKER_TOKEN_RE.sub(
            "", getattr(slot, "marker_prompt", "") or "").replace(" ", "")
        kept = []
        for ln in cleaned.splitlines():
            s = ln.strip()
            if not s:
                continue
            # 丟掉 wrapper 指示本身的回顯（「最終要回 Telegram 的文字請放在…」）
            if instr and s.replace(" ", "") in instr:
                continue
            if self._is_forward_noise_line(s):
                continue
            kept.append(s)
        return "\n".join(kept).strip()

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
        「done / stuck」signal is exactly what the user wants pushed proactively.
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
                if (line.strip() and not _MENU_END_RE.search(line)
                        and not _MENU_CHROME_RE.search(line)):
                    menu_lines = []
                    menu_options = []
        if len(menu_lines) >= 2:
            slot.pending_menu_options = menu_options
            is_action = _MENU_ACTION_RE.search(screen_text)
            title = "待決策：請選一個動作" if is_action else "請選一個選項"
            return f"❓ {title}\n" + "\n".join(menu_lines)
        slot.pending_menu_options = []
        return ""

    def _detect_rate_limit(self, slot):
        """Scan the live screen for Claude rate-limit / session-limit signals.

        Reads directly from _slot_display (not from the _extract_new_text path
        which filters ⎿ lines) so the banner is never missed.

        Returns a dict {"reset": str|"", "interactive": bool} on match,
        or None when no rate-limit signal is found on screen.
        """
        lines = self._slot_display(slot)
        screen_text = "\n".join(l.rstrip() for l in lines)
        if not _RATE_LIMIT_RE.search(screen_text):
            return None
        reset = ""
        m = _RATE_LIMIT_RESET_RE.search(screen_text)
        if m:
            reset = m.group(1).strip().rstrip(".")
        interactive = bool(
            re.search(r'/rate-limit-options', screen_text, re.I)
            or re.search(r'stop and wait|switch to usage credits', screen_text, re.I)
        )
        return {"reset": reset, "interactive": interactive}

    def _notify_rate_limit(self, slot, info: dict):
        """Send a TG notification for a rate-limit episode.

        For interactive menus (/rate-limit-options with 1./2. choices) we also
        attach inline buttons so the user can pick remotely.
        """
        label = slot.label or slot.sid
        reset_str = info["reset"]
        reset_part = f"，{reset_str} 重置" if reset_str else ""
        msg = f"🚫 [{label}] 撞到 Claude 額度上限{reset_part}。"
        if info["interactive"]:
            msg += "\n請選擇如何繼續："
        target_chats = set()
        for uid, active_sid in list(self._user_active.items()):
            if active_sid == slot.sid and uid in self._user_chat:
                target_chats.add(self._user_chat[uid])
        if not target_chats and self._slot_order and slot.sid == self._slot_order[0]:
            for chat_id in self._user_chat.values():
                target_chats.add(chat_id)
        for chat_id in target_chats:
            try:
                if info["interactive"]:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": msg,
                        "reply_markup": {"inline_keyboard": [
                            [{"text": "⏳ 等待重置",
                              "callback_data": f"rlchoice:{slot.sid}:1"}],
                            [{"text": "💳 改用 usage credits",
                              "callback_data": f"rlchoice:{slot.sid}:2"}],
                        ]},
                    })
                else:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": msg,
                    })
                _blog(f"[rate-limit] {slot.sid} notified chat={chat_id}\n")
            except Exception as e:
                _blog(f"[rate-limit] {slot.sid} notify failed: {e}\n")

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
                self._is_bridge_noise_line(l) or
                self._is_forward_noise_line(l)
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
            if self._is_forward_noise_line(stripped):
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

    # ── 長回合心跳（A4）──
    # 設計原則：**零新增掃描**。閘門掛在既有 2s slow_tick 上，只讀 slot 上的
    # float/bool 欄位（O(1)、不碰 buffer、不 render screen）；狀態文字讀
    # main.py 那條 0.6s monitor thread 已經算好的 StatusTracker 快取。
    # 洗版防線有四道：180s 首次門檻、指數退避、內容 hash 去重、/quiet 出口。
    # 寧可漏發不可多發。
    HEARTBEAT_FIRST_S = 180.0     # 3 分鐘沒消息才開始（正常回合完全不會被打擾）
    HEARTBEAT_INTERVAL_S = 300.0  # 基礎間隔 5 分鐘
    HEARTBEAT_BACKOFF = 1.5       # 每發一次拉長 1.5 倍
    HEARTBEAT_MAX_S = 1800.0      # 上限 30 分鐘一則
    # 心跳裡的狀態資料超過這麼久沒更新就不印（免得講的活動早就過期）
    HEARTBEAT_STATUS_MAX_AGE_S = 30.0
    # ── 進行中預覽（A5.2 折衷）──
    PREVIEW_AFTER_S = 900.0       # 等滿 15 分鐘才第一次
    PREVIEW_MAX = 2               # 每個 epoch 上限
    PREVIEW_CHARS = 300

    # 背景 agent 進度行：「Waiting for N background agent…」「↓ 933.7k tokens」
    _BG_AGENT_RE = re.compile(
        r'waiting for (\d+) background agent', re.I)
    _BG_TOKENS_RE = re.compile(r'([↓↑]\s*[\d.]+[kKmM]?)\s*tokens', re.I)

    def _target_chats_for(self, sid: str) -> set:
        """哪些 chat 會收到這個 slot 的回覆／心跳。

        規則沿用原本 flush 迴圈裡的兩段邏輯（抽出來共用，心跳的 G5 閘門也用
        它，避免對空氣心跳）：把這個 slot 設為 active 的使用者，加上——若它是
        第一個 slot——所有還沒明確選過分頁的使用者。

        perf 計時：不自己開 phase。兩個呼叫點都已經被外層計時包住
        （心跳閘門在 `heartbeat_gate` 內、flush 送出路徑在 per-slot 迴圈內），
        巢狀再計一次只會重複計算。成本是 O(使用者數)，個位數 dict 走訪。"""
        chats = set()
        for uid, active_sid in list(self._user_active.items()):
            if active_sid == sid and uid in self._user_chat:
                chats.add(self._user_chat[uid])
        if sid == (self._slot_order[0] if self._slot_order else ""):
            for uid, chat_id in list(self._user_chat.items()):
                if uid not in self._user_active:
                    chats.add(chat_id)
        return chats

    # TG flood-wait 的 description 形狀：'Too Many Requests: retry after 12'
    _RETRY_AFTER_RE = re.compile(r'retry after (\d+)', re.I)
    # flush loop 是所有分頁共用的單執行緒——在這裡 sleep 或等 HTTPS 就等於全部
    # 分頁一起卡。短的 flood-wait 就地重試一次；長的交給呼叫端退避後重抽
    # （回覆沒進去重集合，不會遺失）。
    # M-4：最壞阻塞必須壓在改動前（單次 tg_api 預設 timeout=35s）之下。
    #   10s + sleep(≤3s) + 10s = 23s < 35s。逾時警告改走背景 thread，不佔這條路。
    _SEND_INLINE_RETRY_MAX_S = 3.0
    _SEND_TIMEOUT_S = 10.0
    # 送出失敗的 ⚠ 警告：每個 slot 最少間隔這麼久才再發一次（防洗版）。
    _SEND_FAIL_NOTIFY_INTERVAL_S = 300.0

    # 永久性投遞失敗——重試再多次也不會成功（使用者封鎖了 bot、把 bot 踢出
    # 群組、chat 不存在、帳號被停用）。這種收件人**不可以**讓整批判定成失敗，
    # 否則已經成功收到的人會被無限重送（回報 2026-08-19「對話1跳針」：兩個
    # chat 封鎖了 bot → 每輪 flush 都 FAILED → 不進去重 → 重抽重送，但他其實
    # 每次都收到了）。
    _PERMANENT_SEND_RE = re.compile(
        r"bot was blocked by the user|user is deactivated|chat not found"
        r"|bot was kicked|have no rights to send|PEER_ID_INVALID", re.I)

    def _send_text_checked(self, sid: str, chat_id, text: str):
        """sendMessage + **檢查回傳值**。回 (ok, retry_after_seconds, permanent)。

        P0-3：舊版是 fire-and-forget——`tg_api` 把 429 / 400 / 逾時 / DNS 全部
        轉成 {"ok": False, …} 回傳值，而呼叫端完全不看，回覆卻已經先進了
        `sent_responses` → **永不重送、也永不會被重新抽取**（全 repo grep
        429|retry_after 零命中）。"""
        resp = tg_api(self.config.bot_token, "sendMessage",
                      {"chat_id": chat_id, "text": text},
                      timeout=self._SEND_TIMEOUT_S)
        if resp.get("ok"):
            return True, 0.0, False
        desc = str(resp.get("description") or "")
        m = self._RETRY_AFTER_RE.search(desc)
        wait = float(m.group(1)) if m else 0.0
        if wait and wait <= self._SEND_INLINE_RETRY_MAX_S:
            _blog(f"[send] {sid} → {chat_id} flood-wait {wait:.0f}s, retrying once\n")
            time.sleep(wait)
            resp = tg_api(self.config.bot_token, "sendMessage",
                          {"chat_id": chat_id, "text": text},
                          timeout=self._SEND_TIMEOUT_S)
            if resp.get("ok"):
                return True, 0.0, False
            desc = str(resp.get("description") or "")
            m = self._RETRY_AFTER_RE.search(desc)
            wait = float(m.group(1)) if m else 0.0
        permanent = bool(self._PERMANENT_SEND_RE.search(desc))
        _blog(f"[send] {sid} → {chat_id} sendMessage NOT ok"
              f"{' (PERMANENT)' if permanent else ''}: {desc[:160]}\n")
        return False, wait, permanent

    @staticmethod
    def _fmt_waited(sec: float) -> str:
        sec = int(max(0, sec))
        if sec < 60:
            return f"{sec} 秒"
        if sec < 3600:
            return f"{sec // 60} 分 {sec % 60} 秒"
        return f"{sec // 3600} 小時 {(sec % 3600) // 60} 分"

    def _heartbeat_status_line(self, sid: str):
        """狀態行（第 2 行）——只讀 main.py monitor thread 已經算好的
        StatusTracker 快取，**不觸發** transcript 解析。拿不到就回 ''，心跳
        照發不誤（降級成第 1 行 + 出口提示，絕不因此靜默）。"""
        cb = getattr(self, "_on_agent_status", None)
        if not cb:
            return ""
        t0 = self._perf_t()
        try:
            got = cb(sid)
        except Exception:
            return ""
        finally:
            self._perf_end("heartbeat_status", t0)
        if not got:
            return ""
        res, age = got if isinstance(got, tuple) else (got, 0.0)
        if not res:
            return ""
        if age is not None and age > self.HEARTBEAT_STATUS_MAX_AGE_S:
            return ""      # 資料太舊，講出來只會誤導
        state = res.get("state") or ""
        action = res.get("action") or res.get("summary") or ""
        task = res.get("task") or ""
        bits = [b for b in (state, action) if b]
        line = " · ".join(bits)
        if task and task not in line:
            line = f"{line} — {task}" if line else task
        return line[:180]

    def _heartbeat_bg_line(self, slot):
        """第 3 行：畫面尾端的「等 N 個背景 agent / ↓ tokens」。走既有
        _live_tail（_feed_gen display 快取），不新增 render。"""
        t0 = self._perf_t()
        try:
            tail = self._live_tail(slot, rows=6) or ""
        except Exception:
            return ""
        finally:
            self._perf_end("heartbeat_bg", t0)
        m = self._BG_AGENT_RE.search(tail)
        if not m:
            return ""
        out = f"在等 {m.group(1)} 個背景 agent"
        t = self._BG_TOKENS_RE.search(tail)
        if t:
            out += f"（{t.group(1).replace(' ', '')} tokens）"
        return out

    def _send_heartbeat(self, sid: str, waited: float):
        """背景 thread：發一則「還在跑」的心跳。閘門在 _flush_loop（A4.2）。

        perf 計時：**整支函式刻意不計時**。2026-08-17 實機量到
        `heartbeat_send=945.6ms/1x` —— 那 945ms 幾乎全是 sendMessage 的 HTTPS
        往返，在背景 thread、不吃 flush loop 的 CPU，卻會把 60s 摘要的合計
        直接推爆 150ms 紅線、淹掉真正的迴歸訊號。改成只計 CPU 段
        （`heartbeat_status` / `heartbeat_bg` / `preview_peek`），送出本身以
        `[heartbeat]` log 行記錄。`_set_reaction` 同理（SA A3.6 亦如此規定）。
        """
        slot = self.slots.get(sid)
        if not slot:
            return
        chats = self._target_chats_for(sid)
        if not chats:
            return
        status_line = self._heartbeat_status_line(sid)
        bg_line = self._heartbeat_bg_line(slot)

        # ── 內容 hash 去重（M-2 修正）──
        # 舊條件 `sig == _hb_last_hash and _hb_count > 1` 是錯的：跳過時不更新
        # `_hb_last_hash`，於是**狀態一旦不變，第 2 則之後全部永久靜音**。而
        # 「長時間卡住、狀態一直沒變」正是 s87「愛回不回」的形狀——等於我們修
        # s87 的功能在 s87 的情境下自己失效（QA M-2）。
        # SA 規格是「內容相同 **且** 距上次實際送出未滿 2 × 當前間隔」才跳過：
        # 退避計數照樣推進，所以沉默會愈拉愈長，但**永遠不會永久靜音**。
        # 另外：`status_line` 與 `bg_line` 都空時（main.py 還沒 restart、或
        # shell 分頁）根本沒有「資訊」可以去重，訊息裡唯一會變的就是已等時間
        # ——這種情況不做內容去重，交給退避節流就好，否則就是保證只發一則。
        now = time.time()
        sig = f"{status_line}|{bg_line}"
        informative = bool(status_line or bg_line)
        interval = slot._hb_interval or self.HEARTBEAT_INTERVAL_S
        if (informative and sig == slot._hb_last_hash
                and slot._hb_last_sent_ts
                and (now - slot._hb_last_sent_ts) < 2 * interval):
            _blog(f"[heartbeat] {sid} skipped (same content, "
                  f"{now - slot._hb_last_sent_ts:.0f}s < 2×{interval:.0f}s) "
                  f"waited={waited:.0f}s\n")
            return
        slot._hb_last_hash = sig
        slot._hb_last_sent_ts = now

        lines = [f"⏳「{slot.label}」還在跑 · 已 {self._fmt_waited(waited)}"]
        if status_line:
            lines.append(f"   {status_line}")
        if bg_line:
            lines.append(f"   {bg_line}")

        preview = self._maybe_preview(slot, waited)
        if preview:
            lines.append("   ── 進行中預覽（非最終回覆）──")
            lines.append(f"   {preview}")

        lines.append(f"   /{slot.index} 切過去看 · /fetch 抓現況 · /quiet 這輪別再提醒")
        text = "\n".join(lines)
        _blog(f"[heartbeat] {sid} waited={waited:.0f}s n={slot._hb_count} "
              f"state={status_line[:40]!r} preview={bool(preview)}\n")
        for chat_id in chats:
            try:
                tg_api(self.config.bot_token, "sendMessage",
                       {"chat_id": chat_id, "text": text}, timeout=10)
            except Exception as e:
                _blog(f"[heartbeat] {sid} send to {chat_id} failed: {e}\n")

    def _maybe_preview(self, slot, waited: float) -> str:
        """A5.2 折衷：超長回合的「進行中預覽」。

        ⚠ **S1（唯一不可退讓）：預覽內容絕對不進 `slot.sent_responses`。**
        它走的是心跳訊息，不是回覆路徑——一旦進了去重集合，真正的完整回覆
        來的時候會被當成「已送過」永久壓制（洞 #13/#10）。
        S2：不動 expect_marker / pending_raw / marker_forwarded（不中止監聽）。
        S3：呼叫端一定加「進行中預覽（非最終回覆）」字樣。
        S4：每 epoch 上限 PREVIEW_MAX 次。
        S5+S6：_feed_gen dirty gate ＋ 與上次相同就跳過（tmux capture 3000 行很貴）。
        """
        if waited < self.PREVIEW_AFTER_S:
            return ""
        if slot._preview_count >= self.PREVIEW_MAX:
            return ""
        gen = getattr(slot, "_feed_gen", 0)
        if gen == slot._preview_gen:
            return ""            # buffer 沒新 bytes → 不可能有新東西可預覽
        slot._preview_gen = gen
        t0 = self._perf_t()
        try:
            raw = self._marker_fallback_text(slot) or ""
        except Exception:
            raw = ""
        finally:
            self._perf_end("preview_peek", t0)
        raw = " ".join(raw.split())
        if not raw:
            return ""
        body = raw[:self.PREVIEW_CHARS] + ("…" if len(raw) > self.PREVIEW_CHARS else "")
        if body == slot._preview_last:
            return ""            # S6：卡死的分頁不該重複貼同一段
        slot._preview_last = body
        slot._preview_count += 1
        return body

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

            # ── 長回合心跳閘門（A4.2）──
            # 全部掛在既有的 2s slow_tick 上，**不新增迴圈、不新增掃描**。
            # 這裡只做欄位比較（O(1)/slot，實測見 heartbeat_gate phase）：
            # 不碰輸出緩衝、不 render screen、不跑 regex。唯一昂貴的動作
            # （live tail 讀取、tmux peek）都在 _send_heartbeat 的背景 thread
            # 裡，而且最快 300s 才一次。
            if slow_tick:
                _t_hb = self._perf_t()
                now_hb = time.time()
                for sid in sids:
                    slot = self.slots.get(sid)
                    if not slot:
                        continue
                    # G1 有等待中的使用者訊息（idle slot 直接出局＝等價 dirty gate）
                    if not slot.awaiting_response:
                        continue
                    # G-quiet 使用者這輪說了「別吵」
                    if slot._hb_quiet:
                        continue
                    # G2 這個 epoch 還沒回過任何東西
                    if slot.marker_forwarded:
                        continue
                    if slot.last_extraction_ts > slot.msg_sent_ts:
                        continue
                    # G3 首次門檻
                    waited = now_hb - (slot.msg_sent_ts or now_hb)
                    if waited < self.HEARTBEAT_FIRST_S:
                        continue
                    # G4 間隔節流（指數退避）
                    if now_hb < slot._hb_next_ts:
                        continue
                    # G5 有人收（不要對空氣心跳）
                    if not self._target_chats_for(sid):
                        continue
                    interval = min(
                        self.HEARTBEAT_MAX_S,
                        self.HEARTBEAT_INTERVAL_S
                        * (self.HEARTBEAT_BACKOFF ** slot._hb_count))
                    slot._hb_next_ts = now_hb + interval
                    slot._hb_interval = interval   # 內容去重的 2×間隔基準
                    slot._hb_count += 1
                    threading.Thread(target=self._send_heartbeat,
                                     args=(sid, waited), daemon=True).start()
                self._perf_end("heartbeat_gate", _t_hb)

            # Rate-limit detection: scan live screen for session/usage-limit
            # banners and /rate-limit-options menus. Runs on every slow_tick
            # (2s cadence) so it catches episodes quickly regardless of whether
            # the affected tab has an active user or has_user_msg set.
            if slow_tick and _read_settings().get("rate_limit_notify", True):
                for sid in sids:
                    slot = self.slots.get(sid)
                    if not slot:
                        continue
                    try:
                        info = self._detect_rate_limit(slot)
                    except Exception as e:
                        _blog(f"[rate-limit] {sid} detect failed: {e}\n")
                        continue
                    if info is not None:
                        if not slot.rate_limit_notified:
                            slot.rate_limit_notified = True
                            threading.Thread(
                                target=self._notify_rate_limit,
                                args=(slot, info),
                                daemon=True,
                            ).start()
                    else:
                        # Signal gone (limit reset / user acted) — clear flag
                        # so the next episode re-notifies.
                        slot.rate_limit_notified = False

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

                # P0-3/P0-4 commit model：抽取階段**只**決定「要送什麼」，所有
                # 不可逆副作用（進去重集合、清 pending_raw、關 marker 監聽）
                # 一律等 sendMessage 真的回 ok:true 才套用。舊版順序相反，
                # 於是 TG 一失敗／沒有收件人，回覆就永久蒸發。
                dedup_pending = []
                commit_fallback_reset = False
                commit_marker_forwarded = False

                with slot.output_lock:
                    if slot.last_output_time == 0:
                        continue
                    tick_busy = True  # pending output → stay on the fast cadence
                    if not slot.has_user_msg:
                        # Drain old content so it won't be re-extracted later
                        # when a TG message arrives. This advances _history_offset
                        # and marks existing AI blocks as "sent".
                        # Master-delegated workers live here (the maintainer never DM'd
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
                        # 回歸守則：這裡在 total ≥ 120s 後每 tick 都會進來
                        # （idle<3 的閘門被 total 分支繞過），所以掃描本身
                        # 必須節流 + dirty-gated——2026-07-06 的 96% CPU 事故
                        # 就是這條路徑每 0.5s 對 120KB pending_raw 跑兩次
                        # strip_ansi。節流邏輯在 _try_marker_extract 內。
                        _t_mk = self._perf_t()
                        marked_reply = self._try_marker_extract(slot, now, total)
                        self._perf_end("extract_marker", _t_mk)
                        if not marked_reply:
                            # FALLBACK（v0.29.15，回報：「回覆傳不回來、都要自己
                            # fetch」）：模型有時根本沒吐出 [[TG_REPLY]] marker（忘了
                            # 或吐錯），舊版就永遠等一個不會出現的 marker → 無限
                            # 靜默。這裡**不重置 last_output_time**（讓 flush 每
                            # tick 重入持續嘗試；marker 掃描由 _try_marker_extract
                            # 自身節流），並在 turn 結束（live tail 無 'esc to
                            # interrupt'）且等夠久後，改用 /fetch 那條純文字抽取
                            # 自動轉發，使用者不必再手動 fetch。fallback 的 _peek
                            # 另用 _fb_next_ts 節流到每 3s，避免每 tick 都 tmux
                            # capture。
                            turn_ended = not re.search(
                                r'esc to interrupt', self._live_tail(slot), re.I)
                            # marker_forwarded=True 代表這 epoch 已用 marker 轉發過
                            # → 「沒有新 block」是正常等待，不是漏 marker，別 fallback
                            # 發 peek（會重送）。只有從頭到尾都沒 marker 才 fallback。
                            if slot.marker_forwarded:
                                continue
                            # 時鐘用「自這則使用者訊息送出」算，不用 total（見
                            # msg_sent_ts 註解）——否則忙碌分頁一送新訊息就誤觸。
                            waited = now - (slot.msg_sent_ts or now)
                            if not (turn_ended and waited >= self._MARKER_FALLBACK_SECS):
                                continue
                            if now < getattr(slot, "_fb_next_ts", 0.0):
                                continue
                            slot._fb_next_ts = now + 3.0
                            fb = self._marker_fallback_text(slot)
                            # 去重：畫面上若還是上一則（已送過的）回覆，絕不重送
                            # ——這是「剛送出就回上一則」重複的直接防線。
                            if not fb or fb in slot.sent_responses:
                                continue
                            _blog(f"[send] {sid} marker missing → fallback "
                                  f"forward ({len(fb)} chars) after {total:.0f}s\n")
                            new_lines = [fb]
                            # P0-3/P0-4：**不再**在送出前就 add()／清 buffer。
                            # 舊版先污染去重集合再 fire-and-forget sendMessage，
                            # 429/400/逾時一律回覆永久蒸發（永不重送、永不重抽）。
                            # 這些副作用改到「TG 真的回 ok:true」之後才 commit。
                            dedup_pending = [fb]
                            commit_fallback_reset = True
                        else:
                            # Drain pyte history so the same screen repaint is not
                            # re-extracted on a later mobile turn.
                            try:
                                self._extract_new_text(slot)
                            except Exception:
                                pass
                            new_lines = [marked_reply]
                            # P0-3：add() 延到 sendMessage 回 ok:true 之後。
                            dedup_pending = [marked_reply]
                            commit_marker_forwarded = True
                            # Follow-up 連續訊息（回報 2026-07-26：「只回一則、
                            # 背景 subagent 完成的訊息漏掉」）：**不再**清掉
                            # expect_marker / markers / has_user_msg——保持 marker
                            # 監聽，AI 之後每包一個新的 [[TG_REPLY]] block（例如
                            # 背景 worker 跑完的完成通知）都會被當「新 block」轉發
                            # 一次（去重在 _try_marker_extract）。只有新使用者訊息
                            # 才重置 token / marker_forwarded。marker_forwarded 讓
                            # fallback 只在完全沒用過 marker 時才觸發。
                            # 不清 pending_raw：後續 block 會 append 進來，靠
                            # sent_responses 去重避免重送舊的。
                    else:
                        # Extract new text via screen diff (only final changes).
                        # Guarded: _extract_new_text does heavy pyte/regex parsing;
                        # a throw on pathological screen content used to propagate
                        # out of the flush loop and silently kill the thread
                        # (daemon-thread exceptions go to stderr, not the log →
                        # no traceback, auto-reply just stops for ALL tabs).
                        _t_ex = self._perf_t()
                        try:
                            new_lines = self._extract_new_text(slot)
                        except Exception as e:
                            _blog(f"[flush] {sid} extract_new_text failed "
                                  f"(loop survives): {type(e).__name__}: {e}\n")
                            new_lines = []
                        self._perf_end("extract_new_text", _t_ex)
                        # P0-3：_extract_new_text 內部已先 add() 進去重集合。
                        # 這裡先撤回，統一由 commit 階段在送成功後才寫入——
                        # 否則送失敗／沒收件人時同樣是永久蒸發。
                        for _t in new_lines:
                            slot.sent_responses.discard(_t)
                        dedup_pending = list(new_lines)
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
                        # — anything the user sees in scroll-up that contradicts
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
                # marker lines out of the forwarded text. Guarded like the drain
                # path (2467) — a board/signal error must not kill the flush
                # thread (would stop auto-reply for ALL tabs).
                try:
                    _t_b = self._perf_t()
                    new_lines = self._detect_and_apply_board(slot, new_lines)
                    self._perf_end("detect_board", _t_b)
                    _t_s = self._perf_t()
                    new_lines = self._detect_and_fire_signal(slot, new_lines)
                    self._perf_end("detect_signal", _t_s)
                except Exception as e:
                    _blog(f"[flush] {sid} board/signal detect failed "
                          f"(loop survives): {type(e).__name__}: {e}\n")
                if not new_lines:
                    # 整段被 board/signal 吃掉（例如純 [[SF:GREEN]] 行）：沒有東西
                    # 要送，但內容確實**已經處理過**——必須進去重集合，否則下一波
                    # 輸出會把同一段重抽一次。這不是 P0-3 的「送出失敗」情境。
                    for _t in dedup_pending:
                        slot.sent_responses.add(_t)
                    continue

                clean = '\n'.join(new_lines)
                is_menu_prompt = bool(
                    slot.pending_menu
                    and new_lines
                    and len(new_lines) == 1
                    and new_lines[0].startswith("❓ ")
                )

                # Detect file paths + split for TG. Guarded: regex/splitting on
                # pathological reply content must not kill the flush thread.
                try:
                    file_paths = self._extract_file_paths(clean)
                except Exception as e:
                    _blog(f"[flush] {sid} extract_file_paths failed: "
                          f"{type(e).__name__}: {e}\n")
                    file_paths = []

                # Tag with session label
                prefix = f"[{slot.label}] " if len(self.slots) > 1 else ""
                msg = prefix + clean

                # Long replies are split into multiple TG messages (≤4096 cap),
                # never truncated. Menu prompts stay single (kept short by design).
                try:
                    msg_parts = [msg] if is_menu_prompt else split_for_telegram(msg)
                except Exception as e:
                    _blog(f"[flush] {sid} split_for_telegram failed: "
                          f"{type(e).__name__}: {e}\n")
                    msg_parts = [msg[:3900]]

                # Collect target chat_ids
                target_chats = self._target_chats_for(sid)

                # ── P0-4：沒有任何收件人 → 保留，不要標記成已送 ──
                # 舊版照樣把回覆加進 sent_responses 再送給零個人，之後連
                # /fetch 都救不回來（去重集合已污染）。master 派工出去的
                # worker 分頁天生落在這個洞裡。
                if not target_chats:
                    _blog(f"[flush] {sid} no target chat, keep for /fetch "
                          f"({len(new_lines)} block(s) held)\n")
                    continue

                # 逐收件人判定（v0.29.36）。舊版一律「任一失敗＝整批 FAILED」，
                # 於是**一個封鎖 bot 的 chat 就能讓所有正常收件人被無限重送**。
                # 現在分三種結果：any_ok（有人真的收到）、retryable_fail
                # （429/逾時/網路——值得重抽）、permanent_fail（403 封鎖等，
                # 重試無意義）。
                any_ok = False
                retryable_fail = False
                dead_chats = set()
                retry_after = 0.0
                for chat_id in target_chats:
                    # 每個收件人的送訊/送檔各自 try——一個 send 失敗（尤其
                    # v0.29.14 影片走 _send_tg_file：大檔 sendDocument 失敗、
                    # 路徑消失）以前會**衝出 flush 迴圈、靜默殺掉整條 flush
                    # 執行緒 → 所有分頁停止自動回覆**（回報 2026-07-14
                    # 「/fetch 後很容易斷、不自動回覆」的根因）。現在只記錄
                    # 跳過，執行緒永不因單次 send 而死。
                    try:
                        if is_menu_prompt:
                            self._send_choice_menu(chat_id, slot, msg)
                            any_ok = True
                        else:
                            for part in msg_parts:
                                ok, ra, perm = self._send_text_checked(
                                    sid, chat_id, part)
                                if ok:
                                    any_ok = True
                                elif perm:
                                    dead_chats.add(chat_id)
                                else:
                                    retryable_fail = True
                                    retry_after = max(retry_after, ra)
                        # Send detected files as documents
                        for fp in file_paths:
                            self._send_tg_file(chat_id, fp)
                    except Exception as e:
                        retryable_fail = True
                        _blog(f"[flush] {sid} send to {chat_id} failed "
                              f"(loop survives): {type(e).__name__}: {e}\n")

                # 永久失效的收件人：從路由表移除，否則每則回覆都要再撞一次 403，
                # 而且警告也會一直發給收不到的人。
                if dead_chats:
                    for _uid, _cid in list(self._user_chat.items()):
                        if _cid in dead_chats:
                            self._user_chat.pop(_uid, None)
                            self._user_active.pop(_uid, None)
                    _blog(f"[flush] {sid} dropped dead chat(s) "
                          f"{sorted(dead_chats)} — blocked/invalid, unrouted\n")

                # ── commit / rollback（P0-3，v0.29.36 改為逐收件人）──
                # commit 條件：有人真的收到，或雖然沒人收到但**全是永久失敗**
                # （重抽重送也不會成功，留在 buffer 只會變成無限重試）。
                # 只有「可重試的失敗」才 rollback 重抽。
                if any_ok or not retryable_fail:
                    for _t in dedup_pending:
                        slot.sent_responses.add(_t)
                    if commit_marker_forwarded:
                        slot.marker_forwarded = True
                    if commit_fallback_reset:
                        with slot.output_lock:
                            slot.pending_raw = ""
                            slot.expect_marker = False
                            slot.reply_start_marker = ""
                            slot.reply_end_marker = ""
                            slot.marker_prompt = ""
                            slot.has_user_msg = False
                            slot.marker_next_scan_ts = 0.0
                            slot.marker_scan_gen = -1
                            slot._fb_next_ts = 0.0
                    # 回覆真的出去了 → 關掉這個 epoch 的心跳
                    slot._hb_count = 0
                    slot._hb_next_ts = 0.0
                    slot._hb_interval = 0.0
                    slot._hb_last_hash = ""
                    slot._hb_last_sent_ts = 0.0
                else:
                    # 沒進去重集合＝下一次掃描還會重抽，不會永久蒸發。但要退避，
                    # 免得 TG 掛掉時每 0.5s 重試變成打樁。429 就等到 flood-wait
                    # 結束再重抽（不在 flush loop 裡 sleep 那麼久，見
                    # _send_text_checked）。
                    back = max(30.0, retry_after + 1.0)
                    slot.marker_next_scan_ts = max(
                        slot.marker_next_scan_ts, time.time() + back)
                    slot._fb_next_ts = max(
                        getattr(slot, "_fb_next_ts", 0.0), time.time() + back)
                    _blog(f"[flush] {sid} send FAILED → kept out of dedup, "
                          f"will re-extract\n")
                    # M-4：警告本身要節流，而且**不能在 flush loop 裡等 HTTPS**。
                    # 舊版對每個 target chat 直接 sendMessage、零節流——一旦撞上
                    # 洗版或 TG 掛掉，警告會自己變成第二波洗版，而且把單執行緒的
                    # flush loop 一起拖住（所有分頁的 TG 收送同時停擺）。
                    now_fail = time.time()
                    if now_fail >= slot._send_fail_warn_ts:
                        slot._send_fail_warn_ts = (
                            now_fail + self._SEND_FAIL_NOTIFY_INTERVAL_S)
                        warn = (f"⚠ 「{slot.label}」的回覆送出失敗（Telegram "
                                "拒收或逾時）。內容沒有遺失——用 /fetch 重取。")
                        for chat_id in (target_chats - dead_chats):
                            threading.Thread(
                                target=tg_api,
                                args=(self.config.bot_token, "sendMessage",
                                      {"chat_id": chat_id, "text": warn}),
                                kwargs={"timeout": 5}, daemon=True).start()

            # Adaptive-cadence bookkeeping: grow the idle streak on fully-quiet
            # ticks, reset the moment any slot needs attention.
            idle_streak = 0 if tick_busy else (idle_streak + 1)

    _MODEL_PICKER_HEADER_RE = re.compile(r'Select model', re.I)
    _MODEL_EFFORT_RE = re.compile(r'[◉○].*effort', re.I)

    def _parse_model_menu(self, lines):
        """從 live display 解析 Claude Code /model picker。
        回 (options, effort_line) 或 None。options=[{num,label,desc,current}]。
        實測（CC 2.1.x）：選項為「N. Name[ ✔]   描述」、游標 ❯、footer 有
        Esc to cancel；「◉ xHigh effort ←/→」是 chrome。"""
        text = "\n".join(lines)
        if not self._MODEL_PICKER_HEADER_RE.search(text):
            return None
        options = []
        effort_line = ""
        for line in lines:
            stripped = line.lstrip().lstrip('❯›').lstrip()
            m = _MENU_ITEM_RE.match(stripped)
            if m:
                num, rest = m.group(1), m.group(2).strip()
                parts = re.split(r'\s{2,}', rest, maxsplit=1)
                name = parts[0].strip()
                desc = parts[1].strip() if len(parts) > 1 else ""
                options.append({
                    "num": num,
                    "label": name.replace("✔", "").strip(),
                    "desc": desc,
                    "current": "✔" in rest,
                })
            elif self._MODEL_EFFORT_RE.search(line):
                effort_line = line.strip()
        if len(options) < 2:
            return None
        return options, effort_line

    def _handle_model_command(self, user_id: int, chat_id: int):
        """TG /model：把原生 /model 送進 active 分頁、等 picker 出現、
        把選項變成 inline 按鈕。按鈕→送數字（picker 數字鍵＝立即選定）。"""
        active_sid = self.get_active_sid(user_id)
        slot = self.slots.get(active_sid) if active_sid else None
        if not slot:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": "沒有 active session，先用 /list 選一個。"})
            return

        def _run():
            with slot.write_lock:
                disp = "\n".join(self._slot_display(slot))
                if re.search(r"esc to interrupt", disp, re.I):
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"「{slot.label}」正在跑回合中，等它結束再 /model。"})
                    return
                slot.write_fn("\x15")          # 清輸入框殘字
                time.sleep(0.15)
                slot.write_fn("/model")
                time.sleep(0.3)
                slot.write_fn("\r")
            menu = None
            for _ in range(16):                 # 最多等 ~4.8s
                time.sleep(0.3)
                try:
                    menu = self._parse_model_menu(self._slot_display(slot))
                except Exception:
                    menu = None
                if menu:
                    break
            if not menu:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": (f"已把 /model 送進「{slot.label}」，但沒偵測到選單"
                             "（分頁可能不是 claude、或畫面被其他東西佔住）。")})
                return
            options, effort_line = menu
            slot.pending_menu = True
            slot.pending_menu_options = [
                {"num": o["num"], "text": o["label"]} for o in options]
            lines = [f"🎛 {slot.label} — 選擇模型（點按鈕立即生效，並存為新 session 預設）"]
            for o in options:
                mark = " ✔（目前）" if o["current"] else ""
                desc = f" — {o['desc']}" if o["desc"] else ""
                lines.append(f"{o['num']}. {o['label']}{mark}{desc}")
            if effort_line:
                lines.append(f"（{effort_line}，effort 調整請在桌面端操作）")
            keyboard = []
            for o in options[:9]:
                mark = " ✔" if o["current"] else ""
                keyboard.append([{
                    "text": f"{o['num']}. {o['label']}{mark}",
                    "callback_data": f"mchoice:{slot.sid}:{o['num']}"}])
            keyboard.append([{"text": "✖ 取消（Esc）",
                              "callback_data": f"mcancel:{slot.sid}"}])
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": "\n".join(lines),
                "reply_markup": {"inline_keyboard": keyboard},
            })
        threading.Thread(target=_run, daemon=True).start()

    # ── /effort：調整 active 分頁的推理深度（claude + codex 統一）──────────
    # claude 原生 `/effort <level>`（滑桿層級 low→ultracode，帶參數會跳
    # Yes/No 確認）；codex 走 `/model`→Enter 保留模型→reasoning 編號選單。
    # 兩邊 UX 不同，這裡收斂成一組 TG inline 按鈕。
    _EFFORT_CLAUDE = [
        ("low", "Low 最快"), ("medium", "Medium"), ("high", "High（預設）"),
        ("xhigh", "xHigh"), ("max", "Max 最深"), ("ultracode", "Ultracode（+workflows）"),
    ]
    # codex reasoning 選單編號（實測 gpt-5.6）：1 Low / 2 Medium / 3 High /
    # 4 Extra high / 5 More reasoning…(Max)
    _EFFORT_CODEX = [
        ("1", "Low 最快"), ("2", "Medium"), ("3", "High"),
        ("4", "Extra high"), ("5", "Max 最深"),
    ]

    def _handle_effort_command(self, user_id: int, chat_id: int):
        """TG /effort：把 active 分頁目前的 AI（claude/codex）推理深度層級
        變成 inline 按鈕；點按鈕即套用（efchoice callback 各自驅動原生 UX）。"""
        active_sid = self.get_active_sid(user_id)
        slot = self.slots.get(active_sid) if active_sid else None
        if not slot:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": "沒有 active session，先用 /list 選一個。"})
            return
        kind = _detect_ai(getattr(slot, "cmd", "") or "")
        if kind not in ("claude", "codex"):
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"「{slot.label}」不是 claude/codex 分頁，沒有推理深度可調。"})
            return
        if re.search(r"esc to interrupt", self._live_tail(slot), re.I):
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"「{slot.label}」正在跑回合中，等它結束再 /effort。"})
            return
        levels = self._EFFORT_CLAUDE if kind == "claude" else self._EFFORT_CODEX
        # 目前層級（盡力偵測）：狀態列的 ◈/◉ <level> 或 codex header 的 model 行
        cur = ""
        try:
            disp = "\n".join(self._slot_display(slot))
            m = re.search(r'[◈◉]\s*(low|medium|high|xhigh|max|ultracode)\b', disp, re.I)
            if m:
                cur = m.group(1).lower()
            elif kind == "codex":
                m = re.search(r'model:\s*\S+\s+(low|medium|high|extra high|\S+)',
                              disp, re.I)
                if m:
                    cur = m.group(1).lower()
        except Exception:
            pass
        keyboard = []
        for token, label in levels:
            mark = " ✔" if cur and (cur in label.lower() or label.lower().startswith(cur)) else ""
            keyboard.append([{"text": f"{label}{mark}",
                              "callback_data": f"efchoice:{kind}:{slot.sid}:{token}"}])
        keyboard.append([{"text": "✖ 取消", "callback_data": f"efcancel:{slot.sid}"}])
        head = f"🧠 {slot.label}（{kind}）— 選推理深度" + (f"　目前：{cur}" if cur else "")
        tg_api(self.config.bot_token, "sendMessage", {
            "chat_id": chat_id, "text": head,
            "reply_markup": {"inline_keyboard": keyboard}})

    def _apply_effort_claude(self, slot, level: str):
        """claude：送 /effort <level>，若跳 Yes/No 確認就答 1。回確認字串或 ''。"""
        with slot.write_lock:
            slot.write_fn("\x15")            # 清輸入框
            time.sleep(0.15)
            slot.write_fn(f"/effort {level}")
            time.sleep(0.3)
            slot.write_fn("\r")
        # 等畫面：可能直接生效，或跳「Change effort level? 1. Yes …」確認。
        # 讀 _live_tail（濾空列取尾端）而非 display[-N:]——pyte 螢幕固定 50 列，
        # 實際終端較矮時尾端切片全是空白列，確認字串永遠讀不到（使用者
        # 2026-07-27：ultracode 明明套用成功卻回「沒在畫面看到確認」）。
        for _ in range(14):
            time.sleep(0.4)
            tail = self._live_tail(slot, rows=14)
            if re.search(r"Change effort level\?", tail, re.I):
                with slot.write_lock:
                    slot.write_fn("1\r")     # Yes, switch
                continue
            m = re.search(r'(?:effort level (?:as|to)|thinking with)\s+(\w+)', tail, re.I)
            if m:
                return m.group(1)
        return ""

    def _apply_effort_codex(self, slot, num: str):
        """codex：/model → Enter 保留模型 → reasoning 編號選單 → num → Enter。"""
        with slot.write_lock:
            slot.write_fn("\x15")
            time.sleep(0.15)
            slot.write_fn("/model")
            time.sleep(0.3)
            slot.write_fn("\r")
        # 等 model picker，Enter 保留目前模型 → 進 reasoning 步驟
        got_reasoning = False
        for _ in range(14):
            time.sleep(0.4)
            tail = self._live_tail(slot, rows=16)
            if re.search(r"Select Reasoning Level", tail, re.I):
                got_reasoning = True
                break
            if re.search(r"Select Model and Effort|Select Model", tail, re.I):
                with slot.write_lock:
                    slot.write_fn("\r")      # 保留目前模型，進 effort 步驟
        if not got_reasoning:
            return ""
        with slot.write_lock:
            slot.write_fn(num)
            time.sleep(0.2)
            slot.write_fn("\r")
        for _ in range(10):
            time.sleep(0.4)
            tail = self._live_tail(slot, rows=16)
            m = re.search(r'model:\s*\S+\s+(low|medium|high|extra high|\w+)', tail, re.I)
            if m:
                return m.group(1)
        return "已送出"

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

    # P0-7：_poll_loop 在 enqueue **之前**就存了 offset（為了防「重啟重複處理
    # 同一則」）。代價是 reload/restart 時還躺在 _update_queue 裡、還沒被
    # _dispatch_loop 取走的訊息，永遠不會再被 getUpdates 取回 → 永久遺失，
    # 而且一行 log 都沒有。改動 offset 時序會把重啟迴圈的舊病帶回來，所以改用
    # 「停止時把殘留佇列落盤、下次啟動重播」——純加法、不動既有 offset 語義。
    _PENDING_FILE = _Path.home() / ".config" / "shellframe" / "tg_pending.json"
    _PENDING_MAX = 20
    # 重播時一律濾掉自我重啟指令，避免「重啟 → 重播 /restart → 再重啟」迴圈
    # （這正是 offset 先存的原始理由）。
    _PENDING_SKIP_CMDS = ("/restart", "/reload", "/update_now", "/update")

    def _persist_pending_updates(self):
        left = []
        try:
            while True:
                left.append(self._update_queue.get_nowait())
        except _queue.Empty:
            pass
        except Exception:
            pass
        if not left:
            # ⚠ 這裡**不能**刪檔（2026-08-17 實機驗證抓到）：佇列空只代表「這一輪
            # 沒有待處理訊息」，不代表磁碟上那份是垃圾。檔案只會由
            # _replay_pending_updates() 讀完後刪除，所以它存在＝上一輪存了、還沒
            # 有人重播過。在這裡刪掉，等於兩次快速 reload（第二次的 poll loop
            # 還沒跑到 replay 就又被 stop）就把上一輪的訊息永久丟掉——正是 P0-7
            # 要修的那個病。
            return
        try:
            self._PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._PENDING_FILE.write_text(
                json.dumps(left[-self._PENDING_MAX:], ensure_ascii=False),
                encoding='utf-8')
            _blog(f"[poll] stop: persisted {len(left)} queued update(s) for replay\n")
        except Exception as e:
            _blog(f"[poll] stop: persist pending failed: {e}\n")

    def _replay_pending_updates(self):
        try:
            if not self._PENDING_FILE.exists():
                return 0
            data = json.loads(self._PENDING_FILE.read_text(encoding='utf-8'))
        except Exception:
            data = None
        try:
            self._PENDING_FILE.unlink()
        except Exception:
            pass
        n = 0
        for upd in (data or []):
            if not isinstance(upd, dict):
                continue
            text = ((upd.get("message") or {}).get("text") or "").strip().lower()
            # M-5：必須跟指令派發端（_handle_update）用同一種剝法——群組裡
            # Telegram 客戶端送出的是 `/restart@YourBot`，只切空白會漏掉 →
            # 重播 /restart → 再重啟，正是這個過濾器要防的迴圈。
            cmd = text.split()[0].split("@")[0] if text else ""
            if cmd in self._PENDING_SKIP_CMDS:
                _blog(f"[poll] replay: skipping self-restart cmd {cmd}\n")
                continue
            self._update_queue.put(upd)
            n += 1
        if n:
            _blog(f"[poll] replayed {n} queued update(s) from previous run\n")
        return n

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
        # P0-7：把上一輪 reload/restart 時卡在佇列裡的訊息接回來（offset 已經
        # 存過，getUpdates 不會再給我們一次）。
        self._replay_pending_updates()
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
                    # Hand off to the dispatch worker — NEVER handle inline.
                    # Inline handling froze getUpdates during slow work (STT/
                    # downloads) → watchdog self-reload ate the in-flight
                    # message. See _dispatch_loop.
                    self._update_queue.put(update)
                    # Save AGAIN post-enqueue so /N switches, auto-track, and
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
                # Log every failure (was silent before — reported
                # "feels unstable" with no log evidence to investigate).
                # Coalesce noisy repeats: log first 3 verbosely, then every
                # 10th, to avoid drowning the log if the network's truly down.
                if consecutive_errors <= 3 or consecutive_errors % 10 == 0:
                    _blog(f"[poll] exception #{consecutive_errors} "
                          f"({type(e).__name__}): {e} — sleeping {backoff:.1f}s\n")
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    def _dispatch_loop(self):
        """FIFO worker for inbound TG updates (see _update_queue in __init__).

        One update at a time — preserves arrival order for rapid message
        bursts into the same tab. A handler crash logs the traceback AND
        tells the sender to resend (the offset is already saved, so the
        message will NOT be re-fetched — silence here = permanent loss)."""
        while self.active and not self._stop_event.is_set():
            try:
                update = self._update_queue.get(timeout=1.0)
            except _queue.Empty:
                continue
            try:
                self._handle_update(update)
            except Exception as e:
                import traceback
                _blog(f"[dispatch] _handle_update crashed ({type(e).__name__}): {e}\n"
                      + traceback.format_exc() + "\n")
                chat_id = ((update.get("message") or {}).get("chat") or {}).get("id")
                if chat_id:
                    try:
                        tg_api(self.config.bot_token, "sendMessage", {
                            "chat_id": chat_id,
                            "text": f"⚠ 這則訊息處理時出錯（{type(e).__name__}），沒有送進分頁，請重發。",
                        })
                    except Exception:
                        pass

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

    # Telegram Bot API getFile 只能下載 ≤20MB 的檔案；超過會回 error，
    # 影片檔常超過 → 舊版靜默丟棄（回報 2026-07-11「影片檔也掉」）。
    _TG_GETFILE_MAX = 20 * 1024 * 1024

    def _fetch_media(self, media: dict, default_ext: str, chat_id, label: str) -> str:
        """下載一個 TG 媒體物件（photo/document/video/audio…）到本地，回本地路徑
        或 ''。任何失敗都會**主動通知使用者**（絕不靜默丟棄）：檔案超過 20MB
        getFile 上限、getFile/下載失敗，都回一則說明。media 是 TG 的媒體 dict
        （含 file_id、可能有 file_size / file_name / mime_type）。"""
        if not isinstance(media, dict) or not media.get("file_id"):
            return ""
        size = media.get("file_size") or 0
        if size and size > self._TG_GETFILE_MAX:
            mb = size / 1024 / 1024
            _blog(f"  {label} too big: {mb:.1f}MB > 20MB getFile 上限\n")
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": (f"⚠ 收到{label}（{mb:.0f}MB），但超過 Telegram bot 的 "
                         "20MB 下載上限，無法取得。\n改用其他方式傳：把檔案存到共用"
                         "位置貼路徑、或壓縮/裁短到 20MB 以下再傳。"),
            })
            return ""
        ext = _Path(media.get("file_name", "")).suffix or default_ext
        path = self._download_tg_file(media["file_id"], ext)
        if not path:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": (f"⚠ 收到{label}但下載失敗（可能超過 20MB、網路逾時或 "
                         "Telegram 暫時性錯誤），沒有送進分頁，請重試或改貼路徑。"),
            })
        return path

    def _download_tg_file(self, file_id: str, ext: str = "") -> str:
        """Download a Telegram file by file_id, save to CLAUDE_TMP. Returns local path or ''."""
        try:
            result = tg_api(self.config.bot_token, "getFile", {"file_id": file_id})
            if not result.get("ok"):
                _blog(f"  getFile failed: {result.get('description','?')}\n")
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

        if data.startswith("mchoice:"):
            # /model picker：數字鍵＝立即選定並存為預設（實測），不送 \r
            parts = data.split(":", 2)
            if len(parts) < 3:
                return
            sid, choice = parts[1], parts[2]
            slot = self.slots.get(sid)
            if not slot:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "Session already gone."})
                return
            self._user_chat[user_id] = chat_id
            self._user_active[user_id] = sid
            slot.pending_menu = False
            slot.pending_menu_options = []
            try:
                slot.write_fn(choice)
            except Exception as e:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": f"送出失敗：{e}"})
                return
            tg_api(self.config.bot_token, "editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": f"✅ 已選 {choice}，確認中…"})

            # v0.29.9：確認改為主動掃 live screen。picker 的確認行是
            # 「⎿ Set model to …」——⎿ 開頭在 _extract_new_text 被過濾，
            # 永遠不會經 flush loop 轉回 TG。舊版設 awaiting_response 乾等，
            # TG 停在「等分頁回確認…」→ 使用者誤判「選了沒成功」（實測
            # s70 模型其實切換成功、確認只是沒送回手機）。
            def _confirm(slot=slot, chat_id=chat_id, message_id=message_id, choice=choice):
                for _ in range(12):                     # 最多等 ~6s
                    time.sleep(0.5)
                    try:
                        tail = self._live_tail(slot, rows=12)
                    except Exception:
                        continue
                    m = re.search(r'Set model to\s+([^\n]+?)\s*(?:and saved[^\n]*)?$',
                                  tail, re.M)
                    if m:
                        tg_api(self.config.bot_token, "editMessageText", {
                            "chat_id": chat_id, "message_id": message_id,
                            "text": (f"✅ {slot.label} 模型已切換：{m.group(1).strip()}"
                                     "（已存為新 session 預設）")})
                        return
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": (f"✳ 已送出選擇 {choice}，但 6 秒內沒在畫面看到確認。"
                             f"用 /{slot.index} 切過去或 /fetch 檢查。")})
            threading.Thread(target=_confirm, daemon=True).start()
            return

        if data.startswith("mcancel:"):
            sid = data.split(":", 1)[1]
            slot = self.slots.get(sid)
            if slot:
                slot.pending_menu = False
                slot.pending_menu_options = []
                try:
                    slot.write_fn("\x1b")   # Esc 關閉 picker
                except Exception:
                    pass
            tg_api(self.config.bot_token, "editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": "已取消，模型未變更。"})
            return

        if data.startswith("efchoice:"):
            # /effort：efchoice:<kind>:<sid>:<token>。claude token=層級字串、
            # codex token=reasoning 選單編號。各自驅動原生 UX（見 _apply_effort_*）。
            parts = data.split(":", 3)
            if len(parts) < 4:
                return
            kind, sid, token = parts[1], parts[2], parts[3]
            slot = self.slots.get(sid)
            if not slot:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "Session already gone."})
                return
            self._user_chat[user_id] = chat_id
            self._user_active[user_id] = sid
            tg_api(self.config.bot_token, "editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": f"🧠 套用推理深度中…（{slot.label}）"})

            def _run(kind=kind, slot=slot, token=token,
                     chat_id=chat_id, message_id=message_id):
                try:
                    if kind == "claude":
                        got = self._apply_effort_claude(slot, token)
                        label = token
                    else:
                        got = self._apply_effort_codex(slot, token)
                        label = dict(self._EFFORT_CODEX).get(token, token)
                except Exception as e:
                    tg_api(self.config.bot_token, "editMessageText", {
                        "chat_id": chat_id, "message_id": message_id,
                        "text": f"❌ 套用失敗：{type(e).__name__}: {e}"})
                    return
                if got:
                    tg_api(self.config.bot_token, "editMessageText", {
                        "chat_id": chat_id, "message_id": message_id,
                        "text": f"✅ {slot.label} 推理深度 → {got}"})
                else:
                    tg_api(self.config.bot_token, "editMessageText", {
                        "chat_id": chat_id, "message_id": message_id,
                        "text": (f"✳ 已送出（{label}），但沒在畫面看到確認。"
                                 f"用 /{slot.index} 切過去或 /fetch 檢查。")})
            threading.Thread(target=_run, daemon=True).start()
            return

        if data.startswith("efcancel:"):
            sid = data.split(":", 1)[1]
            slot = self.slots.get(sid)
            if slot:
                try:
                    slot.write_fn("\x1b")
                except Exception:
                    pass
            tg_api(self.config.bot_token, "editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": "已取消，推理深度未變更。"})
            return

        if data.startswith("rlchoice:"):
            # Rate-limit interactive menu: "1" = wait, "2" = usage credits.
            # The /rate-limit-options menu is confirmed with Enter, so we send
            # the digit immediately followed by \r.
            parts = data.split(":", 2)
            if len(parts) < 3:
                return
            sid, choice = parts[1], parts[2]
            slot = self.slots.get(sid)
            if not slot:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "Session already gone."})
                return
            self._user_chat[user_id] = chat_id
            self._user_active[user_id] = sid
            label_map = {"1": "⏳ 等待重置", "2": "💳 改用 usage credits"}
            label_text = label_map.get(choice, choice)
            try:
                slot.write_fn(f"{choice}\r")
                # Clear the notified flag so if the same limit reappears we
                # don't stay silent (shouldn't happen, but cheap insurance).
                slot.rate_limit_notified = False
            except Exception as e:
                tg_api(self.config.bot_token, "editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": f"送出失敗：{e}"})
                return
            tg_api(self.config.bot_token, "editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": f"✅ 已選「{label_text}」，送出中…"})
            _blog(f"[rate-limit] {sid} user chose {choice!r}\n")
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

        # ── Handle photo / document / video / voice / file messages ──
        file_paths = []
        has_photo = bool(msg.get("photo"))
        has_doc = bool(msg.get("document"))
        has_voice = bool(msg.get("voice"))       # TG voice note (ogg/opus)
        has_audio = bool(msg.get("audio"))       # TG audio file
        # video（壓縮影片）/ video_note（圓形短片）/ animation（GIF/無聲 mp4）
        # 舊版完全沒處理 → 傳影片直接靜默丟棄（回報 2026-07-11）。
        has_video = bool(msg.get("video"))
        has_video_note = bool(msg.get("video_note"))
        has_animation = bool(msg.get("animation"))
        # 是否帶了任何媒體：用來在下載全失敗且無文字時「明講」而非靜默 return。
        media_present = any((has_photo, has_doc, has_voice, has_audio,
                             has_video, has_video_note, has_animation))
        _blog(f"_handle_update: text={text!r} caption={caption!r} photo={has_photo} "
              f"doc={has_doc} voice={has_voice} audio={has_audio} video={has_video} "
              f"video_note={has_video_note} animation={has_animation}\n")
        if has_photo:
            # TG sends multiple sizes; pick the largest (last)
            path = self._fetch_media(msg["photo"][-1], ".png", chat_id, "圖片")
            if path:
                file_paths.append(path)
        if has_doc:
            path = self._fetch_media(msg["document"], ".bin", chat_id, "檔案")
            if path:
                file_paths.append(path)
        if has_video:
            path = self._fetch_media(msg["video"], ".mp4", chat_id, "影片")
            if path:
                file_paths.append(path)
        if has_video_note:
            path = self._fetch_media(msg["video_note"], ".mp4", chat_id, "圓形短片")
            if path:
                file_paths.append(path)
        if has_animation:
            path = self._fetch_media(msg["animation"], ".mp4", chat_id, "動圖")
            if path:
                file_paths.append(path)

        # ── Voice / audio → transcribe via local STT ──
        if has_voice or has_audio:
            media = msg.get("voice") or msg.get("audio")
            ext = ".oga" if has_voice else (_Path(media.get("file_name", "")).suffix or ".mp3")
            audio_path = self._download_tg_file(media["file_id"], ext)
            _blog(f"  voice download: path={audio_path!r}\n")
            if not audio_path:
                # P0-5：舊版這裡完全沒有 else——語音檔下載失敗（TG getFile 逾時、
                # 檔案過期、磁碟寫不進去）就直接往下走，最後被
                # 「not text and not file_paths」那條 return 靜默吃掉，使用者
                # 只看到「傳了沒反應」。
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "⚠ 語音檔下載失敗（Telegram getFile 沒拿到檔案），"
                            "這則沒有送進 session。請再錄一次或改打字。",
                }, timeout=10)
                return
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
                    if not voice_apply_gate():
                        # Apply-gate OFF（回報 2026-08-08：語音每次都要按 Apply
                        # 很煩）：轉錄完直接把文字當成一般訊息往下走正常轉發路徑
                        # 自動送進 session，不再跳 ✅ Apply。
                        text = fwd_text
                        _blog(f"  voice auto-submit (gate off): {fwd_text[:60]!r}\n")
                        # fall through to the normal forward path below
                    else:
                        # Apply-gate ON（預設）：STT 有誤差，不自動送出。先泊住
                        # 轉錄文字＋顯示 inline Apply/Cancel，使用者點 ✅ Apply 才
                        # 送進 session（送出時才解析目標分頁，中途切分頁也 OK）。
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

        # If message has only files (no text), we still need to proceed.
        # 媒體有帶但全部下載失敗、又沒文字/caption：不能靜默 return（那正是
        # 「傳了沒反應」的來源）。_fetch_media 失敗時已各自通知過，但若連
        # caption 都沒有就再補一則兜底，確保使用者一定知道這則沒送進去。
        if not text and not file_paths:
            if media_present and not caption:
                _blog("  media-only message: all downloads failed, nothing forwarded\n")
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

        # `//` 逃逸前綴：`//new` ＝「我要的是分頁裡那個 /new，不是 bridge 的
        # /new」。剝掉一個斜線後**跳過 bridge 指令攔截**，讓它照一般 CLI
        # 指令原文送進分頁。沒有這個出口時，凡是撞名的指令（/new /model
        # /status /help…）永遠會被 bridge 吃掉，分頁那邊根本收不到。
        escaped_slash = bool(text) and text.startswith("//") and len(text) > 2
        if escaped_slash:
            text = text[1:]

        # ── Slash commands (text-only, no files) ──
        if text and text.startswith("/") and not file_paths and not escaped_slash:
            cmd = text.split()[0][1:].split("@")[0].lower()
            # Bridge-own commands
            if cmd in ('list', 'status', 'pause', 'resume', 'start', 'help', 'reload', 'close', 'new', 'restart', 'update', 'update_now', 'fetch', 'usage', '水位', 'model', 'effort', '推理', 'rename', '改名', 'break', 'stop', 'esc', 'interrupt', '中斷', '打斷', 'voice', '語音', 'quiet', '安靜') or cmd.isdigit():
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

        # Mark that this session has received a real user message. 每則新訊息
        # 都清 buffer + 重置輸出時鐘：新 epoch 從乾淨開始，避免 follow-up
        # 保留下來的舊 block 與 stale first_output_time 混進來（v0.29.22 的
        # 「剛送出就重送上一則」根因之一）。
        with slot.output_lock:
            slot.output_buf = ""
            slot.pending_raw = ""
            slot.first_output_time = 0
            slot.last_output_time = 0
        slot.msg_sent_ts = time.time()
        slot.has_user_msg = True
        slot.awaiting_response = True  # arm typing indicator + flush extraction
        # 新 epoch：重置心跳 / 預覽狀態（A4.3 停止條件之一）
        slot._hb_next_ts = 0.0
        slot._hb_count = 0
        slot._hb_interval = 0.0
        slot._hb_last_hash = ""
        slot._hb_last_sent_ts = 0.0
        slot._hb_quiet = False
        slot._preview_count = 0
        slot._preview_gen = -1
        slot._preview_last = ""
        # ── T0 送達回執：已收下、準備注入 ──
        # 補掉現在「注入成功到 8s 排隊通知之間完全靜默」的空窗。覆蓋式記錄，
        # _send() 之後用它把狀態推到 T1/T2。
        origin_msg_id = msg.get("message_id")
        self._react_async(chat_id, origin_msg_id, self.REACTION_SEEN)
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
        # the same system prompt. Slash commands (/model, /compact…) are NOT a
        # first message — don't spend the init prompt on them; it stays armed
        # for the next real message (same rule as the web-UI gate).
        init_prompt = ""
        if self._on_consume_init and not forwarded.lstrip().startswith("/"):
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
            slot.marker_next_scan_ts = 0.0
            slot.marker_scan_gen = -1
            slot._fb_next_ts = 0.0
            slot.marker_forwarded = False   # 新訊息 epoch：重新允許 fallback
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

        # See wants_system_directive(): give line-oriented agents an explicit
        # instruction/message split instead of one indistinguishable blob.
        if wants_system_directive(slot.cmd) and visible_payload != forwarded:
            framed = frame_system_directive(visible_payload, forwarded)
            if framed != visible_payload:
                visible_payload = framed
                slot.sent_texts.append(SYSTEM_DIRECTIVE_START)
                slot.sent_texts.append(SYSTEM_DIRECTIVE_END)

        def _send():
            # Serialize all PTY writes for this slot. Without this, a paste
            # that Telegram splits into several messages — or two rapid
            # messages — spawn concurrent _send threads whose write+Enter
            # interleave into one mangled buffer (malformed input / tool calls).
            notify_failed = False
            defer_unconfirmed = False
            forced_after_busy = False   # P0-8：busy guard 等滿 120s 被迫注入
            write_error = ""            # P0-6：write_fn 拋例外
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
                # Ready gate（只擋這個分頁的第一次注入，見 _wait_session_ready）：
                # CLI 還停在啟動對話框時，Ctrl-U＋整段文字＋Enter 等於幫使用者
                # 選了一個選項，而 Claude Code 信任對話框第 2 項是 No, exit。
                if (not slot.ready_confirmed
                        and _detect_ai(getattr(slot, "cmd", "") or "")):
                    blocked = self._wait_input_safe(slot)
                    if not blocked:
                        slot.ready_confirmed = True
                    else:
                        # 沒寫進去＝清掉 👀，並講清楚為什麼、怎麼補送。通知走
                        # 背景 thread：這裡還握著 write_lock，tg_api 可能卡 35s。
                        _blog(f"[ready] {slot.sid} 未就緒，這則不注入\n")
                        self._react_async(chat_id, origin_msg_id, None)
                        threading.Thread(
                            target=tg_api,
                            args=(self.config.bot_token, "sendMessage", {
                                "chat_id": chat_id,
                                "text": (f"⚠「{slot.label}」還卡在{blocked}，"
                                         "這則沒有送進去。\n先到那個分頁把對話框"
                                         "點掉再重發——硬送會被當成在對話框裡選"
                                         "項目，分頁可能因此被關掉。"),
                            }),
                            kwargs={"timeout": 10}, daemon=True).start()
                        return False
                # Busy guard: writing + Enter while Claude Code is mid-turn
                # makes it abort the in-flight turn with "[Request interrupted]"
                # and submit a mixed/empty buffer (this is the「貼文字變
                # preamble / User message: [Request interrupted]」bug). Wait
                # for the CLI to return to idle (no "esc to interrupt" footer)
                # before injecting. Bounded so a wedged session can't block forever.
                # 訊號源用 live screen（_live_tail）而非 PTY ring bytes：
                # ring 是歷史，turn 結束後殘留的 footer 會把 idle 分頁誤判
                # 成 busy，訊息卡在這裡最多 120s（「送不進去」主因之一）。
                t_wait0 = time.time()
                deadline = t_wait0 + 120.0
                queued_notified = False
                while time.time() < deadline:
                    if not re.search(r'esc to interrupt',
                                     self._live_tail(slot), re.I):
                        break
                    # 等超過 8s 就先回報「已收到、排隊中」——這段最長 120s 的
                    # 靜默等待正是「傳了沒反應=以為沒收到」的體感來源。通知用
                    # 背景 thread 發，不在 write_lock 裡等 TG HTTPS。
                    if not queued_notified and time.time() - t_wait0 >= 8.0:
                        queued_notified = True
                        threading.Thread(
                            target=tg_api,
                            args=(self.config.bot_token, "sendMessage", {
                                "chat_id": chat_id,
                                "text": (f"⏳ 已收到。「{slot.label}」回合進行中，"
                                         "訊息排隊等空檔自動送入（最多 2 分鐘）。"),
                            }),
                            kwargs={"timeout": 5}, daemon=True,
                        ).start()
                    time.sleep(0.5)
                else:
                    # P0-8：迴圈跑完 deadline 都沒 break＝對方回合 120s 還沒結束，
                    # 我們仍會強制注入（可能打斷它上一個回合）。舊版對此完全靜默。
                    forced_after_busy = True

                def _inject():
                    # P0-6：write_fn 沒有例外保護時，pane 已經死掉／tmux session
                    # 不見了的 OSError 會直接逸散到這條 daemon thread —— traceback
                    # 只會噴到 stderr，log 一行都沒有，使用者端則是完全靜默。
                    nonlocal write_error
                    try:
                        # Clear residue left in the input box (aborted turn,
                        # dismissed rating prompt, half-typed text) so the payload
                        # isn't appended to stale content.
                        slot.write_fn("\x15")  # Ctrl-U: kill input line
                        time.sleep(0.05)
                        # Bracketed paste: ingest the (often multi-line) payload
                        # atomically so embedded newlines don't prematurely submit
                        # partial input.
                        slot.write_fn("\x1b[200~" + visible_payload + "\x1b[201~")
                        self._wait_paste_drain(slot, len(visible_payload))
                        _blog(f"[send] {slot.sid} submit CR len={len(visible_payload)}\n")
                        slot.write_fn("\r")
                    except Exception as e:
                        write_error = f"{type(e).__name__}: {e}"
                        _blog(f"[send] {slot.sid} write_fn FAILED: {write_error}\n")
                        return False
                    time.sleep(0.6)
                    after = self._live_tail(slot)
                    # Codex can occasionally keep focus on its pasted-content
                    # chip after the first CR. If the chip is still visible and
                    # the CLI did not start a turn, send LF as a conservative
                    # fallback.
                    if (
                        re.search(r'\[Pasted (?:Content|text)[^\]]*\]', after or "", re.I)
                        and not re.search(r'esc to interrupt', after or "", re.I)
                    ):
                        _blog(f"[send] {slot.sid} submit LF fallback after paste chip\n")
                        try:
                            slot.write_fn("\n")
                        except Exception as e:
                            write_error = f"{type(e).__name__}: {e}"
                            return False
                    return True

                inject_t0 = time.time()
                delivered = None       # None = 非 AI 分頁／沒驗證
                injected = _inject()
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
                if injected and _detect_ai(getattr(slot, "cmd", "") or ""):
                    delivered, residue = self._verify_injection(
                        slot, visible_payload, inject_t0)
                    if not delivered and residue:
                        # 典型卡法（Windows/ConPTY 的 codex 最常見）：payload 已
                        # 完整躺在 composer，只是提交的 CR 被貼上偵測（burst）
                        # 當成換行吞掉——字都在，只差一個 Enter。先補裸 Enter：
                        # 已送出時 composer 是空的，多的 Enter 是 no-op；比直接
                        # 全量重貼安全（Ctrl-U 對多行 composer 可能只清一行，
                        # 重貼會疊字）。
                        _blog(f"[send] {slot.sid} residue → bare Enter nudge\n")
                        try:
                            slot.write_fn("\r")
                        except Exception as e:      # P0-6
                            write_error = f"{type(e).__name__}: {e}"
                            _blog(f"[send] {slot.sid} nudge write failed: {write_error}\n")
                        delivered, residue = self._verify_injection(
                            slot, visible_payload, inject_t0, window=4.0)
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
                    elif not delivered:
                        # 無殘留可安全重試（重試有重複送出風險），但也不能
                        # 再靜默——這正是「prompt 沒反應、/fetch 也沒變化」
                        # 的無聲掉訊窗口（對話框吃掉輸入、畫面被捲走等）。
                        # 不過**不立刻吵**：快回合會在 0.5s poll 間隙就結束、
                        # extraction 又要等 marker（fallback 最長 30s），8s 窗
                        # 內兩個強訊號都抓不到 → 假警報「無法確認」之後回覆
                        # 才到（回報 2026-07-24 截圖）。改交給背景延遲判定，
                        # 再觀察 45s 有訊號就靜默收工。
                        _blog(f"[send] {slot.sid} delivery UNCONFIRMED (no residue) → deferred verdict\n")
                        defer_unconfirmed = True
            # Notify OUTSIDE write_lock — tg_api can block up to 35s and
            # holding the slot's write lock that long queues every
            # subsequent message for this tab behind a dead HTTPS call.
            # （reaction 也一律在鎖外，同一個理由。）
            if write_error:
                # P0-6：PTY 寫入本身炸了——訊息 100% 沒送進去，reaction 清空
                # 並明講，不能走「無法確認」那種模稜兩可的話術。
                self._react_async(chat_id, origin_msg_id, None)
                try:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": (f"⚠ 寫入「{slot.label}」失敗，訊息沒有送出"
                                 f"（分頁可能已關閉／tmux session 不在了）。\n"
                                 f"原因：{write_error[:120]}"),
                    }, timeout=10)
                except Exception:
                    pass
            elif notify_failed:
                self._react_async(chat_id, origin_msg_id, None)   # T2：清空回執
                try:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": (f"⚠ 無法確認訊息已送進「{slot.label}」。"
                                 f"若該分頁沒動靜請直接重發，或用 /{slot.index} "
                                 "切過去看狀態、/fetch 看最新回覆。"),
                    })
                except Exception:
                    pass
            elif defer_unconfirmed:
                threading.Thread(
                    target=self._deferred_delivery_verdict,
                    args=(slot, chat_id, inject_t0, 45.0, origin_msg_id),
                    daemon=True).start()
            elif delivered or delivered is None:
                # T1：確認送進去了（非 AI 分頁沒有驗證訊號，但寫入沒出錯，
                # 對使用者來說一樣是「進去了」）。
                self._react_async(chat_id, origin_msg_id, self.REACTION_DELIVERED)

            if forced_after_busy:
                # P0-8：等滿 120s 仍強制注入 —— 保持 👀（訊息確實送了），
                # 但必須明講可能打斷對方上一個回合。
                try:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": (f"⚠ 「{slot.label}」的回合等了 2 分鐘還沒結束，"
                                 "訊息已強制送入——可能打斷它上一個回合。"
                                 f"用 /{slot.index} 切過去確認狀態。"),
                    }, timeout=10)
                except Exception:
                    pass
        def _send_tracked():
            # /fetch 據 inject_pending 回報「訊息排隊中、尚未送入」——
            # 沒有這個旗標，排隊期間 fetch 只會看到上一則回覆，使用者
            # 會誤判成訊息沒傳到。
            slot.inject_pending = True
            try:
                _send()
            finally:
                slot.inject_pending = False

        threading.Thread(target=_send_tracked, daemon=True).start()

    _INJECT_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07')

    def _wait_paste_drain(self, slot, nchars: int):
        """貼上後、送 Enter 前的等待——等 composer echo「安靜」而非固定 0.3s。

        Windows/ConPTY 把整段 payload 逐字合成 key events，client 端（尤其
        codex/crossterm 讀 win32 事件、拿不到 bracketed-paste 框架，靠連續
        輸入 burst 偵測貼上）drain 大 payload 常超過 0.3s；CR 在 burst 窗內
        到達會被當成「換行」插進 composer 而不是送出——訊息整段卡在輸入框
        （Windows 掉訊主因）。改成等最後一個輸出 chunk 靜止 >= QUIET 才視為
        ingest 完成；idle 動畫（spinner/游標重繪）可能讓畫面永不安靜，
        cap 保底、按 payload 長度放大。"""
        QUIET = 0.25
        t0 = time.time()
        floor = t0 + 0.3                # 維持既有下限，行為不倒退
        cap = t0 + (3.0 if _IS_WIN else 1.0) + min(3.0, nchars / 4000.0)
        while True:
            now = time.time()
            if now >= cap:
                break
            last = getattr(slot, "last_chunk_ts", 0.0) or 0.0
            if now >= floor and (now - last) >= QUIET:
                break
            time.sleep(0.05)

    def _live_tail(self, slot, rows: int = 10) -> str:
        """注入訊號用的「現在畫面尾端」文字（footer 區）。

        v0.29.1 之前這裡用 peek_fn()（最後 ~1KB 原始 PTY bytes）——那是
        「歷史」不是「現在」：turn 結束後的收尾重繪若不足 1KB，舊的
        'esc to interrupt' footer 會殘留在 ring 裡，idle 分頁被 busy guard
        誤擋最多 120s（回報：「/fetch 之後訊息送不進去、沒反應」），送達
        驗證也會拿殘影假 delivered → 真失敗不重試不通知。live pyte screen
        沒有記憶效應，footer 在就是在。取最後 rows 個非空行，避免對話
        內文提到 'esc to interrupt' 造成誤判。無 pyte screen 時（測試
        fake slot）退回 ring bytes。

        rows=10（原本 6）：footer 區在 tmux 狀態列 + 「bypass permissions」
        提示 + composer 上下框線 + 輸入行之後，spinner 剛好卡在第 6 行——
        任何一條額外 chrome（「✔ Update installed」、排隊訊息提示）就把它
        擠出取樣窗。螢幕高度已對齊真實 PTY，多取幾行不會撈到畫面外的殘影。"""
        if getattr(slot, "screen", None) is not None:
            try:
                lines = [l for l in self._slot_display(slot) if l.strip()]
                return "\n".join(lines[-rows:])
            except Exception:
                pass
        try:
            return (slot.peek_fn() or "") if slot.peek_fn else ""
        except Exception:
            return ""

    def _wait_input_safe(self, slot, timeout: float = 20.0) -> str:
        """新分頁的第一則訊息：確認畫面不是卡在會吃掉輸入的啟動對話框。

        沒有這道閘門時，訊息會直接打進 trust prompt 之類的選單——Ctrl-U ＋
        一整段文字 ＋ Enter 在選單裡就是「選一個選項」，而 Claude Code 信任
        對話框第 2 項是 **No, exit**：分頁被自己收到的訊息關掉，訊息也一起
        消失。Howard 2026-08-28 手機端 /new 開的分頁就是這樣沒的（注入後畫面
        沒殘留、沒開回合，3 分鐘後 tmux session 直接不見）。

        回傳擋下的原因，空字串＝可以送。偵測不到危險就放行（fail open），
        所以偵測失準最多是退回舊行為，不會把正常分頁的訊息擋死。
        只擋第一次：放行過就記在 slot.ready_confirmed，之後不再 capture-pane。
        """
        if not self._on_input_blocked:
            return ""
        deadline = time.time() + timeout
        reason = ""
        while time.time() < deadline:
            try:
                reason = self._on_input_blocked(slot.sid) or ""
            except Exception as e:
                _blog(f"[ready] {slot.sid} check failed ({type(e).__name__}: {e})"
                      " → 放行\n")
                return ""
            if not reason:
                return ""
            time.sleep(1.0)
        return reason

    def _deferred_delivery_verdict(self, slot, chat_id, injected_at,
                                   extra_wait: float = 45.0, origin_msg_id=None):
        """「不確定且無殘留」的延遲判定——先不吵，再觀察最長 extra_wait 秒。

        8s 驗證窗有結構性盲區：快回合在兩次 0.5s poll 之間就開始又結束
        （footer 抓不到），而 extraction 走 marker 路徑最長要等 30s 的
        fallback 才會發生 → 假警報「⚠ 無法確認已送進」發完、回覆才進來
        （回報 2026-07-24 截圖，HR 分頁連兩天中招）。這裡看到 turn 訊號
        或「這次注入之後」的 extraction 就靜默收工；真的全程無聲才警告。"""
        deadline = time.time() + extra_wait
        while time.time() < deadline:
            try:
                if getattr(slot, "last_extraction_ts", 0.0) > injected_at:
                    _blog(f"[send] {slot.sid} deferred verdict: reply extracted → OK\n")
                    self._react_async(chat_id, origin_msg_id, self.REACTION_DELIVERED)
                    return
                if re.search(r"esc to interrupt", self._live_tail(slot) or "", re.I):
                    _blog(f"[send] {slot.sid} deferred verdict: turn running → OK\n")
                    self._react_async(chat_id, origin_msg_id, self.REACTION_DELIVERED)
                    return
            except Exception:
                pass
            time.sleep(1.0)
        _blog(f"[send] {slot.sid} deferred verdict: still silent after "
              f"{extra_wait:.0f}s → notify\n")
        self._react_async(chat_id, origin_msg_id, None)   # T2：清空回執
        try:
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": (f"⚠ 無法確認訊息已送進「{slot.label}」。"
                         f"若該分頁沒動靜請直接重發，或用 /{slot.index} "
                         "切過去看狀態、/fetch 看最新回覆。"),
            })
        except Exception:
            pass

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
            recent = self._live_tail(slot)
            if re.search(r"esc to interrupt", recent or "", re.I):
                return True, False
            if getattr(slot, "last_extraction_ts", 0.0) > injected_at:
                return True, False
            time.sleep(0.5)
        plain = self._INJECT_ANSI_RE.sub('', recent or "")
        residue = bool(tail) and tail in re.sub(r"\s+", "", plain)
        # codex 把大貼上摺疊成 [Pasted Content …] chip——payload 尾段不在畫面
        # 上，但內容確實還卡在 composer，等同殘留（可安全 nudge/重試）。
        if not residue and re.search(r'\[Pasted (?:Content|text)[^\]]*\]', plain, re.I):
            residue = True
        return False, residue

    def _slot_menu_text(self, user_id) -> str:
        """編號→分頁名的精簡清單（不含回覆預覽，/N 打錯時直接附上）。"""
        active_sid = self.get_active_sid(user_id)
        with self._slots_lock:
            rows = [(self.slots[sid].index, self.slots[sid].label,
                     sid == active_sid) for sid in self._slot_order]
        if not rows:
            return "（目前沒有任何分頁）"
        return "\n".join(f"/{i}  {label}{'  ◀ 現在在這' if act else ''}"
                          for i, label, act in rows)

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
                model = self._slot_model_suffix(slot).lstrip(" ·").strip()
                model_tag = f"  〔{model}〕" if model else ""
                lines.append(f"\n/{slot.index}  {slot.label}{model_tag}{marker}")
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

        elif cmd == "model":
            # 原生 /model 互動化：轉傳給分頁開 picker → 解析選項 → TG inline
            # 按鈕選擇（實測：picker 按數字＝立即選定並存為新 session 預設）。
            self._handle_model_command(user_id, chat_id)

        elif cmd in ("effort", "推理"):
            # 推理深度：claude /effort 滑桿層級 + codex /model reasoning 步驟，
            # 收斂成 TG inline 按鈕（見 _handle_effort_command）。
            self._handle_effort_command(user_id, chat_id)

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

        elif cmd in ("quiet", "安靜"):
            # A4.5：對當前 active 分頁的「這一個 epoch」停發心跳。下一則使用者
            # 訊息自動復原——比叫使用者去設定頁關掉整個功能好，也讓「我知道它
            # 在跑、別吵我」有一個一秒鐘就能按的出口。
            active_sid = self.get_active_sid(user_id)
            slot = self.slots.get(active_sid) if active_sid else None
            if not slot:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id, "text": "沒有 active session。"})
            else:
                slot._hb_quiet = True
                _blog(f"[heartbeat] {slot.sid} /quiet — muted for this epoch\n")
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": (f"🔕 這一輪不再提醒「{slot.label}」的進度。"
                             "回覆好了還是會送給你；下一則訊息自動恢復。"),
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
            switch_msg = None
            out_of_range = 0     # 0=沒事，否則＝目前分頁數（含 0 個）
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
                else:
                    out_of_range = -1
            # 送出一律在鎖外：_slot_menu_text 自己要拿 _slots_lock（非
            # reentrant），而 tg_api 可能卡到 35s，不能扣著 slot 鎖等 HTTPS。
            if switch_msg:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": switch_msg,
                })
            if out_of_range:
                # 死巷子的錯誤訊息（「Invalid session number. Use /list」）在
                # 手機上等於要再敲一次指令才知道能選什麼——而且編號會漂：
                # 分頁關掉／死掉後，後面的全部往前遞補，聊天室裡舊的 /list
                # 就過期了（Howard 2026-08-28：照舊清單敲 /10，第 10 個分頁
                # 早就沒了）。直接把現在的編號表附上，一則訊息就能改點。
                n = len(self._slot_order)
                head = (f"⚠ 沒有 /{idx} —— 目前 {n} 個分頁（/1–/{n}）。"
                        if n else "⚠ 目前沒有任何分頁。")
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": f"{head}\n分頁關掉後編號會往前遞補，舊清單會過期。"
                            f"\n\n{self._slot_menu_text(user_id)}",
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
            # 狀態感知（v0.29.8，回報：「等不及去 fetch 結果拿到舊回覆，
            # 會以為訊息沒傳到」）：fetch 讀的是即時畫面，但內容是「上一則
            # 完整回覆」——若新訊息還在排隊或回合進行中，先講清楚，別讓
            # 舊回覆被誤讀成「沒收到新訊息」。
            status = ""
            if getattr(slot, "inject_pending", False):
                status = "📨 你的訊息還在排隊（分頁回合進行中，尚未送入）\n"
            elif re.search(r"esc to interrupt", self._live_tail(slot), re.I):
                status = "⏳ 回合進行中，新回覆還在生成——以下是上一則回覆\n"
            reply_text = self._peek_last_response(slot)
            if not reply_text:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": (status or "") + "No AI reply found in current session.",
                })
                return
            # Truncate if needed (TG max message = 4096 chars)
            if len(reply_text) > 4000:
                reply_text = reply_text[:4000] + "\n…(truncated)"
            header = f"📌 {slot.label} (/{slot.index})\n{status}" if status else f"📌 {slot.label} (/{slot.index})"
            msg_text = f"{header}\n\n{reply_text}"
            # Don't pin (v0.11.58: the pinned banner in chat is noisy
            # and rarely useful — the message itself is enough; user scrolls
            # if they need to find it).
            tg_api(self.config.bot_token, "sendMessage", {
                "chat_id": chat_id, "text": msg_text,
            })

        elif cmd in ("rename", "改名"):
            # /rename <新名稱> — 改 active 分頁；/rename <編號> <新名稱> — 改指定
            # 分頁（編號同 /N 切換）。走 sfctl IPC 的 rename → main.py
            # rename_session：bridge slot label、webview tab、config 持久化
            # 一次到位（v0.29.2 的即時推送修正也吃得到）。
            parts = (text or "").split(maxsplit=2)
            args = parts[1:]
            target_slot = None
            if len(args) >= 2 and args[0].isdigit():
                idx = int(args[0])
                target_slot = next(
                    (s for s in self.slots.values() if s.index == idx), None)
                new_name = args[1].strip()
                if not target_slot:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": f"找不到編號 /{idx} 的分頁，先 /list 看一下。"})
                    return
            else:
                new_name = " ".join(args).strip()
                active_sid = self.get_active_sid(user_id)
                target_slot = self.slots.get(active_sid) if active_sid else None
                if not target_slot:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id, "text": "沒有 active session，先用 /list 選一個。"})
                    return
            if not new_name:
                tg_api(self.config.bot_token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "用法：/rename <新名稱>（改目前分頁）或 /rename <編號> <新名稱>"})
                return
            if len(new_name) > 60:
                new_name = new_name[:60]

            def _do_rename(slot=target_slot, name=new_name, chat_id=chat_id):
                old = slot.label
                result = self._sfctl_call("rename", {"sid": slot.sid, "name": name})
                if result.get("success"):
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"✏️ /{slot.index}「{old}」→「{name}」"})
                else:
                    tg_api(self.config.bot_token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"❌ 改名失敗：{result.get('message', 'IPC timeout')}"})
            threading.Thread(target=_do_rename, daemon=True).start()

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
                    "  /rename <新名> — 改目前分頁名；/rename <編號> <新名> 改指定分頁（alias /改名）\n"
                    "  /effort — 調目前分頁推理深度（claude/codex，inline 按鈕；alias /推理）\n"
                    "  //xxx — 強制把 /xxx 原文送進分頁（例：//new 送分頁的 /new，"
                    "不會被 bridge 攔截）\n"
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
