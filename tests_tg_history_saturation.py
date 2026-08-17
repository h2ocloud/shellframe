#!/usr/bin/env python3
"""P0-2 回歸測試：pyte history deque 飽和 → scrollback 永久失明（2026-08-17）。

`slot.screen = pyte.HistoryScreen(200, 50, history=800)`，`history.top` 是
`deque(maxlen=800)`。**滿了之後 len() 恆為 800**，內容從左邊被擠掉、長度不變，
於是 `_history_offset` 卡死在 800：`> hlen` 為假（相等）、`< hlen` 也為假 →
這個 slot 此後永遠不再掃描任何 scrollback，只剩 50 行 live screen 可抽，
兩次 flush tick 之間捲過去的回覆就永久遺失。長壽命分頁（跑兩天的 s87）必中。

修法：偵測 `hlen == maxlen` 時改為固定重掃 history 尾端 K=64 行，靠
`sent_responses`（已改成保序的 _OrderedSet）去重；dirty gate 用單調遞增的
`_feed_gen`，避免每 tick 白掃。

本檔同時覆蓋 QA 2026-08-17 抓到的兩個後續問題：
  * **B-1（Blocker）**：飽和重掃讓「沒有提示行終結的 AI block」每輪多吃幾行，
    superset 規則把每一版都當「加長版、該送」→ 同一段內容轉發 64 次。
  * **M-1**：dirty gate 原本取「history 最後一行」當 signature，該行內容前後
    相同時（空白行／重複 log 行）重掃被整個跳過 → 靜默丟訊沒根治。

跑法：.venv/bin/python tests_tg_history_saturation.py
"""

import importlib.util
import itertools
import os
import threading

import pyte

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
# 測試不要污染 production 的 /tmp/shellframe_bridge.log（Howard 靠它除錯）
_bt._blog = lambda msg: None


def _bridge():
    """真實 bridge + 走真實攝入路徑 feed_output()。

    ⚠ 不要用 `slot.stream.feed()` 直接餵——那會繞過 `feed_output`，`_feed_gen`
    永遠不動，飽和重掃的 dirty gate 就被永久關住，測到的行為與 production 不同
    （QA 的重現腳本就是踩到這點）。"""
    br = object.__new__(_bt.TelegramBridge)
    br._perf_enabled = False
    br._perf = {}
    br.active = True
    br._flush_wake = threading.Event()
    br.slots = {}
    return br


def _slot(br, sid="s87"):
    slot = _bt.SessionSlot(sid, "調研者", lambda t: None, 11)
    slot.awaiting_response = False
    br.slots[sid] = slot
    return slot


def _feed(br, slot, text):
    br.feed_output(slot.sid, text)


def _saturate(br, slot, n=1200):
    for i in range(n):
        _feed(br, slot, f"noise line {i}\r\n")


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
    br = _bridge()
    slot = _slot(br)
    _saturate(br, slot)
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
    _feed(br, slot, "⏺ 這是捲走的重要回覆\r\n")
    for i in range(60):
        _feed(br, slot, f"after {i}\r\n")
    got, _ = old_scan(offset)
    assert got == [], f"舊邏輯不該掃到任何東西（失明）: {got[:3]}"


# ── 3. 修後：飽和分頁仍抽得到捲進 scrollback 的回覆 ──
def test_saturated_slot_still_extracts():
    br = _bridge()
    slot = _slot(br)
    _saturate(br, slot)
    br._extract_new_text(slot)              # 吸乾既有內容
    assert slot._history_offset == slot.screen.history.top.maxlen
    _feed(br, slot, "⏺ 這是捲走的重要回覆\r\n")
    for i in range(60):                     # 把它推進 scrollback（超出 50 行畫面）
        _feed(br, slot, f"after {i}\r\n")
    out = br._extract_new_text(slot)
    assert any("這是捲走的重要回覆" in t for t in out), out


# ── 4. M-1：history 尾端那一行「內容沒變」時，仍然抽得到（signature gate 盲點）──
def _tail_line(slot):
    htop = slot.screen.history.top
    cols = slot.screen.columns
    return "".join(htop[-1][c].data for c in range(cols)).rstrip()


def test_unchanged_tail_line_no_blind_spot():
    """舊 dirty gate 取「history 最後一行」當 signature。當捲出去的那一行內容
    前後相同時（空白行、重複的 log 行——TUI 上極常見）signature 不變 → 重掃被
    整個跳過，其間捲過去的內容永久遺失（靜默丟訊沒根治）。
    改用單調遞增的 `_feed_gen` 就沒有這個盲點。"""
    br = _bridge()
    slot = _slot(br)
    _saturate(br, slot)
    br._extract_new_text(slot)

    FILLER = "keepalive"
    for _ in range(60):
        _feed(br, slot, f"{FILLER}\r\n")
    br._extract_new_text(slot)
    sig_before = _tail_line(slot)

    _feed(br, slot, "⏺ 捲走的回覆不能不見\r\n")
    for _ in range(60):                     # 推出畫面（50 行）但仍在 K=64 窗內
        _feed(br, slot, f"{FILLER}\r\n")
    sig_after = _tail_line(slot)

    assert sig_before == sig_after == FILLER, (
        f"前提不成立：signature 應該前後相同才構成盲點 {sig_before!r} vs {sig_after!r}")
    out = br._extract_new_text(slot)
    assert any("捲走的回覆不能不見" in t for t in out), (
        f"signature 沒變就跳過重掃＝靜默丟訊；應改用 _feed_gen。out={out}")


# ── 5. 不會重複轉發：同一塊內容第二次掃描被 sent_responses 擋掉 ──
def test_no_duplicate_on_rescan():
    br = _bridge()
    slot = _slot(br)
    _saturate(br, slot)
    br._extract_new_text(slot)
    _feed(br, slot, "⏺ 只能送一次的回覆\r\n")
    for i in range(60):
        _feed(br, slot, f"after {i}\r\n")
    first = br._extract_new_text(slot)
    assert any("只能送一次的回覆" in t for t in first), first
    second = br._extract_new_text(slot)
    assert not any("只能送一次的回覆" in t for t in second), second


# ── 6. B-1（QA Blocker）：飽和分頁 + 純捲動輸出，不得重複洗版 ──
def _dup_count(br, slot, feeder, rounds=140, mark="只能出現一次"):
    _feed(br, slot, f"⏺ 唯一一則重要回覆，{mark}\r\n")
    hits = []
    for k in range(rounds):
        feeder(br, slot, k)
        for t in br._extract_new_text(slot):
            if mark in t:
                hits.append(len(t))
    return hits


def test_no_flood_on_plain_scrolling_output():
    """長 build / `tail -f` / 訓練 log：沒有提示行終結 AI block，飽和重掃讓
    block 每輪多吃一行 → 舊版每一版都被 superset 規則判成「加長版、該送」，
    同一段內容轉發 64 次、每則遞增一行（799, 806, 813…）。"""
    br = _bridge()
    slot = _slot(br)
    _saturate(br, slot)
    br._extract_new_text(slot)
    hits = _dup_count(br, slot, lambda b, s, k: _feed(b, s, f"背景輸出 {k}\r\n"))
    assert len(hits) <= 1, f"純捲動輸出被重複轉發 {len(hits)} 次，長度={hits[:6]}"


def test_tui_shapes_still_forward_once():
    """Howard 日常在用的 Claude Code TUI 形狀必須維持 1 次——B-1 的修法
    不得動到這條已經正確的路徑。"""
    br = _bridge()
    slot = _slot(br)
    _saturate(br, slot)
    br._extract_new_text(slot)

    def tui(b, s, k):
        _feed(b, s, f"  工具輸出 {k}\r\n")
        _feed(b, s, "› \r\n")
    assert len(_dup_count(br, slot, tui)) == 1

    br2 = _bridge()
    slot2 = _slot(br2, "s88")
    _saturate(br2, slot2)
    br2._extract_new_text(slot2)
    hits = _dup_count(br2, slot2, lambda b, s, k: _feed(b, s, f"  ⎿ 工具結果 {k}\r\n"))
    assert len(hits) == 1, hits


def test_growing_block_forwards_final_version_once_closed():
    """仍在增長時壓住，但 block 一旦被提示行終結，最終完整版要送得出去
    ——不能為了防洗版而製造新的靜默丟訊。"""
    br = _bridge()
    slot = _slot(br)
    _saturate(br, slot)
    br._extract_new_text(slot)
    _feed(br, slot, "⏺ 開頭\r\n")
    br._extract_new_text(slot)              # 第一版送出（開頭）
    for i in range(3):
        _feed(br, slot, f"續行 {i}\r\n")
        got = br._extract_new_text(slot)
        assert not any("續行" in t for t in got), f"仍在增長時不該送: {got}"
    _feed(br, slot, "› \r\n")             # 提示行終結 block
    out = br._extract_new_text(slot)
    assert any("續行 2" in t for t in out), f"終結後應送最終完整版: {out}"


# ── 7. dirty gate：沒有新 PTY bytes 時不重掃尾端 ──
def test_tail_gate_skips_without_new_bytes():
    br = _bridge()
    slot = _slot(br)
    _saturate(br, slot)
    br._extract_new_text(slot)
    gen_after = slot._hist_scan_gen
    assert gen_after == slot._feed_gen
    br._extract_new_text(slot)              # 沒有新 bytes
    assert slot._hist_scan_gen == gen_after, "沒有新 bytes 不該重掃"
    _feed(br, slot, "新東西\r\n")
    br._extract_new_text(slot)
    assert slot._hist_scan_gen > gen_after, "有新 bytes 就要重掃"


# ── 8. 未飽和的 slot 行為不變（只掃 offset 之後的新行）──
def test_unsaturated_unchanged():
    br = _bridge()
    slot = _slot(br)
    for i in range(120):                    # 遠低於 maxlen=800
        _feed(br, slot, f"line {i}\r\n")
    br._extract_new_text(slot)
    hlen = len(slot.screen.history.top)
    assert slot._history_offset == hlen
    assert hlen < slot.screen.history.top.maxlen
    assert slot._hist_scan_gen == -1, "未飽和不該啟用尾端重掃"


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
