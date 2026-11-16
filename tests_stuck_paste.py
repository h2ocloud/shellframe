#!/usr/bin/env python3
"""Words ShellBot typed into a tab whose Enter did not take are not the person's draft (tab_delivery).

Measured 2026-11-15: at 12:25:53 a message typed into a tab (paste, then Enter) never reached the CLI:
no UserPromptSubmit followed and the words stayed in the input box. The draft check read them as the
person typing, and every message to that tab waited behind them for 21 hours; nobody was told. Their
Enter came when someone pressed Esc twice the next morning, and the words were gone.
  - after typing a message in, ShellBot waits for the tab to take it; when the box still holds those
    words, it presses Enter once more (queued sends and direct ones alike)
  - when that does not take either, the sender is told it is stuck in the box
  - words ShellBot typed in earlier that are still in the box get their Enter; what was waiting goes in
  - the person's own unsent words still hold the queue; after 10 minutes the sender is told, once

Run: .venv/bin/python tests_stuck_paste.py
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-stuck-paste-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")
import agent_link  # noqa: E402
agent_link.STATE_DIR = TMP / "state"
agent_link.LOG_FILE = agent_link.STATE_DIR / "agent_link.json"
agent_link.STATE_DIR.mkdir(parents=True, exist_ok=True)
import tab_delivery as td  # noqa: E402

td.POLL, td.TAKEN_WAIT, td.DRAFT_ALERT_S = 0.05, 0.4, 0.6
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


RULE = "─" * 40
box = lambda words="": "\n".join(["some conversation", RULE, "\x1b[39m❯\xa0" + words, RULE, "  footer"])


class Tab:
    def __init__(self, sid):
        self.sid, self._custom_label, self.cmd = sid, sid, "claude"
        self.alive, self.lock, self._recent, self._tmux_name = True, threading.Lock(), bytearray(), None
        self._startup_trust_pending = False


class Api:
    """`enters_lost`: how many Enters a tab ignores before one takes (the bug: the paste lands, its Enter does not)."""
    def __init__(self, *sids):
        self.sessions = {sid: Tab(sid) for sid in sids}
        self.bridge = self.line_bridge = None
        self.state = {sid: "done" for sid in sids}
        self.screen = {sid: box() for sid in sids}
        self.prompt_at = {sid: 0.0 for sid in sids}
        self.enters_lost = {sid: 0 for sid in sids}
        self.typed = []                         # (sid, text, submit)

    def _send_text_to_session(self, s, text, submit=False):
        self.typed.append((s.sid, text, submit))
        if text:
            self.screen[s.sid] = box(text)
        if submit:
            if self.enters_lost[s.sid] > 0:
                self.enters_lost[s.sid] -= 1   # the words stay in the box
            else:
                self.screen[s.sid] = box()
                self.prompt_at[s.sid] = time.time()
                self.state[s.sid] = "working"

    def _agent_state_for_list(self, sid):
        return self.state.get(sid, "")

    def _agent_status_snapshot(self, sid):
        return ({"prompt_at": self.prompt_at.get(sid, 0)}, 0)

    def _bridge_session_busy(self, sid):
        return False


td.capture = lambda api, sid: api.screen.get(sid, "")
enters = lambda api, sid: [t for t in api.typed if t[0] == sid and t[1] == "" and t[2]]
notes = lambda api: [t[1] for t in api.typed if t[0] == "s9" and t[1]]

print("a queued message whose Enter did not take (the 11/15 case)")
api = Api("s1", "s9")
api.state["s1"] = "working"
api.enters_lost["s1"] = 1
r = td.deliver(api, "s1", "離線打字互傳做完了，請看一下結果", from_sid="s9", label="M4")
check("queued while the tab works", r["queued"])
api.state["s1"] = "done"
check("its Enter is pressed again once the box still holds it", wait_for(lambda: len(enters(api, "s1")) == 1), api.typed)
check("…and the tab takes it", wait_for(lambda: api.prompt_at["s1"] > 0))
api.state["s1"] = "done"
r = td.deliver(api, "s1", "下一則", from_sid="s9", label="M4")
check("the next message is not held behind it", not r["queued"] and api.typed[-1][1] == "下一則", r)
check("the sender was not told anything", notes(api) == [], notes(api))

print("a direct send whose Enter did not take")
api = Api("s1", "s9")
api.enters_lost["s1"] = 1
r = td.deliver(api, "s1", "請整理今天的會議重點", from_sid="s9", label="M4")
check("it goes in", not r["queued"])
check("its Enter is pressed again, and it is taken", wait_for(lambda: api.prompt_at["s1"] > 0) and len(enters(api, "s1")) == 1,
      api.typed)

print("an Enter that never takes")
api = Api("s1", "s9")
api.enters_lost["s1"] = 5
td.deliver(api, "s1", "這則送不進去", from_sid="s9", label="M4")
check("the sender is told it is stuck in the box", wait_for(lambda: any("Enter 沒有生效" in n for n in notes(api)), 6),
      notes(api))
check("…once, with what to do", len(notes(api)) == 1 and "sfctl peek" in notes(api)[0], notes(api))

print("ShellBot's own words left in the box from before")
api = Api("s4", "s9")
td._remember("s4", "昨天沒送出去的那則訊息")
api.screen["s4"] = box("昨天沒送出去的那則訊息")
check("busy_reason still reads a draft", td.busy_reason(api, "s4") == "draft")
r = td.deliver(api, "s4", "今天的新訊息", from_sid="s9", label="M4")
check("a new message is queued behind it", r["queued"])
check("the old words get their Enter", wait_for(lambda: len(enters(api, "s4")) == 1), api.typed)
api.state["s4"] = "done"                       # the tab answered them
check("…and the new one goes in after", wait_for(lambda: api.typed[-1][1] == "今天的新訊息"), api.typed)

print("the person's own unsent words")
api = Api("s5", "s9")
api.screen["s5"] = box("我自己正在打的字")
r = td.deliver(api, "s5", "agent 的訊息", from_sid="s9", label="M4", max_wait=3)
check("held by them", r["queued"] and r["reason"] == "draft", r)
check("after the alert time the sender is told what holds it", wait_for(lambda: any("還在等" in n for n in notes(api)), 3),
      notes(api))
time.sleep(1.0)
check("…once; no Enter was pressed on the person's words",
      sum("還在等" in n for n in notes(api)) == 1 and enters(api, "s5") == [], (notes(api), enters(api, "s5")))
check("the words are still there", "我自己正在打的字" in api.screen["s5"])

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
