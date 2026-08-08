#!/usr/bin/env python3
"""語音 Apply-gate 開關回歸測試（v0.29.26）。

修前：語音轉錄後一律跳「✅ Apply」按鈕（寫死、沒有設定）。修後：新增
`settings.voice_apply_gate`（預設 True＝維持確認），關閉時轉錄完直接自動
送出。本測試涵蓋 `voice_apply_gate()` helper 對設定的讀取。

跑法：.venv/bin/python tests_voice_gate.py
"""

import importlib.util
import os
import time

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)


def _with(settings):
    _bt._SETTINGS_CACHE["ts"] = time.monotonic()
    _bt._SETTINGS_CACHE["val"] = settings


# ── 1. 預設（未設定）→ 開（維持既有 Apply 行為）──
def test_default_on():
    _with({})
    assert _bt.voice_apply_gate() is True


# ── 2. 明確 True → 開 ──
def test_explicit_true():
    _with({"voice_apply_gate": True})
    assert _bt.voice_apply_gate() is True


# ── 3. False → 關（自動送出）──
def test_off():
    _with({"voice_apply_gate": False})
    assert _bt.voice_apply_gate() is False


# ── 4. 關閉時 _handle_update 走 auto-submit 分支（原始碼含 text = fwd_text）──
def test_autosubmit_path_present():
    import inspect
    src = inspect.getsource(_bt.TelegramBridge._handle_update)
    assert "voice_apply_gate()" in src, "voice 區塊未接上開關"
    assert "voice auto-submit" in src, "缺 gate-off 自動送出分支"


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
