#!/usr/bin/env python3
"""`sfctl state`, `list` and `bots` report what the hooks say, not the older guess.

Measured: a tab's hook log said working from 12:12 to 12:27 while `sfctl state`
answered done the whole time. The status monitor applied the hook state only to
the copy it pushed to the page; everything that read the stored snapshot
(`_agent_status_snapshot` → state, list, bots, the Telegram heartbeat) got the
pre-hook guess. The primary bot's stock-taking read every busy tab as done.

  - a fresh hook event wins in the snapshot, in the state row and in `sfctl bots`
  - an expired hook (past _HOOK_TTL) no longer overrides
  - with no hook at all, the guess is reported as before

Run: .venv/bin/python tests_state_hook_overlay.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-hookstate-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(HERE))

import main  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_bots": True}, "agent_roster": {
    "coder": {"label": "coder", "cmd": "claude", "bot": {"memory": True}}}}), encoding="utf-8")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class Tracker:
    """Stands in for agent_status.StatusTracker: holds the screen/transcript guess."""
    def __init__(self):
        self.res = {}

    def last_result(self, sid, max_age=None):
        return (self.res.get(sid), 2.0) if sid in self.res else (None, None)


class Tab:
    def __init__(self, sid, label):
        self._custom_label = label
        self.cmd = "claude"
        self.alive = True
        self.lock = threading.Lock()
        self._recent = bytearray()
        self._last_output_activity_time = time.time()


api = Api.__new__(Api)
api.sessions = {"s1": Tab("s1", "coder")}
api.bridge = api.line_bridge = None
api._window = None
api._status_tracker = Tracker()
api._hook_events = {}
api._status_cache = {}
api._persist_session_manifest = lambda *a, **k: None
api._status_tracker.res["s1"] = {"state": "done", "dot": "sig-done", "summary": "done",
                                 "action": "", "narration": "finished the last step"}

print("no hook")
check("with no hook the stored guess is reported", api._agent_state_for_list("s1") == "done")

print("fresh hook")
api._on_agent_event({"sid": "s1", "event": "UserPromptSubmit"})
api._on_agent_event({"sid": "s1", "event": "PreToolUse", "tool_name": "Bash"})
check("the snapshot says working while the hook says so", api._agent_status_snapshot("s1")[0]["state"] == "working")
check("…so does the state used by list and state", api._agent_state_for_list("s1") == "working")
row = api._session_state_row("s1", api.sessions["s1"], with_error=False)
check("…and the sfctl state row", row.get("agent_state", row.get("state")) == "working", row)
lst = api._bots_list_for_sfctl()
coder = [b for b in lst["details"]["bots"] if b["role"] == "coder"][0]
check("…and sfctl bots", coder["doing"] == "working", coder)
check("the detail lines from the transcript are kept", api._agent_status_snapshot("s1")[0]["narration"]
      == "finished the last step")

print("stop")
api._on_agent_event({"sid": "s1", "event": "Stop"})
check("a Stop hook makes it done", api._agent_state_for_list("s1") == "done")

print("expired hook")
api._on_agent_event({"sid": "s1", "event": "PreToolUse", "tool_name": "Bash"})
api._hook_events["s1"]["ts"] -= api._HOOK_TTL + 5
check("an expired hook no longer overrides", api._agent_state_for_list("s1") == "done")

print("no blanket reset")
# Measured 13:00:45: a timer cleared every tab's hook state every five minutes, so a
# tab that was plainly still working (thinking, a long tool run) read done until its
# next hook event. Only a tab that has gone quiet loses its working verdict now.
now = time.time()
tab = api.sessions["s1"]
api._hook_events["s1"] = {"state": "working", "ts": now - 600, "since": now - 600, "tool": "Bash"}
tab._last_output_activity_time = now - 1            # the spinner is still ticking
check("ten minutes without a hook but still painting: kept", api._quiet_hook_sids(now) == [])
check("…and state still says working", api._agent_state_for_list("s1") == "working")
tab._last_output_activity_time = now - api.HOOK_QUIET_S - 5
check("quiet in hooks and on screen (an interrupt with no Stop): released",
      api._quiet_hook_sids(now) == ["s1"])
api._hook_events["s1"] = {"state": "decision", "ts": now - 600, "since": now - 600}
check("a decision verdict is not released by silence (a menu is quiet by nature)",
      api._quiet_hook_sids(now) == [])
src = (HERE / "api_status.py").read_text(encoding="utf-8")
monitor = src.split("    def _start_status_monitor(self):", 1)[1]
check("the status monitor no longer clears every tab's hooks on a timer",
      "self._hook_events.clear()" not in monitor and "HOOK_RESET_INTERVAL" not in monitor)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
