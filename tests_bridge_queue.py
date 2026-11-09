#!/usr/bin/env python3
"""A Telegram message for a busy tab waits its turn (30 min) with "⚡ go in now".

Before: the bridge waited at most 2 minutes and then typed the message in
anyway, cutting the tab's turn off. Decided: queue up to 30 minutes like A2A,
let the person cut in with ⚡, every tab.

  - busy = a turn on screen, the hooks say working, or the user's draft in the
    box; a decision on screen is not (answering it must get through)
  - free: in at once; busy: after a few seconds the phone hears it is queued,
    with the ⚡ button; it goes in when the tab frees
  - ⚡ sends it at once; past the limit it is dropped and the phone is told
  - through the real bridge: a message to a busy tab is held, then typed in

Run: .venv/bin/python tests_bridge_queue.py
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-tg-queue-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")  # fake tabs stay out of the real log

import bridge_queue as bq  # noqa: E402
import bridge_telegram as bt  # noqa: E402
import tab_delivery  # noqa: E402

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


sent = []
bt.tg_api = lambda token, method, payload=None, **k: sent.append((method, payload)) or {"ok": True}
BUSY_SCREEN = "✻ Thinking… (12s · esc to interrupt)"
RULE = "─" * 40


class App:
    def __init__(self):
        self.state, self.screen = {}, {}

    def _agent_state_for_list(self, sid):
        return self.state.get(sid, "")


tab_delivery.capture = lambda api, sid: api.screen.get(sid, "")
b = bt.TelegramBridge("tg", bt.TelegramBridgeConfig(bot_token="x", allowed_users=[]))
b._prune_stale_slots = lambda force=False: None      # a fake tab has no tmux pane to prove it is alive
written = []
b.register_session("s1", "主控", lambda text: written.append(text), cmd="claude")
slot = b.slots["s1"]
tail = {"s1": ""}
b._live_tail = lambda s: tail[s.sid]
b.home_api = App()

print("what counts as busy")
check("idle: not busy", not bq.busy(b, slot))
tail["s1"] = BUSY_SCREEN
check("a turn on screen", bq.busy(b, slot))
tail["s1"] = ""
b.home_api.state["s1"] = "working"
check("the hooks say working", bq.busy(b, slot))
b.home_api.state["s1"] = "decision"
check("a decision on screen does not hold a message back (a digit must answer it)", not bq.busy(b, slot))
b.home_api.state["s1"] = ""
b.home_api.screen["s1"] = "\n".join(["x", RULE, "\x1b[39m❯\xa0寫到一半", RULE])
check("the user's unsent text in the box (Ctrl-U would wipe it)", bq.busy(b, slot))
b.home_api.screen["s1"] = "\n".join(["x", RULE, "\x1b[39m❯\xa0\x1b[2mTry this\x1b[0m", RULE])
check("…but not a dim suggestion", not bq.busy(b, slot))
b.home_api.screen["s1"] = ""

print("waiting")
bq.NOTIFY_AFTER, bq.POLL = 0.2, 0.02
sent.clear()
check("free: in at once, nothing sent to the phone", bq.wait_turn(b, slot, 100) == "ready" and not sent)
tail["s1"] = BUSY_SCREEN
threading.Timer(0.6, lambda: tail.update(s1="")).start()
t0 = time.time()
res = bq.wait_turn(b, slot, 100)
check("busy: it waits, and goes when the turn ends", res == "ready" and 0.5 < time.time() - t0 < 2, res)
note = [p for m, p in sent if m == "sendMessage"]
check("…the phone heard it is queued, with ⚡",
      wait_for(lambda: any("正在忙" in (p.get("text") or "") for _, p in sent if p))
      and any((p.get("reply_markup") or {}).get("inline_keyboard", [[{}]])[0][0].get("callback_data", "")
              .startswith("tgnow:") for _, p in sent if p), sent)

print("⚡")
sent.clear()
tail["s1"] = BUSY_SCREEN
out = {}
th = threading.Thread(target=lambda: out.update(r=bq.wait_turn(b, slot, 100)))
th.start()
wait_for(lambda: any((p or {}).get("reply_markup") for _, p in sent))
token = [p for _, p in sent if (p or {}).get("reply_markup")][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
check("⚡ is ours to handle", bq.on_callback(b, token, 100, 7))
th.join(2)
check("…and the message goes in at once", out.get("r") == "now", out)
check("…the button's message says so", wait_for(lambda: any(m == "editMessageText" and "馬上送進去" in p["text"]
                                                            for m, p in sent)))
sent.clear()
check("a stale ⚡ (already in) just says so", bq.on_callback(b, "tgnow:deadbeef", 100, 8)
      and wait_for(lambda: any("已經送進去" in p["text"] for m, p in sent if m == "editMessageText")))
check("other buttons are not ours", not bq.on_callback(b, "choice:s1:2", 100, 9))

print("giving up")
sent.clear()
check("busy past the limit: dropped", bq.wait_turn(b, slot, 100, max_wait=0.3) == "expired")
check("…and the phone is told how to send it again",
      wait_for(lambda: any("沒有送進去" in (p.get("text") or "") and "⚡" in p["text"] for _, p in sent if p)))
tail["s1"] = ""

print("through the bridge")
src = (HERE / "bridge_telegram.py").read_text(encoding="utf-8")
send_body = src.split("        def _send():", 1)[1].split("        def _send_tracked():", 1)[0]
check("the bridge waits with bridge_queue and no longer forces a message in after 2 minutes",
      "bridge_queue.wait_turn(self, slot, chat_id)" in send_body and "120.0" not in send_body
      and "forced_after_busy" not in src)
check("the ⚡ button is routed", "bridge_queue.on_callback(self, data, chat_id, message_id)" in src)
b._react_async = lambda *a, **k: None
b._wait_input_safe = lambda s: ""
b.config.allowed_users = [100]
written.clear()
sent.clear()
tail["s1"] = BUSY_SCREEN
b._handle_update({"update_id": 1, "message": {"message_id": 1, "text": "等你忙完再看這個", "date": int(time.time()),
                                              "from": {"id": 100, "username": "u"}, "chat": {"id": 100, "type": "private"}}})
check("a message for a busy tab is held: the phone hears it is queued, nothing is typed",
      wait_for(lambda: any("正在忙" in ((p or {}).get("text") or "") for _, p in sent))
      and not any("等你忙完再看這個" in w for w in written), (written, sent[-2:]))
tail["s1"] = ""
check("…and typed in once the tab is free", wait_for(lambda: any("等你忙完再看這個" in w for w in written), 8), written)

print("giving up stands the Telegram turn down")
# Without it the tab kept reading "a Telegram turn is in flight": A2A and sfctl send to it
# waited their full 30 minutes for nothing.
import main  # noqa: E402
bq.MAX_WAIT = 0.3
with slot.output_lock:                    # the earlier message's reply came: nothing else in flight
    b._end_marker_turn(slot)
    slot.awaiting_response = False
tail["s1"] = BUSY_SCREEN
sent.clear()
b._handle_update({"update_id": 2, "message": {"message_id": 2, "text": "這則會等到放棄", "date": int(time.time()),
                                              "from": {"id": 100, "username": "u"}, "chat": {"id": 100, "type": "private"}}})
check("the message is given up", wait_for(lambda: any("沒有送進去" in ((p or {}).get("text") or "") for _, p in sent), 5))
check("…and the tab no longer waits on a Telegram reply", wait_for(lambda: not slot.awaiting_response and not slot.expect_marker
                                                                    and not slot.reply_markers, 3),
      (slot.awaiting_response, slot.expect_marker, slot.reply_markers))
app = main.Api.__new__(main.Api)
app.bridge, app.line_bridge = b, None
check("…so A2A and sfctl send see it free of Telegram again", app._bridge_session_busy("s1") is False)
check("a heartbeat does not speak for a message that has not gone in",
      'if not slot.awaiting_response or getattr(slot, "inject_pending", False):' in src)
slot.reply_markers = [{"start": "[[A]]", "end": "[[/A]]", "answered": False}, {"start": "[[B]]", "end": "[[/B]]", "answered": False}]
slot.reply_start_marker, slot.awaiting_response, slot.expect_marker = "[[B]]", True, True
bq.abandon(b, slot, {"start": "[[B]]"})
check("giving up one message keeps another one's reply on the way",
      slot.awaiting_response and [m["start"] for m in slot.reply_markers] == ["[[A]]"] and slot.reply_start_marker == "[[A]]",
      (slot.reply_markers, slot.reply_start_marker))
bq.MAX_WAIT = 1800.0
tail["s1"] = ""

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
