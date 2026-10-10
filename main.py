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
import tempfile
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
import agent_model
import agent_status
import agent_grok
import account_manager
import sf_config
from api_history import HistoryApiMixin
from api_schedules import SchedulesApiMixin
# Api 的各領域 mixin 透過 api_host 的 late-bound `main` 取用本模組的全域；
# 必須在任何 mixin 方法被呼叫前綁定（見 api_host.py）。
import api_host
api_host.bind(globals())
from api_accounts import AccountsApiMixin
from api_bridges import BridgesApiMixin
from api_desktop import DesktopApiMixin
from api_extensions import ExtensionsApiMixin
from api_glasses import GlassesApiMixin
from api_link import LinkApiMixin
from api_remote import RemoteApiMixin
from api_status import StatusApiMixin
from api_update import UpdateApiMixin
from api_voice import VoiceApiMixin
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
        # Agents addressing each other with [[SF:TO:<role>|<text>]]. Off by
        # default: delivery writes into another agent's prompt unattended, and
        # every tab runs with permissions bypassed. Rules live in agent_link.py.
        "experimental_a2a": False,
        # Role groups: one message to several agents, replies in one thread.
        # Off by default — sending drives several permission-bypassed agents at
        # once. Rules live in agent_group.py.
        "experimental_groups": False,
        # Install a pointer skill into ~/.claude/skills/shellframe/ on start, so
        # an agent in a tab discovers `sfctl skill` without being told. On by
        # default: it is a directory only ShellFrame writes, and it holds a
        # pointer rather than a copy, so there is nothing to go stale. See
        # ai_skill.py.
        "ai_skill_autoinstall": True,
        # Emoji receipts on the user's own Telegram messages (👀 / 🫡). Off:
        # they mark up the user's chat history, and the delivery warning covers
        # the silence they were added for.
        "tg_reactions": False,
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
        # 公網／手機：relay = TG 式出站長輪詢（電腦不用開 port）；public_host =
        # 有 port-forward 時對外的 IP／網域，會一起放進配對 QR。
        "relay": {"url": "", "token": ""},
        "public_host": "",
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
    # 同 Claude／Codex 是自主模式；bypassPermissions 不會寫進 grok 的 config。
    # 模型刻意不指定，交給 grok 自己的預設（/model 的選擇它會自己存）。
    {"name": "Grok Build", "cmd": "grok --permission-mode bypassPermissions", "icon": "\U0001D54F"},  # 𝕏
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


def _worker_is_claude(cmd: str) -> bool:
    """這個分頁跑的是 claude 嗎。用 worker_kind 而不是自己比字串——它認得
    wrapper（sf-claude-home 之類），跟狀態、模型、帳號判斷同一支分類器。"""
    return _session_provider(cmd) == "claude"


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
# 所有 config.json 寫入者共用的鎖（見 sf_config.py）；TG bridge 與 mixin 也用同一把。
_CONFIG_LOCK = sf_config.CONFIG_LOCK


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


# 最後一次成功解析的 config.json 原文。讀到壞檔（另一個寫入者寫到一半、磁碟錯誤）
# 時退回這份，而不是 DEFAULT_CONFIG——後者一旦被任何呼叫端 save 回去，使用者的
# presets、帳號、bot token、配對對象就全被洗掉。
_LAST_GOOD_CONFIG_TEXT = None


def _read_config_json():
    """Parse config.json; on failure retry briefly, then fall back to the last good copy.

    Returns None only when the file cannot be parsed and nothing good was ever read.
    """
    global _LAST_GOOD_CONFIG_TEXT
    for attempt in range(3):
        try:
            text = CONFIG_FILE.read_text(encoding='utf-8')
            cfg = json.loads(text)
            _LAST_GOOD_CONFIG_TEXT = text
            return cfg
        except Exception:
            if attempt < 2:
                time.sleep(0.05)
    if _LAST_GOOD_CONFIG_TEXT is not None:
        _dlog("config", "config.json unreadable; using the last good copy instead of defaults")
        return json.loads(_LAST_GOOD_CONFIG_TEXT)
    return None


def load_config():
    if CONFIG_FILE.exists():
        cfg = _read_config_json()
        if cfg is None:
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
            existing = cfg.get("presets", []) or []
            existing_cmds = {(p.get("cmd") or "").strip() for p in existing}
            # 名稱也要比。cmd 比對是精確字串，使用者一旦改過內建 preset 的指令
            # （例如把 opencode 換成絕對路徑）就對不上了；此時只要 offered 記錄
            # 因為任何原因回退——config 從舊備份還原、或只剩舊的
            # _default_ai_presets_migrated 旗標（它只代表 Claude/Codex）——同名的
            # preset 就會被再加一次，清單裡出現兩個一樣的東西。
            existing_names = {(p.get("name") or "").strip() for p in existing}
            for preset in _DEFAULT_AI_PRESETS:
                if preset["name"] in offered:
                    continue
                if (preset["cmd"] not in existing_cmds
                        and preset["name"] not in existing_names):
                    cfg.setdefault("presets", []).append(dict(preset))
                    existing_names.add(preset["name"])
                    existing_cmds.add(preset["cmd"])
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


def _claude_config_roots() -> list:
    """ShellFrame 可能把 claude 開進去的每一個設定家目錄：預設的 ~/.claude，
    加上 account-profiles/claude/ 底下每個帳號 profile。"""
    roots = [os.path.expanduser("~/.claude")]
    try:
        base = CONFIG_DIR / "account-profiles" / "claude"
        roots += sorted(str(p) for p in base.iterdir() if p.is_dir())
    except OSError:
        pass
    return roots


def _same_dir(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (os.path.realpath(os.path.expanduser(a))
                                    == os.path.realpath(os.path.expanduser(b)))


def _claude_dir_for_live_pids(pids, csid: str = "", roots=None) -> str:
    """跑著的 claude 行程**真正**用的設定家目錄。

    Claude Code 會替每個執行中的行程寫 `<設定家目錄>/sessions/<pid>.json`，
    那個檔案落在哪個目錄，那就是它吃的 CLAUDE_CONFIG_DIR，不管這個值是從哪裡
    繼承來的。`account_refs` 跟 tmux 的 session env 都可能沒有它：舊的 tmux
    server 全域環境曾經帶過 CLAUDE_CONFIG_DIR，後來拿掉了，在那之前開的分頁
    就是「隱性」跑在帳號 profile 裡（實例：9 個分頁 relaunch 後直接消失）。
    同一個 pid 對到好幾個目錄時，以 sessionId 相符的為準。"""
    fallback = ""
    for pid in pids or []:
        for root in (roots if roots is not None else _claude_config_roots()):
            path = os.path.join(root, "sessions", f"{pid}.json")
            if not os.path.isfile(path):
                continue
            if not csid:
                return root
            try:
                with open(path, encoding="utf-8") as f:
                    live_sid = (json.load(f) or {}).get("sessionId")
            except (OSError, ValueError):
                live_sid = None
            if live_sid == csid:
                return root
            fallback = fallback or root
    return fallback


_RESUME_UUID_RE = re.compile(
    r"--(?:resume|session-id)[= ]([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")


def _claude_session_hint(pids, cmd: str, roots=None) -> str:
    """記憶體裡沒有 session uuid 時，這個分頁最可信的 uuid。

    ShellFrame 重開之後、分頁還沒送出任何 hook 事件之前，`session_id` 是空的
    （閒置分頁可以一直空著）。先看跑著的行程自己寫的 sessions/<pid>.json，再退回
    啟動指令裡的 `--resume／--session-id <uuid>`。"""
    for pid in pids or []:
        for root in (roots if roots is not None else _claude_config_roots()):
            path = os.path.join(root, "sessions", f"{pid}.json")
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    live_sid = (json.load(f) or {}).get("sessionId") or ""
            except (OSError, ValueError):
                live_sid = ""
            if live_sid:
                return live_sid
    m = _RESUME_UUID_RE.search(cmd or "")
    return m.group(1) if m else ""


def _claude_newest_transcript(csid: str, roots=None) -> str:
    """所有設定家目錄裡 `<csid>.jsonl` 最新的那一份（沒有就回空字串）。

    同一段對話可能有好幾份：切帳號時複製過、或預設目錄殘留一份很久以前的。
    正在寫的那份一定最新，也最大（實例：舊副本 599 行、真正的 2952 行，
    挑錯就是「重啟成功但對話退回 23 天前」，而且沒有任何錯誤訊息）。"""
    if not csid:
        return ""
    best, best_key = "", None
    for root in (roots if roots is not None else _claude_config_roots()):
        for path in glob.glob(os.path.join(root, "projects", "*", f"{csid}.jsonl")):
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = (st.st_mtime, st.st_size)
            if best_key is None or key > best_key:
                best, best_key = path, key
    return best


def _claude_home_of_transcript(path: str) -> str:
    """`<家目錄>/projects/<slug>/<uuid>.jsonl` → `<家目錄>`。"""
    if not path:
        return ""
    return os.path.dirname(os.path.dirname(os.path.dirname(path)))


def _claude_ensure_transcript_in(csid: str, target_home: str, src: str = "",
                                 roots=None) -> str:
    """確保 `target_home` 底下有這段對話**最新**的 transcript，`--resume` 才接得
    回完整歷史。回傳 target 那份的路徑（做不到就回空字串）。

    target 沒有 → 複製進去。target 已經有、但比來源舊又比較短 → 先把它改名留底
    （`.sf-bak-<時間>`，不刪），再換成新的。舊版只在「沒有」時才複製，target
    若殘留一份舊副本就會悄悄接回舊對話。"""
    src = src if (src and os.path.isfile(src)) else _claude_newest_transcript(csid, roots)
    if not (src and target_home):
        return ""
    if _same_dir(_claude_home_of_transcript(src), target_home):
        return src
    dst_dir = os.path.join(target_home, "projects", os.path.basename(os.path.dirname(src)))
    dst = os.path.join(dst_dir, f"{csid}.jsonl")
    try:
        os.makedirs(dst_dir, exist_ok=True)
        if os.path.exists(dst):
            s_st, d_st = os.stat(src), os.stat(dst)
            if not (s_st.st_mtime > d_st.st_mtime and s_st.st_size > d_st.st_size):
                return dst
            os.replace(dst, f"{dst}.sf-bak-{int(time.time())}")
        shutil.copy2(src, dst)
        _dlog("account", f"transcript {csid[:8]} → {dst}")
        return dst
    except OSError as e:
        _dlog("account", f"transcript {csid[:8]} 複製失敗 {e}")
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
    # 配色用帶外的方式告訴應用程式。想知道背景是深是淺的 TUI 會送 OSC 11 查詢，
    # 而終端的回覆是走「輸入」這條路回去的——問的那一方只要已經不在讀了（啟動
    # 過程中很常見），那段回覆就原封不動變成使用者輸入框裡的一串亂碼。前端因此
    # 不在頻內回答那個查詢（見 web/index.html 的 swallowColorQueries），改用這個
    # 幾十年來就有的環境變數：15;0＝淺前景、深背景，對應 ShellFrame 固定的深色
    # 主題。應用程式讀得到答案，而且它不可能變成輸入。
    env.setdefault("COLORFGBG", "15;0")
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


def _claude_project_key(path: str) -> str:
    """Claude Code projects key normaliser. On Windows Claude stores the key
    with forward slashes ("C:/Users/x") while self.cwd has back slashes, so a
    naive lookup never matches and trust is copied nowhere (the 0.35.16 gap).
    Only swap the separator -- no resolve()/abspath(), which would rewrite a
    unix-style test path into an absolute Windows one."""
    raw = os.path.expanduser(path or "~")
    return raw.replace("\\", "/") if IS_WIN else raw


def _match_project_entry(projects: dict, cwd: str):
    """Find this cwd's entry in a projects dict, comparing normalised
    (separator + case). Returns (existing_key, entry) or (None, None)."""
    if not isinstance(projects, dict):
        return None, None
    want = os.path.normcase(_claude_project_key(cwd))
    for k, v in projects.items():
        try:
            if os.path.normcase(_claude_project_key(k)) == want:
                return k, v
        except Exception:
            continue
    return None, None


def _read_json_obj(path) -> dict:
    try:
        path = Path(path)
        if path.exists():
            v = json.loads(path.read_text(encoding="utf-8"))
            return v if isinstance(v, dict) else {}
    except Exception:
        pass
    return {}


def _atomic_write_json(target, blob: dict):
    """Atomic-replace write. Claude Code writes .claude.json too; a half file
    makes it reset its whole config, so never rewrite in place."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                               prefix=".claude.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cwd_trusted_in_any_config(cwd: str) -> bool:
    """Has the user explicitly trusted this cwd in ANY config -- the canonical
    ~/.claude.json or any account profile. Only an explicit True counts; this
    is "a decision the user made", not one made for them."""
    home = os.path.expanduser("~")
    paths = [Path(home) / ".claude.json"]
    try:
        paths += [Path(x) for x in glob.glob(os.path.join(
            home, ".config", "shellframe", "account-profiles",
            "*", "*", ".claude.json"))]
    except Exception:
        pass
    for pth in paths:
        _, entry = _match_project_entry(_read_json_obj(pth).get("projects") or {}, cwd)
        if (entry or {}).get("hasTrustDialogAccepted") is True:
            return True
    return False


# Mirror these one-time first-run flags from the canonical ~/.claude.json into
# an account profile so a tab pinned to a switched account does not re-ask
# onboarding / the fullscreen-renderer upsell. Only values the canonical config
# already carries are copied -- nothing is fabricated.
_FIRST_RUN_MIRROR_KEYS = (
    "hasCompletedOnboarding",
    "hasCompletedProjectOnboarding",
    "lastOnboardingVersion",
    "fullscreenUpsellSeenCount",
    "hasSeenTasksHint",
    "hasUsedBackslashReturn",
    "effortCalloutV2Dismissed",
)


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
                 account_refs_authoritative: bool = False, claude_home: str = ""):
        self.sid = sid
        # 沒 pin 帳號、對話卻住在某個 claude 家目錄（從環境繼承來的）時，重開要
        # 照原樣只帶 CLAUDE_CONFIG_DIR。不改 pin：pin 會連帶把 profile 當下的
        # access token 寫死進環境變數，那個 token 幾小時就過期、行程又不會自己
        # 換新，分頁之後就變成 401 要重新 /login。只給家目錄，claude 會自己更新
        # 那個目錄裡的憑證。
        self._claude_home_override = claude_home or ""
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
        if self.session_id is None:
            # grok 沒有 hook 會回報 session，只能自己指定 uuid（見 agent_grok.pin_session_id）
            self.cmd, self.session_id = agent_grok.pin_session_id(self.cmd)
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
        home = getattr(self, "_claude_home_override", "")
        if home and not self.account_refs.get("claude"):
            overrides["CLAUDE_CONFIG_DIR"] = home
        return overrides

    def _launch_env(self) -> dict:
        """Build the child environment for this tab's account snapshot."""
        env = _session_env()
        env.update(self._account_env_overrides())
        return env

    def _carry_trust_to_profile(self, env: dict):
        """Answer Claude Code's startup questions by writing config BEFORE
        spawn, instead of letting a keystroke-sending watcher race the
        full-screen TUI after the dialog appears (it loses -- debug log:
        "trust dialog still up after keys").

        1. Trust dialog: if the user has trusted this cwd in the canonical
           config or any account profile, OR this is the range the watcher
           already auto-accepts (home dir + an AI tab), write trust into the
           canonical config and this tab's account profile. Otherwise do
           nothing -- the dir still prompts, the decision stays the user's.
        2. One-time prompts (onboarding, fullscreen upsell): mirror the
           flags the canonical config already carries into the profile.
        3. Once trust is established, clear _startup_trust_pending so the
           watcher never arms; it stays only as a last resort for when the
           pre-seed could not establish trust (e.g. canonical unreadable).

        Every write goes through _atomic_write_json (Claude Code writes this
        file too); a corrupt file is rebuilt; other keys are preserved. All
        three spawn paths (tmux / unix / windows) call this before spawn.
        """
        if not _worker_is_claude(self.cmd):
            return
        cwd = self.cwd or os.path.expanduser("~")
        config_dir = (env or {}).get("CLAUDE_CONFIG_DIR")
        canonical = Path(os.path.expanduser("~/.claude.json"))
        try:
            implicit = _should_auto_accept_startup_trust(self.cmd, cwd)
            trusted = implicit or _cwd_trusted_in_any_config(cwd)
            if not trusted and not config_dir:
                return

            # Canonical: only correct it inside the implicit range (home dir
            # + AI tab -- the same circle the watcher already auto-accepts)
            # and only when the entry is missing / not True. Trust for any
            # other dir is never decided on the user's behalf.
            if trusted and implicit:
                cblob = _read_json_obj(canonical)
                _, centry = _match_project_entry(cblob.get("projects") or {}, cwd)
                if (centry or {}).get("hasTrustDialogAccepted") is not True:
                    cprojects = cblob.setdefault("projects", {})
                    if not isinstance(cprojects, dict):
                        cprojects = cblob["projects"] = {}
                    ck, centry = _match_project_entry(cprojects, cwd)
                    ck = ck or _claude_project_key(cwd)
                    centry = centry or {}
                    centry["hasTrustDialogAccepted"] = True
                    cprojects[ck] = centry
                    try:
                        _atomic_write_json(canonical, cblob)
                        _dlog("trust", f"{self.sid} seeded canonical trust for {cwd}")
                    except Exception as e:
                        _dlog("trust", f"{self.sid} canonical seed failed: {e}")

            established = trusted and (_match_project_entry(
                _read_json_obj(canonical).get("projects") or {}, cwd)[1]
                or {}).get("hasTrustDialogAccepted") is True

            if config_dir:
                target = Path(config_dir) / ".claude.json"
                blob = _read_json_obj(target)
                changed = False

                if trusted:
                    projects = blob.setdefault("projects", {})
                    if not isinstance(projects, dict):
                        projects = blob["projects"] = {}
                    pk, entry = _match_project_entry(projects, cwd)
                    pk = pk or _claude_project_key(cwd)
                    entry = entry or {}
                    if entry.get("hasTrustDialogAccepted") is not True:
                        entry["hasTrustDialogAccepted"] = True
                        changed = True
                    projects[pk] = entry

                cblob = _read_json_obj(canonical)
                for k in _FIRST_RUN_MIRROR_KEYS:
                    if k not in cblob:
                        continue
                    cv, pv = cblob[k], blob.get(k)
                    if isinstance(cv, bool):
                        if cv and not pv:
                            blob[k] = True
                            changed = True
                    elif isinstance(cv, (int, float)):
                        if (not isinstance(pv, (int, float))) or isinstance(pv, bool) or pv < cv:
                            blob[k] = cv
                            changed = True
                    elif isinstance(cv, str):
                        if not pv:
                            blob[k] = cv
                            changed = True

                if changed:
                    try:
                        _atomic_write_json(target, blob)
                        _dlog("trust", f"{self.sid} seeded {Path(config_dir).name} for {cwd}")
                    except Exception as e:
                        _dlog("trust", f"{self.sid} profile seed failed: {e}")
                        return
                if trusted:
                    _, e2 = _match_project_entry(
                        _read_json_obj(target).get("projects") or {}, cwd)
                    if (e2 or {}).get("hasTrustDialogAccepted") is True:
                        established = True

            if established:
                self._startup_trust_pending = False
        except Exception as e:
            _dlog("trust", f"{self.sid} carry trust failed: {e}")

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
            # 在 spawn 之前把信任決定帶進 profile——之後才寫就來不及，對話框
            # 已經跳出來了。
            self._carry_trust_to_profile(launch_env)
            # Per-session env vars must be passed with `-e KEY=VAL`, NOT via
            # subprocess env: `tmux new-session` spawns the pane from the tmux
            # SERVER's environment, so `env=launch_env` is silently ignored
            # whenever a server already exists (i.e. any time there's more than
            # the first tab). The account overrides (CLAUDE_CONFIG_DIR /
            # CLAUDE_CODE_OAUTH_TOKEN / CODEX_HOME) live here — without `-e`
            # they never reach the child, so an account switch launches with the
            # wrong/default credentials and the tab looks logged-out
            # (reported in daily use: 切換 token 後對話消失、要重新 /login).
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
        self._carry_trust_to_profile(env)
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
        self._carry_trust_to_profile(env)
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


def _clipboard_utf16(text: str) -> bytes:
    """CF_UNICODETEXT payload: UTF-16LE, CRLF newlines, one NUL terminator.

    No BOM. The clipboard format is UTF-16LE by definition, so a BOM is not
    needed and would be stored as a literal U+FEFF in front of the copied text
    (which is exactly what feeding `clip.exe` a BOM did). 'surrogatepass'
    because a JS selection can end on half a surrogate pair; a strict encode
    would raise and lose the whole copy."""
    t = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return (t + "\0").encode("utf-16le", "surrogatepass")


_WIN_CLIP = None  # (user32, kernel32) with prototypes set, built on first use


def _win_clip_api():
    global _WIN_CLIP
    if _WIN_CLIP is None:
        from ctypes import wintypes as wt
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Explicit prototypes: without them ctypes marshals handles as 32-bit
        # ints and truncates them on 64-bit Windows.
        u32.OpenClipboard.argtypes = [wt.HWND]
        u32.OpenClipboard.restype = wt.BOOL
        u32.CloseClipboard.argtypes = []
        u32.CloseClipboard.restype = wt.BOOL
        u32.EmptyClipboard.argtypes = []
        u32.EmptyClipboard.restype = wt.BOOL
        u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
        u32.SetClipboardData.restype = wt.HANDLE
        u32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        u32.CreateWindowExW.restype = wt.HWND
        u32.DestroyWindow.argtypes = [wt.HWND]
        u32.DestroyWindow.restype = wt.BOOL
        k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
        k32.GlobalAlloc.restype = wt.HGLOBAL
        k32.GlobalLock.argtypes = [wt.HGLOBAL]
        k32.GlobalLock.restype = wt.LPVOID
        k32.GlobalUnlock.argtypes = [wt.HGLOBAL]
        k32.GlobalUnlock.restype = wt.BOOL
        k32.GlobalFree.argtypes = [wt.HGLOBAL]
        k32.GlobalFree.restype = wt.HGLOBAL
        _WIN_CLIP = (u32, k32)
    return _WIN_CLIP


def _win_set_clipboard_text(text: str) -> None:
    """Put `text` on the Windows clipboard as CF_UNICODETEXT via the Win32 API.

    Replaces piping into clip.exe, which cannot be fed UTF-16 cleanly: without
    a BOM it guesses the encoding from the byte pattern and garbles text that
    is all CJK (no ASCII bytes to give it away); with a BOM it keeps the BOM as
    a literal U+FEFF. Raises OSError (with the Win32 error code) on failure."""
    u32, k32 = _win_clip_api()
    data = _clipboard_utf16(text)
    hmem = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not hmem:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ptr = k32.GlobalLock(hmem)
        if not ptr:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(ptr, data, len(data))
        k32.GlobalUnlock(hmem)
        # A real owner window: documented behaviour when OpenClipboard(NULL) is
        # used is that EmptyClipboard leaves no owner and SetClipboardData fails.
        hwnd = u32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # Another process (clipboard manager, remote session) can hold the
            # clipboard open for a moment; retry ~0.5s before giving up.
            for _ in range(20):
                if u32.OpenClipboard(hwnd):
                    break
                time.sleep(0.025)
            else:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not u32.EmptyClipboard():
                    raise ctypes.WinError(ctypes.get_last_error())
                if not u32.SetClipboardData(13, hmem):  # CF_UNICODETEXT
                    raise ctypes.WinError(ctypes.get_last_error())
                hmem = None  # the system owns the buffer now
            finally:
                u32.CloseClipboard()
        finally:
            u32.DestroyWindow(hwnd)
    finally:
        if hmem:
            k32.GlobalFree(hmem)


class Api(HistoryApiMixin, SchedulesApiMixin,
          AccountsApiMixin, BridgesApiMixin, DesktopApiMixin, ExtensionsApiMixin,
          GlassesApiMixin, LinkApiMixin, RemoteApiMixin, StatusApiMixin,
          UpdateApiMixin, VoiceApiMixin):
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
        # sid -> (checked_at, reason). Why a quiet tab is quiet: a dialog it is
        # waiting on, or '' for genuinely idle. Rate-limited; see _BLOCKED_TTL.
        self._blocked_cache = {}
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
            # grok 的 session 目錄要跑 pgrep／lsof（快取未命中時好幾次子行程），
            # 不能拿著 config 鎖去做——所以在鎖外先解析好，鎖裡只讀結果。
            grok_sids = {sid: agent_grok.session_id(self._worker_ctx(sid, s))
                         for sid, s in list(self.sessions.items())
                         if _session_provider(getattr(s, "cmd", "")) == "grok"}
            with _CONFIG_LOCK:
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
                    if _session_provider(getattr(s, "cmd", "")) == "grok":
                        # grok 的 uuid 不是 claude 的 transcript id，寫進 claude_session_id
                        # 會被當成 claude 的 --resume。目錄以 lsof 為準，/new 之後會跟到新的。
                        grok_sid = grok_sids.get(sid) or hook_csid
                        if grok_sid:
                            entry["grok_session_id"] = grok_sid
                    elif hook_csid:
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
            # A role may pin its own model. The dispatcher is usually the strong
            # model and the workers it opens need not be, and leaving that to
            # whatever the CLI defaults to is how a tab ends up stopped on a
            # model chooser with a delegated message stuck behind it.
            clean["model"] = str(clean.get("model") or "").strip()
            if clean["model"]:
                try:
                    import agent_model
                    clean["cmd"] = agent_model.apply(clean["cmd"], clean["model"])
                except Exception:
                    _swallow("Api._agent_roster_config:model")
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
            "  - 需要使用者／總控決策 → `[[SF:RED]]` → 🔴紅，並接編號選單（選項即決策內容），讓 TG 把選項推給使用者。\n"
            "  - 卡在『外部條件』（等人回覆、等他隊、等外部事件等偵測看不到的）→ `[[SF:YELLOW:一句話原因]]` → 🟡黃，原因推給使用者。\n"
            "- 這兩個是提示不是狀態回報；不確定就不要印，working／done 由偵測涵蓋。決策回合仍要附編號選單供使用者選擇。\n"
            "\n收尾規則（務必遵守）：\n"
            "- 每次『完成任務』或『需要使用者／總控決策』時，回合最後務必輸出一個編號選項選單（搭配上面 GREEN／RED 燈號），"
            "讓 ShellFrame 偵測為待決策、把選項以 TG 按鈕推給使用者。不要只用純文字結尾後 idle。\n"
            "- 選單格式硬規則：至少 2 項，每項自成一行、行首為「數字.」或「數字)」，連續排列、中間不夾其他文字或空行，例如：\n"
            "1. 回收此 tab（任務完成）\n"
            "2. 還要調整：<說明>\n"
            "- 『先沉澱記憶，才可被回收』：在選單提供『回收此 tab』選項前，必須先確認已把本次洞察／學習／"
            "操作 gotcha 寫入 memory（~/.claude/projects/<專案 slug>/memory/），或在該選項旁註明「此任務無需記憶」。"
            "這也是 GREEN 的前提——記憶沉澱完才給綠燈。\n"
        )
        user_prompt = bridge_telegram.load_user_instructions(max_chars=2000)
        if user_prompt:
            prompt += (
                "\n## User Instructions (excerpt)\n\n"
                f"{user_prompt}\n"
            )
        return prompt

    def _wait_until_ready(self, sid: str, timeout: float = 45.0) -> bool:
        """Block until a just-opened AI tab can accept a pasted prompt.

        The same gate the Telegram bridge applies to a tab's first injection,
        reused here rather than reimplemented: a CLI still on its startup screen
        turns a paste plus Enter into an answer to whatever dialog is up. Polls
        rather than watches output because a TUI redraws constantly, so "output
        stopped" says nothing about readiness."""
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            s = self.sessions.get(sid)
            if not s or not getattr(s, "alive", False):
                return False
            if self.is_session_ready_for_bridge(sid) and not self.startup_dialog_blocking(sid):
                return True
            time.sleep(0.4)
        return False

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
        if created and not self._wait_until_ready(sid):
            # Measured: an AI CLI needs seconds to reach its prompt, and text
            # pasted before then is eaten by the startup screen — the tab exists,
            # the call reports success, and nothing was ever asked. Rare when
            # delegating to a tab that is already open; the normal case for a
            # group fan-out, which opens every member that is not running.
            return {"success": False,
                    "message": f"{label}（{sid}）開起來了，但等不到它就緒，這則沒有送出",
                    "details": {"sid": sid, "label": label, "role": resolved_role,
                                "created": True, "not_ready": True}}
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

    # provider → 那個 CLI 用哪個環境變數指 config 目錄
    _CONFIG_DIR_ENV = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR"}

    @staticmethod
    def _provider_config_dir(provider: str, ref) -> str:
        """provider＋account ref → 那個帳號的 config 目錄（''＝沒 pin profile）。"""
        if not ref:
            return ""
        try:
            env = ACCOUNT_MANAGER.env_for(provider, ref) or {}
        except Exception:
            return ""
        return env.get("CODEX_HOME") or env.get("CLAUDE_CONFIG_DIR") or ""

    def _live_config_dir(self, s, provider: str) -> str:
        """這個分頁**實際正在用**的 provider config 目錄。

        `account_refs` 不夠可靠：reattach 時它是從 tmux 的 `SF_ACCOUNT_<P>`
        marker 還原的，而那個 marker 只在建立分頁時「有 ref 才寫」。實測有分頁
        的 tmux env 帶著 `CODEX_HOME=<profile>`、卻沒有 marker，於是還原後
        account_refs 是 None——ShellFrame 以為它沒 pin 帳號，解析就回頭去找全域
        路徑，而那個 process 的 rollout 根本不在全域樹裡。

        所以優先讀 provider 自己的環境變數：那是跑起來的 CLI 真正吃的值，也是
        rollout／transcript 實際寫進去的目錄。讀不到才退回 account_refs 的對應。
        每個分頁只問一次 tmux，之後掛在 session 物件上。status monitor 每輪都會
        呼叫這支，而這台機器上光是既有的 capture-pane 就已經會逾時——再加一個
        週期性的 subprocess 進那條路徑，等於拿終端的流暢度去換一個不會變的值。
        帳號切換走 _restart_session_for_account，那會建一個新的 Session 物件，
        快取跟著舊物件一起消失，所以不需要額外的失效機制。
        """
        env_key = self._CONFIG_DIR_ENV.get(provider)
        if not env_key:
            return ""
        cached = getattr(s, "_config_dir_cache", None)
        if cached and cached[0] == provider:
            return cached[1]
        value = ""
        tmux_name = getattr(s, "_tmux_name", None)
        if tmux_name and not IS_WIN:
            value = (_tmux_get_env(tmux_name, env_key) or "").strip()
            if value and not os.path.isdir(value):
                value = ""          # 目錄不在就當沒設，別把解析導到不存在的樹
        if not value and provider == "claude" and tmux_name and not IS_WIN:
            # session env 沒有、不代表行程沒吃：從 tmux 全域環境繼承來的值只
            # 存在行程自己身上。claude 會在它真正的家目錄寫 sessions/<pid>.json。
            home = _claude_dir_for_live_pids(self._pane_pids(s),
                                             getattr(s, "session_id", "") or "")
            if home and not _same_dir(home, os.path.expanduser("~/.claude")):
                value = home
        if not value:
            value = self._provider_config_dir(
                provider, (getattr(s, "account_refs", {}) or {}).get(provider))
        try:
            s._config_dir_cache = (provider, value)
        except Exception:
            _swallow("_live_config_dir:cache")
        return value

    def _worker_ctx(self, sid: str, s) -> dict:
        """這個分頁的解析 context——狀態、模型、transcript 全部吃同一份。

        以前每個呼叫點各自拼一份 dict，而且都少了帳號身分：多帳號是靠 provider
        的 config 目錄做的（codex→CODEX_HOME、claude→CLAUDE_CONFIG_DIR），
        transcript 與 config 都寫在那個目錄底下。少了它，解析會回頭去讀全域路徑
        ——切過帳號的分頁因此讀到別的帳號，甚至別的分頁的對話。

        `config_dir` 空字串＝這個分頁沒有 pin profile，用 provider 的預設位置。
        """
        provider = _session_provider(getattr(s, "cmd", ""))
        config_dir = ""
        try:
            config_dir = self._live_config_dir(s, provider)
        except Exception:
            _swallow(f"_worker_ctx:{sid}")
        return {
            "cmd": getattr(s, "cmd", ""),
            "cwd": getattr(s, "cwd", "~"),
            "tmux_name": getattr(s, "_tmux_name", None),
            "session_id": getattr(s, "session_id", None),
            "transcript_hint": getattr(s, "_hook_transcript_path", None),
            "codex_session_id": getattr(s, "_codex_sid", "") or "",
            "config_dir": config_dir,
        }

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
            ctx = self._worker_ctx(sid, s)
            path = ""
            try:
                path = agent_status.resolve_transcript(ctx) or ""
            except Exception:
                path = ""
            if IS_WIN or not path:
                taken = {getattr(o, "_codex_sid", "") for k, o in self.sessions.items()
                         if k != sid}
                spawn = float(getattr(s, "_spawn_ts", 0.0) or 0.0)
                best = None
                # 掃描限定這個分頁自己的 sessions 根目錄——認領表跨分頁共用，
                # 但候選檔不能跨帳號，否則會認領到別的帳號的 rollout。
                for f in glob.glob(os.path.join(
                        agent_status.codex_sessions_root(ctx),
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
    def _codex_rollout_exists(csid: str, sessions_root: str = "") -> bool:
        """這個 codex session uuid 在磁碟上還找得到 rollout 嗎。

        sessions_root 空＝全域預設位置。帳號 profile 的 rollout 不在全域樹裡，
        少了這個參數，切過帳號的分頁重開機時一律判定「檔不在」→ 不 resume →
        對話看起來憑空消失。
        """
        if not csid:
            return False
        root = sessions_root or os.path.expanduser("~/.codex/sessions")
        try:
            return bool(glob.glob(os.path.join(
                root, "*", "*", "*", f"rollout-*-{csid}.jsonl")))
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
            # 兩個地方都要找：預設帳號在 ~/.claude/projects，而切過帳號的分頁
            # 在 ~/.config/shellframe/account-profiles/<provider>/<acct>/
            # projects/。只找前者的話，切過帳號的分頁重開機一律被判成「找不到
            # transcript」而開新對話——上滾歷史也是栽在同一個假設上。
            roots = [
                os.path.expanduser("~/.claude/projects"),
                os.path.join(str(CONFIG_DIR), "account-profiles", "*", "*", "projects"),
            ]
            for root in roots:
                if _glob.glob(os.path.join(root, "*", f"{csid}.jsonl")):
                    return True
            return False
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

        只處理 claude 與 grok；codex／agy／一般 shell 各有自己的續接方式，不碰。
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
        # ShellFrame 自己的啟動器（sf-codex、sf-claude-home…）都是
        # `exec <真的 CLI> "$@"`，參數原樣透傳——所以續接的處理跟裸指令一樣，
        # 只是名字要認得出來。漏掉的話那些分頁重開機後一律開新對話，而那正是
        # 大家實際在用的指令。
        if exe.startswith("sf-"):
            for part in exe.split("-")[1:]:
                if part in ("claude", "codex", "grok"):
                    exe = part
                    break

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

        if exe == "grok":
            return agent_grok.cmd_with_resume(cmd, csid)

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
        with _CONFIG_LOCK:
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
                # Bridge 可能在這些分頁存在之前就啟動了（見 _sync_bridge_sessions）
                self._sync_bridge_sessions()
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
                # rollout 要在「這個分頁的帳號目錄」底下找。帳號 profile 的
                # rollout 不在全域樹裡，用全域路徑找一定落空，然後就會判定
                # 「找不到記錄檔」而開一個空白對話。
                entry_refs = dict(entry.get("account_refs") or default_account_refs)
                found = self._codex_rollout_exists(
                    csid, agent_status.codex_sessions_root(
                        {"config_dir": self._provider_config_dir("codex",
                                                                 entry_refs.get("codex"))}))
            elif _session_provider(cmd) == "grok":
                # grok 的對話在 ~/.grok/sessions 底下，不走 claude 的家目錄搬移
                csid = (str(entry.get("grok_session_id") or "").strip()
                        or agent_grok.cmd_session_uuid(cmd) or "")
                found = agent_grok.session_exists(csid)
            else:
                # manifest 沒記 uuid（舊分頁常見）就用啟動指令裡的 --resume uuid，
                # 否則下面的家目錄判斷整段跳過，resume 落在錯的目錄、分頁一開就結束。
                csid = (str(entry.get("claude_session_id") or "").strip()
                        or _claude_session_hint([], cmd))
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
                claude_home = ""
                if csid and found and not _worker_is_codex(cmd) and _session_provider(cmd) != "grok":
                    # 「找得到 transcript」不等於「在這個分頁的帳號目錄裡找得到」。
                    # 找得到的是別的家目錄：沒 pin 就照原樣帶那個家目錄重開，有 pin
                    # 就把最新那份搬進 pin 的目錄——否則 resume 落空、分頁一開就結束。
                    home = _claude_home_of_transcript(_claude_newest_transcript(csid))
                    claude_home = self._claude_home_to_keep(account_refs, home)
                    if claude_home:
                        _dlog("lifecycle", f"  {sid} 對話在 {claude_home}，照原樣帶家目錄")
                    elif home and not _same_dir(home, self._claude_config_dir_for(account_refs)):
                        _claude_ensure_transcript_in(
                            csid, self._claude_config_dir_for(account_refs))
                session = Session(sid, cmd, cols, rows,
                                  on_data=self._output_event.set,
                                  tmux_name=tmux_name,
                                  account_refs=account_refs,
                                  claude_home=claude_home)
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
                # codex 沒有 hook 可以回報，manifest 的 uuid 就是它重開之後
                # 唯一的精確錨點——蓋回 session，解析不必等 lsof 命中。
                if _worker_is_codex(cmd) and csid:
                    session._codex_sid = csid
                self._start_startup_trust_watcher(sid, session)
                label = entry.get("label") or saved_labels.get(sid)
                if label:
                    session._custom_label = label
                restored.append({"sid": sid, "cmd": cmd})
            except Exception as e:
                _dlog("lifecycle", f"  soft restore failed for {sid}: {e}")
        if restored:
            self._persist_session_manifest(saved_order)
        # 同上：還原完無條件同步一次
        self._sync_bridge_sessions()
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

    # ── Hook-driven agent status (exact, event-based) ────────────────────
    _HOOK_TTL = 1800.0   # hook state stays authoritative this long after the last event
    _HOOK_EVENTS = ("UserPromptSubmit", "PreToolUse", "Stop", "Notification", "StopFailure")
    _CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

    def get_config(self) -> str:
        return json.dumps(load_config())

    @staticmethod
    def _preset_variant(name: str, title: str) -> str:
        """同一支 CLI 底下這個 preset 的區別字（''＝這是預設的那個）。

        「Claude (家用地端)」在「Claude Code」這一組裡的區別字是「家用地端」：
        把組名的字拿掉、括號與標點剝掉，剩下的就是它跟同組其他成員的差異。
        """
        rest = (name or "").strip()
        for word in (title or "").split():
            rest = re.sub(re.escape(word), "", rest, flags=re.I)
        rest = rest.strip(" ()[]（）【】·-—_/、,，:：")
        return rest.strip()

    def preset_groups(self) -> str:
        """新增分頁對話框用的 preset 分組。

        同一支 CLI 的幾個啟動器（雲端／地端閘門／帶不同旗標）在平面清單裡只差
        一個括號，讀起來像同一個東西出現兩次。按 CLI 收成一組，區別字放在組裡，
        那才看得出是「同一支的兩種接法」。

        分組用的是 `_session_provider`——跟狀態、模型、帳號判斷同一支分類器，
        分頁與 preset 因此不會各有一套說法。認不出 CLI 的（bash 之類）各自
        獨立一組，前端會畫成單獨一列。順序沿用 config 裡的順序，組的位置就是它
        第一個成員的位置，所以既有的清單不會被重排。
        """
        try:
            labels = usage_probe.provider_labels()
        except Exception:
            labels = {}
        groups, index = [], {}
        for preset in (load_config().get("presets") or []):
            name = str(preset.get("name") or "")
            cmd = str(preset.get("cmd") or "")
            kind = _session_provider(cmd)
            key = kind if kind != "other" else f"solo:{name}"
            if key not in index:
                index[key] = len(groups)
                groups.append({
                    "provider": "" if kind == "other" else kind,
                    "title": labels.get(kind) or name,
                    "items": [],
                })
            group = groups[index[key]]
            group["items"].append({
                "name": name,
                "cmd": cmd,
                "icon": preset.get("icon") or "",
                "variant": self._preset_variant(name, group["title"]),
            })
        return json.dumps(groups, ensure_ascii=False)

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
        with _CONFIG_LOCK:
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
        with _CONFIG_LOCK:
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
        with _CONFIG_LOCK:
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
        with _CONFIG_LOCK:
            cfg = load_config()
            cfg["presets"] = [p for p in cfg["presets"] if p["name"] != name]
            save_config(cfg)
        return json.dumps(cfg)

    def reorder_presets(self, order_json: str) -> str:
        """Reorder presets by name list. E.g. ["Bash","Claude Code","Codex"]."""
        with _CONFIG_LOCK:
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

    _SID_LOCK = threading.Lock()

    def _next_sid(self) -> str:
        """Hand out the next tab id. new_session is called from pywebview's per-call
        threads, the sfctl watcher and the bridges at once; an unlocked
        ``self._counter += 1`` can give two tabs the same sid."""
        with self._SID_LOCK:
            self._counter += 1
            return f"s{self._counter}"

    def new_session(self, cmd: str, cols: int, rows: int, source: str = "manual",
                    handoff: bool = False, inherit_accounts: bool = True) -> str:
        cmd = _canonical_cmd(cmd)
        with _CONFIG_LOCK:
            cfg = load_config()
            if ACCOUNT_MANAGER.ensure(cfg):
                save_config(cfg)
        account_refs = ACCOUNT_MANAGER.session_refs(cfg) if inherit_accounts else {
            provider: None for provider in account_manager.PROVIDERS
        }
        sid = self._next_sid()
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
            with _CONFIG_LOCK:
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
    # `❯` (U+276F) is Claude Code's current prompt glyph. It was missing from the
    # bare-prompt alternative, so a freshly opened Claude tab never registered as
    # ready: measured against a live tab whose pane showed `❯ ` and which this
    # pattern still called busy.
    #
    # It is deliberately NOT added to the second alternative. `❯` also marks the
    # highlighted row of a Claude Code menu (`❯ Switch to Sonnet 5 and continue`),
    # and `^\s*❯\s+\S` would read that menu as an input prompt — which is the
    # worst possible misread, since pasting into a menu picks an option.
    # `startup_dialog_blocking` is what recognises menus.
    _AI_READY_RE = _re.compile(
        r'[>›❯]\s*$'          # Claude Code / Codex input prompt (empty input line)
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
        worker = self._worker_ctx(sid, s)
        try:
            path = agent_status.resolve_transcript(worker)
            return agent_status.detect_model_info(
                worker, path if (path and os.path.exists(path)) else None)
        except Exception:
            _swallow(f"get_session_model_info:{sid}")
            return None

    def _agent_activity_for_list(self, sid: str) -> str:
        """One short line of what this tab is doing, for a remote list.

        Same zero-cost rule as the state beside it: read the monitor's snapshot,
        never parse a transcript to fill a list row. A remote viewer showing
        twenty tabs should be able to tell at a glance which one is waiting on
        it, and a bare state word does not carry that."""
        try:
            snap = self._agent_status_snapshot(sid)
            if not snap:
                return ""
            res = snap[0] if isinstance(snap, tuple) else snap
            if not isinstance(res, dict):
                return ""
            for key in ("task", "action", "narration", "summary"):
                v = str(res.get(key) or "").strip()
                if v:
                    return v[:120]
        except Exception:
            _swallow(f"_agent_activity_for_list:{sid}")
        return ""

    def _agent_blocked_for_list(self, sid: str) -> str:
        """Why this tab is waiting on a person, for a remote list.

        Same zero-cost rule again: this reads what the 0.6s monitor already
        worked out. A phone asking twenty tabs for their state must not make the
        computer fork twenty capture-panes."""
        try:
            snap = self._agent_status_snapshot(sid)
            if not snap:
                return ""
            res = snap[0] if isinstance(snap, tuple) else snap
            if isinstance(res, dict):
                return str(res.get("blocked") or "")[:120]
        except Exception:
            _swallow(f"_agent_blocked_for_list:{sid}")
        return ""

    _BLOCKED_TTL = 6.0          # seconds a menu verdict is trusted for

    def _blocked_reason_cached(self, sid: str, out_ts: float, now: float) -> str:
        """Why this tab is waiting on a person, '' when it is not.

        Two gates, because this forks capture-pane and the monitor runs at 0.6s
        across every tab. A dialog appearing is itself output, so a tab that has
        printed nothing since the last verdict cannot have acquired one — that
        alone removes the steady-state cost for a fleet of quiet tabs. The TTL is
        the fallback for anything that changes without printing."""
        cache = self._blocked_cache
        hit = cache.get(sid)
        if hit and hit[2] == out_ts and now - hit[0] < 60.0:
            return hit[1]
        if hit and now - hit[0] < self._BLOCKED_TTL:
            return hit[1]
        try:
            reason = self.startup_dialog_blocking(sid) or ""
        except Exception:
            reason = ""
        cache[sid] = (now, reason, out_ts)
        return reason

    # 多久沒有輸出算「停滯」，--all 沒給門檻時的預設（分鐘）。
    STATE_STALE_DEFAULT_MIN = 15

    def _session_state_row(self, sid: str, s, now: float = None,
                           with_error: bool = True) -> dict:
        """一個分頁的狀態摘要——外部調度者要的最小集合。

        全部是結構化欄位，沒有畫面內容。狀態、活動、阻塞三項讀 status monitor
        已經算好的快照（零額外成本）；錯誤與最後輸出時間是這支自己補的，因為
        `list` 會被週期性拉取，不該為了這兩個欄位讓每一輪都變貴。
        """
        now = now or time.time()
        out_ts = float(getattr(s, "_last_output_activity_time", 0.0) or 0.0)
        state = self._agent_state_for_list(sid) or "idle"
        # 錯誤要解 transcript，是這一列裡唯一有實際成本的欄位。逐頁掃過去的
        # 呼叫端（status）可以關掉；問單一分頁的（state）一定要。
        last_error = ""
        if with_error:
            try:
                last_error = agent_status.last_error(self._worker_ctx(sid, s))
            except Exception:
                last_error = ""
        # runs_on 要的是「現在跑哪個模型」。agent_model.describe 只讀得到啟動
        # 指令裡有沒有 --model，沒指定的分頁就只會回 CLI 名稱；狀態監控本來就
        # 從 transcript 解出了實際模型，先用它。
        runs_on = ""
        try:
            snap = self._agent_status_snapshot(sid)
            res = (snap[0] if isinstance(snap, tuple) else snap) or {}
            mi = res.get("model") if isinstance(res, dict) else None
            if isinstance(mi, dict) and mi.get("name"):
                runs_on = mi["name"] + (f" {mi['effort']}" if mi.get("effort") else "")
        except Exception:
            runs_on = ""
        if not runs_on:
            runs_on = agent_model.describe(getattr(s, "cmd", ""))
        return {
            "sid": sid,
            "label": (getattr(s, "_custom_label", None)
                      or (s.cmd.split()[0] if s.cmd else sid)),
            "agent_state": state,
            # 活動與阻塞同樣過一次遮蔽：活動行會帶正在跑的指令，而指令裡出現
            # 憑證不是罕見的事，這個輸出是要交給外部調度者的。
            "agent_activity": agent_status.redact(
                self._agent_activity_for_list(sid), 120),
            "agent_blocked": agent_status.redact(
                self._agent_blocked_for_list(sid), 120),
            "last_error": last_error,
            "last_output_at": int(out_ts) if out_ts else 0,
            "idle_for_s": int(now - out_ts) if out_ts else -1,
            "runs_on": runs_on,
        }

    def _state_row(self, sid: str, s, now: float = None,
                   with_error: bool = True, stale_min: float = 0) -> dict:
        """一列狀態，並自己標上「需不需要人介入」。

        讓呼叫端不必重算一次同樣的判斷——單獨問一個分頁時，退出碼要能直接說
        「這個分頁有事」，而那個判斷只有這裡知道門檻。
        """
        row = self._session_state_row(sid, s, now, with_error)
        row["needs_attention"] = self._state_row_is_problem(row, stale_min)
        return row

    def _state_row_is_problem(self, row: dict, stale_min: float = 0) -> bool:
        """這一列需不需要人介入。

        三種：在等人回答、對話裡有錯誤、或太久沒有輸出。「太久」由呼叫端給，
        因為合理值取決於那台在跑什麼——長推理的分頁十分鐘不吭聲是正常的。
        沒給就用預設門檻；正在 working 的分頁不算停滯，它本來就在忙。
        """
        if row.get("agent_blocked"):
            return True
        if row.get("last_error"):
            return True
        limit = float(stale_min or self.STATE_STALE_DEFAULT_MIN) * 60
        idle = row.get("idle_for_s", -1)
        if row.get("agent_state") == "working":
            return False
        return idle >= 0 and idle > limit

    def _agent_state_for_list(self, sid: str) -> str:
        """給 sfctl list／Frame Link 用的單字狀態（'working' / 'done' / ''）。

        只讀 status monitor 已經算好的快照——這支會被遠端 peer 週期性拉取，
        成本必須是零；為了一顆燈在列表時去解 transcript 不划算。取不到就回空
        字串，遠端寧可沒有燈，也不要拖慢列表。"""
        try:
            snap = self._agent_status_snapshot(sid)
            if not snap:
                return ""
            res = snap[0] if isinstance(snap, tuple) else snap
            if isinstance(res, dict):
                return str(res.get("state") or "")
        except Exception:
            _swallow(f"_agent_state_for_list:{sid}")
        return ""

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
        return (dict(res, prompt_at=(self._hook_events.get(sid) or {}).get("prompt_at", 0.0)), age) if res else None

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

    # A Claude Code menu: a highlighted row `❯ <something>` with at least one
    # more option under it. Named dialogs come and go with every release — the
    # model/usage-credits chooser ("❯ Switch to Sonnet 5 and continue") did not
    # exist when the trust dialog was written — so this matches the *shape* of a
    # menu instead of its wording, and any new one is caught the day it ships.
    _MENU_RE = _re.compile(
        r'^[ \t]*❯[ \t]+(\S[^\n]*)\n(?:[ \t]*\n)*[ \t]{2,}(\S[^\n]*)$',
        _re.MULTILINE)

    # An empty composer line. Its presence means the CLI is waiting for typing,
    # not for a choice — a menu takes the composer's place rather than sitting
    # above it. Used to veto _MENU_RE, which otherwise matches a `❯ some command`
    # line sitting in scrollback.
    _COMPOSER_RE = _re.compile(r'^[ \t\u00a0]*❯[ \t\u00a0]*$', _re.MULTILINE)

    # 輸入框（composer）的 `❯` 列不一定是空的：Claude Code 會在閒置的輸入列放
    # 一段 dim 灰字的「建議下一句」，使用者也可能留著沒送出的草稿。只看文字，兩者
    # 都是「❯ 某段字」——空輸入列配不到，退去比選單形狀，再被捲動區裡「❯ 上一則
    # 訊息＋縮排的 ⎿ 附件列」或草稿的第二行配中，閒置分頁就被判成「等你選」：TG
    # 第一則訊息被丟掉、還誤報成信任對話框（回報：TG 發的訊息都進不來；實測 22 個
    # 分頁有 3 個這樣卡著）。輸入框的特徵是上下各一條 ─── 框線、`❯` 緊貼在上框線
    # 下面；選單的 `❯` 上面是標題或說明，不會緊貼框線。
    # 上框線可能帶標題（分頁取過名字時：`──── <分頁名稱> ─`），
    # 所以只要求行首是一長串 ─，不要求整行都是。
    _COMPOSER_BORDER_RE = _re.compile(r'^[ \t]*─{10,}')
    _PROMPT_GLYPH_RE = _re.compile(r'^[ \t\u00a0]*❯')

    @classmethod
    def _blank_composer_line(cls, raw: str) -> str:
        """畫面（可帶 ANSI）→ 把輸入框裡 `❯` 那列換成空的 `❯`，其餘原封不動。

        換掉的是建議字或草稿，判斷「是不是停在選單」時兩者都等於空輸入列。"""
        lines = (raw or "").split("\n")
        plain = [cls._ANSI_RE.sub('', line) for line in lines]
        prev = -1
        for i, text in enumerate(plain):
            if (cls._PROMPT_GLYPH_RE.match(text) and prev >= 0
                    and cls._COMPOSER_BORDER_RE.match(plain[prev])
                    and any(cls._COMPOSER_BORDER_RE.match(t) for t in plain[i + 1:i + 13])):
                lines[i] = "❯"
            if text.strip():
                prev = i
        return "\n".join(lines)

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
        clean = self._ANSI_RE.sub('', self._blank_composer_line("\n".join(parts)))
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
        # A menu *replaces* the composer, so a capture that still shows an empty
        # composer line is a working tab, whatever else is on screen. Without
        # this, a tab whose scrollback happens to hold `❯ ls` above an indented
        # line reads as blocked — which would put a red light on an idle tab and,
        # because this same check gates the Telegram bridge's first injection,
        # refuse a perfectly deliverable message.
        m = None if self._COMPOSER_RE.search(clean) else self._MENU_RE.search(clean)
        if m:
            # The wording is carried back, not just the fact of a menu: the
            # point is that the user can read it on their phone and answer,
            # rather than being told something unnamed is in the way.
            first = m.group(1).strip()[:70]
            second = m.group(2).strip()[:70]
            return f"等你選：{first} ／ {second}"
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

    # ── Bridge API ──

    # ── UI 麥克風語音輸入（介面內錄音 → STT → 注入當前分頁）──
    # 錄音走原生 ffmpeg（mac=avfoundation / win=dshow / linux=alsa），不走
    # WKWebView getUserMedia——TCC 歸屬清楚（掛在 ShellFrame.app 下）、
    # 三平台同一條路，且轉出 16kHz mono WAV 正好是 whisper 要的格式。
    _MIC_MAX_SEC = 300

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

    _glasses_transcript_cache: dict = {}

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
        with _CONFIG_LOCK:
            cfg = load_config()
            labels = cfg.get("session_labels", {})
            labels[sid] = name
            cfg["session_labels"] = labels
            save_config(cfg)
        self._persist_session_manifest()
        return json.dumps({"success": True})

    # ── Remote control (sfctl) ──

    _CMD_FILE = str(TMP_DIR / "shellframe_cmd.json")
    _CMD_DIR = str(TMP_DIR / "shellframe_cmds")
    _RESULT_FILE = str(TMP_DIR / "shellframe_result.json")

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

    On Windows we validate via EnumDisplayMonitors so that stale coords
    from a disconnected monitor (e.g. docking-station removed) don't
    cause the window to open off-screen.
    """
    cx = int(x + w / 2)
    cy = int(y + h / 2)

    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes
            monitors = []

            MonitorEnumProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool,
                ctypes.c_ulong, ctypes.c_ulong,
                ctypes.POINTER(ctypes.wintypes.RECT),
                ctypes.c_long,
            )

            def _enum(hmon, hdc, lprect, lparam):
                r = lprect.contents
                monitors.append((r.left, r.top, r.right, r.bottom))
                return 1

            ctypes.windll.user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_enum), 0)
            if not monitors:
                return True  # can't enumerate — don't block
            for left, top, right, bottom in monitors:
                if left <= cx < right and top <= cy < bottom:
                    return True
            return False
        except Exception:
            return True

    if sys.platform == "darwin":
        try:
            from AppKit import NSScreen
        except Exception:
            return True
        screens = list(NSScreen.screens() or [])
        if not screens:
            return False
        primary_h = float(screens[0].frame().size.height)
        cy_cocoa = primary_h - (y + h / 2.0)  # convert to Cocoa bottom-up Y
        for s in screens:
            f = s.frame()
            x_min = float(f.origin.x)
            x_max = x_min + float(f.size.width)
            y_min = float(f.origin.y)
            y_max = y_min + float(f.size.height)
            if x_min <= (x + w / 2.0) <= x_max and y_min <= cy_cocoa <= y_max:
                return True
        return False

    return True


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


def _start_pid_file_keepalive():
    """定期碰一下 PID 檔，免得它被 /tmp 的清理掃掉。

    macOS 會刪除 /tmp 底下三天沒被存取過的檔案。這個檔寫一次就不再碰，所以
    開著超過三天的安裝會失去它——而 `sfctl restart` 的直接路徑靠它找行程，
    於是長時間運作的機器反而重啟不了（實測：行程跑了五天，restart 只回
    「PID file not found」）。一天碰一次就夠，成本是一次 utime。
    """
    def _loop():
        while True:
            time.sleep(21600)          # 6 小時
            try:
                if _PID_FILE.exists():
                    os.utime(_PID_FILE, None)
                else:
                    _PID_FILE.write_text(str(os.getpid()))
            except Exception:
                pass
    threading.Thread(target=_loop, daemon=True,
                     name="sf-pidfile-keepalive").start()


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
        _start_pid_file_keepalive()
    except Exception:
        return
    atexit.register(_release_pid_file)
    if sys.platform != "win32":
        try:
            signal.signal(signal.SIGUSR1, _on_summon_signal)
        except Exception:
            _swallow("_claim_pid_file:6577")


_WIN_MUTEX_HANDLE = None  # keep the mutex referenced for the process lifetime

# restart_app() spawns the new process, then lets the OLD one sleep ~0.8s
# (cleanup_all + os._exit) so its RPC response returns to the UI first. A
# fresh process can finish Python/import startup and reach this check well
# under that — it would see the mutex still held, decide "another instance
# is running", raise the STALE window, and exit. The self-update silently
# no-ops: the new (fixed/updated) process is the one that dies, and the old
# process — carrying whatever bug the update was fixing — is what survives,
# looking to the user like restart did nothing. Retrying for longer than the
# old process's own exit budget closes that race; a genuine second launch
# just waits under 2s longer before being redirected, which is unnoticeable.
_MUTEX_RETRY_ATTEMPTS = 10
_MUTEX_RETRY_DELAY_SEC = 0.2


def _acquire_mutex_with_retry(try_acquire, attempts=_MUTEX_RETRY_ATTEMPTS,
                               delay=_MUTEX_RETRY_DELAY_SEC, sleep=time.sleep):
    """Call `try_acquire()` (→ (handle, acquired)) until it succeeds or the
    retry budget runs out. Pulled out of _ensure_single_instance_windows so
    the retry/backoff behaviour can be tested without real Windows handles.
    """
    result = try_acquire()
    for _ in range(max(0, attempts - 1)):
        if result[1]:
            return result
        sleep(delay)
        result = try_acquire()
    return result


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
        ERROR_ALREADY_EXISTS = 183

        def _try_acquire():
            h = kernel32.CreateMutexW(None, False, "Local\\shellframe-single-instance")
            return h, bool(h and ctypes.get_last_error() != ERROR_ALREADY_EXISTS)

        handle, acquired = _acquire_mutex_with_retry(_try_acquire)
        if acquired:
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
                with _CONFIG_LOCK:
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
    # Telegram must not depend on the window rendering: a dialog or a stalled
    # load would otherwise cut off remote access entirely. Started on a thread
    # so a slow network cannot hold up the window either.
    threading.Thread(target=api._autostart_bridge, daemon=True,
                     name="sf-bridge-autostart").start()
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
