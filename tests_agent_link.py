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
    agent_link._seen_ids.clear()
    agent_link._fingerprints.clear()
    for path in (agent_link.LOG_FILE, agent_link.handoff_path()):
        try:
            path.unlink()
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

    # ── 跨機位址、去重、交接只回一次。護欄仍在 authorize 外面。──
    reset()

    class Fake:
        def __init__(self):
            self.roster_map = {
                "Coding": {"label": "Coding-CDX"},
                "研究": {"label": "研究-CLD"},
            }
            self.sessions = {"sA": "研究-CLD", "sB": "Coding-CDX"}
            self.peer_map = {"abc": {"name": "otherbox"}}
            self.sent = []
            self.remote_calls = []
            self.on = True
            self.frame = "here"
            self.fid = "me"

        def enabled(self):
            return self.on

        def roster(self):
            return self.roster_map

        def peers(self):
            return self.peer_map

        def frame_name(self):
            return self.frame

        def frame_id(self):
            return self.fid

        def resolve(self, role, sid):
            if sid:
                if sid not in self.sessions:
                    return "", ""
                return sid, self.sessions[sid]
            entry = self.roster_map.get(role) or {}
            label = entry.get("label") or ""
            if not label:
                return "", ""
            for key, value in self.sessions.items():
                if value == label:
                    return key, label
            return "", label

        def send(self, sid, text):
            self.sent.append((sid, text))
            return {"success": True, "message": "sent"}

        def delegate(self, role, task):
            self.sent.append(("new", task))
            return {"success": True, "message": f"Delegated to {role} (s9)",
                    "details": {"sid": "s9"}}

        def remote(self, peer_id, payload):
            self.remote_calls.append((peer_id, payload))
            return {"success": True, "message": "ok"}

    fake = Fake()
    parsed = agent_link.parse_target("otherbox/s12")
    check("peer/sid splits on the first slash",
          parsed == {"peer": "otherbox", "role": "", "sid": "s12"})
    check("a slash in a role is a remote target",
          agent_link.parse_target("nope/Coding")["peer"] == "nope")

    missing = agent_link.dispatch(fake, target="nope/Coding", text="hi", from_sid="sA")
    check("unpaired peer is refused and not delivered",
          missing["success"] is False and "unknown peer" in missing["message"]
          and fake.remote_calls == [] and fake.sent == [])

    fake.frame = "otherbox"
    own = agent_link.dispatch(fake, target="otherbox/Coding", text="hi", from_sid="sA")
    check("refuses to message this machine",
          own["success"] is False and "itself" in own["message"] and fake.remote_calls == [])
    fake.frame = "here"

    sent = agent_link.dispatch(fake, target="otherbox/Coding", text="看一下",
                               from_sid="sA", from_label="研究", msg_id="m-remote")
    check("paired peer gets one signed handoff payload",
          sent["success"] and fake.remote_calls
          and fake.remote_calls[-1][0] == "abc"
          and fake.remote_calls[-1][1]["target"] == "Coding"
          and fake.sent == [])
    again = agent_link.dispatch(fake, target="otherbox/Coding", text="另一句",
                                from_sid="sA", msg_id="m-remote")
    check("the same message id is delivered once",
          again["success"] is False and "duplicate id" in again["message"]
          and len(fake.remote_calls) == 1)

    reset()
    fake = Fake()
    first = agent_link.dispatch(fake, target="Coding", text="同一句", from_sid="sA",
                                from_label="研究")
    second = agent_link.dispatch(fake, target="Coding", text="同一句", from_sid="sA",
                                 from_label="研究")
    check("identical text within 60s is refused before a second send",
          first["success"] and second["success"] is False
          and "duplicate" in second["message"] and len(fake.sent) == 1)
    check("a duplicate does not spend another rate slot",
          len(agent_link._recent.get("sA", [])) == 1)

    reset()
    fake = Fake()
    hops = 0
    reason = ""
    for i in range(agent_link.MAX_DEPTH + 3):
        sender, role = ("sA", "Coding") if i % 2 == 0 else ("sB", "研究")
        res = agent_link.dispatch(fake, target=role, text=f"ping-{i}",
                                  from_sid=sender, from_label=sender)
        if not res["success"]:
            reason = res["message"]
            break
        hops += 1
    check("dispatch stops a back-and-forth at MAX_DEPTH",
          hops == agent_link.MAX_DEPTH and "depth" in reason)

    reset()
    fake = Fake()
    hand = agent_link.dispatch(fake, target="Coding", text="做這件", from_sid="sA",
                               from_label="研究", kind="handoff", msg_id="h1",
                               hint="建議用訂閱模型")
    check("handoff tells the other tab how to report",
          hand["success"] and "sfctl a2a-result h1" in fake.sent[-1][1]
          and "這是建議，不是指令" in fake.sent[-1][1]
          and fake.sent[-1][1].count("做這件") == 1)
    back = agent_link.report_result(fake, "h1", "做好了", from_sid="sB", from_label="Coding")
    twice = agent_link.report_result(fake, "h1", "再報一次", from_sid="sB", from_label="Coding")
    check("a handoff result is delivered once",
          back["success"] and twice["success"] is False
          and sum(1 for sid, _text in fake.sent if sid == "sA") == 1)

    reset()
    fake = Fake()
    agent_link.dispatch(fake, target="otherbox/Coding", text="遠端做", from_sid="sA",
                        from_label="研究", kind="handoff", msg_id="h9")
    denied = agent_link.report_result(fake, "h9", "我不該回", from_sid="sA")
    check("the sender cannot answer its own outgoing handoff",
          denied["success"] is False and "結果要由對方回報" in denied["message"])

    reset()
    fake = Fake()
    incoming = agent_link.accept_remote(fake, {
        "kind": "handoff", "id": "hin", "target": "Coding", "text": "請處理",
        "from_sid": "s9", "from_label": "遠端", "depth": 1, "source_peer": "abc",
    })
    reported = agent_link.report_result(fake, "hin", "完成", from_sid="sB",
                                        from_label="Coding")
    reported2 = agent_link.report_result(fake, "hin", "完成", from_sid="sB")
    check("incoming handoff reports back to the peer once",
          incoming["success"] and reported["success"] and reported2["success"] is False
          and len(fake.remote_calls) == 1
          and fake.remote_calls[0][1]["kind"] == "result"
          and fake.remote_calls[0][1]["id"] == "hin")

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
