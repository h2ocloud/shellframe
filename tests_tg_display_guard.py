#!/usr/bin/env python3
"""The Telegram bridge's screen read survives pyte failing mid-feed (TelegramBridge._slot_display).

Measured in the app: "uncaught exception in thread (_flush_loop): IndexError: string index out of range
at screens.py:241 render": pyte's `display` was read without a lock while output was being fed, it
raised, and the thread that forwards every tab's output to Telegram died until the app restarted.
  - a good read is returned and cached
  - a read that raises returns the last good one (nothing at all before the first), and no exception
Run: .venv/bin/python tests_tg_display_guard.py
"""
import sys
import types
from unittest.mock import MagicMock

sys.modules["webview"] = MagicMock()
import bridge_telegram as BR  # noqa: E402


class Screen:
    fail = False

    @property
    def display(self):
        if self.fail:
            raise IndexError("string index out of range")
        return ["第一行", "第二行"]


bridge = BR.TelegramBridge.__new__(BR.TelegramBridge)
slot = types.SimpleNamespace(screen=Screen(), _feed_gen=1, _display_cache=None, _display_cache_gen=-1)
ok = bridge._slot_display(slot) == ["第一行", "第二行"]
slot._feed_gen, slot.screen.fail = 2, True
ok = ok and bridge._slot_display(slot) == ["第一行", "第二行"]
fresh = types.SimpleNamespace(screen=Screen(), _feed_gen=1, _display_cache=None, _display_cache_gen=-1)
fresh.screen.fail = True
ok = ok and bridge._slot_display(fresh) == []
print("  [PASS]" if ok else "  [FAIL]", "a read that raises gives the last good screen, or none, and the loop goes on")
if not ok:
    sys.exit(1)
print("ALL PASS")
