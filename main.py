#!/usr/bin/env python3
"""
shellframe — Multi-tab GUI terminal with clipboard image paste support.
Runs any CLI tool (Claude, Codex, bash, etc.) in tabbed PTY sessions.

Mac: WKWebView + pty.fork()
Windows: Edge WebView2 + subprocess
"""

import atexit
import base64
import codecs
import concurrent.futures
import ctypes
import ctypes.util
import errno
import importlib
import json
import os
import platform
import plistlib
import glob
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from queue import SimpleQueue

import webview

# Add app dir to path for bridge imports
sys.path.insert(0, str(Path(__file__).parent))
import bridge_telegram
import bridge_line
import board
import agent_status
import account_manager
from api_history import HistoryApiMixin
from api_schedules import SchedulesApiMixin
import usage_probe
import frame_link as frame_link_mod
from bridge_telegram import TelegramBridge, TelegramBridgeConfig
from bridge_line import LineBridge, LineBridgeConfig

IS_WIN = platform.system() == "Windows"

if not IS_WIN:
    import fcntl
    import pty
    import select
    import struct
    import termios

CLAUDE_TMP = Path.home() / ".claude" / "tmp"
CLAUDE_TMP.mkdir(parents=True, exist_ok=True)

# 延遲送出佇列（TG /delay）——持久化到檔案，App 端排程器每幾秒掃、到點注入。
# 放檔案是為了 restart 後不遺失（怕用量不夠時排隊等重置再送）。
SF_STATE_DIR = Path.home() / ".local" / "state" / "shellframe"
DELAYS_FILE = SF_STATE_DIR / "tg_delays.json"

# AI CLI tools that should receive the init prompt.
# Matched against the base command name (last path component, no extension).
# Providers with usage/quota support come from the registry, so adding one there
# is enough for it to be treated as an AI tab here too; the extras are CLIs we
# recognise but don't meter.
OTHER_AI_CLI_TOOLS = {"aider", "cursor", "copilot", "goose", "gemini"}
AI_CLI_TOOLS = set(usage_probe.provider_binaries()) | OTHER_AI_CLI_TOOLS
STARTUP_TRUST_AI_TOOLS = {"claude", "codex", "sf-codex"}
# UI 麥克風錄音注入 AI 分頁時的前置 tag——告訴 AI 這是 STT 逐字稿、要先解析
# 語意/意圖（辨識誤差、口語贅字）再行動，不要逐字照辦。
MIC_STT_TAG = "🎙[語音輸入（STT 逐字稿）｜可能有辨識誤差，請先解析語意與意圖再執行]"
TRUSTED_STARTUP_CWDS = {str(Path.home()), str(Path.home().resolve())}

APP_DIR = Path(__file__).parent
VERSION_FILE = APP_DIR / "version.json"
REPO_URL = "https://raw.githubusercontent.com/h2ocloud/shellframe/main/version.json"
CODEX_AUTONOMOUS_FLAGS = "--dangerously-bypass-approvals-and-sandbox --search --no-alt-screen"
CODEX_LAUNCHER = "codex" if IS_WIN else "sf-codex"
SHELLFRAME_CODEX_CMD = f"{CODEX_LAUNCHER} {CODEX_AUTONOMOUS_FLAGS}"

CONFIG_DIR = Path.home() / ".config" / "shellframe"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"
ACCOUNT_MANAGER = account_manager.AccountManager(
    root=CONFIG_DIR / "account-profiles"
)

DEFAULT_AGENT_ROSTER = {
    "時程信件": {
        "label": "時程信件-CLD",
        "cmd": "claude --permission-mode bypassPermissions --dangerously-skip-permissions",
        "agent_code": "CLD",
        "responsibility": "信件、行程、Scrum 排卡、FEMAS 假單/居家辦公、會議追蹤、回信追蹤",
        "handoff": True,
    },
    "Coding": {
        "label": "Coding-CDX",
        "cmd": SHELLFRAME_CODEX_CMD,
        "agent_code": "CDX",
        "responsibility": "程式、repo、測試、部署、ShellFrame、Jenkins、webhook/API 修正",
        "handoff": True,
    },
    "研究": {
        "label": "研究-CLD",
        "cmd": "claude --permission-mode bypassPermissions --dangerously-skip-permissions",
        "agent_code": "CLD",
        "responsibility": "資料調研、文件整理、長文本分析、RFP/Notion/Plaud 初步彙整",
        "handoff": True,
    },
    "知庫": {
        "label": "知庫-CLD",
        "cmd": "claude --permission-mode bypassPermissions --dangerously-skip-permissions",
        "agent_code": "CLD",
        "responsibility": "Obsidian/Notion 知識庫整理、memory/skill 沉澱建議",
        "handoff": True,
    },
    "規格站": {
        "label": "規格站-CDX",
        "cmd": SHELLFRAME_CODEX_CMD,
        "agent_code": "CDX",
        "responsibility": "Garden CMS 規格站維護：specData.ts 資料補充、Vue UI 改造、build/deploy 到 ToolHub",
        "handoff": True,
    },
}

AGENT_ROLE_ALIASES = {
    "schedule": "時程信件",
    "calendar": "時程信件",
    "email": "時程信件",
    "mail": "時程信件",
    "scrum": "時程信件",
    "femas": "時程信件",
    "假單": "時程信件",
    "居家": "時程信件",
    "信件": "時程信件",
    "時程": "時程信件",
    "coding": "Coding",
    "code": "Coding",
    "repo": "Coding",
    "shellframe": "Coding",
    "sf": "Coding",
    "jenkins": "Coding",
    "webhook": "Coding",
    "research": "研究",
    "rfp": "研究",
    "plaud": "研究",
    "notion": "研究",
    "調研": "研究",
    "研究": "研究",
    "knowledge": "知庫",
    "obsidian": "知庫",
    "知庫": "知庫",
    "spec": "規格站",
    "spec-site": "規格站",
    "garden": "規格站",
    "garden-cms": "規格站",
    "規格站": "規格站",
}

DEFAULT_CONFIG = {
    # Account refs are safe metadata only. Credential snapshots are kept under
    # ACCOUNT_MANAGER.root with private filesystem permissions.
    "accounts": account_manager._empty_accounts(),
    "user_prompt_paths": ["~/.claude/CLAUDE.md"],
    "plugins": {
        "installed": [],
        "enabled": []
    },
    "presets": [
        # Shell first so the "+" menu has a sensible default for any user.
        {"name": "PowerShell", "cmd": "powershell", "icon": "\u25b6"} if IS_WIN else
        {"name": "Bash", "cmd": "bash", "icon": "\u25b6"},
        # AI CLIs ship as defaults — most shellframe users come for these.
        # `cmd` is the bare command name; the user just needs `claude` / `codex`
        # on PATH (Anthropic / OpenAI install scripts put them in ~/.local/bin
        # or /usr/local/bin). Missing binary surfaces as "command not found"
        # in the new session, which is clear enough — no need to gate on a
        # which-check at config-build time.
        {"name": "Claude", "cmd": "claude --permission-mode bypassPermissions --dangerously-skip-permissions", "icon": "\U0001F680"},   # 🚀
        {"name": "Codex",  "cmd": SHELLFRAME_CODEX_CMD,  "icon": "\U0001F916"},   # 🤖
        {"name": "Antigravity", "cmd": "agy", "icon": "\U0001FA90"},              # 🪐
    ],
    "settings": {
        "fontSize": 14,
        "language": "en",
        "master_turn_preamble_enabled": True,
        "experimental_board": False,
        "experimental_loops": False,
        "show_model_badge": True,
        # 眼鏡（Agent Relay）是外掛功能，要另外裝 bridge 才有用。
        # 預設關：沒裝的人不該在每個分頁上看到一顆按不出東西的按鈕。
        "glasses_enabled": False
    },
    "idle_reaper": {
        "enabled": False,
        "review_sec": 300,
        "idle_sec": 1800,
        "summary_grace_sec": 120,
        "keep_labels": ["main", "Main"],
        "keep_sids": [],
        "keep_first_session": False,
        "keep_bridge_active": True,
        "close_ai_only": True,
        "summary_dir": str(CONFIG_DIR / "session_summaries"),
        "self_sediment": False,
        "reflection_file": "",
        "handoff_to_main": True,
        "handoff_on_start": False
    },
    "agent_roster": DEFAULT_AGENT_ROSTER,
    # Optional local HTTP API. Disabled by default. When enabled, exposes the
    # sfctl command surface over loopback so a local agent (e.g. OpenClaw) can
    # drive tabs. Loopback host + token + IP whitelist enforced. Swagger at /docs.
    "api_server": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 8765,
        "token": "",                      # auto-generated on first enable if blank
        "allowed_ips": ["127.0.0.1", "::1"]
    }
}


def _ensure_idle_reaper_defaults(cfg: dict) -> bool:
    """Keep idle-reaper config self-documenting in config.json."""
    defaults = DEFAULT_CONFIG.get("idle_reaper", {})
    raw = cfg.get("idle_reaper")
    if not isinstance(raw, dict):
        raw = {}
    changed = False
    for key, value in defaults.items():
        if key not in raw:
            raw[key] = value
            changed = True
    if cfg.get("idle_reaper") is not raw:
        cfg["idle_reaper"] = raw
        changed = True
    return changed


def _ensure_api_server_defaults(cfg: dict) -> bool:
    """Surface the (default-off) local HTTP API block in config.json so users
    can discover and flip it on. Never overrides an existing value."""
    defaults = DEFAULT_CONFIG.get("api_server", {})
    raw = cfg.get("api_server")
    if not isinstance(raw, dict):
        raw = {}
    changed = False
    for key, value in defaults.items():
        if key not in raw:
            raw[key] = value
            changed = True
    if cfg.get("api_server") is not raw:
        cfg["api_server"] = raw
        changed = True
    return changed


def _ensure_frame_link_defaults(cfg: dict) -> bool:
    """Surface the (default-off) Frame Link block in config.json. frame_id is
    generated once and never rotated — peers key their secrets to it."""
    raw = cfg.get("frame_link")
    if not isinstance(raw, dict):
        raw = {}
    changed = False
    defaults = {
        "enabled": False,
        "listen_host": "0.0.0.0",
        "listen_port": 8767,
        "frame_name": "",
        "peers": {},
    }
    for key, value in defaults.items():
        if key not in raw:
            raw[key] = value
            changed = True
    if not raw.get("frame_id"):
        raw["frame_id"] = uuid.uuid4().hex
        changed = True
    if cfg.get("frame_link") is not raw:
        cfg["frame_link"] = raw
        changed = True
    return changed


def _ensure_agent_roster_defaults(cfg: dict) -> bool:
    """Expose the manual delegation roster in config.json without hard routing."""
    raw = cfg.get("agent_roster")
    if not isinstance(raw, dict):
        raw = {}
    changed = False
    for role, defaults in DEFAULT_AGENT_ROSTER.items():
        existing = raw.get(role)
        if not isinstance(existing, dict):
            raw[role] = dict(defaults)
            changed = True
            continue
        for key, value in defaults.items():
            if key not in existing:
                existing[key] = value
                changed = True
    if cfg.get("agent_roster") is not raw:
        cfg["agent_roster"] = raw
        changed = True
    return changed


def _ensure_user_prompt_paths_default(cfg: dict) -> bool:
    raw = cfg.get("user_prompt_paths")
    if isinstance(raw, list):
        return False
    cfg["user_prompt_paths"] = list(DEFAULT_CONFIG["user_prompt_paths"])
    return True


def _plugins_config(cfg: dict) -> dict:
    raw = cfg.get("plugins")
    return raw if isinstance(raw, dict) else {}


def _installed_plugin_dirs() -> list[str]:
    root = APP_DIR / "shellframe_plugins"
    if not root.exists():
        return []
    names = []
    for sub in sorted(root.iterdir()):
        if sub.is_dir() and (sub / "manifest.json").exists():
            names.append(sub.name)
    return names


def _rokid_has_existing_setup() -> bool:
    return (
        (Path.home() / "Library" / "LaunchAgents" / "com.h2ocloud.rokid-bridge-listener.plist").exists()
        or (Path.home() / ".claude" / "channels" / "rokid-bridge").exists()
    )


def _legacy_enabled_plugins_for_migration() -> list[str]:
    names = []
    for name in _installed_plugin_dirs():
        if name == "rokid-bridge" and not _rokid_has_existing_setup():
            continue
        names.append(name)
    return names


def _ensure_plugins_defaults(cfg: dict) -> bool:
    raw = cfg.get("plugins")
    if not isinstance(raw, dict):
        migrated = _legacy_enabled_plugins_for_migration()
        cfg["plugins"] = {
            "installed": migrated,
            "enabled": migrated,
        }
        return True
    changed = False
    for key in ("installed", "enabled"):
        if not isinstance(raw.get(key), list):
            raw[key] = []
            changed = True
    if cfg.get("plugins") is not raw:
        cfg["plugins"] = raw
        changed = True
    return changed


# Presets offered in the "+" menu for supported AI CLIs. Appending an entry
# here is all a new provider needs: existing installs pick it up on next launch
# (see the seen-list migration in load_config), and anything the user deleted
# stays deleted. Commands stay model-agnostic on purpose — every CLI persists
# its own model choice, and a hard-coded model name goes stale fast.
_DEFAULT_AI_PRESETS = [
    {"name": "Claude", "cmd": "claude --permission-mode bypassPermissions --dangerously-skip-permissions", "icon": "\U0001F680"},
    {"name": "Codex",  "cmd": SHELLFRAME_CODEX_CMD,  "icon": "\U0001F916"},
    {"name": "Antigravity", "cmd": "agy", "icon": "\U0001FA90"},   # 🪐
    # 通用 pi（接自己的 provider）。地端 Spark 版本走使用者自訂 preset
    # sf-pi-spark——那支啟動器帶 SPARK_API_KEY 與 ~/.pi/agent/models.json，
    # 是機器特有設定，不適合當預設。沒安裝時由 registry 的 install 引導。
    {"name": "Pi", "cmd": "pi", "icon": "\U0001D70B"},              # 𝜋
    # 裸指令即可——opencode 自己管模型 provider／登入，不需要 ShellFrame 加旗標。
    # 沒安裝時走既有的「未安裝→安裝」gate（usage_probe.PROVIDER_SPECS['opencode']）。
    {"name": "OpenCode", "cmd": "opencode", "icon": "\U0001F9E9"},  # 🧩
]

_AUTONOMOUS_PRESET_CMDS = {
    "claude": "claude --permission-mode bypassPermissions --dangerously-skip-permissions",
    "codex": SHELLFRAME_CODEX_CMD,
}


def _autonomous_cmd(cmd: str) -> str:
    """Upgrade old bare AI commands to ShellFrame's low-friction launchers."""
    stripped = (cmd or "").strip()
    return _AUTONOMOUS_PRESET_CMDS.get(stripped, cmd)


def _replace_first_command(cmd: str, replacement: str) -> str:
    leading_len = len(cmd) - len(cmd.lstrip())
    leading = cmd[:leading_len]
    rest = cmd[leading_len:]
    if not rest:
        return replacement
    if rest[0] in {'"', "'"}:
        quote = rest[0]
        end = rest.find(quote, 1)
        if end != -1:
            return leading + replacement + rest[end + 1:]
    parts = rest.split(None, 1)
    suffix = f" {parts[1]}" if len(parts) > 1 else ""
    return leading + replacement + suffix


# Where the glasses bridge (Agent Relay) drops its heartbeat. Read-only from
# here — ShellFrame never writes it and never dials the relay itself.
GLASSES_STATE_PATH = os.path.expanduser("~/.local/share/evenclaude/state.json")
GLASSES_STATE_STALE_S = 120


def _session_provider(cmd: str) -> str:
    """'claude' | 'codex' | whatever usage_probe knows | 'other'.

    Derived from the launch command every time rather than stored, so a tab
    that gets relaunched under a different CLI cannot keep a stale label.
    """
    try:
        return agent_status.worker_kind(cmd or "")
    except Exception:
        return "other"


def _worker_is_codex(cmd: str) -> bool:
    """這個分頁跑的是 codex 嗎（看第一個 token，含 .cmd/.exe 包裝）。"""
    try:
        tokens = shlex.split(cmd or "")
    except ValueError:
        return False
    if not tokens:
        return False
    exe = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    exe = exe[:-4] if exe.endswith((".cmd", ".bat", ".exe")) else exe
    return exe in ("codex", "sf-codex")


def _canonical_cmd(cmd: str) -> str:
    cmd = _normalize_dashes(cmd or "")
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = []
    if tokens:
        first = tokens[0]
        first_name = first.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if first_name in {"sf-codex", "sf-codex.cmd", "sf-codex.bat", "sf-codex.exe"}:
            cmd = _replace_first_command(cmd, CODEX_LAUNCHER)
    codex_names = {
        "codex", "codex.cmd", "codex.bat", "codex.exe",
        "sf-codex", "sf-codex.cmd", "sf-codex.bat", "sf-codex.exe",
    }
    if tokens and first_name in codex_names and "--dangerously-bypass-approvals-and-sandbox" in cmd:
        cmd = re.sub(r"\s+-a\s+never(?=\s|$)", "", cmd)
        cmd = re.sub(r"\s+--ask-for-approval(?:=|\s+)never(?=\s|$)", "", cmd)
        return cmd
    return _autonomous_cmd(cmd)

_DASH_RE = re.compile(r'(^|\s)[—–](?=\S)')
MASTER_TURN_PREAMBLE = (
    "ShellFrame master turn reminder: first understand the user's request. "
    "If the task is non-trivial, parallelizable, or better handled by a worker, "
    "run `sfctl list` and consider `sfctl delegate`; do not hard-route by keywords. "
    "When reporting to the user, always refer to a worker by its tab label "
    "(e.g.「點裝備優化」), never by sid (e.g. s48) — sid is only for your own "
    "sfctl calls. If a handoff/report or sfctl output gives only a sid, run "
    "`sfctl list` to map it to the tab label before relaying it. "
    "If the user's message contains #<tab-label> tags (e.g. #研究-CLD), each tag "
    "names an existing tab the task must interact with: keep those #tags verbatim "
    "in the delegate task text — ShellFrame auto-resolves them and attaches "
    "sfctl peek/send interaction instructions for the worker. If you handle the "
    "task yourself instead, interact with the tagged tabs via sfctl directly."
)


def _normalize_dashes(cmd: str) -> str:
    """Smart-substitution autocorrect: macOS turns ``--`` into ``—`` (em-dash)
    or ``–`` (en-dash) in some editable text contexts. CLI flag parsers don't
    understand em/en-dash, so a token starting with one is virtually always a
    typo for ``--``. Normalize at command boundaries.
    """
    if not cmd:
        return cmd
    return _DASH_RE.sub(lambda m: m.group(1) + '--', cmd)


# In-process lock for config read-modify-write. 14+ threads (UI RPC, bridge
# poll, geometry flush, session lifecycle) all do load→mutate→save; without
# this the last writer silently clobbers the others' keys. Writers should
# use update_config(); bare load/save stay for read-only or legacy sites.
_CONFIG_LOCK = threading.RLock()


def update_config(mutator):
    """Atomic config read-modify-write: mutator(cfg) mutates the dict in
    place (or returns a replacement). Returns the saved dict."""
    with _CONFIG_LOCK:
        cfg = load_config()
        out = mutator(cfg)
        if isinstance(out, dict):
            cfg = out
        save_config(cfg)
        return cfg


def load_config():
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        except Exception:
            return DEFAULT_CONFIG.copy()
        # Offer a preset for every supported AI CLI, once each. Tracking which
        # ones were already offered (rather than a single "migrated" flag) is
        # what makes supporting another CLI a one-line change: append it to
        # _DEFAULT_AI_PRESETS and every existing install gains the preset on
        # next launch, while presets the user deleted stay deleted.
        offered = set(cfg.get("_default_ai_presets_offered") or [])
        if not offered and cfg.get("_default_ai_presets_migrated"):
            offered = {"Claude", "Codex"}      # what the old flag stood for
        if len(offered) < len(_DEFAULT_AI_PRESETS):
            existing_cmds = {
                (p.get("cmd") or "").strip() for p in cfg.get("presets", []) or []
            }
            for preset in _DEFAULT_AI_PRESETS:
                if preset["name"] in offered:
                    continue
                if preset["cmd"] not in existing_cmds:
                    cfg.setdefault("presets", []).append(dict(preset))
                offered.add(preset["name"])
            cfg["_default_ai_presets_offered"] = sorted(offered)
            cfg["_default_ai_presets_migrated"] = True   # kept for older builds
            try:
                save_config(cfg)
            except Exception:
                _swallow("load_config:428")
        # One-shot migration: fix em/en-dash typos introduced by macOS smart
        # substitution (e.g. "codex —full-auto" → "codex --full-auto").
        if not cfg.get("_dash_normalized_v1"):
            changed = False
            for p in cfg.get("presets", []):
                normalized = _normalize_dashes(p.get("cmd") or "")
                if normalized != p.get("cmd"):
                    p["cmd"] = normalized
                    changed = True
            cfg["_dash_normalized_v1"] = True
            if changed:
                try:
                    save_config(cfg)
                except Exception:
                    _swallow("load_config:443")
        # One-shot migration: ShellFrame is often driven remotely (e.g. through
        # the Telegram bridge), where an agent stopping to ask for tool approval
        # just stalls. Upgrade the stock Claude/Codex presets to the
        # low-friction launchers, but only while they are still bare commands.
        if not cfg.get("_autonomous_ai_presets_v1"):
            changed = False
            for p in cfg.get("presets", []) or []:
                upgraded = _canonical_cmd(p.get("cmd") or "")
                if upgraded != p.get("cmd"):
                    p["cmd"] = upgraded
                    changed = True
            for entry in (cfg.get("session_manifest") or []):
                upgraded = _canonical_cmd(entry.get("cmd") or "")
                if upgraded != entry.get("cmd"):
                    entry["cmd"] = upgraded
                    changed = True
            cfg["_autonomous_ai_presets_v1"] = True
            if changed:
                try:
                    save_config(cfg)
                except Exception:
                    _swallow("load_config:465")
        # Ongoing cleanup for installs that already passed the one-shot
        # migration while the Codex preset still used a literal "~" path.
        changed = False
        for p in cfg.get("presets", []) or []:
            upgraded = _canonical_cmd(p.get("cmd") or "")
            if upgraded != p.get("cmd"):
                p["cmd"] = upgraded
                changed = True
        for entry in (cfg.get("session_manifest") or []):
            upgraded = _canonical_cmd(entry.get("cmd") or "")
            if upgraded != entry.get("cmd"):
                entry["cmd"] = upgraded
                changed = True
        if changed:
            try:
                save_config(cfg)
            except Exception:
                _swallow("load_config:483")
        cfg_defaults_changed = False
        if _ensure_idle_reaper_defaults(cfg):
            cfg_defaults_changed = True
        if _ensure_agent_roster_defaults(cfg):
            cfg_defaults_changed = True
        if _ensure_user_prompt_paths_default(cfg):
            cfg_defaults_changed = True
        if _ensure_plugins_defaults(cfg):
            cfg_defaults_changed = True
        if _ensure_api_server_defaults(cfg):
            cfg_defaults_changed = True
        if _ensure_frame_link_defaults(cfg):
            cfg_defaults_changed = True
        if cfg_defaults_changed:
            try:
                save_config(cfg)
            except Exception:
                _swallow("load_config:499")
        return cfg
    cfg = DEFAULT_CONFIG.copy()
    _ensure_idle_reaper_defaults(cfg)
    _ensure_agent_roster_defaults(cfg)
    _ensure_user_prompt_paths_default(cfg)
    _ensure_plugins_defaults(cfg)
    _ensure_frame_link_defaults(cfg)
    return cfg


def save_config(cfg):
    with _CONFIG_LOCK:
        return _save_config_locked(cfg)


def _save_config_locked(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    data = json.dumps(cfg, indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.write("\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            _swallow("save_config:520")
    os.replace(tmp, CONFIG_FILE)


TMUX_PREFIX = "sf_"  # tmux session name prefix

_SLUG_STRIP_RE = re.compile(r'[^\w\s-]')
_SLUG_COLLAPSE_RE = re.compile(r'[\s_]+')


def _slugify(text: str, max_len: int = 22) -> str:
    """Convert arbitrary text to a tmux-safe slug (lowercase, hyphens, ≤ max_len chars)."""
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', errors='ignore').decode()
    text = _SLUG_STRIP_RE.sub('', text).lower()
    text = _SLUG_COLLAPSE_RE.sub('-', text).strip('-')
    return text[:max_len].rstrip('-') or 'sf'


def _unique_tmux_name(base: str) -> str:
    """Return base if no tmux session exists with that name, else base-2, base-3, ..."""
    name = base
    i = 2
    while _tmux_session_exists(name):
        suffix = f"-{i}"
        name = base[:22 - len(suffix)] + suffix
        i += 1
    return name


def _haiku_slug(prompt: str) -> str:
    """Call claude --model haiku to produce a 3-5 word slug for the prompt.
    Returns empty string on any failure so callers fall back to sf_sNN."""
    try:
        claude_bin = shutil.which("claude")
        if not claude_bin:
            return ""
        meta_prompt = (
            f'Reply with ONLY a 3-5 word lowercase hyphenated slug (no punctuation, '
            f'no quotes) summarising this task: "{prompt[:200]}"'
        )
        r = subprocess.run(
            [claude_bin, "--model", "claude-haiku-4-5", "--print", meta_prompt],
            capture_output=True, text=True, timeout=8,
        )
        raw = r.stdout.strip().splitlines()
        candidate = next((l.strip() for l in reversed(raw) if l.strip()), "")
        slug = _slugify(candidate)
        return slug if len(slug) >= 3 else ""
    except Exception:
        return ""


def _session_cwd() -> str:
    """Working directory we hand to spawned PTY sessions (claude / codex /
    bash / etc.). We *don't* want them inheriting shellframe's install
    dir as their cwd — that's the host chrome, not where the user
    actually wants to work. Defaults to $HOME so AI CLIs and shells start
    in a neutral place; the init prompt still tells the AI that
    shellframe source lives at ~/.local/apps/shellframe/ if it's asked
    to self-modify."""
    try:
        return os.path.expanduser("~") or "/"
    except Exception:
        return "/"


def _maybe_claude_session_id(cmd: str):
    """For a `claude` launch command, inject `--session-id <uuid>` so the tab
    can be deterministically mapped to its transcript file
    (~/.claude/projects/<slug>/<uuid>.jsonl). Skips codex/other commands and
    any command that already resumes a session. Returns (new_cmd, session_id)
    or (cmd, None) when not applicable."""
    try:
        low = f" {cmd.lower()} "
        if "claude" not in low:
            return cmd, None
        if ("--session-id" in low or "--resume" in low or "--continue" in low
                or " -r " in low or " -c " in low):
            return cmd, None
        new_id = str(uuid.uuid4())
        return f"{cmd} --session-id {new_id}", new_id
    except Exception:
        return cmd, None


def _tmux_get_env(tmux_name: str, key: str):
    """Read a tmux session environment variable (returns '' if unset/error)."""
    try:
        tmux = shutil.which("tmux")
        r = subprocess.run([tmux, "show-environment", "-t", tmux_name, key],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and "=" in r.stdout:
            return r.stdout.strip().split("=", 1)[1]
    except Exception:
        _swallow("_tmux_get_env:615")
    return ""


def _session_env() -> dict:
    env = dict(os.environ)
    path_parts = [
        str(APP_DIR / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".bun" / "bin"),
    ]
    existing = env.get("PATH", "")
    if existing:
        path_parts.append(existing)
    seen = set()
    env["PATH"] = os.pathsep.join(
        p for p in os.pathsep.join(path_parts).split(os.pathsep)
        if p and not (p in seen or seen.add(p))
    )
    return env

# macOS GUI launches often get a minimal PATH. Normalize the parent process
# PATH once so all later subprocess calls can find Homebrew/user binaries.
os.environ["PATH"] = _session_env()["PATH"]


def _apply_macos_app_identity():
    if sys.platform != "darwin":
        return
    icon_candidates = [
        APP_DIR / "ShellFrame.app" / "Contents" / "Resources" / "shellframe.icns",
        Path.home() / "Applications" / "ShellFrame.app" / "Contents" / "Resources" / "shellframe.icns",
        Path("/Applications/ShellFrame.app/Contents/Resources/shellframe.icns"),
    ]
    icon_path = next((p for p in icon_candidates if p.exists()), None)
    try:
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "ShellFrame"
        info["CFBundleDisplayName"] = "ShellFrame"
        info["CFBundleIdentifier"] = "com.h2ocloud.shellframe"
        if icon_path is not None:
            info["CFBundleIconFile"] = str(icon_path)
    except Exception as e:
        _dlog("identity", f"set bundle info failed: {e}")
    try:
        from Foundation import NSProcessInfo
        NSProcessInfo.processInfo().setProcessName_("ShellFrame")
    except Exception as e:
        _dlog("identity", f"set process name failed: {e}")
    try:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyRegular,
            NSImage,
        )
        app = NSApplication.sharedApplication()
        try:
            app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        except Exception:
            _swallow("_apply_macos_app_identity:677")
        if icon_path is not None:
            img = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if img is not None:
                app.setApplicationIconImage_(img)
    except Exception as e:
        _dlog("identity", f"set app icon failed: {e}")


def _refresh_macos_app_launcher(app_path: Path) -> tuple[bool, str]:
    """Keep copied .app bundles from regressing to a shell-script executable.

    The source template's shell launcher is convenient for development, but
    LaunchServices/TCC identify shell/Python-launched GUI work as Python. The
    installed app needs a real Mach-O executable that remains the visible app
    process and spawns the shell/Python payload as a child.
    """
    try:
        if platform.system() != "Darwin":
            return True, "not macOS"
        macos_dir = app_path / "Contents" / "MacOS"
        resources_dir = app_path / "Contents" / "Resources"
        launcher = macos_dir / "shellframe"
        payload = resources_dir / "shellframe.sh"
        resources_dir.mkdir(parents=True, exist_ok=True)
        if not payload.exists() and launcher.exists():
            shutil.copy2(launcher, payload)
        old_payload = macos_dir / "shellframe.sh"
        if old_payload.exists() and not payload.exists():
            shutil.move(str(old_payload), str(payload))
        c_file = APP_DIR / "scripts" / "macos_app_launcher.c"
        clang = shutil.which("clang")
        if not c_file.exists():
            return False, "scripts/macos_app_launcher.c missing"
        if not clang:
            return False, "clang not found"
        arch_flags = []
        machine = platform.machine()
        if machine in ("arm64", "x86_64"):
            arch_flags = ["-arch", machine]
        subprocess.run(
            [clang, *arch_flags, "-mmacosx-version-min=12.0", str(c_file), "-o", str(launcher)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        launcher.chmod(0o755)
        try:
            payload.chmod(0o644)
        except Exception:
            _swallow("_refresh_macos_app_launcher:727")
        subprocess.run(["xattr", "-cr", str(app_path)], capture_output=True, timeout=10)
        subprocess.run(["codesign", "--force", "--sign", "-", str(app_path)], capture_output=True, timeout=30)
        return True, "native launcher refreshed"
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or b"").decode("utf-8", errors="replace").strip()
        return False, detail or str(e)
    except Exception as e:
        return False, str(e)


def _cmd_uses_startup_trust_agent(cmd: str) -> bool:
    tokens = shlex.split(cmd) if cmd else []
    for token in tokens:
        if Path(token).stem in STARTUP_TRUST_AI_TOOLS:
            return True
    return False


def _should_auto_accept_startup_trust(cmd: str, cwd: str) -> bool:
    try:
        trusted = str(Path(cwd).resolve()) in TRUSTED_STARTUP_CWDS
    except Exception:
        trusted = cwd in TRUSTED_STARTUP_CWDS
    return trusted and _cmd_uses_startup_trust_agent(cmd)

# Logging primitives + temp-dir constants live in sf_log so the Api mixin
# modules (api_history / api_schedules) can share them without importing
# main. Names are re-exported here — every existing call site is untouched.
from sf_log import TMP_DIR, DEBUG_LOG, _LOG_MAX_BYTES, _dlog, _swallow  # noqa: F401



def _has_tmux() -> bool:
    """Check if tmux is available on PATH."""
    return _tmux_bin() is not None

def _tmux_bin() -> str | None:
    """Find tmux even when macOS launches the .app with a minimal PATH."""
    path = _session_env().get("PATH", "")
    return shutil.which("tmux", path=path)

def _tmux_session_exists(name: str) -> bool:
    """Check if a tmux session with the given name exists."""
    tmux = _tmux_bin()
    if not tmux:
        return False
    r = subprocess.run([tmux, "has-session", "-t", name],
                       capture_output=True, timeout=3)
    return r.returncode == 0

def _list_tmux_sessions() -> list[dict]:
    """List all sf_* tmux sessions. Returns [{name, cmd, sid}]."""
    try:
        tmux = _tmux_bin()
        if not tmux:
            _dlog("lifecycle", "  tmux binary not found")
            return []
        r = subprocess.run(
            [tmux, "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            _dlog("lifecycle", f"  tmux list-sessions failed rc={r.returncode} err={r.stderr.strip()!r}")
            return []
        result = []
        for line in r.stdout.strip().split("\n"):
            name = line.strip()
            if not name.startswith(TMUX_PREFIX):
                continue
            # Get the original command from tmux env
            cr = subprocess.run(
                [tmux, "show-environment", "-t", name, "SF_CMD"],
                capture_output=True, text=True, timeout=3)
            cmd = ""
            if cr.returncode == 0 and "=" in cr.stdout:
                cmd = cr.stdout.strip().split("=", 1)[1]
            sr = subprocess.run(
                [tmux, "show-environment", "-t", name, "SF_SID"],
                capture_output=True, text=True, timeout=3)
            sid = ""
            if sr.returncode == 0 and "=" in sr.stdout:
                sid = sr.stdout.strip().split("=", 1)[1]
            result.append({"name": name, "cmd": cmd, "sid": sid})
        return result
    except Exception:
        return []


class Session:
    """One PTY tab session."""

    def __init__(self, sid: str, cmd: str, cols: int, rows: int,
                 on_data=None, tmux_name: str = None, account_refs: dict | None = None,
                 account_refs_authoritative: bool = False):
        self.sid = sid
        self.cmd = cmd
        self.cols = int(cols)
        self.rows = int(rows)
        self.account_refs = {
            provider: (account_refs or {}).get(provider)
            for provider in account_manager.PROVIDERS
        }
        # 舊 tmux tab 可能是在帳號 profile 功能加入前建立，manifest 只代表
        # 設定檔快照，不代表正在跑的 CLI 真正吃哪個帳號。切換動作明確傳入
        # authoritative 時才保留傳入 refs；一般 reattach 要以 tmux env 為準。
        self._account_refs_authoritative = bool(account_refs_authoritative)
        # Transcript correlation: claude tabs get a stable --session-id so the
        # auto status detector can find their JSONL. None for codex/other (codex
        # is mapped via lsof) and for reattached sessions (recovered from tmux env).
        self.cmd, self.session_id = _maybe_claude_session_id(self.cmd)
        self.cwd = _session_cwd()
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.master_fd = None
        self.child_pid = None
        self.win_proc = None
        self.alive = True
        now = time.time()
        # codex 分頁在 Windows 上要靠這個時間錨點認自己的 rollout：那裡沒有
        # lsof、也沒有 tmux pane 可查，只能問「哪一份 rollout 是我開起來之後
        # 才出現的」。
        self._spawn_ts = now
        self._last_activity_time = now
        self._last_user_activity_time = now
        self._last_output_activity_time = 0.0
        self._idle_reap_state = ""
        self._idle_summary_requested_at = 0.0
        self._idle_close_after = 0.0
        self._idle_summary_path = ""
        self._slug_pending = True   # True until first user Enter triggers tmux auto-rename
        self._recent = bytearray()  # ring buffer for peeking/startup checks (last 8KB), not consumed by read()
        self._on_data = on_data     # callback to signal new data (e.g. threading.Event.set)
        self._tmux_name = tmux_name  # tmux session name (None = no tmux)
        self._startup_trust_pending = _should_auto_accept_startup_trust(cmd, self.cwd)
        self._startup_trust_deadline = time.monotonic() + 45 if self._startup_trust_pending else 0
        self._startup_trust_answered = False
        # Stateful UTF-8 decoder — carries incomplete multi-byte sequences
        # across read() calls so CJK / box-drawing chars never get split
        # into U+FFFD replacement characters (the "─���─" garble).
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._start(self.cols, self.rows)

    def _account_env_overrides(self) -> dict:
        """Just the account-pinning env vars for this tab (CLAUDE_CONFIG_DIR /
        CLAUDE_CODE_OAUTH_TOKEN / CODEX_HOME). Passed to `tmux new-session` via
        `-e` so the pane actually inherits them (see _start_tmux)."""
        overrides = {}
        for provider, ref in self.account_refs.items():
            if ref:
                overrides.update(ACCOUNT_MANAGER.env_for(provider, ref))
        return overrides

    def _launch_env(self) -> dict:
        """Build the child environment for this tab's account snapshot."""
        env = _session_env()
        env.update(self._account_env_overrides())
        return env

    def _start(self, cols, rows):
        if IS_WIN:
            self._start_win(cols, rows)
        elif _has_tmux():
            self._start_tmux(cols, rows)
        else:
            self._start_unix(cols, rows)

    def _start_tmux(self, cols, rows):
        """Start or reattach a tmux session."""
        if not self._tmux_name:
            self._tmux_name = f"{TMUX_PREFIX}{self.sid}"

        if not _tmux_session_exists(self._tmux_name):
            # Create new tmux session (detached) running the command. We
            # explicitly pass `-c $HOME` so the spawned shell / AI CLI
            # starts in the user's home directory, not in shellframe's
            # install dir (which is just the chrome that hosts them).
            # That way `claude`, `codex`, bash etc. behave the same as if
            # the user opened them from a fresh Terminal — relative paths
            # mean what the user expects, and AI agents that run `pwd`
            # don't think the user wants to work on shellframe internals.
            # The init-prompt still tells the AI "shellframe source lives
            # at ~/.local/apps/shellframe/" if it's asked to self-modify.
            # -e SF_SID=…: the spawned CLI (and thus its Claude Code hooks,
            # which inherit the process env) can identify which ShellFrame
            # tab it belongs to. See sf_agent_hook.py.
            launch_env = self._launch_env()
            # Per-session env vars must be passed with `-e KEY=VAL`, NOT via
            # subprocess env: `tmux new-session` spawns the pane from the tmux
            # SERVER's environment, so `env=launch_env` is silently ignored
            # whenever a server already exists (i.e. any time there's more than
            # the first tab). The account overrides (CLAUDE_CONFIG_DIR /
            # CLAUDE_CODE_OAUTH_TOKEN / CODEX_HOME) live here — without `-e`
            # they never reach the child, so an account switch launches with the
            # wrong/default credentials and the tab looks logged-out
            # (Howard 2026-09-05: 切換 token 後對話消失、要重新 /login).
            new_session_env_args = ["-e", f"SF_SID={self.sid}"]
            for _k, _v in self._account_env_overrides().items():
                new_session_env_args += ["-e", f"{_k}={_v}"]
            result = subprocess.run([
                "tmux", "new-session", "-d",
                "-s", self._tmux_name,
                "-x", str(cols), "-y", str(rows),
                "-c", self.cwd,
                *new_session_env_args,
                self.cmd,
            ], capture_output=True, timeout=5, env=launch_env)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
                _dlog("lifecycle", f"tmux new-session failed name={self._tmux_name} cmd={self.cmd!r} error={detail!r}")
                self.alive = False
                raise RuntimeError(f"tmux failed to create session {self._tmux_name}: {detail or 'unknown error'}")
            # Store original command in tmux environment for recovery
            subprocess.run([
                "tmux", "set-environment", "-t", self._tmux_name,
                "SF_CMD", self.cmd,
            ], capture_output=True, timeout=3, env=launch_env)
            # Persist the claude --session-id so reattach/restart can recover
            # the transcript correlation without guessing.
            if self.session_id:
                subprocess.run([
                    "tmux", "set-environment", "-t", self._tmux_name,
                    "SF_SESSION_ID", self.session_id,
                ], capture_output=True, timeout=3, env=launch_env)
        else:
            # Existing session: the freshly generated session_id is wrong (the
            # running claude already chose one at creation). Recover the real one.
            self.session_id = _tmux_get_env(self._tmux_name, "SF_SESSION_ID") or None
            for provider in account_manager.PROVIDERS:
                runtime_ref = _tmux_get_env(
                    self._tmux_name, f"SF_ACCOUNT_{provider.upper()}"
                )
                if runtime_ref:
                    self.account_refs[provider] = runtime_ref
                elif not self._account_refs_authoritative:
                    # 沒有 runtime marker 的舊 tab 要視為未知，避免 UI 把
                    # manifest 的 Team 誤當成實際正在跑的帳號並鎖住按鈕。
                    self.account_refs[provider] = None
            # Resize existing tmux session to match terminal
            subprocess.run([
                "tmux", "resize-window", "-t", self._tmux_name,
                "-x", str(cols), "-y", str(rows),
            ], capture_output=True, timeout=3, env=_session_env())

        # Store stable metadata on both new and existing sessions. tmux names
        # can be renamed for readability; SF_SID is the durable tab identity.
        subprocess.run([
            "tmux", "set-environment", "-t", self._tmux_name,
            "SF_SID", self.sid,
        ], capture_output=True, timeout=3)
        subprocess.run([
            "tmux", "set-environment", "-t", self._tmux_name,
            "SF_CMD", self.cmd,
        ], capture_output=True, timeout=3)
        for provider in account_manager.PROVIDERS:
            ref = self.account_refs.get(provider)
            if ref:
                subprocess.run([
                    "tmux", "set-environment", "-t", self._tmux_name,
                    f"SF_ACCOUNT_{provider.upper()}={ref}",
                ], capture_output=True, timeout=3)

        # Attach via PTY fork — child runs `tmux attach`, parent reads master_fd
        self.child_pid, self.master_fd = pty.fork()
        if self.child_pid == 0:
            env = self._launch_env()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env.setdefault("LANG", "en_US.UTF-8")
            tmux = shutil.which("tmux", path=env.get("PATH"))
            os.execve(tmux, ["tmux", "attach-session", "-t", self._tmux_name], env)
        else:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                _swallow("Session._start_tmux:976")
            threading.Thread(target=self._reader_unix, daemon=True).start()

    def _start_unix(self, cols, rows):
        """Fallback: direct PTY fork (no tmux)."""
        args = shlex.split(self.cmd)
        env = self._launch_env()
        exe = shutil.which(args[0], path=env.get("PATH"))

        self.child_pid, self.master_fd = pty.fork()

        if self.child_pid == 0:
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["SF_SID"] = self.sid
            env.setdefault("LANG", "en_US.UTF-8")
            # chdir to the user's home before exec so the spawned process
            # doesn't inherit shellframe's install dir as its cwd. See
            # _start_tmux for the full rationale.
            try:
                os.chdir(self.cwd)
            except Exception:
                _swallow("Session._start_unix:998")

            if exe:
                os.execve(exe, args, env)
            else:
                shell = os.environ.get("SHELL", "/bin/bash")
                os.execve(shell, [shell, "-c", f"echo 'Command not found: {args[0]}'; exec {shell}"], env)
        else:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                _swallow("Session._start_unix:1010")
            threading.Thread(target=self._reader_unix, daemon=True).start()

    def _start_win(self, cols, rows):
        args = shlex.split(self.cmd)
        env = self._launch_env()
        exe = shutil.which(args[0], path=env.get("PATH"))
        cmd_args = [exe] + args[1:] if exe else ["powershell", "-NoProfile", "-Command", self.cmd]

        # Try pywinpty for full ConPTY support (colors, TUI)
        try:
            import winpty
            self._winpty = winpty.PtyProcess.spawn(
                cmd_args,
                dimensions=(rows, cols),
                env={**env, "TERM": "xterm-256color", "COLORTERM": "truecolor", "SF_SID": self.sid},
                cwd=self.cwd,
            )
            self._use_winpty = True
            threading.Thread(target=self._reader_winpty, daemon=True).start()
            return
        except ImportError:
            pass

        # Fallback: plain subprocess (no PTY, limited interactivity)
        self._use_winpty = False
        self.win_proc = subprocess.Popen(
            cmd_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.cwd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            env={**env, "TERM": "xterm-256color", "SF_SID": self.sid},
        )
        threading.Thread(target=self._reader_win, daemon=True).start()

    def _reader_unix(self):
        _last_data = time.time()
        while self.alive and self.master_fd is not None:
            try:
                # idle 退避：近 2s 有輸出用 0.05s（低延遲），閒置則 0.3s，砍掉 idle tab
                # 的空轉喚醒（select 一有資料即返回，不影響輸出延遲）。
                _timeout = 0.05 if (time.time() - _last_data) < 2.0 else 0.3
                r, _, _ = select.select([self.master_fd], [], [], _timeout)
                if r:
                    _last_data = time.time()
                    data = os.read(self.master_fd, 16384)
                    if not data:
                        self.alive = False
                        break
                    with self.lock:
                        self.buffer.extend(data)
                        self._recent.extend(data)
                        if len(self._recent) > 8192:
                            self._recent = self._recent[-8192:]
                        now = time.time()
                        self._last_activity_time = now
                        self._last_output_activity_time = now
                    if self._on_data:
                        self._on_data()
            except (OSError, ValueError):
                self.alive = False
                break

    def _reader_winpty(self):
        """Read from pywinpty ConPTY."""
        while self.alive:
            try:
                data = self._winpty.read(16384)
                if data:
                    raw = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
                    with self.lock:
                        self.buffer.extend(raw)
                        # _recent 一定要跟 unix reader 一樣餵：Windows 上 peek_fn、
                        # startup-trust 自動接受、TG 送達驗證的 fallback 全靠它，
                        # 漏餵等於這些機制在 Windows 整組失明。
                        self._recent.extend(raw)
                        if len(self._recent) > 8192:
                            self._recent = self._recent[-8192:]
                        now = time.time()
                        self._last_activity_time = now
                        self._last_output_activity_time = now
                    if self._on_data:
                        self._on_data()
                else:
                    break
            except (EOFError, OSError):
                break
        self.alive = False

    def _reader_win(self):
        """Read from plain subprocess (fallback)."""
        while self.alive and self.win_proc and self.win_proc.poll() is None:
            try:
                data = self.win_proc.stdout.read(4096)
                if data:
                    with self.lock:
                        self.buffer.extend(data)
                        now = time.time()
                        self._last_activity_time = now
                        self._last_output_activity_time = now
                    if self._on_data:
                        self._on_data()
                else:
                    break
            except:
                break
        self.alive = False

    def write(self, data: str, user_activity: bool = True):
        # Only log multi-char writes (init prompt, paste) — single keystrokes
        # are too noisy and the file open/close adds measurable latency.
        if len(data) > 2:
            preview = data[:80].replace('\r', '\\r').replace('\n', '\\n').replace('\x1b', '\\e')
            _dlog("write", f"sid={self.sid} len={len(data)} preview={preview!r}")
        if data:
            now = time.time()
            self._last_activity_time = now
            if user_activity:
                self._last_user_activity_time = now
        if IS_WIN and hasattr(self, '_use_winpty') and self._use_winpty:
            try:
                self._winpty.write(data)
            except (EOFError, OSError):
                _swallow("Session.write:1128")
            return
        raw = data.encode("utf-8", errors="replace")
        if IS_WIN:
            if self.win_proc and self.win_proc.stdin:
                try:
                    self.win_proc.stdin.write(raw)
                    self.win_proc.stdin.flush()
                except OSError:
                    _swallow("Session.write:1137")
        else:
            if self.master_fd is not None:
                try:
                    os.write(self.master_fd, raw)
                except OSError:
                    _swallow("Session.write:1143")

    def read(self) -> str:
        with self.lock:
            if not self.buffer:
                return ""
            data = bytes(self.buffer)
            self.buffer.clear()
        # Incremental decode: any trailing partial multi-byte sequence is
        # stashed in self._decoder and emitted on the next call, so CJK or
        # box-drawing characters spanning a 16KB read boundary stay intact.
        return self._decoder.decode(data)

    def resize(self, cols, rows):
        self.cols = int(cols)
        self.rows = int(rows)
        if IS_WIN and hasattr(self, '_use_winpty') and self._use_winpty:
            try:
                self._winpty.setwinsize(rows, cols)
            except (OSError, AttributeError):
                _swallow("Session.resize:1161")
        elif not IS_WIN and self.master_fd is not None:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                _swallow("Session.resize:1167")
            # Also resize the tmux window so it doesn't clip
            if self._tmux_name:
                subprocess.run(
                    ["tmux", "resize-window", "-t", self._tmux_name,
                     "-x", str(cols), "-y", str(rows)],
                    capture_output=True, timeout=3)

    def kill(self, kill_tmux=True):
        """Kill the session. If kill_tmux=False, only detach (tmux session stays alive)."""
        self.alive = False
        if IS_WIN:
            if hasattr(self, '_use_winpty') and self._use_winpty:
                try:
                    self._winpty.terminate()
                except:
                    _swallow("Session.kill:1183")
            elif self.win_proc:
                self.win_proc.terminate()
        else:
            # Close master fd first — sends SIGHUP to the attach process (not the tmux session)
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    _swallow("Session.kill:1192")
                self.master_fd = None
            if self._tmux_name and kill_tmux:
                # Kill the tmux session (and the process inside it)
                subprocess.run(["tmux", "kill-session", "-t", self._tmux_name],
                               capture_output=True, timeout=3)
            elif not self._tmux_name and self.child_pid:
                # No tmux — kill child process directly
                try:
                    os.killpg(os.getpgid(self.child_pid), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    _swallow("Session.kill:1203")
                threading.Timer(1.0, self._force_kill).start()

    def _force_kill(self):
        if self.child_pid:
            try:
                os.waitpid(self.child_pid, os.WNOHANG)
            except ChildProcessError:
                return  # already dead
            except OSError:
                return
            try:
                os.killpg(os.getpgid(self.child_pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                _swallow("Session._force_kill:1217")


class Api(HistoryApiMixin, SchedulesApiMixin):
    """JS <-> Python bridge."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.bridge: TelegramBridge = None  # single global bridge
        self.line_bridge: LineBridge = None  # optional LINE bridge plugin
        self._counter = 0
        self._window = None
        self._pusher_started = False
        self._status_started = False
        self._status_tracker = agent_status.StatusTracker()
        self._active_sid = ""                     # 當前顯示 tab；背景 tab 的 webview push 節流用
        self._output_event = threading.Event()   # signalled by reader threads
        self._bridge_queue = SimpleQueue()        # feed_output off the hot path
        self._plugins = None
        self._idle_reaper_started = False
        self._api_httpd = None        # Local HTTP API server handle (Settings hot-toggle)
        self.frame_link = None        # FrameLink instance (created lazily in _start_frame_link)
        self._hook_events = {}        # sid -> hook-driven state (see _on_agent_event)
        self._status_cache = {}       # sid -> cached status result (idle gating)
        self._plugins_reload()
        self._start_idle_reaper()

    def _save_soft_session(self, sid: str, cmd: str):
        """Persist a session entry to config.session_list. Used as soft
        persistence on Windows (no tmux) — startup will recreate these as
        fresh PTYs. No-op on systems with tmux since tmux already persists."""
        if not IS_WIN and _has_tmux():
            return
        def _mut(cfg):
            sessions = [s for s in cfg.get("session_list", []) if s.get("sid") != sid]
            sessions.append({"sid": sid, "cmd": cmd})
            cfg["session_list"] = sessions
        update_config(_mut)

    def _drop_soft_session(self, sid: str):
        """Remove a session from soft-persistence list."""
        if not IS_WIN and _has_tmux():
            return
        def _mut(cfg):
            sessions = cfg.get("session_list", [])
            new_list = [s for s in sessions if s.get("sid") != sid]
            if len(new_list) != len(sessions):
                cfg["session_list"] = new_list
        update_config(_mut)

    def _ordered_sids(self, cfg: dict | None = None, preferred_order: list[str] | None = None) -> list[str]:
        """Return current session ids in durable UI order."""
        cfg = cfg or load_config()
        raw_order = preferred_order or cfg.get("session_order") or []
        ordered = [sid for sid in raw_order if sid in self.sessions]
        ordered.extend([sid for sid in self.sessions if sid not in ordered])
        return ordered

    def _persist_session_manifest(self, preferred_order: list[str] | None = None):
        """Persist all open tabs, labels, order, tmux names, and bridge state.

        tmux keeps processes alive across app restarts, but not across a full
        machine reboot. This manifest is the disk-backed fallback: after reboot
        ShellFrame can recreate the same tabs and reconnect Telegram even when
        tmux has no surviving sessions.
        """
        try:
            cfg = load_config()
            labels = cfg.get("session_labels", {}) or {}
            disabled = set(cfg.get("bridge_disabled_sessions", []) or [])
            prev_allowed = set(cfg.get("glasses_allowed_sessions", []) or [])
            glasses_allowed = set(prev_allowed)
            order = self._ordered_sids(cfg, preferred_order)
            manifest = []
            for idx, sid in enumerate(order):
                s = self.sessions.get(sid)
                if not s:
                    continue
                label = getattr(s, '_custom_label', None) or labels.get(sid)
                bridge_enabled = getattr(s, '_bridge_enabled', True)
                if not bridge_enabled:
                    disabled.add(sid)
                else:
                    disabled.discard(sid)
                # Allow list, not a deny list: a tab that is missing from the
                # manifest must come back with the glasses OFF, never ON.
                #
                # The sentinel matters. Reading this with a `False` default and
                # then discarding means **any** code path that builds a Session
                # without setting the flag silently revokes that tab — the
                # authorisation quietly disappears and looks like a bug in the
                # glasses instead. `None` = "this object never had an opinion",
                # and an object with no opinion must not overrule the file.
                glasses_flag = getattr(s, '_glasses_enabled', None)
                if glasses_flag is True:
                    glasses_allowed.add(sid)
                elif glasses_flag is False:
                    glasses_allowed.discard(sid)
                glasses_enabled = sid in glasses_allowed
                entry = {
                    "sid": sid,
                    "cmd": _canonical_cmd(s.cmd),
                    "tmux_name": getattr(s, '_tmux_name', None) or "",
                    "account_refs": dict(getattr(s, "account_refs", {}) or {}),
                    "bridge_enabled": bool(bridge_enabled),
                    "glasses_enabled": bool(glasses_enabled),
                    "order": idx,
                    "updated_at": int(time.time()),
                }
                # 模型 badge 的即時真相是 hook 回報的 transcript 路徑（見
                # agent_event）。它原本只活在記憶體裡：ShellFrame 一重啟，所有
                # 分頁的 hint 就消失，偵測掉回 cmd 的 --resume uuid ＝ 啟動時
                # 指定的那份舊 transcript，badge 於是顯示過期模型。
                hook_tp = getattr(s, "_hook_transcript_path", "") or ""
                if hook_tp:
                    entry["transcript_path"] = hook_tp
                hook_csid = getattr(s, "session_id", "") or ""
                if hook_csid:
                    entry["claude_session_id"] = hook_csid
                # codex 沒有 hook 可以回報，得自己認 rollout。Windows 關掉
                # ShellFrame 等於整批 session 斷線（沒有 tmux 撐著），這個 id
                # 是重開後唯一能接回原本對話的線索。
                if _worker_is_codex(getattr(s, "cmd", "")):
                    codex_sid = self._codex_session_id(sid, s)
                    if codex_sid:
                        entry["codex_session_id"] = codex_sid
                lifecycle_source = getattr(s, "_lifecycle_source", "") or ""
                if lifecycle_source:
                    entry["lifecycle_source"] = lifecycle_source
                if getattr(s, "_lifecycle_handoff", False):
                    entry["lifecycle_handoff"] = True
                if label:
                    entry["label"] = label
                    labels[sid] = label
                manifest.append(entry)
            cfg["session_manifest"] = manifest
            cfg["session_order"] = [e["sid"] for e in manifest]
            cfg["session_labels"] = labels
            cfg["bridge_disabled_sessions"] = sorted(disabled)
            # Tripwire. Emptying the allow list is a legitimate thing for a deny
            # to do, but it should never be a side effect of persisting the tab
            # list — and if it ever is again, this is the line that says so.
            if prev_allowed and not glasses_allowed:
                _dlog("glasses", f"allow list emptied while persisting manifest: "
                                 f"was {sorted(prev_allowed)}, sessions seen={len(order)}")
            cfg["glasses_allowed_sessions"] = sorted(glasses_allowed)
            save_config(cfg)
        except Exception as e:
            _dlog("lifecycle", f"persist manifest failed: {e}")

    @staticmethod
    def _idle_reaper_config(cfg: dict | None = None) -> dict:
        cfg = cfg or load_config()
        raw = cfg.get("idle_reaper") or {}
        default = DEFAULT_CONFIG.get("idle_reaper", {})
        merged = {**default, **raw}
        try:
            merged["review_sec"] = max(5.0, float(merged.get("review_sec", 300)))
        except (TypeError, ValueError):
            merged["review_sec"] = 300.0
        try:
            merged["idle_sec"] = max(30.0, float(merged.get("idle_sec", 1800)))
        except (TypeError, ValueError):
            merged["idle_sec"] = 1800.0
        try:
            merged["summary_grace_sec"] = max(10.0, float(merged.get("summary_grace_sec", 120)))
        except (TypeError, ValueError):
            merged["summary_grace_sec"] = 120.0
        merged["keep_labels"] = [str(x).strip() for x in (merged.get("keep_labels") or []) if str(x).strip()]
        merged["keep_sids"] = [str(x).strip() for x in (merged.get("keep_sids") or []) if str(x).strip()]
        merged["reflection_file"] = str(merged.get("reflection_file") or "").strip()
        return merged

    @staticmethod
    def _agent_roster_config(cfg: dict | None = None) -> dict:
        cfg = cfg or load_config()
        raw = cfg.get("agent_roster") or {}
        roster = {}
        for role, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            clean = dict(entry)
            clean["role"] = str(role)
            clean["label"] = str(clean.get("label") or role).strip()
            clean["cmd"] = _canonical_cmd(str(clean.get("cmd") or "claude").strip())
            clean["agent_code"] = str(clean.get("agent_code") or "").strip()
            clean["responsibility"] = str(clean.get("responsibility") or "").strip()
            clean["handoff"] = bool(clean.get("handoff", True))
            if clean["label"] and clean["cmd"]:
                roster[str(role)] = clean
        return roster

    @staticmethod
    def _resolve_agent_role(role: str, roster: dict) -> tuple[str | None, dict | None]:
        wanted = str(role or "").strip()
        if not wanted:
            return None, None
        if wanted in roster:
            return wanted, roster[wanted]
        alias = AGENT_ROLE_ALIASES.get(wanted.casefold()) or AGENT_ROLE_ALIASES.get(wanted)
        if alias in roster:
            return alias, roster[alias]
        for key, entry in roster.items():
            if wanted.casefold() == key.casefold():
                return key, entry
            label = str(entry.get("label") or "")
            if wanted.casefold() == label.casefold():
                return key, entry
        for key, entry in roster.items():
            label = str(entry.get("label") or "")
            if wanted.casefold() in key.casefold() or wanted.casefold() in label.casefold():
                return key, entry
        return None, None

    def _find_session_by_label(self, label: str) -> tuple[str, Session | None]:
        wanted = str(label or "").strip()
        if not wanted:
            return "", None
        for sid, session in self.sessions.items():
            if not getattr(session, "alive", False):
                continue
            current = getattr(session, "_custom_label", None) or ""
            if current == wanted:
                return sid, session
        for sid, session in self.sessions.items():
            if not getattr(session, "alive", False):
                continue
            current = getattr(session, "_custom_label", None) or ""
            if current.casefold() == wanted.casefold():
                return sid, session
        return "", None

    def _extract_tab_tags(self, text: str, exclude_sid: str = "") -> list[dict]:
        """Resolve ``#<tab-label>`` (or ``#<sid>``) tags in *text* to live sessions.

        Longest labels match first and matched spans are masked so a label that
        is a prefix of another (``#研究`` vs ``#研究-CLD``) can't double-fire.
        Returns ``[{"label": ..., "sid": ...}, ...]`` in order of appearance.
        """
        text = str(text or "")
        if "#" not in text:
            return []
        candidates = []
        for sid, session in self.sessions.items():
            if not getattr(session, "alive", False):
                continue
            label = self._session_label(sid, session)
            needles = {label, sid} if label != sid else {sid}
            for needle in needles:
                if needle:
                    candidates.append((needle, label, sid))
        candidates.sort(key=lambda t: len(t[0]), reverse=True)
        lowered = text.casefold()
        consumed: list[tuple[int, int]] = []
        seen: set[str] = set()
        found = []
        # The excluded session (the delegation target itself) still consumes its
        # matched spans — otherwise a shorter label that is its prefix
        # (#研究 in #研究-CLD) would false-positive on the leftover text.
        for needle, label, sid in candidates:
            target = ("#" + needle).casefold()
            start = 0
            while True:
                pos = lowered.find(target, start)
                if pos < 0:
                    break
                end = pos + len(target)
                if sid not in seen and not any(pos < e and s < end for s, e in consumed):
                    seen.add(sid)
                    consumed.append((pos, end))
                    if sid != exclude_sid:
                        found.append({"label": label, "sid": sid, "pos": pos})
                    break
                start = pos + 1
        found.sort(key=lambda d: d["pos"])
        return [{"label": d["label"], "sid": d["sid"]} for d in found]

    @staticmethod
    def _delegate_prompt(role: str, entry: dict, task: str, tagged: list[dict] | None = None) -> str:
        label = entry.get("label") or role
        responsibility = entry.get("responsibility") or "依總控派工處理指定任務"
        prompt = (
            f"你是「{label}」worker。\n"
            f"職責：{responsibility}\n\n"
            "這是 ShellFrame 總控派工。請維持自己的職責邊界，不要主動接手其他 worker 的領域。\n\n"
            "任務：\n"
            f"{task.strip()}\n\n"
        )
        if tagged:
            tag_lines = "\n".join(f"- #{t['label']} → sid {t['sid']}" for t in tagged)
            prompt += (
                "任務中用 # 標註了需要互動的 tab（其他 agent session）：\n"
                f"{tag_lines}\n"
                "與被標註 tab 互動是本任務的必要環節，不是可選項：\n"
                "- 先 `sfctl peek <sid> --lines 60` 了解該 tab 目前狀態與上下文，再行動。\n"
                "- 用 `sfctl send <sid> '<訊息>'` 對該 tab 的 agent 提問、下指令或交接；送出後再 peek 確認對方收到並回應，必要時等待或追問。\n"
                "- 回報時務必包含與各標註 tab 的互動結果；提及 tab 用 label（#名稱），sid 只用在 sfctl 指令。\n\n"
            )
        prompt += (
            "工作規則：\n"
            "- 先確認需要的上下文與現有狀態；避免重複建立、重複送出或覆蓋。\n"
            "- 查檔案時先限定已知專案路徑；不要廣掃整個 /Users、~/Library、~/Library/Mobile Documents、Mail、Messages、Photos 等 macOS 受保護資料夾，避免觸發系統隱私權限彈窗。找不到路徑時先回報需要總控補上下文。\n"
            "- 若是外部可見操作，只有在使用者文字已明確授權時才送出；否則先 dry-run 或回報需要確認。\n"
            "- 若任務需要其他 worker，回報總控改派，不要自行擴張範圍。\n"
            "- 若產出是可直接給使用者的草稿、報告、查詢結果或操作結論，先用「可直接轉貼」格式回覆，讓總控能立即轉交，不要等其他平行工作完成。\n"
            "- 完成後回覆：可直接轉貼內容、結果、驗證、變更/送出項目、阻塞、是否建議納入 memory/skill/docs。\n"
            "\n燈號（自動偵測，通常不需自報）：\n"
            "- 本 tab 燈號由 ShellFrame 從你的『實際活動』自動判定（工具呼叫、回合起訖、畫面）：執行中自動亮 🔵、回合結束自動轉 🟢。"
            "**不需要再印 `[[SF:WORKING]]` 或 `[[SF:GREEN]]`**，專心做事即可。\n"
            "- 只有兩個『偵測看不出來』的狀態保留為可選提示（要用時自成一行、前後不接其他文字）：\n"
            "  - 需要 Howard／總控決策 → `[[SF:RED]]` → 🔴紅，並接編號選單（選項即決策內容），讓 TG 把選項推給 Howard。\n"
            "  - 卡在『外部條件』（等人回覆、等他隊、等外部事件等偵測看不到的）→ `[[SF:YELLOW:一句話原因]]` → 🟡黃，原因推給 Howard。\n"
            "- 這兩個是提示不是狀態回報；不確定就不要印，working／done 由偵測涵蓋。決策回合仍要附編號選單供 Howard 選擇。\n"
            "\n收尾規則（務必遵守）：\n"
            "- 每次『完成任務』或『需要 Howard／總控決策』時，回合最後務必輸出一個編號選項選單（搭配上面 GREEN／RED 燈號），"
            "讓 ShellFrame 偵測為待決策、把選項以 TG 按鈕推給 Howard。不要只用純文字結尾後 idle。\n"
            "- 選單格式硬規則：至少 2 項，每項自成一行、行首為「數字.」或「數字)」，連續排列、中間不夾其他文字或空行，例如：\n"
            "1. 回收此 tab（任務完成）\n"
            "2. 還要調整：<說明>\n"
            "- 『先沉澱記憶，才可被回收』：在選單提供『回收此 tab』選項前，必須先確認已把本次洞察／學習／"
            "操作 gotcha 寫入 memory（~/.claude/projects/-Users-howard/memory/），或在該選項旁註明「此任務無需記憶」。"
            "這也是 GREEN 的前提——記憶沉澱完才給綠燈。\n"
        )
        user_prompt = bridge_telegram.load_user_instructions(max_chars=2000)
        if user_prompt:
            prompt += (
                "\n## User Instructions (excerpt)\n\n"
                f"{user_prompt}\n"
            )
        return prompt

    def delegate_task(self, role: str, task: str) -> dict:
        task = str(task or "").strip()
        if not task:
            return {"success": False, "message": "task required"}
        roster = self._agent_roster_config(load_config())
        resolved_role, entry = self._resolve_agent_role(role, roster)
        if not entry:
            roles = ", ".join(roster.keys()) or "(empty)"
            return {"success": False, "message": f"Unknown role: {role}. Available: {roles}"}

        label = entry.get("label") or resolved_role
        sid, session = self._find_session_by_label(label)
        created = False
        if not session:
            sid = self.new_session(
                entry.get("cmd", "claude"),
                200,
                50,
                source="delegate",
                handoff=bool(entry.get("handoff", True)),
            )
            renamed = json.loads(self.rename_session(sid, label))
            if not renamed.get("success"):
                return {"success": False, "message": f"Created {sid} but rename to {label} failed"}
            session = self.sessions.get(sid)
            created = True
        if not session:
            return {"success": False, "message": f"No session available for {label}"}

        tagged = self._extract_tab_tags(task, exclude_sid=sid)
        prompt = self._delegate_prompt(resolved_role, entry, task, tagged=tagged)
        session._startup_trust_pending = False
        self._send_text_to_session(session, prompt, submit=True)
        return {
            "success": True,
            "message": f"Delegated to {label} ({sid})",
            "details": {
                "sid": sid,
                "label": label,
                "role": resolved_role,
                "created": created,
                "cmd": entry.get("cmd"),
                "tagged_tabs": tagged,
                "next": f"sfctl peek {sid} --lines 80",
            },
        }

    @staticmethod
    def _session_is_ai(cmd: str) -> bool:
        try:
            tokens = shlex.split(cmd or "")
        except ValueError:
            tokens = []
        for token in tokens:
            base = Path(token).stem
            if base in AI_CLI_TOOLS:
                return True
        return False

    def _main_session_sid(self, cfg: dict, idle_cfg: dict) -> str:
        explicit = str(idle_cfg.get("main_sid") or "").strip()
        if explicit in self.sessions:
            return explicit
        ordered = self._ordered_sids(cfg)
        return ordered[0] if ordered else ""

    def _should_keep_session(self, sid: str, s: Session, cfg: dict, idle_cfg: dict) -> bool:
        if sid in set(idle_cfg.get("keep_sids") or []):
            return True
        if idle_cfg.get("keep_bridge_active", True) and sid in self._bridge_active_sids():
            return True
        label = (getattr(s, "_custom_label", None) or "").strip()
        keep_labels = {x.casefold() for x in (idle_cfg.get("keep_labels") or [])}
        if label.casefold() in keep_labels:
            return True
        if idle_cfg.get("keep_first_session") and sid == self._main_session_sid(cfg, idle_cfg):
            return True
        return False

    def _bridge_active_sids(self) -> set[str]:
        active: set[str] = set()
        for bridge in (self.bridge, self.line_bridge):
            if not bridge:
                continue
            slots = getattr(bridge, "slots", {}) or {}
            for value in getattr(bridge, "_user_active", {}).values():
                if value in slots:
                    active.add(value)
            default_sid = getattr(bridge, "_default_active_sid", "")
            if default_sid in slots:
                active.add(default_sid)
            ui_sid = getattr(bridge, "_ui_active_sid", "")
            if ui_sid in slots:
                active.add(ui_sid)
            try:
                status_sid = bridge.get_primary_active_sid()
            except AttributeError:
                try:
                    status_sid = bridge._status_active_sid()
                except Exception:
                    status_sid = ""
            except Exception:
                status_sid = ""
            if status_sid in slots:
                active.add(status_sid)
        return active

    def _bridge_session_busy(self, sid: str) -> bool:
        for bridge in (self.bridge, self.line_bridge):
            if not bridge:
                continue
            try:
                slot = bridge.slots.get(sid)
            except Exception:
                slot = None
            if slot and (
                getattr(slot, "awaiting_response", False)
                or getattr(slot, "expect_marker", False)
                or getattr(slot, "pending_target_id", "")
            ):
                return True
        return False

    def _idle_summary_prompt(self, label: str, idle_sec: int, idle_cfg: dict | None = None) -> str:
        idle_cfg = idle_cfg or {}
        reflection_file = str(idle_cfg.get("reflection_file") or "").strip()
        sediment = ""
        if idle_cfg.get("self_sediment") and reflection_file:
            sediment = (
                "\n沉澱要求：若這個 session 有值得保留的流程、偏好或專案長期事實，"
                "請在輸出總結後，追加一段精簡反思到下列共用反思主檔：\n"
                f"{reflection_file}\n"
                "只寫 Agent Reflections.md；不要直接改 Agent Memory Hub 或私有 memory。"
                "若不值得沉澱，請在總結中寫 none 並用一句話說明。\n"
            )
        return (
            "ShellFrame 閒置排程即將關閉這個 session。\n"
            f"Session：{label or 'unnamed'}\n"
            f"閒置秒數：約 {idle_sec}\n\n"
            "請在關閉前輸出一份簡短總結與複盤，使用繁體中文。\n"
            "請包含：\n"
            "1. 這個 session 完成了什麼。\n"
            "2. 尚未完成事項或風險。\n"
            "3. 是否建議沉澱為 skill、memory 或 none，並說明原因。\n"
            f"{sediment}"
            "除上述共用反思主檔追加外，請不要執行其他外部可見或破壞性操作。\n"
        )

    def _capture_session_summary(self, sid: str, s: Session, idle_cfg: dict) -> str:
        summary_dir = Path(str(idle_cfg.get("summary_dir") or (CONFIG_DIR / "session_summaries"))).expanduser()
        summary_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = summary_dir / f"{stamp}_{sid}.txt"
        label = getattr(s, "_custom_label", None) or sid
        text = ""
        if getattr(s, "_tmux_name", None):
            try:
                r = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-J", "-t", s._tmux_name, "-S", "-200"],
                    capture_output=True, text=True, timeout=3,
                )
                if r.returncode == 0:
                    text = r.stdout
            except Exception as e:
                text = f"(capture failed: {e})"
        if not text:
            with s.lock:
                text = bytes(s._recent).decode("utf-8", errors="replace")
        path.write_text(
            f"sid: {sid}\nlabel: {label}\ncmd: {s.cmd}\nclosed_at: {datetime.now().isoformat()}\n\n{text}",
            encoding="utf-8",
        )
        s._idle_summary_path = str(path)
        return str(path)

    def _session_label(self, sid: str, s: Session | None = None) -> str:
        if s is not None:
            label = getattr(s, "_custom_label", None)
            if label:
                return str(label)
        try:
            labels = load_config().get("session_labels", {}) or {}
            label = labels.get(sid)
            if label:
                return str(label)
        except Exception:
            _swallow("Api._session_label:1691")
        return sid

    def _master_turn_preamble_enabled(self) -> bool:
        try:
            settings = load_config().get("settings", {}) or {}
            return settings.get("master_turn_preamble_enabled", True) is not False
        except Exception:
            return True

    def _is_master_session(self, sid: str, s: Session | None = None) -> bool:
        label = self._session_label(sid, s).strip()
        folded = label.casefold()
        return (
            label.startswith("總控")
            or folded.startswith("master")
            or folded.startswith("user-facing")
            or "user-facing" in folded
        )

    def _should_prepend_master_turn_preamble(self, sid: str, s: Session, data: str) -> bool:
        if "\r" not in (data or ""):
            return False
        if not (data or "").rstrip("\r\n").strip():
            return False
        return (
            self._master_turn_preamble_enabled()
            and self._is_master_session(sid, s)
            and self._should_inject_init(getattr(s, "cmd", ""))
        )

    def _wrap_master_turn_input(self, user_text: str) -> str:
        preamble = MASTER_TURN_PREAMBLE
        if self.bridge:
            try:
                from bridge_telegram import get_master_turn_preamble
                preamble = get_master_turn_preamble()
            except Exception:
                _swallow("Api._wrap_master_turn_input:1729")
        show_tag = True
        if self.bridge:
            try:
                from bridge_telegram import show_tg_wrapper
                show_tag = show_tg_wrapper()
            except Exception:
                _swallow("Api._wrap_master_turn_input:1736")
        tagged = self._extract_tab_tags(user_text)
        if tagged:
            mapping = "、".join(f"#{t['label']}={t['sid']}" for t in tagged)
            preamble += (
                f"\n[SF tab tags] 本則訊息標註了 tab：{mapping}。"
                "派工時請在 task 文字中原樣保留這些 #tag（delegate 會自動為 worker 附上互動指示）；"
                "若由你直接處理，請自行用 sfctl peek/send 與這些 tab 互動。"
            )
        tag = "[SF delegation prompt ↓]\n" if show_tag else ""
        return f"{tag}{preamble}\n\n---\nUser message: {user_text}"

    def _arm_awaiting_response(self, sid: str, data: str):
        """Tell the bridge this session is awaiting an AI response so
        completion notifications fire for local (non-TG) input too."""
        if "\r" not in (data or ""):
            return
        if not (data or "").rstrip("\r\n").strip():
            return
        s = self.sessions.get(sid)
        if not s or not self._should_inject_init(getattr(s, "cmd", "")):
            return
        if self.bridge:
            slot = self.bridge.slots.get(sid)
            if slot:
                slot.awaiting_response = True
                slot.last_write_ts = time.time()
                slot.stall_warned = False

    def _handoff_target_sid(self, exclude_sids: set[str] | None = None) -> str:
        exclude_sids = exclude_sids or set()
        try:
            cfg = load_config()
            idle_cfg = self._idle_reaper_config(cfg)
        except Exception:
            cfg = {}
            idle_cfg = {}
        labels = cfg.get("session_labels", {}) or {}
        ordered = self._ordered_sids(cfg)
        preferred = []
        main_sid = self._main_session_sid(cfg, idle_cfg)
        if main_sid:
            preferred.append(main_sid)
        preferred.extend([
            sid for sid, s in self.sessions.items()
            if "總控" in (getattr(s, "_custom_label", None) or labels.get(sid, "") or "")
        ])
        preferred.extend(ordered)
        preferred.extend(self.sessions.keys())
        seen = set()
        for sid in preferred:
            if sid in seen or sid in exclude_sids:
                continue
            seen.add(sid)
            s = self.sessions.get(sid)
            if s and getattr(s, "alive", False):
                return sid
        return ""

    def _write_lifecycle_handoff(self, title: str, bullets: list[str], exclude_sids: set[str] | None = None):
        try:
            idle_cfg = self._idle_reaper_config(load_config())
            if idle_cfg.get("handoff_to_main") is False:
                return
            target_sid = self._handoff_target_sid(exclude_sids)
            target = self.sessions.get(target_sid)
            if not target:
                return
            lines = ["[ShellFrame 交接]", title]
            lines.extend(f"- {b}" for b in bullets if b)
            text = "\n".join(lines).rstrip() + "\n"
            _dlog("handoff", f"target={target_sid} title={title!r} bullets={len(bullets)}")
            # Only inject into terminal when bridge is NOT active (local-only mode).
            # When TG bridge is running, _bridge_send_handoff already delivers the
            # notification; injecting multi-line text into the PTY input causes it to
            # pile up in the user's input box without being submitted.
            bridge_active = bool(self.bridge and getattr(self.bridge, "active", False))
            if not bridge_active:
                compact = " | ".join(line for line in lines if line.strip())
                # 用 bracketed-paste + 分離 Enter 的可靠提交（_send_text_to_session），
                # 取代 naive write(text+"\r")——後者在 TUI（總控 mid-turn / 輸入殘留）
                # 會卡在輸入框沒送出（使用者 2026-06-27 實際踩到）。
                self._send_text_to_session(target, compact, submit=True)
            self._bridge_send_handoff(text)
        except Exception as e:
            _dlog("handoff", f"write failed: {e}")

    def _bridge_send_handoff(self, text: str):
        """Send lifecycle handoff message via Telegram bridge."""
        try:
            bridge = self.bridge
            if not bridge or not getattr(bridge, "active", False):
                return
            token = bridge.config.bot_token
            if not token:
                return
            chat_ids = set((bridge._user_chat or {}).values())
            if not chat_ids:
                chat_ids = set(bridge.config.allowed_users or [])
            if not chat_ids:
                return
            from bridge_telegram import tg_api
            for chat_id in chat_ids:
                tg_api(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": text.strip(),
                }, timeout=5)
            _dlog("handoff", f"TG handoff sent to {len(chat_ids)} chats")
        except Exception as e:
            _dlog("handoff", f"TG send failed: {e}")

    # ── 延遲送出佇列（TG /delay）──────────────────────────────────────────
    @staticmethod
    def _load_delays() -> list:
        try:
            return json.loads(DELAYS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []

    @staticmethod
    def _save_delays(items: list):
        try:
            SF_STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = DELAYS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            tmp.replace(DELAYS_FILE)
        except Exception as e:
            _dlog("delay", f"save failed: {e}")

    def delay_add(self, sid: str, text: str, delay_sec: int,
                  chat_id: int = 0, label: str = "") -> dict:
        if not sid or not text.strip():
            return {"success": False, "message": "缺 sid 或內容"}
        delay_sec = max(1, int(delay_sec))
        items = self._load_delays()
        did = uuid.uuid4().hex[:6]
        items.append({
            "id": did, "sid": sid, "text": text,
            "due_ts": time.time() + delay_sec, "created_ts": time.time(),
            "chat_id": int(chat_id or 0), "label": label or sid,
        })
        self._save_delays(items)
        return {"success": True, "id": did, "due_ts": time.time() + delay_sec}

    def delay_list(self) -> list:
        return sorted(self._load_delays(), key=lambda x: x.get("due_ts", 0))

    def delay_cancel(self, did: str) -> dict:
        items = self._load_delays()
        kept = [x for x in items if x.get("id") != did]
        if len(kept) == len(items):
            return {"success": False, "message": f"找不到排程 {did}"}
        self._save_delays(kept)
        return {"success": True}

    def _start_delay_scheduler(self):
        if getattr(self, "_delay_started", False):
            return
        self._delay_started = True

        def _fire(entry):
            sid = entry.get("sid", "")
            s = self.sessions.get(sid)
            label = entry.get("label", sid)
            ok = False
            if s and s.alive:
                try:
                    s._startup_trust_pending = False
                    self._send_text_to_session(s, entry.get("text", ""), submit=True)
                    ok = True
                except Exception as e:
                    _dlog("delay", f"fire failed sid={sid}: {e}")
            chat_id = entry.get("chat_id")
            if chat_id and self.bridge:
                try:
                    from bridge_telegram import tg_api
                    note = (f"⏰ 已送出排程 prompt → {label}" if ok
                            else f"⚠️ 排程到點但分頁不在了（{label}），未送出")
                    tg_api(self.bridge.config.bot_token, "sendMessage",
                           {"chat_id": chat_id, "text": note}, timeout=5)
                except Exception:
                    _swallow("_start_delay_scheduler:notify")

        def _loop():
            while True:
                try:
                    items = self._load_delays()
                    if items:
                        now = time.time()
                        due = [x for x in items if x.get("due_ts", 0) <= now]
                        if due:
                            keep = [x for x in items if x.get("due_ts", 0) > now]
                            self._save_delays(keep)
                            for entry in due:
                                _fire(entry)
                except Exception as e:
                    _dlog("delay", f"scheduler loop error: {e}")
                time.sleep(5)

        threading.Thread(target=_loop, daemon=True, name="sf-delay").start()

    def _start_idle_reaper(self):
        if self._idle_reaper_started:
            return
        self._idle_reaper_started = True

        def _loop():
            while True:
                cfg = load_config()
                idle_cfg = self._idle_reaper_config(cfg)
                review_sec = idle_cfg.get("review_sec", 300.0)
                if not idle_cfg.get("enabled", False):
                    time.sleep(review_sec)
                    continue
                now = time.time()
                for sid, s in list(self.sessions.items()):
                    if not getattr(s, "alive", False):
                        continue
                    if self._should_keep_session(sid, s, cfg, idle_cfg):
                        continue
                    if idle_cfg.get("close_ai_only", True) and not self._session_is_ai(s.cmd):
                        continue
                    if self._bridge_session_busy(sid):
                        continue
                    state = getattr(s, "_idle_reap_state", "")
                    if state == "summarizing":
                        if getattr(s, "_last_user_activity_time", 0) > getattr(s, "_idle_summary_requested_at", 0):
                            s._idle_reap_state = ""
                            s._idle_summary_requested_at = 0.0
                            s._idle_close_after = 0.0
                            _dlog("idle", f"cancel summary sid={sid} reason=user_input")
                            continue
                        if now >= getattr(s, "_idle_close_after", 0):
                            try:
                                summary_path = self._capture_session_summary(sid, s, idle_cfg)
                                _dlog("idle", f"closing sid={sid} summary={summary_path}")
                                idle_seconds = int(now - getattr(
                                    s, "_last_user_activity_time",
                                    getattr(s, "_last_activity_time", now),
                                ))
                                self.close_session(
                                    sid,
                                    reason="idle_reaper",
                                    handoff=True,
                                    summary_path=summary_path,
                                    idle_seconds=idle_seconds,
                                )
                            except Exception as e:
                                _dlog("idle", f"close failed sid={sid}: {e}")
                        continue
                    idle_for = now - getattr(s, "_last_user_activity_time", getattr(s, "_last_activity_time", now))
                    if idle_for < idle_cfg.get("idle_sec", 1800.0):
                        continue
                    label = getattr(s, "_custom_label", None) or sid
                    s._idle_reap_state = "summarizing"
                    s._idle_summary_requested_at = now
                    s._idle_close_after = now + idle_cfg.get("summary_grace_sec", 120.0)
                    _dlog("idle", f"summary request sid={sid} idle_sec={int(idle_for)}")
                    s.write(self._idle_summary_prompt(label, int(idle_for), idle_cfg) + "\r", user_activity=False)
                time.sleep(review_sec)

        threading.Thread(target=_loop, daemon=True).start()

    @staticmethod
    def _manifest_entries(cfg: dict) -> list[dict]:
        manifest = cfg.get("session_manifest") or []
        if manifest:
            return [e for e in manifest if e.get("sid") and e.get("cmd")]
        # Backward compatibility with the old Windows/no-tmux soft list.
        return [e for e in (cfg.get("session_list") or []) if e.get("sid") and e.get("cmd")]

    @staticmethod
    def _resolve_tmux_sid(info: dict, cfg: dict) -> str:
        """Resolve stable sid for a tmux session whose display name may change."""
        if info.get("sid"):
            return str(info["sid"])
        name = info.get("name", "")
        suffix = name[len(TMUX_PREFIX):] if name.startswith(TMUX_PREFIX) else name
        if re.fullmatch(r"s\d+", suffix or ""):
            return suffix
        for entry in Api._manifest_entries(cfg):
            if entry.get("tmux_name") == name:
                return str(entry.get("sid"))
        saved_labels = cfg.get("session_labels", {}) or {}
        for sid, label in saved_labels.items():
            if _slugify(str(label)) == suffix:
                return str(sid)
        return suffix or name

    _CODEX_ROLLOUT_RE = re.compile(r"rollout-.*?-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
                                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$")

    def _codex_session_id(self, sid: str, s) -> str:
        """這個 codex 分頁對應的 rollout session uuid（'' = 認不出來）。

        macOS／Linux 走 agent_status.resolve_transcript：codex 會一直持有
        rollout 的 fd，lsof 直接命中，最準。
        Windows 兩者都沒有（沒 lsof、沒 tmux pane），agent_status 的 fallback
        是「全域最新的一份 rollout」——多個 codex 分頁會全部指到同一份。這裡
        改用時序＋認領：取「這個分頁 spawn 之後才建立、且還沒被別的分頁認領」
        的最早一份。認領表就是各 session 已經記住的 id。
        """
        try:
            cached = getattr(s, "_codex_sid", "")
            if cached:
                return cached
            path = ""
            try:
                path = agent_status.resolve_transcript({
                    "cmd": getattr(s, "cmd", ""),
                    "cwd": getattr(s, "cwd", "~"),
                    "tmux_name": getattr(s, "_tmux_name", None),
                }) or ""
            except Exception:
                path = ""
            if IS_WIN or not path:
                taken = {getattr(o, "_codex_sid", "") for k, o in self.sessions.items()
                         if k != sid}
                spawn = float(getattr(s, "_spawn_ts", 0.0) or 0.0)
                best = None
                for f in glob.glob(os.path.join(
                        os.path.expanduser("~/.codex/sessions"),
                        "*", "*", "*", "rollout-*.jsonl")):
                    m = self._CODEX_ROLLOUT_RE.search(f)
                    if not m or m.group(1) in taken:
                        continue
                    try:
                        born = os.path.getmtime(f)
                    except OSError:
                        continue
                    # 給 5 秒寬容：rollout 可能在 spawn 之前一瞬間就建好
                    if born < spawn - 5:
                        continue
                    if best is None or born < best[0]:
                        best = (born, m.group(1))
                if best:
                    s._codex_sid = best[1]
                    return best[1]
                return ""
            m = self._CODEX_ROLLOUT_RE.search(path)
            if m:
                s._codex_sid = m.group(1)
                return m.group(1)
        except Exception:
            _swallow(f"_codex_session_id:{sid}")
        return ""

    @staticmethod
    def _codex_rollout_exists(csid: str) -> bool:
        """這個 codex session uuid 在磁碟上還找得到 rollout 嗎。"""
        if not csid:
            return False
        try:
            return bool(glob.glob(os.path.join(
                os.path.expanduser("~/.codex/sessions"),
                "*", "*", "*", f"rollout-*-{csid}.jsonl")))
        except Exception:
            return False

    @staticmethod
    def _claude_transcript_exists(csid: str) -> bool:
        """這個 session uuid 在磁碟上還找得到 transcript 嗎。

        不看 manifest 存的 `transcript_path`——那是 hook 回報**當時**的路徑，
        `/clear` 之後就換檔了（重開機演練時，14 個分頁裡有 5 個是這種：uuid
        還在、舊路徑已消失）。uuid 才是 `--resume` 真正吃的東西，直接在
        ~/.claude/projects 底下找它，不必猜 cwd slug。"""
        if not csid:
            return False
        try:
            import glob as _glob
            root = os.path.expanduser("~/.claude/projects")
            return bool(_glob.glob(os.path.join(root, "*", f"{csid}.jsonl")))
        except Exception:
            return False

    @staticmethod
    def _cmd_with_resume(cmd: str, csid: str) -> str:
        """把 claude 分頁的啟動指令換成 `--resume <當前 session uuid>`。

        機器重新開機後 tmux server 不在了，分頁得重新 spawn。manifest 存的 cmd
        是「當初怎麼開的」——沒有 --resume 就是開一個**全新對話**，所有分頁的
        上下文一次丟光。就算 cmd 裡本來就有 --resume，那個 uuid 也是**啟動當時**
        的：`/clear` 會輪替 uuid、resume 也常 fork 出新檔（實例：某分頁 cmd 裡是
        八月的 uuid，當前其實已經是另一個）。所以一律以 hook 回報、落地在
        manifest 的 uuid 為準，並把舊的 --resume / --session-id 拿掉。

        只處理 claude；codex／agy／一般 shell 各有自己的續接方式，不碰。
        """
        if not cmd or not csid:
            return cmd
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            return cmd
        if not tokens:
            return cmd
        exe = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        exe = exe[:-4] if exe.endswith((".cmd", ".bat", ".exe")) else exe

        if exe == "claude":
            out, skip = [tokens[0], "--resume", csid], False
            for t in tokens[1:]:
                if skip:
                    skip = False
                    continue
                if t in ("--resume", "--session-id"):
                    skip = True
                    continue
                if t.startswith("--resume=") or t.startswith("--session-id="):
                    continue
                out.append(t)
            return shlex.join(out)

        if exe == "codex":
            # `codex resume <SESSION_ID>` —— resume 是子指令，必須緊接在
            # 執行檔後面。舊指令若已經是 `codex resume …`（帶 id、帶 --last
            # 或什麼都沒帶的 picker 形式），先把那段拆掉再重組，否則會變成
            # `codex resume resume …`。
            rest = tokens[1:]
            if rest and rest[0] == "resume":
                rest = rest[1:]
                if rest and not rest[0].startswith("-"):
                    rest = rest[1:]          # 舊的 session id
                rest = [t for t in rest if t != "--last"]
            return shlex.join([tokens[0], "resume", csid, *rest])

        return cmd          # agy / 一般 shell 各有自己的續接方式，不碰

    @staticmethod
    def _restore_transcript_hint(session, entry: dict):
        """把 manifest 存下來的 transcript hint 接回 Session。

        少了這一步，重啟後的分頁只剩 cmd 裡的 --resume uuid 可推——那是「啟動
        時那一份」，不是「現在在寫的那一份」（/clear 會輪替 uuid，resume 也常
        fork 出新檔），模型 badge 會停在舊檔最後一筆的模型。路徑不存在就不接，
        讓偵測照原本的優先序往下走。"""
        try:
            tp = str(entry.get("transcript_path") or "").strip()
            if tp and os.path.exists(tp):
                session._hook_transcript_path = tp
            csid = str(entry.get("claude_session_id") or "").strip()
            if csid and not getattr(session, "session_id", None):
                session.session_id = csid
        except Exception:
            _swallow(f"restore_transcript_hint:{getattr(session, 'sid', '?')}")

    def restore_tmux_sessions(self, cols: int = 80, rows: int = 24) -> str:
        """Restore orphaned sessions on startup.

        Two paths:
          - tmux available: detect sf_* tmux sessions and reattach (Linux/macOS)
          - no tmux: read config.session_list and recreate as fresh PTYs.
            This is "soft persistence" — labels and command list are kept,
            but scrollback is gone. Used on Windows.
        """
        _dlog("lifecycle", f"restore_tmux_sessions called cols={cols} rows={rows}")
        cfg = load_config()
        if ACCOUNT_MANAGER.ensure(cfg):
            save_config(cfg)
        default_account_refs = ACCOUNT_MANAGER.session_refs(cfg)
        saved_labels = cfg.get("session_labels", {})
        bridge_disabled = set(cfg.get("bridge_disabled_sessions", []))
        glasses_allowed = set(cfg.get("glasses_allowed_sessions", []) or [])
        manifest = self._manifest_entries(cfg)
        manifest_by_sid = {str(e.get("sid")): e for e in manifest}
        manifest_by_tmux = {str(e.get("tmux_name")): e for e in manifest if e.get("tmux_name")}
        saved_order = cfg.get("session_order") or [str(e.get("sid")) for e in sorted(manifest, key=lambda x: x.get("order", 9999))]
        order_index = {sid: i for i, sid in enumerate(saved_order)}
        restored = []

        if not IS_WIN and _has_tmux():
            existing = _list_tmux_sessions()
            _dlog("lifecycle", f"  found tmux sessions: {[e['name'] for e in existing]}")
            if existing:
                existing = sorted(
                    existing,
                    key=lambda info: order_index.get(self._resolve_tmux_sid(info, cfg), 9999),
                )
            for info in existing:
                tmux_name = info["name"]
                sid = self._resolve_tmux_sid(info, cfg)
                entry = manifest_by_sid.get(sid) or manifest_by_tmux.get(tmux_name) or {}
                cmd = _canonical_cmd(info["cmd"] or entry.get("cmd") or "bash")
                account_refs = dict(entry.get("account_refs") or {})
                for provider in account_manager.PROVIDERS:
                    account_refs.setdefault(
                        provider,
                        _tmux_get_env(tmux_name, f"SF_ACCOUNT_{provider.upper()}")
                        or default_account_refs.get(provider),
                    )
                if sid in self.sessions:
                    continue  # already attached
                self._counter = max(self._counter, int(sid[1:]) if sid[1:].isdigit() else 0)
                session = Session(sid, cmd, cols, rows,
                                  on_data=self._output_event.set,
                                  tmux_name=tmux_name,
                                  account_refs=account_refs)
                self.sessions[sid] = session
                # Restore bridge enabled/disabled state from config
                session._bridge_enabled = bool(entry.get("bridge_enabled", sid not in bridge_disabled))
                session._glasses_enabled = bool(entry.get("glasses_enabled", sid in glasses_allowed))
                session._init_pending = False
                # 這裡**不要**關掉 _startup_trust_pending。開機／重開 app 時
                # tmux 裡那些 `claude --resume` 是剛長出來的行程，一樣會停在
                # 資料夾信任對話框上，而且游標預設在 No, exit——沒人來答就是
                # 整排分頁全卡死（2026-09-04 實例：14 個分頁全中）。
                # Session.__init__ 已經依「受信任 cwd + AI 指令」算好該不該
                # 接手，照它的判斷走，並把 watcher 掛起來。
                session._slug_pending = False
                session._lifecycle_source = entry.get("lifecycle_source", "")
                session._lifecycle_handoff = bool(entry.get("lifecycle_handoff", False))
                self._restore_transcript_hint(session, entry)
                self._start_startup_trust_watcher(sid, session)
                # Restore custom label
                label = entry.get("label") or saved_labels.get(sid)
                if label:
                    session._custom_label = label
                restored.append({"sid": sid, "cmd": cmd})
            if existing:
                self._persist_session_manifest(saved_order)
                return json.dumps(restored)

        # Disk-backed fallback: recreate tabs fresh after a machine reboot
        # (tmux server gone) or on Windows/no-tmux systems.
        soft_list = manifest
        _dlog("lifecycle", f"  soft restore from config: {[s.get('sid') for s in soft_list]}")
        soft_list = sorted(soft_list, key=lambda e: order_index.get(str(e.get("sid")), e.get("order", 9999)))
        for entry in soft_list:
            sid = entry.get("sid", "")
            cmd = _canonical_cmd(entry.get("cmd", ""))
            if not sid or not cmd or sid in self.sessions:
                continue
            # 這條路是「tmux 沒了」才走的（機器重開機）。接回原本的對話，
            # 否則每個分頁都會是一個空白的新 session。transcript 檔不在就
            # 不接——與其 resume 失敗讓分頁開不起來，不如開新的。
            if _worker_is_codex(cmd):
                csid = str(entry.get("codex_session_id") or "").strip()
                found = self._codex_rollout_exists(csid)
            else:
                csid = str(entry.get("claude_session_id") or "").strip()
                found = self._claude_transcript_exists(csid)
            if csid:
                if found:
                    resumed = self._cmd_with_resume(cmd, csid)
                    if resumed != cmd:
                        _dlog("lifecycle", f"  {sid} 接回對話 resume {csid[:8]}")
                        cmd = resumed
                else:
                    _dlog("lifecycle", f"  {sid} 有 uuid 但找不到記錄檔，開新對話")
            try:
                self._counter = max(self._counter, int(sid[1:]) if sid[1:].isdigit() else 0)
                tmux_name = entry.get("tmux_name") or None
                account_refs = dict(entry.get("account_refs") or default_account_refs)
                session = Session(sid, cmd, cols, rows,
                                  on_data=self._output_event.set,
                                  tmux_name=tmux_name,
                                  account_refs=account_refs)
                self.sessions[sid] = session
                session._bridge_enabled = bool(entry.get("bridge_enabled", sid not in bridge_disabled))
                session._glasses_enabled = bool(entry.get("glasses_enabled", sid in glasses_allowed))
                session._init_pending = False
                # soft restore 是**重新 spawn** 一個行程（機器重開、tmux 沒
                # 了），信任對話框百分之百會出現，更不能關掉 watcher。
                session._slug_pending = False
                session._lifecycle_source = entry.get("lifecycle_source", "")
                session._lifecycle_handoff = bool(entry.get("lifecycle_handoff", False))
                self._restore_transcript_hint(session, entry)
                self._start_startup_trust_watcher(sid, session)
                label = entry.get("label") or saved_labels.get(sid)
                if label:
                    session._custom_label = label
                restored.append({"sid": sid, "cmd": cmd})
            except Exception as e:
                _dlog("lifecycle", f"  soft restore failed for {sid}: {e}")
        if restored:
            self._persist_session_manifest(saved_order)
        return json.dumps(restored)

    def _start_output_pusher(self):
        """Background threads that push PTY output to frontend via evaluate_js.
        Event-driven: reader threads signal _output_event so pusher wakes instantly."""
        if self._pusher_started:
            return
        self._pusher_started = True
        pending = {}  # sid -> str
        bg_last_push = {}  # sid -> 上次 push 時間（背景 tab 節流用）
        BG_PUSH_INTERVAL = 0.25  # 背景(非當前顯示)tab 最多 4Hz push webview；當前 tab 全速
        MAX_PUSH_CHARS = 65536  # 單次推 webview 的字元上限，防爆量輸出(大檔/base64/長log)一次 evaluate_js 灌爆主執行緒凍住 UI

        def pusher():
            while True:
                self._output_event.clear()
                pushed = False
                throttled = False  # 有背景 tab 的 pending 還沒到節流窗口
                now = time.time()
                active = self._active_sid
                for sid, s in list(self.sessions.items()):
                    data = s.read()
                    if data:
                        self._auto_accept_startup_trust_prompt(sid, s)
                        if (self.bridge or self.line_bridge) and getattr(s, '_bridge_enabled', True):
                            self._bridge_queue.put_nowait((sid, data))
                        # Frame Link：把原始輸出餵給正在被遠端串流的分頁（無縫
                        # 遠端畫面）。feed_output 只緩衝有人在看的分頁，平時零成本。
                        fl = getattr(self, "frame_link", None)
                        if fl is not None:
                            try:
                                fl.feed_output(sid, data)
                            except Exception:
                                pass
                        pending[sid] = pending.get(sid, "") + data
                    chunk = pending.get(sid)
                    if chunk and self._window:
                        # 背景 tab(非當前顯示)節流：未到 4Hz 窗口就先留著 pending 不 push，
                        # 切回該 tab 時 set_active_tab 會喚醒立刻刷出 → 不掉字、不卡主緒。
                        # active 為空(尚未設定)時視同全速，退化為原行為。
                        is_active = (not active) or (sid == active)
                        if not is_active and (now - bg_last_push.get(sid, 0.0)) < BG_PUSH_INTERVAL:
                            throttled = True
                            continue
                        # 防 webview 被爆量輸出灌爆主執行緒：單次推送超過上限只送尾端，
                        # 從換行邊界切避免截斷 ANSI escape，前面標一行說明略過量。
                        if len(chunk) > MAX_PUSH_CHARS:
                            dropped = len(chunk) - MAX_PUSH_CHARS
                            cut = chunk.find("\n", dropped)
                            chunk = chunk[cut + 1:] if cut != -1 else chunk[-MAX_PUSH_CHARS:]
                            chunk = f"\x1b[2m…[已略過 {dropped} 字元的大量輸出]…\x1b[0m\r\n" + chunk
                        escaped = json.dumps(chunk)
                        try:
                            # evaluate_js 會等主執行緒排程＋等 JS 跑完（xterm.write
                            # 連渲染一起算）。UI 凍結時就是卡在這裡，但以前沒有任何
                            # 數據——凍結是間歇性的，事後 sample 抓不到。超過 400ms
                            # 的推送留一筆，才知道是哪個分頁、多大的 chunk 造成的。
                            _t0 = time.time()
                            self._window.evaluate_js(f'_pushOutput("{sid}",{escaped})')
                            _dt = time.time() - _t0
                            if _dt > 0.4:
                                _dlog("perf", f"evaluate_js 慢 {int(_dt * 1000)}ms "
                                              f"sid={sid} chunk={len(chunk)}字元 "
                                              f"tabs={len(self.sessions)}")
                            pending.pop(sid, None)
                            bg_last_push[sid] = now
                            pushed = True
                        except Exception:
                            _swallow("_start_output_pusher.pusher:2072")
                # Event-driven: reader threads set _output_event on every new
                # chunk, so the idle wait is just a safety net — not a polling
                # interval. 0.5s idle floor cuts the steady-state from 66 to 2
                # wakes/s (each wake iterates every session under its lock).
                # 串流中(剛 push 過)用 5ms 把殘餘 pending 排乾；有節流中的背景
                # pending 用 0.1s 醒來等下個 4Hz 窗口。
                self._output_event.wait(0.005 if pushed else (0.1 if throttled else 0.5))

        def bridge_feeder():
            while True:
                sid, data = self._bridge_queue.get()
                if self.bridge:
                    self.bridge.feed_output(sid, data)
                if self.line_bridge:
                    self.line_bridge.feed_output(sid, data)

        threading.Thread(target=pusher, daemon=True).start()
        threading.Thread(target=bridge_feeder, daemon=True).start()

    @staticmethod
    def _auto_status_enabled() -> bool:
        # Feature flag — default ON; set settings.auto_status_detect=false to
        # disable and fall back to the browser-side heuristic + [[SF:]] markers.
        try:
            settings = load_config().get("settings", {}) or {}
            return settings.get("auto_status_detect", True) is not False
        except Exception:
            return True

    # ── Hook-driven agent status (exact, event-based) ────────────────────
    _HOOK_TTL = 1800.0   # hook state stays authoritative this long after the last event
    _HOOK_EVENTS = ("UserPromptSubmit", "PreToolUse", "Stop", "Notification", "StopFailure")
    _CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

    @staticmethod
    def _hook_state_for(event: str, notification_type: str = "", message: str = "") -> str | None:
        """Map a Claude Code hook event to a feed state; None = no transition."""
        if event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure"):
            return "working"
        if event == "Stop":
            return "done"
        if event == "StopFailure":
            return "stuck"
        if event == "SessionEnd":
            return "done"
        if event == "Notification":
            blob = f"{notification_type} {message}".lower()
            if "permission" in blob:
                return "decision"
            if "idle" in blob or "waiting for your input" in blob:
                return "done"
        return None

    def _on_agent_event(self, args: dict) -> dict:
        """sfctl cmd `agent_event` — fired by sf_agent_hook.py (fire-and-forget)."""
        sid = str(args.get("sid") or "").strip()
        if not sid:
            return {"success": False, "message": "sid required"}
        event = str(args.get("event") or "")
        # hook 事件帶的 session_id / transcript_path 是「這個分頁現在寫哪個
        # transcript」的唯一即時真相——/clear 會在同一個 claude process 裡
        # 輪替 uuid，spawn 時的 --session-id 與 nearest-birth 都會釘在舊檔
        # （2026-08-06 tab13 badge 顯示 Opus 4.6、實際 Opus 5 的根因）。
        # 存在 state gate 之前：被 ignore 的事件同樣帶有效路徑。
        s = self.sessions.get(sid)
        if s is not None:
            tp = str(args.get("transcript_path") or "").strip()
            csid = str(args.get("session_id") or "").strip()
            changed = False
            if tp and getattr(s, "_hook_transcript_path", None) != tp:
                s._hook_transcript_path = tp
                changed = True
            if csid and getattr(s, "session_id", None) != csid:
                s.session_id = csid
                changed = True
            # 只有真的換檔才落地——hook 每個 PreToolUse 都會進來，無條件
            # persist 等於把整份 config 重寫成高頻寫入。
            if changed:
                self._persist_session_manifest()
        state = self._hook_state_for(
            event,
            str(args.get("notification_type") or ""),
            str(args.get("message") or ""))
        if not state:
            return {"success": True, "message": f"ignored {event}"}
        now = time.time()
        prev = self._hook_events.get(sid)
        since = prev["since"] if (prev and prev["state"] == state) else now
        tool = str(args.get("tool_name") or "")
        self._hook_events[sid] = {
            "state": state, "ts": now, "since": since,
            "tool": tool if state == "working" else "",
            "event": event,
        }
        # Invalidate the gated cache so the next monitor pass (≤0.6s) refreshes
        # transcript-side details alongside the new exact state.
        self._status_cache.pop(sid, None)
        _dlog("hookstat", f"sid={sid} {event} -> {state}")
        return {"success": True, "message": f"{sid} -> {state}"}

    @staticmethod
    def _apply_hook_state(result: dict, hk: dict, now: float) -> dict:
        """Overlay the hook-derived state on a heuristic result. Detail fields
        (task/narration from the transcript) are kept — only the state verdict
        and its dependents are replaced when they disagree."""
        state = hk["state"]
        if result.get("state") == state:
            return result
        out = dict(result)
        out["state"] = state
        out["dot"] = agent_status.DOT.get(state, "")
        out["elapsed"] = int(now - hk.get("since", now))
        if state == "working":
            if hk.get("tool"):
                out["summary"] = f"Running {hk['tool']}"
        elif state == "decision":
            out["summary"] = "等待權限決策"
        elif state == "stuck":
            out["summary"] = "回合異常結束"
        return out

    def _sf_hook_command(self) -> str:
        return f'python3 "{APP_DIR / "sf_agent_hook.py"}"'

    def get_status_hooks_info(self) -> str:
        """Settings UI: are the ShellFrame status hooks installed in ~/.claude/settings.json?"""
        installed = False
        try:
            p = self._CLAUDE_SETTINGS_PATH
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            hooks = cfg.get("hooks") or {}
            installed = all(
                any("sf_agent_hook.py" in json.dumps(g) for g in (hooks.get(ev) or []))
                for ev in self._HOOK_EVENTS)
        except Exception:
            _swallow("Api.get_status_hooks_info:2188")
        return json.dumps({"installed": installed,
                           "settings_path": str(self._CLAUDE_SETTINGS_PATH)})

    def set_status_hooks_enabled(self, enabled: bool) -> str:
        """Install/remove the status hook entries. Merge is surgical: only
        groups whose command references sf_agent_hook.py are touched, every
        other hook in the user's settings.json survives byte-for-byte."""
        p = self._CLAUDE_SETTINGS_PATH
        try:
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception as e:
            return json.dumps({"installed": False, "error": f"settings.json unreadable: {e}"})
        hooks = cfg.setdefault("hooks", {})
        if enabled:
            for ev in self._HOOK_EVENTS:
                groups = hooks.setdefault(ev, [])
                if any("sf_agent_hook.py" in json.dumps(g) for g in groups):
                    continue
                groups.append({"matcher": "", "hooks": [{
                    "type": "command", "command": self._sf_hook_command(),
                    "async": True, "timeout": 10}]})
        else:
            for ev in list(hooks.keys()):
                kept = []
                for g in hooks.get(ev) or []:
                    inner = [h for h in (g.get("hooks") or [])
                             if "sf_agent_hook.py" not in str(h.get("command", ""))]
                    if inner or not g.get("hooks"):
                        if g.get("hooks"):
                            g = dict(g)
                            g["hooks"] = inner
                        kept.append(g)
                if kept:
                    hooks[ev] = kept
                else:
                    hooks.pop(ev, None)
            if not hooks:
                cfg.pop("hooks", None)
            self._hook_events.clear()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(p) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception as e:
            return json.dumps({"installed": False, "error": f"write failed: {e}"})
        return self.get_status_hooks_info()

    def refresh_agent_status(self, sid: str = "") -> str:
        """把狀態快取與 hook 狀態清掉，強制下一輪從畫面／transcript 重算。

        中斷對話（Ctrl+C／Esc）時 Claude Code 不一定會發 Stop hook，
        `_hook_events` 就卡在 working；而 status monitor 的 idle gating 又因為
        PTY 不再輸出而跳過重算——燈號於是一直停在「執行中」（Howard 2026-09-03
        回報）。清掉之後 heuristic 會重新判斷：真的還在跑就會再標回 working，
        所以清掉是安全的。

        sid 空字串＝全部分頁。"""
        try:
            targets = [sid] if sid else list(self.sessions.keys())
            for t in targets:
                self._status_cache.pop(t, None)
                self._hook_events.pop(t, None)
            _dlog("status", f"強制重算狀態 targets={len(targets)}"
                            f"{' sid=' + sid if sid else ' (全部)'}")
            return json.dumps({"refreshed": len(targets)})
        except Exception as e:
            _swallow(f"refresh_agent_status:{sid}")
            return json.dumps({"refreshed": 0, "error": str(e)})

    def _start_status_monitor(self):
        """Background thread: every ~600ms compute each tab's agent status from
        its transcript/rollout log (+ screen wording) and push to the webview.
        Fully isolated from the PTY/output path — any failure just yields
        'unknown' and the browser heuristic keeps working."""
        if self._status_started:
            return
        self._status_started = True

        def monitor():
            # (hook override below) Industry survey 2026-06: every OSS agent
            # manager (claude-squad SHA256 pane diff, agentapi 2s screen
            # stability, ccmanager UI-string regex) scrapes the screen and is
            # fragile by design. Claude Code's own hooks emit the exact
            # transitions instead — see _on_agent_event / sf_agent_hook.py.
            # Heuristic detection below stays as the fallback (Codex, plain
            # shells, hooks not installed, tabs opened before install).
            # Idle gating: a tab whose PTY printed nothing since the last pass
            # cannot have changed state — transcript and screen only move when
            # the program outputs, and working tabs always stream spinner/timer
            # frames. Reuse the cached result and just tick `elapsed`. With 10
            # idle tabs this removes ~17 tmux capture-pane forks plus ~17
            # 256KB transcript tail-reads PER SECOND from the steady state.
            # Wall-clock-dependent transitions (pending-tool age guard,
            # debounce) still land within FORCE_REFRESH.
            FORCE_REFRESH = 15.0   # full recompute at least this often per tab
            PUSH_HEARTBEAT = 5.0   # elapsed-only changes push at most this often
            # FORCE_REFRESH 只在「有輸出過」的分頁上重算，救不了中斷後就完全
            # 安靜的分頁——hook 沒發 Stop、PTY 也不再動，燈號會一直停在
            # working。每 5 分鐘連 hook 狀態一起清掉重判一次（Howard
            # 2026-09-03：「有些情況我會中斷對話，這時候燈號就不會變動了」）。
            HOOK_RESET_INTERVAL = 300.0
            cache = self._status_cache  # sid -> {out_ts, computed_at, since_ts, result}
            last_push = {"key": None, "at": 0.0}
            last_hook_reset = time.time()
            while True:
                try:
                    if not self._auto_status_enabled() or not self._window:
                        time.sleep(1.0)
                        continue
                    now = time.time()
                    for stale in [k for k in cache if k not in self.sessions]:
                        cache.pop(stale, None)
                    for stale in [k for k in self._hook_events if k not in self.sessions]:
                        self._hook_events.pop(stale, None)
                    if now - last_hook_reset >= HOOK_RESET_INTERVAL:
                        last_hook_reset = now
                        cache.clear()
                        self._hook_events.clear()
                        _dlog("status", "五分鐘定期重算：清掉 hook 與狀態快取")
                    out = {}
                    for sid, s in list(self.sessions.items()):
                        try:
                            out_ts = getattr(s, "_last_output_activity_time", 0.0)
                            c = cache.get(sid)
                            if (c and c["out_ts"] == out_ts
                                    and now - c["computed_at"] < FORCE_REFRESH):
                                result = dict(c["result"])
                                result["elapsed"] = int(now - c["since_ts"])
                            else:
                                worker = {
                                    "cmd": getattr(s, "cmd", ""),
                                    "cwd": getattr(s, "cwd", "~"),
                                    "tmux_name": getattr(s, "_tmux_name", None),
                                    "session_id": getattr(s, "session_id", None),
                                    "transcript_hint": getattr(s, "_hook_transcript_path", None),
                                }
                                # Screen wording must come from the CURRENT rendered
                                # screen. The _recent ring buffer is a byte-stream
                                # history — a /model or feedback menu that scrolled
                                # away stays in it and false-triggers "decision".
                                screen_tail = ""
                                tn = getattr(s, "_tmux_name", None)
                                if tn:
                                    try:
                                        r = subprocess.run(
                                            ["tmux", "capture-pane", "-t", tn, "-p"],
                                            capture_output=True, text=True, timeout=2)
                                        if r.returncode == 0:
                                            screen_tail = "\n".join(
                                                r.stdout.rstrip().splitlines()[-20:])
                                    except Exception:
                                        _swallow("_start_status_monitor.monitor:2308")
                                if not screen_tail:
                                    screen_tail = bytes(getattr(s, "_recent", b"")).decode(
                                        "utf-8", errors="replace")[-4000:]
                                st = self._status_tracker.status_for(
                                    sid, worker, screen_tail=screen_tail)
                                result = {"state": st.get("state"),
                                          "dot": st.get("dot"),
                                          "summary": st.get("summary"),
                                          "task": st.get("task", ""),
                                          "elapsed": st.get("elapsed", 0),
                                          "activity": st.get("activity") or {},
                                          "loop": st.get("loop"),
                                          "model": st.get("model")}
                                cache[sid] = {
                                    "out_ts": out_ts,
                                    "computed_at": now,
                                    "since_ts": now - result["elapsed"],
                                    "result": result,
                                }
                            # Hook events (Claude Code hooks → sf_agent_hook.py)
                            # are exact turn/permission transitions — while
                            # fresh they override the screen/transcript guess.
                            hk = self._hook_events.get(sid)
                            if hk and now - hk["ts"] <= self._HOOK_TTL:
                                result = self._apply_hook_state(result, hk, now)
                            # 排程面板用：標出被 scheduler/auto 啟動的頁籤
                            result["lifecycle_source"] = getattr(s, "_lifecycle_source", "")
                            out[sid] = result
                        except Exception:
                            out[sid] = {"state": "unknown", "dot": "",
                                        "summary": "", "activity": {}}
                            cache.pop(sid, None)
                    if out and self._window:
                        # Push only when something besides `elapsed` changed,
                        # or on a slow heartbeat so elapsed keeps ticking —
                        # idle fleets stop waking the webview 1.7×/s for
                        # identical payloads.
                        key = json.dumps(
                            {k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                             for k, v in out.items()}, sort_keys=True)
                        if key != last_push["key"] or now - last_push["at"] >= PUSH_HEARTBEAT:
                            payload = json.dumps(out)
                            try:
                                self._window.evaluate_js(
                                    f'window.__sfAgentStatus && window.__sfAgentStatus({payload})')
                                last_push["key"] = key
                                last_push["at"] = now
                            except Exception:
                                _swallow("_start_status_monitor.monitor:2357")
                except Exception:
                    _swallow("_start_status_monitor.monitor:2359")
                time.sleep(0.6)

        threading.Thread(target=monitor, daemon=True).start()

    def get_config(self) -> str:
        return json.dumps(load_config())

    @staticmethod
    def _board_enabled() -> bool:
        return bool((load_config().get("settings", {}) or {}).get("experimental_board", False))

    def board_list(self) -> str:
        """Return {enabled, tasks} for the experimental task board."""
        return json.dumps({"enabled": self._board_enabled(), "tasks": board.list_tasks()})

    def board_add(self, title: str, assignee: str = "unassigned",
                  status: str = "todo", difficulty: str = "medium", notes: str = "") -> str:
        try:
            task = board.add_task(title, assignee=assignee, status=status,
                                  difficulty=difficulty, notes=notes)
            return json.dumps({"success": True, "task": task})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def board_update(self, task_id: str, fields_json: str = "{}") -> str:
        try:
            fields = json.loads(fields_json) if fields_json else {}
            task = board.update_task(task_id, **fields)
            if task is None:
                return json.dumps({"success": False, "message": "task not found"})
            return json.dumps({"success": True, "task": task})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def board_remove(self, task_id: str) -> str:
        ok = board.remove_task(task_id)
        return json.dumps({"success": ok})









    def get_saved_bridge(self) -> str:
        """Return saved bridge config (for restoring on startup)."""
        cfg = load_config()
        bridge = cfg.get("bridge")
        if bridge:
            # Mask token for display (show last 6 chars)
            masked = bridge.copy()
            t = masked.get("bot_token", "")
            masked["bot_token_masked"] = "..." + t[-6:] if len(t) > 6 else t
            return json.dumps(masked)
        return json.dumps(None)

    def get_saved_line_bridge(self) -> str:
        """Return saved LINE bridge config with secrets masked for display."""
        cfg = load_config()
        line_cfg = cfg.get("line_bridge")
        if not line_cfg:
            return json.dumps(None)
        masked = line_cfg.copy()
        token = masked.get("channel_access_token", "")
        secret = masked.get("channel_secret", "")
        forward_secret = masked.get("forward_secret", "")
        masked["channel_access_token_masked"] = "..." + token[-6:] if len(token) > 6 else token
        masked["channel_secret_masked"] = "..." + secret[-6:] if len(secret) > 6 else secret
        masked["forward_secret_masked"] = "..." + forward_secret[-6:] if len(forward_secret) > 6 else forward_secret
        return json.dumps(masked)

    def save_preset(self, name: str, cmd: str, icon: str) -> str:
        cmd = _normalize_dashes(cmd)
        cfg = load_config()
        # Update existing or add new
        for p in cfg["presets"]:
            if p["name"] == name:
                p["cmd"] = cmd
                p["icon"] = icon
                save_config(cfg)
                return json.dumps(cfg)
        cfg["presets"].append({"name": name, "cmd": cmd, "icon": icon})
        save_config(cfg)
        return json.dumps(cfg)

    def save_settings(self, settings_json: str) -> str:
        cfg = load_config()
        old_hotkey = (cfg.get("settings", {}) or {}).get("global_hotkey_enabled", True)
        cfg["settings"] = json.loads(settings_json)
        save_config(cfg)
        # Re-register the global hotkey if the toggle changed, so users
        # don't need to restart for the setting to take effect.
        new_hotkey = cfg["settings"].get("global_hotkey_enabled", True)
        if old_hotkey != new_hotkey:
            try:
                _register_global_hotkey()
            except Exception:
                _swallow("Api.save_settings:2626")
        return json.dumps(cfg)

    def save_idle_reaper(self, idle_json: str) -> str:
        cfg = load_config()
        _ensure_idle_reaper_defaults(cfg)
        current = cfg.get("idle_reaper", {}) or {}
        incoming = json.loads(idle_json) if idle_json else {}

        def _bool(key: str, default: bool) -> bool:
            value = incoming.get(key, default)
            return bool(value)

        def _seconds(key: str, default: float, minimum: float) -> int:
            try:
                value = float(incoming.get(key, default))
            except (TypeError, ValueError):
                value = default
            return int(max(minimum, value))

        current["enabled"] = _bool("enabled", current.get("enabled", False))
        current["idle_sec"] = _seconds("idle_sec", current.get("idle_sec", 1800), 30)
        current["summary_grace_sec"] = _seconds(
            "summary_grace_sec",
            current.get("summary_grace_sec", 120),
            10,
        )
        current["handoff_to_main"] = _bool(
            "handoff_to_main",
            current.get("handoff_to_main", True),
        )
        cfg["idle_reaper"] = current
        save_config(cfg)
        return json.dumps(cfg)

    def delete_preset(self, name: str) -> str:
        cfg = load_config()
        cfg["presets"] = [p for p in cfg["presets"] if p["name"] != name]
        save_config(cfg)
        return json.dumps(cfg)

    def reorder_presets(self, order_json: str) -> str:
        """Reorder presets by name list. E.g. ["Bash","Claude Code","Codex"]."""
        cfg = load_config()
        order = json.loads(order_json) if order_json else []
        by_name = {p["name"]: p for p in cfg.get("presets", [])}
        reordered = [by_name[n] for n in order if n in by_name]
        # Append any presets not in the order list (safety)
        seen = set(order)
        for p in cfg.get("presets", []):
            if p["name"] not in seen:
                reordered.append(p)
        cfg["presets"] = reordered
        save_config(cfg)
        return json.dumps(cfg)

    def list_sessions(self) -> str:
        """Return list of active sessions (for reconnect after page reload)."""
        result = []
        for sid, s in self.sessions.items():
            if s.alive:
                result.append({"sid": sid, "cmd": s.cmd, "alive": True,
                               "bridge_enabled": getattr(s, '_bridge_enabled', True),
                               "glasses_enabled": getattr(s, '_glasses_enabled', False),
                               "provider": _session_provider(s.cmd),
                               "label": getattr(s, '_custom_label', None)})
        return json.dumps(result)

    def new_session(self, cmd: str, cols: int, rows: int, source: str = "manual",
                    handoff: bool = False, inherit_accounts: bool = True) -> str:
        cmd = _canonical_cmd(cmd)
        cfg = load_config()
        if ACCOUNT_MANAGER.ensure(cfg):
            save_config(cfg)
        account_refs = ACCOUNT_MANAGER.session_refs(cfg) if inherit_accounts else {
            provider: None for provider in account_manager.PROVIDERS
        }
        self._counter += 1
        sid = f"s{self._counter}"
        _dlog("lifecycle", f"new_session sid={sid} cmd={cmd!r} cols={cols} rows={rows} source={source!r}")
        session = Session(sid, cmd, cols, rows, on_data=self._output_event.set,
                          account_refs=account_refs)
        session._lifecycle_source = source or ""
        session._lifecycle_handoff = bool(handoff or source in {"scheduler", "scheduled", "auto"})
        self.sessions[sid] = session
        def _remember_account_refs(current):
            accounts = current.setdefault("accounts", account_manager._empty_accounts())
            accounts.setdefault("sessions", {})[sid] = dict(account_refs)
        update_config(_remember_account_refs)
        self._start_startup_trust_watcher(sid, session)
        session._bridge_enabled = True
        # Glasses stay off until someone explicitly opens this tab. See
        # Api.set_session_glasses for why there is no enable-all.
        session._glasses_enabled = False
        # Soft persistence (Windows / no-tmux fallback): record this session
        # so the next startup can recreate it
        self._save_soft_session(sid, cmd)
        self._persist_session_manifest()
        # Auto-register with bridge
        if self.bridge:
            label = cmd.split()[0] if cmd else sid
            self.bridge.register_session(
                sid, label,
                lambda text, _s=session: _s.write(text),
                peek_fn=lambda _s=session: bytes(_s._recent).decode('utf-8', errors='replace'),
                prepare_fn=lambda _s=session: self._prepare_pane_for_input(_s),
                cmd=cmd,
                cols=session.cols, rows=session.rows,
            )
            self.bridge.refresh_commands()
        if self.line_bridge:
            label = cmd.split()[0] if cmd else sid
            self.line_bridge.register_session(
                sid, label,
                lambda text, _s=session: _s.write(text),
                peek_fn=lambda _s=session: bytes(_s._recent).decode('utf-8', errors='replace'),
            )

        # Mark session for init prompt — only for AI CLI tools, not shells/editors/etc.
        session._init_pending = self._inject_init_prompt_enabled() and self._should_inject_init(cmd)
        # Nudge the UI to reconcile immediately (don't wait for 1.5s bridge poll).
        # Covers sessions created via TG /new, sfctl, or any non-UI path.
        self._plugin_dispatch_session_open(sid, cmd.split()[0] if cmd else sid)
        self._notify_ui_sessions_changed()
        idle_cfg = self._idle_reaper_config(load_config())
        if getattr(session, "_lifecycle_handoff", False) and idle_cfg.get("handoff_on_start", False):
            label = getattr(session, "_custom_label", None) or (cmd.split()[0] if cmd else sid)
            self._write_lifecycle_handoff(
                "排程已啟動頁籤",
                [
                    f"{label} ({sid})",
                    f"來源：{source or 'unknown'}",
                    f"指令：{cmd}",
                ],
                exclude_sids={sid},
            )
        return sid

    def _notify_ui_sessions_changed(self):
        """Ping the web UI to re-sync session list. Safe no-op if window not ready."""
        try:
            if self._window:
                self._window.evaluate_js('window._syncSessionsFromBackend && window._syncSessionsFromBackend()')
        except Exception:
            _swallow("Api._notify_ui_sessions_changed:2751")

    @staticmethod
    def _inject_init_prompt_enabled() -> bool:
        """首次訊息前置 INIT_PROMPT 的全域開關，預設關（回報 2026-07-14：
        觸發時機不對、內容已非必要）。只 gate `_init_pending` 的武裝——
        `_should_inject_init` 本身另被 master preamble 與完成通知
        （_arm_awaiting_response）借用為「AI 分頁」判定，不能在那裡關。
        切換後對新開的分頁生效。"""
        try:
            return (load_config().get("settings", {}) or {}).get(
                "inject_init_prompt", False) is True
        except Exception:
            return False

    def _should_inject_init(self, cmd: str) -> bool:
        """Decide whether a session command should receive the init prompt.

        Logic:
        1. If the preset has an explicit "inject_init" field, honour it.
        2. Otherwise, check if the base command name (or any arg) matches AI_CLI_TOOLS.
           This handles direct invocations (claude, codex) and wrapper forms
           (npx claude, bunx codex, /usr/local/bin/claude --model opus).
        """
        # Check preset-level override first
        cfg = load_config()
        for preset in cfg.get("presets", []):
            if preset.get("cmd", "").strip() == cmd.strip():
                override = preset.get("inject_init")
                if override is not None:
                    return bool(override)

        # Fall back to whitelist heuristic: scan all tokens in the command
        tokens = shlex.split(cmd) if cmd else []
        for token in tokens:
            # Strip path and get base name (e.g. /usr/local/bin/claude -> claude)
            base = Path(token).stem  # stem strips extension too (.exe, .py)
            if base in AI_CLI_TOOLS:
                return True
        return False

    def _get_init_prompt(self) -> str:
        """Load init prompt, strip TG section if bridge not active."""
        prompt = bridge_telegram.get_ui_prompt()
        if not prompt:
            return ""
        if not self.bridge or not self.bridge.active:
            marker = "\n## Telegram Bridge"
            idx = prompt.find(marker)
            if idx > 0:
                prompt = prompt[:idx].rstrip()
                prompt += "\n\nAcknowledge briefly and wait for the user's first message."
        prompt = bridge_telegram.append_user_instructions(prompt)
        return prompt

    def close_session(
        self,
        sid: str,
        reason: str = "manual",
        handoff: bool = False,
        summary_path: str = "",
        idle_seconds: int | None = None,
    ):
        _dlog("lifecycle", f"close_session sid={sid} reason={reason!r}")
        # 授權要跟著分頁一起結束。不收的話那個 sid 會永遠留在
        # glasses_allowed_sessions 裡，而 `sfctl glasses` 只走訪還活著的
        # session，所以看不到它——一個看不見的、方向朝「開」的殘留。
        # sid 單調遞增不會重複用，所以目前危害有限，但方向錯了就是錯了。
        if sid in self.sessions and getattr(self.sessions[sid], "_glasses_enabled", False):
            try:
                self.set_session_glasses(sid, False, "close")
            except Exception:
                _swallow(f"close_session:glasses:{sid}")
        s = self.sessions.get(sid)
        label = self._session_label(sid, s)
        cmd = s.cmd if s else ""
        lifecycle_source = getattr(s, "_lifecycle_source", "") if s else ""
        lifecycle_handoff = bool(getattr(s, "_lifecycle_handoff", False)) if s else False
        # Unregister from bridge
        if self.bridge:
            self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
        if self.line_bridge:
            self.line_bridge.unregister_session(sid)
        s = self.sessions.pop(sid, None)
        if s:
            self._plugin_dispatch_session_close(sid)
            s.kill()
            # Clean up persisted label
            cfg = load_config()
            labels = cfg.get("session_labels", {})
            if sid in labels:
                del labels[sid]
                cfg["session_labels"] = labels
                save_config(cfg)
            # Drop from soft-persistence list (Windows / no-tmux)
            self._drop_soft_session(sid)
            def _drop_account_ref(current):
                accounts = current.get("accounts") or {}
                sessions = accounts.get("sessions") or {}
                if sid in sessions:
                    sessions.pop(sid, None)
                    accounts["sessions"] = sessions
                    current["accounts"] = accounts
            update_config(_drop_account_ref)
            self._persist_session_manifest()
        self._notify_ui_sessions_changed()
        if s and (handoff or lifecycle_handoff):
            bullets = [f"已關閉：{label} ({sid})"]
            if lifecycle_source:
                bullets.append(f"來源：{lifecycle_source}")
            if reason:
                bullets.append(f"原因：{reason}")
            if idle_seconds is not None:
                bullets.append(f"閒置：約 {idle_seconds} 秒")
            if summary_path:
                bullets.append(f"摘要檔：{summary_path}")
            if cmd:
                bullets.append(f"指令：{cmd}")
            self._write_lifecycle_handoff("頁籤已關閉交接", bullets, exclude_sids={sid})

    # Patterns in CLI output that indicate the AI tool is ready for conversation
    # (not in login/setup/auth flow). Checked after stripping ANSI escapes.
    import re as _re
    _ANSI_RE = _re.compile(r'\x1b\[[^A-Za-z]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b.|\x07')
    _AI_READY_RE = _re.compile(
        r'[>›]\s*$'           # Claude Code / Codex input prompt
        r'|^\s*[>›]\s+\S'     # Codex placeholder on the input line
        r'|^\s*Tip:'           # Codex tip line (shown after ready)
        r'|model:\s+\S'        # Codex model info box
        r'|claude\.ai'         # Claude Code welcome
        r'|What can I help'    # Common AI greeting
        , _re.MULTILINE
    )
    _STARTUP_TRUST_RE = _re.compile(
        r'(Quick safety check|Is this a project you trust|Do you trust (?:the )?(?:files|project|folder))'
        r'[\s\S]{0,800}'
        r'(?:1[.)]\s*)?Yes,\s*I\s*trust\s*this\s*folder',
        _re.IGNORECASE,
    )

    def _start_startup_trust_watcher(self, sid: str, s: Session):
        if not getattr(s, '_startup_trust_pending', False):
            return

        def _watch():
            while getattr(s, 'alive', False) and getattr(s, '_startup_trust_pending', False):
                self._auto_accept_startup_trust_prompt(sid, s)
                if not getattr(s, '_startup_trust_pending', False):
                    break
                if time.monotonic() > getattr(s, '_startup_trust_deadline', 0):
                    s._startup_trust_pending = False
                    break
                time.sleep(0.15)

        threading.Thread(target=_watch, daemon=True).start()

    def _startup_trust_tail(self, s: Session) -> str:
        parts = []
        with s.lock:
            recent = bytes(s._recent).decode('utf-8', errors='replace')
        if recent:
            parts.append(recent)
        tmux_name = getattr(s, '_tmux_name', None)
        if tmux_name:
            try:
                r = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-J", "-t", tmux_name, "-S", "-80"],
                    capture_output=True, text=True, timeout=1,
                )
                if r.returncode == 0 and r.stdout:
                    parts.append(r.stdout)
            except Exception:
                _swallow("Api._startup_trust_tail:2894")
        return "\n".join(parts)[-4000:]

    def _startup_trust_screen(self, s: Session) -> str:
        """**當前畫面**（單一幀），拿來判斷游標在哪一行、以及對話框答掉了沒。

        跟 `_startup_trust_tail` 的差別是這裡不接 ring buffer：ring buffer 是
        TUI 逐幀重繪的原始位元組，同一個對話框會疊很多份殘影，用來算游標位置
        會跨幀配對出反方向（見 `_trust_dialog_nav`）。偵測要靈敏所以用 tail，
        **按鍵前的定位一律用這個**。沒有 tmux（Windows）時才退回 ring buffer。
        """
        tmux_name = getattr(s, '_tmux_name', None)
        if tmux_name:
            try:
                r = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-J", "-t", tmux_name],
                    capture_output=True, text=True, timeout=1,
                )
                if r.returncode == 0 and r.stdout:
                    return r.stdout[-4000:]
            except Exception:
                _swallow("Api._startup_trust_screen")
        with s.lock:
            return bytes(s._recent).decode('utf-8', errors='replace')[-4000:]

    # 信任對話框的兩個選項行。Claude Code 這版的游標**預設停在「No, exit」**
    # （2026-08-28 截圖實證），所以「按 Enter 就對了」是致命假設：Enter 直接
    # 讓 CLI 退出、分頁消失。選項順序與有無編號都隨版本變，所以一律先讀游標
    # 在哪一行，再決定要按幾次方向鍵。
    _TRUST_OPT_YES_RE = _re.compile(r'Yes,\s*I\s*trust\s*this\s*folder', _re.I)
    _TRUST_OPT_NO_RE = _re.compile(r'No,?\s*exit', _re.I)
    _TRUST_CURSOR_RE = _re.compile(r'^\s*[❯>›»▶]\s*\S')

    def _trust_dialog_nav(self, clean: str):
        """(方向, 步數) 讓游標走到「Yes, I trust this folder」；抓不到回 None。

        **只認最後一幀**：餵進來的文字可能是 ring buffer（TUI 逐幀重繪的原始
        輸出）接上 tmux 快照，同一個對話框會出現好幾次，而且早期那幾幀常常
        還沒畫上游標。舊版拿「第一個 Yes」配「第一個游標」，跨幀配對就會算出
        反方向——2026-09-04 正式環境 log 實錄 `keys=['Up','Enter']`：游標本來
        就在 No, exit，Up 到頂不會 wrap，等於原地不動再按 Enter＝選 No, exit
        把分頁關掉。取最後兩個選項行才是當前畫面的真實狀態。
        """
        rows = []          # [(is_yes, has_cursor)]
        for line in clean.splitlines():
            is_yes = bool(self._TRUST_OPT_YES_RE.search(line))
            is_no = bool(self._TRUST_OPT_NO_RE.search(line))
            if not (is_yes or is_no):
                continue
            rows.append((is_yes, bool(self._TRUST_CURSOR_RE.match(line))))
        if len(rows) < 2:
            return None
        rows = rows[-2:]
        # 一幀裡有且只有一個游標。0 個＝還沒畫完，2 個＝跨幀殘影，
        # 兩種都代表這份文字不是乾淨的單幀畫面 → 不敢按。
        if sum(1 for _, cur in rows if cur) != 1:
            return None
        if len({is_yes for is_yes, _ in rows}) != 2:
            return None          # 兩行是同一個選項的殘影，配對不出來
        try:
            yes_i = next(i for i, (is_yes, _) in enumerate(rows) if is_yes)
            cur_i = next(i for i, (_, cur) in enumerate(rows) if cur)
        except StopIteration:
            return None
        delta = yes_i - cur_i
        return ("Down" if delta > 0 else "Up", abs(delta))

    def answer_startup_trust(self, sid: str, trust: bool = True) -> bool:
        """把啟動信任對話框答掉（預設選 Yes）。回傳有沒有真的按下去。

        絕不盲按 Enter：這版游標預設在「No, exit」，盲按 = 關掉分頁。"""
        s = self.sessions.get(sid)
        if not s or not getattr(s, "alive", False):
            return False
        # 冷卻：答完之後對話框文字還會留在畫面／ring buffer 幾秒，沒有這道
        # 閘門會再按一次——那時已經是正常 composer，Up 會把上一則輸入叫回
        # 輸入框、Enter 再送出去。5s 足夠讓畫面翻頁。
        now = time.monotonic()
        if now - getattr(s, "_trust_answered_at", 0.0) < 5.0:
            return False
        # 定位一律用當前畫面（單一幀），不用會疊殘影的 tail。
        clean = self._ANSI_RE.sub('', self._startup_trust_screen(s) or "")
        if not self._STARTUP_TRUST_RE.search(clean):
            return False
        nav = self._trust_dialog_nav(clean)
        if nav is None:
            _dlog("trust", f"trust dialog options unparsable sid={sid} — 不敢按")
            return False
        key, steps = nav
        if not trust:                      # 要選 No：往反方向同樣的步數
            key, steps = ("Up" if key == "Down" else "Down"), steps
            if steps == 0:
                key, steps = "Down", 1
        keys = [key] * steps + ["Enter"]
        tmux_name = getattr(s, "_tmux_name", None)
        _dlog("trust", f"answering trust dialog sid={sid} trust={trust} keys={keys}")
        try:
            if tmux_name:
                subprocess.run(["tmux", "send-keys", "-t", tmux_name] + keys,
                               capture_output=True, timeout=2)
            else:
                seq = {"Down": "\x1b[B", "Up": "\x1b[A", "Enter": "\r"}
                for k in keys:
                    s.write(seq[k])
                    time.sleep(0.05)
        except Exception:
            _swallow("Api.answer_startup_trust")
            return False
        s._trust_answered_at = time.monotonic()
        # 送完要回頭確認畫面**真的翻頁了**。舊版送完就回 True，呼叫端跟著把
        # pending 關掉——只要那幾個按鍵送進還沒接手鍵盤的 TUI（開機頭一秒很
        # 常見），按鍵石沉大海，對話框留在畫面上而且再也沒人來救，分頁卡死。
        for _ in range(3):
            time.sleep(0.25)
            after = self._ANSI_RE.sub('', self._startup_trust_screen(s) or "")
            if not self._STARTUP_TRUST_RE.search(after):
                return True
        _dlog("trust", f"trust dialog still up after keys sid={sid} — 保持 pending 待重試")
        return False

    def _auto_accept_startup_trust_prompt(self, sid: str, s: Session):
        """Answer only known startup trust prompts for trusted AI cwd launches."""
        if not getattr(s, '_startup_trust_pending', False):
            return
        if time.monotonic() > getattr(s, '_startup_trust_deadline', 0):
            s._startup_trust_pending = False
            return
        if not _should_auto_accept_startup_trust(s.cmd, getattr(s, 'cwd', '')):
            s._startup_trust_pending = False
            return
        tail = self._startup_trust_tail(s)
        clean = self._ANSI_RE.sub('', tail) if tail else ""
        clean = self._ANSI_STRIP_RE.sub('', clean) if clean else ""
        if not self._STARTUP_TRUST_RE.search(clean):
            return
        # 舊版在這裡直接送 Enter —— 這版游標預設停在「No, exit」，等於
        # 自動把新分頁關掉（2026-08-28 手機端開的分頁就是這樣沒的）。
        if not self.answer_startup_trust(sid, trust=True):
            # 選項讀不出來就**維持 pending**，讓 TG 那邊把對話框帶回手機給
            # 使用者自己選，絕不亂按。
            s._startup_trust_pending = True
            return
        s._startup_trust_pending = False
        s._startup_trust_answered = True
        _dlog("trust", f"auto-accepted startup trust prompt sid={sid} cwd={getattr(s, 'cwd', '')!r}")

    def _prepare_pane_for_input(self, s: Session) -> bool:
        """Ready a session's pane to receive injected input (TG bridge
        prepare_fn). A pane left in tmux copy-mode — scrolled-back terminal,
        stray PageUp — consumes pasted bytes as copy-mode keystrokes, so a
        bridged message vanishes without a trace. Exit the mode first.
        Returns True when a recovery action was taken."""
        tn = getattr(s, "_tmux_name", None)
        if not tn or IS_WIN or not shutil.which("tmux"):
            return False
        try:
            r = subprocess.run(
                ["tmux", "display-message", "-p", "-t", tn, "#{pane_in_mode}"],
                capture_output=True, text=True, timeout=2)
            if r.stdout.strip() == "1":
                subprocess.run(["tmux", "send-keys", "-t", tn, "-X", "cancel"],
                               capture_output=True, timeout=2)
                _dlog("send", f"exited copy-mode before inject sid={s.sid}")
                return True
        except Exception:
            _swallow("Api._prepare_pane_for_input:2945")
        return False

    def _send_text_to_session(self, s: Session, text: str, submit: bool = False) -> bool:
        """Send orchestrator text as a paste, then optionally press Enter.

        Direct PTY writes are fine for keystrokes, but large AI prompts can leave
        Claude/Codex in a paste/multiline state where the following CR is ignored
        or treated as another line. tmux paste-buffer with bracketed paste gives
        terminal apps one coherent paste event; Enter is sent only after that
        paste has completed.
        """
        text = str(text or "")
        if text:
            now = time.time()
            s._startup_trust_pending = False
            s._last_activity_time = now
            s._last_user_activity_time = now

        tmux_name = getattr(s, "_tmux_name", None)
        if not IS_WIN and tmux_name and shutil.which("tmux"):
            buffer_name = f"shellframe-send-{s.sid}-{os.getpid()}-{int(time.time() * 1000)}"
            pasted = False
            try:
                if text:
                    loaded = subprocess.run(
                        ["tmux", "load-buffer", "-b", buffer_name, "-"],
                        input=text.encode("utf-8", errors="replace"),
                        capture_output=True,
                        timeout=5,
                    )
                    if loaded.returncode != 0:
                        raise RuntimeError(loaded.stderr.decode("utf-8", errors="replace").strip())
                    pasted_result = subprocess.run(
                        ["tmux", "paste-buffer", "-d", "-p", "-r", "-b", buffer_name, "-t", tmux_name],
                        capture_output=True,
                        timeout=5,
                    )
                    if pasted_result.returncode != 0:
                        raise RuntimeError(pasted_result.stderr.decode("utf-8", errors="replace").strip())
                    pasted = True
                if submit:
                    if text:
                        time.sleep(min(0.5, max(0.08, len(text) / 50000.0)))
                    entered = subprocess.run(
                        ["tmux", "send-keys", "-t", tmux_name, "Enter"],
                        capture_output=True,
                        timeout=3,
                    )
                    if entered.returncode != 0:
                        raise RuntimeError(entered.stderr.decode("utf-8", errors="replace").strip())
                _dlog("send", f"tmux paste sid={s.sid} len={len(text)} submit={submit}")
                return True
            except Exception as e:
                _dlog("send", f"tmux paste failed sid={s.sid} target={tmux_name!r}: {e}")
                try:
                    subprocess.run(["tmux", "delete-buffer", "-b", buffer_name], capture_output=True, timeout=1)
                except Exception:
                    _swallow("Api._send_text_to_session:3003")
                if pasted:
                    if submit:
                        s.write("\r")
                    return False

        if text:
            s.write(text)
        if submit:
            if IS_WIN and text:
                # ConPTY 把 payload 逐字合成 key events，client 端（尤其 codex/
                # crossterm 讀 win32 事件、拿不到 bracketed-paste 框架）drain 大
                # payload 遠超過固定短延遲；CR 在貼上偵測（burst）窗內到達會被
                # 當成換行插進 composer 而不是送出——訊息整段卡在輸入框。
                # 等待按長度放大；送出後若畫面仍掛著 payload 尾段（或 codex 的
                # paste chip）且無 turn 訊號，補一個裸 Enter——composer 已空時
                # 是 no-op，不會重複送出。
                time.sleep(max(0.3, min(2.0, len(text) / 2500.0)))
                s.write("\r")
                time.sleep(0.8)
                try:
                    tail = self._ANSI_RE.sub('', bytes(s._recent).decode("utf-8", errors="replace"))
                except Exception:
                    tail = ""
                probe = re.sub(r"\s+", "", text)[-18:]
                flat = re.sub(r"\s+", "", tail)
                stuck = ((probe and probe in flat)
                         or re.search(r'\[Pasted (?:Content|text)[^\]]*\]', tail, re.I))
                if stuck and not re.search(r"esc to interrupt", tail, re.I):
                    _dlog("send", f"win nudge Enter sid={s.sid} (payload stuck in composer)")
                    s.write("\r")
            else:
                time.sleep(0.05)
                s.write("\r")
        return False

    @staticmethod
    def _is_user_content(data: str) -> bool:
        """True when this PTY-input chunk carries real typed/pasted text, as
        opposed to a bare Enter, a control key, or an escape sequence (arrow /
        function keys). xterm delivers a message's text and the Enter that
        submits it in separate write_input calls, so init-prompt injection must
        key off the first content chunk rather than the trailing '\\r'."""
        d = data or ""
        if not d:
            return False
        if '\x1b[200~' in d:          # bracketed paste always carries content
            return True
        if d.startswith('\x1b'):      # escape seq (arrows, F-keys) — not content
            return False
        return any(ch >= ' ' for ch in d)  # any printable (non-C0) char

    def write_input(self, sid: str, data: str):
        s = self.sessions.get(sid)
        if not s:
            return
        if data:
            now = time.time()
            # ── IME commit 雙送的保底去重 ──
            # 前端（web/index.html 的 _makeImeDedup）擋的是 xterm.onData 那條路，
            # 但 2026-09-02 實測重複仍然穿過來：10:05:51.665 / .758 兩筆一模一樣
            # 的 8 字、中間 93ms，而前端**一筆 ime-dup 足跡都沒留**——那條路徑
            # 根本沒經過它（write_input 在前端有 28 個呼叫點，onData 只是其中
            # 一條）。這裡是所有輸入的唯一出口，保底擋在這。
            #
            # 只認 IME commit 的形狀：含非 ASCII、長度 > 1、內容完全相同、
            # 200ms 內。人要連打出一模一樣的**詞組**，光注音加選字就要三百毫秒
            # 以上，碰不到這個窗口；單字（len == 1）完全不管，免得吃掉「哈哈」
            # 這種連字（實測雙送間隔 93～111ms）。
            if len(data) > 1 and not data.isascii():
                prev = getattr(s, "_ime_last_chunk", "")
                prev_ts = getattr(s, "_ime_last_ts", 0.0)
                if data == prev and (now - prev_ts) < 0.2:
                    _dlog("ime", f"sid={sid} 擋掉 IME 重複 "
                                 f"gap={int((now - prev_ts) * 1000)}ms "
                                 f"len={len(data)} preview={data[:20]!r}")
                    s._ime_last_ts = now      # 連三送也要一路擋掉
                    return
                s._ime_last_chunk = data
                s._ime_last_ts = now
            else:
                # 任何不是 IME-commit 形狀的輸入（按鍵、Enter、ASCII、單字）都
                # 代表「上一次 commit 已經結束」——雙送的兩筆之間不會夾任何東西
                # （實測 10:05:51.665 / .758 中間沒有別的 write）。清掉狀態，
                # 免得使用者送出後立刻再打同一個詞被當成重複吃掉。
                s._ime_last_chunk = ""
                s._ime_last_ts = 0.0
            s._startup_trust_pending = False
            s._last_activity_time = now
            s._last_user_activity_time = now
            if getattr(s, "_idle_reap_state", ""):
                s._idle_reap_state = ""
                s._idle_summary_requested_at = 0.0
                s._idle_close_after = 0.0
        # Auto-slug: on first user Enter, rename tmux session to a haiku-derived slug.
        # Runs in background so it never blocks the keystroke path. Only fires once
        # (_slug_pending) and only when the session has a default sf_sNN tmux name.
        if (getattr(s, '_slug_pending', False)
                and '\r' in data
                and getattr(s, '_tmux_name', None)
                and s._tmux_name.startswith(TMUX_PREFIX)):
            s._slug_pending = False
            user_text = data.rstrip('\r\n').strip()
            if user_text:
                def _do_slug(sid=sid, s=s, text=user_text):
                    slug = _haiku_slug(text)
                    if not slug:
                        return
                    new_name = _unique_tmux_name(f"{TMUX_PREFIX}{slug}")
                    old_name = s._tmux_name
                    r = subprocess.run(
                        ["tmux", "rename-session", "-t", old_name, new_name],
                        capture_output=True, timeout=3,
                    )
                    if r.returncode == 0:
                        s._tmux_name = new_name
                        display_name = slug.replace('-', ' ')
                        self.rename_session(sid, display_name)
                        self._persist_session_manifest()
                        _dlog("slug", f"tmux rename {old_name!r} → {new_name!r}")
                threading.Thread(target=_do_slug, daemon=True).start()
        # IME dedup：前端 _makeImeDedup 擋 xterm.onData 那條，上面的保底擋其餘路徑。
        # On the first REAL user message, inject the init prompt BEFORE it.
        #
        # xterm.js delivers the message text and the Enter that submits it in
        # SEPARATE write_input calls — each typed key / paste flushes on its own
        # and Enter arrives as a bare '\r'. The old guard `'\r' in data` fired
        # only on that bare Enter, by which point the user's text had already
        # been written to the PTY; the prompt was then appended after it (with an
        # empty user_text), landing INIT_PROMPT in the middle / after the user
        # message. Trigger instead on the first content-bearing chunk and prepend
        # the prompt to it, so INIT_PROMPT is always first and the user's text
        # (and its later bare '\r') flow naturally after.
        #
        # SLASH COMMANDS ARE NOT A FIRST MESSAGE (使用者: 新分頁打 /model 被
        # inject 一大段、指令直接壞掉). A chunk whose line starts with '/' is a
        # CLI command (/model, /compact…) — never spend the init prompt on it.
        # Because input arrives per-keystroke, a lone '/' must also HOLD the
        # gate for the rest of that line (otherwise the next key 'm' would
        # inject mid-command); the hold releases when the line is submitted.
        if getattr(s, '_init_pending', False):
            decision = self._init_inject_decision(s, data)
            if decision == "inject":
                # Check if CLI output looks like an AI tool ready for
                # conversation (not a login screen, auth flow, shell prompt)
                with s.lock:
                    tail = bytes(s._recent).decode('utf-8', errors='replace')
                clean = self._ANSI_RE.sub('', tail) if tail else ""
                if self._AI_READY_RE.search(clean):
                    # AI tool is ready — inject init prompt ahead of this chunk.
                    s._init_pending = False
                    prompt = self._get_init_prompt()
                    if prompt:
                        if self.bridge:
                            slot = self.bridge.slots.get(sid)
                            if slot:
                                slot.sent_texts.append(prompt)
                        s.write(prompt + "\n\n---\nUser's first message: " + data)
                        self._arm_awaiting_response(sid, data)
                        return
                # Not ready yet (login/auth flow) — pass through, keep _init_pending
        should = self._should_prepend_master_turn_preamble(sid, s, data)
        _dlog("preamble", f"sid={sid} should={should} label={self._session_label(sid, s)!r} is_master={self._is_master_session(sid, s)} inject_init={self._should_inject_init(getattr(s, 'cmd', ''))} enabled={self._master_turn_preamble_enabled()} data={data!r:.60}")
        if should:
            user_text = data.rstrip('\r\n')
            s.write(self._wrap_master_turn_input(user_text) + "\r")
            self._arm_awaiting_response(sid, data)
            return
        s.write(data)
        self._arm_awaiting_response(sid, data)

    def get_session_model_info(self, sid: str):
        """Model + thinking effort for a session — TG bridge menu/list uses
        this to mirror the desktop sidebar badge (the user 2026-07-06). Returns
        {"name","effort","provider"} or None (non-AI tab / not detectable).
        Cheap: agent_status mtime-caches its transcript/settings parses. Uses
        the real session's cwd+session_id so it's per-tab accurate (the same
        path the sidebar badge takes), not the global settings fallback."""
        s = self.sessions.get(sid)
        if not s:
            return None
        worker = {
            "cmd": getattr(s, "cmd", ""),
            "cwd": getattr(s, "cwd", "~"),
            "tmux_name": getattr(s, "_tmux_name", None),
            "session_id": getattr(s, "session_id", None),
            "transcript_hint": getattr(s, "_hook_transcript_path", None),
        }
        try:
            path = agent_status.resolve_transcript(worker)
            return agent_status.detect_model_info(
                worker, path if (path and os.path.exists(path)) else None)
        except Exception:
            _swallow(f"get_session_model_info:{sid}")
            return None

    def _agent_status_snapshot(self, sid: str):
        """TG 長回合心跳的狀態來源：**唯讀**最近一次 StatusTracker 結果。

        回 (result_dict, age_seconds) 或 None。刻意不呼叫 status_for()——那會
        觸發 transcript 解析（lsof / JSONL 尾讀），成本會被帶進 bridge 的
        flush loop。_start_status_monitor 那條 0.6s thread 已經在算了，這裡
        只是把算好的值遞出去，等於零額外成本。"""
        try:
            res, age = self._status_tracker.last_result(sid)
        except Exception:
            return None
        return (res, age) if res else None

    @staticmethod
    def _init_inject_decision(s, data: str) -> str:
        """State machine for the web-UI init-prompt gate. Returns:
          'inject' — first chunk of a real message: safe to prepend INIT_PROMPT
          'pass'   — control/enter chunk, or slash-command line in progress
        Slash-command handling: a content chunk whose line starts with '/'
        sets _init_hold so per-keystroke follow-ups ('m','o','d'…) don't
        inject mid-command; the hold clears once that line submits (\\r/\\n),
        keeping _init_pending armed for the NEXT real message."""
        submits = ('\r' in data) or ('\n' in data)
        if getattr(s, '_init_hold', False):
            if submits:
                s._init_hold = False
            return "pass"
        if not Api._is_user_content(data):
            return "pass"
        if data.lstrip().startswith("/"):
            if not submits:          # pasted "/cmd\r" completes in one chunk
                s._init_hold = True  # typed '/': hold until the line submits
            return "pass"
        return "inject"

    def consume_init_prompt_if_ready(self, sid: str) -> str:
        """If session has pending init prompt AND CLI looks ready, consume and return it.
        Used by TG bridge to inject init prompt on the first forwarded message
        (web UI path does this inline in write_input). Returns "" if not ready
        or no init pending, leaving state untouched so next message retries."""
        s = self.sessions.get(sid)
        if not s or not getattr(s, '_init_pending', False):
            return ""
        with s.lock:
            tail = bytes(s._recent).decode('utf-8', errors='replace')
        clean = self._ANSI_RE.sub('', tail) if tail else ""
        if not self._AI_READY_RE.search(clean):
            return ""
        prompt = self._get_init_prompt()
        if not prompt:
            s._init_pending = False
            return ""
        s._init_pending = False
        return prompt

    def is_session_ready_for_bridge(self, sid: str) -> bool:
        """Return True when a bridged AI tab is ready to receive pasted input."""
        s = self.sessions.get(sid)
        if not s or not getattr(s, "alive", False):
            return False
        self._auto_accept_startup_trust_prompt(sid, s)
        parts = []
        with s.lock:
            recent = bytes(s._recent).decode('utf-8', errors='replace')
        if recent:
            parts.append(recent)
        tmux_name = getattr(s, '_tmux_name', None)
        if tmux_name:
            try:
                r = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-J", "-t", tmux_name, "-S", "-80"],
                    capture_output=True, text=True, timeout=1,
                )
                if r.returncode == 0 and r.stdout:
                    parts.append(r.stdout)
            except Exception:
                _swallow("Api.is_session_ready_for_bridge:3154")
        clean = self._ANSI_RE.sub('', "\n".join(parts)) if parts else ""
        return bool(self._AI_READY_RE.search(clean))

    _STARTUP_EXIT_OPTION_RE = _re.compile(
        r'^\s*(?:[❯>›]\s*)?2[.)]\s*No,?\s*exit', _re.MULTILINE | _re.IGNORECASE)

    def startup_dialog_blocking(self, sid: str) -> str:
        """分頁是否正停在會吃掉貼上輸入的啟動對話框；回傳原因（空＝安全）。

        TG bridge 的注入是 Ctrl-U ＋整段文字 ＋ Enter——打進選單就是「幫使用者
        選一個選項」，而 Claude Code 信任對話框第 2 項是 No, exit，分頁會被
        自己收到的訊息關掉（2026-08-28 實例）。這裡刻意只偵測**危險狀態**，
        偵測不到就放行：`_AI_READY_RE` 那種「就緒偵測」對 Claude Code 2.x 的
        ❯ composer 配不到，拿來當閘門會把正常分頁全擋死。
        """
        s = self.sessions.get(sid)
        if not s or not getattr(s, "alive", False):
            return ""
        # 先給既有的自動接受一次機會處理掉信任對話框
        try:
            self._auto_accept_startup_trust_prompt(sid, s)
        except Exception:
            _swallow("Api.startup_dialog_blocking:trust")
        parts = []
        tmux_name = getattr(s, "_tmux_name", None)
        if tmux_name:
            try:
                r = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-J", "-t", tmux_name, "-S", "-40"],
                    capture_output=True, text=True, timeout=1)
                if r.returncode == 0 and r.stdout:
                    parts.append(r.stdout)
            except Exception:
                _swallow("Api.startup_dialog_blocking:capture")
        if not parts:
            return ""
        clean = self._ANSI_RE.sub('', "\n".join(parts))
        if self._STARTUP_TRUST_RE.search(clean):
            # 受信任的 cwd 就直接（游標感知地）答掉，不要讓使用者卡在這。
            # 這條路徑沒有 _startup_trust_deadline 的時限，所以連「開機那幾秒
            # 沒抓到、對話框一直掛著」的分頁也救得回來。
            if (_should_auto_accept_startup_trust(getattr(s, "cmd", ""),
                                                 getattr(s, "cwd", ""))
                    and self.answer_startup_trust(sid, trust=True)):
                time.sleep(0.6)
                s._startup_trust_pending = False
                s._startup_trust_answered = True
                return ""
            return "啟動信任對話框"
        if self._STARTUP_EXIT_OPTION_RE.search(clean):
            return "啟動選單（有 No, exit 選項）"
        return ""

    def read_output(self, sid: str) -> str:
        """Read buffered output. Used only during reconnect — normal output is pushed."""
        s = self.sessions.get(sid)
        if not s:
            return ""
        return s.read()

    def is_alive(self, sid: str) -> bool:
        s = self.sessions.get(sid)
        return s.alive if s else False

    def _account_config(self):
        cfg = load_config()
        if ACCOUNT_MANAGER.ensure(cfg):
            save_config(cfg)
        return cfg

    @staticmethod
    def _single_account_state(provider: str):
        """Panel entry for a provider that has quota but no switchable profiles.

        Some CLIs sign in as exactly one account and manage that themselves
        (agy → one Google account), so there is nothing to switch between. They
        still belong in the panel: the point is seeing every account's
        water-level. `single_account` tells the UI to drop the switch buttons.
        Derived from the registry, so a future usage-only provider needs no
        change here.
        """
        spec = usage_probe.PROVIDER_SPECS.get(provider) or {}
        label = ""
        try:
            label = spec["account"](None, {}) if spec.get("account") else ""
        except Exception:
            label = ""
        if not label:
            return {"current": None, "global": None, "accounts": [],
                    "logged_in": False, "single_account": True}
        item = {"id": f"{provider}-current", "email": label, "label": label,
                "plan": "", "organization": ""}
        return {"current": item, "global": item, "accounts": [item],
                "logged_in": True, "single_account": True}

    def _account_state(self, sid: str = ""):
        cfg = self._account_config()
        session = self.sessions.get(sid)
        if session:
            cfg.setdefault("accounts", account_manager._empty_accounts()) \
                .setdefault("sessions", {})[sid] = dict(session.account_refs)
        state = ACCOUNT_MANAGER.safe_state(cfg, sid or None)
        for provider in usage_probe.PROVIDERS:
            if provider not in account_manager.PROVIDERS:
                state["providers"][provider] = self._single_account_state(provider)
            entry = state["providers"].setdefault(provider, {})
            # "not installed" and "installed but not signed in" need different
            # advice, so the panel gets both facts instead of one blank row.
            entry["installed"] = usage_probe.provider_installed(provider)
            entry["install"] = (
                usage_probe.PROVIDER_SPECS.get(provider, {}).get("install") or {}
            )
        return cfg, state

    def account_state(self, sid: str = "") -> str:
        """Safe account panel data: refs and labels, never credential contents."""
        try:
            return json.dumps(self._account_state(sid)[1], ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def account_switch_global(self, provider: str, ref: str) -> str:
        """Change only the account inherited by future sessions."""
        try:
            cfg = self._account_config()
            ACCOUNT_MANAGER.set_global(cfg, provider, ref)
            save_config(cfg)
            return json.dumps({"success": True, "scope": "global",
                               "state": self._account_state()[1]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    @staticmethod
    def _claude_config_dir_for(refs: dict) -> str:
        """這組 account refs 對應的 claude 設定家目錄（含 projects/transcripts）。
        沒釘 profile → 預設 ~/.claude。"""
        ref = (refs or {}).get("claude")
        env = ACCOUNT_MANAGER.env_for("claude", ref) if ref else {}
        return env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")

    def _carry_claude_transcript(self, old_session, csid: str, new_refs: dict):
        """切帳號＝換 CLAUDE_CONFIG_DIR，新帳號的 projects 裡沒有這段對話的
        transcript，`--resume` 會找不到、歷史就消失（Howard 2026-09-05）。
        把當前對話的 uuid.jsonl 複製進新帳號的 projects/<同一個 cwd slug>/，
        resume 才接得回同一段歷史。同帳號（config dir 沒變）則不必搬。"""
        if not csid:
            return
        old_dir = self._claude_config_dir_for(getattr(old_session, "account_refs", {}))
        new_dir = self._claude_config_dir_for(new_refs)
        if os.path.abspath(old_dir) == os.path.abspath(new_dir):
            return
        src = getattr(old_session, "_hook_transcript_path", "") or ""
        if not (src and os.path.isfile(src)):
            import glob as _glob
            for root in (os.path.join(old_dir, "projects"),
                         os.path.expanduser("~/.claude/projects")):
                hits = _glob.glob(os.path.join(root, "*", f"{csid}.jsonl"))
                if hits:
                    src = hits[0]
                    break
        if not (src and os.path.isfile(src)):
            _dlog("account", f"switch: 找不到 {csid} 的 transcript，歷史無法搬移")
            return
        slug = os.path.basename(os.path.dirname(src))
        dst_dir = os.path.join(new_dir, "projects", slug)
        try:
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, f"{csid}.jsonl")
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
            _dlog("account", f"switch: 搬移 transcript {csid} → {dst}")
        except OSError as e:
            _dlog("account", f"switch: transcript 搬移失敗 {e}")

    def _restart_session_for_account(self, sid: str, account_refs: dict):
        old = self.sessions.get(sid)
        if not old:
            raise ValueError("此 tab 不存在或已關閉")
        cmd = old.cmd
        # 保留對話：claude 分頁換帳號時，把當前 uuid 的 transcript 搬進新帳號的
        # config dir，並以 --resume <uuid> 重開，歷史才不會消失。
        csid = getattr(old, "session_id", "") or ""
        try:
            is_claude = usage_probe.detect_ai(cmd) == "claude"
        except Exception:
            is_claude = False
        if is_claude and csid:
            self._carry_claude_transcript(old, csid, account_refs)
            cmd = self._cmd_with_resume(cmd, csid)
        cols, rows = old.cols, old.rows
        tmux_name = old._tmux_name
        label = getattr(old, "_custom_label", None)
        bridge_enabled = getattr(old, "_bridge_enabled", True)
        glasses_enabled = getattr(old, "_glasses_enabled", False)
        lifecycle_source = getattr(old, "_lifecycle_source", "")
        lifecycle_handoff = getattr(old, "_lifecycle_handoff", False)
        if self.bridge:
            self.bridge.unregister_session(sid)
        if self.line_bridge:
            self.line_bridge.unregister_session(sid)
        old.kill()
        session = Session(sid, cmd, cols, rows, on_data=self._output_event.set,
                          tmux_name=tmux_name, account_refs=account_refs,
                          account_refs_authoritative=True)
        session._bridge_enabled = bridge_enabled
        session._glasses_enabled = glasses_enabled
        session._init_pending = False
        # 換帳號＝把行程砍掉重開，等同全新啟動，信任對話框會再問一次。
        session._slug_pending = False
        session._lifecycle_source = lifecycle_source
        session._lifecycle_handoff = lifecycle_handoff
        if label:
            session._custom_label = label
        self.sessions[sid] = session
        # 註冊進 self.sessions 之後才掛 watcher——answer_startup_trust 是用
        # sid 回查 session 的，先掛會空轉幾輪。
        self._start_startup_trust_watcher(sid, session)
        if self.bridge:
            self.bridge.register_session(
                sid, label or (cmd.split()[0] if cmd else sid),
                lambda text, _s=session: _s.write(text),
                peek_fn=lambda _s=session: bytes(_s._recent).decode("utf-8", errors="replace"),
                prepare_fn=lambda _s=session: self._prepare_pane_for_input(_s),
                cmd=cmd,
                cols=session.cols, rows=session.rows,
            )
            self.bridge.refresh_commands()
        if self.line_bridge:
            self.line_bridge.register_session(
                sid, label or (cmd.split()[0] if cmd else sid),
                lambda text, _s=session: _s.write(text),
                peek_fn=lambda _s=session: bytes(_s._recent).decode("utf-8", errors="replace"),
            )
        self._persist_session_manifest()
        self._notify_ui_sessions_changed()
        return session

    def account_switch_session(self, sid: str, provider: str, ref: str) -> str:
        """Switch/relaunch one tab; all other running tabs stay untouched."""
        try:
            cfg = self._account_config()
            if provider not in account_manager.PROVIDERS:
                raise ValueError("unknown provider")
            ACCOUNT_MANAGER.set_session_ref(cfg, sid, provider, ref)
            session = self.sessions.get(sid)
            if not session:
                raise ValueError("此 tab 不存在或已關閉")
            refs = dict(session.account_refs)
            refs[provider] = ref
            cfg.setdefault("accounts", account_manager._empty_accounts()) \
                .setdefault("sessions", {})[sid] = refs
            save_config(cfg)
            self._restart_session_for_account(sid, refs)
            return json.dumps({"success": True, "scope": "session", "sid": sid,
                               "state": self._account_state(sid)[1]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def account_login_start(self, provider: str) -> str:
        """Open an explicit provider login tab; nothing starts automatically."""
        try:
            if provider == "codex":
                cmd = f"{CODEX_LAUNCHER} login"
            elif provider == "claude":
                cmd = "claude"
            else:
                raise ValueError("unknown provider")
            # Login must use the provider's canonical auth location. If it
            # inherited the current profile, /login would overwrite that
            # profile instead of creating a new account.
            sid = self.new_session(cmd, 120, 30, source="account-login",
                                   inherit_accounts=False)
            if provider == "claude":
                # The login command is deliberately sent only after the user
                # explicitly pressed the panel's Login button.
                threading.Timer(
                    2.0, lambda: self._send_text_to_session(
                        self.sessions.get(sid), "/login", submit=True
                    ) if self.sessions.get(sid) else None
                ).start()
            return json.dumps({"success": True, "sid": sid, "provider": provider,
                               "message": "登入頁籤已開啟；完成瀏覽器登入後回到面板按重新整理。"},
                              ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def account_capture_login(self, provider: str) -> str:
        """Capture credentials after an explicit login flow into a new profile."""
        try:
            cfg = self._account_config()
            ref = ACCOUNT_MANAGER.sync_current(cfg, provider)
            if not ref:
                raise ValueError("尚未偵測到已登入帳號")
            save_config(cfg)
            return json.dumps({"success": True, "ref": ref,
                               "state": self._account_state()[1]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def ai_providers(self) -> str:
        """The AI-CLI registry, for the web UI.

        The front end derives "is this an AI tab / which provider" from this
        instead of keeping its own literals, so supporting another CLI needs no
        change in index.html. `extra` are CLIs recognised without quota support.
        """
        try:
            return json.dumps({
                "providers": {
                    name: {"label": spec["label"],
                           "binaries": list(spec["binaries"]),
                           "installed": usage_probe.provider_installed(name),
                           "install": spec.get("install") or {}}
                    for name, spec in usage_probe.PROVIDER_SPECS.items()
                },
                "extra": sorted(OTHER_AI_CLI_TOOLS),
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"providers": {}, "extra": [], "error": str(e)})

    def provider_ready(self, cmd: str) -> str:
        """Is the AI CLI this command launches actually installed?

        Called before opening an AI tab. Without this check a missing binary
        makes the tab die instantly with "command not found", which reads as
        ShellFrame being broken rather than as a CLI that needs installing —
        exactly what a stock preset for a not-yet-installed CLI would do.
        Errors resolve to ready=True: a broken check must not block a tab.
        """
        try:
            provider = usage_probe.detect_ai(cmd or "")
            if not provider:
                return json.dumps({"ready": True})
            spec = usage_probe.PROVIDER_SPECS.get(provider) or {}
            return json.dumps({
                "ready": usage_probe.provider_installed(provider),
                "provider": provider,
                "label": spec.get("label", provider),
                "install": spec.get("install") or {},
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ready": True, "error": str(e)}, ensure_ascii=False)

    def install_provider(self, provider: str) -> str:
        """Open a shell tab and run that CLI's documented install command.

        Visible on purpose: the command runs in a real tab the user can read,
        interrupt and re-run, instead of a silent background install.
        """
        try:
            spec = usage_probe.PROVIDER_SPECS.get(provider)
            if not spec:
                raise ValueError("unknown provider")
            command = ((spec.get("install") or {}).get("command") or "").strip()
            if not command:
                raise ValueError(f"{spec.get('label', provider)} 沒有內建安裝指令，請參考官方文件")
            shell = "powershell" if IS_WIN else "bash"
            sid = self.new_session(shell, 120, 30, source="provider-install",
                                   inherit_accounts=False)
            session = self.sessions.get(sid)
            if session:
                threading.Timer(
                    1.5, lambda: self._send_text_to_session(session, command, submit=True)
                ).start()
            return json.dumps({
                "success": True, "sid": sid, "command": command,
                "message": f"已在新分頁執行 {spec.get('label', provider)} 安裝指令，"
                           f"裝完再開分頁即可。",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def account_usage_all(self, refresh: bool = False) -> str:
        """Every logged-in account's water-level, for the AI accounts panel.

        One reading per account so the panel can show all of them at once
        instead of only the active tab's. Accounts are queried in parallel —
        they use different tokens / CODEX_HOMEs, so there is no shared
        rate-limit budget between them — while usage_probe keeps a per-account
        cache so re-opening the panel does not re-hit the APIs.
        """
        try:
            # Normalised explicitly: a JS-side "false" arriving as a string
            # would make bool() force a refresh on every open — the fastest way
            # to get every account rate-limited.
            force = refresh is True or str(refresh).strip().lower() in ("true", "1")
            cfg = self._account_config()
            accounts = cfg.get("accounts") or {}
            jobs = []
            for provider in usage_probe.PROVIDERS:
                if provider not in account_manager.PROVIDERS:
                    # Quota but no switchable profiles: one implicit account,
                    # read with no credential override (see _single_account_state).
                    entry = self._single_account_state(provider)
                    if entry["logged_in"]:
                        jobs.append((provider, entry["current"]["id"],
                                     entry["current"], True))
                    continue
                # The account the provider is really signed in as right now:
                # it may read from the canonical location instead of a snapshot.
                current = (ACCOUNT_MANAGER.discover(provider) or {}).get("id")
                for item in (accounts.get("profiles") or {}).get(provider, []) or []:
                    ref = item.get("id")
                    if ref:
                        jobs.append((provider, ref, item, ref == current))

            def _one(job):
                provider, ref, item, is_current = job
                profiled = provider in account_manager.PROVIDERS
                data = usage_probe.account_usage(
                    provider,
                    env=ACCOUNT_MANAGER.env_for(provider, ref) if profiled else {},
                    ref=ref,
                    account=usage_probe.profile_account(item) if profiled
                    else (item.get("email") or ""),
                    force=force,
                    is_current=is_current,
                )
                data["is_current_login"] = is_current
                return provider, ref, data

            out = {provider: {} for provider in usage_probe.PROVIDERS}
            if jobs:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(4, len(jobs))
                ) as pool:
                    for provider, ref, data in pool.map(_one, jobs):
                        out[provider][ref] = data
            return json.dumps({"success": True, "providers": out}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def _probe_session_data(self, session: Session):
        provider = usage_probe.detect_ai(session.cmd)
        ref = session.account_refs.get(provider) if provider else None
        # A reattached tab from before per-account profiles has no ref (see
        # Session._account_refs_authoritative); env_for needs a real ref, so
        # fall back to the provider's global credentials instead of erroring.
        env = ACCOUNT_MANAGER.env_for(provider, ref) if ref else {}
        result = usage_probe.probe_data(session.cmd, env=env)
        profile = ACCOUNT_MANAGER.profile(
            self._account_config(), provider, ref
        ) if ref else None
        if profile:
            result["account"] = usage_probe.profile_account(profile)
        # Existing Codex tmux processes predate per-account CODEX_HOME and
        # therefore left their rollout JSONL in ~/.codex. Reuse that snapshot
        # only when this tab is the currently discovered canonical account;
        # a genuinely different profile must not display another account's
        # quota.
        if provider == "codex" and result.get("error") == "no_data" and ref:
            discovered = ACCOUNT_MANAGER.discover(provider) or {}
            if discovered.get("id") == ref:
                result = usage_probe.probe_data(session.cmd)
        return result

    def tab_usage(self, sid: str) -> str:
        """Web /usage slash command: return this tab's AI usage water-level.

        Detects claude/codex from the session's launch command and queries the
        matching local usage script. Result is shown in the web UI, never sent
        into the agent's conversation. Can take a few seconds (network/JSONRPC).
        """
        s = self.sessions.get(sid)
        if not s:
            return "此 tab 不存在或已關閉。"
        try:
            data = self._probe_session_data(s)
            return usage_probe.probe_text(data)
        except Exception as e:
            return f"用量查詢失敗：{e}"

    def tab_usage_brief(self, sid: str) -> str:
        """Structured usage for the inline top-bar pill (polled ~every 5 min).

        Follows the active tab: if it runs claude/codex, probe that provider;
        otherwise fall back to claude (account-global, no tab needed) so the
        indicator still shows something on non-AI tabs. Returns JSON.
        """
        s = self.sessions.get(sid)
        cmd = (s.cmd if s else "") or ""
        if usage_probe.detect_ai(cmd) is None:
            cmd = "claude"
        try:
            provider = usage_probe.detect_ai(cmd)
            result = self._probe_session_data(s) if s else usage_probe.probe_data(cmd)
            if s and provider:
                profile = ACCOUNT_MANAGER.profile(
                    self._account_config(), provider, s.account_refs.get(provider)
                )
                if profile:
                    result["account"] = " · ".join(
                        x for x in (profile.get("email"), profile.get("label")) if x
                    )
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"ai": None, "error": str(e)})

    def resize(self, sid: str, cols: int, rows: int):
        _dlog("resize", f"sid={sid} cols={cols} rows={rows}")
        s = self.sessions.get(sid)
        if s:
            s.resize(cols, rows)
            # The TG bridge reads this session through its own pyte screen —
            # leave that at the old height and every row below the new viewport
            # keeps its last paint forever (ghost text). `_live_tail` would then
            # sample ghosts instead of the live footer and the tab goes blind
            # (no delivery confirm, no busy guard, no stall watch).
            if self.bridge is not None:
                try:
                    self.bridge.resize_session(sid, cols, rows)
                except Exception:
                    _swallow("App.resize:bridge_resize")



















    def set_active_tab(self, sid: str) -> str:
        """Persist the user's active tab sid to config.json. localStorage in
        WKWebView can be cleared unpredictably across launches; this is the
        durable backup."""
        try:
            self._active_sid = sid
            # 立刻喚醒 pusher，讓切過去的 tab 把累積的背景 buffer 馬上刷出（不掉字）
            try:
                self._output_event.set()
            except Exception:
                _swallow("Api.set_active_tab:4086")
            update_config(lambda cfg: cfg.__setitem__("last_active_tab", sid))
            self._plugin_dispatch_session_change(sid)
            return json.dumps({"success": True})
        except Exception as e:
            return json.dumps({"success": False, "reason": str(e)})

    def get_active_tab(self) -> str:
        """Return the last persisted active tab sid as JSON (or empty)."""
        try:
            cfg = load_config()
            return json.dumps({"sid": cfg.get("last_active_tab", "") or ""})
        except Exception:
            return json.dumps({"sid": ""})

    def _plugin_api(self):
        import plugin_sdk
        return plugin_sdk.PluginHostAPI(
            get_active_sid=lambda: load_config().get("last_active_tab", "") or "",
            list_sessions=lambda: [
                {
                    "sid": sid,
                    "label": getattr(s, "_custom_label", None) or sid,
                    "cmd": s.cmd,
                    "alive": bool(s.alive),
                }
                for sid, s in self.sessions.items()
                if s.alive
            ],
            send_to_session=lambda sid, text: (
                self.sessions[sid].write(text) if sid in self.sessions else None
            ),
            config_dir=CONFIG_DIR,
        )

    def _plugins_reload(self):
        """Re-scan shellframe_plugins without restarting the app."""
        try:
            import plugin_sdk
            importlib.reload(plugin_sdk)
            cfg = load_config()
            enabled = _plugins_config(cfg).get("enabled") or []
            self._plugins = plugin_sdk.PluginRegistry(
                APP_DIR / "shellframe_plugins",
                self._plugin_api(),
                enabled_plugins=[str(name) for name in enabled],
            )
            self._plugins.load_all()
            _dlog("plugins", f"reloaded; {len(self._plugins.plugins)} plugin(s)")
        except Exception as e:
            self._plugins = None
            _dlog("plugins", f"reload failed: {e!r}")

    def _plugin_dispatch_session_open(self, sid: str, label: str):
        try:
            if self._plugins:
                self._plugins.dispatch_session_open(sid, label)
        except Exception as e:
            _dlog("plugins", f"session_open failed: {e!r}")

    def _plugin_dispatch_session_close(self, sid: str):
        try:
            if self._plugins:
                self._plugins.dispatch_session_close(sid)
        except Exception as e:
            _dlog("plugins", f"session_close failed: {e!r}")

    def _plugin_dispatch_session_change(self, sid: str):
        try:
            if self._plugins:
                self._plugins.dispatch_session_change(sid)
        except Exception as e:
            _dlog("plugins", f"session_change failed: {e!r}")

    def list_plugin_panels(self) -> str:
        """Return plugin metadata + injected HTML/CSS/JS for settings tabs."""
        if not self._plugins:
            return "[]"
        try:
            return json.dumps(self._plugins.collect_settings_panels(), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def plugin_sidebar_badges(self, sid: str) -> str:
        """Return concatenated HTML snippets rendered after a session label."""
        if not self._plugins:
            return ""
        try:
            return self._plugins.collect_sidebar_badges(sid)
        except Exception:
            return ""

    def plugin_action(self, plugin_name: str, action: str, args_json: str = "{}") -> str:
        if not self._plugins:
            return json.dumps({"ok": False, "message": "plugin registry not loaded"})
        try:
            args = json.loads(args_json) if args_json else {}
        except Exception:
            args = {}
        try:
            result = self._plugins.dispatch_action(plugin_name, action, args)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False)

    def marketplace_list(self) -> str:
        """List curated plugins and mark installed versions."""
        try:
            mk_path = APP_DIR / "shellframe_plugins" / "_marketplace.json"
            data = json.loads(mk_path.read_text(encoding="utf-8")) if mk_path.exists() else {"plugins": []}
            cfg = load_config()
            plugin_cfg = _plugins_config(cfg)
            installed_cfg = set(plugin_cfg.get("installed") or [])
            enabled_cfg = set(plugin_cfg.get("enabled") or [])
            installed = {
                p.manifest.name: p.manifest.version
                for p in (self._plugins.plugins if self._plugins else [])
            }
            for p in data.get("plugins", []):
                name = p.get("name")
                target = APP_DIR / "shellframe_plugins" / re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
                local_manifest = target / "manifest.json"
                local_version = ""
                if local_manifest.exists():
                    try:
                        local_version = json.loads(local_manifest.read_text(encoding="utf-8")).get("version", "")
                    except Exception:
                        local_version = ""
                bundled = bool(p.get("bundled"))
                p["installed"] = name in installed_cfg or (not bundled and local_manifest.exists())
                p["enabled"] = name in enabled_cfg
                p["installed_version"] = installed.get(name) or local_version
            return json.dumps(data, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "plugins": []}, ensure_ascii=False)

    def _marketplace_plugin_entry(self, name: str) -> dict:
        mk_path = APP_DIR / "shellframe_plugins" / "_marketplace.json"
        data = json.loads(mk_path.read_text(encoding="utf-8")) if mk_path.exists() else {"plugins": []}
        for p in data.get("plugins", []):
            if p.get("name") == name:
                return p
        return {}

    def _set_plugin_installed_enabled(self, name: str, installed: bool | None = None, enabled: bool | None = None) -> dict:
        cfg = load_config()
        _ensure_plugins_defaults(cfg)
        plugin_cfg = _plugins_config(cfg)
        installed_set = {str(v) for v in (plugin_cfg.get("installed") or [])}
        enabled_set = {str(v) for v in (plugin_cfg.get("enabled") or [])}
        if installed is not None:
            (installed_set.add if installed else installed_set.discard)(name)
        if enabled is not None:
            (enabled_set.add if enabled else enabled_set.discard)(name)
        if enabled is True:
            installed_set.add(name)
        plugin_cfg["installed"] = sorted(installed_set)
        plugin_cfg["enabled"] = sorted(enabled_set)
        cfg["plugins"] = plugin_cfg
        save_config(cfg)
        return cfg

    def marketplace_install(self, name: str, repo_url: str) -> str:
        """Install a plugin by cloning it into shellframe_plugins/<name>."""
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
        if not safe_name or safe_name != name:
            return json.dumps({"ok": False, "message": "invalid plugin name"})
        target = APP_DIR / "shellframe_plugins" / safe_name
        if target.exists():
            if not (target / "manifest.json").exists():
                return json.dumps({"ok": False, "message": f"{safe_name} already exists but is not a plugin"})
            self._set_plugin_installed_enabled(safe_name, installed=True, enabled=True)
            self._plugins_reload()
            return json.dumps({"ok": True, "message": f"enabled {safe_name}"})
        try:
            subprocess.check_output(
                ["git", "clone", "--depth", "1", repo_url, str(target)],
                stderr=subprocess.STDOUT,
                timeout=60,
            )
            self._set_plugin_installed_enabled(safe_name, installed=True, enabled=True)
            self._plugins_reload()
            return json.dumps({"ok": True, "message": f"installed {safe_name}"})
        except subprocess.CalledProcessError as e:
            return json.dumps({"ok": False, "message": e.output.decode("utf-8", errors="replace")[-400:]})
        except Exception as e:
            return json.dumps({"ok": False, "message": str(e)})

    def marketplace_enable(self, name: str, enabled: bool) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
        if not safe_name or safe_name != name:
            return json.dumps({"ok": False, "message": "invalid plugin name"})
        target = APP_DIR / "shellframe_plugins" / safe_name
        if not target.exists() or not (target / "manifest.json").exists():
            return json.dumps({"ok": False, "message": f"{safe_name} not installed"})
        self._set_plugin_installed_enabled(safe_name, installed=True, enabled=bool(enabled))
        self._plugins_reload()
        return json.dumps({"ok": True, "message": f"{'enabled' if enabled else 'disabled'} {safe_name}"})

    def marketplace_uninstall(self, name: str) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
        if not safe_name or safe_name != name:
            return json.dumps({"ok": False, "message": "invalid plugin name"})
        target = APP_DIR / "shellframe_plugins" / safe_name
        entry = self._marketplace_plugin_entry(safe_name)
        bundled = bool(entry.get("bundled"))
        if not bundled and (not target.exists() or not target.is_dir()):
            return json.dumps({"ok": False, "message": f"{safe_name} not installed"})
        try:
            self._set_plugin_installed_enabled(safe_name, installed=False, enabled=False)
            if target.exists() and target.is_dir() and not bundled:
                shutil.rmtree(target)
            self._plugins_reload()
            return json.dumps({"ok": True, "message": f"removed {safe_name}"})
        except Exception as e:
            return json.dumps({"ok": False, "message": str(e)})

    def open_local_file(self, path: str) -> str:
        """Open a file (or directory) in the OS default app.
        Used by the terminal Ctrl+Click handler."""
        try:
            if not path:
                return json.dumps({"success": False, "message": "empty path"})
            # Resolve relative paths against the active session's CWD if known
            p = Path(path).expanduser()
            if not p.is_absolute():
                # Try resolving relative to user's home — not perfect but
                # avoids accidentally opening files in shellframe's cwd
                p = Path.home() / p
            if not p.exists():
                return json.dumps({"success": False, "message": f"not found: {p}"})
            if IS_WIN:
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["/usr/bin/open", str(p)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(p)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return json.dumps({"success": True, "path": str(p)})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def open_url(self, url: str) -> str:
        """Open an http(s) URL in the OS default browser.
        Used by the terminal Ctrl+Click handler for hard-wrapped URLs that
        WebLinksAddon can't stitch across buffer lines."""
        try:
            if not url or not url.lower().startswith(("http://", "https://")):
                return json.dumps({"success": False, "message": "not an http url"})
            if IS_WIN:
                os.startfile(url)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["/usr/bin/open", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return json.dumps({"success": True})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def copy_text(self, text: str) -> str:
        """Copy text to system clipboard. macOS uses pbcopy, Windows uses
        clip.exe (UTF-16LE BOM expected for Unicode), Linux tries xclip/wl-copy."""
        try:
            if IS_WIN:
                # clip.exe accepts UTF-16LE; encode with BOM for safety
                p = subprocess.Popen(['clip'], stdin=subprocess.PIPE)
                p.communicate(text.encode('utf-16le'))
            else:
                # macOS: pbcopy. Linux fallback: try xclip then wl-copy.
                tool = 'pbcopy' if shutil.which('pbcopy') else (
                    'xclip' if shutil.which('xclip') else (
                        'wl-copy' if shutil.which('wl-copy') else None))
                if not tool:
                    return 'ERROR: no clipboard tool found'
                args = [tool, '-selection', 'clipboard'] if tool == 'xclip' else [tool]
                p = subprocess.Popen(args, stdin=subprocess.PIPE)
                p.communicate(text.encode('utf-8'))
            return 'ok'
        except Exception as e:
            return f'ERROR: {e}'

    def paste_text(self) -> str:
        """Read text from system clipboard."""
        try:
            if IS_WIN:
                # PowerShell Get-Clipboard handles Unicode properly
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-Command', 'Get-Clipboard -Raw'],
                    capture_output=True, text=True, timeout=3
                )
                # PowerShell adds a trailing newline; strip just one
                out = result.stdout
                return out.rstrip('\r\n') if out else ''
            else:
                tool = 'pbpaste' if shutil.which('pbpaste') else (
                    'xclip' if shutil.which('xclip') else (
                        'wl-paste' if shutil.which('wl-paste') else None))
                if not tool:
                    return ''
                args = [tool, '-selection', 'clipboard', '-o'] if tool == 'xclip' else [tool]
                result = subprocess.run(args, capture_output=True, text=True, timeout=3)
                return result.stdout
        except Exception as e:
            return ''

    def get_clipboard_files(self) -> str:
        """Get file paths from system clipboard (Finder copy).
        Returns JSON array of file paths, or empty array if no files."""
        try:
            if IS_WIN:
                # Windows: use PowerShell to read clipboard file list
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Clipboard -Format FileDropList | ForEach-Object { $_.FullName }"],
                    capture_output=True, text=True, timeout=3
                )
                paths = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
                return json.dumps(paths)
            else:
                # macOS: use osascript to read Finder clipboard
                result = subprocess.run(
                    ["osascript", "-e",
                     'try\n'
                     'set theFiles to (the clipboard as «class furl»)\n'
                     'POSIX path of theFiles\n'
                     'on error\n'
                     'try\n'
                     'set theList to (the clipboard as list)\n'
                     'set out to ""\n'
                     'repeat with f in theList\n'
                     'set out to out & POSIX path of f & linefeed\n'
                     'end repeat\n'
                     'out\n'
                     'on error\n'
                     '""\n'
                     'end try\n'
                     'end try'],
                    capture_output=True, text=True, timeout=3
                )
                paths = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
                # Validate paths exist
                paths = [p for p in paths if os.path.exists(p)]
                return json.dumps(paths)
        except Exception:
            return json.dumps([])

    def paths_exist(self, paths_json: str) -> str:
        """拖放路徑修復鏈用：回傳每個候選路徑是否真實存在。

        WebKit 對含非 ASCII 檔名的拖放，text/uri-list 可能只給到資料夾
        （檔名整段消失）——前端用 dt.files 的檔名把資料夾補回完整路徑後，
        必須經這裡驗證存在才敢注入，驗不過就退 blob fallback。"""
        try:
            paths = json.loads(paths_json or "[]")
            return json.dumps([bool(p) and os.path.exists(str(p)) for p in paths])
        except Exception:
            return json.dumps([])

    def drag_pasteboard_paths(self) -> str:
        """macOS：從 drag pasteboard 直讀拖曳檔案的真實路徑（drop 後仍在）。

        新版 macOS 的 Finder 拖曳放上 pasteboard 的是 file-reference URL
        （file:///.file/id=…），WebKit 轉不出 text/uri-list——DOM 端 types
        只剩 ["Files"]、完全拿不到路徑（2026-08-05 實案，js:drop 足跡）。
        原生 pasteboard 上這顆 URL 還在，NSURL.path() 會解回真實路徑
        （含 CJK 檔名）。"""
        if sys.platform != "darwin":
            return json.dumps([])
        try:
            from AppKit import NSPasteboard
            from Foundation import NSURL
            pb = NSPasteboard.pasteboardWithName_("Apple CFPasteboard drag")
            paths = []
            for it in (pb.pasteboardItems() or []):
                u = it.stringForType_("public.file-url")
                if not u:
                    continue
                try:
                    p = NSURL.URLWithString_(u).path()
                except Exception:
                    p = None
                if p:
                    paths.append(str(p))
            _dlog("drop", f"drag pasteboard → {paths!r}")
            return json.dumps(paths)
        except Exception as e:
            _dlog("drop", f"drag pasteboard read failed: {e}")
            return json.dumps([])

    def drag_pasteboard_snapshot(self) -> str:
        """同 drag_pasteboard_paths，但附上 pasteboard 的 changeCount。

        drag pasteboard **會留著上一次拖曳的內容**——實測沒有任何拖曳進行中，
        仍讀得到十分鐘前那次拖進來的 pptx。所以「路徑數量跟這次拖進來的檔案數
        對得上」不足以判定它是這次的：從瀏覽器拖 in-memory blob 的來源根本不寫
        這塊 pasteboard，數量又剛好都是 1 個，就會把殘留的舊檔附上去。
        changeCount 只在有人真的寫入時遞增，是唯一分得出「這次寫的」跟「上次留
        下的」的訊號。前端拿它跟最後一次採用過的值比對，相同就不信。"""
        if sys.platform != "darwin":
            return json.dumps({"paths": [], "change": -1})
        try:
            from AppKit import NSPasteboard
            from Foundation import NSURL
            pb = NSPasteboard.pasteboardWithName_("Apple CFPasteboard drag")
            change = int(pb.changeCount())
            paths = []
            for it in (pb.pasteboardItems() or []):
                u = it.stringForType_("public.file-url")
                if not u:
                    continue
                try:
                    p = NSURL.URLWithString_(u).path()
                except Exception:
                    p = None
                if p:
                    paths.append(str(p))
            return json.dumps({"paths": paths, "change": change})
        except Exception as e:
            _dlog("drop", f"drag pasteboard snapshot failed: {e}")
            return json.dumps({"paths": [], "change": -1})

    def drag_mark(self) -> str:
        """拖曳進入視窗 → 開始盯滑鼠左鍵，記下放開的那一刻。

        JS 拿不到「使用者放開滑鼠」的時間：drop 事件本身就是放開之後才被派送
        的，所以前端量到的 sinceDragOver 分不出兩種完全不同的情況——使用者拖著
        不動幾秒才放手，還是放手後 WebKit 卡在 dispatch 前面。這裡用
        NSEvent.pressedMouseButtons() 補上那一刻（20ms 輪詢，最多盯 60 秒），
        js_debug('drop') 進來時就能算出真正的感知延遲。
        觸控板 tap-drag 之類左鍵本來就沒按下的情況標成 unknown，不要謊報 0。"""
        if sys.platform != "darwin":
            return "skip"
        if getattr(self, "_drag_watch_running", False):
            return "already"
        self._drag_mouse_up_ts = 0.0
        self._drag_watch_running = True

        def _watch():
            try:
                from AppKit import NSEvent
                t0 = time.time()
                # 先確認現在真的按著左鍵，否則這次量測沒有意義
                pressed = False
                while time.time() - t0 < 1.0:
                    if int(NSEvent.pressedMouseButtons()) & 1:
                        pressed = True
                        break
                    time.sleep(0.02)
                if not pressed:
                    self._drag_mouse_up_ts = -1.0     # unknown
                    return
                while time.time() - t0 < 60.0:
                    if not (int(NSEvent.pressedMouseButtons()) & 1):
                        self._drag_mouse_up_ts = time.time()
                        return
                    time.sleep(0.02)
            except Exception as e:
                _dlog("drop", f"drag_mark watch failed: {e}")
                self._drag_mouse_up_ts = -1.0
            finally:
                self._drag_watch_running = False

        threading.Thread(target=_watch, daemon=True).start()
        return "ok"

    def js_debug(self, tag: str, msg: str) -> str:
        """前端事件落 debug log。拖放/貼上這類 WebKit 行為差異在後端毫無
        足跡（2026-08-05 drop 掉檔名查了半天），給前端一條 log 通道。"""
        extra = ""
        if tag == "drop":
            up = getattr(self, "_drag_mouse_up_ts", 0.0)
            if up and up > 0:
                extra = f"  sinceMouseUp={int((time.time() - up) * 1000)}ms"
            elif up == -1.0:
                extra = "  sinceMouseUp=unknown(左鍵未按下)"
            else:
                extra = "  sinceMouseUp=?(還沒放開就進 drop?)"
        _dlog(f"js:{tag}", str(msg)[:500] + extra)
        return "ok"

    def save_file_from_clipboard(self, data_url: str, filename: str) -> str:
        """Save a non-image file from clipboard data URL. Returns saved path."""
        try:
            _, encoded = data_url.split(",", 1)
            file_data = base64.b64decode(encoded)
            # Microsecond precision (`%f` = 6 digits) so multi-file pastes
            # within the same second get distinct paths; without this, every
            # blob written in the same second overwrote the previous one and
            # the JS attachFile dedup (matching on path equality) collapsed
            # the chips down to one — user thought only one file attached.
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            # Preserve original extension
            ext = Path(filename).suffix or '.bin'
            safe_name = Path(filename).stem[:50]
            path = CLAUDE_TMP / f"clipboard_{ts}_{safe_name}{ext}"
            path.write_bytes(file_data)
            return str(path)
        except Exception as e:
            return f"ERROR: {e}"

    def save_image(self, data_url: str) -> str:
        try:
            _, encoded = data_url.split(",", 1)
            img_data = base64.b64decode(encoded)
            # See save_file_from_clipboard for the multi-paste collision
            # rationale — same fix here.
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = CLAUDE_TMP / f"clipboard_{ts}.png"
            path.write_bytes(img_data)

            cutoff = time.time() - 3600
            for f in CLAUDE_TMP.glob("clipboard_*.png"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    _swallow("Api.save_image:4472")
            return str(path)
        except Exception as e:
            return f"ERROR: {e}"

    def read_clipboard_image(self) -> str:
        """Read an image from the system clipboard. Returns a data URL
        (data:image/png;base64,...) or '' if no image present.

        WKWebView's navigator.clipboard.read() is unreliable on macOS for
        image blobs (permission gating and incomplete MIME exposure), so the
        UI falls back here. We read NSPasteboard directly via PyObjC: try
        PNG first, fall back to TIFF and re-encode through NSBitmapImageRep
        if the source is e.g. a screenshot (TIFF on the pasteboard).
        """
        if IS_WIN:
            try:
                ps = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $img) { exit 2 }
$ms = New-Object System.IO.MemoryStream
try {
  $img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
  [Convert]::ToBase64String($ms.ToArray())
} finally {
  $ms.Dispose()
  $img.Dispose()
}
"""
                r = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-STA",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        ps,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if r.returncode != 0:
                    return ''
                b64 = (r.stdout or '').strip()
                if not b64:
                    return ''
                return 'data:image/png;base64,' + b64
            except Exception as e:
                try:
                    _dlog('clipboard', f'windows read_clipboard_image failed: {e}')
                except Exception:
                    _swallow("Api.read_clipboard_image:4527")
                return ''
        if platform.system() != 'Darwin':
            return ''
        try:
            from AppKit import (
                NSPasteboard,
                NSPasteboardTypePNG,
                NSPasteboardTypeTIFF,
                NSBitmapImageRep,
            )
            pb = NSPasteboard.generalPasteboard()
            data = pb.dataForType_(NSPasteboardTypePNG)
            if data is None:
                tiff = pb.dataForType_(NSPasteboardTypeTIFF)
                if tiff is None:
                    return ''
                rep = NSBitmapImageRep.imageRepWithData_(tiff)
                if rep is None:
                    return ''
                # NSBitmapImageFileTypePNG = 4
                data = rep.representationUsingType_properties_(4, None)
                if data is None:
                    return ''
            raw = bytes(data)
            return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
        except Exception as e:
            try:
                _dlog('clipboard', f'read_clipboard_image failed: {e}')
            except Exception:
                _swallow("Api.read_clipboard_image:4557")
            return ''

    def get_version(self) -> str:
        """Return current local version info."""
        try:
            return VERSION_FILE.read_text(encoding='utf-8')
        except:
            return json.dumps({"version": "unknown", "channel": "main"})

    def get_changelog(self) -> str:
        """Return changelog content."""
        changelog = APP_DIR / "CHANGELOG.md"
        try:
            return changelog.read_text(encoding='utf-8')
        except:
            return ""

    def get_latest_release_notes(self) -> str:
        """Return the current version + the top (latest) CHANGELOG section as
        markdown, for the startup 'what's new' popup. Body is just the first
        '## ...' block so we don't ship the whole 240KB changelog to JS."""
        try:
            version = json.loads(VERSION_FILE.read_text(encoding='utf-8')).get("version", "")
        except Exception:
            version = ""
        body, heading = "", ""
        try:
            text = (APP_DIR / "CHANGELOG.md").read_text(encoding='utf-8')
            lines = text.splitlines()
            start = None
            for i, ln in enumerate(lines):
                if ln.startswith("## "):
                    start = i
                    break
            if start is not None:
                heading = lines[start][3:].strip()
                section = []
                for ln in lines[start + 1:]:
                    if ln.startswith("## "):
                        break
                    section.append(ln)
                body = "\n".join(section).strip()
        except Exception:
            _swallow("Api.get_latest_release_notes:4601")
        return json.dumps({"version": version, "heading": heading, "body": body})

    def check_update(self) -> str:
        """Check GitHub for latest version. Returns JSON with local, remote, update_available."""
        try:
            local = json.loads(VERSION_FILE.read_text(encoding='utf-8')) if VERSION_FILE.exists() else {"version": "0.0.0"}
        except:
            local = {"version": "0.0.0"}

        # Remote version STRING — for display only (banner「vX 可更新」). Cache-
        # bust: raw.githubusercontent is Fastly-cached (~5 min) and serves a
        # STALE version.json right after a push.
        remote_ver = None
        try:
            bust = int(time.time())
            req = urllib.request.Request(
                f"{REPO_URL}?t={bust}",
                headers={"User-Agent": "shellframe",
                         "Cache-Control": "no-cache", "Pragma": "no-cache"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_ver = json.loads(resp.read().decode()).get("version")
        except Exception:
            pass

        # AUTHORITATIVE update signal: git commit SHA, NOT the version.json
        # semver (使用者 2026-08-03:「版號不能衝突，會讓其他機器檢測不到
        # update」）。並行 session 撞版號時，舊機器看到 remote_v == local_v →
        # 誤判沒更新、永遠不更新。改比對 remote main 的 commit SHA vs 本機
        # HEAD：版號變純顯示，撞號再也不影響偵測。git 不可用時退回 semver。
        def _git(*args, timeout=8):
            try:
                r = subprocess.run(["git", "-C", str(APP_DIR), *args],
                                   capture_output=True, text=True, timeout=timeout)
                return r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                return ""
        local_sha = _git("rev-parse", "HEAD")
        ls = _git("ls-remote", "origin", "-h", "refs/heads/main")
        remote_sha = ls.split()[0] if ls else ""
        if local_sha and remote_sha:
            has_update = remote_sha != local_sha
            if has_update:
                # 避免「本機領先遠端」誤報：remote_sha 是本機 HEAD 的祖先
                # ＝本機在前面（remote 物件在本機才判得出；不在＝遠端有新東西
                # → 維持 has_update）。免 fetch。
                anc = subprocess.run(
                    ["git", "-C", str(APP_DIR), "merge-base",
                     "--is-ancestor", remote_sha, "HEAD"],
                    capture_output=True, timeout=8)
                if anc.returncode == 0:
                    has_update = False
            return json.dumps({
                "local": local["version"],
                "remote": remote_ver or local["version"],
                "update_available": has_update,
                "remote_sha": remote_sha[:7],
            })

        # FALLBACK：git 不可用 → 回到 version.json semver 比對（舊行為）。
        if remote_ver is None:
            return json.dumps({"local": local["version"], "remote": None,
                               "update_available": False,
                               "error": "Could not reach GitHub"})
        def _vtuple(s):
            out = []
            for x in str(s).split("."):
                m = re.match(r"\d+", x)
                out.append(int(m.group()) if m else 0)
            return tuple(out)
        has_update = _vtuple(remote_ver) > _vtuple(local.get("version", "0"))
        return json.dumps({
            "local": local["version"],
            "remote": remote_ver,
            "update_available": has_update,
        })

    def do_update(self) -> str:
        """Full upgrade with defensive fallbacks so a half-bad state doesn't brick the install.

        Steps (each with its own recovery):
          1. Auto-stash dirty working tree (so local edits never block pull).
          2. `git pull --ff-only` → on failure, `git fetch && git reset --hard origin/main`
             (force-sync to remote; the stash in step 1 preserves user work).
          3. `python -m pip install -r requirements.txt` → on failure, recreate
             `.venv` from scratch and retry once.
          4. Refresh `.app` bundle (macOS). Never touches the source .app in
             APP_DIR, so if copy fails the user can still launch via CLI.

        Recovery hint (always returned on total failure):
          curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash
        """
        post_steps = []
        RECOVERY_CMD = ("curl -fsSL "
                        "https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh "
                        "| bash")
        try:
            # Pre-check: APP_DIR must be a git repo for `git pull` to work.
            # Users who installed via zip/download have no .git — auto-fallback
            # to install.sh (which converts a non-git dir into a git clone).
            if not (APP_DIR / ".git").exists():
                post_steps.append(".git missing — running install.sh to re-initialize")
                ok, msg = _run_install_sh()
                if ok:
                    try:
                        new_ver = json.loads(VERSION_FILE.read_text(encoding='utf-8'))["version"]
                    except Exception:
                        new_ver = "unknown"
                    post_steps.append(f"install.sh: {msg}")
                    return json.dumps({
                        "success": True,
                        "message": "Reinitialized via install.sh",
                        "version": new_ver,
                        "can_hot_reload": False,
                        "needs_restart": True,
                        "changed_files": [],
                        "post_steps": post_steps,
                    })
                else:
                    return json.dumps({
                        "success": False,
                        "message": f"install.sh failed: {msg}",
                        "post_steps": post_steps,
                        "recovery": RECOVERY_CMD,
                    })

            old_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(APP_DIR),
                capture_output=True, text=True, timeout=10
            ).stdout.strip()

            # ── Step 1: auto-stash dirty tree ────────────────────────
            try:
                status = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(APP_DIR),
                    capture_output=True, text=True, timeout=10
                )
                if status.stdout.strip():
                    stash_tag = f"shellframe-auto-{int(time.time())}"
                    stash = subprocess.run(
                        ["git", "stash", "push", "-u", "-m", stash_tag],
                        cwd=str(APP_DIR),
                        capture_output=True, text=True, timeout=15
                    )
                    if stash.returncode == 0:
                        post_steps.append(f"stashed local changes ({stash_tag})")
                    else:
                        post_steps.append(f"stash skipped: {stash.stderr.strip()[:80]}")
            except Exception as e:
                post_steps.append(f"stash check failed: {e}")

            # ── Step 2: pull with fallback to force-sync ─────────────
            pull_out = ""
            pull = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(APP_DIR),
                capture_output=True, text=True, timeout=45
            )
            if pull.returncode == 0:
                pull_out = pull.stdout.strip()
            else:
                post_steps.append(f"ff-only pull failed: {pull.stderr.strip()[:100]} — falling back to force-sync")
                fetch = subprocess.run(
                    ["git", "fetch", "origin", "main"],
                    cwd=str(APP_DIR),
                    capture_output=True, text=True, timeout=45
                )
                if fetch.returncode != 0:
                    return json.dumps({
                        "success": False,
                        "message": f"git fetch failed: {fetch.stderr.strip()[-200:]}",
                        "post_steps": post_steps,
                        "recovery": RECOVERY_CMD,
                    })
                reset = subprocess.run(
                    ["git", "reset", "--hard", "origin/main"],
                    cwd=str(APP_DIR),
                    capture_output=True, text=True, timeout=15
                )
                if reset.returncode != 0:
                    return json.dumps({
                        "success": False,
                        "message": f"git reset failed: {reset.stderr.strip()[-200:]}",
                        "post_steps": post_steps,
                        "recovery": RECOVERY_CMD,
                    })
                post_steps.append("force-synced to origin/main")
                pull_out = reset.stdout.strip()

            try:
                new_ver = json.loads(VERSION_FILE.read_text(encoding='utf-8'))["version"]
            except Exception:
                new_ver = "unknown"

            # Determine what changed
            changed_files = []
            needs_restart = False
            if old_head:
                diff = subprocess.run(
                    ["git", "diff", "--name-only", old_head, "HEAD"],
                    cwd=str(APP_DIR),
                    capture_output=True, text=True, timeout=10
                )
                changed_files = [f for f in diff.stdout.strip().split('\n') if f]
                needs_restart = any(
                    f.endswith('.py') or f == 'requirements.txt' or f == 'filters.json'
                    for f in changed_files
                )

            # ── Step 3: pip install with venv-recreate fallback ─────
            req_changed = 'requirements.txt' in changed_files
            venv_dir = APP_DIR / ".venv"
            req_file = str(APP_DIR / "requirements.txt")
            if req_changed or not _venv_has_pip(venv_dir):
                pip_ok, pip_msg = _pip_install_robust(venv_dir, req_file)
                post_steps.append(f"pip install: {pip_msg}")
                if not pip_ok:
                    post_steps.append("venv may be broken — try recovery command")
                    return json.dumps({
                        "success": False,
                        "message": f"pip install failed: {pip_msg}",
                        "version": new_ver,
                        "post_steps": post_steps,
                        "recovery": RECOVERY_CMD,
                    })

            # ── Step 4: refresh .app bundle (macOS only) ────────────
            if not IS_WIN:
                src_app = APP_DIR / "ShellFrame.app"
                if src_app.exists():
                    for dest_dir in [Path("/Applications"), Path.home() / "Applications"]:
                        dest = dest_dir / "ShellFrame.app"
                        try:
                            if dest.exists() or dest_dir.exists():
                                subprocess.run(
                                    ["rm", "-rf", str(dest)],
                                    capture_output=True, timeout=10
                                )
                                subprocess.run(
                                    ["cp", "-R", str(src_app), str(dest)],
                                    capture_output=True, timeout=10
                                )
                                ok, launcher_msg = _refresh_macos_app_launcher(dest)
                                post_steps.append(f".app launcher: {launcher_msg}")
                                post_steps.append(f".app copied to {dest}")
                                if dest_dir == Path("/Applications"):
                                    user_app = Path.home() / "Applications" / "ShellFrame.app"
                                    subprocess.run(
                                        ["rm", "-rf", str(user_app)],
                                        capture_output=True, timeout=10
                                    )
                                    post_steps.append(f"removed stale app copy: {user_app}")
                                break
                        except Exception as e:
                            post_steps.append(f".app copy to {dest} failed: {e}")
                            # Non-fatal — src .app in APP_DIR is still usable

            has_sessions = len(self.sessions) > 0
            return json.dumps({
                "success": True,
                "message": pull_out,
                "version": new_ver,
                "can_hot_reload": has_sessions and not needs_restart,
                "needs_restart": needs_restart,
                "changed_files": changed_files,
                "post_steps": post_steps,
                "platform": platform.system(),
            })
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": str(e),
                "post_steps": post_steps,
                "recovery": RECOVERY_CMD,
            })

    def restart_app(self) -> str:
        """Restart the app — spawns a new instance and exits the current one.
        tmux-backed sessions persist; the new instance reattaches on startup.

        Strategies (tried in order):
          macOS:   `open -n -a ShellFrame.app` → launcher script → python relaunch
          Windows: shellframe.bat in install dir → pythonw.exe main.py
          Linux:   launcher script → python relaunch
        """
        try:
            spawned = False
            err_msgs = []
            in_place_restart = False

            def _find_macos_app_path():
                candidates = [
                    Path("/Applications/ShellFrame.app"),
                    Path.home() / "Applications" / "ShellFrame.app",
                    APP_DIR / "ShellFrame.app",
                ]
                for c in candidates:
                    try:
                        if c.exists():
                            return c.resolve()
                    except Exception:
                        _swallow("restart_app._find_macos_app_path:4854")
                return None

            def _schedule_macos_app_relaunch(app_path: Path):
                """Launch via the .app bundle after this PID exits.

                Opening the bundle before the old process has quit can be a
                no-op on some LaunchServices states. Replacing the process via
                execv is reliable but loses the .app identity and shows up as
                Python in Dock, so keep the handoff outside this process and
                retry `open -n` after the old PID is gone.
                """
                pid = os.getpid()
                app = shlex.quote(str(app_path))
                log = shlex.quote(DEBUG_LOG)
                script = (
                    f"pid={pid}; app={app}; log={log}; "
                    "i=0; "
                    "while kill -0 \"$pid\" >/dev/null 2>&1 && [ $i -lt 80 ]; do "
                    "  i=$((i+1)); sleep 0.1; "
                    "done; "
                    "for j in 1 2 3; do "
                    "  if /usr/bin/open -n \"$app\" >/dev/null 2>&1; then "
                    "    echo \"$(date +%H:%M:%S.%3N) [restart] relaunched app=$app\" >> \"$log\"; "
                    "    exit 0; "
                    "  fi; "
                    "  sleep 1; "
                    "done; "
                    "echo \"$(date +%H:%M:%S.%3N) [restart] failed to relaunch app=$app\" >> \"$log\""
                )
                subprocess.Popen(
                    ["/bin/sh", "-c", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )

            if IS_WIN:
                if self.sessions:
                    return json.dumps({
                        "success": False,
                        "message": (
                            "Windows restart would terminate live terminal sessions. "
                            "Close or finish sessions first; update files are installed, "
                            "but Python/core changes apply after a manual restart."
                        ),
                        "preserves_sessions": False,
                    })
                # Strategy W1: shellframe.bat from install dir / user's local bin
                bat_candidates = [
                    APP_DIR / "ShellFrame.bat",
                    Path.home() / ".local" / "bin" / "shellframe.bat",
                ]
                bat_path = None
                for c in bat_candidates:
                    try:
                        if c.exists():
                            bat_path = c
                            break
                    except Exception:
                        _swallow("Api.restart_app:4914")
                if bat_path:
                    try:
                        DETACHED_PROCESS = 0x00000008
                        CREATE_NEW_PROCESS_GROUP = 0x00000200
                        subprocess.Popen(
                            ["cmd", "/c", "start", "", str(bat_path)],
                            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            close_fds=True,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"shellframe.bat failed: {e}")

                # Strategy W2: pythonw.exe main.py (windowless Python)
                if not spawned:
                    try:
                        # Try pythonw.exe (no console) first, fall back to python.exe
                        py_exe = sys.executable
                        if py_exe.endswith("python.exe"):
                            pyw = py_exe[:-10] + "pythonw.exe"
                            if Path(pyw).exists():
                                py_exe = pyw
                        DETACHED_PROCESS = 0x00000008
                        CREATE_NEW_PROCESS_GROUP = 0x00000200
                        subprocess.Popen(
                            [py_exe, str(APP_DIR / "main.py")],
                            cwd=str(APP_DIR),
                            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            close_fds=True,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"pythonw relaunch failed: {e}")
            else:
                if platform.system() == "Darwin":
                    app_path = _find_macos_app_path()
                    if app_path:
                        try:
                            _schedule_macos_app_relaunch(app_path)
                            spawned = True
                        except Exception as e:
                            err_msgs.append(f".app relaunch schedule failed: {e}")
                    else:
                        err_msgs.append("ShellFrame.app not found")
                else:
                    # Linux/no-.app fallback: replace the current Python
                    # process in-place after the RPC response has been written.
                    try:
                        def _exec_soon():
                            time.sleep(0.8)
                            try:
                                self.cleanup_all()
                            except Exception as e:
                                _dlog("restart", f"cleanup before exec failed: {e}")
                            try:
                                os.chdir(str(APP_DIR))
                            except Exception:
                                _swallow("restart_app._exec_soon:4976")
                            _dlog("restart", f"exec in-place python={sys.executable!r}")
                            os.execv(sys.executable, [sys.executable, str(APP_DIR / "main.py")])

                        threading.Thread(target=_exec_soon, daemon=True).start()
                        spawned = True
                        in_place_restart = True
                    except Exception as e:
                        err_msgs.append(f"in-place exec schedule failed: {e}")

                # Strategy 1: `open -n <absolute .app path>` — no `-a`, so
                # LaunchServices doesn't route by bundle ID. Passing the
                # path directly gives the spawned process full .app bundle
                # context (Info.plist / CFBundleName / icon), so Dock +
                # Cmd-Tab show "ShellFrame" with the right icon. The old
                # "exec launcher directly" strategy worked around a stale
                # bundle-id registration but lost the bundle wrapping, so
                # the new process showed up as a generic "Python" icon —
                # the user saw two Dock entries during restart and couldn't
                # tell which was shellframe. With `open -n <path>` the new
                # instance inherits the clicked app's identity properly.
                app_path = _find_macos_app_path()
                if not spawned and app_path:
                    try:
                        _schedule_macos_app_relaunch(app_path)
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"open -n <path> failed: {e}")

                # Strategy 2: `open -n -a` (resolves by bundle id via
                # LaunchServices). Fallback if the direct-path form above
                # isn't supported on this macOS build.
                if not spawned and app_path and platform.system() == "Darwin":
                    try:
                        subprocess.Popen(
                            ["/usr/bin/open", "-n", "-a", str(app_path)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"open -n -a failed: {e}")

                # Strategy 3: relaunch via current Python
                if not spawned and platform.system() != "Darwin":
                    try:
                        subprocess.Popen(
                            [sys.executable, str(APP_DIR / "main.py")],
                            cwd=str(APP_DIR),
                            start_new_session=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"python relaunch failed: {e}")

            if not spawned:
                return json.dumps({"success": False, "message": "; ".join(err_msgs) or "no spawn method worked"})

            if not in_place_restart:
                # Schedule exit so the response can return cleanly first
                def _exit_soon():
                    time.sleep(0.8)
                    try:
                        self.cleanup_all()  # detaches from tmux without killing
                    except Exception:
                        _swallow("restart_app._exit_soon:5043")
                    os._exit(0)
                threading.Thread(target=_exit_soon, daemon=True).start()
            return json.dumps({"success": True})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "message": str(e)})

    # ── Bridge API ──

    def start_bridge(self, bot_token: str, allowed_users_json: str,
                     prefix_enabled: bool, initial_prompt: str) -> str:
        """Start the global TG bridge. Registers all current sessions."""
        if self.bridge:
            self.bridge.stop()

        allowed = json.loads(allowed_users_json) if allowed_users_json else []
        # Pull STT settings from config so they survive across restarts
        cfg_now = load_config()
        bridge_cfg = cfg_now.get("bridge", {})
        config = TelegramBridgeConfig(
            bot_token=bot_token,
            allowed_users=[int(u) for u in allowed],
            prefix_enabled=prefix_enabled,
            initial_prompt=initial_prompt,
            stt_backend=bridge_cfg.get("stt_backend", "auto"),
        )

        self.bridge = TelegramBridge(
            bridge_id="tg",
            config=config,
            on_reload=self.hot_reload_bridge,
            on_close_session=self.close_session,
            on_restart=self.restart_app,
            on_check_update=self.check_update,
            on_new_session=lambda c: self.new_session(c, 200, 50),
            on_consume_init=self.consume_init_prompt_if_ready,
            on_model_info=self.get_session_model_info,
            on_agent_status=self._agent_status_snapshot,
            on_input_blocked=self.startup_dialog_blocking,
            on_answer_dialog=self.answer_startup_trust,
        )

        # Register existing sessions (skip bridge-disabled ones)
        for sid, s in self.sessions.items():
            if not getattr(s, 'alive', False):
                continue
            if not getattr(s, '_bridge_enabled', True):
                continue
            label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
            self.bridge.register_session(
                sid, label,
                lambda text, _s=s: _s.write(text),
                peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                prepare_fn=lambda _s=s: self._prepare_pane_for_input(_s),
                cmd=getattr(s, 'cmd', '') or '',
                cols=getattr(s, 'cols', 0), rows=getattr(s, 'rows', 0),
            )

        self.bridge.start()

        # Send initial prompt to first session (delayed to let CLI load)
        if initial_prompt and self.sessions:
            first_sid = list(self.sessions.keys())[0]
            # Track in sent_texts so echo gets filtered
            slot = self.bridge.slots.get(first_sid)
            if slot:
                slot.sent_texts.append(initial_prompt)
            def _send_prompt(sid=first_sid, text=initial_prompt):
                time.sleep(3)
                s = self.sessions.get(sid)
                if s:
                    s.write(text)
                    time.sleep(0.3)
                    s.write("\r")
            threading.Thread(target=_send_prompt, daemon=True).start()

        # Persist bridge config (preserve existing STT settings)
        cfg = load_config()
        prev_bridge = cfg.get("bridge", {})
        persisted_initial_prompt = initial_prompt
        if not persisted_initial_prompt and prev_bridge.get("initial_prompt"):
            persisted_initial_prompt = prev_bridge.get("initial_prompt", "")
        cfg["bridge"] = {
            "bot_token": bot_token,
            "allowed_users": [int(u) for u in allowed],
            "prefix_enabled": prefix_enabled,
            "initial_prompt": persisted_initial_prompt,
            "stt_backend": prev_bridge.get("stt_backend", "auto"),
            "stt_providers": prev_bridge.get("stt_providers", []),
        }
        save_config(cfg)
        self._persist_session_manifest()

        return json.dumps({"success": self.bridge.connected, **self.bridge.get_status()})

    # ── STT (Speech-to-Text) settings ──
    def stt_status(self) -> str:
        """Return diagnostic info: which STT backends are available."""
        cfg = load_config().get("bridge", {})
        remote_url = cfg.get("stt_remote_url", "")
        backend = cfg.get("stt_backend", "auto")
        try:
            status = TelegramBridge.stt_status(remote_url)
        except Exception as e:
            return json.dumps({"error": str(e)})
        status["backend"] = backend
        return json.dumps(status)

    def stt_save_settings(self, backend: str, providers_json: str) -> str:
        """Update STT backend + provider chain in config + live bridge."""
        cfg = load_config()
        bridge_cfg = cfg.get("bridge", {})
        if backend in ("auto", "plugin", "local", "remote", "off"):
            bridge_cfg["stt_backend"] = backend
        if providers_json is not None:
            try:
                providers = json.loads(providers_json) if providers_json else []
                if not isinstance(providers, list):
                    return json.dumps({"success": False, "message": "providers must be a list"})
                bridge_cfg["stt_providers"] = providers
            except json.JSONDecodeError as e:
                return json.dumps({"success": False, "message": f"invalid JSON: {e}"})
        cfg["bridge"] = bridge_cfg
        save_config(cfg)
        # Apply to running bridge
        if self.bridge:
            self.bridge.config.stt_backend = bridge_cfg.get("stt_backend", "auto")
        return json.dumps({"success": True})

    def stt_get_providers(self) -> str:
        """Return the configured provider chain (for the settings UI)."""
        cfg = load_config()
        return json.dumps((cfg.get("bridge", {}) or {}).get("stt_providers") or [])

    def stt_install_local(self) -> str:
        """Install whisper.cpp + download base model.

        Picks the right package manager per platform:
          macOS:   brew install whisper-cpp
          Windows: winget install ggerganov.whisper-cpp (or choco)
          Linux:   apt / dnf hint (no auto-install — too varied)

        Always downloads the GGML base model to LOCAL_MODEL_DIR regardless
        of platform."""
        try:
            steps = []

            if IS_WIN:
                # Windows: try winget first, then chocolatey
                winget = shutil.which("winget")
                choco = shutil.which("choco")
                installed = False
                if winget:
                    r = subprocess.run(
                        [winget, "install", "--id", "ggerganov.whisper.cpp",
                         "--accept-source-agreements", "--accept-package-agreements",
                         "--silent"],
                        capture_output=True, text=True, timeout=600,
                    )
                    steps.append({"step": "winget install whisper.cpp", "rc": r.returncode,
                                  "out": r.stdout[-500:], "err": r.stderr[-500:]})
                    if r.returncode == 0 or "already installed" in (r.stdout + r.stderr).lower():
                        installed = True
                if not installed and choco:
                    r = subprocess.run(
                        [choco, "install", "whisper-cpp", "-y"],
                        capture_output=True, text=True, timeout=600,
                    )
                    steps.append({"step": "choco install whisper-cpp", "rc": r.returncode,
                                  "out": r.stdout[-500:], "err": r.stderr[-500:]})
                    if r.returncode == 0 or "already installed" in (r.stdout + r.stderr).lower():
                        installed = True
                if not installed:
                    return json.dumps({
                        "success": False,
                        "message": "No winget or chocolatey found. Install whisper.cpp manually from https://github.com/ggml-org/whisper.cpp/releases and add it to PATH.",
                        "steps": steps,
                    })
            else:
                # macOS / Linux: prefer Homebrew
                brew = shutil.which("brew")
                if not brew:
                    hint = ""
                    if shutil.which("apt"):
                        hint = " (or try `sudo apt install whisper-cpp` if your distro packages it)"
                    return json.dumps({
                        "success": False,
                        "message": f"Homebrew not found. Install from https://brew.sh first.{hint}",
                    })
                r = subprocess.run([brew, "install", "whisper-cpp"], capture_output=True, text=True, timeout=600)
                steps.append({"step": "brew install whisper-cpp", "rc": r.returncode,
                              "out": r.stdout[-500:], "err": r.stderr[-500:]})
                if r.returncode != 0 and "already installed" not in (r.stderr + r.stdout).lower():
                    return json.dumps({
                        "success": False,
                        "message": f"brew install failed: {r.stderr[-300:]}",
                        "steps": steps,
                    })

            # Download model (cross-platform via urllib.request)
            model_dir = TelegramBridge.LOCAL_MODEL_DIR
            model_dir.mkdir(parents=True, exist_ok=True)
            model_path = model_dir / TelegramBridge.LOCAL_MODEL_NAME
            if not model_path.exists():
                steps.append({"step": "download model", "url": TelegramBridge.LOCAL_MODEL_URL})
                req = urllib.request.Request(TelegramBridge.LOCAL_MODEL_URL, headers={"User-Agent": "shellframe"})
                with urllib.request.urlopen(req, timeout=600) as resp, open(model_path, "wb") as out:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
            steps.append({"step": "model_path", "path": str(model_path), "exists": model_path.exists()})

            return json.dumps({
                "success": True,
                "message": "Local STT installed",
                "model": str(model_path),
                "steps": steps,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "message": str(e)})

    # ── UI 麥克風語音輸入（介面內錄音 → STT → 注入當前分頁）──
    # 錄音走原生 ffmpeg（mac=avfoundation / win=dshow / linux=alsa），不走
    # WKWebView getUserMedia——TCC 歸屬清楚（掛在 ShellFrame.app 下）、
    # 三平台同一條路，且轉出 16kHz mono WAV 正好是 whisper 要的格式。
    _MIC_MAX_SEC = 300

    @staticmethod
    def _mic_ffmpeg() -> str:
        return shutil.which("ffmpeg") or (
            "/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else "")

    @staticmethod
    def _parse_dshow_audio_devices(listing: str) -> list:
        """從 `ffmpeg -list_devices` 的 stderr 撈 dshow 音訊裝置名。"""
        return re.findall(r'"([^"]+)"\s*\(audio\)', listing or "")

    def mic_record_start(self) -> str:
        proc = getattr(self, "_mic_proc", None)
        if proc is not None and proc.poll() is None:
            return json.dumps({"ok": False, "reason": "busy"})
        ffmpeg = self._mic_ffmpeg()
        if not ffmpeg:
            return json.dumps({"ok": False, "reason": "no_ffmpeg"})
        import tempfile
        out = os.path.join(tempfile.gettempdir(), f"sf_mic_{int(time.time())}.wav")

        def _spawn(in_args):
            # stdin=PIPE：之後寫 'q' 讓 ffmpeg 優雅收尾（正確寫 WAV header）
            return subprocess.Popen(
                [ffmpeg, "-hide_banner", "-loglevel", "error", *in_args,
                 "-ac", "1", "-ar", "16000", "-t", str(self._MIC_MAX_SEC), "-y", out],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if sys.platform == "darwin":
            proc = _spawn(["-f", "avfoundation", "-i", ":default"])
            time.sleep(0.8)
            if proc.poll() is not None:  # 舊版 ffmpeg 不認 :default → 退 :0
                proc = _spawn(["-f", "avfoundation", "-i", ":0"])
                time.sleep(0.8)
        elif IS_WIN:
            try:
                r = subprocess.run(
                    [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                    capture_output=True, text=True, timeout=10)
                devices = self._parse_dshow_audio_devices((r.stderr or "") + (r.stdout or ""))
            except Exception:
                devices = []
            if not devices:
                return json.dumps({"ok": False, "reason": "no_mic_device"})
            proc = _spawn(["-f", "dshow", "-i", f"audio={devices[0]}"])
            time.sleep(0.8)
        else:
            proc = _spawn(["-f", "alsa", "-i", "default"])
            time.sleep(0.8)

        if proc.poll() is not None:
            detail = ""
            try:
                detail = (proc.stderr.read() or b"").decode("utf-8", "replace")[-400:]
            except Exception:
                pass
            _dlog("mic", f"record start failed: {detail!r}")
            return json.dumps({"ok": False, "reason": "record_failed", "detail": detail})
        self._mic_proc = proc
        self._mic_path = out
        _dlog("mic", f"recording → {out}")
        return json.dumps({"ok": True})

    def mic_record_stop(self, sid: str = "", cancel: bool = False) -> str:
        proc = getattr(self, "_mic_proc", None)
        path = getattr(self, "_mic_path", "") or ""
        self._mic_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if cancel:
            try:
                os.unlink(path)
            except OSError:
                pass
            return json.dumps({"ok": True, "cancelled": True})
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        # 16kHz mono s16 ≈ 32KB/s；小於這個幾乎必是沒收到聲音（含 TCC 拒絕）
        if size < 12000:
            try:
                os.unlink(path)
            except OSError:
                pass
            return json.dumps({"ok": False, "reason": "empty_audio"})
        return self._mic_transcribe_inject(path, sid)

    def mic_retry_transcribe(self, sid: str = "") -> str:
        """裝完 STT 後重轉上一段錄音（音檔在失敗時被保留）。"""
        path = getattr(self, "_mic_last_wav", "") or ""
        if not path or not os.path.exists(path):
            return json.dumps({"ok": False, "reason": "no_audio"})
        return self._mic_transcribe_inject(path, sid)

    def _mic_transcribe_inject(self, path: str, sid: str) -> str:
        br = self.bridge
        text = ""
        try:
            if br is not None:
                text = br._transcribe_voice(path)
            else:
                # TG bridge 沒開也能轉：借 TelegramBridge 的 STT 鏈（方法只用
                # config.stt_backend 與 class 屬性，不碰 bridge 執行狀態）
                import types as _t
                backend = (load_config().get("bridge", {}) or {}).get("stt_backend", "auto")
                shim = _t.SimpleNamespace(config=_t.SimpleNamespace(
                    stt_backend=backend, stt_remote_url=""))
                text = TelegramBridge._transcribe_voice(shim, path)
        except Exception as e:
            _dlog("mic", f"transcribe error: {e}")
        if not (text or "").strip():
            ready = False
            try:
                cfg = load_config().get("bridge", {}) or {}
                st = TelegramBridge.stt_status(cfg.get("stt_remote_url", ""))
                ready = bool(st["local"]["ready"] or st["plugin"]["ready"] or st["remote"]["ready"])
            except Exception:
                pass
            self._mic_last_wav = path  # 留檔給裝完後 mic_retry_transcribe
            return json.dumps({"ok": False, "reason": "stt_failed" if ready else "no_backend"})
        try:
            if br is not None:
                text = br._refine_transcript(text) or text
        except Exception:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        self._mic_last_wav = ""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"ok": True, "text": text, "injected": False})
        is_ai = bool(bridge_telegram._detect_ai(getattr(s, "cmd", "") or ""))
        if is_ai:
            # 下 tag 讓 AI 知道這是語音轉文字、要先解析語意再動作
            payload = MIC_STT_TAG + "\n" + text
            self._send_text_to_session(s, payload, submit=True)
        else:
            # 非 AI 分頁（shell 等）：純文字貼進輸入行、不送出，避免誤執行
            self._send_text_to_session(s, text, submit=False)
        return json.dumps({"ok": True, "text": text, "injected": True, "ai": is_ai})

    def mic_install_ffmpeg(self) -> str:
        """引導安裝 ffmpeg（錄音依賴）——一次到位，不只給指令。"""
        try:
            if IS_WIN:
                winget = shutil.which("winget")
                if not winget:
                    return json.dumps({"success": False,
                                       "message": "找不到 winget，請手動安裝 ffmpeg 後重試"})
                r = subprocess.run(
                    [winget, "install", "--id", "Gyan.FFmpeg",
                     "--accept-source-agreements", "--accept-package-agreements", "--silent"],
                    capture_output=True, text=True, timeout=900)
            elif sys.platform == "darwin":
                brew = shutil.which("brew") or (
                    "/opt/homebrew/bin/brew" if os.path.exists("/opt/homebrew/bin/brew") else "")
                if not brew:
                    return json.dumps({"success": False,
                                       "message": "找不到 Homebrew，請先裝 brew 或手動安裝 ffmpeg"})
                r = subprocess.run([brew, "install", "ffmpeg"],
                                   capture_output=True, text=True, timeout=1800)
            else:
                return json.dumps({"success": False,
                                   "message": "請用系統套件管理器安裝 ffmpeg（apt/dnf）"})
            ok = bool(self._mic_ffmpeg())
            return json.dumps({
                "success": ok,
                "message": "ffmpeg 已就緒" if ok else ((r.stderr or r.stdout) or "")[-300:],
            })
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def stop_bridge(self) -> str:
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
            # Remove from config
            cfg = load_config()
            cfg.pop("bridge", None)
            save_config(cfg)
            return json.dumps({"success": True})
        return json.dumps({"success": False, "message": "No bridge running"})

    # ── LINE bridge plugin ──
    def start_line_bridge(self, channel_access_token: str, channel_secret: str,
                          allowed_users_json: str, prefix_enabled: bool,
                          webhook_port: int, webhook_path: str,
                          public_webhook_url: str, delivery_mode: str = "push",
                          poll_path: str = "/line/poll",
                          forward_secret: str = "") -> str:
        """Start the LINE bridge plugin and persist its config."""
        if self.line_bridge:
            self.line_bridge.stop()
            self.line_bridge = None
        try:
            allowed = json.loads(allowed_users_json) if allowed_users_json else []
            allowed = [str(u).strip() for u in allowed if str(u).strip()]
            config = LineBridgeConfig(
                channel_access_token=channel_access_token,
                channel_secret=channel_secret,
                allowed_users=allowed,
                prefix_enabled=bool(prefix_enabled),
                webhook_port=int(webhook_port or 8787),
                webhook_path=webhook_path or "/line/webhook",
                public_webhook_url=public_webhook_url or "",
                delivery_mode=delivery_mode or "push",
                poll_path=poll_path or "/line/poll",
                forward_secret=forward_secret or "",
            )
            self.line_bridge = LineBridge(
                bridge_id="line",
                config=config,
                on_new_session=lambda c: self.new_session(c, 200, 50),
                on_close_session=self.close_session,
                on_consume_init=self.consume_init_prompt_if_ready,
                on_rename_session=self.rename_session,
                on_session_ready=self.is_session_ready_for_bridge,
                gateway_worker_cmd=SHELLFRAME_CODEX_CMD,
            )
            for sid, s in self.sessions.items():
                if not getattr(s, '_bridge_enabled', True):
                    continue
                label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
                self.line_bridge.register_session(
                    sid, label,
                    lambda text, _s=s: _s.write(text),
                    peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                )
            self.line_bridge.start()
            status = self.line_bridge.get_status()
            if not self.line_bridge.connected:
                message = status.get("message") or "LINE bridge failed to start"
                self.line_bridge = None
                return json.dumps({"success": False, "message": message, **status})

            cfg = load_config()
            cfg["line_bridge"] = {
                "channel_access_token": channel_access_token,
                "channel_secret": channel_secret,
                "allowed_users": allowed,
                "prefix_enabled": bool(prefix_enabled),
                "webhook_port": config.webhook_port,
                "webhook_path": config.webhook_path,
                "public_webhook_url": public_webhook_url or "",
                "delivery_mode": config.delivery_mode,
                "poll_path": config.poll_path,
                "forward_secret": forward_secret or "",
            }
            save_config(cfg)
            return json.dumps({"success": True, "exists": True, **status})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "message": str(e)})

    def stop_line_bridge(self) -> str:
        if self.line_bridge:
            self.line_bridge.stop()
            self.line_bridge = None
        cfg = load_config()
        cfg.pop("line_bridge", None)
        save_config(cfg)
        return json.dumps({"success": True})

    def get_line_bridge_status(self) -> str:
        if not self.line_bridge:
            return json.dumps({"exists": False})
        return json.dumps({"exists": True, **self.line_bridge.get_status()})

    def toggle_bridge(self) -> str:
        """Toggle pause/resume."""
        if not self.bridge:
            return json.dumps({"active": False, "exists": False})
        is_active = self.bridge.toggle_pause()
        return json.dumps({"active": is_active, "exists": True, **self.bridge.get_status()})

    def get_bridge_status(self) -> str:
        if not self.bridge:
            return json.dumps({"exists": False})
        return json.dumps({"exists": True, **self.bridge.get_status()})

    def set_session_bridge(self, sid: str, enabled: bool) -> str:
        """Enable/disable TG bridge for a specific session. Persists to config."""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False})
        s._bridge_enabled = bool(enabled)
        if self.bridge:
            if enabled:
                label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
                self.bridge.register_session(
                    sid, label,
                    lambda text, _s=s: _s.write(text),
                    peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                    prepare_fn=lambda _s=s: self._prepare_pane_for_input(_s),
                    cmd=getattr(s, 'cmd', '') or '',
                    cols=getattr(s, 'cols', 0), rows=getattr(s, 'rows', 0),
                )
            else:
                self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
        if self.line_bridge:
            if enabled:
                label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
                self.line_bridge.register_session(
                    sid, label,
                    lambda text, _s=s: _s.write(text),
                    peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                )
            else:
                self.line_bridge.unregister_session(sid)
        # Persist bridge-disabled sessions so they survive restart
        cfg = load_config()
        disabled = set(cfg.get("bridge_disabled_sessions", []))
        if enabled:
            disabled.discard(sid)
        else:
            disabled.add(sid)
        cfg["bridge_disabled_sessions"] = sorted(disabled)
        save_config(cfg)
        self._persist_session_manifest()
        return json.dumps({"success": True, "enabled": enabled})

    # ---------------------------------------------------------- glasses ---
    # The Agent Relay bridge (G2 glasses -> relay -> this Mac) can inject text
    # into a tab as if it were typed. Every tab here runs with
    # --dangerously-skip-permissions, so opening a tab to the glasses means
    # "anything I say out loud, on the street, runs on this machine". Hence:
    # allow list not deny list, off by default, and every change is recorded.
    #
    # Note what is NOT claimed: that many tabs cannot be opened at once. They
    # can — `sfctl glasses allow` takes several sids, and a shell loop would
    # work even if it did not. What holds is that no single control opens
    # everything, and that each grant lands in `config.glasses_audit` with its
    # source, so a mass grant is visible after the fact even though it is not
    # prevented. (2026-08-31: eleven tabs were opened in five seconds and the
    # only trace was a debug log that rolls.)

    def set_session_glasses(self, sid: str, enabled: bool, source: str = "") -> str:
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False, "message": f"no such session {sid}"})
        was = sid in set(load_config().get("glasses_allowed_sessions", []) or [])
        s._glasses_enabled = bool(enabled)
        cfg = load_config()
        allowed = set(cfg.get("glasses_allowed_sessions", []) or [])
        if enabled:
            allowed.add(sid)
        else:
            allowed.discard(sid)
        cfg["glasses_allowed_sessions"] = sorted(allowed)
        # "沒有全開按鈕" 只是 UI 的性質，不是強制的限制：一個 shell 迴圈五秒就能把
        # 每個分頁各開一次。實際發生過（2026-08-31 有支程式這樣做，11 個分頁全開了
        # 二十分鐘沒人發現）。擋不住的就要看得見——所以每一次變更都留痕，`sfctl
        # glasses` 會把最近幾筆印出來。
        # 只有真的改變狀態才留痕。不然對同一個 sid 連下 40 次 no-op deny
        # 就能把先前的紀錄全部擠出環狀緩衝——而授權本身完全沒動。
        # 「擋不住的就要看得見」，那個「看得見」不能這麼容易被洗掉。
        if was != bool(enabled):
            trail = list(cfg.get("glasses_audit") or [])
            trail.append({
                "ts": int(time.time()),
                "sid": sid,
                "enabled": bool(enabled),
                "source": source or "?",
                "label": getattr(s, "_custom_label", None) or "",
            })
            cfg["glasses_audit"] = trail[-40:]
        save_config(cfg)
        self._persist_session_manifest()
        _dlog("glasses", f"{sid} glasses_enabled={bool(enabled)} source={source or '?'}")
        return json.dumps({"success": True, "sid": sid, "enabled": bool(enabled)})

    _glasses_transcript_cache: dict = {}

    def _glasses_transcript(self, sid: str) -> str:
        hit = Api._glasses_transcript_cache.get(sid)
        if hit and time.time() - hit[0] < 20:
            return hit[1]
        s = self.sessions.get(sid)
        path = ""
        if s is not None:
            try:
                path = agent_status.resolve_transcript({
                    "cmd": getattr(s, "cmd", ""),
                    "cwd": getattr(s, "cwd", "~"),
                    "tmux_name": getattr(s, "_tmux_name", None),
                    "session_id": getattr(s, "session_id", None),
                    "transcript_hint": getattr(s, "_hook_transcript_path", None),
                }) or ""
            except Exception:
                _swallow(f"_glasses_transcript:{sid}")
                path = ""
        Api._glasses_transcript_cache[sid] = (time.time(), path)
        return path

    @staticmethod
    def _glasses_bridge_state():
        """(state_dict | None, age_seconds | None) from the bridge heartbeat."""
        try:
            with open(GLASSES_STATE_PATH) as f:
                st = json.load(f)
            return st, int(time.time() - os.path.getmtime(GLASSES_STATE_PATH))
        except Exception:
            return None, None

    def get_glasses_status(self) -> str:
        st, age = self._glasses_bridge_state()
        allowed = []
        for sid, s in self.sessions.items():
            if not getattr(s, "_glasses_enabled", False):
                continue
            allowed.append({
                "sid": sid,
                "label": getattr(s, "_custom_label", None) or (s.cmd.split()[0] if s.cmd else sid),
                "provider": _session_provider(s.cmd),
                "alive": bool(s.alive),
            })
        return json.dumps({
            "success": True,
            "allowed": allowed,
            "bridge": st,
            "bridgeAgeSec": age,
            "bridgeStale": age is None or age > GLASSES_STATE_STALE_S,
        })

    def _glasses_report(self) -> str:
        st, age = self._glasses_bridge_state()
        out = []
        if st is None:
            out.append("  bridge     未執行 —— 找不到 " + GLASSES_STATE_PATH)
            out.append("             眼鏡送得出去，但沒有人在這台機器上收")
        elif age is not None and age > GLASSES_STATE_STALE_S:
            out.append(f"  bridge     心跳停在 {age} 秒前 —— 多半掛了或被 launchd 停掉")
        else:
            r = st.get("relay") or {}
            out.append(f"  bridge     執行中（心跳 {age} 秒前，v{st.get('version', '?')}）")
            if r.get("reachable"):
                out.append(f"  relay      通  bridgeOnline={r.get('bridgeOnline')}  "
                           f"devices={r.get('devices')}  queued={r.get('queued')}")
            else:
                out.append(f"  relay      連不到  {r.get('error') or ''}")
            devs = st.get("devices") or []
            out.append(f"  devices    {len(devs)} 副眼鏡已配對"
                       + (f"（{devs[0].get('label', '')}）" if devs else ""))
        out.append("")
        allowed = [(sid, s) for sid, s in self.sessions.items()
                   if getattr(s, "_glasses_enabled", False)]
        if not allowed:
            out.append("  開放中     0 個分頁 —— fail-closed，眼鏡現在送不進任何地方")
        else:
            out.append(f"  開放中     {len(allowed)} 個分頁")
            for sid, s in allowed:
                label = getattr(s, "_custom_label", None) or (s.cmd.split()[0] if s.cmd else sid)
                mark = "\u25cf" if s.alive else "\u25cb"
                out.append(f"    {mark} {sid:<5s} {_session_provider(s.cmd):<7s} {label}")
        trail = (load_config().get("glasses_audit") or [])[-5:]
        if trail:
            out.append("")
            out.append("  最近的授權變更")
            for e in reversed(trail):
                when = time.strftime("%m-%d %H:%M", time.localtime(e.get("ts", 0)))
                verb = "開放" if e.get("enabled") else "收回"
                out.append(f"    {when}  {verb} {e.get('sid', '?'):<5s} "
                           f"{e.get('label', ''):<10s} via {e.get('source', '?')}")
        out.append("")
        out.append("  開放一個分頁：sfctl glasses allow <sid>      收回：sfctl glasses deny <sid>")
        return "\n".join(out)

    def reorder_sessions(self, order_json: str) -> str:
        """Reorder sessions. Updates TG bridge /1 /2 commands to match."""
        order = json.loads(order_json)
        if self.bridge:
            self.bridge.reorder_slots(order)
            self.bridge.refresh_commands()
        if self.line_bridge:
            self.line_bridge.reorder_slots(order)
        self._persist_session_manifest(order)
        return json.dumps({"success": True})

    def switch_bridge_session(self, sid: str) -> str:
        """Switch TG bridge active session and notify TG users."""
        if not self.bridge:
            return json.dumps({"success": False, "message": "No bridge"})
        try:
            self.bridge.switch_active_session(sid)
            return json.dumps({"success": True, "active_sid": sid})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def switch_line_bridge_session(self, sid: str) -> str:
        """Switch LINE bridge active session for forwarded / polled chats."""
        if not self.line_bridge:
            return json.dumps({"success": False, "message": "No LINE bridge"})
        try:
            self.line_bridge.switch_active_session(sid)
            return json.dumps({"success": True, "active_sid": sid})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def debug_bridge_info(self) -> str:
        """Debug: return bridge internals for troubleshooting."""
        if not self.bridge:
            return json.dumps({"bridge": False})
        b = self.bridge
        return json.dumps({
            "bridge": True,
            "slot_order": list(b._slot_order),
            "slots": list(b.slots.keys()),
            "user_active": {str(k): v for k, v in b._user_active.items()},
            "user_chat": {str(k): v for k, v in b._user_chat.items()},
            "active_sid": b.get_primary_active_sid(),
        })

    def hot_reload_bridge(self) -> str:
        """Hot-reload bridge_telegram module without restarting the app.
        Preserves PTY sessions — only restarts the TG bridge with new code."""
        global bridge_telegram, TelegramBridge, TelegramBridgeConfig
        try:
            # Save current bridge config + user routing state
            old_config = None
            was_active = False
            saved_offset = 0
            saved_user_active = {}
            saved_user_chat = {}
            saved_default_active = None
            saved_slot_state = {}  # sid -> {sent_texts, sent_responses, pending_menu}
            if self.bridge:
                was_active = self.bridge.active
                old_config = self.bridge.config
                saved_offset = self.bridge._offset
                saved_user_active = dict(getattr(self.bridge, '_user_active', {}) or {})
                saved_user_chat = dict(getattr(self.bridge, '_user_chat', {}) or {})
                saved_default_active = getattr(self.bridge, '_default_active_sid', None)
                # Snapshot per-slot state the echo filter / prefix-strip path
                # rely on. Without this, /reload wipes sent_texts + sent_responses
                # and the first few AI replies after reload leak back to TG as
                # echo because the filter has no recent-sent history to compare.
                for sid, slot in (getattr(self.bridge, 'slots', {}) or {}).items():
                    try:
                        saved_slot_state[sid] = {
                            'sent_texts': list(getattr(slot, 'sent_texts', []) or []),
                            'sent_responses': set(getattr(slot, 'sent_responses', set()) or []),
                            'pending_menu': bool(getattr(slot, 'pending_menu', False)),
                            'pending_menu_options': list(getattr(slot, 'pending_menu_options', []) or []),
                        }
                    except Exception:
                        _swallow("Api.hot_reload_bridge:5491")
                self.bridge.stop()

            # Reload the module
            bridge_telegram = importlib.reload(bridge_telegram)
            TelegramBridge = bridge_telegram.TelegramBridge
            TelegramBridgeConfig = bridge_telegram.TelegramBridgeConfig
            # Also reload filters
            bridge_telegram.reload_filters()

            # Restart bridge with same config if it was running
            if was_active and old_config:
                self.bridge = TelegramBridge(
                    bridge_id="tg",
                    config=old_config,
                    on_reload=self.hot_reload_bridge,
                    on_close_session=self.close_session,
                    on_restart=self.restart_app,
                    on_check_update=self.check_update,
                    on_new_session=lambda c: self.new_session(c, 200, 50),
                    on_consume_init=self.consume_init_prompt_if_ready,
                    on_model_info=self.get_session_model_info,
                    on_agent_status=self._agent_status_snapshot,
                    on_input_blocked=self.startup_dialog_blocking,
                    on_answer_dialog=self.answer_startup_trust,
                )
                # Preserve TG polling offset so it doesn't re-process the /reload command
                self.bridge._offset = saved_offset
                for sid, s in self.sessions.items():
                    if not getattr(s, 'alive', False):
                        continue
                    if not getattr(s, '_bridge_enabled', True):
                        continue
                    label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
                    self.bridge.register_session(
                        sid, label,
                        lambda text, _s=s: _s.write(text),
                        peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                        prepare_fn=lambda _s=s: self._prepare_pane_for_input(_s),
                        cmd=getattr(s, 'cmd', '') or '',
                        cols=getattr(s, 'cols', 0), rows=getattr(s, 'rows', 0),
                    )
                # Restore user routing state — filter out sids that disappeared
                self.bridge._user_active = {
                    uid: sid for uid, sid in saved_user_active.items()
                    if sid in self.bridge.slots
                }
                self.bridge._user_chat = saved_user_chat
                if saved_default_active and saved_default_active in self.bridge.slots:
                    self.bridge._default_active_sid = saved_default_active
                # Restore per-slot echo-filter state so the first few replies
                # after /reload don't leak preamble + user-message echo back
                # to TG (filter has nothing to compare against otherwise).
                for sid, snap in saved_slot_state.items():
                    slot = self.bridge.slots.get(sid)
                    if not slot:
                        continue
                    slot.sent_texts = list(snap.get('sent_texts', []))
                    slot.sent_responses = set(snap.get('sent_responses', set()))
                    slot.pending_menu = bool(snap.get('pending_menu', False))
                    slot.pending_menu_options = list(snap.get('pending_menu_options', []))
                self.bridge.start()
                return json.dumps({"success": True, "message": "Bridge reloaded and restarted", **self.bridge.get_status()})
            else:
                self.bridge = None
                return json.dumps({"success": True, "message": "Bridge module reloaded (bridge was not running)"})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "message": f"Reload failed: {e}"})

    def bridge_register_session(self, sid: str, label: str):
        """Register a new session with the running bridge."""
        if not self.bridge:
            return
        s = self.sessions.get(sid)
        if s and getattr(s, 'alive', False):
            self.bridge.register_session(
                sid, label,
                lambda text, _s=s: _s.write(text),
                peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                prepare_fn=lambda _s=s: self._prepare_pane_for_input(_s),
                cmd=getattr(s, 'cmd', '') or '',
                cols=getattr(s, 'cols', 0), rows=getattr(s, 'rows', 0),
            )
            self.bridge.refresh_commands()

    def report_ui_state(self, payload: str) -> str:
        """JS 回呼（ui_sessions 診斷）：webview 把它眼中的 tabs/labels 存回來。"""
        self._ui_state_report = str(payload or "")
        return "ok"

    def rename_session(self, sid: str, name: str, manual: bool = False) -> str:
        """Rename a session. Updates bridge label if connected. Persists to config.

        manual=True 代表「使用者自己取的名字」，只有這種才會取消 auto-slug。
        preset 開分頁時也會走這支（帶 preset 名稱），那是系統自動帶的
        ——把它也當成手動命名的話，preset 分頁會永遠停在 preset 名稱、
        再也不會被 auto-slug 依內容改名。
        """
        _dlog("lifecycle", f"rename_session sid={sid} name={name!r} manual={manual}")
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False})
        s._custom_label = name
        # 使用者自己取的名字不該再被 auto-slug 蓋掉。auto-slug 是在第一次送出
        # 訊息時用 haiku 依內容命名——新分頁一建立就跳命名 popup 之後，這個覆蓋
        # 會讓剛取的名字在第一句話之後消失，功能等於白做。
        # 但只認 manual：preset 帶進來的名稱也走這支，那不是使用者的決定。
        if manual and getattr(s, "_slug_pending", False):
            s._slug_pending = False
            _dlog("lifecycle", f"  {sid} 已手動命名，取消 auto-slug")
        if self.bridge and sid in self.bridge.slots:
            self.bridge.slots[sid].label = name
            self.bridge.refresh_commands()
        if self.line_bridge and sid in self.line_bridge.slots:
            self.line_bridge.slots[sid].label = name
        # 即時推給 webview——sfctl/TG 改名原本只更新後端，UI 靠 1.5s 輪詢
        # 撿；輪詢若失效（JS 例外、pywebview 斷橋）tab 名就永遠停在舊值，
        # 造成「後端說改了、畫面沒變」各說各話。直接推一次，輪詢當備援。
        if getattr(self, "_window", None):
            try:
                self._window.evaluate_js(
                    f'window.__sfApplyLabel && '
                    f'__sfApplyLabel({json.dumps(sid)}, {json.dumps(name)})')
            except Exception:
                _swallow("rename_session.push_label")
        # Persist
        cfg = load_config()
        labels = cfg.get("session_labels", {})
        labels[sid] = name
        cfg["session_labels"] = labels
        save_config(cfg)
        self._persist_session_manifest()
        return json.dumps({"success": True})

    def bridge_unregister_session(self, sid: str):
        """Remove a session from the bridge."""
        if self.bridge:
            self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
        if self.line_bridge:
            self.line_bridge.unregister_session(sid)

    # ── Remote control (sfctl) ──

    _CMD_FILE = str(TMP_DIR / "shellframe_cmd.json")
    _CMD_DIR = str(TMP_DIR / "shellframe_cmds")
    _RESULT_FILE = str(TMP_DIR / "shellframe_result.json")

    def _start_command_watcher(self):
        """Watch for commands from sfctl CLI (file-based IPC)."""
        def _command_paths() -> list[Path]:
            paths = []
            legacy = Path(self._CMD_FILE)
            if legacy.exists():
                paths.append(legacy)
            cmd_dir = Path(self._CMD_DIR)
            try:
                cmd_dir.mkdir(parents=True, exist_ok=True)
                paths.extend(sorted(cmd_dir.glob("*.json")))
            except OSError as e:
                _dlog("sfctl", f"cmd dir unavailable: {e}")
            return paths

        def watcher():
            _dlog("sfctl", f"watcher started cmd_file={self._CMD_FILE} cmd_dir={self._CMD_DIR}")
            while True:
                try:
                    time.sleep(0.5)
                    paths = _command_paths()
                    if not paths:
                        continue
                    for path in paths:
                        try:
                            with open(path, encoding='utf-8') as f:
                                cmd_data = json.load(f)
                            path.unlink(missing_ok=True)
                        except (json.JSONDecodeError, IOError, OSError) as e:
                            _dlog("sfctl", f"failed reading cmd path={path}: {e}")
                            try:
                                path.unlink(missing_ok=True)
                            except OSError:
                                _swallow("_start_command_watcher.watcher:5638")
                            continue

                        # Ignore stale commands (older than 30s)
                        if time.time() - cmd_data.get("ts", 0) > 30:
                            _dlog("sfctl", f"ignored stale cmd={cmd_data.get('cmd')!r}")
                            continue

                        cmd = cmd_data.get("cmd", "")
                        args = cmd_data.get("args", {})
                        _dlog("sfctl", f"exec cmd={cmd!r}")
                        try:
                            result = self._execute_sfctl(cmd, args)
                        except Exception as e:
                            import traceback
                            _dlog("sfctl", f"execute crashed cmd={cmd!r}: {e}\n{traceback.format_exc()}")
                            result = {"success": False, "message": f"sfctl crashed: {e}"}

                        try:
                            result_file = str(cmd_data.get("result_file") or self._RESULT_FILE)
                            tmp = result_file + ".tmp"
                            with open(tmp, "w", encoding='utf-8') as f:
                                json.dump(result, f, ensure_ascii=False)
                                f.flush()
                                try:
                                    os.fsync(f.fileno())
                                except OSError:
                                    _swallow("_start_command_watcher.watcher:5665")
                            os.replace(tmp, result_file)
                        except IOError as e:
                            _dlog("sfctl", f"failed writing result: {e}")
                except Exception as e:
                    # Never let the remote-control thread die; TG recovery
                    # depends on sfctl staying alive after bad edge cases.
                    import traceback
                    _dlog("sfctl", f"watcher loop crashed: {e}\n{traceback.format_exc()}")
        threading.Thread(target=watcher, daemon=True).start()

    def _start_api_server(self):
        """Start the optional local HTTP API (opt-in via config api_server.enabled).

        Loopback + token + IP whitelist. Wraps _execute_sfctl. A blank token is
        auto-generated and persisted on first enable so the surface is never
        unauthenticated. Failures (missing module, bind error) are non-fatal."""
        if getattr(self, "_api_httpd", None):
            return  # already running (hot re-enable from Settings)
        try:
            cfg = (load_config().get("api_server") or {})
        except Exception:
            cfg = {}
        if not cfg.get("enabled"):
            return
        token = (cfg.get("token") or "").strip()
        if not token:
            import secrets
            token = secrets.token_urlsafe(24)
            try:
                full = load_config()
                full.setdefault("api_server", {})["token"] = token
                save_config(full)
            except Exception as e:
                _dlog("api", f"failed persisting generated token: {e}")
        try:
            import api_server
        except Exception as e:
            _dlog("api", f"api_server import failed: {e}")
            return
        # Stamp the live event bus onto the bridge so signal transitions
        # (RED/YELLOW/GREEN) surface to API clients via GET /events.
        self.api_event_bus = api_server.EVENT_BUS
        try:
            ver = json.loads((Path(__file__).parent / "version.json").read_text()).get("version", "0")
        except Exception:
            ver = "0"
        httpd, _thread = api_server.start(
            self._execute_sfctl,
            host=cfg.get("host", "127.0.0.1"),
            port=cfg.get("port", 8765),
            token=token,
            allowed_ips=cfg.get("allowed_ips") or ["127.0.0.1", "::1"],
            version=ver,
            log=lambda m: _dlog("api", m),
        )
        self._api_httpd = httpd  # None on bind failure → info shows not running

    def get_api_server_info(self) -> str:
        """Settings UI: current Local HTTP API state."""
        try:
            cfg = (load_config().get("api_server") or {})
        except Exception:
            cfg = {}
        host = cfg.get("host", "127.0.0.1")
        port = cfg.get("port", 8765)
        return json.dumps({
            "enabled": bool(cfg.get("enabled")),
            "host": host,
            "port": port,
            "token": cfg.get("token") or "",
            "running": bool(getattr(self, "_api_httpd", None)),
            "docs_url": f"http://{host}:{port}/docs",
        })

    def set_api_server_enabled(self, enabled: bool) -> str:
        """Settings UI toggle — hot start/stop, no restart needed.
        Enabling auto-generates and persists a token if blank (fail-closed
        stays intact: _start_api_server never serves without a token)."""
        try:
            full = load_config()
            full.setdefault("api_server", {})["enabled"] = bool(enabled)
            save_config(full)
        except Exception as e:
            _dlog("api", f"failed saving api_server.enabled: {e}")
        if enabled:
            self._start_api_server()
        else:
            httpd = getattr(self, "_api_httpd", None)
            self._api_httpd = None
            if httpd:
                try:
                    httpd.shutdown()
                    httpd.server_close()
                except Exception as e:
                    _dlog("api", f"api_server shutdown failed: {e}")
        return self.get_api_server_info()

    # ── Frame Link（跨機配對）────────────────────────────────────────────
    def _start_frame_link(self):
        """Create the FrameLink instance (always) and start its listener when
        config frame_link.enabled is true. Safe to call again after a settings
        toggle — start() is idempotent."""
        if getattr(self, "frame_link", None) is None:
            try:
                ver = json.loads(VERSION_FILE.read_text()).get("version", "0")
            except Exception:
                ver = "0"
            self.frame_link = frame_link_mod.FrameLink(
                get_config=load_config,
                update_config=update_config,
                execute_fn=self._execute_sfctl,
                notify=self._on_link_event,
                log=lambda m: _dlog("link", m),
                version=ver,
            )
        try:
            if load_config().get("frame_link", {}).get("enabled"):
                self.frame_link.start()
        except Exception as e:
            _dlog("link", f"start failed: {e}")

    def _on_link_event(self, ev: dict):
        """Push a Frame Link event (message / file / paired / peer_status)
        into the webview so the panel updates live."""
        try:
            if self._window:
                payload = json.dumps(ev, ensure_ascii=False)
                self._window.evaluate_js(
                    f"window._sfLinkEvent && _sfLinkEvent({payload})")
        except Exception:
            _swallow("_on_link_event")

    def _link(self):
        if getattr(self, "frame_link", None) is None:
            self._start_frame_link()
        return self.frame_link

    def link_status(self) -> str:
        try:
            return json.dumps(self._link().status(), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"enabled": False, "running": False,
                               "peers": [], "error": str(e)})

    def link_set_enabled(self, enabled: bool) -> str:
        def fn(cfg):
            cfg.setdefault("frame_link", {})["enabled"] = bool(enabled)
        update_config(fn)
        fl = self._link()
        if enabled:
            fl.start()
        else:
            fl.stop()
        return self.link_status()

    def link_set_name(self, name: str) -> str:
        def fn(cfg):
            cfg.setdefault("frame_link", {})["frame_name"] = str(name or "").strip()[:60]
        update_config(fn)
        return self.link_status()

    def link_pair_begin(self, mode: str = "duplex") -> str:
        return json.dumps(self._link().pairing_begin(mode), ensure_ascii=False)

    def link_pair_cancel(self) -> str:
        return json.dumps(self._link().pairing_cancel())

    def link_join(self, host: str, port: int, code: str) -> str:
        return json.dumps(self._link().join(host, port, code), ensure_ascii=False)

    def link_unpair(self, peer_id: str) -> str:
        return json.dumps(self._link().unpair(peer_id))

    def link_update_peer(self, peer_id: str, host: str, port: int) -> str:
        return json.dumps(self._link().update_peer(peer_id, host, port),
                          ensure_ascii=False)

    def link_ping(self, peer_id: str) -> str:
        return json.dumps(self._link().ping_peer(peer_id), ensure_ascii=False)

    def link_remote_tabs(self, peer_id: str) -> str:
        return json.dumps(self._link().remote_info(peer_id), ensure_ascii=False)

    def link_remote_peek(self, peer_id: str, sid: str, lines: int = 120) -> str:
        return json.dumps(self._link().remote_peek(peer_id, sid, lines),
                          ensure_ascii=False)

    def link_remote_stream(self, peer_id: str, sid: str, since: int = -1) -> str:
        return json.dumps(self._link().remote_stream(peer_id, sid, since),
                          ensure_ascii=False)

    def link_remote_send(self, peer_id: str, sid: str, text: str,
                         submit: bool = True) -> str:
        return json.dumps(self._link().remote_send(peer_id, sid, text, submit),
                          ensure_ascii=False)

    def link_remote_input(self, peer_id: str, sid: str, data: str) -> str:
        return json.dumps(self._link().remote_input(peer_id, sid, data),
                          ensure_ascii=False)

    def link_remote_resize(self, peer_id: str, sid: str, cols: int, rows: int) -> str:
        return json.dumps(self._link().remote_resize(peer_id, sid, cols, rows),
                          ensure_ascii=False)

    def link_remote_paste(self, peer_id: str, sid: str, data_url: str,
                          filename: str = "paste.png") -> str:
        return json.dumps(self._link().remote_paste(peer_id, sid, data_url, filename),
                          ensure_ascii=False)

    def link_remote_new(self, peer_id: str, cmd: str = "claude") -> str:
        return json.dumps(self._link().remote_new(peer_id, cmd),
                          ensure_ascii=False)

    def link_remote_close(self, peer_id: str, sid: str) -> str:
        return json.dumps(self._link().remote_close(peer_id, sid),
                          ensure_ascii=False)

    def link_message(self, peer_id: str, text: str) -> str:
        return json.dumps(self._link().send_message(peer_id, text),
                          ensure_ascii=False)

    def link_send_file(self, peer_id: str, path: str) -> str:
        return json.dumps(self._link().send_file(peer_id, path),
                          ensure_ascii=False)

    def link_recent_events(self, limit: int = 100) -> str:
        try:
            return json.dumps(self._link().recent_events(int(limit)),
                              ensure_ascii=False)
        except Exception:
            return "[]"

    def link_pick_file(self) -> str:
        """Native open-file dialog for「傳檔案給 peer」."""
        try:
            dialog_type = getattr(webview, "OPEN_DIALOG", None)
            if dialog_type is None:
                dialog_type = webview.FileDialog.OPEN
            result = self._window.create_file_dialog(dialog_type,
                                                     allow_multiple=False)
            if result:
                path = result[0] if isinstance(result, (list, tuple)) else result
                return json.dumps({"success": True, "path": str(path)})
            return json.dumps({"success": False, "message": "cancelled"})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def _execute_sfctl(self, cmd: str, args: dict = None) -> dict:
        """Execute a sfctl command and return result dict."""
        args = args or {}
        if cmd == "new_session":
            try:
                preset_cmd = args.get("cmd", "claude")
                cols = args.get("cols", 200)
                rows = args.get("rows", 50)
                source = args.get("source", "sfctl")
                handoff = bool(args.get("handoff", False))
                sid = self.new_session(preset_cmd, cols, rows, source=source, handoff=handoff)
                return {
                    "success": True,
                    "message": f"Created session {sid}",
                    "details": {"sid": sid, "cmd": preset_cmd},
                }
            except Exception as e:
                return {"success": False, "message": f"Failed: {e}"}

        elif cmd == "close_session":
            try:
                sid = args.get("sid", "")
                if not sid:
                    return {"success": False, "message": "No sid provided"}
                self.close_session(
                    sid,
                    reason=args.get("reason", "sfctl"),
                    handoff=bool(args.get("handoff", False)),
                    summary_path=args.get("summary_path", "") or "",
                )
                return {"success": True, "message": f"Closed {sid}"}
            except Exception as e:
                return {"success": False, "message": f"Failed: {e}"}

        elif cmd == "restart":
            try:
                result_json = self.restart_app()
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                return {
                    "success": result.get("success", False),
                    "message": result.get("message", "Restart triggered"),
                    "details": {k: v for k, v in result.items() if k not in ("success", "message")},
                }
            except Exception as e:
                return {"success": False, "message": f"Restart failed: {e}"}

        elif cmd == "reload":
            try:
                result_json = self.hot_reload_bridge()
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                return {
                    "success": result.get("success", False),
                    "message": result.get("message", "Reload completed"),
                    "details": {
                        "state": result.get("state", "unknown"),
                        "bot": result.get("bot", ""),
                        "sessions": result.get("sessions", 0),
                    }
                }
            except Exception as e:
                return {"success": False, "message": f"Reload failed: {e}"}

        elif cmd == "status":
            if not self.bridge:
                return {
                    "success": True,
                    "message": "Bridge not running",
                    "details": {"state": "stopped", "sessions": len(self.sessions)}
                }
            status = self.bridge.get_status()
            return {
                "success": True,
                "message": f"Bridge {status.get('state', 'unknown')} — @{status.get('bot', '?')}",
                "details": {
                    "state": status.get("state"),
                    "bot": status.get("bot"),
                    "sessions": status.get("sessions", 0),
                    "paused": status.get("paused", False),
                }
            }

        elif cmd == "list":
            # List all sessions with sid + label + alive state
            sessions_info = []
            for sid, s in self.sessions.items():
                sessions_info.append({
                    "sid": sid,
                    "label": getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid),
                    "cmd": s.cmd,
                    "alive": s.alive,
                    "bridge_enabled": getattr(s, '_bridge_enabled', True),
                    "glasses_enabled": getattr(s, '_glasses_enabled', False),
                    "provider": _session_provider(s.cmd),
                    # Frame Link 無縫遠端分頁：對齊對方 PTY 尺寸，alt-screen TUI
                    # （claude/codex）才不會因 cols/rows 不同而畫面錯位。
                    "cols": getattr(s, 'cols', 0),
                    "rows": getattr(s, 'rows', 0),
                    # the glasses bridge needs this to find a codex rollout:
                    # codex has no --session-id, so the only reliable link from
                    # tab to transcript is the fd the process holds open.
                    # Resolved here (cached) rather than in the bridge so the
                    # lsof logic lives in exactly one place, and only for tabs
                    # that are actually open to the glasses — normally zero.
                    "tmux_name": getattr(s, '_tmux_name', None) or "",
                    "transcript": (self._glasses_transcript(sid)
                                   if getattr(s, '_glasses_enabled', False) else ""),
                })
            return {
                "success": True,
                "message": f"{len(sessions_info)} sessions",
                "details": {"sessions": sessions_info},
            }

        elif cmd == "glasses_status":
            st = json.loads(self.get_glasses_status())
            return {"success": True,
                    "message": f"{len(st.get('allowed') or [])} allowed",
                    "details": st}

        elif cmd == "glasses":
            action = str(args.get("action") or "status").lower()
            sids = [x for x in (args.get("sids") or []) if x]
            if action in ("allow", "deny"):
                if not sids:
                    return {"success": False, "message": f"glasses {action} 需要至少一個 sid"}
                changed, missing = [], []
                source = str(args.get("source") or "sfctl")
                for sid in sids:
                    r = json.loads(self.set_session_glasses(sid, action == "allow", source))
                    (changed if r.get("success") else missing).append(sid)
                if not changed:
                    return {"success": False,
                            "message": f"沒有這些分頁：{', '.join(missing)}"}
                verb = "開放" if action == "allow" else "收回"
                msg = f"{verb} {', '.join(changed)}"
                if missing:
                    msg += f"（略過不存在的 {', '.join(missing)}）"
            elif action == "status":
                msg = "glasses status"
            else:
                return {"success": False, "message": f"unknown action {action!r}"}
            return {"success": True, "message": msg,
                    "details": {"text": self._glasses_report()}}

        elif cmd == "roster":
            roster = self._agent_roster_config(load_config())
            roles = []
            for role, entry in roster.items():
                roles.append({
                    "role": role,
                    "label": entry.get("label", role),
                    "agent_code": entry.get("agent_code", ""),
                    "responsibility": entry.get("responsibility", ""),
                    "cmd": entry.get("cmd", ""),
                })
            return {
                "success": True,
                "message": f"{len(roles)} roles",
                "details": {"roles": roles},
            }

        elif cmd == "delegate":
            try:
                return self.delegate_task(args.get("role", ""), args.get("task", ""))
            except Exception as e:
                return {"success": False, "message": f"Delegate failed: {e}"}

        elif cmd == "agent_event":
            try:
                return self._on_agent_event(args)
            except Exception as e:
                return {"success": False, "message": f"agent_event failed: {e}"}

        elif cmd == "send":
            try:
                sid = args.get("sid", "")
                text = args.get("text", "")
                submit = args.get("submit", True)
                if not sid:
                    return {"success": False, "message": "No sid provided"}
                s = self.sessions.get(sid)
                if not s:
                    return {"success": False, "message": f"No such session: {sid}"}
                s._startup_trust_pending = False
                self._send_text_to_session(s, text, submit=submit)
                return {"success": True, "message": f"Sent {len(text)} chars to {sid}"}
            except Exception as e:
                return {"success": False, "message": f"Send failed: {e}"}

        elif cmd == "raw_input":
            # Frame Link 無縫遠端分頁：把遠端使用者的原始鍵盤位元組直接寫進 PTY
            # （方向鍵、Ctrl-C、Enter 都照原樣），不走 send 的 paste-buffer/submit。
            try:
                sid = args.get("sid", "")
                data = args.get("data", "")
                s = self.sessions.get(sid)
                if not s:
                    return {"success": False, "message": f"No such session: {sid}"}
                s.write(data)
                return {"success": True}
            except Exception as e:
                return {"success": False, "message": f"raw_input failed: {e}"}

        elif cmd == "save_paste":
            # Frame Link：把遠端檢視端貼上的圖片位元組（base64）落地成檔案，
            # 回傳路徑，讓對方的 CLI（Claude/Codex）能像本機貼圖那樣讀到。
            try:
                import base64 as _b64
                filename = os.path.basename(args.get("filename", "") or "paste.png")
                data_b64 = args.get("data_b64", "") or ""
                if "," in data_b64 and data_b64[:5].lower() == "data:":
                    data_b64 = data_b64.split(",", 1)[1]
                raw = _b64.b64decode(data_b64)
                if not raw or len(raw) > 64 * 1024 * 1024:
                    return {"success": False, "message": "bad/too-large paste"}
                dest_dir = CLAUDE_TMP / "framelink-paste"
                dest_dir.mkdir(parents=True, exist_ok=True)
                stem = re.sub(r"[^\w.\-]", "_", filename) or "paste.png"
                dest = dest_dir / f"{int(time.time()*1000)}_{stem}"
                dest.write_bytes(raw)
                return {"success": True, "details": {"path": str(dest)}}
            except Exception as e:
                return {"success": False, "message": f"save_paste failed: {e}"}

        elif cmd == "raw_screen":
            # Frame Link 無縫遠端分頁的初次上畫：用 tmux capture-pane -e 取「當前
            # 可視畫面」含 ANSI 色碼，前置 clear+home 直接重現對方畫面。這對
            # alt-screen TUI（claude/codex/opencode）才畫得對——cleaned peek 拿到
            # 的是 scrollback 重建版，貼進 xterm 會破圖。
            try:
                sid = args.get("sid", "")
                s = self.sessions.get(sid)
                if not s:
                    return {"success": False, "message": f"No such session: {sid}"}
                tn = getattr(s, "_tmux_name", None)
                if tn:
                    tmux = _tmux_bin() or "tmux"
                    out = subprocess.run(
                        [tmux, "capture-pane", "-e", "-p", "-t", tn],
                        capture_output=True, text=True, timeout=3)
                    if out.returncode == 0:
                        screen = out.stdout.rstrip("\n").replace("\n", "\r\n")
                        return {"success": True,
                                "details": {"screen": "\x1b[2J\x1b[H" + screen}}
                # 非 tmux session：退回 cleaned history（至少有內容）
                raw = self.get_clean_history(sid, max_lines=200)
                res = json.loads(raw) if isinstance(raw, str) else raw
                txt = (res.get("text") or "").replace("\n", "\r\n")
                return {"success": True,
                        "details": {"screen": "\x1b[2J\x1b[H" + txt}}
            except Exception as e:
                return {"success": False, "message": f"raw_screen failed: {e}"}

        elif cmd == "resize_pty":
            # Frame Link：遠端檢視端把「它的可視尺寸」推過來，讓這台的 PTY
            # reflow 成一樣大——遠端 pane 才撐得滿、alt-screen 不破版。
            try:
                sid = args.get("sid", "")
                cols = int(args.get("cols") or 0)
                rows = int(args.get("rows") or 0)
                if not sid or cols <= 0 or rows <= 0:
                    return {"success": False, "message": "bad sid/cols/rows"}
                if sid not in self.sessions:
                    return {"success": False, "message": f"No such session: {sid}"}
                self.resize(sid, cols, rows)
                return {"success": True}
            except Exception as e:
                return {"success": False, "message": f"resize_pty failed: {e}"}

        elif cmd == "peek":
            try:
                sid = args.get("sid", "")
                max_lines = int(args.get("lines", 200))
                if not sid:
                    return {"success": False, "message": "No sid provided"}
                if sid not in self.sessions:
                    return {"success": False, "message": f"No such session: {sid}"}
                raw = self.get_clean_history(sid, max_lines=max_lines)
                result = json.loads(raw) if isinstance(raw, str) else raw
                if not result.get("success"):
                    return {"success": False, "message": result.get("reason", "peek failed")}
                text = result.get("text", "")
                # Keep only the last max_lines non-empty lines for master orchestration use
                lines = [l for l in text.split("\n") if l.strip()]
                tail = "\n".join(lines[-max_lines:])
                return {
                    "success": True,
                    "message": f"{len(lines)} lines",
                    "details": {"text": tail},
                }
            except Exception as e:
                return {"success": False, "message": f"Peek failed: {e}"}

        elif cmd == "rename":
            try:
                sid = args.get("sid", "")
                name = args.get("name", "")
                if not sid or not name:
                    return {"success": False, "message": "sid and name required"}
                result_json = self.rename_session(sid, name)
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                if result.get("success"):
                    return {"success": True, "message": f"Renamed {sid} to {name}"}
                return {"success": False, "message": "Rename failed"}
            except Exception as e:
                return {"success": False, "message": f"Rename failed: {e}"}

        elif cmd == "ui_sessions":
            # 診斷用：回傳 webview「眼中」的 tabs/labels。專治「後端說有、
            # 畫面沒有」各說各話——直接問 UI 而不是用後端狀態推論。
            # 注意：evaluate_js 的「回傳值」在背景 thread 會卡死（WKWebView
            # round-trip 不回來），所以走 fire-and-forget＋JS 回呼
            # report_ui_state 存值、這裡輪詢取件。
            try:
                if not self._window:
                    return {"success": False, "message": "no window"}
                self._ui_state_report = None
                self._window.evaluate_js(
                    'try { pywebview.api.report_ui_state('
                    'window.__sfUiState ? __sfUiState() : "{}"); } catch(e) {}')
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    if self._ui_state_report is not None:
                        return {"success": True, "message": self._ui_state_report}
                    time.sleep(0.1)
                return {"success": False, "message": "UI 未回報（webview 無回應）"}
            except Exception as e:
                return {"success": False, "message": f"ui_sessions failed: {e}"}

        elif cmd == "history_audit":
            try:
                sid = args.get("sid", "")
                if not sid:
                    if self.bridge and self.bridge.slots:
                        sid = next(iter(self.bridge.slots))
                    elif self.sessions:
                        sid = next(iter(self.sessions))
                if not sid:
                    return {"success": False, "message": "no sessions"}
                raw = self.history_audit(sid)
                return json.loads(raw) if isinstance(raw, str) else raw
            except Exception as e:
                return {"success": False, "message": f"history_audit failed: {e}"}

        elif cmd == "do_update":
            try:
                result_json = self.do_update()
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                return {
                    "success": result.get("success", False),
                    "message": result.get("message", ""),
                    "details": {
                        "version": result.get("version", "?"),
                        "needs_restart": result.get("needs_restart", False),
                        "can_hot_reload": result.get("can_hot_reload", False),
                    }
                }
            except Exception as e:
                return {"success": False, "message": f"Update failed: {e}"}

        elif cmd == "board_list":
            tasks = board.list_tasks()
            return {
                "success": True,
                "message": f"{len(tasks)} tasks",
                "details": {"enabled": self._board_enabled(), "tasks": tasks},
            }

        elif cmd == "board_add":
            try:
                task = board.add_task(
                    args.get("title", ""),
                    assignee=args.get("assignee", "unassigned"),
                    status=args.get("status", "todo"),
                    difficulty=args.get("difficulty", "medium"),
                    notes=args.get("notes", ""),
                )
                return {"success": True, "message": f"Added {task['id']}", "details": {"task": task}}
            except Exception as e:
                return {"success": False, "message": f"board_add failed: {e}"}

        elif cmd == "board_update":
            try:
                task_id = args.get("id", "")
                fields = {k: args[k] for k in ("title", "assignee", "status", "difficulty", "notes") if k in args}
                task = board.update_task(task_id, **fields)
                if task is None:
                    return {"success": False, "message": f"No such task: {task_id}"}
                return {"success": True, "message": f"Updated {task_id}", "details": {"task": task}}
            except Exception as e:
                return {"success": False, "message": f"board_update failed: {e}"}

        elif cmd == "board_remove":
            ok = board.remove_task(args.get("id", ""))
            return {"success": ok, "message": "Removed" if ok else "No such task"}

        elif cmd == "link_status":
            # Frame Link（跨機配對）狀態：listener + peers 可達性。
            try:
                st = self._link().status()
                lines = [f"🔗 Frame Link — {'on' if st.get('running') else 'off'}"
                         f" · {st.get('frame_name')}"]
                if st.get("running"):
                    addrs = ", ".join(st.get("addresses") or []) or "?"
                    lines.append(f"   {addrs} :{st.get('listen_port')}")
                for p in st.get("peers") or []:
                    dot = "🟢" if p.get("reachable") else "⚫"
                    lines.append(f" {dot} {p['name']} — {p.get('host') or '(無位址)'}"
                                 f":{p.get('port') or ''}")
                if not st.get("peers"):
                    lines.append(" (尚未配對任何 ShellFrame)")
                return {"success": True, "message": "\n".join(lines),
                        "details": st}
            except Exception as e:
                return {"success": False, "message": f"link status failed: {e}"}

        elif cmd == "link_pair":
            # 產生短效一次性配對碼（TG 遠端也能觸發，人在外面即可配對）。
            try:
                res = self._link().pairing_begin()
                if not res.get("success"):
                    return res
                addrs = ", ".join(res.get("addresses") or []) or "?"
                return {"success": True,
                        "message": (f"🔗 配對碼：{res['code']}\n"
                                    f"位址：{addrs}  port {res['port']}\n"
                                    f"{res['expires_in']} 秒內、限一次，"
                                    f"在另一台 ShellFrame 選「加入配對」輸入"),
                        "details": res}
            except Exception as e:
                return {"success": False, "message": f"link pair failed: {e}"}

        elif cmd == "link_join":
            try:
                res = self._link().join(args.get("host", ""),
                                        int(args.get("port") or 8767),
                                        args.get("code", ""))
                if res.get("success"):
                    self._notify_ui_sessions_changed()
                    return {"success": True,
                            "message": f"✅ 已配對：{res.get('peer_name')}",
                            "details": res}
                return res
            except Exception as e:
                return {"success": False, "message": f"link join failed: {e}"}

        elif cmd == "delay_schedule":
            try:
                res = self.delay_add(
                    args.get("sid", ""), args.get("text", ""),
                    int(args.get("delay_sec") or 0),
                    chat_id=int(args.get("chat_id") or 0),
                    label=args.get("label", ""))
                if not res.get("success"):
                    return res
                mins = int(args.get("delay_sec") or 0) // 60
                secs = int(args.get("delay_sec") or 0) % 60
                when = time.strftime("%H:%M", time.localtime(res["due_ts"]))
                dur = (f"{mins}m" + (f"{secs}s" if secs else "")) if mins else f"{secs}s"
                return {"success": True,
                        "message": f"⏳ 已排程（{res['id']}）：{dur} 後（約 {when}）送出\n"
                                   f"未送出前可 /delay cancel {res['id']} 收回",
                        "details": res}
            except Exception as e:
                return {"success": False, "message": f"delay schedule failed: {e}"}

        elif cmd == "delay_list":
            try:
                items = self.delay_list()
                if not items:
                    return {"success": True, "message": "沒有排程中的 /delay"}
                now = time.time()
                lines = ["⏳ 排程中："]
                for x in items:
                    left = int(x.get("due_ts", 0) - now)
                    when = time.strftime("%H:%M", time.localtime(x.get("due_ts", 0)))
                    left_s = (f"{left // 60}m{left % 60}s" if left >= 60
                              else f"{max(0, left)}s")
                    preview = (x.get("text", "") or "").replace("\n", " ")[:40]
                    lines.append(f" [{x.get('id')}] {x.get('label','')} · {when}"
                                 f"（剩 {left_s}）\n   {preview}")
                return {"success": True, "message": "\n".join(lines), "details": {"items": items}}
            except Exception as e:
                return {"success": False, "message": f"delay list failed: {e}"}

        elif cmd == "delay_cancel":
            try:
                res = self.delay_cancel(args.get("id", ""))
                return {"success": res.get("success", False),
                        "message": ("🗑 已收回排程 " + args.get("id", ""))
                                   if res.get("success") else res.get("message", "取消失敗")}
            except Exception as e:
                return {"success": False, "message": f"delay cancel failed: {e}"}

        elif cmd == "link_unpair":
            try:
                target = (args.get("peer") or "").strip()
                peers = self._link().peers()
                pid = target if target in peers else ""
                if not pid:
                    for k, v in peers.items():
                        if v.get("name") == target:
                            pid = k
                            break
                if not pid:
                    return {"success": False,
                            "message": f"找不到 peer「{target}」（用 link_status 看名單）"}
                self._link().unpair(pid)
                return {"success": True, "message": f"已斷開 {target}"}
            except Exception as e:
                return {"success": False, "message": f"link unpair failed: {e}"}

        elif cmd == "usage":
            # Per-tab AI usage water-level. Detects claude/codex from the
            # session's launch command and queries the matching local script.
            sid = args.get("sid", "")
            s = self.sessions.get(sid)
            if not s:
                return {"success": False, "message": "此 tab 不存在或已關閉。"}
            try:
                text = usage_probe.probe(s.cmd)
                return {"success": True, "message": text}
            except Exception as e:
                return {"success": False, "message": f"用量查詢失敗：{e}"}

        else:
            return {"success": False, "message": f"Unknown command: {cmd}"}

    def cleanup_all(self):
        # Tear down the global hotkey FIRST so a trailing ⌃⌥Space during
        # shutdown can't kick `open -b com.h2ocloud.shellframe` and race
        # the incoming second instance against our still-running TG
        # bridge (→ 409 Conflict on the bot token).
        try:
            _unregister_global_hotkey()
        except Exception:
            _swallow("Api.cleanup_all:6035")
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
        if self.line_bridge:
            self.line_bridge.stop()
            self.line_bridge = None
        if getattr(self, "frame_link", None):
            try:
                self.frame_link.stop()
            except Exception:
                _swallow("Api.cleanup_all:frame_link")
        for s in list(self.sessions.values()):
            # Detach only — tmux sessions stay alive for reattach on restart
            s.kill(kill_tmux=False)
        self.sessions.clear()

    def cleanup_and_exit(self):
        """Clean up and force exit — pywebview on macOS can hang after window close."""
        self.cleanup_all()
        # Give child processes a moment to die, then force exit
        threading.Timer(1.5, lambda: os._exit(0)).start()


def _venv_python(venv_dir: Path) -> str:
    """Return absolute path to venv's python, or sys.executable if venv missing."""
    if IS_WIN:
        candidates = [venv_dir / "Scripts" / "python.exe", venv_dir / "Scripts" / "python"]
    else:
        candidates = [venv_dir / "bin" / "python3", venv_dir / "bin" / "python"]
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except Exception:
            _swallow("_venv_python:6065")
    return sys.executable


def _venv_has_pip(venv_dir: Path) -> bool:
    """True if venv has a working python + pip module."""
    py = _venv_python(venv_dir)
    try:
        r = subprocess.run(
            [py, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=10
        )
        return r.returncode == 0
    except Exception:
        return False


def _pip_install_robust(venv_dir: Path, req_file: str):
    """Install requirements into venv. Returns (ok: bool, message: str).
    Falls back to recreating the venv from scratch if the first install fails."""
    def _run_pip(py: str):
        return subprocess.run(
            [py, "-m", "pip", "install", "-q", "-r", req_file],
            cwd=str(APP_DIR),
            capture_output=True, text=True, timeout=180
        )

    # Attempt 1: existing venv (or system python if no venv)
    py = _venv_python(venv_dir)
    try:
        r = _run_pip(py)
        if r.returncode == 0:
            return True, "ok"
        first_err = r.stderr.strip()[-200:] or r.stdout.strip()[-200:]
    except Exception as e:
        first_err = str(e)

    # Attempt 2: recreate venv and retry
    try:
        if venv_dir.exists():
            shutil.rmtree(str(venv_dir), ignore_errors=True)
        r = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            return False, f"venv recreate failed: {r.stderr.strip()[-200:]} | first: {first_err}"
        py = _venv_python(venv_dir)
        r = _run_pip(py)
        if r.returncode == 0:
            return True, "ok (after venv recreate)"
        return False, f"retry failed: {r.stderr.strip()[-200:]} | first: {first_err}"
    except Exception as e:
        return False, f"recreate exception: {e} | first: {first_err}"


def _run_install_sh():
    """Run install.sh via curl|bash to re-initialize a broken install in place.
    Returns (ok: bool, message: str). Windows: return (False, reason) — install.ps1
    would need equivalent handling there."""
    if IS_WIN:
        return False, "install.sh fallback not supported on Windows — run install.ps1 manually"
    try:
        # curl | bash: self-contained bootstrap. install.sh handles both the
        # "dir exists but no .git" case (git init + fetch + reset) and the
        # "fresh machine" case. Uses the same URL the user would curl by hand.
        cmd = (
            "curl -fsSL "
            "https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh "
            "| bash"
        )
        r = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=600
        )
        if r.returncode == 0:
            # Last line of install.sh output usually has the version summary
            summary = r.stdout.strip().split('\n')[-1] if r.stdout.strip() else "ok"
            return True, summary[:200]
        return False, (r.stderr.strip()[-300:] or r.stdout.strip()[-300:] or f"exit {r.returncode}")
    except subprocess.TimeoutExpired:
        return False, "install.sh timed out (>10min)"
    except Exception as e:
        return False, str(e)


def _self_heal_venv():
    """Auto-detect and fix stale venv on startup.
    If key packages are missing, re-run pip install; if that fails, recreate venv."""
    missing = []
    for mod in ("pyte", "webview"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        return

    print(f"[shellframe] missing modules {missing} — running pip install...")
    venv_dir = APP_DIR / ".venv"
    req_file = str(APP_DIR / "requirements.txt")
    ok, msg = _pip_install_robust(venv_dir, req_file)
    if ok:
        print(f"[shellframe] pip install {msg} — please restart ShellFrame.")
    else:
        print(f"[shellframe] self-heal failed ({msg}).")
        print("[shellframe] Recover with:")
        print("  curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash")


_nap_activity = None  # module-global so NSProcessInfo doesn't GC the activity token


def _prevent_app_nap():
    """Opt out of macOS App Nap so the TG bridge and PTY readers keep running
    when the display sleeps or the app is backgrounded. Without this, macOS
    throttles us to ~1 tick/minute and Telegram messages stall.

    We DON'T use NSActivityIdleSystemSleepDisabled — lid-close should still
    put the Mac to sleep. Telegram holds messages for 24h so they re-deliver
    on wake. We only want to stop App Nap (display-off throttling).
    """
    global _nap_activity
    if platform.system() != "Darwin":
        return
    try:
        from Foundation import NSProcessInfo
        # NSActivityUserInitiated = 0x00FFFFFF  (high-priority user work)
        # NSActivityLatencyCritical = 0xFF00000000  (timing-sensitive; e.g. audio/IO)
        NSActivityUserInitiated = 0x00FFFFFF
        NSActivityLatencyCritical = 0xFF00000000
        _nap_activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            NSActivityUserInitiated | NSActivityLatencyCritical,
            "shellframe: keep TG bridge + PTY readers alive when display sleeps",
        )
    except Exception as e:
        print(f"[shellframe] App Nap opt-out failed (non-fatal): {e}")


def _coords_on_attached_screen(x: int, y: int, w: int, h: int) -> bool:
    """Return True if the window rect's centre lands on an attached display.

    pywebview's cocoa backend crashes during startup when the initial
    position has no hosting screen (external monitor unplugged, saved
    coords stale, etc.) — windowDidMove_ calls window.screen() which
    returns None, then .frame() blows up. We pre-validate via NSScreen
    and drop the coords if they're off-screen.

    On non-macOS platforms we don't currently detect this — return True
    so nothing is dropped. Linux/Windows pywebview backends have
    different (usually safer) fallback behaviour.
    """
    if sys.platform != "darwin":
        return True
    try:
        from AppKit import NSScreen
    except Exception:
        return True
    screens = list(NSScreen.screens() or [])
    if not screens:
        return False
    primary_h = float(screens[0].frame().size.height)
    cx = x + w / 2.0
    cy_pywebview = y + h / 2.0
    cy_cocoa = primary_h - cy_pywebview  # convert to Cocoa bottom-up Y
    for s in screens:
        f = s.frame()
        x_min = float(f.origin.x)
        x_max = x_min + float(f.size.width)
        y_min = float(f.origin.y)
        y_max = y_min + float(f.size.height)
        if x_min <= cx <= x_max and y_min <= cy_cocoa <= y_max:
            return True
    return False


def _patch_pywebview_cocoa_none_screen():
    """Neuter pywebview's cocoa `windowDidMove_` crash.

    On macOS, pywebview's BrowserView.windowDidMove_ does
    `i.window.screen().frame()` — if the window is transiently off every
    attached display (which happens during the initial move-to-saved-coords
    on multi-monitor setups, even when our pre-validator says the final
    centre is on-screen), screen() returns None and .frame() raises
    AttributeError, taking the whole app down before the UI ever paints.
    Wrap the callback to treat None as a no-op; the window still ends up
    at its final position, we just skip the spurious mid-move event.
    """
    if sys.platform != "darwin":
        return
    try:
        from webview.platforms import cocoa as _cocoa
        orig = getattr(_cocoa.BrowserView, "windowDidMove_", None)
        if orig is None or getattr(orig, "_sf_patched", False):
            return
        def safe_windowDidMove_(self, notification):
            try:
                w = getattr(self, "window", None)
                if w is None or w.screen() is None:
                    return
                return orig(self, notification)
            except AttributeError:
                return
        safe_windowDidMove_._sf_patched = True
        _cocoa.BrowserView.windowDidMove_ = safe_windowDidMove_
    except Exception as e:
        print(f"[shellframe] cocoa patch skipped: {e}", file=sys.stderr)


_global_hotkey_monitors = []
_carbon_hotkey_lib = None
_carbon_hotkey_ref = None
_carbon_hotkey_handler_ref = None
_carbon_hotkey_callback = None


def _unregister_global_hotkey():
    """Pull down any live NSEvent monitors. Safe to call repeatedly and
    during shutdown — if AppKit isn't importable we just clear the list."""
    global _global_hotkey_monitors
    global _carbon_hotkey_lib, _carbon_hotkey_ref, _carbon_hotkey_handler_ref
    global _carbon_hotkey_callback
    try:
        if _carbon_hotkey_lib is not None and _carbon_hotkey_ref is not None:
            _carbon_hotkey_lib.UnregisterEventHotKey(_carbon_hotkey_ref)
        if _carbon_hotkey_lib is not None and _carbon_hotkey_handler_ref is not None:
            _carbon_hotkey_lib.RemoveEventHandler(_carbon_hotkey_handler_ref)
    except Exception as e:
        _dlog("hotkey", f"carbon unregister failed: {e}")
    _carbon_hotkey_ref = None
    _carbon_hotkey_handler_ref = None
    _carbon_hotkey_callback = None
    try:
        from AppKit import NSEvent
        for _m in _global_hotkey_monitors:
            try:
                NSEvent.removeMonitor_(_m)
            except Exception:
                _swallow("_unregister_global_hotkey:6303")
    except Exception:
        _swallow("_unregister_global_hotkey:6305")
    _global_hotkey_monitors = []


class _CarbonEventHotKeyID(ctypes.Structure):
    _fields_ = [
        ("signature", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
    ]


class _CarbonEventTypeSpec(ctypes.Structure):
    _fields_ = [
        ("eventClass", ctypes.c_uint32),
        ("eventKind", ctypes.c_uint32),
    ]


_CarbonEventHandler = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)


def _fourcc(value: str) -> int:
    return int.from_bytes(value.encode("ascii"), "big")


def _register_carbon_hotkey(on_press) -> tuple[bool, str]:
    """Register Ctrl+Option+Space via Carbon so it does not require
    Accessibility permission. Falls back to NSEvent global monitor on failure."""
    global _carbon_hotkey_lib, _carbon_hotkey_ref, _carbon_hotkey_handler_ref
    global _carbon_hotkey_callback
    if sys.platform != "darwin":
        return False, "not macOS"
    try:
        carbon_path = (
            ctypes.util.find_library("Carbon")
            or "/System/Library/Frameworks/Carbon.framework/Carbon"
        )
        carbon = ctypes.CDLL(carbon_path)

        carbon.GetApplicationEventTarget.argtypes = []
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p,
            _CarbonEventHandler,
            ctypes.c_uint32,
            ctypes.POINTER(_CarbonEventTypeSpec),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.InstallEventHandler.restype = ctypes.c_int32
        carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        carbon.RemoveEventHandler.restype = ctypes.c_int32
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            _CarbonEventHotKeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        carbon.UnregisterEventHotKey.restype = ctypes.c_int32

        target = carbon.GetApplicationEventTarget()
        if not target:
            return False, "GetApplicationEventTarget returned null"

        def _handler(_next_handler, _event, _user_data):
            try:
                from AppKit import NSOperationQueue
                NSOperationQueue.mainQueue().addOperationWithBlock_(on_press)
            except Exception:
                try:
                    on_press()
                except Exception as e:
                    _dlog("hotkey", f"carbon handler failed: {e}")
            return 0

        callback = _CarbonEventHandler(_handler)
        event_types = (_CarbonEventTypeSpec * 1)(
            _CarbonEventTypeSpec(_fourcc("keyb"), 5)  # kEventHotKeyPressed
        )
        handler_ref = ctypes.c_void_p()
        status = carbon.InstallEventHandler(
            target, callback, 1, event_types, None, ctypes.byref(handler_ref)
        )
        if status != 0:
            return False, f"InstallEventHandler status={status}"

        hotkey_ref = ctypes.c_void_p()
        hotkey_id = _CarbonEventHotKeyID(_fourcc("ShFr"), 1)
        # Carbon modifier bits: optionKey=1<<11, controlKey=1<<12.
        status = carbon.RegisterEventHotKey(
            49,  # kVK_Space
            (1 << 11) | (1 << 12),
            hotkey_id,
            target,
            0,
            ctypes.byref(hotkey_ref),
        )
        if status != 0:
            try:
                carbon.RemoveEventHandler(handler_ref)
            except Exception:
                _swallow("_register_carbon_hotkey:6412")
            return False, f"RegisterEventHotKey status={status}"

        _carbon_hotkey_lib = carbon
        _carbon_hotkey_callback = callback
        _carbon_hotkey_handler_ref = handler_ref
        _carbon_hotkey_ref = hotkey_ref
        return True, "Carbon RegisterEventHotKey active"
    except Exception as e:
        return False, str(e)


_PID_FILE = TMP_DIR / "shellframe.pid"


def _move_windows_to_mouse_screen():
    """Move every shellframe NSWindow to the screen where the cursor
    currently sits, centred on that screen. Must be called on the main
    thread — caller wraps in NSOperationQueue.mainQueue() if invoked
    from a non-main context (signal handler etc.).

    the user's ask: "滑鼠到哪邊，調用快捷鍵就要啟動在那個視窗" — when
    the user fires the global hotkey, the window should appear on
    whichever monitor the cursor is on, not wherever the window
    happened to be sitting before. NSWindowCollectionBehaviorMoveToActiveSpace
    handles the Spaces axis; this fills in the multi-monitor axis.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSScreen, NSEvent, NSApp
    except Exception:
        return
    if NSApp is None:
        return
    try:
        mouse = NSEvent.mouseLocation()
    except Exception:
        return
    target = None
    try:
        for s in NSScreen.screens() or []:
            f = s.frame()
            if (f.origin.x <= mouse.x < f.origin.x + f.size.width and
                f.origin.y <= mouse.y < f.origin.y + f.size.height):
                target = s
                break
    except Exception:
        return
    if target is None:
        return
    try:
        tf = target.frame()
    except Exception:
        return
    for w in (NSApp.windows() or []):
        try:
            if not w.isVisible():
                continue
            if w.screen() is target:
                continue
            wf = w.frame()
            new_x = tf.origin.x + (tf.size.width - wf.size.width) / 2.0
            new_y = tf.origin.y + (tf.size.height - wf.size.height) / 2.0
            w.setFrameOrigin_((new_x, new_y))
        except Exception:
            continue


_last_summon_ts = 0.0
_SUMMON_MIN_INTERVAL = 2.0   # seconds — see _summon_self_main_thread


def _summon_self_main_thread():
    """Bring this process's shellframe window to the front. Safe to call
    from a signal handler thread — dispatches the AppKit work onto the
    main queue.

    Rate-limited: if the last summon fired within the last
    `_SUMMON_MIN_INTERVAL` seconds, skip. macOS / LaunchServices can
    end up looping launch attempts in some background scenarios
    (Dock animation, paste-driven app activation, NSWorkspace events
    that fire `open -b` which re-enters _ensure_single_instance which
    re-sends SIGUSR1, …). Without throttling the user saw the window
    "keep popping to the front without me pressing the hotkey". A
    legitimate user click resolves to a single summon; a runaway loop
    only paints once.
    """
    global _last_summon_ts
    if sys.platform != "darwin":
        return
    now = time.time()
    if now - _last_summon_ts < _SUMMON_MIN_INTERVAL:
        return
    _last_summon_ts = now
    try:
        from AppKit import (
            NSOperationQueue, NSApp,
            NSRunningApplication, NSApplicationActivateIgnoringOtherApps,
        )
    except Exception:
        return
    def _do():
        # If we're already foreground + visible, do nothing. No need to
        # repaint or warp the window; a background SIGUSR1 from a
        # spurious launch-attempt should be a quiet no-op when the user
        # already sees us.
        try:
            if NSApp is not None and NSApp.isActive() and not NSApp.isHidden():
                return
        except Exception:
            _swallow("_summon_self_main_thread._do:6523")
        try:
            _move_windows_to_mouse_screen()
        except Exception as e:
            print(f"[shellframe] move-to-mouse-screen failed: {e}", file=sys.stderr)
        try:
            if NSApp is not None:
                try: NSApp.unhide_(None)
                except Exception: _swallow("_summon_self_main_thread._do:6531")
            NSRunningApplication.currentApplication().activateWithOptions_(
                NSApplicationActivateIgnoringOtherApps
            )
        except Exception as e:
            print(f"[shellframe] summon failed: {e}", file=sys.stderr)
    try:
        NSOperationQueue.mainQueue().addOperationWithBlock_(_do)
    except Exception:
        _swallow("_summon_self_main_thread:6540")


def _on_summon_signal(signum, frame):
    """SIGUSR1 from a duplicate-launch attempt — bring this instance to
    the foreground instead of letting the new copy boot."""
    try:
        print("[shellframe] received summon signal, bringing window forward",
              file=sys.stderr)
        _summon_self_main_thread()
    except Exception:
        _swallow("_on_summon_signal:6551")


def _release_pid_file():
    try:
        if _PID_FILE.exists():
            try:
                pid = int(_PID_FILE.read_text().strip())
            except Exception:
                pid = -1
            if pid == os.getpid():
                _PID_FILE.unlink(missing_ok=True)
    except Exception:
        _swallow("_release_pid_file:6564")


def _claim_pid_file():
    try:
        _PID_FILE.write_text(str(os.getpid()))
    except Exception:
        return
    atexit.register(_release_pid_file)
    if sys.platform != "win32":
        try:
            signal.signal(signal.SIGUSR1, _on_summon_signal)
        except Exception:
            _swallow("_claim_pid_file:6577")


_WIN_MUTEX_HANDLE = None  # keep the mutex referenced for the process lifetime


def _ensure_single_instance_windows():
    """Windows duplicate guard — named mutex instead of the PID file.

    A kernel mutex is auto-released when its owner dies, so there is no
    stale-file case to probe. Same failure this prevents as on macOS: two
    instances sharing one Telegram bot token → getUpdates 409 conflicts,
    plus duplicated PTY sessions. If another instance already holds the
    mutex, bring its window forward (best-effort, mirrors the SIGUSR1
    summon path) and exit this process.
    """
    global _WIN_MUTEX_HANDLE
    try:
        import ctypes
        # use_last_error + get_last_error: windll.GetLastError() is
        # documented-unreliable (ctypes' own calls can clobber it) — a
        # misread here either disables the guard or kills the only instance.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, "Local\\shellframe-single-instance")
        ERROR_ALREADY_EXISTS = 183
        if handle and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            _WIN_MUTEX_HANDLE = handle
            return
        print("[shellframe] another instance already running — "
              "bringing it forward and exiting this one", file=sys.stderr)
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "shellframe")
            if hwnd:
                SW_RESTORE = 9
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            _swallow("_ensure_single_instance_windows:6612")
        os._exit(0)
    except Exception:
        # ctypes/kernel32 unavailable (exotic runtime) — degrade to no guard
        # rather than blocking startup.
        return


def _ensure_single_instance():
    """Before allocating anything, check whether another shellframe is
    already running. If so, signal it to come forward and exit this
    process. Otherwise claim the PID file so the *next* duplicate
    launch can find us.

    Why PID file (not NSRunningApplication.bundleIdentifier): on macOS
    the launcher execs `python main.py` so the kernel-reported bundle
    is `org.python.python` (or whatever Python's framework uses), NOT
    `com.h2ocloud.shellframe`. The previous bundle-id lookup never
    matched our own process, never blocked duplicate launches, and
    the user kept seeing two-instance TG 409 conflicts. PID file +
    SIGUSR1 sidesteps the bundle-id resolution entirely.
    """
    if sys.platform == "win32":
        _ensure_single_instance_windows()
        return
    old_pid = 0
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
        except Exception:
            old_pid = 0
    if old_pid <= 0 or old_pid == os.getpid():
        _claim_pid_file()
        return
    # Probe liveness — kill(pid, 0) doesn't kill, just reports whether
    # the pid exists. ESRCH = no such process (stale file).
    alive = False
    try:
        os.kill(old_pid, 0)
        alive = True
    except OSError as e:
        if e.errno != errno.ESRCH:
            # EPERM — process exists but we can't signal it; still alive
            alive = True
    if not alive:
        _claim_pid_file()
        return
    print(f"[shellframe] another instance (pid={old_pid}) already running — "
          f"signalling it to come forward and exiting this one",
          file=sys.stderr)
    try:
        os.kill(old_pid, signal.SIGUSR1)
    except OSError:
        _swallow("_ensure_single_instance:6665")
    # NOTE: removed the `open -b com.h2ocloud.shellframe` belt-and-braces.
    # macOS LaunchServices treats that as a relaunch-intent which can
    # come back round to spawn another shellframe, which re-enters this
    # function, which re-sends SIGUSR1, which re-activates… → window
    # "keeps popping to the front without me pressing the hotkey".
    # SIGUSR1 alone is the canonical wake path; if it doesn't reach,
    # the duplicate-launch loses but no loop is triggered.
    os._exit(0)


def _register_global_hotkey():
    """Ctrl+Option+Space: show shellframe if hidden, hide it if active.

    macOS only for now (uses NSEvent.addGlobalMonitor / addLocalMonitor).
    Global monitor requires Accessibility permission — users who've run
    `sfctl permissions` have it. Without permission the hotkey silently
    no-ops (key still works inside shellframe itself via the local
    monitor, which doesn't need Accessibility).

    Settings.global_hotkey_enabled (default True) gates registration.
    """
    if sys.platform != "darwin":
        return
    # Tear down any prior registration (e.g. re-register after settings flip)
    _unregister_global_hotkey()

    settings = (load_config().get("settings", {}) or {})
    if not settings.get("global_hotkey_enabled", True):
        return

    try:
        from AppKit import (
            NSEvent,
            NSApp,
            NSRunningApplication,
            NSApplicationActivateIgnoringOtherApps,
        )
    except Exception as e:
        print(f"[shellframe] global hotkey skipped (AppKit): {e}", file=sys.stderr)
        return

    NSEventMaskKeyDown = 1 << 10  # NSEventMaskKeyDown
    # Modifier flag bits (from NSEvent.h)
    MOD_SHIFT = 1 << 17
    MOD_CONTROL = 1 << 18
    MOD_OPTION = 1 << 19
    MOD_COMMAND = 1 << 20
    MOD_MASK = MOD_SHIFT | MOD_CONTROL | MOD_OPTION | MOD_COMMAND
    NEED = MOD_CONTROL | MOD_OPTION
    FORBIDDEN = MOD_COMMAND | MOD_SHIFT

    SPACE_KEYCODE = 49  # kVK_Space

    def _is_on_current_space() -> bool:
        """True iff a shellframe window is visible in the user's CURRENT
        macOS space. Uses Quartz's on-screen window list, which only
        enumerates windows on the active space — windows on other spaces
        are absent regardless of their app's activation state."""
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGWindowListOptionOnScreenOnly,
                kCGNullWindowID,
            )
            wins = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID,
            ) or []
            pid = os.getpid()
            for w in wins:
                if w.get("kCGWindowOwnerPID") == pid:
                    return True
        except Exception:
            _swallow("_register_global_hotkey._is_on_current_space:6738")
        return False

    _last_hotkey_dispatch = 0.0

    def _toggle_visibility():
        try:
            is_active = bool(NSApp and NSApp.isActive())
            is_hidden = bool(NSApp and NSApp.isHidden())
            on_space = _is_on_current_space()
            print(f"[shellframe] hotkey toggle: active={is_active} "
                  f"hidden={is_hidden} on_current_space={on_space}",
                  file=sys.stderr)
            # Only treat as "hide" when shellframe is visible in THIS space
            # AND focused. the user uses macOS Spaces heavily — if the window
            # is on another space, activating should pull it to the current
            # space (via NSWindowCollectionBehaviorMoveToActiveSpace set at
            # load time), not yank the user across spaces.
            if on_space and is_active and not is_hidden:
                NSApp.hide_(None)
                return
            # Summon path. NOT rate-limited — this branch only runs from a
            # real user keypress (NSEvent local/global monitor), and the user
            # legitimately toggles hide→summon faster than the 2s floor.
            # The SIGUSR1 / LaunchServices feedback loop the throttle was
            # meant to break lives in _summon_self_main_thread, which has
            # its own _last_summon_ts gate.
            global _last_summon_ts
            _last_summon_ts = time.time()
            try:
                _move_windows_to_mouse_screen()
            except Exception as e:
                print(f"[shellframe] move-to-mouse-screen failed: {e}", file=sys.stderr)
            if NSApp is not None:
                try:
                    NSApp.unhide_(None)
                except Exception:
                    _swallow("_register_global_hotkey._toggle_visibility:6775")
            try:
                NSRunningApplication.currentApplication().activateWithOptions_(
                    NSApplicationActivateIgnoringOtherApps
                )
            except Exception:
                _swallow("_register_global_hotkey._toggle_visibility:6781")
            # NOTE: removed `open -b com.h2ocloud.shellframe` belt-and-braces
            # from this branch too. `unhide_` + `activateWithOptions_` is
            # enough for the in-process hotkey path; the LaunchServices
            # `open -b` form was the suspected feedback source for "window
            # keeps popping". If the unhide+activate combo somehow fails,
            # we'd rather drop one summon than risk looping.
        except Exception as e:
            print(f"[shellframe] hotkey toggle failed: {e}", file=sys.stderr)

    def _fire_hotkey(source: str):
        # Carbon hotkeys should consume the key event, but keep a tiny
        # duplicate guard in case the NSEvent local monitor also observes it
        # while ShellFrame is foreground.
        nonlocal _last_hotkey_dispatch
        now = time.time()
        if now - _last_hotkey_dispatch < 0.12:
            _dlog("hotkey", f"duplicate ignored source={source}")
            return
        _last_hotkey_dispatch = now
        _dlog("hotkey", f"pressed source={source}")
        _toggle_visibility()

    def _matches(event) -> bool:
        try:
            if event.isARepeat():
                return False
            if event.keyCode() != SPACE_KEYCODE:
                return False
            mods = int(event.modifierFlags()) & MOD_MASK
            if (mods & NEED) != NEED:
                return False
            if mods & FORBIDDEN:
                return False
            return True
        except Exception:
            return False

    def _global_handler(event):
        # Other apps have focus; global monitor can only observe, can't
        # swallow. We still react (toggle our app forward).
        if _matches(event):
            _fire_hotkey("nsevent-global")

    def _local_handler(event):
        # Shellframe itself has focus; swallow the event so xterm doesn't
        # see Ctrl+⌥+Space.
        if _matches(event):
            _fire_hotkey("nsevent-local")
            return None
        return event

    try:
        carbon_ok, carbon_msg = _register_carbon_hotkey(
            lambda: _fire_hotkey("carbon")
        )
        _dlog("hotkey", f"carbon register ok={carbon_ok}: {carbon_msg}")
        print(f"[shellframe] hotkey carbon register ok={carbon_ok}: "
              f"{carbon_msg}", file=sys.stderr)
        m1 = None
        if not carbon_ok:
            m1 = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown, _global_handler,
            )
            if m1 is None:
                _dlog("hotkey", "NSEvent global monitor returned nil")
                print("[shellframe] hotkey fallback failed: NSEvent global "
                      "monitor returned nil", file=sys.stderr)
            else:
                _dlog("hotkey", "NSEvent global monitor fallback active")
        m2 = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, _local_handler,
        )
        if m1 is not None:
            _global_hotkey_monitors.append(m1)
        if m2 is not None:
            _global_hotkey_monitors.append(m2)
    except Exception as e:
        print(f"[shellframe] hotkey register failed: {e}", file=sys.stderr)


def _ensure_mic_usage_plist():
    """macOS：確保 app bundle 有 NSMicrophoneUsageDescription。

    沒有這個 key，TCC 不會跳麥克風授權、錄音直接靜默失敗。既有安裝走
    git pull 更新不會重跑 install.sh，所以啟動時自癒：補 key + ad-hoc
    重簽（不 --deep，同 install.sh 的理由），下次 TCC 檢查即生效。"""
    if sys.platform != "darwin":
        return
    desc = "ShellFrame 需要使用麥克風進行語音輸入（STT 語音轉文字）。"
    for bundle in (Path("/Applications/ShellFrame.app"),
                   Path.home() / "Applications" / "ShellFrame.app",
                   APP_DIR / "ShellFrame.app"):
        plist = bundle / "Contents" / "Info.plist"
        if not plist.exists():
            continue
        try:
            r = subprocess.run(
                ["/usr/libexec/PlistBuddy", "-c", "Print :NSMicrophoneUsageDescription", str(plist)],
                capture_output=True, timeout=5)
            if r.returncode == 0:
                continue
            subprocess.run(
                ["/usr/libexec/PlistBuddy", "-c",
                 f"Add :NSMicrophoneUsageDescription string {desc}", str(plist)],
                capture_output=True, timeout=5)
            subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)],
                           capture_output=True, timeout=15)
            print(f"[shellframe] added NSMicrophoneUsageDescription → {bundle}", file=sys.stderr)
        except Exception as e:
            print(f"[shellframe] mic plist heal failed for {bundle}: {e}", file=sys.stderr)


def main():
    _self_heal_venv()
    _ensure_mic_usage_plist()
    # Guard before we allocate anything expensive — if another shellframe
    # is already running, activate it and exit this process. Prevents
    # double-instance TG bridge 409 conflicts when the user rapidly toggles
    # via hotkey / Dock click while the previous instance is still winding
    # down.
    _ensure_single_instance()
    _prevent_app_nap()
    _apply_macos_app_identity()
    _patch_pywebview_cocoa_none_screen()
    api = Api()
    html_path = Path(__file__).parent / "web" / "index.html"

    # Safety net: clean up on exit no matter what
    atexit.register(api.cleanup_all)
    def _exit_on_signal(signum, _frame):
        # 同上：訊號路徑也要留痕，才分得出「被 kill」跟「視窗被 quit」。
        try:
            _dlog("lifecycle", f"signal {signum} → exiting pid={os.getpid()}")
        except Exception:
            pass
        api.cleanup_all()
        os._exit(0)

    signal.signal(signal.SIGINT, _exit_on_signal)
    signal.signal(signal.SIGTERM, _exit_on_signal)

    # Restore window geometry from last close. Pass ONLY width/height to
    # create_window; x/y is applied AFTER the window exists (via
    # window.move in the loaded handler), not as the initial position.
    #
    # Reason: pywebview's cocoa backend crashes during the initial move-to-
    # saved-coords if the moving window is transiently off-screen. Its
    # windowDidMove_ callback calls self.window.screen().frame(); when
    # screen() is None, .frame() raises AttributeError BEFORE any Python
    # try/except or monkey-patch can help (PyObjC method tables bind at
    # class creation, so replacing BrowserView.windowDidMove_ in Python
    # doesn't affect the ObjC dispatch). Letting the window spawn centered
    # first, then moving it after shown, avoids that entire failure mode.
    win_cfg = load_config().get("window", {}) or {}
    create_kwargs = dict(
        title="shellframe",
        url=str(html_path),
        js_api=api,
        width=int(win_cfg.get("width") or 1000),
        height=int(win_cfg.get("height") or 720),
        min_size=(640, 400),
        text_select=True,
        background_color="#1a1b26",
    )
    saved_x, saved_y = win_cfg.get("x"), win_cfg.get("y")
    pending_move = None
    if isinstance(saved_x, (int, float)) and isinstance(saved_y, (int, float)):
        if _coords_on_attached_screen(
            int(saved_x), int(saved_y),
            create_kwargs["width"], create_kwargs["height"],
        ):
            pending_move = (int(saved_x), int(saved_y))
        else:
            # Saved screen is gone — scrub so we don't stash stale coords
            # back on the first move event.
            try:
                cfg_now = load_config()
                win = cfg_now.get("window", {}) or {}
                win.pop("x", None)
                win.pop("y", None)
                cfg_now["window"] = win
                save_config(cfg_now)
            except Exception:
                _swallow("main:6923")
            print(f"[shellframe] saved window position ({saved_x},{saved_y}) "
                  f"is off-screen — centering on primary.", file=sys.stderr)

    window = webview.create_window(**create_kwargs)
    api._window = window

    # Persist geometry on move/resize, debounced so rapid drag events don't
    # hammer the config file. Also saves once on close as a safety net.
    _geom_state = {
        "x": pending_move[0] if pending_move else None,
        "y": pending_move[1] if pending_move else None,
        "width": create_kwargs["width"],
        "height": create_kwargs["height"],
        "timer": None,
    }
    _geom_lock = threading.Lock()

    def _flush_geom():
        try:
            def _mut(cfg):
                cfg["window"] = {
                    "x": _geom_state["x"],
                    "y": _geom_state["y"],
                    "width": _geom_state["width"],
                    "height": _geom_state["height"],
                }
            update_config(_mut)
        except Exception:
            _swallow("main._flush_geom:6952")

    def _schedule_flush():
        with _geom_lock:
            t = _geom_state.get("timer")
            if t:
                t.cancel()
            nt = threading.Timer(0.8, _flush_geom)
            nt.daemon = True
            _geom_state["timer"] = nt
            nt.start()

    def _on_moved(x, y):
        _geom_state["x"] = int(x)
        _geom_state["y"] = int(y)
        _schedule_flush()

    def _on_resized(w, h):
        _geom_state["width"] = int(w)
        _geom_state["height"] = int(h)
        _schedule_flush()

    try:
        window.events.moved += _on_moved
    except Exception:
        _swallow("main:6977")
    try:
        window.events.resized += _on_resized
    except Exception:
        _swallow("main:6981")

    def _on_closed_save_and_cleanup():
        # 關閉一定要留痕。2026-08-31 23:51 macOS 排程的自動更新發起重新開機、
        # loginwindow 逐一 quit 掉所有 GUI app，ShellFrame 就這樣沒了——debug
        # log 裡一個字都沒有，只能靠 unified log 逐秒比對才確定不是自己崩潰。
        try:
            _dlog("lifecycle", f"window closed → cleanup_and_exit pid={os.getpid()}")
        except Exception:
            pass
        # Cancel pending debounce + flush synchronously so the close actually
        # captures the last known geometry before the process exits.
        with _geom_lock:
            t = _geom_state.get("timer")
            if t:
                t.cancel()
        _flush_geom()
        api.cleanup_and_exit()

    def _on_loaded():
        _apply_macos_app_identity()
        # Apply the saved x/y AFTER the window has been shown centered.
        # By this point cocoa has a valid screen() for the window, so
        # windowDidMove_ callbacks triggered by .move() won't hit the
        # None-screen crash path.
        if pending_move is not None:
            try:
                window.move(pending_move[0], pending_move[1])
            except Exception as e:
                print(f"[shellframe] post-show move to {pending_move} "
                      f"failed: {e}", file=sys.stderr)
        # Spaces-aware activation: tag each NSWindow with
        # MoveToActiveSpace so that when the global hotkey activates the
        # app, the window moves to the user's CURRENT space instead of
        # warping the user to whichever space the window happened to be
        # on. the user uses Mission Control heavily — the default behaviour
        # (space-switch to window) breaks flow; "window comes to me"
        # matches his ask ("隨傳隨到").
        try:
            if sys.platform == "darwin":
                from AppKit import NSApp
                from Foundation import NSOperationQueue
                MOVE_TO_ACTIVE_SPACE = 1 << 1  # NSWindowCollectionBehaviorMoveToActiveSpace
                # macOS 26+ enforces main-thread-only NSWindow mutation and
                # SIGTRAPs otherwise. _on_loaded fires on pywebview's event
                # thread, so dispatch the setCollectionBehavior loop back
                # onto the main queue.
                def _apply_collection_behavior():
                    for w in NSApp.windows():
                        try:
                            w.setCollectionBehavior_(
                                w.collectionBehavior() | MOVE_TO_ACTIVE_SPACE
                            )
                        except Exception:
                            _swallow("_on_loaded._apply_collection_behavior:7028")
                NSOperationQueue.mainQueue().addOperationWithBlock_(
                    _apply_collection_behavior
                )
        except Exception as e:
            print(f"[shellframe] setCollectionBehavior failed: {e}",
                  file=sys.stderr)
        api._start_output_pusher()
        api._start_status_monitor()

    window.events.loaded += _on_loaded
    window.events.closed += _on_closed_save_and_cleanup

    # Global hotkey Ctrl+⌥+Space — register after window exists so NSApp
    # has been spun up by pywebview. Settings-gated; flip off in Settings
    # → General and call api.reload_global_hotkey() to take effect.
    _register_global_hotkey()
    api._start_command_watcher()
    api._start_api_server()
    api._start_frame_link()
    api._start_delay_scheduler()
    webview.start(debug=("--debug" in sys.argv))

    # If webview.start() returns but process is still alive, force exit
    api.cleanup_all()
    os._exit(0)


def _write_crash_log(exc: BaseException):
    """Dump traceback + recovery hint so users can diagnose startup failures.
    Windows under pythonw has no console, so printing isn't enough."""
    try:
        import traceback as _tb
        crash_file = Path.home() / ".shellframe-crash.log"
        with open(crash_file, "w", encoding="utf-8") as f:
            f.write(f"shellframe startup crash at {datetime.now().isoformat()}\n")
            f.write(f"python: {sys.executable}\n")
            f.write(f"cwd: {os.getcwd()}\n\n")
            _tb.print_exception(type(exc), exc, exc.__traceback__, file=f)
            f.write("\n\nRecover with:\n")
            f.write("  curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash\n")
        print(f"[shellframe] crash log written to {crash_file}", file=sys.stderr)
        # macOS: surface a dialog so the user's colleagues see the recovery command
        if sys.platform == "darwin":
            try:
                subprocess.run([
                    "osascript", "-e",
                    'display dialog "ShellFrame failed to start.\n\nRecover by running in Terminal:\n\ncurl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash\n\nDetails: ~/.shellframe-crash.log" '
                    'with title "ShellFrame" buttons {"OK"} default button 1'
                ], capture_output=True, timeout=30)
            except Exception:
                _swallow("_write_crash_log:7077")
    except Exception:
        _swallow("_write_crash_log:7079")


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        _write_crash_log(e)
        raise
