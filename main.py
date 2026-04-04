#!/usr/bin/env python3
"""
shellframe — Multi-tab GUI terminal with clipboard image paste support.
Runs any CLI tool (Claude, Codex, bash, etc.) in tabbed PTY sessions.

Mac: WKWebView + pty.fork()
Windows: Edge WebView2 + subprocess
"""

import atexit
import base64
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

import webview

# Add app dir to path for bridge imports
sys.path.insert(0, str(Path(__file__).parent))
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


class Session:
    """One PTY tab session."""

    def __init__(self, sid: str, cmd: str, cols: int, rows: int):
        self.sid = sid
        self.cmd = cmd
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.master_fd = None
        self.child_pid = None
        self.win_proc = None
        self.alive = True
        self._start(cols, rows)

    def _start(self, cols, rows):
        if IS_WIN:
            self._start_win(cols, rows)
        else:
            self._start_unix(cols, rows)

    def _start_unix(self, cols, rows):
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
        cmd = [exe] + args[1:] if exe else ["cmd.exe", "/c", self.cmd]

        self.win_proc = subprocess.Popen(
            cmd,
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
            except (OSError, ValueError):
                self.alive = False
                break

    def _reader_win(self):
        while self.alive and self.win_proc and self.win_proc.poll() is None:
            try:
                data = self.win_proc.stdout.read(4096)
                if data:
                    with self.lock:
                        self.buffer.extend(data)
                else:
                    break
            except:
                break
        self.alive = False

    def write(self, data: str):
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
        if not IS_WIN and self.master_fd is not None:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    def kill(self):
        self.alive = False
        if IS_WIN:
            if self.win_proc:
                self.win_proc.terminate()
        else:
            # Close master fd first — sends SIGHUP to child
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
            if self.child_pid:
                # SIGTERM the process group
                try:
                    os.killpg(os.getpgid(self.child_pid), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
                # Give it a moment, then SIGKILL if still alive
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
        self.bridges: dict[str, TelegramBridge] = {}  # sid -> bridge
        self._counter = 0
        self._bridge_counter = 0

    def get_config(self) -> str:
        return json.dumps(load_config())

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
                result.append({"sid": sid, "cmd": s.cmd, "alive": True})
        return json.dumps(result)

    def new_session(self, cmd: str, cols: int, rows: int) -> str:
        self._counter += 1
        sid = f"s{self._counter}"
        session = Session(sid, cmd, cols, rows)
        self.sessions[sid] = session
        return sid

    def close_session(self, sid: str):
        s = self.sessions.pop(sid, None)
        if s:
            s.kill()

    def write_input(self, sid: str, data: str):
        s = self.sessions.get(sid)
        if s:
            s.write(data)

    def read_output(self, sid: str) -> str:
        s = self.sessions.get(sid)
        if not s:
            return ""
        data = s.read()
        # Feed output to bridge if attached
        bridge = self.bridges.get(sid)
        if bridge and data:
            bridge.feed_output(data)
        return data

    def is_alive(self, sid: str) -> bool:
        s = self.sessions.get(sid)
        return s.alive if s else False

    def resize(self, sid: str, cols: int, rows: int):
        s = self.sessions.get(sid)
        if s:
            s.resize(cols, rows)

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

    def start_bridge(self, sid: str, bot_token: str, allowed_users_json: str,
                     prefix_enabled: bool, initial_prompt: str) -> str:
        """Start a TG bridge attached to session sid."""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False, "message": "Session not found"})

        # Stop existing bridge on this session
        old = self.bridges.get(sid)
        if old:
            old.stop()

        allowed = json.loads(allowed_users_json) if allowed_users_json else []
        config = TelegramBridgeConfig(
            bot_token=bot_token,
            allowed_users=[int(u) for u in allowed],
            prefix_enabled=prefix_enabled,
            initial_prompt=initial_prompt,
        )

        self._bridge_counter += 1
        bridge_id = f"b{self._bridge_counter}"

        bridge = TelegramBridge(
            bridge_id=bridge_id,
            config=config,
            write_fn=lambda text: s.write(text),
        )
        bridge.start()
        self.bridges[sid] = bridge

        status = bridge.get_status()
        return json.dumps({"success": bridge.connected, **status})

    def stop_bridge(self, sid: str) -> str:
        bridge = self.bridges.pop(sid, None)
        if bridge:
            bridge.stop()
            return json.dumps({"success": True})
        return json.dumps({"success": False, "message": "No bridge on this session"})

    def toggle_bridge(self, sid: str) -> str:
        """Toggle pause/resume. Returns new state."""
        bridge = self.bridges.get(sid)
        if not bridge:
            return json.dumps({"active": False, "exists": False})
        is_active = bridge.toggle_pause()
        return json.dumps({"active": is_active, "exists": True, **bridge.get_status()})

    def get_bridge_status(self, sid: str) -> str:
        bridge = self.bridges.get(sid)
        if not bridge:
            return json.dumps({"exists": False})
        return json.dumps({"exists": True, **bridge.get_status()})

    def list_bridges(self) -> str:
        result = {}
        for sid, b in self.bridges.items():
            result[sid] = b.get_status()
        return json.dumps(result)

    def cleanup_all(self):
        for b in list(self.bridges.values()):
            b.stop()
        self.bridges.clear()
        for s in list(self.sessions.values()):
            s.kill()
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
    window.events.closed += api.cleanup_and_exit
    webview.start(debug=("--debug" in sys.argv))

    # If webview.start() returns but process is still alive, force exit
    api.cleanup_all()
    os._exit(0)


if __name__ == "__main__":
    main()
