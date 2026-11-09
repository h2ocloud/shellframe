#!/usr/bin/env python3
"""A reused tab id shows its own conversation, and the effort it was launched with.

Measured: s347 (a bot woken at 16:44) showed "【ShellBot：這個 bot 對話要關閉了，請先寫記憶】"
as its activity while it was plainly working on something else. That line was the
last prompt of s346, a tab closed a minute earlier in the same folder:

  * before the new tab's first message its own transcript did not exist, and the
    nearest-birth guess took s346's file, born 7 minutes before the process;
  * the "keep the last non-empty detail" fallback is per tab id and never noticed
    the conversation had changed, so that line stayed after the right file was found;
  * a closed tab's per-id status was never dropped, so a later tab with the same id
    could inherit it.

And the same tab, launched with `--effort max`, read xhigh: the effort came from the
global default, not the launch command.

Run: .venv/bin/python tests_tab_id_reuse.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-tab-reuse-"))
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")  # fake tabs stay out of the real log

import agent_status as ag  # noqa: E402
import main  # noqa: E402
from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


print("guessing a new tab's transcript")
older = TMP / "older.jsonl"
older.write_text("{}\n", encoding="utf-8")
born = os.stat(older).st_birthtime
check("a file born minutes before the process started is not its conversation",
      ag._nearest_birth_jsonl(str(TMP / "*.jsonl"), born + 400) is None)
check("one born just around the start still is (a CLI that writes it at launch)",
      ag._nearest_birth_jsonl(str(TMP / "*.jsonl"), born + 2) == str(older)
      and ag._nearest_birth_jsonl(str(TMP / "*.jsonl"), born - 30) == str(older))

print("the detail kept between looks stays with its conversation")
a, b = TMP / "a.jsonl", TMP / "b.jsonl"
for f in (a, b):
    f.write_text("{}\n", encoding="utf-8")
CLOSE_PROMPT = "【ShellBot：這個 bot 對話要關閉了，請先寫記憶】"
evs_a = [{"kind": "user_msg", "text": CLOSE_PROMPT, "ts": time.time() - 5},
         {"kind": "assistant_text", "text": "記憶寫好了", "ts": time.time() - 4}]
evs_b = [{"kind": "tool_call", "tool": "Bash", "target": "for d in sims", "ts": time.time() - 1}]
tracker = ag.StatusTracker()
worker = {"cmd": "claude", "cwd": "~", "tmux_name": None}


def look(path, evs):
    with mock.patch.object(ag.StatusTracker, "_resolve_cached", return_value=str(path)), \
         mock.patch.object(ag, "_read_tail_events", return_value=("claude", evs, None)), \
         mock.patch.object(ag, "detect_model_info", return_value=None), \
         mock.patch.object(ag, "detect_schedules", return_value=None):
        return tracker.status_for("s347", worker, screen_tail="")


look(a, evs_a)
res = look(b, evs_b)
check("another conversation under the same id: the old prompt does not carry over",
      res.get("task") == "" and CLOSE_PROMPT not in json.dumps(res, ensure_ascii=False), res.get("task"))
look(b, evs_b + [{"kind": "user_msg", "text": "修好模擬器", "ts": time.time()}])
res = look(b, evs_b)
check("…while within one conversation a gap in the tail still keeps the last task", res.get("task") == "修好模擬器",
      res.get("task"))

print("closing a tab forgets what was kept under its id")
main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {}}), encoding="utf-8")
api = Api.__new__(Api)
api.bridge = api.line_bridge = None
api._window = None
api._status_tracker = tracker
api._hook_events = {"s347": {"state": "done"}}
api._status_cache = {"s347": {"result": {}}}
api._persist_session_manifest = lambda *a, **k: None
api._plugin_dispatch_session_close = lambda sid: None
api._bots_divert_close = lambda sid, reason: False


class Tab:
    def __init__(self):
        self.alive, self.lock, self._recent, self.cmd = True, threading.Lock(), bytearray(), "claude"
        self._tmux_name = None

    def kill(self):
        self.alive = False


api.sessions = {"s347": Tab()}
tracker._last_result["s347"] = (time.time(), {"task": CLOSE_PROMPT})
try:
    api.close_session("s347", reason="test")
except Exception as e:
    print("   (close_session raised after the cleanup:", type(e).__name__, e, ")")
left = [name for name, table in (("path", tracker._path_cache), ("state", tracker._last), ("detail", tracker._detail),
                                  ("marker", tracker._pmark), ("result", tracker._last_result),
                                  ("hooks", api._hook_events), ("cache", api._status_cache)) if "s347" in table]
check("the tracker, the hook state and the status cache let go of the id", not left, left)

print("the effort a tab was launched with")
check("--effort max in the launch command wins over the global default",
      (ag.detect_model_info({"cmd": "claude --effort max --model opus", "cwd": "/tmp", "tmux_name": None}) or {})
      .get("effort") == "max")
check("--effort=high too", ag._parse_model_flag("claude --effort=high --model opus", "--effort") == "high")
eff = TMP / "eff.jsonl"
eff.write_text('{"type":"user","message":{"content":"<local-command-stdout>Set effort level to low</local-command-stdout>"}}\n'
               '{"type":"assistant","message":{"model":"claude-opus-5","content":"x"}}\n', encoding="utf-8")
check("…but a /effort inside the conversation is newer still",
      (ag.detect_model_info({"cmd": "claude --effort max", "cwd": "/tmp", "tmux_name": None}, transcript_path=str(eff))
       or {}).get("effort") == "low")

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
