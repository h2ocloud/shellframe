#!/usr/bin/env python3
"""_flush_loop marker 掃描節流回歸測試（v0.29.5 修 96% CPU）。

2026-07-06 事故：expect_marker 分支在 total ≥ 120s 後（idle<3 閘門被 total
分支繞過）每 0.5s tick 對 ≤120KB pending_raw 跑兩次 strip_ansi（~45ms/次，
實測 18% CPU/slot；三個 stuck slot + 主線程 pyte feed = 96%）。修法：
_try_marker_extract 單次掃描 + 節流（_MARKER_RESCAN_INTERVAL）+ dirty gate
（_feed_gen 沒前進就不重掃）。

跑法：
    .venv/bin/python tests_tg_marker_throttle.py
    .venv/bin/python -m pytest tests_tg_marker_throttle.py
"""

import importlib.util
import os
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

BR = object.__new__(_bt.TelegramBridge)
START = "[[TG_REPLY_ab12cd34]]"
END = "[[/TG_REPLY_ab12cd34]]"


def _slot(raw, gen=1):
    return types.SimpleNamespace(
        expect_marker=True,
        reply_start_marker=START,
        reply_end_marker=END,
        pending_raw=raw,
        peek_fn=None,
        marker_prompt="",
        marker_next_scan_ts=0.0,
        marker_scan_gen=-1,
        _feed_gen=gen,
    )


class _ScanCounter:
    """包住真正的 _pick_marker_reply，數它被叫幾次（= 幾次全量掃描）。"""

    def __init__(self):
        self.count = 0
        self._real = _bt.TelegramBridge._pick_marker_reply

    def __enter__(self):
        counter = self

        def counted(self_br, slot, allow_inprogress):
            counter.count += 1
            return counter._real(self_br, slot, allow_inprogress)

        _bt.TelegramBridge._pick_marker_reply = counted
        return self

    def __exit__(self, *a):
        _bt.TelegramBridge._pick_marker_reply = self._real


# ── 1. 失敗掃描後：同 gen（沒有新 PTY bytes）不得重掃，即使時間到 ──
def test_dirty_gate_blocks_rescan_without_new_bytes():
    slot = _slot("no marker here at all")
    with _ScanCounter() as sc:
        assert BR._try_marker_extract(slot, now=1000.0, total=150.0) == ""
        assert sc.count == 1
        # 0.5s 後（模擬下一 tick）——節流中
        assert BR._try_marker_extract(slot, now=1000.5, total=150.5) == ""
        assert sc.count == 1, "per-tick 重掃回歸（節流失效）"
        # 3s 後但 gen 未變——dirty gate 擋下
        assert BR._try_marker_extract(slot, now=1004.0, total=154.0) == ""
        assert sc.count == 1, "gen 未變仍重掃（dirty gate 失效）"


# ── 2. 時間到 + 有新 bytes → 允許重掃 ──
def test_rescan_after_interval_and_new_bytes():
    slot = _slot("no marker here")
    with _ScanCounter() as sc:
        BR._try_marker_extract(slot, now=1000.0, total=150.0)
        slot._feed_gen = 2          # 新 PTY chunk
        # 時間未到——仍節流
        assert BR._try_marker_extract(slot, now=1001.0, total=151.0) == ""
        assert sc.count == 1
        # 時間到 + gen 前進 → 重掃
        BR._try_marker_extract(slot, now=1004.0, total=154.0)
        assert sc.count == 2


# ── 3. 串流守衛保留：未閉合 start → total<30 等待、total>=30 取最後完整 ──
def test_streaming_guard_preserved():
    raw = START + "舊的完整回應" + END + "\n" + START + "新的還在打字中…"
    assert BR._try_marker_extract(_slot(raw), now=1000.0, total=10.0) == ""
    got = BR._try_marker_extract(_slot(raw), now=1000.0, total=40.0)
    assert got == "舊的完整回應", got


# ── 4. 抽取成功 → 節流狀態重置（下一輪注入不被舊狀態卡住）──
def test_success_resets_throttle_state():
    slot = _slot(START + "完整回覆" + END)
    got = BR._try_marker_extract(slot, now=1000.0, total=10.0)
    assert got == "完整回覆", got
    assert slot.marker_next_scan_ts == 0.0
    assert slot.marker_scan_gen == -1


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
