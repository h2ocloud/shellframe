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
import ctypes
import ctypes.util
import errno
import importlib
import json
import os
import platform
import plistlib
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
from api_history import HistoryApiMixin
from api_schedules import SchedulesApiMixin
import usage_probe
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

# AI CLI tools that should receive the init prompt.
# Matched against the base command name (last path component, no extension).
AI_CLI_TOOLS = {"claude", "codex", "sf-codex", "aider", "cursor", "copilot", "goose", "gemini"}
STARTUP_TRUST_AI_TOOLS = {"claude", "codex", "sf-codex"}
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
    ],
    "settings": {
        "fontSize": 14,
        "language": "en",
        "master_turn_preamble_enabled": True,
        "experimental_board": False,
        "experimental_loops": False,
        "show_model_badge": True
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


_DEFAULT_AI_PRESETS = [
    {"name": "Claude", "cmd": "claude --permission-mode bypassPermissions --dangerously-skip-permissions", "icon": "\U0001F680"},
    {"name": "Codex",  "cmd": SHELLFRAME_CODEX_CMD,  "icon": "\U0001F916"},
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
        # One-shot migration for installs that predate the AI-CLI defaults:
        # if neither Claude nor Codex appears in the user's preset list,
        # append them so the "+" menu offers them out of the box. Users who
        # explicitly removed either preset before this migration ran will
        # get them back once — that's acceptable; deleting them again is
        # one click and the flag below blocks future re-adds.
        if not cfg.get("_default_ai_presets_migrated"):
            existing_cmds = {
                (p.get("cmd") or "").strip() for p in cfg.get("presets", []) or []
            }
            for preset in _DEFAULT_AI_PRESETS:
                if preset["cmd"] not in existing_cmds:
                    cfg.setdefault("presets", []).append(dict(preset))
            cfg["_default_ai_presets_migrated"] = True
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
        # One-shot migration: Howard uses ShellFrame through Telegram and
        # wants the agents to execute instead of repeatedly asking for tool
        # approvals. Upgrade the stock Claude/Codex presets only when they
        # are still the old bare commands.
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
                 on_data=None, tmux_name: str = None):
        self.sid = sid
        self.cmd = cmd
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
        self._start(cols, rows)

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
            result = subprocess.run([
                "tmux", "new-session", "-d",
                "-s", self._tmux_name,
                "-x", str(cols), "-y", str(rows),
                "-c", self.cwd,
                "-e", f"SF_SID={self.sid}",
                self.cmd,
            ], capture_output=True, timeout=5, env=_session_env())
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
                _dlog("lifecycle", f"tmux new-session failed name={self._tmux_name} cmd={self.cmd!r} error={detail!r}")
                self.alive = False
                raise RuntimeError(f"tmux failed to create session {self._tmux_name}: {detail or 'unknown error'}")
            # Store original command in tmux environment for recovery
            subprocess.run([
                "tmux", "set-environment", "-t", self._tmux_name,
                "SF_CMD", self.cmd,
            ], capture_output=True, timeout=3, env=_session_env())
            # Persist the claude --session-id so reattach/restart can recover
            # the transcript correlation without guessing.
            if self.session_id:
                subprocess.run([
                    "tmux", "set-environment", "-t", self._tmux_name,
                    "SF_SESSION_ID", self.session_id,
                ], capture_output=True, timeout=3, env=_session_env())
        else:
            # Existing session: the freshly generated session_id is wrong (the
            # running claude already chose one at creation). Recover the real one.
            self.session_id = _tmux_get_env(self._tmux_name, "SF_SESSION_ID") or None
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

        # Attach via PTY fork — child runs `tmux attach`, parent reads master_fd
        self.child_pid, self.master_fd = pty.fork()
        if self.child_pid == 0:
            env = _session_env()
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
        env = _session_env()
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
        env = _session_env()
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
                    with self.lock:
                        self.buffer.extend(data.encode("utf-8", errors="replace") if isinstance(data, str) else data)
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
                entry = {
                    "sid": sid,
                    "cmd": _canonical_cmd(s.cmd),
                    "tmux_name": getattr(s, '_tmux_name', None) or "",
                    "bridge_enabled": bool(bridge_enabled),
                    "order": idx,
                    "updated_at": int(time.time()),
                }
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
                # 會卡在輸入框沒送出（Howard 2026-06-27 實際踩到）。
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
        saved_labels = cfg.get("session_labels", {})
        bridge_disabled = set(cfg.get("bridge_disabled_sessions", []))
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
                if sid in self.sessions:
                    continue  # already attached
                self._counter = max(self._counter, int(sid[1:]) if sid[1:].isdigit() else 0)
                session = Session(sid, cmd, cols, rows,
                                  on_data=self._output_event.set,
                                  tmux_name=tmux_name)
                self.sessions[sid] = session
                # Restore bridge enabled/disabled state from config
                session._bridge_enabled = bool(entry.get("bridge_enabled", sid not in bridge_disabled))
                session._init_pending = False
                session._startup_trust_pending = False
                session._slug_pending = False
                session._lifecycle_source = entry.get("lifecycle_source", "")
                session._lifecycle_handoff = bool(entry.get("lifecycle_handoff", False))
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
            try:
                self._counter = max(self._counter, int(sid[1:]) if sid[1:].isdigit() else 0)
                tmux_name = entry.get("tmux_name") or None
                session = Session(sid, cmd, cols, rows,
                                  on_data=self._output_event.set,
                                  tmux_name=tmux_name)
                self.sessions[sid] = session
                session._bridge_enabled = bool(entry.get("bridge_enabled", sid not in bridge_disabled))
                session._init_pending = False
                session._startup_trust_pending = False
                session._slug_pending = False
                session._lifecycle_source = entry.get("lifecycle_source", "")
                session._lifecycle_handoff = bool(entry.get("lifecycle_handoff", False))
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
                            self._window.evaluate_js(f'_pushOutput("{sid}",{escaped})')
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
            cache = self._status_cache  # sid -> {out_ts, computed_at, since_ts, result}
            last_push = {"key": None, "at": 0.0}
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
                               "label": getattr(s, '_custom_label', None)})
        return json.dumps(result)

    def new_session(self, cmd: str, cols: int, rows: int, source: str = "manual", handoff: bool = False) -> str:
        cmd = _canonical_cmd(cmd)
        self._counter += 1
        sid = f"s{self._counter}"
        _dlog("lifecycle", f"new_session sid={sid} cmd={cmd!r} cols={cols} rows={rows} source={source!r}")
        session = Session(sid, cmd, cols, rows, on_data=self._output_event.set)
        session._lifecycle_source = source or ""
        session._lifecycle_handoff = bool(handoff or source in {"scheduler", "scheduled", "auto"})
        self.sessions[sid] = session
        self._start_startup_trust_watcher(sid, session)
        session._bridge_enabled = True
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
        session._init_pending = self._should_inject_init(cmd)
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
        s._startup_trust_pending = False
        s._startup_trust_answered = True
        _dlog("trust", f"auto-accepted startup trust prompt sid={sid} cwd={getattr(s, 'cwd', '')!r}")
        if getattr(s, '_tmux_name', None):
            try:
                subprocess.run(
                    ["tmux", "send-keys", "-t", s._tmux_name, "Enter"],
                    capture_output=True, timeout=1,
                )
                return
            except Exception:
                _swallow("Api._auto_accept_startup_trust_prompt:2923")
        s.write("\r")

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
        # IME dedup is handled in JS (compositionstart/end + time window)
        # On the first user *content*, inject the init prompt BEFORE the message.
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
        if getattr(s, '_init_pending', False) and self._is_user_content(data):
            # Check if CLI output looks like an AI tool ready for conversation
            # (not a login screen, auth flow, or shell prompt)
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

    def read_output(self, sid: str) -> str:
        """Read buffered output. Used only during reconnect — normal output is pushed."""
        s = self.sessions.get(sid)
        if not s:
            return ""
        return s.read()

    def is_alive(self, sid: str) -> bool:
        s = self.sessions.get(sid)
        return s.alive if s else False

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
            return usage_probe.probe(s.cmd)
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
            return json.dumps(usage_probe.probe_data(cmd))
        except Exception as e:
            return json.dumps({"ai": None, "error": str(e)})

    def resize(self, sid: str, cols: int, rows: int):
        _dlog("resize", f"sid={sid} cols={cols} rows={rows}")
        s = self.sessions.get(sid)
        if s:
            s.resize(cols, rows)



















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

        try:
            req = urllib.request.Request(REPO_URL, headers={"User-Agent": "shellframe"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote = json.loads(resp.read().decode())
        except:
            return json.dumps({"local": local["version"], "remote": None, "update_available": False, "error": "Could not reach GitHub"})

        local_v = tuple(int(x) for x in local["version"].split("."))
        remote_v = tuple(int(x) for x in remote["version"].split("."))
        has_update = remote_v > local_v

        return json.dumps({
            "local": local["version"],
            "remote": remote["version"],
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
                # Howard saw two Dock entries during restart and couldn't
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
            )
            self.bridge.refresh_commands()

    def rename_session(self, sid: str, name: str) -> str:
        """Rename a session. Updates bridge label if connected. Persists to config."""
        _dlog("lifecycle", f"rename_session sid={sid} name={name!r}")
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False})
        s._custom_label = name
        if self.bridge and sid in self.bridge.slots:
            self.bridge.slots[sid].label = name
            self.bridge.refresh_commands()
        if self.line_bridge and sid in self.line_bridge.slots:
            self.line_bridge.slots[sid].label = name
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
                })
            return {
                "success": True,
                "message": f"{len(sessions_info)} sessions",
                "details": {"sessions": sessions_info},
            }

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

    Howard's ask: "滑鼠到哪邊，調用快捷鍵就要啟動在那個視窗" — when
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
    re-sends SIGUSR1, …). Without throttling Howard saw the window
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
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Local\\shellframe-single-instance")
        ERROR_ALREADY_EXISTS = 183
        if handle and kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
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
    except OSError:
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
    Howard kept seeing two-instance TG 409 conflicts. PID file +
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
            # AND focused. Howard uses macOS Spaces heavily — if the window
            # is on another space, activating should pull it to the current
            # space (via NSWindowCollectionBehaviorMoveToActiveSpace set at
            # load time), not yank the user across spaces.
            if on_space and is_active and not is_hidden:
                NSApp.hide_(None)
                return
            # Summon path. NOT rate-limited — this branch only runs from a
            # real user keypress (NSEvent local/global monitor), and Howard
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


def main():
    _self_heal_venv()
    # Guard before we allocate anything expensive — if another shellframe
    # is already running, activate it and exit this process. Prevents
    # double-instance TG bridge 409 conflicts when Howard rapidly toggles
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
    signal.signal(signal.SIGINT, lambda *_: (api.cleanup_all(), os._exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (api.cleanup_all(), os._exit(0)))

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
        # on. Howard uses Mission Control heavily — the default behaviour
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
        # macOS: surface a dialog so Howard's colleagues see the recovery command
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
