"""A system notification from the app: one title, one line of text, sent and forgotten.

macOS: osascript `display notification`; Linux: `notify-send` when the program is installed; Windows: nothing
yet. Leaf module (stdlib only, no `import main`): the ops TV uses it, and the finished-reply notice (today
tied to the Telegram bridge) can move to it later.

A test never sends one (`sf_config._running_tests()`): the call is recorded in `SENT` and returns False, so a
test can check that a notification was asked for without a banner on the person's screen. `send` never raises
and never waits for the program: it runs it on a thread with a time limit and the call returns at once.
"""
import shutil
import subprocess
import sys
import threading

import sf_config

LIMIT_S = 5.0
SENT = []                    # what a test run was asked to send: [(title, text)]


def escape(text, n=240) -> str:
    """A string safe inside an AppleScript double-quoted literal: backslash and quote escaped, line breaks and
    other control characters turned into spaces, length capped."""
    out = []
    for ch in str(text if text is not None else "")[:n]:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 32 or ch == "\x7f":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out).strip()


def command(title, text):
    """The program and its arguments for this system, or None when there is nothing to send with."""
    title, text = escape(title, 80), escape(text)
    if sys.platform == "darwin":
        return ["osascript", "-e", f'display notification "{text}" with title "{title}"']
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        # notify-send takes plain arguments: no escaping, but nothing that looks like an option
        return ["notify-send", "--", title, text]
    return None


def _run(cmd):
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=LIMIT_S)
    except (subprocess.SubprocessError, OSError):
        pass


def send(title, text) -> bool:
    """Ask the system to show a notification. True when it was handed to the system (not that it was shown:
    Focus modes and notification settings decide that); False when this system has no way, or in a test."""
    if sf_config._running_tests():
        SENT.append((str(title), str(text)))
        return False
    cmd = command(title, text)
    if not cmd:
        return False
    threading.Thread(target=_run, args=(cmd,), daemon=True, name="desktop-notify").start()
    return True
