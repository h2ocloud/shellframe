#!/usr/bin/env python3
"""Right-click copy must leave exactly the selected text on the clipboard.

Symptom (reported in daily use): right-click copy occasionally puts the wrong
text on the clipboard, and the next copy is fine again. It depends on WHAT was
selected, which is why it looks intermittent.

Root cause: on Windows Api.copy_text piped UTF-16 into clip.exe, and clip.exe
cannot be fed UTF-16 cleanly. Without a BOM it guesses the encoding from the
byte pattern: text containing ASCII produces NUL bytes that give it away, but a
selection that is entirely CJK is decoded as an ANSI code page and comes out
garbled. With a BOM the guess is skipped, but clip.exe stores the BOM as a
literal U+FEFF in front of the text. No input encoding is clean.

Fix: write CF_UNICODETEXT through the Win32 clipboard API. Every copy also logs
surface / length / ok / duration -- never the text -- so the next report can
say which path misbehaved.

This file never touches the real clipboard (the Win32 writer is stubbed).

Run: .venv/bin/python tests_clipboard_copy.py
"""
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

import main as _main  # noqa: E402

logs = []
_main._dlog = lambda category, msg: logs.append((category, msg))

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── 1. payload encoding ──
cjk = "中文測試"
p = _main._clipboard_utf16(cjk)
check("payload has no BOM (a BOM would be stored as a literal U+FEFF)",
      not p.startswith(b"\xff\xfe") and not p.startswith(b"\xfe\xff"), repr(p[:4]))
check("pure-CJK text round-trips exactly, plus one NUL terminator",
      p.decode("utf-16le") == cjk + "\0", repr(p.decode("utf-16le")))
check("LF becomes CRLF (Windows apps expect it)",
      _main._clipboard_utf16("a\nb\n").decode("utf-16le") == "a\r\nb\r\n\0")
check("existing CRLF is not doubled",
      _main._clipboard_utf16("a\r\nb").decode("utf-16le") == "a\r\nb\0")
check("astral character (surrogate pair) survives",
      _main._clipboard_utf16("😀 ok").decode("utf-16le") == "😀 ok\0")
try:
    lone = _main._clipboard_utf16("x\ud83dy")  # selection cut through a pair
    check("a lone surrogate does not raise (the copy is not lost)",
          lone.decode("utf-16le", "surrogatepass") == "x\ud83dy\0")
except Exception as e:  # noqa: BLE001
    check("a lone surrogate does not raise (the copy is not lost)", False, repr(e))

# ── 2. copy_text: result contract + diagnostics that never carry the text ──
SECRET = "SECRET-中文-payload"
calls = []
_saved = (_main.IS_WIN, getattr(_main, "_win_set_clipboard_text", None))
_main.IS_WIN = True
_main._win_set_clipboard_text = lambda t: calls.append(t)
try:
    fake_self = object()  # copy_text does not use self

    logs.clear()
    r = _main.Api.copy_text(fake_self, SECRET, "live-rightclick")
    line = logs[-1][1] if logs else ""
    check("success returns 'ok'", r == "ok", r)
    check("writer received the exact text", calls == [SECRET], repr(calls))
    check("logged under category 'clipboard'", bool(logs) and logs[-1][0] == "clipboard", repr(logs))
    check("log names surface, length and ok",
          "surface=live-rightclick" in line and f"len={len(SECRET)}" in line and "ok=True" in line, line)
    check("log never contains the copied text",
          "SECRET" not in line and "中文" not in line and "payload" not in line, line)

    # failure: the exception message here quotes the text, as a codec error does
    def _boom(_t):
        e = OSError("write failed for SECRET-中文-payload")
        e.winerror = 5
        raise e
    _main._win_set_clipboard_text = _boom
    logs.clear()
    r = _main.Api.copy_text(fake_self, SECRET, "history-rightclick")
    line = logs[-1][1] if logs else ""
    check("failure returns ERROR with class and Win32 code only",
          r == "ERROR: OSError winerror=5", r)
    check("failure log says ok=False and carries no message text",
          "ok=False" in line and "err=OSError winerror=5" in line
          and "SECRET" not in line and "SECRET" not in r, line + " | " + r)

    def _codec(_t):
        raise UnicodeEncodeError("utf-16-le", "S", 0, 1, "surrogates not allowed")
    _main._win_set_clipboard_text = _codec
    logs.clear()
    r = _main.Api.copy_text(fake_self, SECRET, "live-ctrl-c")
    line = logs[-1][1] if logs else ""
    check("codec error is logged by class name, not by its message (which quotes text)",
          "UnicodeEncodeError" in line and "surrogates" not in line and "can't encode" not in line, line)

    # empty text must not clear the user's clipboard
    calls.clear()
    _main._win_set_clipboard_text = lambda t: calls.append(t)
    logs.clear()
    r = _main.Api.copy_text(fake_self, "", "live-rightclick")
    check("empty text is refused and the writer is never called",
          r == "ERROR: empty text" and calls == [], f"{r} {calls}")
    check("empty refusal is logged as ok=False",
          bool(logs) and "ok=False" in logs[-1][1] and "len=0" in logs[-1][1], repr(logs))

    # surface tag is JS-supplied: keep it to one safe token so it cannot forge log lines
    logs.clear()
    _main.Api.copy_text(fake_self, "x", "a b\nc;[fake] rm")
    check("surface tag is sanitised to a single safe token",
          len(logs) == 1 and "\n" not in logs[0][1] and "surface=abcfakerm " in logs[0][1], repr(logs))
    logs.clear()
    _main.Api.copy_text(fake_self, "x")
    check("missing surface logs as '?'", "surface=? " in logs[-1][1], repr(logs))
finally:
    _main.IS_WIN = _saved[0]
    if _saved[1] is not None:
        _main._win_set_clipboard_text = _saved[1]

# ── 3. the writer must be the Win32 API, not a pipe into clip.exe ──
cs = inspect.getsource(_main.Api.copy_text)
check("copy_text no longer pipes into clip.exe", "'clip'" not in cs and '"clip"' not in cs)
ws = inspect.getsource(_main._win_set_clipboard_text)
check("Windows writer uses SetClipboardData with CF_UNICODETEXT (13), no subprocess",
      "SetClipboardData(13" in ws and "subprocess" not in ws)

# ── 4. front end: every selection copy is tagged; right-click can't lose its capture ──
html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
check("no selection copy calls the bridge untagged",
      not re.search(r"copy_text\(\s*(?:term|histTerm)\.getSelection", html))
m = re.search(r"\$wrap\.addEventListener\('contextmenu'.*?\n  \}\);", html, re.S)
h = m.group(0) if m else ""
check("live right-click handler found", bool(h))
check("live right-click is tagged 'live-rightclick'", "copySelection(sel, 'live-rightclick')" in h)
check("capture is taken BEFORE the copy's await (a quick 2nd right-click keeps its own)",
      "_pendingSel = '';" in h and "await copySelection" in h
      and h.index("_pendingSel = '';") < h.index("await copySelection"))
check("selection is cleared only after a confirmed write",
      re.search(r"if \(await copySelection\(sel, 'live-rightclick'\)\) term\.clearSelection\(\)", h) is not None)
for tag in ("history-rightclick", "history-ctrl-c", "live-ctrl-c"):
    check(f"'{tag}' surface is wired", f"'{tag}'" in html)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
