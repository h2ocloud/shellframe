"""A tab's sign-in, taken through step by step for someone who does not know the CLI.

Asked for (11/16): when a tab says "Login expired · Please run /login", the chat view should lead
a person who knows neither the product nor the CLI through signing in again, without typing
commands. This module reads which step Claude Code's /login is at from the tab's screen and
presses what that step needs; web/chat.js (and the phone, POST /link/login) draw a card for it.

The steps, in Claude Code's words (read from its 2.1.325 binary, so nothing is guessed):
  expired   the last turn says "Login expired · Please run /login" (or an expired OAuth token,
            an invalid API key)
  menu      /login's choice: "Claude account with subscription" / "Anthropic Console account"
  browser   "Opening browser to authorize …" or "Browser didn't open? Use the url below to sign in"
            and its URL (read with wrapped lines joined, so a long URL comes whole)
  code      "Paste code here >": the sign-in page gave a code to paste back
  done      "Login successful. Press Enter to continue"

Leaf module: stdlib only; `api` is the host (duck-typed: sessions, _send_text_to_session).
"""

import re
import subprocess
import threading
import time

EXPIRED_RE = re.compile(r"Login expired|Please run /login|OAuth token (?:has )?expired|Invalid API key", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
URL_RE = re.compile(r"https://[^\s\"'<>]+")

_active = {}             # sid -> when a sign-in was started from here (the card follows it until done)
_lock = threading.Lock()


def screen(api, sid: str) -> str:
    """The tab's screen as plain text, wrapped lines joined (-J: a long URL stays one line)."""
    s = api.sessions.get(sid)
    name = getattr(s, "_tmux_name", None) if s else None
    if not name:
        return ""
    try:
        out = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-t", name, "-S", "-60"],
                             capture_output=True, text=True, timeout=3)
        return ANSI_RE.sub("", out.stdout) if out.returncode == 0 else ""
    except Exception:
        return ""


def step_of(text: str) -> dict:
    """{"step", "url"?} for a screen; step '' when no sign-in is on it."""
    if "Login successful" in text:
        return {"step": "done"}
    if "Paste code here" in text:
        m = URL_RE.findall(text)
        return {"step": "code", **({"url": m[-1]} if m else {})}
    if "Use the url below to sign in" in text or "Opening browser to authorize" in text or "Browser didn't open" in text:
        m = URL_RE.findall(text)
        return {"step": "browser", **({"url": m[-1]} if m else {})}
    if "Claude account with subscription" in text and "Anthropic Console account" in text:
        return {"step": "menu"}
    return {"step": ""}


def expired_in(turns) -> bool:
    """The conversation's last turn says the sign-in is gone."""
    for t in reversed(turns or []):
        text = str(t.get("text") or "")
        if not text.strip():
            continue
        return t.get("kind") != "user_msg" and bool(EXPIRED_RE.search(text))
    return False


def view(api, sid: str, turns=None) -> dict:
    """What the chat view's card shows, or {} when there is nothing to sign in to. The screen is read only
    while a sign-in is under way or the last turn says it expired (the view is asked every few seconds)."""
    with _lock:
        active = sid in _active
    expired = expired_in(turns)
    if not active and not expired:
        return {}
    st = step_of(screen(api, sid))
    if st["step"]:
        return st
    if active and time.time() - _active.get(sid, 0) < 8:
        return {"step": "starting"}
    if expired:
        return {"step": "expired"}
    with _lock:
        _active.pop(sid, None)            # it went away (signed in, or the person left it): no card
    return {}


def act(api, sid: str, action: str, code: str = "") -> dict:
    """One step, pressed for the person: start (types /login), subscription / console (picks it from the
    menu), code (pastes what the sign-in page gave), finish (the Enter after "Login successful")."""
    s = api.sessions.get(sid)
    if not s:
        return {"success": False, "code": "no_tab"}
    if action == "start":
        with _lock:
            _active[sid] = time.time()
        api._send_text_to_session(s, "/login", submit=True)
    elif action == "subscription":
        s.write("\r")                     # the first choice is the highlighted one
    elif action == "console":
        s.write("\x1b[B")
        time.sleep(0.15)
        s.write("\r")
    elif action == "code":
        code = str(code or "").strip()
        if not code:
            return {"success": False, "code": "empty"}
        api._send_text_to_session(s, code, submit=True)
    elif action == "finish":
        s.write("\r")
        with _lock:
            _active.pop(sid, None)
    elif action == "cancel":
        s.write("\x1b")
        with _lock:
            _active.pop(sid, None)
    else:
        return {"success": False, "code": "unknown"}
    time.sleep(0.8)                       # the CLI draws the next step
    return {"success": True, "login": view(api, sid)}
