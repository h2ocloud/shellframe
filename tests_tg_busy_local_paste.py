#!/usr/bin/env python3
"""A tab is "busy with Telegram" only while a bridged message is on its way in or unanswered.

Measured: text pasted in a tab with its Enter (one write holding text and \\r) arms the
Telegram slot's awaiting_response, for completion notices of local turns. Nothing clears it
outside a Telegram turn, so main._bridge_session_busy stayed True for hours: tab_delivery read
"telegram", and every A2A and chat-view send to that tab queued and was given up after 30 min
("等 Telegram 那一則處理完再送進去" with no Telegram message anywhere). A marker reply already
forwarded also kept expect_marker for follow-ups, with the same effect.
  - a local paste with Enter: still armed for the done notice, not busy
  - a Telegram message going in (inject_pending) or begun and not answered: busy
  - its marker reply forwarded: free again, though the slot keeps listening for follow-ups
  - a Telegram turn without a marker: busy until its reply is taken
  - a LINE message waiting for its reply: busy
  - tab_delivery sees the same: a local paste gives no "telegram" reason

Run: .venv/bin/python tests_tg_busy_local_paste.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-tg-busy-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")
import bridge_telegram as bt  # noqa: E402
bt._LOG_FILE = str(TMP / "shellframe_bridge.log")
import main  # noqa: E402
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


bt.tg_api = lambda *a, **k: {"ok": True}
b = bt.TelegramBridge("tg", bt.TelegramBridgeConfig(bot_token="x", allowed_users=[]))
b._prune_stale_slots = lambda force=False: None
b.register_session("s1", "秘書", lambda text: None, cmd="claude")
slot = b.slots["s1"]

app = main.Api.__new__(main.Api)
app.bridge, app.line_bridge = b, None
app.sessions = {"s1": types.SimpleNamespace(cmd="claude", alive=True)}
app._should_inject_init = lambda cmd: True


def reset():
    for k, v in (("has_user_msg", False), ("awaiting_response", False), ("expect_marker", False),
                 ("marker_forwarded", False), ("inject_pending", False)):
        setattr(slot, k, v)


print("a paste in the tab")
reset()
app._arm_awaiting_response("s1", "17:55 本日進度匯報\r\n第二行\r")
check("still armed for the done notice", slot.awaiting_response is True)
check("…but the tab is not busy with Telegram", app._bridge_session_busy("s1") is False)

print("a Telegram message")
reset()
slot.inject_pending = True
check("being typed in: busy", app._bridge_session_busy("s1") is True)
reset()
slot.has_user_msg, slot.awaiting_response, slot.expect_marker = True, True, True
check("begun, its marker reply not out yet: busy", app._bridge_session_busy("s1") is True)
slot.marker_forwarded, slot.awaiting_response = True, False      # the reply went; the slot keeps listening
check("its marker reply forwarded: free again (follow-ups are still listened for)",
      app._bridge_session_busy("s1") is False and slot.expect_marker is True)
reset()
slot.has_user_msg, slot.awaiting_response = True, True
check("no marker: busy until its reply is taken", app._bridge_session_busy("s1") is True)
slot.awaiting_response = False
check("…then free", app._bridge_session_busy("s1") is False)

print("a LINE message")
reset()
app.line_bridge = types.SimpleNamespace(slots={"s1": types.SimpleNamespace(pending_target_id="U123", has_user_msg=True,
                                                                           awaiting_response=True, expect_marker=True)})
check("waiting for its reply: busy", app._bridge_session_busy("s1") is True)
app.line_bridge = None

print("what tab_delivery reads")
reset()
app._arm_awaiting_response("s1", "貼上一段\r")
app._agent_state_for_list = lambda sid: "done"
real_capture = tab_delivery.capture
tab_delivery.capture = lambda api, sid: ""
check("a local paste gives no 'telegram' reason", tab_delivery.busy_reason(app, "s1") != "telegram",
      tab_delivery.busy_reason(app, "s1"))
slot.has_user_msg, slot.expect_marker = True, True
check("a Telegram turn in flight does", tab_delivery.busy_reason(app, "s1") == "telegram")
tab_delivery.capture = real_capture

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
