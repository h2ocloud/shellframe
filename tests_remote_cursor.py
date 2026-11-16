#!/usr/bin/env python3
"""A remote tab's pasted screen leaves the cursor where the tab has it (api_remote raw_screen, snapshot).

Reported with a screenshot: on another computer's tab, what was typed (and often after pasting a
picture) landed on the screen's last line, under the CLI's input box. The remote pane pastes the tab's
screen when it attaches (and again after a resize or when the stream ran past its buffer), then draws
the CLI's echo from wherever the cursor is. raw_screen (the desktop's remote pane) sent the screen
without the cursor, so it stayed after the last line; snapshot (the phones') already sent it.
  - raw_screen ends with the cursor's position from tmux, as snapshot does
  - when tmux cannot say, the screen still goes, without it
Run: .venv/bin/python tests_remote_cursor.py
"""
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import api_remote  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


SCREEN = "line one\n line two\n─────\n❯ \n─────\n  footer\n\n\n"
cursor = {"out": "2,3\n"}                     # x=2, y=3: the input box's line


def fake_run(cmd, **kw):
    if "capture-pane" in cmd:
        return types.SimpleNamespace(returncode=0, stdout=SCREEN if kw.get("text") else SCREEN.encode(), stderr=b"")
    if "display-message" in cmd:
        if cursor["out"] is None:
            raise subprocess.TimeoutExpired(cmd, 2)
        return types.SimpleNamespace(returncode=0, stdout=cursor["out"] if kw.get("text") else cursor["out"].encode())
    raise AssertionError(cmd)


api_remote.subprocess.run = fake_run
api_remote.main = types.SimpleNamespace(_tmux_bin=lambda: "tmux", IS_WIN=False)
api_remote.shutil = types.SimpleNamespace(which=lambda n: "/usr/bin/tmux")
host = api_remote.RemoteApiMixin.__new__(api_remote.RemoteApiMixin)
host.sessions = {"s1": types.SimpleNamespace(_tmux_name="sf_s1", cols=80, rows=24)}

print("the desktop's remote pane (raw_screen)")
res = api_remote.RemoteApiMixin._execute_sfctl(host, "raw_screen", {"sid": "s1"})
screen = (res.get("details") or {}).get("screen", "")
check("cleared, the screen, then the cursor back on the input box's line", screen.startswith("\x1b[2J\x1b[H")
      and "❯" in screen and screen.endswith("\x1b[4;3H"), repr(screen[-30:]))
cursor["out"] = None
res = api_remote.RemoteApiMixin._execute_sfctl(host, "raw_screen", {"sid": "s1"})
check("tmux cannot say: the screen still goes, without a cursor move",
      res["success"] and not res["details"]["screen"].endswith("H"), repr(res["details"]["screen"][-20:]))
cursor["out"] = "2,3\n"

print("the phones' snapshot, as before")
res = api_remote.RemoteApiMixin._execute_sfctl(host, "snapshot", {"sid": "s1"})
check("it ends with the same cursor move", (res.get("details") or {}).get("ansi", "").endswith("\x1b[4;3H"),
      repr(((res.get("details") or {}).get("ansi", ""))[-20:]))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
