#!/usr/bin/env python3
"""P0-3 / P0-4 / P1-13 回歸測試：送 TG 失敗或沒人收，回覆不得永久蒸發。

P0-3 修前：
    slot.sent_responses.add(marked_reply)      # 先污染去重集合
    ...
    tg_api(token, "sendMessage", {...})        # 再送，且**完全不看回傳值**
`tg_api` 把 429 flood-wait / 400 / 逾時 / DNS 全部轉成 {"ok": False, …} 回傳，
這裡不看 → 回覆已經進了 sent_responses → **永不重送、也永不會被重新抽取**。
（全 repo grep `429|retry_after` 零命中，沒有任何重試或退避。）

P0-4 修前：`target_chats` 為空集合時（沒有使用者 active、又不是 _slot_order[0]
的 master 派工 worker 分頁），回覆照樣被抽出、加進 sent_responses、然後送給
零個人，之後連 /fetch 都救不回來。

P1-13：sent_responses 曾是 plain set，superset/subset 迴圈與溢位裁切都依賴任意
迭代順序。P0-2 會刻意重掃 scrollback，去重不可靠就會變成隨機重複洗版。

跑法：.venv/bin/python tests_tg_send_commit.py
"""

import importlib.util
import os
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

TOKEN = "TG_REPLY_deadbeef"
START, END = f"[[{TOKEN}]]", f"[[/{TOKEN}]]"
REPLY = "部署跑完了，三個服務都正常。"


# ────────────────────────── 一格 flush tick 的測試台 ──────────────────────────

def _bridge(calls, responder):
    """真的跑 _flush_loop 一輪；只把週邊（prune / rate-limit / typing / compact）
    換成 no-op，抽取→送出→commit 這條主線全部是真的。"""
    br = object.__new__(_bt.TelegramBridge)
    br.active = True
    br._stop_event = threading.Event()
    br._flush_wake = threading.Event()
    br._perf = {}
    br._perf_enabled = False
    br._perf_window_start = 0.0
    br._perf_ticks = 0
    br.slots = {}
    br._slot_order = []
    br._slots_lock = threading.Lock()
    br._user_active = {}
    br._user_chat = {}
    br._last_prune_ts = 0.0
    br.paused = False
    br.config = types.SimpleNamespace(bot_token="x")
    # 跑滿一個完整 tick 就停（_perf_maybe_emit 在 tick 開頭、per-slot 迴圈之前）
    def _stop_after_one_tick():
        br.active = False
    br._perf_maybe_emit = _stop_after_one_tick
    br._prune_stale_slots = lambda *a, **k: None
    br._detect_rate_limit = lambda slot: None
    br._maybe_auto_compact = lambda slot: None
    br._send_typing = lambda sid: None
    br._maybe_notify_completion = lambda slot: None
    br._detect_and_apply_board = lambda slot, lines: lines
    br._detect_and_fire_signal = lambda slot, lines: lines
    br._extract_file_paths = lambda text: []
    br._live_tail = lambda slot, rows=6: ""      # turn 已結束
    br._send_tg_file = lambda chat_id, fp: None

    def _fake_tg_api(token, method, data=None, timeout=35):
        calls.append((method, data))
        return responder(method, data)
    _bt.tg_api = _fake_tg_api
    return br


def _slot_with_marker(br, sid="s87"):
    slot = _bt.SessionSlot(sid, "調研者", lambda t: None, 11)
    slot.has_user_msg = True
    slot.awaiting_response = True
    slot.expect_marker = True
    slot.reply_start_marker = START
    slot.reply_end_marker = END
    slot.marker_prompt = ""
    slot.pending_raw = f"⏺ {START}\n{REPLY}\n{END}\n"
    slot._feed_gen = 5
    now = __import__("time").time()
    slot.msg_sent_ts = now          # 剛送出 → 不會誤觸 180s 心跳閘門
    slot.first_output_time = now - 10.0
    slot.last_output_time = now - 5.0        # idle ≥ 3s → 進得了抽取
    br.slots[sid] = slot
    br._slot_order = [sid]
    return slot


def _run(responder, with_chat=True):
    calls = []
    br = _bridge(calls, responder)
    slot = _slot_with_marker(br)
    if with_chat:
        br._user_chat = {42: 999}
        br._user_active = {42: slot.sid}
    br._flush_loop()
    return br, slot, calls


def _sent_texts(calls):
    return [d.get("text", "") for m, d in calls if m == "sendMessage"]


_OK = lambda m, d: {"ok": True, "result": {}}
_FLOOD = lambda m, d: {"ok": False, "description":
                       "HTTP 429: {\"description\":\"Too Many Requests: retry after 12\"}"}
_HARD = lambda m, d: {"ok": False, "description": "HTTP 400: Bad Request: chat not found"}


# ── 1. 送成功 → 才進去重集合 ──
def test_success_commits_dedup():
    br, slot, calls = _run(_OK)
    assert REPLY in _sent_texts(calls)[0], calls
    assert REPLY in slot.sent_responses, "送成功後應該進去重集合"
    assert slot.marker_forwarded is True


# ── 2. P0-3：TG 回 429 → 不得進去重集合（否則永久蒸發）──
def test_flood_wait_does_not_pollute_dedup():
    br, slot, calls = _run(_FLOOD)
    assert REPLY not in slot.sent_responses, "送失敗卻進了去重集合＝回覆永久蒸發"
    assert slot.marker_forwarded is False, "送失敗不該宣告已用 marker 轉發過"
    warn = [t for t in _sent_texts(calls) if "送出失敗" in t]
    assert warn, f"應該要告知使用者可以 /fetch 重取: {_sent_texts(calls)}"


# ── 3. P0-3：429 會退避（不是每 0.5s 重試打樁），退避看得到 retry_after ──
def test_flood_wait_backs_off():
    import time as _t
    br, slot, calls = _run(_FLOOD)
    assert slot.marker_next_scan_ts > _t.time() + 10, slot.marker_next_scan_ts


# ── 4. P0-3：硬失敗（400）同樣不污染去重集合 ──
def test_hard_failure_does_not_pollute_dedup():
    br, slot, calls = _run(_HARD)
    assert REPLY not in slot.sent_responses
    assert [t for t in _sent_texts(calls) if "送出失敗" in t]


# ── 5. P0-4：沒有收件人 → 不進去重集合、不清 pending_raw、一個字都不送 ──
def test_no_target_chat_keeps_reply():
    br, slot, calls = _run(_OK, with_chat=False)
    assert not _sent_texts(calls), f"沒有收件人卻送出了: {calls}"
    assert REPLY not in slot.sent_responses, "沒人收卻標記成已送＝/fetch 也救不回"
    assert slot.pending_raw, "pending_raw 不該被清掉（回覆還要留給 /fetch）"
    assert slot.marker_forwarded is False


# ── 6. _send_text_checked：短 flood-wait 就地重試一次 ──
def test_inline_retry_once_on_short_flood():
    seq = [{"ok": False, "description": "Too Many Requests: retry after 1"},
           {"ok": True, "result": {}}]
    seen = []

    def api(token, method, data=None, timeout=35):
        seen.append(data)
        return seq.pop(0)
    _bt.tg_api = api
    br = object.__new__(_bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="x")
    ok, ra = br._send_text_checked("s1", 9, "hi")
    assert ok is True and ra == 0.0, (ok, ra)
    assert len(seen) == 2, "短 flood-wait 應該就地重試一次"


# ── 7. _send_text_checked：長 flood-wait 不在 flush loop 裡 sleep，直接回失敗+秒數 ──
def test_long_flood_not_slept_inline():
    import time as _t

    def api(token, method, data=None, timeout=35):
        return {"ok": False, "description": "Too Many Requests: retry after 25"}
    _bt.tg_api = api
    br = object.__new__(_bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="x")
    t0 = _t.time()
    ok, ra = br._send_text_checked("s1", 9, "hi")
    assert ok is False and ra == 25.0, (ok, ra)
    assert _t.time() - t0 < 1.0, "flush loop 是所有分頁共用的執行緒，不可以在裡面睡 25 秒"


# ── 8. _target_chats_for 語義 ──
def test_target_chats_for():
    br = object.__new__(_bt.TelegramBridge)
    br._user_active = {1: "sA"}
    br._user_chat = {1: 100, 2: 200}
    br._slot_order = ["sA", "sB"]
    assert br._target_chats_for("sA") == {100, 200}   # 第一個 slot 收沒選過的人
    assert br._target_chats_for("sB") == set()        # 沒人 active → 空


# ── 9. P1-13：_OrderedSet 保序，裁切留下的真的是「最近加入的」 ──
def test_ordered_set_trims_newest():
    s = _bt._OrderedSet()
    for i in range(300):
        s.add(f"item-{i}")
    kept = list(s)[-100:]
    assert kept[-1] == "item-299" and kept[0] == "item-200", (kept[0], kept[-1])
    s.discard("item-299")
    assert "item-299" not in s and len(s) == 299


# ── 10. P1-13：superset/subset 同時成立時，判定與加入順序無關 ──
def test_dedup_decision_order_independent():
    """候選 text = "BBB"；已送集合同時有「包含它的」AAABBBCCC 與「被它包含的」BB。
    舊版兩種關係各自 `break`，先撞到哪一種取決於 set 迭代順序 → 同一份輸入可能
    這次轉發、下次不轉發。修後明訂優先序：已被送過的內容包含 → 不重送。"""
    br = object.__new__(_bt.TelegramBridge)
    br._perf_enabled = False
    br._perf = {}
    results = []
    for order in (["AAABBBCCC", "BB"], ["BB", "AAABBBCCC"]):
        slot = _bt.SessionSlot("s1", "t", lambda t: None, 1)
        for txt in order:
            slot.sent_responses.add(txt)
        slot.stream.feed("⏺ BBB\r\n")
        slot.stream.feed("❯ prompt\r\n")     # prompt marker 收掉 block
        results.append(br._extract_new_text(slot))
    assert results[0] == results[1], f"判定仍與順序有關: {results}"
    assert results[0] == [], f"已被更長的已送內容包含 → 不該重送: {results[0]}"


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
