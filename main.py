#!/usr/bin/env python3
"""
shellframe — Multi-tab GUI terminal with clipboard image paste support.
Runs any CLI tool (Claude, Codex, bash, etc.) in tabbed PTY sessions.

Mac: WKWebView + pty.fork()
Windows: Edge WebView2 + subprocess
"""

import atexit
import base64
import importlib
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
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
            return json.loads(CONFIG_FILE.read_text())
        except:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


TMUX_PREFIX = "sf_"  # tmux session name prefix

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
        return data.decode("utf-8", errors="replace")

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

    def restore_tmux_sessions(self, cols: int = 80, rows: int = 24) -> str:
        """Detect orphaned sf_* tmux sessions and reattach them.
        Called from frontend on startup before list_sessions."""
        if IS_WIN or not _has_tmux():
            return json.dumps([])
        existing = _list_tmux_sessions()
        restored = []
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
            session._bridge_enabled = True
            session._init_pending = False
            restored.append({"sid": sid, "cmd": cmd})
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

    def list_sessions(self) -> str:
        """Return list of active sessions (for reconnect after page reload)."""
        result = []
        for sid, s in self.sessions.items():
            if s.alive:
                result.append({"sid": sid, "cmd": s.cmd, "alive": True,
                               "bridge_enabled": getattr(s, '_bridge_enabled', True)})
        return json.dumps(result)

    def new_session(self, cmd: str, cols: int, rows: int) -> str:
        self._counter += 1
        sid = f"s{self._counter}"
        session = Session(sid, cmd, cols, rows, on_data=self._output_event.set)
        self.sessions[sid] = session
        session._bridge_enabled = True
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
        return sid

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
        # Unregister from bridge
        if self.bridge:
            self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
        s = self.sessions.pop(sid, None)
        if s:
            s.kill()

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
        s = self.sessions.get(sid)
        if s:
            s.resize(cols, rows)

    def copy_text(self, text: str) -> str:
        """Copy text to system clipboard."""
        try:
            p = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            p.communicate(text.encode('utf-8'))
            return 'ok'
        except Exception as e:
            return f'ERROR: {e}'

    def paste_text(self) -> str:
        """Read text from system clipboard."""
        try:
            result = subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=3)
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
            return VERSION_FILE.read_text()
        except:
            return json.dumps({"version": "unknown", "channel": "main"})

    def get_changelog(self) -> str:
        """Return changelog content."""
        changelog = APP_DIR / "CHANGELOG.md"
        try:
            return changelog.read_text()
        except:
            return ""

    def check_update(self) -> str:
        """Check GitHub for latest version. Returns JSON with local, remote, update_available."""
        try:
            local = json.loads(VERSION_FILE.read_text()) if VERSION_FILE.exists() else {"version": "0.0.0"}
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
        """Pull latest from git. Sessions stay alive — frontend reloads to pick up changes."""
        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(APP_DIR),
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                try:
                    new_ver = json.loads(VERSION_FILE.read_text())["version"]
                except:
                    new_ver = "unknown"
                has_sessions = len(self.sessions) > 0
                return json.dumps({
                    "success": True,
                    "message": result.stdout.strip(),
                    "version": new_ver,
                    "can_hot_reload": has_sessions,
                })
            else:
                return json.dumps({"success": False, "message": result.stderr.strip()})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    # ── Bridge API ──

    def start_bridge(self, bot_token: str, allowed_users_json: str,
                     prefix_enabled: bool, initial_prompt: str) -> str:
        """Start the global TG bridge. Registers all current sessions."""
        if self.bridge:
            self.bridge.stop()

        allowed = json.loads(allowed_users_json) if allowed_users_json else []
        config = TelegramBridgeConfig(
            bot_token=bot_token,
            allowed_users=[int(u) for u in allowed],
            prefix_enabled=prefix_enabled,
            initial_prompt=initial_prompt,
        )

        self.bridge = TelegramBridge(
            bridge_id="tg",
            config=config,
            on_reload=self.hot_reload_bridge,
            on_close_session=self.close_session,
        )

        # Register existing sessions (skip bridge-disabled ones)
        for sid, s in self.sessions.items():
            if not getattr(s, '_bridge_enabled', True):
                continue
            label = s.cmd.split()[0] if s.cmd else sid
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

        # Persist bridge config
        cfg = load_config()
        cfg["bridge"] = {
            "bot_token": bot_token,
            "allowed_users": [int(u) for u in allowed],
            "prefix_enabled": prefix_enabled,
            "initial_prompt": initial_prompt,
        }
        save_config(cfg)

        return json.dumps({"success": self.bridge.connected, **self.bridge.get_status()})

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
        """Enable/disable TG bridge for a specific session."""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False})
        s._bridge_enabled = bool(enabled)
        if self.bridge:
            if enabled:
                label = s.cmd.split()[0] if s.cmd else sid
                self.bridge.register_session(
                    sid, label,
                    lambda text, _s=s: _s.write(text),
                    peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                )
            else:
                self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
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
            # Save current bridge config
            old_config = None
            was_active = False
            saved_offset = 0
            if self.bridge:
                was_active = self.bridge.active
                old_config = self.bridge.config
                saved_offset = self.bridge._offset
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
                )
                # Preserve TG polling offset so it doesn't re-process the /reload command
                self.bridge._offset = saved_offset
                for sid, s in self.sessions.items():
                    if not getattr(s, '_bridge_enabled', True):
                        continue
                    label = s.cmd.split()[0] if s.cmd else sid
                    self.bridge.register_session(
                        sid, label,
                        lambda text, _s=s: _s.write(text),
                        peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                    )
                self.bridge.start()
                return json.dumps({"success": True, "message": "Bridge reloaded and restarted", **self.bridge.get_status()})
            else:
                self.bridge = None
                return json.dumps({"success": True, "message": "Bridge module reloaded (bridge was not running)"})
        except Exception as e:
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

    def bridge_unregister_session(self, sid: str):
        """Remove a session from the bridge."""
        if self.bridge:
            self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()

    # ── Remote control (sfctl) ──

    _CMD_FILE = "/tmp/shellframe_cmd.json"
    _RESULT_FILE = "/tmp/shellframe_result.json"

    def _start_command_watcher(self):
        """Watch for commands from sfctl CLI (file-based IPC)."""
        def watcher():
            while True:
                time.sleep(0.5)
                if not os.path.exists(self._CMD_FILE):
                    continue
                try:
                    with open(self._CMD_FILE) as f:
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
                    with open(self._RESULT_FILE, "w") as f:
                        json.dump(result, f)
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


def main():
    api = Api()
    html_path = Path(__file__).parent / "web" / "index.html"

    # Safety net: clean up on exit no matter what
    atexit.register(api.cleanup_all)
    signal.signal(signal.SIGINT, lambda *_: (api.cleanup_all(), os._exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (api.cleanup_all(), os._exit(0)))

    window = webview.create_window(
        "shellframe",
        url=str(html_path),
        js_api=api,
        width=1000,
        height=720,
        min_size=(640, 400),
        text_select=True,
        background_color="#1a1b26",
    )
    api._window = window
    window.events.loaded += lambda: api._start_output_pusher()
    window.events.closed += api.cleanup_and_exit
    api._start_command_watcher()
    webview.start(debug=("--debug" in sys.argv))

    # If webview.start() returns but process is still alive, force exit
    api.cleanup_all()
    os._exit(0)


if __name__ == "__main__":
    main()
