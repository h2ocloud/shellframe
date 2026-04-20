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
import importlib
import json
import os
import platform
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
from datetime import datetime
from pathlib import Path
from queue import SimpleQueue

import webview

# Add app dir to path for bridge imports
sys.path.insert(0, str(Path(__file__).parent))
import bridge_telegram
from bridge_telegram import TelegramBridge, TelegramBridgeConfig

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
AI_CLI_TOOLS = {"claude", "codex", "aider", "cursor", "copilot", "goose", "gemini"}

APP_DIR = Path(__file__).parent
VERSION_FILE = APP_DIR / "version.json"
REPO_URL = "https://raw.githubusercontent.com/h2ocloud/shellframe/main/version.json"

CONFIG_DIR = Path.home() / ".config" / "shellframe"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "presets": [
        {"name": "Bash", "cmd": "bash", "icon": "\u25b6"},
    ],
    "settings": {
        "fontSize": 14,
        "language": "en"
    }
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        except:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')


TMUX_PREFIX = "sf_"  # tmux session name prefix

# Cross-platform temp dir — keep /tmp on Unix for continuity with existing
# installs, fall back to %TEMP% on Windows
import tempfile as _tempfile
TMP_DIR = Path("/tmp") if not IS_WIN else Path(_tempfile.gettempdir())
DEBUG_LOG = str(TMP_DIR / "shellframe_debug.log")


_LOG_MAX_BYTES = 1 * 1024 * 1024  # 1MB — auto-truncate logs to prevent unbounded growth

def _dlog(category: str, msg: str):
    """Append a timestamped line to the debug log. Best-effort, never raises.
    Auto-truncates when file exceeds _LOG_MAX_BYTES (keeps last half)."""
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(DEBUG_LOG, 'a', encoding='utf-8') as f:
            f.write(f"{ts} [{category}] {msg}\n")
        # Lazy size check (not every call — amortized via file size)
        try:
            if os.path.getsize(DEBUG_LOG) > _LOG_MAX_BYTES:
                with open(DEBUG_LOG, 'r', encoding='utf-8') as f:
                    content = f.read()
                with open(DEBUG_LOG, 'w', encoding='utf-8') as f:
                    f.write(content[len(content) // 2:])
        except Exception:
            pass
    except Exception:
        pass


def _has_tmux() -> bool:
    """Check if tmux is available on PATH."""
    return shutil.which("tmux") is not None

def _tmux_session_exists(name: str) -> bool:
    """Check if a tmux session with the given name exists."""
    r = subprocess.run(["tmux", "has-session", "-t", name],
                       capture_output=True, timeout=3)
    return r.returncode == 0

def _list_tmux_sessions() -> list[dict]:
    """List all sf_* tmux sessions. Returns [{name, cmd}]."""
    try:
        r = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return []
        result = []
        for line in r.stdout.strip().split("\n"):
            name = line.strip()
            if not name.startswith(TMUX_PREFIX):
                continue
            # Get the original command from tmux env
            cr = subprocess.run(
                ["tmux", "show-environment", "-t", name, "SF_CMD"],
                capture_output=True, text=True, timeout=3)
            cmd = ""
            if cr.returncode == 0 and "=" in cr.stdout:
                cmd = cr.stdout.strip().split("=", 1)[1]
            result.append({"name": name, "cmd": cmd})
        return result
    except Exception:
        return []


class Session:
    """One PTY tab session."""

    def __init__(self, sid: str, cmd: str, cols: int, rows: int,
                 on_data=None, tmux_name: str = None):
        self.sid = sid
        self.cmd = cmd
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.master_fd = None
        self.child_pid = None
        self.win_proc = None
        self.alive = True
        self._recent = bytearray()  # ring buffer for peeking (last 1KB), not consumed by read()
        self._on_data = on_data     # callback to signal new data (e.g. threading.Event.set)
        self._tmux_name = tmux_name  # tmux session name (None = no tmux)
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
            # Create new tmux session (detached) running the command
            subprocess.run([
                "tmux", "new-session", "-d",
                "-s", self._tmux_name,
                "-x", str(cols), "-y", str(rows),
                self.cmd,
            ], capture_output=True, timeout=5)
            # Store original command in tmux environment for recovery
            subprocess.run([
                "tmux", "set-environment", "-t", self._tmux_name,
                "SF_CMD", self.cmd,
            ], capture_output=True, timeout=3)
        else:
            # Resize existing tmux session to match terminal
            subprocess.run([
                "tmux", "resize-window", "-t", self._tmux_name,
                "-x", str(cols), "-y", str(rows),
            ], capture_output=True, timeout=3)

        # Attach via PTY fork — child runs `tmux attach`, parent reads master_fd
        self.child_pid, self.master_fd = pty.fork()
        if self.child_pid == 0:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env.setdefault("LANG", "en_US.UTF-8")
            tmux = shutil.which("tmux")
            os.execve(tmux, ["tmux", "attach-session", "-t", self._tmux_name], env)
        else:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass
            threading.Thread(target=self._reader_unix, daemon=True).start()

    def _start_unix(self, cols, rows):
        """Fallback: direct PTY fork (no tmux)."""
        args = shlex.split(self.cmd)
        exe = shutil.which(args[0])

        self.child_pid, self.master_fd = pty.fork()

        if self.child_pid == 0:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env.setdefault("LANG", "en_US.UTF-8")

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
                pass
            threading.Thread(target=self._reader_unix, daemon=True).start()

    def _start_win(self, cols, rows):
        args = shlex.split(self.cmd)
        exe = shutil.which(args[0])
        cmd_args = [exe] + args[1:] if exe else ["cmd.exe", "/c", self.cmd]

        # Try pywinpty for full ConPTY support (colors, TUI)
        try:
            import winpty
            self._winpty = winpty.PtyProcess.spawn(
                cmd_args,
                dimensions=(rows, cols),
                env={**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"},
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
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            env={**os.environ, "TERM": "xterm-256color"},
        )
        threading.Thread(target=self._reader_win, daemon=True).start()

    def _reader_unix(self):
        while self.alive and self.master_fd is not None:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.05)
                if r:
                    data = os.read(self.master_fd, 16384)
                    if not data:
                        self.alive = False
                        break
                    with self.lock:
                        self.buffer.extend(data)
                        self._recent.extend(data)
                        if len(self._recent) > 1024:
                            self._recent = self._recent[-1024:]
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
                    if self._on_data:
                        self._on_data()
                else:
                    break
            except:
                break
        self.alive = False

    def write(self, data: str):
        # Only log multi-char writes (init prompt, paste) — single keystrokes
        # are too noisy and the file open/close adds measurable latency.
        if len(data) > 2:
            preview = data[:80].replace('\r', '\\r').replace('\n', '\\n').replace('\x1b', '\\e')
            _dlog("write", f"sid={self.sid} len={len(data)} preview={preview!r}")
        if IS_WIN and hasattr(self, '_use_winpty') and self._use_winpty:
            try:
                self._winpty.write(data)
            except (EOFError, OSError):
                pass
            return
        raw = data.encode("utf-8", errors="replace")
        if IS_WIN:
            if self.win_proc and self.win_proc.stdin:
                try:
                    self.win_proc.stdin.write(raw)
                    self.win_proc.stdin.flush()
                except OSError:
                    pass
        else:
            if self.master_fd is not None:
                try:
                    os.write(self.master_fd, raw)
                except OSError:
                    pass

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
                pass
        elif not IS_WIN and self.master_fd is not None:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass
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
                    pass
            elif self.win_proc:
                self.win_proc.terminate()
        else:
            # Close master fd first — sends SIGHUP to the attach process (not the tmux session)
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
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
                    pass
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
                pass


class Api:
    """JS <-> Python bridge."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.bridge: TelegramBridge = None  # single global bridge
        self._counter = 0
        self._window = None
        self._pusher_started = False
        self._output_event = threading.Event()   # signalled by reader threads
        self._bridge_queue = SimpleQueue()        # feed_output off the hot path

    def _save_soft_session(self, sid: str, cmd: str):
        """Persist a session entry to config.session_list. Used as soft
        persistence on Windows (no tmux) — startup will recreate these as
        fresh PTYs. No-op on systems with tmux since tmux already persists."""
        if not IS_WIN and _has_tmux():
            return
        cfg = load_config()
        sessions = cfg.get("session_list", [])
        sessions = [s for s in sessions if s.get("sid") != sid]  # dedup
        sessions.append({"sid": sid, "cmd": cmd})
        cfg["session_list"] = sessions
        save_config(cfg)

    def _drop_soft_session(self, sid: str):
        """Remove a session from soft-persistence list."""
        if not IS_WIN and _has_tmux():
            return
        cfg = load_config()
        sessions = cfg.get("session_list", [])
        new_list = [s for s in sessions if s.get("sid") != sid]
        if len(new_list) != len(sessions):
            cfg["session_list"] = new_list
            save_config(cfg)

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
        restored = []

        if not IS_WIN and _has_tmux():
            existing = _list_tmux_sessions()
            _dlog("lifecycle", f"  found tmux sessions: {[e['name'] for e in existing]}")
            for info in existing:
                tmux_name = info["name"]
                cmd = info["cmd"] or "bash"
                # Extract sid from tmux name: sf_s1 → s1
                sid = tmux_name[len(TMUX_PREFIX):]
                if sid in self.sessions:
                    continue  # already attached
                self._counter = max(self._counter, int(sid[1:]) if sid[1:].isdigit() else 0)
                session = Session(sid, cmd, cols, rows,
                                  on_data=self._output_event.set,
                                  tmux_name=tmux_name)
                self.sessions[sid] = session
                # Restore bridge enabled/disabled state from config
                session._bridge_enabled = sid not in bridge_disabled
                session._init_pending = False
                # Restore custom label
                if sid in saved_labels:
                    session._custom_label = saved_labels[sid]
                restored.append({"sid": sid, "cmd": cmd})
            return json.dumps(restored)

        # Soft-persistence path (Windows / no tmux): recreate sessions fresh
        soft_list = cfg.get("session_list", [])
        _dlog("lifecycle", f"  soft restore from config: {[s.get('sid') for s in soft_list]}")
        for entry in soft_list:
            sid = entry.get("sid", "")
            cmd = entry.get("cmd", "")
            if not sid or not cmd or sid in self.sessions:
                continue
            try:
                self._counter = max(self._counter, int(sid[1:]) if sid[1:].isdigit() else 0)
                session = Session(sid, cmd, cols, rows, on_data=self._output_event.set)
                self.sessions[sid] = session
                session._bridge_enabled = sid not in bridge_disabled
                session._init_pending = False
                if sid in saved_labels:
                    session._custom_label = saved_labels[sid]
                restored.append({"sid": sid, "cmd": cmd})
            except Exception as e:
                _dlog("lifecycle", f"  soft restore failed for {sid}: {e}")
        return json.dumps(restored)

    def _start_output_pusher(self):
        """Background threads that push PTY output to frontend via evaluate_js.
        Event-driven: reader threads signal _output_event so pusher wakes instantly."""
        if self._pusher_started:
            return
        self._pusher_started = True
        pending = {}  # sid -> str

        def pusher():
            while True:
                self._output_event.clear()
                pushed = False
                for sid, s in list(self.sessions.items()):
                    data = s.read()
                    if data:
                        if self.bridge and getattr(s, '_bridge_enabled', True):
                            self._bridge_queue.put_nowait((sid, data))
                        pending[sid] = pending.get(sid, "") + data
                    chunk = pending.get(sid)
                    if chunk and self._window:
                        escaped = json.dumps(chunk)
                        try:
                            self._window.evaluate_js(f'_pushOutput("{sid}",{escaped})')
                            pending.pop(sid, None)
                            pushed = True
                        except Exception:
                            pass
                # Event-driven: wake instantly on new data, idle-back-off otherwise
                self._output_event.wait(0.001 if pushed else 0.015)

        def bridge_feeder():
            while True:
                sid, data = self._bridge_queue.get()
                if self.bridge:
                    self.bridge.feed_output(sid, data)

        threading.Thread(target=pusher, daemon=True).start()
        threading.Thread(target=bridge_feeder, daemon=True).start()

    def get_config(self) -> str:
        return json.dumps(load_config())

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

    def save_preset(self, name: str, cmd: str, icon: str) -> str:
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
        cfg["settings"] = json.loads(settings_json)
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

    def new_session(self, cmd: str, cols: int, rows: int) -> str:
        self._counter += 1
        sid = f"s{self._counter}"
        _dlog("lifecycle", f"new_session sid={sid} cmd={cmd!r} cols={cols} rows={rows}")
        session = Session(sid, cmd, cols, rows, on_data=self._output_event.set)
        self.sessions[sid] = session
        session._bridge_enabled = True
        # Soft persistence (Windows / no-tmux fallback): record this session
        # so the next startup can recreate it
        self._save_soft_session(sid, cmd)
        # Auto-register with bridge
        if self.bridge:
            label = cmd.split()[0] if cmd else sid
            self.bridge.register_session(
                sid, label,
                lambda text, _s=session: _s.write(text),
                peek_fn=lambda _s=session: bytes(_s._recent).decode('utf-8', errors='replace'),
            )
            self.bridge.refresh_commands()

        # Mark session for init prompt — only for AI CLI tools, not shells/editors/etc.
        session._init_pending = self._should_inject_init(cmd)
        # Nudge the UI to reconcile immediately (don't wait for 1.5s bridge poll).
        # Covers sessions created via TG /new, sfctl, or any non-UI path.
        self._notify_ui_sessions_changed()
        return sid

    def _notify_ui_sessions_changed(self):
        """Ping the web UI to re-sync session list. Safe no-op if window not ready."""
        try:
            if self._window:
                self._window.evaluate_js('window._syncSessionsFromBackend && window._syncSessionsFromBackend()')
        except Exception:
            pass

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
        prompt = bridge_telegram.load_init_prompt()
        if not prompt:
            return ""
        if not self.bridge or not self.bridge.active:
            marker = "\n## Telegram Bridge"
            idx = prompt.find(marker)
            if idx > 0:
                prompt = prompt[:idx].rstrip()
                prompt += "\n\nAcknowledge briefly and wait for the user's first message."
        return prompt

    def close_session(self, sid: str):
        _dlog("lifecycle", f"close_session sid={sid}")
        # Unregister from bridge
        if self.bridge:
            self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
        s = self.sessions.pop(sid, None)
        if s:
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
        self._notify_ui_sessions_changed()

    # Patterns in CLI output that indicate the AI tool is ready for conversation
    # (not in login/setup/auth flow). Checked after stripping ANSI escapes.
    import re as _re
    _ANSI_RE = _re.compile(r'\x1b\[[^A-Za-z]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b.|\x07')
    _AI_READY_RE = _re.compile(
        r'[>›]\s*$'           # Claude Code / Codex input prompt
        r'|^\s*Tip:'           # Codex tip line (shown after ready)
        r'|model:\s+\S'        # Codex model info box
        r'|claude\.ai'         # Claude Code welcome
        r'|What can I help'    # Common AI greeting
        , _re.MULTILINE
    )

    def write_input(self, sid: str, data: str):
        s = self.sessions.get(sid)
        if not s:
            return
        # IME dedup is handled in JS (compositionstart/end + time window)
        # On user Enter, check if we should inject init prompt
        if getattr(s, '_init_pending', False) and '\r' in data:
            # Check if CLI output looks like an AI tool ready for conversation
            # (not a login screen, auth flow, or shell prompt)
            with s.lock:
                tail = bytes(s._recent).decode('utf-8', errors='replace')
            clean = self._ANSI_RE.sub('', tail) if tail else ""
            if self._AI_READY_RE.search(clean):
                # AI tool is ready — inject init prompt with this message
                s._init_pending = False
                prompt = self._get_init_prompt()
                if prompt:
                    if self.bridge:
                        slot = self.bridge.slots.get(sid)
                        if slot:
                            slot.sent_texts.append(prompt)
                    user_text = data.rstrip('\r\n')
                    combined = prompt + "\n\n---\nUser's first message: " + user_text + "\r"
                    s.write(combined)
                    return
            # Not ready yet (login/auth flow) — pass through, keep _init_pending
        s.write(data)

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

    _ANSI_STRIP_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

    @staticmethod
    def _visual_width(s: str) -> int:
        """Approx terminal cell width. CJK/fullwidth count 2, control 0,
        everything else 1. Used for dedup thresholds so CJK lines (4 chars
        = 8 cells) aren't mis-treated as 'too short to dedup'."""
        w = 0
        for ch in s:
            o = ord(ch)
            if o < 0x20 or o == 0x7f:
                continue
            ea = unicodedata.east_asian_width(ch)
            w += 2 if ea in ("W", "F") else 1
        return w

    def get_clean_history(self, sid: str, max_lines: int = 10000, ansi: bool = True) -> str:
        """Return tmux pane history with prefix-duplicate lines collapsed.

        Streaming TUI apps (Claude Code, Codex) flush incomplete lines as they
        generate tokens. tmux captures every intermediate frame into scrollback,
        so scrolling up shows the same line repeated with progressively longer
        truncations. This method captures the full pane buffer, then for each
        run of consecutive lines where line[i+1].startswith(line[i]), keeps
        only the longest (final) version.

        When ansi=True (default), captures with `tmux capture-pane -e` so ANSI
        color/style escapes are preserved — the UI can feed the result directly
        into an xterm.js instance and get the original terminal styling back.
        Dedup still compares on stripped text so the colored line wins.
        """
        s = self.sessions.get(sid)
        if not s or not getattr(s, '_tmux_name', None):
            return json.dumps({"success": False, "reason": "no tmux", "text": ""})
        try:
            cmd = ["tmux", "capture-pane", "-p", "-J", "-t", s._tmux_name,
                   "-S", f"-{max_lines}"]
            if ansi:
                cmd.append("-e")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return json.dumps({"success": False, "reason": r.stderr[-200:], "text": ""})
            raw_lines = r.stdout.split("\n")
            cleaned = []  # list of (stripped_for_compare, original_for_output)
            for line in raw_lines:
                # Strip bare CR — they survive tmux capture for some TUIs and
                # cause xterm.js (with convertEol: true) to jump to col 0 and
                # overwrite earlier chars, leaving only the line tail visible.
                # Replace with nothing; the `\n` split already handled row breaks.
                line = line.replace("\r", "")
                original = line.rstrip()
                stripped = self._ANSI_STRIP_RE.sub('', original).rstrip() if ansi else original
                if cleaned:
                    prev_stripped, _ = cleaned[-1]
                    # Current is strict prefix of previous → skip (rare)
                    if prev_stripped.startswith(stripped) and stripped != prev_stripped:
                        continue
                    # Previous is strict prefix of current → replace with longer
                    if stripped.startswith(prev_stripped) and stripped != prev_stripped:
                        cleaned[-1] = (stripped, original)
                        continue
                cleaned.append((stripped, original))

            # Pass 2: drop non-consecutive repeats. Claude Code redraws the
            # visible region between streaming chunks, and tmux writes each
            # redraw into scrollback. The prefix pass above misses this when
            # the repeats are separated by spinner / status lines, or when
            # two redraw frames are byte-identical (neither is a strict
            # prefix of the other, so both are kept).
            #
            # Rule: if a line's stripped content has been emitted before and
            # is "distinctive enough" (visual width >= DEDUP_MIN_WIDTH), skip
            # it. Width — not codepoint count — matters here: "交付成果"
            # is 4 codepoints but 8 cells wide, and repeats of it absolutely
            # need collapsing. Short lines (blanks, "> ", prompts, dividers
            # "─────", wrap-fragments like "bjc") can legitimately recur and
            # are left alone.
            DEDUP_MIN_WIDTH = 8
            seen = set()
            final = []
            for stripped, original in cleaned:
                s_key = stripped.strip()
                if self._visual_width(s_key) >= DEDUP_MIN_WIDTH:
                    if s_key in seen:
                        continue
                    seen.add(s_key)
                final.append((stripped, original))
            # Append SGR reset to each line so an unclosed \x1b[...m on one
            # line can't bleed background/foreground colors into subsequent
            # lines when rendered in xterm.js (manifested as a giant red /
            # dark-bg rectangle across several rows in the overlay).
            reset = "\x1b[0m" if ansi else ""
            return json.dumps({
                "success": True,
                "text": "\n".join(orig + reset for _, orig in final),
                "ansi": ansi,
            })
        except Exception as e:
            return json.dumps({"success": False, "reason": str(e), "text": ""})

    def enter_scroll_history(self, sid: str) -> str:
        """Enter tmux copy-mode for scrollable history.
        xterm.js scrollback is always empty for TUI apps (Claude/Codex) that
        use cursor-positioning instead of line-feeds. tmux's own pane buffer
        has the real scrollback. This triggers copy-mode + PageUp so the user
        sees old conversation. Press q to exit."""
        s = self.sessions.get(sid)
        if not s or not s._tmux_name:
            return json.dumps({"success": False, "reason": "no tmux"})
        try:
            subprocess.run(["tmux", "copy-mode", "-t", s._tmux_name],
                           capture_output=True, timeout=3)
            # Immediate PageUp so the user sees history right away
            subprocess.run(["tmux", "send-keys", "-t", s._tmux_name, "PageUp"],
                           capture_output=True, timeout=3)
            return json.dumps({"success": True})
        except Exception as e:
            return json.dumps({"success": False, "reason": str(e)})

    def scroll_history(self, sid: str, direction: str, lines: int = 3) -> str:
        """Scroll within tmux copy-mode. Enters copy-mode automatically if
        needed. On first entry parks the cursor at top-line so the very next
        scroll-up walks straight into scrollback (no wasted cursor motion
        across the visible rows). Auto-exits when scrolling reaches the live
        bottom — also forces cursor to bottom-line on exit so the live view
        is fully visible. Returns whether still in copy-mode after scrolling."""
        _dlog("scroll", f"sid={sid} direction={direction} lines={lines}")
        s = self.sessions.get(sid)
        if not s or not s._tmux_name:
            return json.dumps({"success": False, "inCopyMode": False})
        lines = max(1, min(lines, 15))
        t = s._tmux_name
        try:
            r = subprocess.run(
                ["tmux", "display-message", "-t", t, "-p", "#{pane_in_mode}"],
                capture_output=True, text=True, timeout=3)
            in_mode = r.stdout.strip() == "1"
            _dlog("scroll", f"  in_mode={in_mode}")

            if direction == "up":
                if not in_mode:
                    subprocess.run(["tmux", "copy-mode", "-t", t],
                                   capture_output=True, timeout=3)
                    # Park cursor at the very top of the visible area so the
                    # next cursor-up motion immediately scrolls into history
                    # rather than walking down→up across visible rows.
                    subprocess.run(
                        ["tmux", "send-keys", "-t", t, "-X", "top-line"],
                        capture_output=True, timeout=3)
                # Use semantic copy-mode command (works under both vi/emacs)
                for _ in range(lines):
                    subprocess.run(
                        ["tmux", "send-keys", "-t", t, "-X", "cursor-up"],
                        capture_output=True, timeout=3)
            elif direction == "down" and in_mode:
                # Park cursor at the bottom of the visible area first, so the
                # subsequent cursor-down keys actually scroll the SCREEN
                # (driving the scrollbar back toward live) instead of just
                # walking the cursor across visible rows.
                subprocess.run(
                    ["tmux", "send-keys", "-t", t, "-X", "bottom-line"],
                    capture_output=True, timeout=3)
                for _ in range(lines):
                    subprocess.run(
                        ["tmux", "send-keys", "-t", t, "-X", "cursor-down"],
                        capture_output=True, timeout=3)
                # Check scroll position — at bottom (0) → exit cleanly
                rp = subprocess.run(
                    ["tmux", "display-message", "-t", t, "-p", "#{scroll_position}"],
                    capture_output=True, text=True, timeout=3)
                try:
                    scroll_pos = int(rp.stdout.strip() or "0")
                except (ValueError, TypeError):
                    scroll_pos = 0
                if scroll_pos == 0:
                    subprocess.run(
                        ["tmux", "send-keys", "-t", t, "-X", "cancel"],
                        capture_output=True, timeout=3)

            r2 = subprocess.run(
                ["tmux", "display-message", "-t", t, "-p", "#{pane_in_mode}"],
                capture_output=True, text=True, timeout=3)
            still_in = r2.stdout.strip() == "1"
            _dlog("scroll", f"  done sid={sid} still_in_copy_mode={still_in}")
            return json.dumps({"success": True, "inCopyMode": still_in})
        except Exception as e:
            _dlog("scroll", f"  ERROR sid={sid} {e}")
            return json.dumps({"success": False, "inCopyMode": False, "reason": str(e)})

    def set_active_tab(self, sid: str) -> str:
        """Persist the user's active tab sid to config.json. localStorage in
        WKWebView can be cleared unpredictably across launches; this is the
        durable backup."""
        try:
            cfg = load_config()
            cfg["last_active_tab"] = sid
            save_config(cfg)
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
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = CLAUDE_TMP / f"clipboard_{ts}.png"
            path.write_bytes(img_data)

            cutoff = time.time() - 3600
            for f in CLAUDE_TMP.glob("clipboard_*.png"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    pass
            return str(path)
        except Exception as e:
            return f"ERROR: {e}"

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
                    for dest_dir in [Path.home() / "Applications", Path("/Applications")]:
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
                                subprocess.run(
                                    ["codesign", "--force", "--deep", "--sign", "-", str(dest)],
                                    capture_output=True, timeout=30
                                )
                                post_steps.append(f".app copied to {dest}")
                                break
                        except Exception as e:
                            post_steps.append(f".app copy to {dest} failed: {e}")
                            # Non-fatal — src .app in APP_DIR is still usable

            has_sessions = len(self.sessions) > 0
            return json.dumps({
                "success": True,
                "message": pull_out,
                "version": new_ver,
                "can_hot_reload": has_sessions,
                "needs_restart": needs_restart,
                "changed_files": changed_files,
                "post_steps": post_steps,
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

            if IS_WIN:
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
                        pass
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
                # Strategy 1: macOS `open -n -a` against the .app bundle
                # Find the .app bundle (resolve symlinks)
                candidates = [
                    APP_DIR / "ShellFrame.app",
                    Path("/Applications/ShellFrame.app"),
                    Path.home() / "Applications" / "ShellFrame.app",
                ]
                app_path = None
                for c in candidates:
                    try:
                        if c.exists():
                            app_path = c.resolve()
                            break
                    except Exception:
                        pass
                if app_path:
                    try:
                        subprocess.Popen(
                            ["/usr/bin/open", "-n", "-a", str(app_path)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"open -n -a failed: {e}")

                # Strategy 2: launcher script directly (macOS .app bundle internal)
                if not spawned:
                    launcher = APP_DIR / "ShellFrame.app" / "Contents" / "MacOS" / "shellframe"
                    if launcher.exists():
                        try:
                            subprocess.Popen(
                                [str(launcher)],
                                start_new_session=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            spawned = True
                        except Exception as e:
                            err_msgs.append(f"launcher failed: {e}")

                # Strategy 3: relaunch via current Python
                if not spawned:
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

            # Schedule exit so the response can return cleanly first
            def _exit_soon():
                time.sleep(0.8)
                try:
                    self.cleanup_all()  # detaches from tmux without killing
                except Exception:
                    pass
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
            if not getattr(s, '_bridge_enabled', True):
                continue
            label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
            self.bridge.register_session(
                sid, label,
                lambda text, _s=s: _s.write(text),
                peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
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
        cfg["bridge"] = {
            "bot_token": bot_token,
            "allowed_users": [int(u) for u in allowed],
            "prefix_enabled": prefix_enabled,
            "initial_prompt": initial_prompt,
            "stt_backend": prev_bridge.get("stt_backend", "auto"),
            "stt_providers": prev_bridge.get("stt_providers", []),
        }
        save_config(cfg)

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
                )
            else:
                self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
        # Persist bridge-disabled sessions so they survive restart
        cfg = load_config()
        disabled = set(cfg.get("bridge_disabled_sessions", []))
        if enabled:
            disabled.discard(sid)
        else:
            disabled.add(sid)
        cfg["bridge_disabled_sessions"] = sorted(disabled)
        save_config(cfg)
        return json.dumps({"success": True, "enabled": enabled})

    def reorder_sessions(self, order_json: str) -> str:
        """Reorder sessions. Updates TG bridge /1 /2 commands to match."""
        order = json.loads(order_json)
        if self.bridge:
            self.bridge.reorder_slots(order)
            self.bridge.refresh_commands()
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
            if self.bridge:
                was_active = self.bridge.active
                old_config = self.bridge.config
                saved_offset = self.bridge._offset
                saved_user_active = dict(getattr(self.bridge, '_user_active', {}) or {})
                saved_user_chat = dict(getattr(self.bridge, '_user_chat', {}) or {})
                saved_default_active = getattr(self.bridge, '_default_active_sid', None)
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
                    if not getattr(s, '_bridge_enabled', True):
                        continue
                    label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
                    self.bridge.register_session(
                        sid, label,
                        lambda text, _s=s: _s.write(text),
                        peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                    )
                # Restore user routing state — filter out sids that disappeared
                self.bridge._user_active = {
                    uid: sid for uid, sid in saved_user_active.items()
                    if sid in self.bridge.slots
                }
                self.bridge._user_chat = saved_user_chat
                if saved_default_active and saved_default_active in self.bridge.slots:
                    self.bridge._default_active_sid = saved_default_active
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
        if s:
            self.bridge.register_session(
                sid, label,
                lambda text, _s=s: _s.write(text),
                peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
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
        # Persist
        cfg = load_config()
        labels = cfg.get("session_labels", {})
        labels[sid] = name
        cfg["session_labels"] = labels
        save_config(cfg)
        return json.dumps({"success": True})

    def bridge_unregister_session(self, sid: str):
        """Remove a session from the bridge."""
        if self.bridge:
            self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()

    # ── Remote control (sfctl) ──

    _CMD_FILE = str(TMP_DIR / "shellframe_cmd.json")
    _RESULT_FILE = str(TMP_DIR / "shellframe_result.json")

    def _start_command_watcher(self):
        """Watch for commands from sfctl CLI (file-based IPC)."""
        def watcher():
            while True:
                time.sleep(0.5)
                if not os.path.exists(self._CMD_FILE):
                    continue
                try:
                    with open(self._CMD_FILE, encoding='utf-8') as f:
                        cmd_data = json.load(f)
                    os.unlink(self._CMD_FILE)
                except (json.JSONDecodeError, IOError, OSError):
                    continue

                # Ignore stale commands (older than 30s)
                if time.time() - cmd_data.get("ts", 0) > 30:
                    continue

                cmd = cmd_data.get("cmd", "")
                args = cmd_data.get("args", {})
                result = self._execute_sfctl(cmd, args)

                try:
                    with open(self._RESULT_FILE, "w", encoding='utf-8') as f:
                        json.dump(result, f, ensure_ascii=False)
                except IOError:
                    pass
        threading.Thread(target=watcher, daemon=True).start()

    def _execute_sfctl(self, cmd: str, args: dict = None) -> dict:
        """Execute a sfctl command and return result dict."""
        args = args or {}
        if cmd == "new_session":
            try:
                preset_cmd = args.get("cmd", "claude")
                cols = args.get("cols", 200)
                rows = args.get("rows", 50)
                sid = self.new_session(preset_cmd, cols, rows)
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
                self.close_session(sid)
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
                s.write(text)
                if submit:
                    time.sleep(0.05)
                    s.write("\r")
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

        else:
            return {"success": False, "message": f"Unknown command: {cmd}"}

    def cleanup_all(self):
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
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
            pass
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


def main():
    _self_heal_venv()
    _prevent_app_nap()
    api = Api()
    html_path = Path(__file__).parent / "web" / "index.html"

    # Safety net: clean up on exit no matter what
    atexit.register(api.cleanup_all)
    signal.signal(signal.SIGINT, lambda *_: (api.cleanup_all(), os._exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (api.cleanup_all(), os._exit(0)))

    # Restore window geometry from last close. Absolute x/y preserves the
    # user's monitor on multi-display setups, as long as that monitor is
    # still attached. Pre-validate against currently-attached screens
    # because pywebview's cocoa backend crashes at startup when the initial
    # point lands off-screen: windowDidMove_ callback calls
    # self.window.screen() which returns None (no screen there), then
    # .frame() on None raises AttributeError before we ever get a chance
    # to recover. Must happen BEFORE webview.create_window.
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
    if isinstance(saved_x, (int, float)) and isinstance(saved_y, (int, float)):
        if _coords_on_attached_screen(
            int(saved_x), int(saved_y),
            create_kwargs["width"], create_kwargs["height"],
        ):
            create_kwargs["x"] = int(saved_x)
            create_kwargs["y"] = int(saved_y)
        else:
            # Saved screen is gone (external display unplugged). Drop x/y
            # so pywebview centers on the primary display, and scrub the
            # stale coords from config so we don't stash them back on the
            # first move event.
            try:
                cfg_now = load_config()
                win = cfg_now.get("window", {}) or {}
                win.pop("x", None)
                win.pop("y", None)
                cfg_now["window"] = win
                save_config(cfg_now)
            except Exception:
                pass
            print(f"[shellframe] saved window position ({saved_x},{saved_y}) "
                  f"is off-screen — centering on primary.", file=sys.stderr)

    try:
        window = webview.create_window(**create_kwargs)
    except Exception as e:
        # Defensive second layer: if something else in create_window
        # tripped on the x/y pair (pywebview version skew, unusual screen
        # layout, etc.), retry without the position hint.
        print(f"[shellframe] create_window with saved geometry failed: {e} "
              f"— retrying centered.", file=sys.stderr)
        create_kwargs.pop("x", None)
        create_kwargs.pop("y", None)
        window = webview.create_window(**create_kwargs)
    api._window = window

    # Persist geometry on move/resize, debounced so rapid drag events don't
    # hammer the config file. Also saves once on close as a safety net.
    _geom_state = {
        "x": create_kwargs.get("x"),
        "y": create_kwargs.get("y"),
        "width": create_kwargs["width"],
        "height": create_kwargs["height"],
        "timer": None,
    }
    _geom_lock = threading.Lock()

    def _flush_geom():
        try:
            cfg = load_config()
            cfg["window"] = {
                "x": _geom_state["x"],
                "y": _geom_state["y"],
                "width": _geom_state["width"],
                "height": _geom_state["height"],
            }
            save_config(cfg)
        except Exception:
            pass

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
        pass
    try:
        window.events.resized += _on_resized
    except Exception:
        pass

    def _on_closed_save_and_cleanup():
        # Cancel pending debounce + flush synchronously so the close actually
        # captures the last known geometry before the process exits.
        with _geom_lock:
            t = _geom_state.get("timer")
            if t:
                t.cancel()
        _flush_geom()
        api.cleanup_and_exit()

    window.events.loaded += lambda: api._start_output_pusher()
    window.events.closed += _on_closed_save_and_cleanup
    api._start_command_watcher()
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
                pass
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        _write_crash_log(e)
        raise
