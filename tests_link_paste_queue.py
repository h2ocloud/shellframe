#!/usr/bin/env python3
"""A phone's picture and its queued message (frame_link /link/paste, /link/send/now, /link/send/cancel).

Reported: a picture attached in the app never reached the tab, and nothing said so. Measured in the
debug log: "refused POST /link/paste from peer … (iPhone): bad signature". The body reader gave up past
2 MB and the signature was then checked against an empty body; a phone screenshot is 2-5 MB in base64.
  - a picture up to 24 MB goes in; past that the answer is 413 "too large", never "bad signature"
Asked for: a queued message only cost time and tokens; offer to send it now, or take it back.
  - /link/send/now types the queued message in at once; /link/send/cancel withdraws it
  - one that already went in answers "gone"; a one-way pairing may do neither
Run: .venv/bin/python tests_link_paste_queue.py
"""
import base64
import json
import os
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.modules.setdefault("webview", MagicMock())
sys.path.insert(0, str(HERE))
import frame_link  # noqa: E402
import tests_frame_link as tf  # noqa: E402  (Node, signed_post; its main() does not run on import)

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


a, b = tf.Node("frameA"), tf.Node("frameB")
queued = {}                       # what A's queued_send command was asked
real_exec = a.link._execute


def execute(cmd, args):
    if cmd == "queued_send":
        queued.setdefault("calls", []).append(dict(args))
        if args.get("id") == "gone1":
            return {"success": False, "code": "gone", "message": "already in"}
        return {"success": True, "message": "ok"}
    if cmd == "save_paste":
        return {"success": True, "details": {"path": "/tmp/x.png"}}
    return real_exec(cmd, args)


a.link._execute = execute
try:
    a.link.start(); b.link.start()
    pair = a.link.pairing_begin()
    b.link.join("127.0.0.1", a.port, pair["code"])
    peer = b.link.peers()[a.fid]

    print("a picture")
    small = base64.b64encode(os.urandom(300_000)).decode()
    st, body = tf.signed_post(b, peer, "/link/paste", {"sid": "s1", "filename": "a.png", "data_b64": small})
    check("a small one goes in", st == 200 and body.get("success"), (st, body))
    big = base64.b64encode(os.urandom(3_000_000)).decode()        # 4 MB in base64: a phone screenshot
    st, body = tf.signed_post(b, peer, "/link/paste", {"sid": "s1", "filename": "b.png", "data_b64": big})
    check("a phone screenshot (4 MB in base64) goes in; before: 'bad signature'", st == 200 and body.get("success"),
          (st, body))
    huge = "A" * (frame_link.PASTE_MAX_BYTES + 10)
    st, body = tf.signed_post(b, peer, "/link/paste", {"sid": "s1", "filename": "c.png", "data_b64": huge})
    check("past the cap: 413 'too large', not 'bad signature'", st == 413 and "太大" in (body.get("message") or ""),
          (st, body))

    print("a queued message")
    st, body = tf.signed_post(b, peer, "/link/send/now", {"sid": "s1", "id": "q1"})
    check("send now: the queued one goes in", st == 200 and body.get("success")
          and queued["calls"][-1] == {"sid": "s1", "id": "q1", "mode": "now"}, (st, body, queued))
    st, body = tf.signed_post(b, peer, "/link/send/cancel", {"sid": "s1", "id": "q2"})
    check("withdraw: taken back", st == 200 and body.get("success")
          and queued["calls"][-1] == {"sid": "s1", "id": "q2", "mode": "cancel"}, (st, body))
    st, body = tf.signed_post(b, peer, "/link/send/now", {"sid": "s1", "id": "gone1"})
    check("one that already went in: gone", st == 200 and body.get("code") == "gone", body)
finally:
    a.cleanup(); b.cleanup()

print("the command behind it")
import api_remote  # noqa: E402
import tab_delivery as td  # noqa: E402
host = api_remote.RemoteApiMixin.__new__(api_remote.RemoteApiMixin)
typed = []
tab = types.SimpleNamespace(sid="s1", _startup_trust_pending=False)
host.sessions = {"s1": tab}
host._send_text_to_session = lambda s, text, submit=False: typed.append(text)
host._agent_state_for_list = lambda sid: "decision"           # a menu holds it
host._agent_status_snapshot = lambda sid: ({"prompt_at": 0}, 0)
host._bridge_session_busy = lambda sid: False
td.capture = lambda api, sid: ""
r1 = td.deliver(host, "s1", "第一則", person=True)
r2 = td.deliver(host, "s1", "第二則", person=True)
check("held by a menu: both queued", r1["queued"] and r2["queued"])
res = api_remote.RemoteApiMixin._execute_sfctl(host, "queued_send", {"sid": "s1", "id": r1["id"], "mode": "now"})
check("now: typed in at once, out of the queue", res.get("success") and typed == ["第一則"], (res, typed))
res = api_remote.RemoteApiMixin._execute_sfctl(host, "queued_send", {"sid": "s1", "id": r2["id"], "mode": "cancel"})
check("cancel: withdrawn, never typed", res.get("success") and typed == ["第一則"] and not td.pending("s1"), (res, typed))
res = api_remote.RemoteApiMixin._execute_sfctl(host, "queued_send", {"sid": "s1", "id": r2["id"], "mode": "now"})
check("again: gone", res.get("code") == "gone", res)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
