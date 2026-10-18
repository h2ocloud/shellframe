"""用量導向的路由建議。只建議，不改派、不啟動行程、不花額度。

呼叫端把各 provider 的已用百分比放進 readings。沒有讀數就是未知，
不當成 0，也不在這裡打網路。已用是 utilisation（用掉多少），不是剩餘。
"""

import re

_REVIEW = ("審查", "review", "校對")
_PLAN = ("規劃", "架構", "系統設計", "權衡", "tradeoff", "trade-off", "設計")
_LIGHT = ("摘要", "格式", "翻譯", "查一下")
_PAID_NAME = re.compile(
    r"claude|gpt-|gemini|sonnet|opus|(?:^|[^a-z0-9])o[134](?:[^0-9]|$)", re.I)

_SKIP = {"not_installed", "auth_required"}

# 候選是固定清單。Cursor 只出現訂閱模型 composer-2.5，不選第三方付費模型。
_OPENCODE = {"key": "opencode", "label": "OpenCode（地端 Spark）", "model": ""}
_PI = {"key": "pi", "label": "Pi（地端 Spark）", "model": ""}
_GROK = {"key": "grok", "label": "Grok Build（訂閱）", "model": ""}
_CURSOR = {"key": "cursor", "label": "cursor-agent composer-2.5（訂閱）",
           "model": "composer-2.5"}
_OPUS = {"key": "claude", "label": "Claude Opus", "model": "opus", "drop_at_90": True}

# 預設分工。大量實作先給 cursor 訂閱模型；規劃與審查先給 Opus。
_TIERS = {
    "light": (_OPENCODE, _PI, _GROK, _CURSOR),
    "code": (_CURSOR, _GROK),
    "plan": (_OPUS, _GROK),
    "review": (_OPUS, _GROK),
}


def classify(task, tier=None) -> str:
    """light／code／plan／review。--tier 覆寫關鍵字。"""
    if tier in _TIERS:
        return tier
    text = task or ""
    low = text.lower()
    if any(k in text or k in low for k in _REVIEW):
        return "review"
    if any(k in text or k in low for k in _PLAN):
        return "plan"
    if any(k in text for k in _LIGHT):
        return "light"
    return "code"


def _pct(readings, key):
    """(state, pct)。state 是 skip／unknown／ok。未知不是 0。"""
    row = (readings or {}).get(key) or {}
    err = row.get("error")
    if err in _SKIP:
        return "skip", None
    pct = row.get("week_pct")
    if pct is None or pct == "":
        return "unknown", None
    try:
        return "ok", float(pct)
    except (TypeError, ValueError):
        return "unknown", None


def _pick(cands, readings):
    front, back = [], []
    for cand in cands:
        state, pct = _pct(readings, cand["key"])
        if state == "skip":
            continue
        if cand.get("drop_at_90") and pct is not None and pct >= 90:
            continue
        item = dict(cand)
        item["pct"] = pct
        if pct is not None and pct >= 90:
            back.append(item)
        else:
            front.append(item)
    ordered = front + back
    return ordered[0] if ordered else None


def _paid_named(task) -> bool:
    return bool(_PAID_NAME.search(task or ""))


def _counterpart(tier):
    """配對的另一側。(provider, model)。輕活沒有 Claude／Cursor 配對。"""
    if tier in ("plan", "review"):
        return "cursor", "composer-2.5"
    if tier == "code":
        return "claude", "opus"
    return "", ""


def _pair_sentence(tier) -> str:
    """點出配對的另一側。不改這一則實際選到誰。"""
    if tier in ("plan", "review"):
        return ("配對的實作方是 cursor-agent composer-2.5"
                "（訂閱內；Grok 4.7 也是訂閱內）。")
    if tier == "code":
        return "配對的規劃與審查是 Claude Opus。"
    if tier == "light":
        return "輕活留給地端 Spark。"
    return ""


def _text(tier, label, note, paid) -> str:
    parts = [f"建議（{tier}）：{label}" if label else "沒有可建議的 agent"]
    pair = _pair_sentence(tier)
    if pair:
        parts.append(pair)
    if note:
        parts.append(note)
    if paid:
        parts.append("任務點名的模型若走 Cursor 是付費，這則建議不選它。")
    parts.append("這是建議，不是指令。")
    return " ".join(parts)


def hint(task, readings, tier=None) -> dict:
    """純函式。readings 由呼叫端準備。永不拋。"""
    try:
        kind = classify(task, tier)
        chosen = _pick(_TIERS[kind], readings)
        paid = _paid_named(task)
        other, other_model = _counterpart(kind)
        if not chosen:
            return {"tier": kind, "suggestion": "", "model": "", "label": "",
                    "counterpart": other, "counterpart_model": other_model,
                    "paid_named": paid, "note": "",
                    "text": _text(kind, "", "", paid)}
        note = ""
        if chosen["key"] == "cursor" and chosen.get("pct") is None:
            note = "用量未知"
        elif chosen.get("pct") is not None and chosen["pct"] >= 90:
            note = "此候選週已用已達 90%，仍是目前較可接受的選擇"
        return {"tier": kind, "suggestion": chosen["key"], "model": chosen["model"],
                "label": chosen["label"], "counterpart": other,
                "counterpart_model": other_model, "paid_named": paid, "note": note,
                "text": _text(kind, chosen["label"], note, paid)}
    except Exception:
        return {"tier": "code", "suggestion": "", "model": "", "label": "",
                "counterpart": "claude", "counterpart_model": "opus",
                "paid_named": False, "note": "",
                "text": "沒有可建議的 agent。這是建議，不是指令。"}


def annotate(result, task, readings=None):
    """把 route_hint 放進 details。不改 success，也不改 message。永不拋。"""
    try:
        if not isinstance(result, dict):
            return result
        out = dict(result)
        details = dict(out.get("details") or {})
        details["route_hint"] = hint(task or "", readings or {})
        out["details"] = details
        return out
    except Exception:
        return result


def collect_readings(probe) -> dict:
    """probe(cmd) 應是 usage_probe.probe_data。快取通常是熱的。失敗就當未知。"""
    cmds = {"opencode": "opencode", "pi": "pi", "grok": "grok",
            "cursor": "cursor-agent", "claude": "claude"}
    out = {}
    for key, cmd in cmds.items():
        try:
            data = probe(cmd) or {}
        except Exception:
            data = {}
        week = data.get("week") if isinstance(data, dict) else None
        pct = week.get("pct") if isinstance(week, dict) else None
        err = data.get("error") if isinstance(data, dict) else "no_data"
        out[key] = {"week_pct": pct, "error": err}
    return out
