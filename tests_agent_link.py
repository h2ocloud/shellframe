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
import a2a_ssh  # noqa: E402

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
            self.ssh_calls = []
            self.workers_map = {}
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

        def workers(self):
            return self.workers_map

        def run_ssh(self, argv):
            self.ssh_calls.append(list(argv))
            return {"code": 0, "out": "planned"}

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

    # Claude 分頁與 Cursor 分頁：結果可以是 diff 摘要，下一步回到同一個目標。
    reset()
    fake = Fake()
    pair = agent_link.dispatch(fake, target="Coding", text="照計畫改", from_sid="sA",
                               from_label="研究", kind="handoff", msg_id="pair1")
    early = agent_link.continue_handoff(fake, "pair1", "再改一處", from_sid="sA",
                                        from_label="研究")
    diff = agent_link.report_result(fake, "pair1", "main.py +3", from_sid="sB",
                                    from_label="Coding", diff=True)
    nxt = agent_link.continue_handoff(fake, "pair1", "把測試補上", from_sid="sA",
                                      from_label="研究")
    worker = agent_link.continue_handoff(fake, "pair1", "我自己接", from_sid="sB",
                                         from_label="Coding")
    sent_to_b = [text for sid, text in fake.sent if sid == "sB"]
    check("claude to cursor: diff summary then the next step returns",
          pair["success"] and early["success"] is False and "還沒回報" in early["message"]
          and diff["success"] and "diff 摘要：" in fake.sent[1][1]
          and nxt["success"] and nxt["details"].get("continues") == "pair1"
          and any(text.startswith("延續 pair1。") or "延續 pair1。" in text for text in sent_to_b)
          and worker["success"] is False and "交辦方" in worker["message"])

    reset()
    fake = Fake()
    agent_link.dispatch(fake, target="otherbox/Coding", text="遠端實作", from_sid="sA",
                        from_label="研究", kind="handoff", msg_id="pair9")
    agent_link.report_result(fake, "pair9", "diff ready", from_sid="sB",
                             from_label="Coding", remote_return=True, diff=True)
    remote_next = agent_link.continue_handoff(fake, "pair9", "下一步", from_sid="sA",
                                              from_label="研究")
    check("cross-machine next step goes to the same peer",
          remote_next["success"] and len(fake.remote_calls) == 2
          and fake.remote_calls[-1][0] == "abc"
          and fake.remote_calls[-1][1]["text"].startswith("延續 pair9。")
          and remote_next["details"].get("continues") == "pair9")

    reset()
    fake = Fake()
    fake.roster_map["總管"] = {"label": "總管", "coordinator": True}
    fake.sessions["sC"] = "總管"
    blocked = agent_link.dispatch(fake, target="總管", text="請你實作", from_sid="sA",
                                  from_label="研究", kind="handoff")
    check("a coordinator only dispatches and accepts",
          blocked["success"] is False and "總管只派工與驗收" in blocked["message"]
          and fake.sent == [] and fake.ssh_calls == [])

    reset()
    fake = Fake()
    fake.workers_map = {
        "spark": a2a_ssh.normalize("spark", {"target": "spark-box", "shell": "posix"}),
        "site": a2a_ssh.normalize("site", {
            "target": "site-box", "shell": "powershell", "production": True, "mode": "plan"}),
    }
    held = agent_link.dispatch(fake, target="worker:site", text="重啟 Apache",
                               from_sid="sA", from_label="總管", kind="handoff")
    check("a production restart is refused before ssh",
          held["success"] is False and "restart" in held["message"] and fake.ssh_calls == [])
    planned = agent_link.dispatch(fake, target="worker:site", text="只讀看設定",
                                  from_sid="sA", from_label="總管", kind="handoff",
                                  msg_id="w1")
    writable = agent_link.dispatch(
        fake, target="worker:site", text="改一行設定", from_sid="sA",
        from_label="總管", kind="handoff", msg_id="w2", allow_write=True)
    plan_toks = fake.ssh_calls[0][-1].split()
    write_toks = fake.ssh_calls[1][-1].split()
    check("production worker stays read-only until writes are authorized",
          planned["success"] and planned["details"]["mode"] == "plan"
          and "--mode" in plan_toks and "plan" in plan_toks and "--force" not in plan_toks
          and "planned" in fake.sent[-1][1] and fake.sent[-1][0] == "sA"
          and writable["details"]["mode"] == "agent"
          and "--force" not in write_toks and "--mode" not in write_toks)
    nxt = agent_link.continue_handoff(fake, "w1", "看下一個檔", from_sid="sA",
                                      from_label="總管")
    check("the next step runs on the same ssh worker",
          nxt["success"] and nxt["details"].get("continues") == "w1"
          and "延續 w1。" in fake.ssh_calls[-1][-1])

    # ── ssh 在背景跑：dispatch 先回，結果之後才進交辦分頁 ──
    reset()
    fake = Fake()
    fake.workers_map = {
        "spark": a2a_ssh.normalize("spark", {"target": "spark-box", "shell": "posix"}),
    }
    queued = []
    fake.spawn = queued.append
    quick = agent_link.dispatch(fake, target="worker:spark", text="查一下 log",
                                from_sid="sA", from_label="總管", kind="handoff",
                                msg_id="a1")
    early = agent_link.continue_handoff(fake, "a1", "再查", from_sid="sA",
                                        from_label="總管")
    check("a worker handoff returns before ssh runs",
          quick["success"] and "ssh" in quick["message"] and len(queued) == 1
          and fake.ssh_calls == [] and fake.sent == []
          and early["success"] is False and "還沒回報" in early["message"])
    queued[0]()
    after = agent_link.continue_handoff(fake, "a1", "再查", from_sid="sA",
                                        from_label="總管")
    check("the ssh result lands in the requesting tab, then the next step opens",
          len(fake.ssh_calls) == 1 and fake.sent[0][0] == "sA"
          and "planned" in fake.sent[0][1] and after["success"]
          and after["details"].get("continues") == "a1")

    reset()
    fake = Fake()
    fake.workers_map = {
        "spark": a2a_ssh.normalize("spark", {"target": "spark-box", "shell": "posix"}),
    }
    fake.run_ssh = lambda argv: {"code": 255, "out": "", "err": "boom"}
    queued = []
    fake.spawn = queued.append
    agent_link.dispatch(fake, target="worker:spark", text="會失敗的任務", from_sid="sA",
                        from_label="總管", kind="handoff", msg_id="f1")
    queued[0]()
    gone = agent_link.continue_handoff(fake, "f1", "再試", from_sid="sA",
                                       from_label="總管")
    check("a failed ssh run tells the requesting tab and drops the handoff",
          fake.sent and fake.sent[-1][0] == "sA" and "執行失敗" in fake.sent[-1][1]
          and "boom" in fake.sent[-1][1]
          and gone["success"] is False and "unknown handoff" in gone["message"])
    again = agent_link.dispatch(fake, target="worker:spark", text="會失敗的任務",
                                from_sid="sA", from_label="總管", kind="handoff")
    check("a failed run releases the fingerprint so the same task can be retried",
          again["success"] and len(queued) == 2)

    reset()
    fake = Fake()
    fake.workers_map = {
        "spark": a2a_ssh.normalize("spark", {"target": "spark-box", "shell": "posix"}),
    }
    fake.run_ssh = lambda argv: {"code": 0, "out": "x" * (agent_link.MAX_CHARS + 500)}
    agent_link.dispatch(fake, target="worker:spark", text="輸出很長", from_sid="sA",
                        from_label="總管", kind="handoff")
    delivered = fake.sent[-1][1]
    check("worker output over MAX_CHARS is truncated and marked",
          "已截斷" in delivered and "x" * agent_link.MAX_CHARS in delivered
          and "x" * (agent_link.MAX_CHARS + 1) not in delivered)

    # 總管收訊息與結果，只是不接交辦。用 sid 指到也一樣。
    reset()
    fake = Fake()
    fake.roster_map["總管"] = {"label": "總管", "coordinator": True}
    fake.sessions["s7"] = "總管"
    report = agent_link.dispatch(fake, target="總管", text="工作完成回報", from_sid="sB",
                                 from_label="Coding")
    by_sid = agent_link.accept_remote(fake, {
        "kind": "handoff", "id": "hc", "target": "s7", "text": "請你實作",
        "from_sid": "s9", "from_label": "遠端", "depth": 1, "source_peer": "abc"})
    check("a worker can message the coordinator but cannot hand work to it",
          report["success"] and fake.sent[0][0] == "s7"
          and "工作完成回報" in fake.sent[0][1]
          and by_sid["success"] is False and "總管只派工與驗收" in by_sid["message"]
          and len(fake.sent) == 1)
    retry = agent_link.accept_remote(fake, {
        "kind": "send", "id": "hc", "target": "s7", "text": "請你實作",
        "from_sid": "s9", "from_label": "遠端", "depth": 1, "source_peer": "abc"})
    check("a refused coordinator handoff releases its id and fingerprint",
          retry["success"] and len(fake.sent) == 2
          and ("peer:abc", "handoff", "s7", "請你實作") not in agent_link._fingerprints)

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
