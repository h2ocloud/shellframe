"""TG 注入送達驗證（_verify_injection）回歸測試。

兩個案例來自 v0.23.0 對抗性審查抓到的 CONFIRMED bug：
  1. extraction loop 會把 slot.last_write_ts 歸零 → 舊版跟 last_write_ts 比
     會被「前一輪的 extraction」假 delivered。現在跟 injected_at 快照比。
  2. shell 分頁會 echo 輸入 → 殘留判定誤觸重複注入。現在 verify/retry 只對
     AI 分頁（detect_ai(slot.cmd)）啟用——該 gate 在 _send() 內，此處測
     verify 本身的判定正確性。

跑法：
    .venv/bin/python tests_tg_inject.py
    .venv/bin/python -m pytest tests_tg_inject.py
"""

import importlib.util
import os
import sys
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

BR = object.__new__(_bt.TelegramBridge)
PAYLOAD = "[SF-TG wrapper]\n最終要回 Telegram 的文字…\n\nhoward: 幫我查一下部署狀態好嗎"


def _slot(screen, extraction_ts=0.0, write_ts=100.0):
    return types.SimpleNamespace(peek_fn=lambda: screen,
                                 last_extraction_ts=extraction_ts,
                                 last_write_ts=write_ts)


# ── 1. turn 開始 footer → delivered ──
def test_turn_start_is_delivered():
    got = BR._verify_injection(_slot("...thinking (esc to interrupt)"), PAYLOAD,
                               injected_at=100.0, window=0.6)
    assert got == (True, False), got


# ── 2. 注入「之後」的 extraction → delivered ──
def test_fresh_extraction_is_delivered():
    got = BR._verify_injection(_slot("whatever", extraction_ts=200.0), PAYLOAD,
                               injected_at=150.0, window=0.6)
    assert got == (True, False), got


# ── 3. 審查 bug #1：注入「之前」的舊 extraction（且 last_write_ts 被
#      extraction loop 歸零）不得假 delivered；殘留在畫面 → 可重試 ──
def test_stale_extraction_not_delivered():
    slot = _slot("\x1b[38;5;242m> \x1b[0mhoward: 幫我查一下部署狀態好嗎",
                 extraction_ts=100.0, write_ts=0.0)   # write_ts 已被歸零
    got = BR._verify_injection(slot, PAYLOAD, injected_at=150.0, window=0.6)
    assert got == (False, True), f"舊 extraction 假 delivered 回歸：{got}"


# ── 4. 殘留判定：payload 最後一行掛在畫面（含 ANSI）→ (False, True) ──
def test_composer_residue_detected():
    slot = _slot("\x1b[38;5;242m> \x1b[0mhoward: 幫我查一下部署狀態好嗎\x1b[K\n ▶▶ bypass")
    got = BR._verify_injection(slot, PAYLOAD, injected_at=100.0, window=0.6)
    assert got == (False, True), got


# ── 5. 不確定（畫面無殘留、無訊號）→ (False, False)：不重試不吵 ──
def test_ambiguous_no_retry():
    got = BR._verify_injection(_slot("完全無關的畫面內容"), PAYLOAD,
                               injected_at=100.0, window=0.6)
    assert got == (False, False), got


# ── 6. 審查 bug #2 的 gate 素材：detect_ai 分流正確（gate 本身在 _send 閉包內）──
def test_detect_ai_gate_material():
    assert _bt._detect_ai("claude --permission-mode x") == "claude"
    assert _bt._detect_ai("/usr/local/bin/sf-codex") == "codex"
    assert _bt._detect_ai("/bin/zsh -l") is None
    assert _bt._detect_ai("") is None


if __name__ == "__main__":
    import traceback
    fails = 0
    for name in sorted(list(globals())):
        if name.startswith("test_") and callable(globals()[name]):
            try:
                globals()[name]()
                print(f"PASS  {name}")
            except Exception:
                fails += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
