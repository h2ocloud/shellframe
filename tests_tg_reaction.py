#!/usr/bin/env python3
"""送達回執（reaction 狀態機）測試 — 2026-08-17。

痛點：訊息注入成功時 bridge 完全靜默（只有失敗才講話），0–8s 排隊期也靜默，
使用者的體感就是「不知道有沒有收到」。改用 reaction 就地標在使用者自己那則
訊息上：不佔對話列、不推播、不洗版；且 bot 只能有一個 reaction、後設的取代
先設的，天生就是狀態機。

  T0 已收下、準備注入      → 👀
  T1 確認送進 session      → 🫡
  T2 送不進去 / 寫入失敗   → 清空 reaction ＋ 既有文字警告
  T3 busy guard 等滿 120s  → 保持 👀 ＋ 新增文字警告（P0-8）

⚠ ✅ **不在** Telegram setMessageReaction 的 emoji 白名單。2026-08-17 用真實
bot token 實測：👀 ok / 🫡 ok / 👌 ok / ✅ 回 400 REACTION_INVALID。

跑法：.venv/bin/python tests_tg_reaction.py
"""

import importlib.util
import os
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
# 測試不要污染 production 的 /tmp/shellframe_bridge.log（使用者靠它除錯）
_bt._blog = lambda msg: None


def _bridge(responder):
    br = object.__new__(_bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="x")
    br._reaction_disabled = False
    br._reaction_fail = {}
    calls = []

    def api(token, method, data=None, timeout=35):
        calls.append((method, data, timeout))
        return responder(method, data)
    _bt.tg_api = api
    return br, calls


_OK = lambda m, d: {"ok": True, "result": True}
_INVALID = lambda m, d: {"ok": False, "error_code": 400,
                         "description": "Bad Request: REACTION_INVALID"}


# ── 1. 白名單：程式裡用的 emoji 不能是被拒的 ✅ ──
def test_uses_whitelisted_emojis():
    assert _bt.TelegramBridge.REACTION_SEEN == "👀"
    assert _bt.TelegramBridge.REACTION_DELIVERED == "🫡"
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "bridge_telegram.py"), encoding="utf-8").read()
    assert '"emoji": "✅"' not in src, "✅ 不在 TG reaction 白名單，會被 API 拒"


# ── 2. 設 reaction 的 payload 形狀 + timeout=5（不得用預設 35s）──
def test_set_reaction_payload_and_timeout():
    br, calls = _bridge(_OK)
    assert br._set_reaction(999, 4180, br.REACTION_SEEN) is True
    method, data, timeout = calls[0]
    assert method == "setMessageReaction"
    assert data["chat_id"] == 999 and data["message_id"] == 4180
    assert data["reaction"] == [{"type": "emoji", "emoji": "👀"}]
    assert timeout == 5, f"reaction 一律 timeout=5（tg_api 可以卡 35s）: {timeout}"


# ── 3. T2：傳 None 是「清空」，不是設一個空 emoji ──
def test_clear_reaction():
    br, calls = _bridge(_OK)
    br._set_reaction(999, 4180, None)
    assert calls[0][1]["reaction"] == []


# ── 4. 缺 message_id / chat_id → 直接不呼叫 API ──
def test_missing_ids_noop():
    br, calls = _bridge(_OK)
    assert br._set_reaction(None, 1, "👀") is False
    assert br._set_reaction(1, None, "👀") is False
    assert calls == []


# ── 5. 失敗計數：連續 3 次才停用，且只發一次告知 ──
def test_disables_after_three_failures():
    br, calls = _bridge(_INVALID)
    for _ in range(2):
        br._set_reaction(999, 1, "🫡")
    assert br._reaction_disabled is False, "前兩次失敗不該停用（單則失敗沒有告知價值）"
    assert not [c for c in calls if c[0] == "sendMessage"], "不該退回文字製造雜訊"
    br._set_reaction(999, 1, "🫡")
    assert br._reaction_disabled is True
    notes = [c for c in calls if c[0] == "sendMessage"]
    assert len(notes) == 1 and "已停用" in notes[0][1]["text"], notes
    # 停用後不再打 API
    n = len(calls)
    br._set_reaction(999, 1, "🫡")
    assert len(calls) == n


# ── 6. 成功一次就把該 chat 的失敗計數清零（避免慢性累積誤觸停用）──
def test_success_resets_fail_counter():
    state = {"fail": True}

    def responder(m, d):
        return _INVALID(m, d) if state["fail"] else _OK(m, d)
    br, calls = _bridge(responder)
    br._set_reaction(999, 1, "🫡")
    br._set_reaction(999, 1, "🫡")
    assert br._reaction_fail[999] == 2
    state["fail"] = False
    br._set_reaction(999, 1, "🫡")
    assert 999 not in br._reaction_fail
    state["fail"] = True
    br._set_reaction(999, 1, "🫡")
    assert br._reaction_disabled is False


# ── 7. T2 的文字警告不受 reaction 停用開關影響（結構檢查）──
def test_delivery_warning_is_plain_text_not_reaction():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "bridge_telegram.py"), encoding="utf-8").read()
    assert "無法確認訊息已送進" in src, "T2 的實質告警必須永遠送得出去（文字）"
    assert "訊息已強制送入" in src, "P0-8：busy guard 逾時強制注入必須明講"
    assert "寫入「" in src, "P0-6：write_fn 失敗必須明講，而不是靜默"


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
