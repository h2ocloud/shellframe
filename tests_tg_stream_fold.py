#!/usr/bin/env python3
"""串流中間狀態摺疊回歸（v0.29.53）。

2026-08-31 s88「日常」實案：TG 收到的訊息裡同一段話反覆出現、逐步變長——
    ▎ 在等待期間，我們的案件在 NVC 是否仍維
    ▎ 在等待期間，我們的案件在 NVC 是否仍維持「文件齊備」…的狀態？
TUI 逐步畫出長回覆時，每次重繪都在原始 PTY 串流留下一份「畫到一半」的版本，
而它們全落在 marker span 內 → 當成正文送出。逐行去重擋不住（每行都不同）。

修法：丟掉「是後面某行嚴格前綴」的行，只留最完整版。
**但不可變成靜默刪內容**——清單裡 `- todo` 與 `- todo list 要整理` 是兩個
合法項目，所以加 12 字長度門檻。

跑法：.venv/bin/python tests_tg_stream_fold.py
"""

import importlib.util
import os
import time

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

REAL = """▎ 在等待期間，我們的案件在 NVC 是否仍維
▎ 文件效期。我們的體檢是在七月底完成的。
▎ 在等待期間，我們的案件在 NVC 是否仍維持「文件齊備」的狀態？
▎ 文件效期。我們的體檢是在七月底完成的。Yu Ching 的良民證於 8 月 8 日製發
▎ 文件效期。我們的體檢是在七月底完成的。Yu Ching 的良民證於 8 月 8 日製發，正本已在手上。哪些會過期？"""


# ── 1. 核心：串流中間狀態摺疊成最完整版 ──
def test_streaming_states_folded():
    out = _bt.clean_mobile_marker_response(REAL).splitlines()
    assert len(out) == 2, f"應摺疊成 2 行，實得 {len(out)}：{out}"
    assert any("哪些會過期" in l for l in out), "最完整版被丟掉了"
    assert any("文件齊備" in l for l in out), "最完整版被丟掉了"
    assert not any(l.strip().endswith("是否仍維") for l in out), "半截版仍在"


# ── 2. 誤刪防護：短行的前綴關係在正常內容很常見，不得摺疊 ──
def test_short_prefix_not_folded():
    src = "1. 買牛奶\n2. 買牛奶和麵包\n- todo\n- todo list 要整理"
    out = _bt.clean_mobile_marker_response(src)
    for item in ("1. 買牛奶", "- todo"):
        assert item in out.splitlines(), f"合法清單項被誤刪：{item}"


# ── 3. 完全不相關的內容一行都不能少 ──
def test_unrelated_lines_all_kept():
    src = "\n".join(f"第 {i} 段完全不同的內容說明文字。" for i in range(20))
    assert len(_bt.clean_mobile_marker_response(src).splitlines()) == 20


# ── 4. 效能：長回覆不得退化成 O(n²)（窗口限制）──
def test_perf_long_reply():
    big = "\n".join(f"第 {i} 段內容，這是一段中等長度的中文說明文字。" for i in range(800))
    t0 = time.perf_counter()
    _bt.clean_mobile_marker_response(big)
    dt = (time.perf_counter() - t0) * 1000
    assert dt < 60, f"800 行花了 {dt:.0f}ms，窗口限制可能失效"


# ── 5. 門檻與窗口是具名常數（別被改成魔術數字）──
def test_named_constants():
    assert _bt._STREAM_FOLD_MIN_LEN >= 8
    assert _bt._STREAM_FOLD_WINDOW >= 10


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
