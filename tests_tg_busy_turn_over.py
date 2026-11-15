#!/usr/bin/env python3
"""A tab whose hooks say the Telegram message's turn ended stops counting as busy after a grace.

Measured 2026-11-14: the bridge stopped receiving output (the output loop was stuck). A Telegram
message went into a tab at 13:26:03, the tab answered with its marker by 13:26:42, the bridge
never saw it: the tab stayed "busy with Telegram" and every A2A to it was given up, 18 hours.
  - no hooks for the tab: busy until the reply is taken, as before
  - its turn ended, within the grace (the bridge's own fallback has 30 s): still busy
  - past the grace: free, said once in the debug log; tab_delivery stops reading "telegram"
  - a turn that ended before the message went in, or one still working: busy
  - a message being typed in, or a LINE message waiting: busy whatever the hooks say

Run: .venv/bin/python tests_tg_busy_turn_over.py
"""
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-tg-turn-over-"))
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
app._agent_state_for_list = lambda sid: "done"
tab_delivery.capture = lambda api, sid: ""
GRACE = main.Api._BRIDGED_TURN_GRACE_S


def telegram_turn(sent_ago, prompt_ago=None, ended_ago=None, state="done"):
    """A Telegram message typed in `sent_ago` s ago; the tab took a prompt `prompt_ago` s ago
    and has been `state` for `ended_ago` s (None: no hooks at all)."""
    now = time.time()
    for k, v in (("has_user_msg", True), ("awaiting_response", True), ("expect_marker", True),
                 ("marker_forwarded", False), ("inject_pending", False)):
        setattr(slot, k, v)
    slot.msg_sent_ts = now - sent_ago
    app._hook_events = {} if prompt_ago is None else {"s1": {
        "state": state, "ts": now - ended_ago, "since": now - ended_ago, "event": "Stop",
        "tool": "", "prompt_at": now - prompt_ago}}
    app._bridged_turn_said = {}


def said():
    try:
        return Path(sf_log.DEBUG_LOG).read_text(encoding="utf-8").count("not counted busy")
    except FileNotFoundError:
        return 0


print("a Telegram message whose reply the bridge never saw")
telegram_turn(sent_ago=GRACE + 600)
check("no hooks for the tab: busy, as before", app._bridge_session_busy("s1") is True)
telegram_turn(sent_ago=100, prompt_ago=99, ended_ago=60)
check("its turn ended within the grace: still busy", app._bridge_session_busy("s1") is True)
telegram_turn(sent_ago=GRACE + 100, prompt_ago=GRACE + 99, ended_ago=GRACE + 60)
n = said()
check("past the grace: free", app._bridge_session_busy("s1") is False)
check("…said once in the debug log", said() == n + 1 and app._bridge_session_busy("s1") is False and said() == n + 1)
check("tab_delivery stops reading 'telegram'", tab_delivery.busy_reason(app, "s1") != "telegram",
      tab_delivery.busy_reason(app, "s1"))

print("what still counts")
telegram_turn(sent_ago=GRACE + 100, prompt_ago=GRACE + 500, ended_ago=GRACE + 400)
check("a turn that ended before the message went in: busy", app._bridge_session_busy("s1") is True)
telegram_turn(sent_ago=GRACE + 100, prompt_ago=GRACE + 99, ended_ago=GRACE + 60, state="working")
check("a turn still working: busy", app._bridge_session_busy("s1") is True)
check("…and tab_delivery reads 'telegram'", tab_delivery.busy_reason(app, "s1") == "telegram")
telegram_turn(sent_ago=GRACE + 100, prompt_ago=GRACE + 99, ended_ago=GRACE + 60)
slot.inject_pending = True
check("a message being typed in: busy whatever the hooks say", app._bridge_session_busy("s1") is True)
telegram_turn(sent_ago=GRACE + 100, prompt_ago=GRACE + 99, ended_ago=GRACE + 60)
app.line_bridge = types.SimpleNamespace(slots={"s1": types.SimpleNamespace(pending_target_id="U123")})
check("a LINE message waiting: busy whatever the hooks say", app._bridge_session_busy("s1") is True)
app.line_bridge = None
telegram_turn(sent_ago=GRACE + 100, prompt_ago=GRACE + 99, ended_ago=GRACE + 60)
slot.marker_forwarded, slot.awaiting_response = True, False
check("a reply that did go: free, as before", app._bridge_session_busy("s1") is False)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
