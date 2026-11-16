#!/usr/bin/env python3
"""Pictures go in with their message, as one message (frame_link /link/send `images`, tab_delivery).

Reported: a picture attached in the app never showed up. Found on the way (Coding, 11/16): the app
pasted the picture first (POST /link/paste, no Enter; Claude Code shows it as [Image #1]) and then sent
the words; the draft check read [Image #1] as the person's unsent words, so the words waited or the
picture was cleared. Now the pictures travel with the message and are typed in only when it goes in.
  - /link/send `images`: each is saved on this computer; when the message goes in, each picture is pasted
    (its path, bracketed, as a picture dropped in the terminal), then the words, then Enter
  - queued, it waits whole; sent now, it goes in whole; withdrawn, no picture is typed
  - pictures with no words: they go in with an Enter
  - a stuck Enter after ShellBot's own pictures gets pressed again; the person's [Image #1] is theirs
Demo data only. Run: .venv/bin/python tests_send_images.py
"""
import base64
import os
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.modules.setdefault("webview", MagicMock())
sys.path.insert(0, str(HERE))
import tab_delivery as td  # noqa: E402

td.POLL, td.TAKEN_WAIT, td.IMAGE_SETTLE = 0.05, 0.3, 0.0
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class Tab:
    def __init__(self, sid, log):
        self.sid, self._startup_trust_pending, self.log = sid, False, log

    def write(self, data):
        self.log.append(("paste", data))


class Api:
    def __init__(self, sid):
        self.log = []
        self.sessions = {sid: Tab(sid, self.log)}
        self.state, self.bridge, self.line_bridge = {sid: ""}, None, None
        self.prompt_at = {sid: 0.0}

    def _send_text_to_session(self, s, text, submit=False):
        self.log.append(("words", text, submit))
        if submit:
            self.prompt_at[s.sid] = time.time()

    def _agent_state_for_list(self, sid):
        return self.state.get(sid, "")

    def _agent_status_snapshot(self, sid):
        return ({"prompt_at": self.prompt_at.get(sid, 0)}, 0)

    def _bridge_session_busy(self, sid):
        return False


td.capture = lambda api, sid: ""
P1, P2 = "/tmp/sf-demo/a.jpg", "/tmp/sf-demo/b.jpg"

print("in at once")
api = Api("s1")
r = td.deliver(api, "s1", "看這兩張", images=[P1, P2], person=True)
check("each picture pasted (bracketed path), in order, then the words and Enter",
      api.log == [("paste", f" \x1b[200~{P1}\x1b[201~"), ("paste", f" \x1b[200~{P2}\x1b[201~"), ("words", "看這兩張", True)]
      and not r["queued"], api.log)
api = Api("s1")
td.deliver(api, "s1", "", images=[P1], person=True)
check("pictures with no words: pasted, then an Enter", api.log == [("paste", f" \x1b[200~{P1}\x1b[201~"), ("words", "", True)],
      api.log)

print("queued, then in, now, or withdrawn")
api = Api("s2")
api.state["s2"] = "decision"
r = td.deliver(api, "s2", "等一下送", images=[P1], person=True)
check("held by a menu: nothing typed yet, not even the picture", r["queued"] and api.log == [], api.log)
check("…the picture waits with it (kept on disk too)", "images" in td._KEEP and td._queues["s2"][0].get("images") == [P1])
api.state["s2"] = ""
check("when it goes in: the picture, then the words", wait_for(lambda: [e[0] for e in api.log] == ["paste", "words"]), api.log)
api = Api("s3")
api.state["s3"] = "decision"
r = td.deliver(api, "s3", "收回這則", images=[P2], person=True)
check("withdrawn: gone from the queue", td.take("s3", r["id"]) is not None and not td.pending("s3"))
time.sleep(0.3)
check("…and no picture was typed", api.log == [], api.log)

print("a stuck Enter after the pictures")
td._pasted.clear()
td._remember("s4", "", True)
check("ShellBot just pasted pictures: [Image #1] in the box is ours", td._ours("s4", "[Image #1]"))
td._pasted.clear()
check("otherwise [Image #1] is the person's", not td._ours("s4", "[Image #1]"))

print("Frame Link /link/send with images")
import tests_frame_link as tf  # noqa: E402
a, b = tf.Node("frameA"), tf.Node("frameB")
calls = []
real = a.link._execute


def execute(cmd, args):
    calls.append((cmd, dict(args)))
    if cmd == "save_paste":
        return {"success": True, "details": {"path": f"/tmp/sf-demo/{args['filename']}"}}
    if cmd == "send":
        return {"success": True, "message": "sent"}
    return real(cmd, args)


a.link._execute = execute
try:
    a.link.start(); b.link.start()
    pair = a.link.pairing_begin()
    b.link.join("127.0.0.1", a.port, pair["code"])
    peer = b.link.peers()[a.fid]
    img = base64.b64encode(os.urandom(2_500_000)).decode()      # 3.3 MB in base64
    st, body = tf.signed_post(b, peer, "/link/send", {"sid": "s1", "text": "看圖", "ask_draft": True,
                                                       "images": [{"filename": "x.jpg", "data_b64": img},
                                                                  {"filename": "y.jpg", "data_b64": "aGk="}]})
    saves = [c for c in calls if c[0] == "save_paste"]
    send = next((c[1] for c in calls if c[0] == "send"), {})
    check("a message with two pictures (3 MB) is taken", st == 200 and body.get("success"), (st, body))
    check("…each picture saved, and the send carries their paths with the words",
          [s[1]["filename"] for s in saves] == ["x.jpg", "y.jpg"]
          and send.get("images") == ["/tmp/sf-demo/x.jpg", "/tmp/sf-demo/y.jpg"] and send.get("text") == "看圖", send)
finally:
    a.cleanup(); b.cleanup()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
