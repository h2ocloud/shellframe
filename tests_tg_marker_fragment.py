#!/usr/bin/env python3
"""marker 碎片洩漏回歸（v0.29.52）。

2026-08-31 實案：TG 收到的訊息長這樣——
    [Pi] 正在檢查 ShellFrame 的 Telegram 串接邏輯    [[
    ~ [[/            [[/TG
    [[/TG_REPLY      [[/TG_REPLY_3ca65bb9

根因：pi 是**逐字元 flush** end marker，原始 PTY 串流留下 `[[` → `[[/` →
`[[/TG` → … 整串中間狀態，全都落在 start／end 之間 → 被當成回覆內容送出。
`_REPLY_MARKER_TOKEN_RE` 要求 `]]` 結尾擋不到半截；
`clean_mobile_marker_response` 的逐行去重也擋不住（每行都不同）。
Claude Code 一次寫完 marker，所以從不觸發。

跑法：.venv/bin/python tests_tg_marker_fragment.py
"""

import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

START = "[[TG_REPLY_3ca65bb9]]"
END = "[[/TG_REPLY_3ca65bb9]]"
BR = object.__new__(_bt.TelegramBridge)


def _slot(raw):
    s = type("S", (), {})()
    s.expect_marker = True
    s.reply_start_marker = START
    s.reply_end_marker = END
    s.pending_raw = raw
    s.peek_fn = None
    s.marker_prompt = ""
    return s


def _streamed_end():
    """複刻 pi 逐字元輸出 end marker 的原始串流。"""
    out = START + "正在檢查 ShellFrame 的 Telegram 串接邏輯\n"
    for i in range(2, len(END) + 1):
        out += END[:i] + "\n"
    return out


# ── 1. 核心：逐字元 end marker 的碎片不得洩漏，正文要完整 ──
def test_streamed_marker_fragments_stripped():
    reply, _ = BR._pick_marker_reply(_slot(_streamed_end()), allow_inprogress=False)
    frags = [l for l in reply.splitlines() if l.strip().startswith("[[")]
    assert not frags, f"marker 碎片洩漏 {len(frags)} 行：{frags[:3]}"
    assert "正在檢查 ShellFrame 的 Telegram 串接邏輯" in reply, reply


# ── 2. start marker 的碎片同樣要清 ──
def test_start_marker_fragments_stripped():
    raw = "".join(START[:i] + "\n" for i in range(2, len(START) + 1))
    raw += START + "真正的回覆\n" + END
    reply, _ = BR._pick_marker_reply(_slot(raw), allow_inprogress=False)
    assert reply == "真正的回覆", repr(reply)


# ── 3. 誤刪防護：內容裡正常的 [[...]] 不可以被當成碎片 ──
def test_normal_double_bracket_kept():
    for body in ("看這段設定 [[weird]] 很重要", "[[note]] 開頭也算正文",
                 "[[TG_OTHER_xx]] 不是本輪 marker"):
        reply, _ = BR._pick_marker_reply(
            _slot(START + body + "\n" + END), allow_inprogress=False)
        assert body.split()[0] in reply or body in reply, (body, reply)


# ── 4. _is_marker_fragment 的判定邊界 ──
def test_fragment_predicate():
    ms = (START, END)
    for frag in ("[[", "[[/", "[[/TG", "[[/TG_REPLY_3ca", "[[/TG_REPLY_3ca65bb9]"):
        assert _bt._is_marker_fragment(frag, ms) is True, frag
    for keep in ("正常內容", "[[weird]]", "[", "", "[[note]] x"):
        assert _bt._is_marker_fragment(keep, ms) is False, keep


# ── 5. 沒傳 markers 時維持既有行為（其他呼叫端不受影響）──
def test_backward_compatible_without_markers():
    out = _bt.clean_mobile_marker_response("一般回覆\n第二行")
    assert "一般回覆" in out and "第二行" in out


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
