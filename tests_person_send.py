#!/usr/bin/env python3
"""A message the person wrote goes in while the tab works, as text typed in the terminal does.

Asked for in daily use: a message from the phone or the chat view waited in ShellBot's queue
until the tab's turn ended (up to 30 min), though the AI CLIs keep a message submitted mid-turn
themselves. Measured in Claude Code 2.1.325: a message pasted with its Enter during a 15 s tool
call showed as queued and was taken right after that tool call, inside the same turn; that turn
never wrote its own last reply.
  - the person's message (chat view, a phone's /link/send): a working tab gets it at once
  - an agent's too, since v0.45.2 (asked for: the queue was mostly unnecessary); never over the
    person's own words in the input box, and a memory save (queue=False) still waits for a free tab
  - still held for the person: a decision menu (its keys would pick an option), a model being
    switched from the screen, a draft to clear (a Ctrl+C would stop the turn), a bridged message
    being typed in, a LINE message waiting for its reply (the LINE bridge sends the tab's next
    output), and a Telegram message whose turn runs or whose reply the bridge still waits for
  - who counts as the person: the chat view (chat_send) and a phone (from_phone); nothing else

Run: .venv/bin/python tests_person_send.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-person-send-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")
import agent_link  # noqa: E402
agent_link.STATE_DIR = TMP / "state"
agent_link.LOG_FILE = agent_link.STATE_DIR / "agent_link.json"
agent_link.STATE_DIR.mkdir(parents=True, exist_ok=True)
import main  # noqa: E402
import api_remote  # noqa: E402
import tab_delivery as td  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {}}), encoding="utf-8")
td.POLL, td.TAKEN_WAIT = 0.05, 0.4

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class Tab:
    def __init__(self, sid):
        self.sid, self._custom_label, self.cmd = sid, "demo", "claude"
        self.alive, self.lock, self._recent = True, threading.Lock(), bytearray()
        self._tmux_name = None
        self._startup_trust_pending = True


def make_api():
    api = main.Api.__new__(main.Api)
    api.sessions = {"s1": Tab("s1")}
    api.bridge = api.line_bridge = None
    api._hook_events, api._bridged_turn_said = {}, {}
    api._window = None
    api.state, api.screen, api.sent = {"s1": ""}, {"s1": ""}, []
    api._send_text_to_session = lambda s, text, submit=False: api.sent.append(text)
    api._agent_state_for_list = lambda sid: api.state.get(sid, "")
    api._agent_status_snapshot = lambda sid: ({"prompt_at": 0}, 0)
    return api


td.capture = lambda api, sid: api.screen.get(sid, "")


def clear_queue():
    for sid in list(td._queues):
        for it in list(td._queues.get(sid, ())):
            it["cancelled"] = True
    time.sleep(0.2)
    td._queues.clear()


def slot(**kw):
    base = dict(inject_pending=False, pending_target_id="", has_user_msg=False, marker_forwarded=False,
                awaiting_response=False, expect_marker=False, msg_sent_ts=0.0)
    base.update(kw)
    return types.SimpleNamespace(**base)


print("a working tab")
api = make_api()
api.state["s1"] = "working"
r = td.deliver(api, "s1", "from the phone", person=True)
check("the person's message goes in at once", r["success"] and not r["queued"] and api.sent == ["from the phone"], (r, api.sent))
r = td.deliver(api, "s1", "from an agent")
check("an agent's too (v0.45.2: chosen for every message)", not r["queued"] and api.sent[-1] == "from an agent", r)
api.screen["s1"] = "\n".join(["x", "─" * 40, "\x1b[39m❯\xa0我正在打的字", "─" * 40, "  footer"])
r = td.deliver(api, "s1", "another agent message")
check("…but not over the person's own words while it works: those first", r["queued"] and r["reason"] == "draft"
      and api.sent[-1] == "from an agent", r)
clear_queue()
r = td.deliver(api, "s1", "memory save", queue=False)
api.screen["s1"] = ""
r = td.deliver(api, "s1", "memory save", queue=False)
check("a memory save (queue=False) still waits for a free tab", r["success"] is False and r["reason"] == "working", r)

print("what still holds the person's message")
cases = []
api = make_api()
api.state["s1"] = "decision"
cases.append(("a decision menu (its keys would pick an option)", api, {}))
api = make_api()
api.state["s1"] = "working"
td.HOLD["s1"] = "model"
cases.append(("a model being switched from the screen", api, {}))
for name, a, kw in cases:
    r = td.deliver(a, "s1", "x", person=True, **kw)
    check(name, r["queued"] and a.sent == [], (r, a.sent))
    clear_queue()
td.HOLD.pop("s1", None)

api = make_api()
api.state["s1"] = "working"
r = td.deliver(api, "s1", "x", person=True, clear_draft=True)
check("a draft to clear while the tab works (the Ctrl+C would stop the turn)", r["queued"] and api.sent == [], r)
clear_queue()

api = make_api()
api.state["s1"] = "working"
api.bridge = types.SimpleNamespace(slots={"s1": slot(inject_pending=True)})
r = td.deliver(api, "s1", "x", person=True)
check("a Telegram message being typed in (two pastes would run together)", r["queued"] and api.sent == [], r)
clear_queue()

api = make_api()
api.state["s1"] = "working"
api.line_bridge = types.SimpleNamespace(slots={"s1": slot(pending_target_id="U-demo")})
r = td.deliver(api, "s1", "x", person=True)
check("a LINE message waiting for its reply (the bridge sends the tab's next output)", r["queued"] and api.sent == [], r)
clear_queue()

api = make_api()
api.state["s1"] = "done"
api.bridge = types.SimpleNamespace(slots={"s1": slot(has_user_msg=True, awaiting_response=True, expect_marker=True,
                                                     msg_sent_ts=time.time() - 10)})
r = td.deliver(api, "s1", "x", person=True)
check("a Telegram reply the bridge still waits for after the turn", r["queued"] and r["reason"] == "telegram", r)
clear_queue()

api = make_api()
api.state["s1"] = "working"
api.bridge = types.SimpleNamespace(slots={"s1": slot(has_user_msg=True, awaiting_response=True, expect_marker=True,
                                                     msg_sent_ts=time.time() - 10)})
r = td.deliver(api, "s1", "x", person=True)
check("a Telegram turn still running (the message would join it, and its reply could be lost)",
      r["queued"] and api.sent == [], r)
clear_queue()

print("who counts as the person")
got = []
real_deliver = td.deliver
td.deliver = lambda api, sid, text, **kw: (got.append(kw.get("person")), {"success": True, "queued": False})[1]
remote = api_remote.RemoteApiMixin.__new__(api_remote.RemoteApiMixin)
remote.sessions = {"s1": Tab("s1")}
remote._session_label = lambda sid, s: "demo"
api_remote.RemoteApiMixin._execute_sfctl(remote, "send", {"sid": "s1", "text": "x", "from_phone": True})
api_remote.RemoteApiMixin._execute_sfctl(remote, "send", {"sid": "s1", "text": "x", "what": "chat", "person": True})
api_remote.RemoteApiMixin._execute_sfctl(remote, "send", {"sid": "s1", "text": "x"})
api_remote.RemoteApiMixin._execute_sfctl(remote, "send", {"sid": "s1", "text": "x", "from_sid": "s9"})
td.deliver = real_deliver
check("a phone (from_phone) and the chat view (person) are; sfctl send and A2A are not",
      got == [True, True, False, False], got)
src = (HERE / "api_chat.py").read_text(encoding="utf-8")
body = src.split("def chat_send(", 1)[1].split("\n    def ", 1)[0]
check("chat_send marks its send as the person's", '"person": True' in body)
fl = (HERE / "frame_link.py").read_text(encoding="utf-8")
local_send = fl.split("def _local_send(", 1)[1].split("\n    def ", 1)[0]
check("a phone's /link/send passes from_phone for a peer of kind ios",
      '"from_phone": peer.get("kind") == "ios"' in local_send)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
