#!/usr/bin/env python3
"""marker 掃描效能優化回歸（v0.29.37）。

實測背景：pending_raw 撐到 120KB 上限時，`strip_ansi` 單次 **31ms**；而
production log 顯示 **81% 的 marker 掃描是 raw=False**（marker 已被截斷擠出
buffer）＝ 31ms 註定白花。兩個修法：
1. 截斷時保住 start marker → span 還能配對（同時修「愛回不回」）。
2. 便宜預檢（str.find，0.016ms）→ 沒有 marker 痕跡就不跑昂貴掃描；但**不直接
   放棄**，改用較長節流，避免 ANSI 打斷 marker 時製造新的靜默丟訊。

跑法：.venv/bin/python tests_tg_marker_perf.py
"""

import importlib.util
import os
import time
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

BR = object.__new__(_bt.TelegramBridge)
BR._perf_enabled = False
START = "[[TG_REPLY_ab12cd34]]"
END = "[[/TG_REPLY_ab12cd34]]"
NOISE = "\x1b[38;5;12m⏺ 分析中… 中文輸出 abc\x1b[0m\x1b[2K\r\n"


def _slot(raw, gen=1):
    return types.SimpleNamespace(
        sid="s1", msg_sent_ts=time.time(), expect_marker=True,
        reply_start_marker=START, reply_end_marker=END,
        pending_raw=raw, peek_fn=None, marker_prompt="",
        marker_next_scan_ts=0.0, marker_scan_gen=-1, _feed_gen=gen,
        sent_responses=set())


# ── 1. 預檢：marker 不在 120KB buffer → 便宜返回且套用長節流 ──
def test_precheck_skips_expensive_scan():
    slot = _slot((NOISE * 4000)[:120000])
    t0 = time.perf_counter()
    got = BR._try_marker_extract(slot, now=1000.0, total=10.0)
    dt = (time.perf_counter() - t0) * 1000
    assert got == ""
    # 全量 strip_ansi 實測 ~31ms；預檢路徑必須遠低於它
    assert dt < 12.0, f"預檢沒生效，仍跑了昂貴掃描：{dt:.1f}ms"
    assert slot.marker_next_scan_ts == 1000.0 + BR._MARKER_PROBE_BACKOFF


# ── 2. 預檢不得造成靜默丟訊：marker 真的在 → 照樣抽得到 ──
def test_precheck_does_not_break_real_extraction():
    slot = _slot((NOISE * 3000)[:90000] + START + "真正的回覆" + END)
    got = BR._try_marker_extract(slot, now=1000.0, total=10.0)
    assert got == "真正的回覆", got


# ── 3. marker 只出現在尾端、且被 ANSI 夾著 → 第二層(尾端 strip)要救回來 ──
def test_precheck_second_tier_catches_ansi_wrapped():
    raw = (NOISE * 3000)[:90000] + "\x1b[0m" + START + "夾在 ANSI 裡的回覆" + END
    got = BR._try_marker_extract(_slot(raw), now=1000.0, total=10.0)
    assert got == "夾在 ANSI 裡的回覆", got


# ── 4. 截斷保 marker：超過上限時 start marker 不得被擠掉 ──
def test_truncate_preserves_start_marker():
    br = object.__new__(_bt.TelegramBridge)
    br.active = True
    br._flush_wake = types.SimpleNamespace(set=lambda: None)
    slot = _slot("")
    slot.output_lock = __import__("threading").Lock()
    slot.last_output_time = 0
    slot.first_output_time = 0
    slot.last_chunk_ts = 0
    slot.scan_dirty = False
    slot.awaiting_response = False
    slot._display_cache = None
    slot._display_cache_gen = -1
    slot.stream = types.SimpleNamespace(feed=lambda x: None)
    br.slots = {"s1": slot}
    # 先餵 marker，再餵超過上限的內容把它擠出去
    br.feed_output("s1", START + "開頭\n")
    br.feed_output("s1", NOISE * 5000)
    assert len(slot.pending_raw) <= br._PENDING_RAW_MAX + len(START) + 1
    assert START in slot.pending_raw, \
        "start marker 被截斷擠掉 → span 永遠配不出來（愛回不回 + 每次白掃 31ms）"


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
