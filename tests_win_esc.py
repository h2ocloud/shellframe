#!/usr/bin/env python3
"""Esc to a Windows (ConPTY) tab goes as a win32-input-mode key event once ConPTY asked for that mode.

Reported: in a Codex tab on Windows, Esc did not interrupt the turn. A lone ESC byte is also how every escape
sequence starts, so ConPTY's input parser cannot tell the key from the start of a sequence. ConPTY asks its host
for win32-input-mode at start (CSI ? 9001 h); in that mode each key is one record CSI Vk;Sc;Uc;Kd;Cs;Rc _.
  - a keystroke that is only Esc (one or a few) is written as Escape's press and release records, while ConPTY
    has asked for the mode; nothing else changes (Alt+x, arrows, text, Esc inside a paste)
  - the request is seen in the tab's output, also when it is split across two reads; CSI ? 9001 l turns it off
  - before any request, and on macOS/Linux, the byte goes as it always did
  - the debug log says when an Esc was sent this way
No Windows needed: the ConPTY is a stand-in. Run: .venv/bin/python tests_win_esc.py
"""
import sys
import threading
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import main  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


ESC_KEY = "\x1b[27;1;27;1;0;1_\x1b[27;1;27;0;0;1_"

print("the rules")
check("Esc while ConPTY asked: press and release records", main._w32_keys("\x1b", True) == ESC_KEY)
check("two quick Esc presses: two pairs", main._w32_keys("\x1b\x1b", True) == ESC_KEY * 2)
check("before ConPTY asked: the byte as it was", main._w32_keys("\x1b", False) == "\x1b")
for name, data in (("Alt+x", "\x1bx"), ("an arrow key", "\x1b[A"), ("text", "hello"), ("a paste with Esc in it", "a\x1bb"), ("nothing", "")):
    check(f"{name} is unchanged", main._w32_keys(data, True) == data, repr(main._w32_keys(data, True)))
check("the request turns it on", main._w32_mode_after(b"x\x1b[?9001h", False) is True)
check("…the last one counts", main._w32_mode_after(b"\x1b[?9001h y \x1b[?9001l", True) is False)
check("…other output leaves it as it was", main._w32_mode_after(b"\x1b[?1049h hello", True) is True
      and main._w32_mode_after(b"\x1b[?1049h hello", False) is False)


class FakeConPTY:
    def __init__(self, chunks):
        self.chunks, self.written = list(chunks), []

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else ""

    def write(self, data):
        self.written.append(data)


def tab(chunks):
    s = main.Session.__new__(main.Session)
    s.sid, s.alive, s.lock = "s1", True, threading.Lock()
    s.buffer, s._recent = bytearray(), bytearray()
    s._on_data, s._use_winpty = None, True
    s._last_activity_time = s._last_user_activity_time = s._last_output_activity_time = 0
    s._winpty = FakeConPTY(chunks)
    return s


print("a Windows tab (stand-in ConPTY)")
logs = []
with patch.object(main, "IS_WIN", True), patch.object(main, "_dlog", lambda tag, msg: logs.append((tag, msg))):
    s = tab([])
    s.write("\x1b")
    check("before ConPTY asked, Esc goes as the byte", s._winpty.written == ["\x1b"], s._winpty.written)
    s = tab(["\x1b[?1;2c\x1b[?90", "01h\x1b[?25l ready"])     # the request split across two reads
    s._reader_winpty()
    check("the request is seen across two reads", getattr(s, "_w32_input", False) is True)
    s.write("\x1b")
    s.write("\x1b[A")
    s.write("hi")
    check("…then Esc goes as key events, everything else as before", s._winpty.written == [ESC_KEY, "\x1b[A", "hi"], s._winpty.written)
    check("…and the debug log says so", any(t == "write" and "as win32-input-mode" in m for t, m in logs), logs)
    s._winpty.chunks = ["bye \x1b[?9001l"]
    s.alive = True
    s._reader_winpty()
    s.write("\x1b")
    check("once ConPTY turns it off, the byte again", s._winpty.written[-1] == "\x1b", s._winpty.written)

with patch.object(main, "IS_WIN", False):
    s = tab([])
    s._use_winpty = False
    s.master_fd = None
    s._w32_input = True
    s.write("\x1b")                                   # nothing to write to: only that it does not convert
    check("not on Windows: never converted (the Unix path takes the bytes)", s._winpty.written == [])

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
