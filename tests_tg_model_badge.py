"""TG 指令選單／list 帶模型徽章（v0.29.5）回歸測試。

Howard 2026-07-06：TG 也要像側邊欄顯示每分頁的模型＋思考深度。
model 資訊來自 main.py 的 get_session_model_info（callback on_model_info）。

跑法：
    .venv/bin/python tests_tg_model_badge.py
"""

import importlib.util
import os
import sys
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)


def _bridge(model_map):
    br = object.__new__(_bt.TelegramBridge)
    br._on_model_info = lambda sid: model_map.get(sid)
    return br


def _slot(sid, index, label):
    return types.SimpleNamespace(sid=sid, index=index, label=label)


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


def test_command_description_shape():
    br = _bridge({"s1": {"name": "Fable 5", "effort": "xhigh", "provider": "claude"}})
    slot = _slot("s1", 1, "toolhub 優化")
    desc = f"Switch to {slot.label}{br._slot_model_suffix(slot)}"
    assert desc == "Switch to toolhub 優化 · Fable 5 · xhigh"
    assert len(desc) <= 256


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
