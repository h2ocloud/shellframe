#!/usr/bin/env python3
"""啟動信任對話框處理回歸測試（v0.29.49）。

2026-08-28 截圖實證：Claude Code 這版的信任對話框游標**預設停在 No, exit**

    ❯ No, exit
      Yes, I trust this folder

舊的自動接受是「送一個 Enter」——等於自動把新分頁關掉。純手機操作時桌面
不在手邊，這個對話框必須帶回 TG 用按鈕回答。

跑法：.venv/bin/python tests_trust_dialog.py
"""

import importlib.util
import inspect
import os
import threading

import main as _main

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

A = _main.Api

NEW_SCREEN = """Accessing workspace:

/Users/neux

Quick safety check: Is this a project you created or one you trust?

Claude Code'll be able to read, edit, and execute files here.

Security guide

❯ No, exit
  Yes, I trust this folder

Enter to confirm · Esc to cancel"""

LEGACY_SCREEN = """Do you trust the files in this folder?
❯ 1. Yes, I trust this folder
  2. No, exit"""


# ── 1. 游標在 No, exit（現行版本）→ 要往下一格才是 Yes ──
def test_nav_cursor_on_no():
    assert A._trust_dialog_nav(A, NEW_SCREEN) == ("Down", 1)


# ── 2. 游標已經在 Yes（舊版排列）→ 直接 Enter，不要亂移 ──
def test_nav_cursor_on_yes():
    key, steps = A._trust_dialog_nav(A, LEGACY_SCREEN)
    assert steps == 0


# ── 3. 讀不出選項就回 None（呼叫端要「不敢按」，不是盲按 Enter）──
def test_nav_unparsable():
    assert A._trust_dialog_nav(A, "Quick safety check: ...") is None
    assert A._trust_dialog_nav(A, "") is None


# ── 4. 對話框偵測要吃得下現行畫面（沒有編號、No 在前）──
def test_trust_regex_matches_current_dialog():
    assert A._STARTUP_TRUST_RE.search(NEW_SCREEN)


# ── 5. 自動接受不准再盲按 Enter ──
def test_auto_accept_no_blind_enter():
    src = inspect.getsource(A._auto_accept_startup_trust_prompt)
    assert "answer_startup_trust" in src, "沒有走游標感知的作答路徑"
    assert 'send-keys", "-t", s._tmux_name, "Enter"' not in src, "還在盲按 Enter"


# ── 6. 讀不出選項時維持 pending（留給 TG 帶回手機），不亂按 ──
def test_auto_accept_keeps_pending_when_unparsable():
    src = inspect.getsource(A._auto_accept_startup_trust_prompt)
    assert "_startup_trust_pending = True" in src


# ── 7. TG 端：按鈕帶得回去，而且同一個分頁只推一次 ──
def test_offer_buttons_once():
    sent = []
    br = object.__new__(_bt.TelegramBridge)
    br.slots = {}
    br._slot_order = []
    br._slots_lock = threading.Lock()
    br._on_answer_dialog = lambda sid, trust: True
    br.config = type("C", (), {"bot_token": "t"})()
    _bt.tg_api = lambda token, method, payload=None, **kw: (
        sent.append((method, payload)) or {"ok": True})
    br.register_session("s9", "Claude", lambda t: None, cols=101, rows=31)
    assert br._offer_trust_buttons("s9", 1, "Claude", "啟動信任對話框") is True
    assert br._offer_trust_buttons("s9", 1, "Claude") is False, "同一分頁重複洗版"
    kb = sent[0][1]["reply_markup"]["inline_keyboard"][0]
    assert kb[0]["callback_data"] == "trust:s9:yes"
    assert kb[1]["callback_data"] == "trust:s9:no"


# ── 8. 沒有 answer callback（main.py 還沒重啟）→ 不要推假按鈕 ──
def test_no_callback_no_buttons():
    br = object.__new__(_bt.TelegramBridge)
    br.slots = {}
    br._on_answer_dialog = None
    assert br._offer_trust_buttons("s9", 1, "Claude") is False


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
