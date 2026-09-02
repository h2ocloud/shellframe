"""TG 指令選單／list 帶模型徽章（v0.29.5）回歸測試。

回報 2026-07-06：TG 也要像側邊欄顯示每分頁的模型＋思考深度。
model 資訊來自 main.py 的 get_session_model_info（callback on_model_info）。

跑法：
    .venv/bin/python tests_tg_model_badge.py
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


# _slot_model_suffix 會讀 settings 的 show_model_badge（跟側邊欄同一個開關）。
# 測試不能吃到這台機器的真實 config——Howard 本地就是關的，那會讓所有「該顯示
# 模型」的測項全部假性失敗。預設 stub 成開啟，要驗開關本身的測項自己覆蓋。
_bt._read_settings = lambda: {"show_model_badge": True}


def _bridge(model_map):
    br = object.__new__(_bt.TelegramBridge)
    br._on_model_info = lambda sid: model_map.get(sid)
    return br


def _slot(sid, index, label):
    return types.SimpleNamespace(sid=sid, index=index, label=label)


def test_suffix_follows_local_badge_toggle():
    """側邊欄的模型徽章關掉時，TG 這邊也要一起消失。同一個開關管兩邊——
    Howard 2026-09-02：他本地早就關了，TG 卻還在顯示一個不準的值。"""
    br = _bridge({"s1": {"name": "Opus 5", "effort": "xhigh", "provider": "claude"}})
    slot = _slot("s1", 1, "SF")
    orig = _bt._read_settings
    try:
        _bt._read_settings = lambda: {"show_model_badge": False}
        assert br._slot_model_suffix(slot) == ""
        _bt._read_settings = lambda: {"show_model_badge": True}
        assert br._slot_model_suffix(slot) == " · Opus 5 · xhigh"
        _bt._read_settings = lambda: {}          # 沒設過 → 預設顯示
        assert br._slot_model_suffix(slot) == " · Opus 5 · xhigh"
    finally:
        _bt._read_settings = orig


def test_suffix_with_effort():
    br = _bridge({"s1": {"name": "Opus 4.8", "effort": "xhigh", "provider": "claude"}})
    assert br._slot_model_suffix(_slot("s1", 1, "SF")) == " · Opus 4.8 · xhigh"


def test_suffix_without_effort():
    br = _bridge({"s1": {"name": "GPT-5.5", "effort": "", "provider": "codex"}})
    assert br._slot_model_suffix(_slot("s1", 1, "cdx")) == " · GPT-5.5"


def test_suffix_none_is_empty():
    br = _bridge({})                       # 未知/非 AI → 空字串
    assert br._slot_model_suffix(_slot("nope", 9, "x")) == ""


def test_suffix_no_callback_is_empty():
    br = object.__new__(_bt.TelegramBridge)
    br._on_model_info = None
    assert br._slot_model_suffix(_slot("s1", 1, "x")) == ""


def test_command_description_has_no_model():
    """選單只放分頁名。setMyCommands 是註冊當下的快照，模型掛上去只會腐爛——
    分頁久沒動就凍在幾天前那次對話的模型，手機端看不出它已經過期
    （Howard 2026-09-01：scrum 分頁顯示 8/24 留下的 Opus 4.8）。"""
    br = _bridge({"s1": {"name": "Fable 5", "effort": "xhigh", "provider": "claude"}})
    br._slots_lock = threading.Lock()
    slot = _slot("s1", 1, "toolhub 優化")
    br.slots = {"s1": slot}
    br._slot_order = ["s1"]
    br.config = types.SimpleNamespace(bot_token="TEST", allowed_users=[])
    br.bot_info = {}
    sent = []
    _bt.tg_api = lambda tok, m, p: sent.append((m, p)) or {}
    br._set_bot_commands()
    desc = next(p for m, p in sent if m == "setMyCommands")["commands"][0]["description"]
    assert desc == "Switch to toolhub 優化", desc
    assert "Fable" not in desc and "xhigh" not in desc
    assert len(desc) <= 256


def test_switch_header_carries_model():
    """模型改在 /N 切過去時即時算，放進表頭。"""
    br = _bridge({"s1": {"name": "Opus 5", "effort": "xhigh", "provider": "claude"}})
    br._slots_lock = threading.Lock()
    slot = _slot("s1", 5, "雜事")
    br.slots = {"s1": slot}
    br._slot_order = ["s1"]
    br._user_active = {}
    br._last_prune_ts = time.time()
    br.config = types.SimpleNamespace(bot_token="TEST", allowed_users=[])
    br._peek_last_response = lambda s: "上一則回覆"
    sent = []
    _bt.tg_api = lambda tok, m, p: sent.append((m, p)) or {}
    br._handle_command("1", 111, 222)   # /N 是 _slot_order 的 1-based 位置
    text = next(p for m, p in sent if m == "sendMessage")["text"]
    assert text.startswith("Switched to 雜事 (/5) · Opus 5 · xhigh"), text
    assert "💬 Last AI response:\n上一則回覆" in text


def test_switch_header_without_model():
    """偵測不到就只有分頁名，不能掰一個出來。"""
    br = _bridge({})
    br._slots_lock = threading.Lock()
    slot = _slot("s1", 2, "bash")
    br.slots = {"s1": slot}
    br._slot_order = ["s1"]
    br._user_active = {}
    br._last_prune_ts = time.time()
    br.config = types.SimpleNamespace(bot_token="TEST", allowed_users=[])
    br._peek_last_response = lambda s: ""
    sent = []
    _bt.tg_api = lambda tok, m, p: sent.append((m, p)) or {}
    br._handle_command("1", 111, 222)
    text = next(p for m, p in sent if m == "sendMessage")["text"]
    assert text == "Switched to bash (/2)", text


def test_list_line_shape():
    br = _bridge({"s1": {"name": "Sonnet 5", "effort": "xhigh", "provider": "claude"}})
    slot = _slot("s1", 4, "HR")
    model = br._slot_model_suffix(slot).lstrip(" ·").strip()
    tag = f"  〔{model}〕" if model else ""
    assert tag == "  〔Sonnet 5 · xhigh〕"
    # 未知 session 該行不帶 tag
    model2 = br._slot_model_suffix(_slot("nope", 5, "x")).lstrip(" ·").strip()
    assert (f"  〔{model2}〕" if model2 else "") == ""


def test_callback_exception_safe():
    br = object.__new__(_bt.TelegramBridge)
    def _boom(sid):
        raise RuntimeError("detect failed")
    br._on_model_info = _boom
    assert br._slot_model_suffix(_slot("s1", 1, "x")) == ""   # 吞例外→空


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
