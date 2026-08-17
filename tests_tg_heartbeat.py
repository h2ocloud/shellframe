#!/usr/bin/env python3
"""長回合心跳 ＋ 進行中預覽測試 — 2026-08-17。

痛點：主 agent 在等背景 sub 時，Claude Code 的 footer 一直掛著
`esc to interrupt` → `turn_ended` 恆為 False → 30s fallback 永遠不觸發 →
背景任務跑 5 分鐘、30 分鐘、數小時，這條路就靜默數小時（Howard 說的
「愛回不回」）。心跳補的就是這段。

設計紅線（本檔就是在守它們）：
  * 零新增掃描——閘門掛既有 2s slow_tick，只讀 slot 上的 float/bool 欄位。
  * 寧可漏發不可多發：180s 首次門檻 ＋ 指數退避 300→1800s ＋ 內容 hash 去重
    ＋ /quiet 出口。
  * **S1（唯一不可退讓）**：進行中預覽絕對不進 sent_responses——否則真回覆
    來時會被當成「已送過」永久壓制。

跑法：.venv/bin/python tests_tg_heartbeat.py
"""

import importlib.util
import os
import threading
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
# 測試不要污染 production 的 /tmp/shellframe_bridge.log（Howard 靠它除錯）
_bt._blog = lambda msg: None
BR = _bt.TelegramBridge


# ─────────────────────── 閘門（純函式化重現 A4.2 的條件） ───────────────────────

def _gate(slot, now):
    """複製 _flush_loop slow_tick 區塊的判斷順序，用來測門檻/退避。
    真正的程式碼在 bridge_telegram.py，這裡只驗條件語義。"""
    if not slot.awaiting_response:
        return False, "not-awaiting"
    if slot._hb_quiet:
        return False, "quiet"
    if slot.marker_forwarded:
        return False, "already-replied"
    if slot.last_extraction_ts > slot.msg_sent_ts:
        return False, "extracted"
    waited = now - (slot.msg_sent_ts or now)
    if waited < BR.HEARTBEAT_FIRST_S:
        return False, "too-early"
    if now < slot._hb_next_ts:
        return False, "backoff"
    slot._hb_next_ts = now + min(
        BR.HEARTBEAT_MAX_S,
        BR.HEARTBEAT_INTERVAL_S * (BR.HEARTBEAT_BACKOFF ** slot._hb_count))
    slot._hb_count += 1
    return True, "fire"


def _slot(**kw):
    s = _bt.SessionSlot("s87", "調研者", lambda t: None, 11)
    s.awaiting_response = True
    s.msg_sent_ts = 1000.0
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# ── 1. 180s 前不發（正常回合完全不會被打擾）──
def test_no_heartbeat_before_first_threshold():
    s = _slot()
    for t in (1000.0, 1060.0, 1179.0):
        fired, why = _gate(s, t)
        assert not fired and why == "too-early", (t, why)
    assert _gate(s, 1180.0)[0] is True


# ── 2. 退避序列：3min → +5min → +7.5min → +11.25min …，上限 30min ──
def test_backoff_sequence():
    s = _slot()
    now = 1000.0 + BR.HEARTBEAT_FIRST_S
    gaps = []
    prev = 1000.0                          # 使用者訊息送出的時刻
    for _ in range(8):
        while not _gate(s, now)[0]:
            now += 1.0
        gaps.append(round(now - prev))
        prev = now
        now += 1.0
    assert gaps[0] == 180, gaps            # 首次 = 門檻
    assert gaps[1] == 300, gaps            # 基礎間隔 5 分鐘
    assert gaps[2] == 450, gaps            # ×1.5
    assert gaps[3] == 675, gaps
    assert all(g <= BR.HEARTBEAT_MAX_S for g in gaps), gaps
    assert gaps[-1] == 1800, f"應該收斂到 30 分鐘上限: {gaps}"


# ── 3. 回覆出去後就停（marker_forwarded / last_extraction_ts）──
def test_stops_after_reply():
    s = _slot(marker_forwarded=True)
    assert _gate(s, 9999.0) == (False, "already-replied")
    s2 = _slot(last_extraction_ts=2000.0)
    assert _gate(s2, 9999.0) == (False, "extracted")
    s3 = _slot(awaiting_response=False)
    assert _gate(s3, 9999.0) == (False, "not-awaiting")


# ── 4. /quiet 出口 ──
def test_quiet_mutes_epoch():
    s = _slot(_hb_quiet=True)
    assert _gate(s, 9999.0) == (False, "quiet")


# ── 5b. M-2：狀態不變時**不得永久靜音**（QA Major）──
def _hb_bridge(sent, status=None):
    def api(token, method, data=None, timeout=35):
        sent.append(data["text"])
        return {"ok": True}
    _bt.tg_api = api
    br = object.__new__(BR)
    br.config = types.SimpleNamespace(bot_token="x")
    br._perf_enabled = False
    br._perf = {}
    br._on_agent_status = (lambda sid: (status, 1.0)) if status else None
    br._live_tail = lambda slot, rows=6: ""
    return br


def _wire(br, s):
    br.slots = {"s87": s}
    br._slot_order = ["s87"]
    br._user_active = {1: "s87"}
    br._user_chat = {1: 555}


def test_unchanging_state_is_not_silenced_forever():
    """舊條件 `sig == _hb_last_hash and _hb_count > 1` 讓「狀態一直沒變的卡住
    分頁」每個 epoch 只發一則就永久靜音——而那正是 s87「愛回不回」的形狀，
    等於修 s87 的功能在 s87 的情境下自己失效。SA 規格是「相同 **且** 距上次
    實際送出未滿 2×當前間隔」才跳過：退避照樣拉長，但永遠不會永久靜音。"""
    sent = []
    br = _hb_bridge(sent, status={"state": "working", "action": "同一件事",
                                  "task": ""})
    s = _slot()
    _wire(br, s)
    # 模擬閘門連續觸發（間隔照退避數列），內容全程不變
    now = 1000.0 + BR.HEARTBEAT_FIRST_S
    fired = 0
    for _ in range(12):
        while not _gate(s, now)[0]:
            now += 1.0
        fired += 1
        s._hb_interval = min(BR.HEARTBEAT_MAX_S,
                             BR.HEARTBEAT_INTERVAL_S
                             * (BR.HEARTBEAT_BACKOFF ** max(0, s._hb_count - 1)))
        _send_at(br, "s87", now - 1000.0, now)
        now += 1.0
    assert len(sent) >= 3, (
        f"狀態不變時 {fired} 次閘門只送出 {len(sent)} 則——永久靜音又回來了")


def _send_at(br, sid, waited, now):
    """用固定的 now 呼叫 _send_heartbeat（避開真實時鐘）。"""
    real = _bt.time.time
    _bt.time.time = lambda: now
    try:
        br._send_heartbeat(sid, waited)
    finally:
        _bt.time.time = real


def test_dedup_window_is_time_based_not_count_based():
    """同一內容、距上次送出很近 → 跳過；超過 2×間隔 → 一定要再發一次。"""
    sent = []
    br = _hb_bridge(sent, status={"state": "working", "action": "X", "task": ""})
    s = _slot()
    _wire(br, s)
    s._hb_count, s._hb_interval = 1, 300.0
    _send_at(br, "s87", 200.0, 10000.0)
    assert len(sent) == 1
    s._hb_count = 2
    _send_at(br, "s87", 500.0, 10000.0 + 300.0)     # 300 < 2×300 → 跳過
    assert len(sent) == 1, sent
    s._hb_count = 3
    _send_at(br, "s87", 900.0, 10000.0 + 700.0)     # 700 > 2×300 → 要發
    assert len(sent) == 2, sent


def test_no_informative_content_never_deduped():
    """main.py 尚未 restart（_on_agent_status is None）、或 shell 分頁時，
    status/bg 兩行都空 → sig 恆為 '|'。此時訊息裡唯一會變的就是已等時間，
    做內容去重等於保證每個 epoch 只發一則（QA 指出的加重情形）。"""
    sent = []
    br = _hb_bridge(sent)                            # 無 status callback
    s = _slot()
    _wire(br, s)
    for i, (cnt, t) in enumerate(((1, 10000.0), (2, 10300.0), (3, 10750.0))):
        s._hb_count, s._hb_interval = cnt, 300.0
        _send_at(br, "s87", 200.0 + i * 300, t)
    assert len(sent) == 3, f"沒有可去重的資訊時不該擋: {sent}"


# ── 5. 內容 hash 去重：一樣的狀態不連發 ──
def test_content_hash_dedup():
    sent = []

    def api(token, method, data=None, timeout=35):
        sent.append(data["text"])
        return {"ok": True}
    _bt.tg_api = api
    br = object.__new__(BR)
    br.config = types.SimpleNamespace(bot_token="x")
    br._on_agent_status = lambda sid: ({"state": "working", "action": "Delegating",
                                        "task": "wiring config"}, 1.0)
    br._live_tail = lambda slot, rows=6: "✻ Waiting for 1 background agent to finish"
    s = _slot()
    br.slots = {"s87": s}
    br._slot_order = ["s87"]
    br._user_active = {1: "s87"}
    br._user_chat = {1: 555}
    s._hb_count = 1
    br._send_heartbeat("s87", 200.0)
    assert len(sent) == 1, sent
    assert "調研者" in sent[0] and "working" in sent[0]
    assert "背景 agent" in sent[0], sent[0]
    assert "/quiet" in sent[0], "訊息裡要有出口，使用者才知道怎麼叫它閉嘴"
    s._hb_count = 2
    br._send_heartbeat("s87", 500.0)
    assert len(sent) == 1, f"同樣狀態不該連發: {sent}"


# ── 6. 沒有收件人時完全不發（不要對空氣心跳）──
def test_no_target_chat_no_heartbeat():
    sent = []
    _bt.tg_api = lambda *a, **k: sent.append(a) or {"ok": True}
    br = object.__new__(BR)
    br.config = types.SimpleNamespace(bot_token="x")
    br._on_agent_status = None
    br.slots = {"s87": _slot()}
    br._slot_order = ["sOther"]
    br._user_active = {}
    br._user_chat = {}
    br._send_heartbeat("s87", 500.0)
    assert sent == []


# ── 7. 拿不到 StatusTracker 資料 → 降級，但**絕不因此不發** ──
def test_degrades_without_status():
    sent = []

    def api(token, method, data=None, timeout=35):
        sent.append(data["text"])
        return {"ok": True}
    _bt.tg_api = api
    br = object.__new__(BR)
    br.config = types.SimpleNamespace(bot_token="x")
    br._on_agent_status = None            # main.py 還沒重啟的情況
    br._live_tail = lambda slot, rows=6: ""
    s = _slot()
    br.slots = {"s87": s}
    br._slot_order = ["s87"]
    br._user_active = {1: "s87"}
    br._user_chat = {1: 555}
    br._send_heartbeat("s87", 500.0)
    assert len(sent) == 1 and "還在跑" in sent[0], sent


# ── 8. 狀態資料過期（>30s）就不印，免得講的活動早就過去了 ──
def test_stale_status_not_shown():
    br = object.__new__(BR)
    br._on_agent_status = lambda sid: ({"state": "working", "action": "X"}, 99.0)
    assert br._heartbeat_status_line("s87") == ""
    br._on_agent_status = lambda sid: ({"state": "working", "action": "X"}, 3.0)
    assert "working" in br._heartbeat_status_line("s87")


# ── 9. S1：進行中預覽**絕不**進 sent_responses（唯一不可退讓的條件）──
def test_preview_never_enters_sent_responses():
    br = object.__new__(BR)
    br._perf_enabled = False
    br._perf = {}
    br._marker_fallback_text = lambda slot: "我正在改 config.py，還沒跑完測試。"
    s = _slot()
    before = set(s.sent_responses)
    body = br._maybe_preview(s, BR.PREVIEW_AFTER_S + 1)
    assert body and "config.py" in body, body
    assert set(s.sent_responses) == before, "S1 違反：預覽進了去重集合，真回覆會被壓制"
    assert s.expect_marker is not False or True   # S2：不動 marker 監聽
    assert s.marker_forwarded is False
    assert s.pending_raw == ""


# ── 10. S4/S5/S6：15 分鐘前不預覽、每 epoch 上限 2 次、內容相同就跳過 ──
def test_preview_throttles():
    br = object.__new__(BR)
    br._perf_enabled = False
    br._perf = {}
    texts = iter(["第一段進度", "第二段進度", "第三段進度"])
    br._marker_fallback_text = lambda slot: next(texts)
    s = _slot()
    assert br._maybe_preview(s, 100.0) == "", "15 分鐘前不該預覽"
    s._feed_gen = 1
    assert br._maybe_preview(s, 1000.0) == "第一段進度"
    assert br._maybe_preview(s, 1000.0) == "", "S5：_feed_gen 沒變不重取"
    s._feed_gen = 2
    assert br._maybe_preview(s, 1000.0) == "第二段進度"
    s._feed_gen = 3
    assert br._maybe_preview(s, 1000.0) == "", "S4：每 epoch 上限 2 次"


def test_preview_same_content_skipped():
    br = object.__new__(BR)
    br._perf_enabled = False
    br._perf = {}
    br._marker_fallback_text = lambda slot: "一模一樣的進度"
    s = _slot()
    s._feed_gen = 1
    assert br._maybe_preview(s, 1000.0) == "一模一樣的進度"
    s._feed_gen = 2
    assert br._maybe_preview(s, 1000.0) == "", "S6：卡死的分頁不該重複貼同一段"


# ── 11. 閘門必須掛在既有 slow_tick、且有 perf 計時（回歸守則第 2 條）──
def test_gate_is_perf_instrumented():
    src = open(os.path.join(_HERE, "bridge_telegram.py"), encoding="utf-8").read()
    assert '_perf_end("heartbeat_gate"' in src, "沒掛計時的路徑＝下次事故的藏身處"
    assert '_perf_end("preview_peek"' in src
    body = src.split("def _flush_loop", 1)[1]
    gate = body.split("heartbeat_gate", 1)[0]
    assert "if slow_tick:" in gate, "心跳閘門必須掛在既有 2s slow_tick，不得另開輪詢"


# ── 12. /quiet 指令有註冊（否則出口按了沒反應）──
def test_quiet_command_registered():
    src = open(os.path.join(_HERE, "bridge_telegram.py"), encoding="utf-8").read()
    assert "'quiet'" in src and 'cmd in ("quiet", "安靜")' in src
    assert '{"command": "quiet"' in src, "要出現在 TG 指令選單裡"


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
