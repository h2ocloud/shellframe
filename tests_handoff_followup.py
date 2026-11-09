#!/usr/bin/env python3
"""A hand-off that is never reported gets one reminder, and then its requester hears.

Measured: whether `sfctl a2a-result` was run depended on the model remembering
it. Decided: Claude Code's Stop hook really blocks once; other CLIs get a
reminder from ShellBot; past a timeout the requester is told.

  - a hand-off record knows its tab and when it was in front of the agent
    (a queued one: when the queue typed it in, not when it was sent)
  - Stop check: blocks once for a hand-off this tab got before the turn began;
    never twice, never for a later or answered one, never with A2A off
  - the hook script: no file read hit → no question to the app; stop_hook_active
    → never blocks again; a block prints Claude Code's decision JSON
  - follow-ups for any CLI: one reminder after the tab sat idle, the requester
    told OVERDUE_S later or when the tab is closed; each once

Run: .venv/bin/python tests_handoff_followup.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-handoff-followup-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")  # fake tabs stay out of the real log

import agent_link  # noqa: E402
agent_link.STATE_DIR = TMP / "state"
agent_link.LOG_FILE = agent_link.STATE_DIR / "agent_link.json"
agent_link.STATE_DIR.mkdir(parents=True, exist_ok=True)
import a2a_host  # noqa: E402
import main  # noqa: E402
import sf_agent_hook  # noqa: E402
import tab_delivery as td  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_a2a": True}, "agent_roster": {
    "coder": {"label": "coder", "cmd": "claude", "responsibility": "code"}}}), encoding="utf-8")
td.POLL, td.TAKEN_WAIT = 0.05, 0.3

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
    return False


class Tab:
    def __init__(self, sid, label):
        self.sid, self._custom_label, self.cmd = sid, label, "claude"
        self.alive, self.lock, self._recent = True, threading.Lock(), bytearray()
        self._tmux_name = None
        self._startup_trust_pending = True
        self._last_output_activity_time = time.time()


def make_api():
    api = Api.__new__(Api)
    api.sessions = {"s1": Tab("s1", "coder"), "s2": Tab("s2", "boss")}
    api.bridge = api.line_bridge = None
    api._window = None
    api._hook_events = {}
    api.state = {"s1": "", "s2": ""}
    api.sent = []

    def send(s, text, submit=False):
        api.sent.append((s.sid, text))
    api._send_text_to_session = send
    api._agent_state_for_list = lambda sid: api.state.get(sid, "")
    api._agent_status_snapshot = lambda sid: ({"prompt_at": 0}, 0)
    return api


td.capture = lambda api, sid: ""
rec = lambda hid: agent_link._get_handoff(hid) or {}

print("the record")
api = make_api()
res = a2a_host.handle(api, "a2a_handoff", {"target": "coder", "text": "fix the parser", "from_sid": "s2"})
hid = (res.get("details") or {}).get("id")
check("a hand-off to a free tab goes in", res.get("success") and any(s == "s1" for s, _ in api.sent), res)
check("…and its record names the tab and when it was in front of the agent",
      rec(hid).get("to_sid") == "s1" and rec(hid).get("delivered_at", 0) > 0, rec(hid))

api.state["s1"] = "working"
res = a2a_host.handle(api, "a2a_handoff", {"target": "coder", "text": "and the tests", "from_sid": "s2"})
qid = (res.get("details") or {}).get("id")
check("to a busy tab it is queued, and not yet in front of the agent",
      (res.get("details") or {}).get("queued") and not rec(qid).get("delivered_at"), (res, rec(qid)))
api.state["s1"] = ""
check("once the queue types it in, that is when it counts",
      wait_for(lambda: rec(qid).get("delivered_at", 0) > 0), rec(qid))

print("the Stop check")
now = time.time()
check("unknown turn start (no hook yet): never blocks", agent_link.stop_check("s1", 0) == ("", ""))
check("another tab: nothing", agent_link.stop_check("s2", now + 10) == ("", ""))
first, reason = agent_link.stop_check("s1", now + 10)
check("the oldest unanswered hand-off blocks the stop, with how to report it",
      first == hid and f"a2a-result {hid}" in reason and "only reminder" in reason, (first, reason))
check("…once: the next stop is not blocked for it again", agent_link.stop_check("s1", now + 10)[0] != hid)
second, _ = agent_link.stop_check("s1", rec(qid)["delivered_at"] - 10)
check("a hand-off typed in after this turn began does not block this stop", second == "", second)
res = a2a_host.handle(api, "a2a_result", {"id": qid, "text": "done", "from_sid": "s1"})
check("an answered one never blocks", res.get("success") and agent_link.stop_check("s1", now + 99) == ("", ""), res)

print("through the app")
api.sent.clear()
res = a2a_host.handle(api, "a2a_handoff", {"target": "coder", "text": "rename the module", "from_sid": "s2"})
hid3 = (res.get("details") or {}).get("id")
api._hook_events["s1"] = {"state": "done", "ts": time.time(), "prompt_at": time.time() + 1}
r = a2a_host.handle(api, "a2a_stop_check", {"sid": "s1"})
check("the app answers the hook: block, with the reason", r["details"]["block"] and hid3 in r["details"]["reason"], r)
r = a2a_host.handle(api, "a2a_stop_check", {"sid": "s1"})
check("…and only once", r["details"]["block"] is False, r)
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_a2a": False}}), encoding="utf-8")
res4 = a2a_host.handle(api, "a2a_stop_check", {"sid": "s1"})
check("with A2A off it never blocks", res4["details"]["block"] is False, res4)
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_a2a": True}, "agent_roster": {
    "coder": {"label": "coder", "cmd": "claude", "responsibility": "code"}}}), encoding="utf-8")

print("the hook script")
check("it looks where agent_link keeps hand-offs", sf_agent_hook.HANDOFF_FILE == str(
    Path.home() / ".local" / "state" / "shellframe" / "a2a_handoff.json"))
asked = []


def run_hook(stdin, open_handoff, answer):
    sf_agent_hook._open_handoff_for = lambda sid: open_handoff
    import sfctl
    sfctl._rpc = lambda cmd, args=None, timeout=15: asked.append(cmd) or answer
    os.environ["SF_SID"] = "s1"
    sys.stdin = io.StringIO(json.dumps(stdin))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sf_agent_hook.stop_check()
    sys.stdin = sys.__stdin__
    return out.getvalue()


block = {"success": True, "details": {"block": True, "reason": "report first"}}
check("nothing open in the file: no question to the app, no output",
      run_hook({}, False, block) == "" and asked == [])
check("already continuing because of a stop hook: never blocks again",
      run_hook({"stop_hook_active": True}, True, block) == "" and asked == [])
out = run_hook({}, True, block)
check("an open hand-off: asks the app and prints Claude Code's block decision",
      asked == ["a2a_stop_check"] and json.loads(out) == {"decision": "block", "reason": "report first"}, out)
check("the app says no (or does not answer): no output, the stop goes ahead",
      run_hook({}, True, {"success": False, "message": "Timeout"}) == "")
os.environ.pop("SF_SID", None)
home = TMP / "home"
p = subprocess.run([sys.executable, str(HERE / "sf_agent_hook.py"), "--stop-check"], input="{}",
                   capture_output=True, text=True, timeout=10,
                   env=dict(os.environ, SF_SID="s1", HOME=str(home)))
check("as Claude Code runs it, with no hand-off file: exit 0, no output", p.returncode == 0 and p.stdout == "",
      (p.returncode, p.stdout, p.stderr[-200:]))

print("follow-ups, for every CLI")


class Ctx:
    def __init__(self):
        self.alive, self.idle, self.reminded, self.told = {"s1": True}, {"s1": 0.0}, [], []

    def tab_alive(self, sid):
        return self.alive.get(sid, False)

    def idle_for(self, sid):
        return self.idle.get(sid, -1.0)

    def remind(self, sid, hid, text):
        self.reminded.append((sid, hid, text))

    def tell_requester(self, rec, hid, text):
        self.told.append((rec.get("origin_sid"), hid, text))


for h, r in agent_link._load_handoffs().items():   # start clean: earlier ones are answered
    agent_link._patch_handoff(h, answered=True)
res = a2a_host.handle(api, "a2a_handoff", {"target": "coder", "text": "write the docs", "from_sid": "s2"})
h5 = (res.get("details") or {}).get("id")
got = rec(h5)["delivered_at"]
ctx = Ctx()
ctx.idle["s1"] = 10.0
check("not idle long enough: nothing yet", agent_link.followups(ctx, now=got + 120) == [])
ctx.idle["s1"] = -1.0
check("…while it runs: nothing", agent_link.followups(ctx, now=got + 600) == [])
ctx.idle["s1"] = 90.0
check("idle 90 s after it got the hand-off: one reminder typed in",
      agent_link.followups(ctx, now=got + 120) == [("nudged", h5)] and ctx.reminded[0][:2] == ("s1", h5)
      and f"a2a-result {h5}" in ctx.reminded[0][2])
check("…once", agent_link.followups(ctx, now=got + 300) == [] and len(ctx.reminded) == 1)
check("still nothing 30 min after the reminder: the requester is told",
      agent_link.followups(ctx, now=got + 120 + agent_link.OVERDUE_S + 1) == [("overdue", h5)]
      and ctx.told[0][0] == "s2" and "#coder" in ctx.told[0][2])
check("…once", agent_link.followups(ctx, now=got + 9999) == [] and len(ctx.told) == 1)

res = a2a_host.handle(api, "a2a_handoff", {"target": "coder", "text": "check the logs", "from_sid": "s2"})
h6 = (res.get("details") or {}).get("id")
api._hook_events["s1"] = {"state": "done", "ts": time.time(), "prompt_at": time.time() + 1}
a2a_host.handle(api, "a2a_stop_check", {"sid": "s1"})
ctx = Ctx()
ctx.idle["s1"] = 600.0
check("already reminded by the Stop hook: no second reminder",
      agent_link.followups(ctx, now=rec(h6)["delivered_at"] + 120) == [] and not ctx.reminded)
ctx.alive["s1"] = False
check("the tab closed before reporting: the requester hears that, once",
      agent_link.followups(ctx, now=time.time()) == [("closed", h6)] and "closed" in ctx.told[0][2]
      and agent_link.followups(ctx, now=time.time()) == [])
check("follow-ups are in the A2A log", any(str(e.get("kind", "")).startswith("follow-up:")
                                             for e in agent_link.history(50)))

print("the host's own pieces")
real = a2a_host._Ctx(api)
api.state["s1"] = "working"
check("idle_for: -1 while the tab works", real.idle_for("s1") == -1.0)
api.state["s1"] = ""
api._last_activity_at = lambda sid, s, out_ts: time.time() - 75
check("…seconds since its last activity otherwise", 74 <= real.idle_for("s1") <= 77, real.idle_for("s1"))
api.sent.clear()
real.tell_requester({"origin_sid": "s2"}, "abc", "[[ShellBot: late]]")
check("tell_requester types the note into the requester's tab", ("s2", "[[ShellBot: late]]") in api.sent, api.sent)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
