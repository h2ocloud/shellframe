"""TG /model 互動選單回歸測試（v0.29.3）。

picker 測資為 CC 2.1.199 實機截取；互動模型實測結論：**picker 按數字＝
立即選定並存為新 session 預設（免 Enter）**，故 mchoice 只送數字。

跑法：
    .venv/bin/python tests_tg_model_menu.py
    .venv/bin/python -m pytest tests_tg_model_menu.py
"""

import importlib.util
import os
import sys
import threading
import time
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

# 測試不得寫進 production 的 bridge log（使用者靠那份 log 除錯）
_bt._blog = lambda msg: None

# CC 2.1.199 /model picker 實機畫面
FRAME = """❯ /model
────────────────────────────────────────────────────────────────
  Select model
  Switch between Claude models. Your pick becomes the default for new sessions. For other/previous
  model names, specify with --model.
    1. Default (recommended)  Opus 4.8 with 1M context · Best for everyday, complex tasks
    2. Opus                   Opus 4.8 with 1M context · Best for everyday, complex tasks
  ❯ 3. Fable ✔                Fable 5 · Most capable for your hardest and longest-running tasks
    4. Sonnet                 Sonnet 5 · Efficient for routine tasks
    5. Haiku                  Haiku 4.5 · Fastest for quick answers
  ◉ xHigh effort ←/→ to adjust
  Enter to set as default · s to use this session only · Esc to cancel""".split("\n")


def _bridge(frame=None, writes=None, sent=None):
    br = object.__new__(_bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="TEST", allowed_users=[])
    br._user_active = {111: "s99"}
    br._user_chat = {111: 222}
    br._default_active_sid = ""
    slot = types.SimpleNamespace(
        sid="s99", label="測試",
        write_fn=lambda d: (writes.append(d) if writes is not None else None),
        write_lock=threading.Lock(), pending_menu=False, pending_menu_options=[],
        awaiting_response=False, last_write_ts=0.0, stall_warned=False,
        screen=object(), _feed_gen=1,
        _display_cache=frame or FRAME, _display_cache_gen=1)
    br.slots = {"s99": slot}
    br.get_active_sid = lambda uid: "s99"
    _bt.tg_api = lambda tok, m, p: (sent.append((m, p)) if sent is not None else None) or {}
    return br, slot


def _cq(data):
    return {"id": "1", "data": data,
            "message": {"message_id": 9, "chat": {"id": 222}}, "from": {"id": 111}}


def test_parse_real_picker_frame():
    br, _ = _bridge()
    menu = br._parse_model_menu(FRAME)
    assert menu
    opts, effort = menu
    assert len(opts) == 5
    assert opts[2]["current"] and opts[2]["label"] == "Fable"
    assert opts[0]["label"] == "Default (recommended)"
    assert "effort" in effort.lower()


def test_generic_menu_detect_survives_effort_chrome():
    """◉ effort chrome 行曾把已收集選項 reset 掉——/model 選單偵測不到的根因。"""
    br, slot = _bridge()
    det = br._detect_menu_prompt(slot)
    assert det and "1." in det and "5." in det, det


def test_model_command_full_flow():
    writes, sent = [], []
    br, slot = _bridge(writes=writes, sent=sent)
    br._handle_model_command(111, 222)
    # 等「含鍵盤的那則」——前面測試的 _confirm daemon thread 可能先往
    # （被 re-patch 的）global tg_api 塞 editMessageText，不能等到有訊息就停。
    menus = []
    for _ in range(40):
        menus = [p for m, p in sent if isinstance(p, dict) and "reply_markup" in p]
        if menus:
            break
        time.sleep(0.2)
    assert menus, sent
    payload = menus[-1]
    kb = payload["reply_markup"]["inline_keyboard"]
    assert len(kb) == 6                       # 5 選項 + 取消
    assert kb[2][0]["callback_data"] == "mchoice:s99:3" and "✔" in kb[2][0]["text"]
    assert kb[-1][0]["callback_data"] == "mcancel:s99"
    assert writes[:3] == ["\x15", "/model", "\r"]


def test_busy_guard_blocks():
    writes, sent = [], []
    br, slot = _bridge(frame=["回覆生成中…", "(esc to interrupt)"],
                       writes=writes, sent=sent)
    br._handle_model_command(111, 222)
    for _ in range(20):
        if sent:
            break
        time.sleep(0.2)
    assert sent and "正在跑回合" in sent[0][1]["text"]
    assert writes == []


def test_mchoice_writes_digit_only():
    writes, sent = [], []
    br, slot = _bridge(writes=writes, sent=sent)
    slot.pending_menu = True
    # 畫面直接放確認行，讓 _confirm thread 立刻收斂（免等 6s fallback）
    slot._display_cache = ["⎿  Set model to Sonnet 5 and saved as your default for new sessions"]
    br._handle_callback_query(_cq("mchoice:s99:4"))
    assert writes == ["4"], writes            # 免 \r：數字即選定（2026-07-07 實機再驗證）
    assert slot.pending_menu is False
    assert any("已選 4" in p.get("text", "") for m, p in sent if m == "editMessageText")


def test_mchoice_confirms_from_screen():
    """v0.29.9：確認行「⎿ Set model to …」被 _extract_new_text 過濾（⎿ 開頭），
    永遠不會經 flush loop 回 TG——必須主動掃 live screen 回報，否則 TG 停在
    「等確認」讓使用者誤判失敗（實際上模型已切換）。"""
    writes, sent = [], []
    br, slot = _bridge(writes=writes, sent=sent)
    slot.pending_menu = True
    slot._display_cache = ["⎿  Set model to Sonnet 5 and saved as your default for new sessions"]
    br._handle_callback_query(_cq("mchoice:s99:4"))
    deadline = time.time() + 3.0
    ok = lambda: any("模型已切換：Sonnet 5" in p.get("text", "")
                     for m, p in sent if m == "editMessageText")
    while time.time() < deadline and not ok():
        time.sleep(0.1)
    assert ok(), sent


def test_mcancel_sends_esc():
    writes, sent = [], []
    br, slot = _bridge(writes=writes, sent=sent)
    slot.pending_menu = True
    br._handle_callback_query(_cq("mcancel:s99"))
    assert writes == ["\x1b"]
    assert any("已取消" in p.get("text", "") for m, p in sent if m == "editMessageText")


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
