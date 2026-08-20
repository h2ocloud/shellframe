#!/usr/bin/env python3
"""封鎖 bot 的收件人不得害其他人被無限重送（v0.29.36）。

2026-08-19 回報「對話1跳針」：兩個 chat 封鎖了 bot（403 bot was blocked
by the user），舊版 flush 一律「任一失敗＝整批 FAILED」→ 內容不進去重集合
→ 下一輪重抽重送 → 唯一收得到的人每輪都再收一次同樣內容，還附帶
一則「送出失敗」警告。

修法：逐收件人判定——any_ok / retryable_fail / permanent(403…)。
有人收到，或全部都是永久失敗，就 commit（不重抽）。

跑法：.venv/bin/python tests_tg_blocked_chat.py
"""

import importlib.util
import os
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

# 測試不得寫進 production 的 bridge log（使用者靠那份 log 除錯）
_bt._blog = lambda msg: None

BLOCKED = {"ok": False, "error_code": 403,
           "description": "Forbidden: bot was blocked by the user"}
FLOOD = {"ok": False, "error_code": 429,
         "description": "Too Many Requests: retry after 40"}
OK = {"ok": True}


def _br(responses):
    """responses: chat_id -> 回傳值。"""
    br = object.__new__(_bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="T")
    _bt.tg_api = lambda tok, m, p=None, timeout=None: responses.get(
        (p or {}).get("chat_id"), OK)
    return br


# ── 1. 403 封鎖 → permanent=True（不可重試）──
def test_blocked_is_permanent():
    br = _br({9: BLOCKED})
    ok, ra, perm = br._send_text_checked("s1", 9, "hi")
    assert ok is False and perm is True, (ok, ra, perm)


# ── 2. 429 flood → permanent=False（值得重抽）──
def test_flood_is_retryable():
    br = _br({9: FLOOD})
    ok, ra, perm = br._send_text_checked("s1", 9, "hi")
    assert ok is False and perm is False, (ok, ra, perm)
    assert ra == 40.0, ra


# ── 3. 成功 → (True, 0, False) ──
def test_ok():
    assert _br({})._send_text_checked("s1", 9, "hi") == (True, 0.0, False)


# ── 4. 永久錯誤字樣涵蓋常見幾種 ──
def test_permanent_patterns():
    for desc in ("Forbidden: bot was blocked by the user",
                 "Forbidden: user is deactivated",
                 "Bad Request: chat not found",
                 "Forbidden: bot was kicked from the group chat"):
        br = _br({9: {"ok": False, "description": desc}})
        _ok, _ra, perm = br._send_text_checked("s1", 9, "x")
        assert perm is True, desc


# ── 5. 核心回歸：一個封鎖 + 一個正常 → 必須 commit（不重抽、不洗版）──
#      這條就是 使用者看到的跳針。用原始碼層級確認 commit 條件正確。
def test_commit_condition_source():
    import inspect
    src = inspect.getsource(_bt.TelegramBridge._flush_loop)
    assert "any_ok" in src and "retryable_fail" in src, "未改成逐收件人判定"
    assert "if any_ok or not retryable_fail:" in src, \
        "commit 條件錯：有人收到或全永久失敗時都必須 commit"
    assert "dead_chats" in src, "未把永久失效的 chat 移出路由"


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
