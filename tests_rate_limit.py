"""Rate-limit 偵測與通知回歸測試 (v0.29.12)

測試項目：
  1. 真實橫幅文字命中偵測，且能解析 reset 時間
  2. /rate-limit-options interactive 選單 → interactive=True
  3. 去重：同狀態連續兩 tick 只通知一次
  4. 訊號消失後清旗標 → 再次出現可重新通知
  5. 正常畫面不誤報

跑法：
    .venv/bin/python tests_rate_limit.py
"""

import importlib.util
import os
import sys
import threading
import types

# ── 載入 bridge_telegram ──────────────────────────────────────────────────────
_spec = importlib.util.spec_from_file_location(
    "bt",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"),
)
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

# 測試不得寫進 production 的 bridge log（Howard 靠那份 log 除錯）
_bt._blog = lambda msg: None

# ── 建立最小可用的 TelegramBridge 實例（不啟動執行緒）───────────────────────
_BR = object.__new__(_bt.TelegramBridge)
_BR._slot_order = []
_BR._user_active = {}
_BR._user_chat = {}
_BR.config = types.SimpleNamespace(bot_token="FAKE_TOKEN")


# ── helper：偽造一個 SessionSlot ─────────────────────────────────────────────
class _FakeScreen:
    def __init__(self, lines):
        self._lines = lines
        self.display = lines

    history = types.SimpleNamespace(top=[])
    columns = 200


def _make_slot(screen_lines):
    slot = object.__new__(_bt.SessionSlot)
    slot.sid = "test"
    slot.label = "test-tab"
    slot.rate_limit_notified = False
    slot.screen = _FakeScreen(screen_lines)
    slot._feed_gen = 0
    slot._display_cache = None
    slot._display_cache_gen = -1
    return slot


# ── 攔截 tg_api 呼叫 ─────────────────────────────────────────────────────────
_sent_messages = []
_orig_tg_api = _bt.tg_api


def _fake_tg_api(token, method, data=None, **kw):
    _sent_messages.append({"method": method, "data": data})
    return {"ok": True, "result": {}}


# ── 測試案例 ─────────────────────────────────────────────────────────────────

def test_banner_detected_with_reset():
    """真實橫幅：命中且解析 reset 時間。"""
    lines = [
        "  ⎿  You've hit your session limit · resets 3pm (Asia/Taipei)",
        "  /usage-credits to finish what you're working on.",
        "  › ",
    ]
    slot = _make_slot(lines)
    info = _BR._detect_rate_limit(slot)
    assert info is not None, "應命中 rate-limit，但回傳 None"
    assert "3pm" in info["reset"], f"reset 應含 '3pm'，實際：{info['reset']!r}"
    assert info["interactive"] is False, "純橫幅不應 interactive"


def test_interactive_menu_detected():
    """/rate-limit-options 互動選單 → interactive=True。"""
    lines = [
        "  You've hit your session limit.",
        "  /rate-limit-options",
        "  1. Stop and wait for limit to reset",
        "  2. Switch to usage credits",
        "  Enter to confirm · Esc to cancel",
    ]
    slot = _make_slot(lines)
    info = _BR._detect_rate_limit(slot)
    assert info is not None, "應命中 rate-limit，但回傳 None"
    assert info["interactive"] is True, "有 /rate-limit-options 應 interactive=True"


def test_dedup_same_tick_only_notifies_once():
    """連續兩次偵測到 rate-limit，只通知一次。"""
    lines = [
        "  ⎿  You've hit your session limit · resets 5pm (Asia/Taipei)",
        "  /usage-credits to finish what you're working on.",
    ]
    slot = _make_slot(lines)
    _BR._slot_order = [slot.sid]
    _BR._user_chat = {1: 9999}
    _BR._user_active = {1: slot.sid}

    _sent_messages.clear()
    _bt.tg_api = _fake_tg_api
    try:
        # 第一 tick：應通知
        info = _BR._detect_rate_limit(slot)
        if info is not None and not slot.rate_limit_notified:
            slot.rate_limit_notified = True
            _BR._notify_rate_limit(slot, info)

        first_count = len(_sent_messages)
        assert first_count >= 1, "第一次應送通知"

        # 第二 tick：已 notified，不重複
        info = _BR._detect_rate_limit(slot)
        if info is not None and not slot.rate_limit_notified:
            slot.rate_limit_notified = True
            _BR._notify_rate_limit(slot, info)

        second_count = len(_sent_messages)
        assert second_count == first_count, (
            f"第二 tick 不應再送（sent_count: {second_count} vs {first_count}）")
    finally:
        _bt.tg_api = _orig_tg_api


def test_clear_and_renotify_after_reset():
    """訊號消失 → 清旗標 → 再次出現 → 重新通知。"""
    rate_lines = [
        "  ⎿  You've hit your session limit · resets 5pm (Asia/Taipei)",
        "  /usage-credits to finish what you're working on.",
    ]
    normal_lines = [
        "  • Sure, I can help with that.",
        "  › ",
    ]
    slot = _make_slot(rate_lines)
    _BR._slot_order = [slot.sid]
    _BR._user_chat = {1: 9999}
    _BR._user_active = {1: slot.sid}

    _sent_messages.clear()
    _bt.tg_api = _fake_tg_api
    try:
        # Episode 1
        info = _BR._detect_rate_limit(slot)
        if info is not None and not slot.rate_limit_notified:
            slot.rate_limit_notified = True
            _BR._notify_rate_limit(slot, info)
        count_after_ep1 = len(_sent_messages)
        assert count_after_ep1 >= 1

        # 訊號消失 → 清旗標
        slot.screen = _FakeScreen(normal_lines)
        slot._display_cache = None
        info2 = _BR._detect_rate_limit(slot)
        assert info2 is None, "正常畫面應回 None"
        if info2 is None:
            slot.rate_limit_notified = False

        assert not slot.rate_limit_notified, "旗標應已清除"

        # Episode 2：rate-limit 再次出現 → 應再通知
        slot.screen = _FakeScreen(rate_lines)
        slot._display_cache = None
        info3 = _BR._detect_rate_limit(slot)
        if info3 is not None and not slot.rate_limit_notified:
            slot.rate_limit_notified = True
            _BR._notify_rate_limit(slot, info3)
        count_after_ep2 = len(_sent_messages)
        assert count_after_ep2 > count_after_ep1, (
            f"第二次 episode 應再送通知（sent_count: {count_after_ep2}）")
    finally:
        _bt.tg_api = _orig_tg_api


def test_normal_screen_no_false_positive():
    """正常 Claude 輸出不觸發 rate-limit 偵測。"""
    lines = [
        "  • Sure, I can help with that.",
        "  Let me look at the code.",
        "  › ",
    ]
    slot = _make_slot(lines)
    info = _BR._detect_rate_limit(slot)
    assert info is None, f"正常畫面應回 None，但回傳：{info}"


def test_usage_credits_line_detected():
    """/usage-credits 單行也能命中（橫幅情況二）。"""
    lines = [
        "  /usage-credits to finish what you're working on.",
    ]
    slot = _make_slot(lines)
    info = _BR._detect_rate_limit(slot)
    assert info is not None, "含 /usage-credits 應命中"


# ── 執行器 ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import traceback

    fails = 0
    for name in sorted(globals()):
        if name.startswith("test_") and callable(globals()[name]):
            try:
                globals()[name]()
                print(f"PASS  {name}")
            except Exception:
                fails += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
