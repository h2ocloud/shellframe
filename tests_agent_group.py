#!/usr/bin/env python3
"""角色群組（實驗性）回歸測試。

群發會同時驅動好幾個 bypass-permissions 的 agent，所以「誰能被加進群」「訊息長
什麼樣」「合併後的對話能不能分辨誰是誰」這三件事必須是可預期的。

跑法：.venv/bin/python tests_agent_group.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_group  # noqa: E402

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


ROSTER = {"Coding": {}, "研究": {}, "時程信件": {}}


def main():
    # ── 只能用名冊裡有的角色 ──
    ok, _, _, err = agent_group.validate("小隊", ["Coding", "不存在"], ROSTER)
    check("unknown role refused", ok is False and "不在名冊" in err)
    ok, _, roles, _ = agent_group.validate("小隊", ["Coding", "研究"], ROSTER)
    check("valid roles accepted", ok is True and roles == ["Coding", "研究"])

    # ── 名稱與成員的邊界 ──
    ok, _, _, err = agent_group.validate("  ", ["Coding"], ROSTER)
    check("blank name refused", ok is False)
    ok, _, _, err = agent_group.validate("小隊", [], ROSTER)
    check("empty group refused", ok is False and "至少" in err)
    ok, _, roles, _ = agent_group.validate("小隊", ["Coding", "Coding", "研究"], ROSTER)
    check("duplicate members collapse", ok is True and roles == ["Coding", "研究"])
    big = {f"r{i}": {} for i in range(agent_group.MAX_MEMBERS + 2)}
    ok, _, _, err = agent_group.validate("大隊", list(big), big)
    check(f"capped at {agent_group.MAX_MEMBERS} members",
          ok is False and str(agent_group.MAX_MEMBERS) in err)

    # ── 壞掉的設定不能讓功能爆掉 ──
    g = agent_group.normalize({"好": {"roles": ["Coding"]}, "壞": "not a dict", 7: None})
    check("malformed config is tolerated", list(g.keys()) == ["好"])
    check("normalized entry keeps its roles", g["好"]["roles"] == ["Coding"])

    # ── 送出去的內容 ──
    body = agent_group.format_group_message("小隊", ["Coding", "研究"], "Coding", "查一下這個")
    check("message says it is a group message", "群組訊息" in body)
    check("message names the other members", "研究" in body and "Coding" not in body.split("還有：")[1].split("]]")[0])
    # SF:TO is agent-to-agent messaging, which is a *separate* experimental
    # flag. Offering it while a2a is off would point members at a marker that is
    # refused before delivery, so it only appears when a2a is on too.
    check("no SF:TO hint while a2a is off", "SF:TO:" not in body)
    with_a2a = agent_group.format_group_message("小隊", ["Coding", "研究"], "Coding",
                                                "查一下這個", a2a=True)
    check("SF:TO hint appears once a2a is on", "SF:TO:" in with_a2a)
    check("a one-member group gets no SF:TO even with a2a on",
          "SF:TO:" not in agent_group.format_group_message("獨", ["Coding"], "Coding",
                                                           "hi", a2a=True))
    check("the user's text is last", body.strip().endswith("查一下這個"))
    solo = agent_group.format_group_message("獨", ["Coding"], "Coding", "hi")
    check("a one-member group lists no peers", "還有" not in solo)

    # ── 合併對話 ──
    merged = agent_group.merge_turns({
        "Coding": [{"kind": "assistant_text", "ts": 30, "text": "b"},
                   {"kind": "assistant_text", "ts": 10, "text": "a"}],
        "研究": [{"kind": "assistant_text", "ts": 20, "text": "c"}],
    })
    check("merged in time order", [t["text"] for t in merged] == ["a", "c", "b"])
    check("every turn says who spoke",
          [t["speaker"] for t in merged] == ["Coding", "研究", "Coding"])
    check("limit keeps the most recent",
          [t["text"] for t in agent_group.merge_turns({
              "Coding": [{"ts": 1, "text": "x"}, {"ts": 2, "text": "y"}]}, limit=1)] == ["y"])
    check("junk turns are skipped",
          agent_group.merge_turns({"Coding": [None, "nope", {"ts": 1, "text": "ok"}]})[0]["text"] == "ok")

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
