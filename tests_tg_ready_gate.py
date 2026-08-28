#!/usr/bin/env python3
"""新分頁第一則訊息的啟動對話框閘門回歸測試（v0.29.48）。

沒有這道閘門時，訊息會直接貼進還停在啟動對話框的 CLI：Ctrl-U ＋整段文字＋
Enter 在選單裡就是「選一個選項」，而 Claude Code 信任對話框第 2 項是
**No, exit** —— 分頁被自己收到的訊息關掉，訊息也一起沒了。
Howard 2026-08-28：手機端 /new 開的分頁，丟進去的任務石沉大海，3 分鐘後
tmux session 直接消失（注入後畫面沒殘留、也沒開回合，正是打進對話框的樣子）。

跑法：.venv/bin/python tests_tg_ready_gate.py
"""

import importlib.util
import inspect
import os
import re
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

SEND_SRC = inspect.getsource(_bt.TelegramBridge._handle_update)


def _bridge(blocked_fn):
    br = object.__new__(_bt.TelegramBridge)
    br._on_input_blocked = blocked_fn
    return br


def _slot():
    return _bt.SessionSlot("s9", "Claude", lambda t: None, 1, cmd="claude")


# ── 1. 沒有 callback（舊 main.py 還沒重啟）→ 一律放行，不能擋住訊息 ──
def test_no_callback_passes():
    assert _bridge(None)._wait_input_safe(_slot()) == ""


# ── 2. 畫面正常 → 立刻放行（正常分頁不能被拖慢）──
def test_safe_screen_passes_fast():
    t0 = time.time()
    assert _bridge(lambda sid: "")._wait_input_safe(_slot()) == ""
    assert time.time() - t0 < 1.0


# ── 3. 一直卡在對話框 → timeout 後擋下並說原因 ──
def test_dialog_blocks():
    calls = []
    br = _bridge(lambda sid: calls.append(sid) or "啟動信任對話框")
    assert br._wait_input_safe(_slot(), timeout=2.2) == "啟動信任對話框"
    assert len(calls) >= 2, "應該持續輪詢（對話框可能自己被點掉）"


# ── 4. 對話框中途消失 → 放行 ──
def test_dialog_clears_then_passes():
    seen = {"n": 0}
    def probe(sid):
        seen["n"] += 1
        return "啟動信任對話框" if seen["n"] < 2 else ""
    assert _bridge(probe)._wait_input_safe(_slot(), timeout=5.0) == ""


# ── 5. callback 自己爆炸 → fail open（寧可送出，也不要全分頁啞掉）──
def test_callback_error_fails_open():
    def boom(sid):
        raise RuntimeError("tmux gone")
    assert _bridge(boom)._wait_input_safe(_slot()) == ""


# ── 5. 新 slot 預設「未確認」，確認過才記住 ──
def test_slot_flag_default():
    assert _slot().ready_confirmed is False


# ── 6. 閘門真的接在注入路徑上，而且只擋第一次 ──
def test_gate_wired_into_send():
    assert "_wait_input_safe" in SEND_SRC, "注入路徑沒有 ready gate"
    assert re.search(r"not slot\.ready_confirmed", SEND_SRC), \
        "閘門沒有用 ready_confirmed 只擋第一次"
    assert re.search(r"slot\.ready_confirmed = True", SEND_SRC), \
        "確認就緒後沒有記起來（每則訊息都會再 capture-pane）"
    # 擋下時必須是「不注入 + 通知」，不能默默吞掉
    gate = SEND_SRC.split("_wait_input_safe", 1)[1][:2000]
    assert "return False" in gate, "擋下後仍會往下注入"
    assert "sendMessage" in gate, "擋下沒有通知使用者"


# ── 7. 只對 AI 分頁生效（shell 分頁沒有「就緒」概念，不能擋）──
def test_gate_only_for_ai_tabs():
    gate_line = re.search(r"not slot\.ready_confirmed[\s\S]{0,200}", SEND_SRC).group(0)
    assert "_detect_ai" in gate_line, "shell 分頁也被 ready gate 擋住了"


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
