#!/usr/bin/env python3
"""TG /effort 推理深度調整回歸測試（v0.29.19）。

claude 原生 /effort（滑桿 low→ultracode，帶參數跳 Yes/No 確認）與 codex
/model→reasoning 編號選單，收斂成一組 TG inline 按鈕。

跑法：.venv/bin/python tests_tg_effort.py
"""

import importlib.util
import os
import threading
import time
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)


def _bridge(cmd, display, sent=None, writes=None):
    br = object.__new__(_bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="T", allowed_users=[])
    br._user_active = {111: "s1"}
    br._user_chat = {111: 222}
    slot = types.SimpleNamespace(
        sid="s1", label="測試", index=1, cmd=cmd,
        write_fn=lambda d: (writes.append(d) if writes is not None else None),
        write_lock=threading.Lock(),
        screen=object(), _feed_gen=1, _display_cache=display, _display_cache_gen=1)
    br.slots = {"s1": slot}
    br.get_active_sid = lambda uid: "s1"
    br._live_tail = lambda s, rows=6: "\n".join(s._display_cache[-rows:])
    _bt.tg_api = lambda tok, m, p=None: (sent.append((m, p)) if sent is not None else None) or {}
    return br, slot


# ── 1. claude 分頁 → 6 個 claude 層級按鈕 + 取消 ──
def test_claude_buttons():
    sent = []
    br, _ = _bridge("claude --model opus", ["❯ ", "◈ max · /effort"], sent=sent)
    br._handle_effort_command(111, 222)
    _, p = sent[-1]
    kb = p["reply_markup"]["inline_keyboard"]
    assert len(kb) == 7, len(kb)                      # 6 層級 + 取消
    tokens = [row[0]["callback_data"] for row in kb]
    assert tokens[0] == "efchoice:claude:s1:low"
    assert tokens[4] == "efchoice:claude:s1:max"
    assert tokens[5] == "efchoice:claude:s1:ultracode"
    assert tokens[-1] == "efcancel:s1"


# ── 2. codex 分頁 → 5 個 codex 編號按鈕 ──
def test_codex_buttons():
    sent = []
    br, _ = _bridge("sf-codex --search", ["model: gpt-5.6 high  /model to change"], sent=sent)
    br._handle_effort_command(111, 222)
    _, p = sent[-1]
    kb = p["reply_markup"]["inline_keyboard"]
    assert len(kb) == 6, len(kb)                      # 5 層級 + 取消
    assert kb[0][0]["callback_data"] == "efchoice:codex:s1:1"
    assert kb[4][0]["callback_data"] == "efchoice:codex:s1:5"


# ── 3. 非 AI 分頁 → 拒絕 ──
def test_non_ai_rejected():
    sent = []
    br, _ = _bridge("zsh", ["$ "], sent=sent)
    br._handle_effort_command(111, 222)
    assert sent and "不是 claude/codex" in sent[-1][1]["text"]


# ── 4. 回合進行中 → 擋下 ──
def test_busy_blocked():
    sent = []
    br, _ = _bridge("claude", ["thinking… esc to interrupt"], sent=sent)
    br._handle_effort_command(111, 222)
    assert sent and "正在跑回合" in sent[-1][1]["text"]


# ── 5. claude apply：送 /effort <level>，遇確認答 1，回讀層級 ──
def test_apply_claude_confirms():
    writes = []
    # 先顯示確認框，再顯示「Kept effort level as high」
    br, slot = _bridge("claude", ["Change effort level?", "1. Yes, switch to high"],
                       writes=writes)
    # apply 期間畫面會被讀多次；用 mutable 讓第二輪出現確認結果
    seq = [["Change effort level?", "1. Yes, switch to high"],
           ["⎿  Set effort level to high", "◈ high"]]
    state = {"i": 0}
    def disp(s):
        i = min(state["i"], len(seq) - 1); state["i"] += 1
        return seq[i]
    br._slot_display = disp
    got = br._apply_effort_claude(slot, "high")
    assert any(w.startswith("/effort high") for w in writes), writes
    assert "1\r" in writes, writes                    # 答了 Yes
    assert got == "high", got


# ── 6. 層級定義完整（claude 6 級、codex 5 級）──
def test_level_tables():
    assert [t for t, _ in _bt.TelegramBridge._EFFORT_CLAUDE] == \
        ["low", "medium", "high", "xhigh", "max", "ultracode"]
    assert [t for t, _ in _bt.TelegramBridge._EFFORT_CODEX] == \
        ["1", "2", "3", "4", "5"]


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                import traceback
                print(f"FAIL {name}: {e}")
                traceback.print_exc()
                fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
