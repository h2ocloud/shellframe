#!/usr/bin/env python3
"""`<<TG_REPLY_x>>` 別名回歸測試（v0.29.46）。

模型偶爾把 `[[TG_REPLY_x]]` 寫成 `<<TG_REPLY_x>>`，而且黏性極強——同一個
session 寫過一次就會一路照抄自己上一輪，該分頁的回覆從此**每一回合**都配
不到 marker，只能等 30s fallback 兜底（實際案例：連續 5 回合全是 `<<>>`，
回覆一次都沒自動轉發，只能手動 /fetch）。

跑法：.venv/bin/python tests_tg_marker_alias.py
"""

import importlib.util
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

TOKEN = "TG_REPLY_670f3168"
START, END = f"[[{TOKEN}]]", f"[[/{TOKEN}]]"
REPLY = "簡報一直都在 ToolHub 線上，連結給你。"


def _slot(raw):
    slot = _bt.SessionSlot("s1", "雜事", lambda t: None, 1, cmd="claude")
    slot.expect_marker = True
    slot.reply_start_marker = START
    slot.reply_end_marker = END
    slot.marker_prompt = f"最終要回 Telegram 的文字請放在 {START} 和 {END} 之間。"
    slot.pending_raw = raw
    slot.peek_fn = None
    return slot


def _bridge():
    br = object.__new__(_bt.TelegramBridge)
    br._perf_enabled = False
    return br


# ── 1. 官方寫法照舊 ──
def test_official_markers_still_extract():
    br, slot = _bridge(), _slot(f"⏺ {START}\n{REPLY}\n{END}\n")
    reply, _ = br._pick_marker_reply(slot, allow_inprogress=False)
    assert reply == REPLY, repr(reply)


# ── 2. `<<>>` 別名也要抽得到 ──
def test_angle_alias_extracts():
    raw = f"⏺ <<{TOKEN}>>\n{REPLY}\n<</{TOKEN}>>\n"
    br, slot = _bridge(), _slot(raw)
    reply, _ = br._pick_marker_reply(slot, allow_inprogress=False)
    assert reply == REPLY, repr(reply)


# ── 3. 混用（重繪時一半舊一半新）也要配得起來 ──
def test_mixed_delimiters():
    raw = f"⏺ <<{TOKEN}>>\n{REPLY}\n{END}\n"
    br, slot = _bridge(), _slot(raw)
    reply, _ = br._pick_marker_reply(slot, allow_inprogress=False)
    assert reply == REPLY, repr(reply)


# ── 4. 別名的殘留 token 不能漏進轉發文字 ──
def test_alias_token_residue_stripped():
    out = _bt.clean_mobile_marker_response(
        f"{REPLY}\n<<{TOKEN}>> 殘影行\n")
    assert TOKEN not in out, out
    assert REPLY in out


# ── 5. 正規化只碰 TG_REPLY token，其他 << >> 不動 ──
def test_normalize_leaves_other_angles():
    src = "if (a << 2) > b: pass\n<<EOF\n"
    assert _bt.normalize_reply_markers(src) == src


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
