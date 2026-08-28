#!/usr/bin/env python3
"""分頁消失時的靜默改指回歸測試（v0.29.47）。

分頁死掉（CLI 退出／被關）時 `_remove_slots_locked` 會把使用者的 active
指到第一格——舊版**完全不講**，手機端只有 👀／🫡，下一則工作指令就悄悄
落進別的分頁。實例 2026-08-28：新分頁 3 分鐘後死掉，接著丟的「台壽展場案
API 規格」任務跑進「雜事」，Howard 是 /10 打不開才發現。

同時釘住 /N 打錯時要回「現在的編號表」而不是死巷子的英文錯誤。

跑法：.venv/bin/python tests_tg_slot_gone.py
"""

import importlib.util
import os
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

UID, CHAT = 42, 4200


def _bridge(sent):
    br = object.__new__(_bt.TelegramBridge)
    br.slots = {}
    br._slot_order = []
    br._slots_lock = threading.Lock()
    br._user_active = {}
    br._user_chat = {UID: CHAT}
    br._default_active_sid = ""
    br._last_prune_ts = time.time() + 3600      # 別讓測試去打 sfctl
    br.paused = False
    br.config = type("C", (), {"bot_token": "t"})()
    _bt.tg_api = lambda token, method, payload=None, **kw: (
        sent.append((method, payload)) or {"ok": True})
    return br


def _fill(br, *labels):
    for i, label in enumerate(labels, 1):
        br.register_session(f"s{i}", label, lambda t: None, cols=101, rows=31)


# ── 1. 使用者手上的分頁死掉 → 一定要收到「改送到哪」的通知 ──
def test_active_slot_gone_notifies():
    sent = []
    br = _bridge(sent)
    _fill(br, "HR", "雜事", "小N開發")
    br._user_active[UID] = "s3"
    with br._slots_lock:
        br._remove_slots_locked(["s3"])
    for _ in range(50):                       # 通知走背景 thread
        if sent:
            break
        time.sleep(0.02)
    assert sent, "分頁消失沒有通知使用者（靜默改指）"
    text = sent[-1][1]["text"]
    assert "小N開發" in text, text            # 死掉的是哪一個
    assert "HR" in text and "/1" in text      # 之後會送去哪
    assert br._user_active[UID] == "s1"


# ── 2. 沒被影響的使用者不該收到噪音 ──
def test_untouched_active_no_notice():
    sent = []
    br = _bridge(sent)
    _fill(br, "HR", "雜事", "小N開發")
    br._user_active[UID] = "s1"
    with br._slots_lock:
        br._remove_slots_locked(["s3"])
    time.sleep(0.15)
    assert not sent, f"不相干的分頁關掉也發通知：{sent}"


# ── 3. 全部分頁都沒了也要講，不能靜默 ──
def test_last_slot_gone_notifies():
    sent = []
    br = _bridge(sent)
    _fill(br, "HR")
    br._user_active[UID] = "s1"
    with br._slots_lock:
        br._remove_slots_locked(["s1"])
    for _ in range(50):
        if sent:
            break
        time.sleep(0.02)
    assert sent and "HR" in sent[-1][1]["text"]
    assert UID not in br._user_active


# ── 4. 編號表：現在的編號 + 誰是 active ──
def test_slot_menu_text():
    br = _bridge([])
    _fill(br, "HR", "雜事")
    br._user_active[UID] = "s2"
    menu = br._slot_menu_text(UID)
    assert "/1  HR" in menu and "/2  雜事" in menu
    assert menu.count("◀") == 1


# ── 5. /N 超出範圍 → 附上編號表，不是死巷子錯誤 ──
def test_invalid_index_returns_menu():
    sent = []
    br = _bridge(sent)
    _fill(br, "HR", "雜事")
    br._peek_last_response = lambda slot: ""
    br._handle_command("10", UID, CHAT)
    text = sent[-1][1]["text"]
    assert "Invalid session number" not in text
    assert "/10" in text and "2 個分頁" in text
    assert "/1  HR" in text and "/2  雜事" in text


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
