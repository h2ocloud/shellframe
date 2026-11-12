#!/usr/bin/env python3
"""A send from the chat view while the terminal's input box holds a draft (#35).

Reported in daily use: with text left in the terminal (a right-click had pasted it there),
a message sent from the chat view said it was queued and never went in. Measured: it was
queued behind the draft, and 30 minutes later dropped with one debug line ("not delivered
(draft) … from ?"); the bubble that said "waiting" went away and nobody was told.

  - chat_send with a draft in the box sends nothing and answers {"code": "draft", "draft":
    its first 60 characters}: the view asks what to do (a dim suggestion is not a draft)
  - "clear it and send": the draft goes with one Ctrl+C (measured in Claude Code: one empties
    a draft of any length, Ctrl+U only the cursor's line, and a second Ctrl+C quits), then
    the text goes in; while the tab is working the Ctrl+C waits for its turn to end; a
    draft that does not clear stops the send, with no second Ctrl+C
  - a queued send carries an id: chat_view's `queue` lists it while it waits, and once it is
    given up, with why; a chat send is never just gone
  - the send command passes the chat marker and the clear on to the queue
Run: .venv/bin/python tests_chat_draft_send.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-chat-draft-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")  # fake tabs stay out of the real log

import api_chat  # noqa: E402
import tab_delivery as td  # noqa: E402

td.POLL, td.TAKEN_WAIT = 0.05, 0.2
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


RULE = "─" * 40
box = lambda *rows: "\n".join(["some conversation", RULE, *rows, RULE, "  footer"])
EMPTY = box("\x1b[39m❯\xa0\x1b[2mTry \"edit <filepath> to...\"\x1b[0m")       # a dim suggestion only
DRAFT = "終端機裡的草稿（右鍵誤貼上來的）"


class Tab:
    def __init__(self, sid, api):
        self.sid, self.api = sid, api
        self._tmux_name = None
        self._startup_trust_pending = False

    def write(self, data):
        self.api.keys.append((self.sid, data))
        if data == td.CLEAR_KEY and not self.api.stubborn:
            self.api.screens[self.sid] = EMPTY          # the CLI empties its box


class FakeApi:
    """What tab_delivery reads: tabs, their state and screen, a way to type."""
    def __init__(self, *sids):
        self.sessions = {sid: Tab(sid, self) for sid in sids}
        self.screens = {sid: EMPTY for sid in sids}
        self.busy, self.sent, self.keys, self.stubborn = set(), [], [], False

    def _agent_state_for_list(self, sid):
        return "working" if sid in self.busy else ""

    def _bridge_session_busy(self, sid):
        return False

    def _send_text_to_session(self, s, text, submit=False):
        self.sent.append((s.sid, text))

    def _agent_status_snapshot(self, sid):
        return ({"prompt_at": time.time()}, 0)


td.capture = lambda api, sid: api.screens.get(sid, "")
ctrl_c = lambda app, sid: sum(1 for s, k in app.keys if s == sid and k == td.CLEAR_KEY)

print("1. reading the box")
app = FakeApi("s1")
check("a dim suggestion is no draft", td.draft_of(app, "s1") == "")
app.screens["s1"] = box("\x1b[39m❯\xa0" + DRAFT)
check("typed text is", td.draft_of(app, "s1") == DRAFT, td.draft_of(app, "s1"))

print("2. clear it and send")
r = td.deliver(app, "s1", "從對話畫面送出的訊息", what="chat", clear_draft=True)
check("the tab is idle: one Ctrl+C, then the text goes in at once",
      r["success"] and not r.get("queued") and ctrl_c(app, "s1") == 1 and app.sent == [("s1", "從對話畫面送出的訊息")], (r, app.keys, app.sent))
app = FakeApi("s1")
app.screens["s1"] = box("\x1b[39m❯\xa0" + DRAFT)
app.busy.add("s1")
r = td.deliver(app, "s1", "等它做完再送", what="chat", clear_draft=True)
check("the tab is working: queued, no Ctrl+C yet (it would stop the turn)",
      r.get("queued") and r.get("reason") == "working" and ctrl_c(app, "s1") == 0, (r, app.keys))
time.sleep(0.2)
check("…still none while it works", ctrl_c(app, "s1") == 0 and not app.sent)
app.busy.discard("s1")
check("…its turn ends: one Ctrl+C, then the text",
      wait_for(lambda: app.sent == [("s1", "等它做完再送")]) and ctrl_c(app, "s1") == 1, (app.keys, app.sent))
app = FakeApi("s1")
app.screens["s1"] = box("\x1b[39m❯\xa0" + DRAFT)
app.stubborn = True
r = td.deliver(app, "s1", "清不掉就不送", what="chat", clear_draft=True)
check("a draft that does not clear: nothing sent, one Ctrl+C and no second",
      not r["success"] and r.get("code") == "draft_stuck" and not app.sent and ctrl_c(app, "s1") == 1, (r, app.keys))

print("3. a queued send is followed, and never just gone")
app = FakeApi("s2")
app.busy.add("s2")
r = td.deliver(app, "s2", "排隊的訊息", what="chat", max_wait=0.3)
check("queued with an id", r.get("queued") and len(r.get("id") or "") == 12, r)
check("…chat_view's queue lists it while it waits", td.queue_state("s2")["queued"] == [r["id"]], td.queue_state("s2"))
given = wait_for(lambda: td.queue_state("s2")["given_up"])
st = td.queue_state("s2")
check("…once its time runs out it is given up, with why, and no longer waiting",
      given and st["queued"] == [] and st["given_up"][0]["id"] == r["id"] and st["given_up"][0]["why"] == td._reason_text("working"), st)
r2 = td.deliver(app, "s2", "sfctl 送的", max_wait=0.3)
time.sleep(0.8)
check("…a send from sfctl or A2A is told to its sender as before, not listed for the view",
      [g["id"] for g in td.queue_state("s2")["given_up"]] == [r["id"]], td.queue_state("s2"))
app.busy.discard("s2")

print("4. chat_send")


class Host(api_chat.ChatApiMixin):
    def __init__(self, app):
        self.sessions, self.app, self.calls = app.sessions, app, []
        self.screens = app.screens

    def _execute_sfctl(self, cmd, args):
        self.calls.append((cmd, args))
        return {"success": True, "message": "ok"}

    def _auto_name(self, sid, s, text):
        pass


app = FakeApi("s3")
host = Host(app)
app.screens["s3"] = box("\x1b[39m❯\xa0" + DRAFT)
res = json.loads(host.chat_send("s3", "對話畫面的訊息"))
check("a draft in the box: nothing sent, the view gets the draft to show",
      res == {"success": False, "code": "draft", "draft": DRAFT} and host.calls == [], (res, host.calls))
long = "這是一段很長的草稿" * 12
app.screens["s3"] = box("\x1b[39m❯\xa0" + long)
res = json.loads(host.chat_send("s3", "對話畫面的訊息"))
check("…its first 60 characters, cut with …", res["draft"] == long[:60] + "…", res["draft"])
res = json.loads(host.chat_send("s3", "對話畫面的訊息", True))
check("clear it and send: the send goes on, marked as the chat view's, asking for the clear",
      res["success"] and host.calls[-1] == ("send", {"sid": "s3", "text": "對話畫面的訊息", "submit": True,
                                                    "what": "chat", "clear_draft": True}), host.calls)
app.screens["s3"] = EMPTY
host.calls.clear()
json.loads(host.chat_send("s3", "沒有草稿"))
check("no draft (a dim suggestion): sent as usual, nothing to clear",
      host.calls == [("send", {"sid": "s3", "text": "沒有草稿", "submit": True, "what": "chat", "clear_draft": False})],
      host.calls)

print("5. the send command passes them on")
import api_remote  # noqa: E402
got = {}


def fake_deliver(api, sid, text, **kw):
    got.update(kw)
    return {"success": True, "queued": True, "position": 1, "id": "abc123abc123", "reason": "working",
            "message": "queued"}


real_deliver, td.deliver = td.deliver, fake_deliver
remote = api_remote.RemoteApiMixin.__new__(api_remote.RemoteApiMixin)
remote.sessions = {"s4": Tab("s4", FakeApi())}
remote._session_label = lambda sid, s: "示範分頁"
out = api_remote.RemoteApiMixin._execute_sfctl(remote, "send", {"sid": "s4", "text": "x", "what": "chat",
                                                                "clear_draft": True})
td.deliver = real_deliver
check("what='chat' and clear_draft reach the queue", got.get("what") == "chat" and got.get("clear_draft") is True, got)
check("…and the id and the reason come back", out.get("details") == {"queued": True, "position": 1,
                                                                     "id": "abc123abc123", "reason": "working"}, out)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
