#!/usr/bin/env python3
"""「自動派工」開關接線回歸測試（v0.29.10）。

修前：`auto_delegate_enabled` 沒有任何後端 consumer（死開關），而派工協調
指令藏在 DEFAULT_TG_PROMPT 的「Default coordination」段落，每回合照灌——
Howard:「自動派工關掉 shellframe 還是會派工」。修後：TG prompt 協調段落與
master per-turn preamble（含自訂文字）都受開關管。

跑法：
    .venv/bin/python tests_auto_delegate_gate.py
"""

import importlib.util
import os
import time

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)


def _with_settings(d):
    """把假 settings 灌進模組層 cache（TTL 內 _read_settings 直接回它）。"""
    _bt._SETTINGS_CACHE["ts"] = time.monotonic()
    _bt._SETTINGS_CACHE["val"] = d


# ── 1. 開關關（預設）→ TG prompt 不推派工、明講僅在使用者要求時派工 ──
def test_tg_prompt_manual_when_off():
    for settings in ({}, {"auto_delegate_enabled": False}):
        _with_settings(settings)
        p = _bt.get_tg_prompt()
        assert "prefer `sfctl delegate" not in p, p
        assert "Auto-delegation is OFF" in p or "auto-delegation is OFF" in p, p
        assert "[TG] Replying to Telegram mobile" in p  # 基底段落保留


# ── 2. 開關開 → 協調段落照舊 ──
def test_tg_prompt_delegates_when_on():
    _with_settings({"auto_delegate_enabled": True})
    p = _bt.get_tg_prompt()
    assert "prefer `sfctl delegate" in p, p


# ── 3. master preamble：關 → 中性版（保留 grounding），連自訂文字也不放行 ──
def test_master_preamble_manual_when_off():
    _with_settings({"auto_delegate_enabled": False,
                    "master_turn_preamble": "Prefer delegation over everything."})
    p = _bt.get_master_turn_preamble()
    assert "Prefer delegation" not in p, p
    assert "only when the user explicitly asks" in p, p
    assert "do NOT fabricate" in p  # grounding 規則保留


# ── 4. master preamble：開 → 自訂文字優先、無自訂用內建 ──
def test_master_preamble_custom_when_on():
    _with_settings({"auto_delegate_enabled": True,
                    "master_turn_preamble": "MY CUSTOM PROTOCOL"})
    assert _bt.get_master_turn_preamble() == "MY CUSTOM PROTOCOL"
    _with_settings({"auto_delegate_enabled": True})
    assert "sfctl delegate" in _bt.get_master_turn_preamble()


# ── 5. 自訂 tg_prompt 原文照用（不做手術），不受開關影響 ──
def test_custom_tg_prompt_untouched():
    _with_settings({"auto_delegate_enabled": False, "tg_prompt": "CUSTOM TG"})
    assert _bt.get_tg_prompt() == "CUSTOM TG"


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
