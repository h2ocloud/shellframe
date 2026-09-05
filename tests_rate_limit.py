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

# 測試不得寫進 production 的 bridge log（使用者靠那份 log 除錯）
_bt._blog = lambda msg: None

# ── 建立最小可用的 TelegramBridge 實例（不啟動執行緒）───────────────────────
_BR = object.__new__(_bt.TelegramBridge)
_BR._slot_order = []
_BR._user_active = {}
_BR._user_chat = {}
_BR.config = types.SimpleNamespace(bot_token="FAKE_TOKEN")
_BR._rate_limit_seen = {}
_BR._save_state = lambda: None


def _tick(bridge, slot):
    """One slow_tick of the poll loop's rate-limit branch.

    Copied structurally from _poll_loop so the dedup tests exercise the real
    decision (persisted signature) rather than a flag the loop no longer reads.
    """
    info = bridge._detect_rate_limit(slot)
    if info is not None:
        sig = bridge._rate_limit_signature(info)
        slot.rate_limit_notified = True
        if bridge._rate_limit_seen.get(slot.sid) != sig:
            bridge._rate_limit_seen[slot.sid] = sig
            bridge._notify_rate_limit(slot, info)
    else:
        slot.rate_limit_notified = False
        bridge._rate_limit_seen.pop(slot.sid, None)
    return info


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
    """連續兩 tick 偵測到同一個 episode，只通知一次。"""
    lines = [
        "  ⎿  You've hit your session limit · resets 5pm (Asia/Taipei)",
        "  /usage-credits to finish what you're working on.",
    ]
    slot = _make_slot(lines)
    _BR._slot_order = [slot.sid]
    _BR._user_chat = {1: 9999}
    _BR._user_active = {1: slot.sid}
    _BR._rate_limit_seen = {}

    _sent_messages.clear()
    _bt.tg_api = _fake_tg_api
    try:
        _tick(_BR, slot)
        first_count = len(_sent_messages)
        assert first_count >= 1, "第一次應送通知"

        _tick(_BR, slot)
        assert len(_sent_messages) == first_count, (
            f"第二 tick 不應再送（sent_count: {len(_sent_messages)}）")
    finally:
        _bt.tg_api = _orig_tg_api


def test_dedup_survives_bridge_restart():
    """Bridge 重啟（slot 物件重建）後不得重送同一個 episode。

    這是 TG 狂跳的主因：旗標掛在 SessionSlot 上，bridge 一重啟就歸零，
    而 bridge 重啟得比一次額度視窗還頻繁。
    """
    lines = [
        "  ⎿  You've hit your session limit · resets 5pm (Asia/Taipei)",
        "  /usage-credits to finish what you're working on.",
    ]
    slot = _make_slot(lines)
    _BR._slot_order = [slot.sid]
    _BR._user_chat = {1: 9999}
    _BR._user_active = {1: slot.sid}
    _BR._rate_limit_seen = {}

    _sent_messages.clear()
    _bt.tg_api = _fake_tg_api
    try:
        _tick(_BR, slot)
        first_count = len(_sent_messages)
        assert first_count >= 1

        # 重啟：slot 全新（rate_limit_notified=False），signature map 從磁碟回來
        fresh = _make_slot(lines)
        _tick(_BR, fresh)
        assert len(_sent_messages) == first_count, (
            "重啟後重送了同一個 episode"
            f"（sent_count: {len(_sent_messages)} vs {first_count}）")
    finally:
        _bt.tg_api = _orig_tg_api


def test_clear_and_renotify_after_reset():
    """訊號消失 → 忘掉 episode → 再次出現 → 重新通知。"""
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
    _BR._rate_limit_seen = {}

    _sent_messages.clear()
    _bt.tg_api = _fake_tg_api
    try:
        _tick(_BR, slot)
        count_after_ep1 = len(_sent_messages)
        assert count_after_ep1 >= 1

        slot.screen = _FakeScreen(normal_lines)
        slot._display_cache = None
        assert _tick(_BR, slot) is None, "正常畫面應回 None"
        assert slot.sid not in _BR._rate_limit_seen, "episode 應已忘掉"

        slot.screen = _FakeScreen(rate_lines)
        slot._display_cache = None
        _tick(_BR, slot)
        assert len(_sent_messages) > count_after_ep1, (
            f"第二次 episode 應再送通知（sent_count: {len(_sent_messages)}）")
    finally:
        _bt.tg_api = _orig_tg_api


# 額度視窗滾過去之後的真實畫面形狀：橫幅還在，但 Claude 自己印了 reset 行，
# 底下對話又繼續了好幾行。這份畫面在日常使用中每分鐘重送一次通知。
_STALE_SCREEN = [
    "  ⏺ Stopped watching Artifact: \"notes\" (artifact not found)",
    "     ⎿  You've hit your session limit · resets 10:40pm (Asia/Taipei)",
    "        /upgrade or /usage-credits to finish what you're working on.",
    "",
    "  ⏺ Usage limit reached · continuing automatically at 10:40pm · esc or type to cancel",
    "",
    "  ✻ Brewed for 0s · done 7:23 PM",
    "",
    "  ⏺ Usage limit reset · continuing automatically",
    "",
    "  ⏺ 沒有進行中的任務被中斷——上次的工作已經完成並交付了。",
    "",
    "  ✻ Brewed for 31s · done 10:41 PM",
    "",
    "  " + "\u2500" * 100,
    "  ❯ ",
    "  " + "\u2500" * 100,
    "    ⏵⏵ bypass permissions on (shift+tab to cycle)",
]


def test_finished_episode_not_reported():
    """視窗已經滾過去（畫面上有 reset 行）→ 不得再視為 live。"""
    slot = _make_slot(list(_STALE_SCREEN))
    assert _BR._detect_rate_limit(slot) is None, (
        "額度已重置的畫面仍被判為 rate-limit")


def test_scrolled_away_banner_not_reported():
    """reset 行已捲掉、只剩橫幅，但對話早就走遠 → 不得再視為 live。"""
    screen = [l for l in _STALE_SCREEN if "Usage limit reset" not in l]
    screen[9:9] = ["  接下來我把三個 sheet 的文字重新順過。", "",
                   "  - 功能說明改成規格表語氣", "",
                   "  - 每個儲存格都加了註解", "",
                   "  - 欄位名稱同步改掉", "",
                   "  ⏺ 已經寫回檔案了。", "",
                   "  ✻ Brewed for 12s · done 11:02 PM", ""]
    slot = _make_slot(screen)
    assert _BR._detect_rate_limit(slot) is None, (
        "橫幅已是 scrollback，仍被判為 rate-limit")


def test_live_banner_above_prompt_still_reported():
    """橫幅就在輸入框上方（真的正在卡額度）→ 仍要通知。"""
    screen = [
        "  ⏺ 我來看一下這段程式。",
        "",
        "     ⎿  You've hit your session limit · resets 10:40pm (Asia/Taipei)",
        "        /upgrade or /usage-credits to finish what you're working on.",
        "",
        "  ⏺ Usage limit reached · continuing automatically at 10:40pm",
        "",
        "  " + "\u2500" * 100,
        "  ❯ ",
        "  " + "\u2500" * 100,
    ]
    slot = _make_slot(screen)
    info = _BR._detect_rate_limit(slot)
    assert info is not None, "真的卡額度時仍必須通知"
    assert "10:40pm" in info["reset"]


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
