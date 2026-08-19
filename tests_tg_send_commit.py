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
import queue as _queue
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
# 測試不要污染 production 的 /tmp/shellframe_bridge.log（Howard 靠它除錯）
_bt._blog = lambda msg: None

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
# 可重試的硬失敗（伺服器暫時性錯誤）——這種才該 rollback 重抽。
# 注意：不可以用 "chat not found"／"bot was blocked" 當測資，那是**永久性**
# 失敗，v0.29.36 起會 commit（重抽再多次也不會成功，留著只會無限重試）。
_HARD = lambda m, d: {"ok": False, "description": "HTTP 500: Internal Server Error"}
# 永久性失敗（使用者封鎖 bot）
_BLOCKED = lambda m, d: {"ok": False,
                         "description": "Forbidden: bot was blocked by the user"}


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
    ok, ra, _perm = br._send_text_checked("s1", 9, "hi")
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
    ok, ra, _perm = br._send_text_checked("s1", 9, "hi")
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


# ── 11. M-4：送出失敗的 ⚠ 警告要節流，而且不得在 flush loop 裡等 HTTPS ──
def test_send_failure_warning_is_throttled():
    """洗版或 TG 掛掉時，零節流的警告會自己變成第二波洗版，並把單執行緒的
    flush loop 一起拖住（所有分頁 TG 收送同時停擺）。"""
    br, slot, calls = _run(_HARD)
    warns = [t for t in _sent_texts(calls) if "送出失敗" in t]
    assert len(warns) == 1, warns
    assert slot._send_fail_warn_ts > 0, "沒有設節流閘"
    # 節流窗內第二次失敗不得再發
    import time as _t
    calls2 = []
    br2 = _bridge(calls2, _HARD)
    slot2 = _slot_with_marker(br2)
    slot2._send_fail_warn_ts = _t.time() + 120.0    # 仍在節流窗內
    br2._user_chat = {42: 999}
    br2._user_active = {42: slot2.sid}
    br2._flush_loop()
    assert not [t for t in _sent_texts(calls2) if "送出失敗" in t], calls2


def test_send_failure_warning_not_blocking_flush_loop():
    """警告走背景 thread；失敗路徑的同步阻塞上限必須低於改動前的單次
    tg_api 預設 timeout=35s。"""
    src = open(os.path.join(_HERE, "bridge_telegram.py"), encoding="utf-8").read()
    fail_branch = src.split("send FAILED → kept out of dedup", 1)[1][:1200]
    assert "threading.Thread(" in fail_branch, "警告必須在背景 thread 發"
    br = object.__new__(_bt.TelegramBridge)
    worst = (br._SEND_TIMEOUT_S * 2) + br._SEND_INLINE_RETRY_MAX_S
    assert worst < 35.0, f"失敗路徑同步阻塞 {worst}s，比改動前的 35s 還糟"


# ── 12. M-5：重播過濾器必須跟指令派發端用同一種剝法（/restart@botname）──
def test_replay_filter_strips_bot_suffix():
    """群組裡 Telegram 客戶端送的是 `/restart@YourBot`。只切空白會漏掉 →
    重播 /restart → 再重啟，正是這個過濾器要防的迴圈。"""
    import json as _json
    import tempfile
    from pathlib import Path as _P
    br = object.__new__(_bt.TelegramBridge)
    br._update_queue = _queue.Queue()
    tmp = _P(tempfile.mkdtemp()) / "tg_pending.json"
    br._PENDING_FILE = tmp
    tmp.write_text(_json.dumps([
        {"message": {"text": "/restart@ShellFrameBot"}},
        {"message": {"text": "/RELOAD@ShellFrameBot"}},
        {"message": {"text": "/restart"}},
        {"message": {"text": "正常訊息"}},
    ]), encoding="utf-8")
    n = br._replay_pending_updates()
    got = []
    while not br._update_queue.empty():
        got.append(br._update_queue.get_nowait()["message"]["text"])
    assert got == ["正常訊息"], f"自我重啟指令沒被濾掉: {got}"
    assert n == 1
    assert not tmp.exists(), "重播後應刪檔，避免無限重播"


# ── 13. P0-7：stop() 不得刪掉「上一輪存了、還沒被重播」的 pending 檔 ──
def test_stop_does_not_delete_unreplayed_pending():
    """2026-08-17 實機驗證抓到：`_persist_pending_updates()` 在佇列為空時會
    unlink 檔案。但佇列空只代表「這一輪沒有待處理訊息」，不代表磁碟上那份是
    垃圾——檔案只由 _replay_pending_updates() 讀完後刪除，所以它存在＝上一輪
    存了、還沒有人重播過。兩次快速 reload（第二次的 poll loop 還沒跑到 replay
    就又被 stop）就會把上一輪的訊息永久丟掉，正是 P0-7 要修的那個病。"""
    import json as _json
    import tempfile
    from pathlib import Path as _P
    br = object.__new__(_bt.TelegramBridge)
    br._update_queue = _queue.Queue()
    tmp = _P(tempfile.mkdtemp()) / "tg_pending.json"
    br._PENDING_FILE = tmp
    payload = [{"message": {"text": "上一輪還沒重播的訊息"}}]
    tmp.write_text(_json.dumps(payload), encoding="utf-8")

    br._persist_pending_updates()          # 佇列是空的
    assert tmp.exists(), "stop() 把還沒重播的訊息刪掉了＝永久遺失"
    assert _json.loads(tmp.read_text()) == payload, "內容被覆寫了"

    # 有東西時照樣覆寫存檔，且重播後檔案才消失
    br._update_queue.put({"message": {"text": "新的一則"}})
    br._persist_pending_updates()
    assert [u["message"]["text"] for u in _json.loads(tmp.read_text())] == ["新的一則"]
    assert br._replay_pending_updates() == 1
    assert not tmp.exists(), "重播後應刪檔"


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


# ── v0.29.36：唯一收件人封鎖了 bot（永久失敗）→ 必須 commit，不可無限重抽。
#    這就是 Howard 2026-08-19「對話1跳針」的端對端形狀。
def test_permanent_failure_commits_and_unroutes():
    br, slot, calls = _run(_BLOCKED)
    assert REPLY in slot.sent_responses, \
        "永久失敗仍不 commit → 下一輪重抽重送＝跳針"
    assert not br._user_chat, f"封鎖 bot 的 chat 未從路由移除: {br._user_chat}"
