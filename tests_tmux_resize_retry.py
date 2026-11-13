#!/usr/bin/env python3
"""A tab's tmux window follows its size even when tmux is slow (Session.resize).

Reported on a release candidate: right after a restart a tab drew in the top-left three quarters of its
terminal. Its tmux window had stayed at the restart's 80x24: the one `tmux resize-window` call timed out
(3 s, every tab restoring at once) and nothing tried again, as the window size is manual.
  - the resize returns at once; the tmux call runs off the caller's thread
  - a call that times out is tried again, and the newest size asked for is the one applied
Run: .venv/bin/python tests_tmux_resize_retry.py
"""
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")
import main  # noqa: E402

calls, lock = [], threading.Lock()
real_run, real_sleep = subprocess.run, time.sleep


def fake_run(argv, **kw):
    if argv[:2] != ["tmux", "resize-window"]:
        return real_run(argv, **kw)
    with lock:
        calls.append(argv[-3] + "x" + argv[-1])
        first = len(calls) == 1
    if first:
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    return subprocess.CompletedProcess(argv, 0, b"", b"")


main.subprocess.run = fake_run
main.time.sleep = lambda s: real_sleep(0.01)
s = main.Session.__new__(main.Session)
import os  # noqa: E402
master, slave = os.openpty()            # a real pty, so the ioctl has something to size
s.sid, s._tmux_name, s.master_fd, s._use_winpty = "s9", "sf_s9", master, False
t0 = time.time()
s.resize(101, 49)
quick = time.time() - t0 < 0.5
s.resize(120, 50)
deadline = time.time() + 3
while time.time() < deadline and "120x50" not in calls:
    real_sleep(0.02)
real_sleep(0.1)
main.subprocess.run, main.time.sleep = real_run, real_sleep
ok1 = quick
ok2 = "120x50" in calls and calls[-1] == "120x50"
print("  [PASS]" if ok1 else "  [FAIL]", "the resize returns at once")
print("  [PASS]" if ok2 else "  [FAIL]", "a timed-out call is tried again, and the newest size is the one applied", calls)
if not (ok1 and ok2):
    sys.exit(1)
print("ALL PASS")
