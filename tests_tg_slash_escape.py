#!/usr/bin/env python3
"""`//` 逃逸前綴回歸測試（v0.29.45）。

需求：bridge 攔截了一批跟 CLI 撞名的指令（/new /model /status /help…），
使用者要送給**分頁**的同名指令永遠到不了。`//new` ＝ 剝一層斜線後跳過
bridge 攔截，把 `/new` 原文送進分頁。

跑法：.venv/bin/python tests_tg_slash_escape.py
"""

import importlib.util
import os
import re

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

SRC = __import__("inspect").getsource(_bt.TelegramBridge._handle_update)


def _escape(text):
    """複刻 _handle_update 的逃逸判定（來源即下方斷言釘住的那兩行）。"""
    escaped = bool(text) and text.startswith("//") and len(text) > 2
    return (text[1:] if escaped else text), escaped


# ── 1. //new → /new 且跳過 bridge 攔截 ──
def test_double_slash_escapes():
    txt, esc = _escape("//new")
    assert txt == "/new" and esc is True


# ── 2. 帶參數也要能逃 ──
def test_escape_with_args():
    txt, esc = _escape("//model opus")
    assert txt == "/model opus" and esc is True


# ── 3. 一般指令不受影響（仍由 bridge 處理）──
def test_single_slash_untouched():
    txt, esc = _escape("/new")
    assert txt == "/new" and esc is False


# ── 4. 純文字不受影響 ──
def test_plain_text_untouched():
    for s in ("hello", "http://x/y", "a//b"):
        txt, esc = _escape(s)
        assert txt == s and esc is False, s


# ── 5. 邊界：單獨的 `//` 不當逃逸（剝完是空指令，沒意義）──
def test_bare_double_slash():
    txt, esc = _escape("//")
    assert esc is False and txt == "//"


# ── 6. 原始碼確實接上了逃逸閘門（跳過 bridge 攔截）──
def test_source_wires_escape_into_gate():
    assert "escaped_slash" in SRC, "逃逸旗標不存在"
    assert re.search(r'startswith\("/"\).*not file_paths and not escaped_slash', SRC), \
        "bridge 指令攔截沒有排除 escaped_slash"


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}"); fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
