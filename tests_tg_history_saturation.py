#!/usr/bin/env python3
"""P0-2 回歸測試：pyte history deque 飽和 → scrollback 永久失明（2026-08-17）。

`slot.screen = pyte.HistoryScreen(200, 50, history=800)`，`history.top` 是
`deque(maxlen=800)`。**滿了之後 len() 恆為 800**，內容從左邊被擠掉、長度不變，
於是 `_history_offset` 卡死在 800：`> hlen` 為假（相等）、`< hlen` 也為假 →
這個 slot 此後永遠不再掃描任何 scrollback，只剩 50 行 live screen 可抽，
兩次 flush tick 之間捲過去的回覆就永久遺失。長壽命分頁（跑兩天的 s87）必中。

修法：偵測 `hlen == maxlen` 時改為固定重掃 history 尾端 K=64 行，靠
`sent_responses`（已改成保序的 _OrderedSet）去重；再加一道「最後一行沒變就
跳過」的廉價 dirty gate，避免每 tick 白掃。

跑法：.venv/bin/python tests_tg_history_saturation.py
"""

import importlib.util
import itertools
import os

import pyte

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)


def _bridge():
    br = object.__new__(_bt.TelegramBridge)
    br._perf_enabled = False
    br._perf = {}
    return br


def _slot():
    return _bt.SessionSlot("s87", "調研者", lambda t: None, 11)


def _saturate(slot, n=1200):
    for i in range(n):
        slot.stream.feed(f"noise line {i}\r\n")


# ── 1. 先確認前提成立：deque 真的會飽和且 len() 不再增長 ──
def test_deque_saturates():
    s = pyte.HistoryScreen(80, 5, history=10)
    st = pyte.Stream(s)
    for i in range(40):
        st.feed(f"line {i}\r\n")
    top = s.history.top
    assert len(top) == top.maxlen == 10, (len(top), top.maxlen)


# ── 2. 舊邏輯（純 _history_offset）飽和後掃到 0 行 —— 失明重現 ──
def test_old_offset_logic_goes_blind():
    slot = _slot()
    _saturate(slot)
    htop = slot.screen.history.top
    hlen = len(htop)
    cols = slot.screen.columns

    def old_scan(offset):
        got = []
        if offset > hlen:
            offset = 0
        if offset < len(htop):
            for hl in itertools.islice(htop, offset, len(htop)):
                got.append("".join(hl[c].data for c in range(cols)).rstrip())
            offset = len(htop)
        return got, offset

    _, offset = old_scan(0)
    assert offset == hlen == htop.maxlen
    slot.stream.feed("⏺ 這是捲走的重要回覆\r\n")
    for i in range(60):
        slot.stream.feed(f"after {i}\r\n")
    got, _ = old_scan(offset)
    assert got == [], f"舊邏輯不該掃到任何東西（失明）: {got[:3]}"


# ── 3. 修後：飽和分頁仍抽得到捲進 scrollback 的回覆 ──
def test_saturated_slot_still_extracts():
    br, slot = _bridge(), _slot()
    _saturate(slot)
    br._extract_new_text(slot)              # 吸乾既有內容
    assert slot._history_offset == slot.screen.history.top.maxlen
    slot.stream.feed("⏺ 這是捲走的重要回覆\r\n")
    for i in range(60):                     # 把它推進 scrollback
        slot.stream.feed(f"after {i}\r\n")
    out = br._extract_new_text(slot)
    assert any("這是捲走的重要回覆" in t for t in out), out


# ── 4. 不會重複轉發：同一塊內容第二次掃描被 sent_responses 擋掉 ──
def test_no_duplicate_on_rescan():
    br, slot = _bridge(), _slot()
    _saturate(slot)
    br._extract_new_text(slot)
    slot.stream.feed("⏺ 只能送一次的回覆\r\n")
    for i in range(60):
        slot.stream.feed(f"after {i}\r\n")
    first = br._extract_new_text(slot)
    assert any("只能送一次的回覆" in t for t in first), first
    second = br._extract_new_text(slot)
    assert not any("只能送一次的回覆" in t for t in second), second


# ── 5. dirty gate：history 沒長新行時不重掃尾端 ──
def test_tail_gate_skips_when_history_unchanged():
    br, slot = _bridge(), _slot()
    _saturate(slot)
    br._extract_new_text(slot)
    sig_before = slot._hist_tail_sig
    br._extract_new_text(slot)              # 沒有新輸出
    assert slot._hist_tail_sig == sig_before
    slot.stream.feed("⏺ 新東西\r\n")
    for i in range(60):
        slot.stream.feed(f"more {i}\r\n")
    br._extract_new_text(slot)
    assert slot._hist_tail_sig != sig_before, "history 長了新行，signature 應該要變"


# ── 6. 未飽和的 slot 行為不變（只掃 offset 之後的新行）──
def test_unsaturated_unchanged():
    br, slot = _bridge(), _slot()
    for i in range(120):                    # 遠低於 maxlen=800
        slot.stream.feed(f"line {i}\r\n")
    br._extract_new_text(slot)
    hlen = len(slot.screen.history.top)
    assert slot._history_offset == hlen
    assert hlen < slot.screen.history.top.maxlen
    assert slot._hist_tail_sig is None, "未飽和不該啟用尾端重掃"


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
