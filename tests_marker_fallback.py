#!/usr/bin/env python3
"""marker 缺失 fallback 回歸測試（v0.29.15）。

修前：模型沒吐出 [[TG_REPLY]] marker 時，回覆永遠不轉發 → 使用者只能自己
/fetch（回報 2026-07-12「回覆傳不回來、像失聯、都要自己 fetch」）。
修後：turn 結束後仍抓不到 marker，就用 /fetch 那條純文字（_peek_last_response）
自動轉發，並清掉殘留的 [[TG_REPLY_xxx]] token 與 wrapper 指示回顯。

本測試涵蓋 `_marker_fallback_text` 的清洗邏輯。

跑法：.venv/bin/python tests_marker_fallback.py
"""

import importlib.util
import os
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

INSTR = ("最終要回 Telegram 的文字請放在 [[TG_REPLY_ab12]] 和 [[/TG_REPLY_ab12]] 之間。"
         "標記外可以思考或操作，但手機只會收到標記內文字。")


def _br(peek):
    br = object.__new__(_bt.TelegramBridge)
    br._peek_last_response = lambda slot: peek
    return br


def _slot():
    return types.SimpleNamespace(marker_prompt=INSTR)


# ── 1. 殘留的 marker token 被清掉，真正回覆保留 ──
def test_strips_leaked_marker_tokens():
    peek = "[[TG_REPLY_ab12]]這是真正的回覆\n第二段。[[/TG_REPLY_ab12]]"
    out = _br(peek)._marker_fallback_text(_slot())
    assert "TG_REPLY" not in out, out
    assert "這是真正的回覆" in out and "第二段" in out, out


# ── 2. wrapper 指示回顯不會被當成回覆送出 ──
def test_drops_instruction_echo():
    peek = INSTR + "\n實際要說的內容在這裡。"
    out = _br(peek)._marker_fallback_text(_slot())
    assert "最終要回 Telegram" not in out, out
    assert "實際要說的內容在這裡。" in out, out


# ── 3. 純文字回覆（模型完全忘了 marker）原樣保留 ──
def test_plain_reply_passthrough():
    peek = "好的，我已經把部署跑完了，三個服務都正常。"
    out = _br(peek)._marker_fallback_text(_slot())
    assert out == "好的，我已經把部署跑完了，三個服務都正常。", out


# ── 4. 畫面上沒有可轉發內容 → 回 ''（維持等待，不亂送）──
def test_empty_peek_returns_empty():
    assert _br("")._marker_fallback_text(_slot()) == ""
    assert _br("   \n  \n")._marker_fallback_text(_slot()) == ""


# ── 5. _peek 拋例外也不炸，回 '' ──
def test_peek_exception_safe():
    br = object.__new__(_bt.TelegramBridge)
    def boom(slot): raise RuntimeError("tmux gone")
    br._peek_last_response = boom
    assert br._marker_fallback_text(_slot()) == ""


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
