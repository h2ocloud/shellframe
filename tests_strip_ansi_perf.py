#!/usr/bin/env python3
"""strip_ansi O(N²) 災難性回溯回歸測試（v0.29.7 修「TG 收不到」事故鏈）。

2026-07-07 事故：filters.json 的 status pattern `.*\\d+%\\s*left.*`（`.*` 開頭
無 ^ 錨點）在長無空白行（base64/JWT/minified 輸出）上 O(N²)——一行 40KB
base64 單次 .sub 要 9 秒。它跑在 marker 掃描（flush loop 熱路徑、握著
output_lock），整條 TG 回覆鏈卡死數十秒到數分鐘，體感=訊息沒收到。
修法：_build_regex 對 `.*` 開頭的 status pattern 一律自動補 `^`（MULTILINE
下引擎只在行首嘗試 → 線性）；loading regex `\\w{2,}` 加上界 40。

跑法：
    .venv/bin/python tests_strip_ansi_perf.py
"""

import base64
import importlib.util
import os
import time

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)


def _pathological_buffer():
    """~60KB，含一行 40KB base64（大量大寫/數字的超長 \\w run）。"""
    blob = base64.b64encode(os.urandom(30000)).decode()
    return ("正常輸出行\n" * 50) + blob + "\n" + ("more text Thinking… \n" * 50) + blob[:20000]


# ── 1. 病態 buffer 必須線性時間完成（修前 9.2s，修後 ~20ms；上限給寬到 2s）──
def test_no_quadratic_blowup_on_long_tokens():
    raw = _pathological_buffer()
    t0 = time.perf_counter()
    _bt.strip_ansi(raw, sent_texts=[])
    dt = time.perf_counter() - t0
    assert dt < 2.0, f"strip_ansi 花了 {dt:.1f}s——O(N²) 回溯回歸（修前 9.2s）"


# ── 2. `.*` 開頭的 status pattern 被自動錨定（filters 可能來自遠端/使用者）──
def test_status_patterns_auto_anchored():
    c = _bt._build_regex()
    if c["status"] is None:
        return
    for branch in c["status"].pattern.split("|"):
        assert not branch.startswith(".*"), \
            f"未錨定的 .* 開頭 status pattern 洩漏進編譯結果：{branch!r}"


# ── 3. 行為不變：狀態列照樣被濾掉、正文保留 ──
def test_status_bar_still_filtered():
    sample = "回覆內容\nSonnet 4.6 · Claude Max · 12% left\n其他行"
    out = _bt.strip_ansi(sample, sent_texts=[])
    assert "12% left" not in out and "回覆內容" in out and "其他行" in out, repr(out)


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
