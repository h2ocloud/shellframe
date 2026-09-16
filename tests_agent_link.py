#!/usr/bin/env python3
"""agent_link（實驗性 A2A 代理互傳）回歸測試。

這個功能會**自動把文字寫進另一個 agent 的 prompt**，而每個分頁都是
bypass permissions 在跑，所以護欄本身就是功能的主體。這支測試守的就是護欄：

  - 預設關閉：experimental_a2a 沒開就什麼都不做
  - 只能寄給 roster 裡有的角色，不能自己寄給自己
  - 深度上限：A→B→A… 這種來回會在 MAX_DEPTH 之後被切斷
  - 速率上限：單一分頁每分鐘最多 MAX_PER_MINUTE 則
  - 長度上限；空訊息不送
  - 使用者自己打字會重置深度鏈（不算 agent 之間的接力）
  - 每一筆（含被拒的）都要留下稽核記錄

跑法：.venv/bin/python tests_agent_link.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Redirect the audit log into a temp dir before the module caches the path.
_TMP = tempfile.mkdtemp(prefix="sf-a2a-")
import agent_link  # noqa: E402

agent_link.STATE_DIR = Path(_TMP)
agent_link.LOG_FILE = Path(_TMP) / "agent_link.json"

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


ROSTER = {"Coding": {"label": "Coding-CDX"}, "研究": {"label": "研究-CLD"}}


def reset():
    agent_link._recent.clear()
    agent_link._inbound_depth.clear()
    try:
        agent_link.LOG_FILE.unlink()
    except OSError:
        pass


def auth(**kw):
    base = dict(enabled=True, from_sid="s1", from_label="時程信件-CLD",
                to_role="Coding", to_sid="s2", text="幫我看一下 relay",
                roster=ROSTER)
    base.update(kw)
    return agent_link.authorize(**base)


def main():
    reset()

    # ── 預設關閉 ──
    ok, reason, _ = auth(enabled=False)
    check("off by default: nothing is delivered", ok is False and "off" in reason)

    # ── 目標必須是 roster 裡的角色 ──
    ok, reason, _ = auth(to_role="不存在的角色")
    check("unknown role refused", ok is False and "unknown role" in reason)
    ok, reason, _ = auth(to_role="")
    check("empty role refused", ok is False)

    # ── 不能寄給自己 ──
    ok, reason, _ = auth(to_sid="s1")
    check("refuses to message itself", ok is False and "itself" in reason)

    # ── 內容檢查 ──
    reset()
    ok, reason, _ = auth(text="   ")
    check("empty message refused", ok is False and "empty" in reason)
    ok, reason, _ = auth(text="x" * (agent_link.MAX_CHARS + 1))
    check("oversized message refused", ok is False and "over" in reason)

    # ── 正常情況 ──
    reset()
    ok, reason, depth = auth()
    check("a normal message is allowed", ok is True and reason == "" and depth == 1)

    # ── 速率上限 ──
    reset()
    allowed = sum(1 for _ in range(agent_link.MAX_PER_MINUTE + 3) if auth()[0])
    check(f"rate limited to {agent_link.MAX_PER_MINUTE}/min",
          allowed == agent_link.MAX_PER_MINUTE)
    ok, reason, _ = auth()
    check("rate-limit refusal says so", ok is False and "rate limit" in reason)
    # a different tab is unaffected by another tab's flooding
    ok, _, _ = auth(from_sid="s9")
    check("rate limit is per sender", ok is True)

    # ── 深度上限：A→B→A… 會被切斷 ──
    reset()
    depth = 0
    hops = 0
    a, b = "sA", "sB"
    for i in range(agent_link.MAX_DEPTH + 4):
        sender, target = (a, b) if i % 2 == 0 else (b, a)
        ok, reason, depth = agent_link.authorize(
            enabled=True, from_sid=sender, from_label=sender,
            to_role="Coding", to_sid=target, text="ping", roster=ROSTER)
        if not ok:
            break
        agent_link.note_delivered(target, depth)
        hops += 1
    check(f"back-and-forth stops at depth {agent_link.MAX_DEPTH}",
          hops == agent_link.MAX_DEPTH and "depth" in reason)

    # ── 使用者插話會重置鏈 ──
    agent_link.clear_chain(a)
    ok, _, depth = agent_link.authorize(
        enabled=True, from_sid=a, from_label=a, to_role="Coding",
        to_sid=b, text="fresh", roster=ROSTER)
    check("a user turn resets the chain", ok is True and depth == 1)

    # ── 稽核記錄 ──
    reset()
    agent_link.record({"kind": "message", "from_label": "A", "to_role": "Coding",
                       "text": "hi", "depth": 1})
    agent_link.record({"kind": "refused", "from_label": "A", "to_role": "Coding",
                       "text": "hi", "reason": "rate limit"})
    h = agent_link.history(10)
    check("both delivered and refused are audited", len(h) == 2
          and h[0]["kind"] == "message" and h[1]["kind"] == "refused")
    check("audit entries are ordered and identified",
          h[0]["id"] == 1 and h[1]["id"] == 2 and h[1]["ts"] >= h[0]["ts"])

    # ── 送出去的內容要標明來源、且不冒充使用者 ──
    body = agent_link.format_delivery("研究-CLD", "把這段查一下")
    check("delivery is attributed to the peer, not the user",
          "研究-CLD" in body and "不是使用者本人" in body and body.endswith("把這段查一下"))

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
