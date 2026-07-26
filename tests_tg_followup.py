#!/usr/bin/env python3
"""Follow-up 連續訊息回歸測試（v0.29.21）。

修前：TG-wrap 分頁第一則 marker 回覆後就清掉 expect_marker/has_user_msg，
之後 AI 再包的訊息（尤其背景 subagent 完成通知）落進 drain 路徑不轉發 →
「只回一則」。修後：保持 marker 監聽，每個「新」的 [[TG_REPLY]] block 各
轉發一次（去重在 _try_marker_extract：已在 sent_responses 的 block 當「沒新的」）。

跑法：.venv/bin/python tests_tg_followup.py
"""

import importlib.util
import os
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

BR = object.__new__(_bt.TelegramBridge)
START = "[[TG_REPLY_aa11]]"
END = "[[/TG_REPLY_aa11]]"


def _slot(raw, gen=1, sent=None):
    return types.SimpleNamespace(
        expect_marker=True, reply_start_marker=START, reply_end_marker=END,
        pending_raw=raw, peek_fn=None, marker_prompt="",
        marker_next_scan_ts=0.0, marker_scan_gen=-1, _feed_gen=gen,
        sent_responses=set(sent or ()))


# ── 1. 第一個 block → 抽得到 ──
def test_first_block_extracted():
    slot = _slot(START + "第一則回覆" + END)
    got = BR._try_marker_extract(slot, now=1000.0, total=10.0)
    assert got == "第一則回覆", got


# ── 2. 已轉發過的 block（在 sent_responses）→ 當「沒新的」回 '' ──
def test_already_sent_block_skipped():
    slot = _slot(START + "第一則回覆" + END, sent={"第一則回覆"})
    got = BR._try_marker_extract(slot, now=1000.0, total=10.0)
    assert got == "", f"已送過的 block 不該再回：{got!r}"


# ── 3. 出現新的第二個 block（feed_gen 前進）→ 抽得到新的、不重覆第一個 ──
def test_second_block_forwarded():
    # buffer 同時有 block1（已送）與 block2（新）；_pick_marker_reply 回最後一個
    raw = START + "第一則回覆" + END + "\n" + START + "背景 worker 完成：報告已上架" + END
    slot = _slot(raw, gen=2, sent={"第一則回覆"})
    got = BR._try_marker_extract(slot, now=1000.0, total=10.0)
    assert got == "背景 worker 完成：報告已上架", got


# ── 4. 沒有新 block 時的節流：同 gen 不重掃（dirty gate）──
def test_throttle_no_rescan_same_gen():
    slot = _slot(START + "第一則回覆" + END, sent={"第一則回覆"})
    assert BR._try_marker_extract(slot, now=1000.0, total=10.0) == ""
    # 節流已武裝；同 gen、時間到也不重掃
    assert BR._try_marker_extract(slot, now=1004.0, total=14.0) == ""
    assert slot.marker_scan_gen == 1


# ── 5. marker_forwarded 旗標存在於新分頁（follow-up/fallback gating 用）──
def test_slot_has_marker_forwarded_flag():
    import inspect
    src = inspect.getsource(_bt.SessionSlot.__init__)
    assert "self.marker_forwarded" in src, "SessionSlot 缺 marker_forwarded 初始化"


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                import traceback
                print(f"FAIL {name}: {e}")
                traceback.print_exc()
                fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
