#!/usr/bin/env python3
"""A message pasted into a tab is submitted: one more Enter, once, when the paste stayed in the composer.

Reported (with the log): a message sent from the office's chat box to a Claude Code tab stayed in the tab's
composer; the tmux paste was followed by its Enter (`submit=True`) but no turn began until the person pressed
Enter. The message held a pasted picture: Claude Code turns an image path into an [Image #N] attachment,
reading the file before it takes the next key, and an Enter 80 ms behind the paste was lost.
  - a paste naming an image file waits at least 0.9 s before its Enter
  - 1.2 s after the Enter, on a thread of its own: if the tab's hook did not say a turn began, the tab is not
    working (esc to interrupt), no menu is up (an Enter there would pick an option), and the screen still
    shows the paste (its end, a [Pasted text] or [Image #N] chip), one more Enter
  - only in an AI CLI's tab: in a shell a program may be waiting for a line
Nothing here types into a real tab: tmux and the clock are stand-ins.
Run: .venv/bin/python tests_send_submit_check.py
"""
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(HERE))
import main  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    passed, failed = passed + bool(ok), failed + (not ok)


class Tab:
    def __init__(self, sid="s1", cmd="claude", screen=""):
        self.sid, self.cmd, self.alive = sid, cmd, True
        self._recent = bytearray(screen.encode("utf-8"))
        self._tmux_name = f"sf_{sid}"
        self.written = []

    def write(self, d):
        self.written.append(d)


api = main.Api.__new__(main.Api)
api._hook_events = {}
MSG = "有個狀況，我點辦公室的任何一個角色，他會拉近，但我找不到地方維持這個縮放 /tmp/clip_1.png"

print("is the paste still in the composer?")
t0 = time.time()
stuck_screen = "╭───╮\n│ > " + MSG[-30:] + " │\n╰───╯\n"
check("the paste's end still on the screen, nothing else: stuck", api._submit_stuck(Tab(screen=stuck_screen), MSG, t0))
check("an [Image #2] chip still in the composer: stuck",
      api._submit_stuck(Tab(screen="│ > [Image #2] 看一下 │"), "看一下 /tmp/a.png", t0))
api._hook_events["s1"] = {"state": "working", "ts": t0 + 0.3}
check("the tab's hook said a turn began after the Enter: not stuck", not api._submit_stuck(Tab(screen=stuck_screen), MSG, t0))
api._hook_events["s1"] = {"state": "idle", "ts": t0 - 30}
check("…a hook event from before the Enter does not count", api._submit_stuck(Tab(screen=stuck_screen), MSG, t0))
api._hook_events = {}
check("working (esc to interrupt): not stuck",
      not api._submit_stuck(Tab(screen=stuck_screen + "✻ Thinking… (esc to interrupt)"), MSG, t0))
check("a menu is up: not stuck (an Enter would pick an option)",
      not api._submit_stuck(Tab(screen=stuck_screen + "Do you want to proceed?\n❯ 1. Yes\n  2. No"), MSG, t0))
check("an empty composer after the reply: not stuck",
      not api._submit_stuck(Tab(screen="╭───╮\n│ > │\n╰───╯\n"), MSG, t0))

print("the check after the Enter")
nudges = []
api._check_submitted_later(Tab(screen=stuck_screen), MSG, t0, lambda: nudges.append(1), after=0.05)
time.sleep(0.4)
check("a Claude tab whose paste stayed: one more Enter", nudges == [1], nudges)
nudges.clear()
api._check_submitted_later(Tab(cmd="zsh", screen=stuck_screen), MSG, t0, lambda: nudges.append(1), after=0.05)
time.sleep(0.4)
check("a shell tab: no check, no Enter", nudges == [], nudges)
nudges.clear()
api._check_submitted_later(Tab(screen="│ > │"), MSG, t0, lambda: nudges.append(1), after=0.05)
time.sleep(0.4)
check("a Claude tab that took it: no Enter", nudges == [], nudges)

print("the tmux send")
calls, sleeps, later = [], [], []
real_run, real_which, real_sleep = main.subprocess.run, main.shutil.which, main.time.sleep


def fake_run(cmd, **kw):
    calls.append(cmd)
    return MagicMock(returncode=0, stderr=b"")


main.subprocess.run = fake_run
main.shutil.which = lambda name: "/usr/bin/" + name
main.time.sleep = lambda s: sleeps.append(s)
api._check_submitted_later = lambda s, text, t, nudge, after=1.2: later.append((s.sid, text))
try:
    ok = api._send_text_to_session(Tab(), MSG, submit=True)
    check("a paste naming an image waits at least 0.9 s before its Enter", sleeps and max(sleeps) >= 0.9, sleeps)
    check("…then Enter, and the check is scheduled once", [c[-1] for c in calls if "send-keys" in c] == ["Enter"]
          and later == [("s1", MSG)], (calls, later))
    calls.clear(); sleeps.clear(); later.clear()
    api._send_text_to_session(Tab(), "好", submit=True)
    check("a short plain message keeps the short wait", sleeps and max(sleeps) < 0.5, sleeps)
    calls.clear(); sleeps.clear(); later.clear()
    api._send_text_to_session(Tab(), "", submit=True)
    check("a bare Enter (no text) schedules no check", later == [], later)
finally:
    main.subprocess.run, main.shutil.which, main.time.sleep = real_run, real_which, real_sleep
    del api._check_submitted_later

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
