#!/usr/bin/env python3
"""虛擬終端幾何回歸測試（v0.29.46）。

bridge 每個 slot 有自己的 pyte 螢幕，用來讀「現在畫面」判斷送達／忙碌／
卡住。它固定 200x50，真實 PTY 卻常是 101x31 —— CLI 只會重畫 viewport 內
的列，**viewport 以下那些列永遠停在上一次被畫到的內容**（殘影）。
`_live_tail` 取最後幾行非空列，於是每次都撈到 12:49 的殘影而不是現在的
footer → 'esc to interrupt' 永遠配不到 →

  · 每則訊息都回「⚠ 無法確認訊息已送進…」（送達驗證兩個訊號全瞎）
  · busy guard 失效（回合進行中照樣注入）
  · marker fallback 的 turn_ended 誤判

跑法：.venv/bin/python tests_tg_screen_geometry.py
"""

import importlib.util
import os
import threading

import pyte

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

VIEWPORT_ROWS = 31          # 真實 pane 高度（s161 實測 101x31）
FOOTER = "✻ Sautéing… (esc to interrupt)"


def _bridge():
    br = object.__new__(_bt.TelegramBridge)
    br.slots = {}
    br._slot_order = []
    br._slots_lock = threading.Lock()
    return br


def _paint_ghosts(slot):
    """模擬「以前畫面比較高」留下的殘影：viewport 以下的列。"""
    for row, text in ((38, "  挖信箱裡那條技術討論串當硬材料，呼叫了 5 次"),
                      (39, "  查信。這種長推理很像卡住但其實沒事。"),
                      (40, "✻ Worked for 3m 50s · done 12:49 PM"),
                      (41, "─" * 60), (42, "❯"), (43, "─" * 60)):
        slot.stream.feed(f"\x1b[{row};1H{text}")
    slot._feed_gen += 1


def _paint_live_turn(slot):
    """模擬 CLI 在 viewport 內重畫一個進行中的回合。"""
    rows = [(VIEWPORT_ROWS - 5, FOOTER),
            (VIEWPORT_ROWS - 4, "─" * 60),
            (VIEWPORT_ROWS - 3, "❯"),
            (VIEWPORT_ROWS - 2, "─" * 60),
            (VIEWPORT_ROWS - 1, "  ⏵⏵ bypass permissions on (shift+tab to cycle)"),
            (VIEWPORT_ROWS, "[sf_s161] 0:2.1.246*                        20:58 26-Aug")]
    for row, text in rows:
        slot.stream.feed(f"\x1b[{row};1H{text}")
    slot._feed_gen += 1


# ── 1. 錯的幾何＝看不到 footer（釘住 bug 本體）──
def test_oversized_screen_samples_ghosts():
    br = _bridge()
    br.register_session("s1", "雜事", lambda t: None)   # 沒給 cols/rows → 200x50
    slot = br.slots["s1"]
    assert (slot.screen_cols, slot.screen_rows) == (200, 50)
    _paint_ghosts(slot)
    _paint_live_turn(slot)
    tail = br._live_tail(slot)
    assert "esc to interrupt" not in tail, "殘影應該蓋掉 footer（bug 重現失敗）"
    assert "12:49" in tail, f"取樣窗沒撈到殘影？{tail!r}"


# ── 2. 對齊真實高度＝footer 抓得到 ──
def test_right_sized_screen_sees_footer():
    br = _bridge()
    br.register_session("s1", "雜事", lambda t: None, cols=101, rows=VIEWPORT_ROWS)
    slot = br.slots["s1"]
    assert slot.screen_rows == VIEWPORT_ROWS
    _paint_ghosts(slot)          # 螢幕內寫不出 viewport 以下的列
    _paint_live_turn(slot)
    assert "esc to interrupt" in br._live_tail(slot)


# ── 3. resize_session：換掉螢幕、殘影消失、scrollback 留著 ──
def test_resize_session_drops_ghosts_keeps_history():
    br = _bridge()
    br.register_session("s1", "雜事", lambda t: None)
    slot = br.slots["s1"]
    slot.stream.feed("舊回覆內容\r\n" * 60)      # 推一些列進 history
    hist_before = len(slot.screen.history.top)
    assert hist_before > 0
    _paint_ghosts(slot)
    br.resize_session("s1", 101, VIEWPORT_ROWS)
    assert (slot.screen_cols, slot.screen_rows) == (200, VIEWPORT_ROWS)
    assert len(slot.screen.history.top) == hist_before, "scrollback 不該被丟掉"
    _paint_live_turn(slot)                        # SIGWINCH 後 CLI 重畫
    assert "esc to interrupt" in br._live_tail(slot)
    assert "12:49" not in br._live_tail(slot)


# ── 4. 重複註冊（restart／重新 attach）也要帶新幾何 ──
def test_reregister_resizes():
    br = _bridge()
    br.register_session("s1", "雜事", lambda t: None)
    br.register_session("s1", "雜事", lambda t: None, cols=101, rows=VIEWPORT_ROWS)
    assert br.slots["s1"].screen_rows == VIEWPORT_ROWS
    assert len(br._slot_order) == 1, "重複註冊不該長出第二格"


# ── 5. 為什麼不用 pyte 自己的 resize()：縮列是從**上面**砍 ──
def test_pyte_native_resize_would_keep_ghosts():
    screen = pyte.HistoryScreen(200, 50, history=800)
    stream = pyte.Stream(screen)
    for row in range(1, VIEWPORT_ROWS + 1):
        stream.feed(f"\x1b[{row};1HLIVE{row}")
    stream.feed(f"\x1b[45;1HGHOST")
    screen.resize(VIEWPORT_ROWS, 101)
    kept = [l for l in screen.display if l.strip()]
    assert any("GHOST" in l for l in kept), "pyte 若改成砍下面，這條可以刪掉"
    assert not any("LIVE1 " in l or l.strip() == "LIVE1" for l in kept), \
        "pyte resize 保留了最上面的列？語意變了，回頭檢查 resize_session"


# ── 6. _screen_dims 夾範圍：高度不明時退回舊行為，不能比 viewport 還矮 ──
def test_screen_dims_clamped():
    assert _bt._screen_dims(101, 31) == (200, 31)      # 寬度保持寬鬆
    assert _bt._screen_dims(0, 0) == (200, 50)         # 不明 → 舊預設
    assert _bt._screen_dims(101, 3) == (200, 50)       # 荒謬高度不採用
    assert _bt._screen_dims(101, 9999) == (200, 50)
    assert _bt._screen_dims(None, None) == (200, 50)
    assert _bt._screen_dims(400, 60) == (400, 60)


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
