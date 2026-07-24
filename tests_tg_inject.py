"""TG 注入送達驗證（_verify_injection）回歸測試。

兩個案例來自 v0.23.0 對抗性審查抓到的 CONFIRMED bug：
  1. extraction loop 會把 slot.last_write_ts 歸零 → 舊版跟 last_write_ts 比
     會被「前一輪的 extraction」假 delivered。現在跟 injected_at 快照比。
  2. shell 分頁會 echo 輸入 → 殘留判定誤觸重複注入。現在 verify/retry 只對
     AI 分頁（detect_ai(slot.cmd)）啟用——該 gate 在 _send() 內，此處測
     verify 本身的判定正確性。

跑法：
    .venv/bin/python tests_tg_inject.py
    .venv/bin/python -m pytest tests_tg_inject.py
"""

import importlib.util
import os
import sys
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

BR = object.__new__(_bt.TelegramBridge)
PAYLOAD = "[SF-TG wrapper]\n最終要回 Telegram 的文字…\n\nhoward: 幫我查一下部署狀態好嗎"


def _slot(screen, extraction_ts=0.0, write_ts=100.0):
    return types.SimpleNamespace(peek_fn=lambda: screen,
                                 last_extraction_ts=extraction_ts,
                                 last_write_ts=write_ts)


# ── 1. turn 開始 footer → delivered ──
def test_turn_start_is_delivered():
    got = BR._verify_injection(_slot("...thinking (esc to interrupt)"), PAYLOAD,
                               injected_at=100.0, window=0.6)
    assert got == (True, False), got


# ── 2. 注入「之後」的 extraction → delivered ──
def test_fresh_extraction_is_delivered():
    got = BR._verify_injection(_slot("whatever", extraction_ts=200.0), PAYLOAD,
                               injected_at=150.0, window=0.6)
    assert got == (True, False), got


# ── 3. 審查 bug #1：注入「之前」的舊 extraction（且 last_write_ts 被
#      extraction loop 歸零）不得假 delivered；殘留在畫面 → 可重試 ──
def test_stale_extraction_not_delivered():
    slot = _slot("\x1b[38;5;242m> \x1b[0mhoward: 幫我查一下部署狀態好嗎",
                 extraction_ts=100.0, write_ts=0.0)   # write_ts 已被歸零
    got = BR._verify_injection(slot, PAYLOAD, injected_at=150.0, window=0.6)
    assert got == (False, True), f"舊 extraction 假 delivered 回歸：{got}"


# ── 4. 殘留判定：payload 最後一行掛在畫面（含 ANSI）→ (False, True) ──
def test_composer_residue_detected():
    slot = _slot("\x1b[38;5;242m> \x1b[0mhoward: 幫我查一下部署狀態好嗎\x1b[K\n ▶▶ bypass")
    got = BR._verify_injection(slot, PAYLOAD, injected_at=100.0, window=0.6)
    assert got == (False, True), got


# ── 5. 不確定（畫面無殘留、無訊號）→ (False, False)：不重試不吵 ──
def test_ambiguous_no_retry():
    got = BR._verify_injection(_slot("完全無關的畫面內容"), PAYLOAD,
                               injected_at=100.0, window=0.6)
    assert got == (False, False), got


# ── 6. v0.29.1：stale PTY ring 不得假 delivered——訊號源改 live screen。
#      turn 結束後 'esc to interrupt' 殘留在 ring（peek_fn），但現在畫面
#      （pyte display）乾淨 → 不可回 (True, False)（那會讓真失敗不重試
#      不通知，Howard 的「/fetch 後訊息送不進去」）。──
def test_stale_ring_not_delivered():
    slot = _slot("...舊輸出 (esc to interrupt) 殘影...")  # ring 有殘影
    # 模擬 live screen（走 _slot_display 的 cache-hit 路徑，免建真 pyte）
    slot.screen = object()
    slot._feed_gen = 1
    slot._display_cache = ["❯ 輸入框", "", "  乾淨的 idle 畫面"]
    slot._display_cache_gen = 1
    got = BR._verify_injection(slot, PAYLOAD, injected_at=100.0, window=0.6)
    assert got == (False, False), got


# ── 7. v0.29.1：live screen 顯示 mid-turn → delivered（CC 會把注入排隊，
#      不得 retry 造成重複送出）──
def test_live_screen_midturn_is_delivered():
    slot = _slot("ring 沒有訊號")
    slot.screen = object()
    slot._feed_gen = 1
    slot._display_cache = ["回覆生成中…", "(esc to interrupt)"]
    slot._display_cache_gen = 1
    got = BR._verify_injection(slot, PAYLOAD, injected_at=100.0, window=0.6)
    assert got == (True, False), got


# ── 8. v0.29.9：codex paste chip 摺疊 → payload 尾段不在畫面上，但內容
#      確實卡在 composer——必須視為 residue（可 nudge/重試），不得落入
#      (False, False) 靜默放棄（Windows/ConPTY 卡輸入框主場景）──
def test_paste_chip_is_residue():
    got = BR._verify_injection(_slot("❯ [Pasted Content 42 lines]"), PAYLOAD,
                               injected_at=100.0, window=0.6)
    assert got == (False, True), got


# ── 9. v0.29.9：_wait_paste_drain——echo 已安靜時只等下限（~0.3s），
#      echo 持續滾動時等到 cap 為止（不無限等）──
def test_wait_paste_drain_quiet_vs_noisy():
    import time as _t
    quiet = types.SimpleNamespace(last_chunk_ts=_t.time() - 10.0)
    t0 = _t.time()
    BR._wait_paste_drain(quiet, 500)
    quiet_elapsed = _t.time() - t0
    assert 0.25 <= quiet_elapsed < 0.9, f"安靜畫面應只等下限：{quiet_elapsed:.2f}s"

    class _Ticker:
        # last_chunk_ts 每次讀都是「剛剛」→ 永不安靜，必須由 cap 收斂
        @property
        def last_chunk_ts(self):
            return _t.time()
    t0 = _t.time()
    BR._wait_paste_drain(_Ticker(), 0)
    noisy_elapsed = _t.time() - t0
    cap_max = (3.0 if _bt._IS_WIN else 1.0) + 0.5
    assert quiet_elapsed < noisy_elapsed <= cap_max, \
        f"滾動畫面應等到 cap：{noisy_elapsed:.2f}s (cap≈{cap_max})"


# ── 11. v0.29.19：轉發雜訊過濾——marker 行 / 輪換動詞 footer / 標題分隔線
#      不得混進 TG 訊息（Howard 2026-07-24 截圖回歸）──
def test_forward_noise_lines():
    noise = [
        "[[TG_REPLY_8672de59]]",
        "[[/TG_REPLY_7bd51f7f]]",
        "✳ Cogitated for 1m 38s",
        "✻ Crunched for 1m 50s",
        "* Baked for 12s",
        "new task? /clear to save 670.3k tokens",
        "──────────────── 整理 HR 系統待簽單 ────",
        "────────────────────────",
    ]
    for ln in noise:
        assert BR._is_forward_noise_line(ln), f"應判雜訊：{ln!r}"
    content = [
        "✅ 簽完了。原本列的 20 筆＋新進 17 筆，總共 37 筆全部簽核成功",
        "那筆曾婉瑜的生理假要不要保留？我可以幫你看內容",
        "Testing for 30 seconds of load then report",
        "補打卡已排 9:10——完成後回報",
    ]
    for ln in content:
        assert not BR._is_forward_noise_line(ln), f"誤殺內容：{ln!r}"


# ── 12. v0.29.19：延遲判定——快回合的「不確定」不再立刻發假警報；
#      有 extraction 訊號→靜默收工、全程無聲→才通知 ──
def test_deferred_verdict():
    import time as _t
    br = object.__new__(_bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="tok")
    sent = []
    orig = _bt.tg_api
    _bt.tg_api = lambda *a, **k: sent.append(a)
    try:
        # a) 注入後有 extraction → 不通知
        slot = _slot("乾淨畫面", extraction_ts=200.0)
        slot.label, slot.index, slot.sid = "HR", 5, "s1"
        br._deferred_delivery_verdict(slot, 123, injected_at=150.0, extra_wait=1.2)
        assert sent == [], f"有訊號仍通知：{sent}"
        # b) 全程無訊號 → 通知一次
        slot2 = _slot("乾淨畫面", extraction_ts=0.0)
        slot2.label, slot2.index, slot2.sid = "HR", 5, "s2"
        t0 = _t.time()
        br._deferred_delivery_verdict(slot2, 123, injected_at=150.0, extra_wait=1.2)
        assert len(sent) == 1, f"無訊號應通知一次：{sent}"
        assert _t.time() - t0 >= 1.0, "應等滿觀察窗才通知"
    finally:
        _bt.tg_api = orig


# ── 10. 審查 bug #2 的 gate 素材：detect_ai 分流正確（gate 本身在 _send 閉包內）──
def test_detect_ai_gate_material():
    assert _bt._detect_ai("claude --permission-mode x") == "claude"
    assert _bt._detect_ai("/usr/local/bin/sf-codex") == "codex"
    assert _bt._detect_ai("/bin/zsh -l") is None
    assert _bt._detect_ai("") is None


if __name__ == "__main__":
    import traceback
    fails = 0
    for name in sorted(list(globals())):
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
