#!/usr/bin/env python3
"""
cli-gui — Multi-tab GUI terminal with clipboard image paste support.
Runs any CLI tool (Claude, Codex, bash, etc.) in tabbed PTY sessions.

Mac: WKWebView + pty.fork()
Windows: Edge WebView2 + subprocess
"""

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
from datetime import datetime
from pathlib import Path

import webview

IS_WIN = platform.system() == "Windows"

if not IS_WIN:
    import fcntl
    import pty
    import select
    import struct
    import termios

CLAUDE_TMP = Path.home() / ".claude" / "tmp"
CLAUDE_TMP.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = Path.home() / ".config" / "cli-gui"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "presets": [
        {"name": "Claude Code", "cmd": "claude", "icon": "\u2728"},
        {"name": "Codex", "cmd": "codex", "icon": "\u26a1"},
        {"name": "Bash", "cmd": "bash", "icon": "\u25b6"},
    ]
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
            if self.child_pid:
                try:
                    os.kill(self.child_pid, signal.SIGTERM)
                except OSError:
                    pass
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None


class Api:
    """JS <-> Python bridge."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self._counter = 0

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

    def delete_preset(self, name: str) -> str:
        cfg = load_config()
        cfg["presets"] = [p for p in cfg["presets"] if p["name"] != name]
        save_config(cfg)
        return json.dumps(cfg)

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
        return s.read() if s else ""

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

    def cleanup_all(self):
        for s in list(self.sessions.values()):
            s.kill()
        self.sessions.clear()


def main():
    api = Api()
    html_path = Path(__file__).parent / "web" / "index.html"

    window = webview.create_window(
        "cli-gui",
        url=str(html_path),
        js_api=api,
        width=1000,
        height=720,
        min_size=(640, 400),
        text_select=True,
        background_color="#1a1b26",
    )
    window.events.closed += api.cleanup_all
    webview.start(debug=("--debug" in sys.argv))


if __name__ == "__main__":
    main()
