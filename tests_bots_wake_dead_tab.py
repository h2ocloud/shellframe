#!/usr/bin/env python3
"""「叫醒」 on a bot whose tab is dead really wakes it (api_bots.bots_open).

Measured: a codex bot whose CLI had exited (and one whose tmux pane was gone) was still bound to its
session in `self.sessions`, so bots_open answered "already awake" and did nothing, while the sidebar
(list_sessions drops a tab that is not alive) showed it asleep.

  - bound tab not alive: bots_open closes it (reason bot-dead: no memory flow against a dead process),
    opens a new tab, binds it, and says it is waking, not "already awake"
  - alive process but its tmux session is gone (tmux answers "no such session"): the same
  - tmux that does not answer (timeout) or is missing counts as alive: a slow tmux must not get a live
    bot closed and duplicated
  - a live tab is still "already awake" and nothing is opened
  - sidebar, `sfctl bots` and /link/bots call a dead-tab bot asleep, as bots_open does, and the tab a
    bot gets again is the new one

Demo roster and fake tabs. Run: .venv/bin/python tests_bots_wake_dead_tab.py
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-bots-dead-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import api_bots  # noqa: E402
import main  # noqa: E402
import tmux_sessions  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
api_bots._CLIS.update(at=1e12, list=[{"key": "codex", "label": "Codex", "installed": True, "models": False}])
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
    def __init__(self, sid, cmd, label="", alive=True, tmux=None):
        self.sid, self.cmd, self._custom_label, self.alive = sid, cmd, label, alive
        self._tmux_name, self._recent, self.lock = tmux, bytearray(), threading.Lock()
        self._startup_trust_pending, self._init_pending = True, True
        self._last_output_activity_time = 0.0


def write_config(bound):
    roster = {"Scribe": {"label": "Scribe", "cmd": "sf-codex", "bot": {"memory": True}}}
    main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_bots": True}, "agent_roster": roster,
                                            "bots_sessions": {sid: {"bot": "Scribe"} for sid in bound}}), encoding="utf-8")


api = Api.__new__(Api)
api.sessions = {}
api.bridge = api.line_bridge = None
api._window = None
api.opened, api.closed = [], []
counter = [10]


def new_session(cmd, cols, rows, source="manual", handoff=False, inherit_accounts=True):
    counter[0] += 1
    sid = f"s{counter[0]}"
    api.sessions[sid] = Tab(sid, cmd)
    api.opened.append(sid)
    return sid


def close_session(sid, reason="manual", **k):
    api.closed.append((sid, reason))
    api.sessions.pop(sid, None)


api.new_session = new_session
api.close_session = close_session
api.rename_session = lambda sid, name, manual=False: (setattr(api.sessions[sid], "_custom_label", name), '{"success": true}')[1]
api._send_text_to_session = lambda s, text, submit=False: True
api._wait_until_ready = lambda sid, timeout=45.0: sid in api.sessions
api._agent_state_for_list = lambda sid: ""
api._agent_status_snapshot = lambda sid: ({}, 0)
api._agent_blocked_for_list = lambda sid: ""
api._last_activity_at = lambda sid, s, out_ts=0.0: 0.0
api._ordered_sids = lambda cfg=None, preferred_order=None: list(api.sessions)
api._bots_notify_ui = lambda: None
api.preset_groups = lambda: json.dumps([{"provider": "codex", "items": [{"cmd": "sf-codex"}]}])

main._tmux_bin = lambda: "/usr/bin/tmux"
gone_answer = {"v": False}
tmux_sessions.session_gone = lambda tmux, name, timeout=3.0: gone_answer["v"]


def state_in_sfctl():
    rows = api._bots_list_for_sfctl()["details"]["bots"]
    return next(r for r in rows if r["role"] == "Scribe")


def state_in_link():
    rows = api._execute_sfctl("bots_link", {})["bots"]
    return next(r for r in rows if r["role"] == "Scribe")


def reset(tab):
    api.sessions.clear()
    api.opened.clear()
    api.closed.clear()
    api.sessions[tab.sid] = tab
    write_config([tab.sid])


print("a tab whose process ended")
reset(Tab("s1", "sf-codex", "Scribe", alive=False))
check("before: sfctl bots and /link/bots both call the bot asleep (as the sidebar does)",
      state_in_sfctl()["state"] == "asleep" and state_in_link()["state"] == "asleep" and state_in_sfctl()["sid"] == "", (state_in_sfctl(), state_in_link()))
res = json.loads(api.bots_open("Scribe"))
check("bots_open does not say already awake: it wakes the bot on a new tab",
      res["success"] and not res["details"].get("existed") and res["details"]["sid"] == api.opened[-1] and "Waking" in res["message"], res)
check("…the dead tab was closed with a reason that skips the memory flow", api.closed == [("s1", "bot-dead")] and "s1" not in api.sessions, api.closed)
new_sid = res["details"]["sid"]
check("…the new tab is bound to the bot", json.loads(main.CONFIG_FILE.read_text())["bots_sessions"].get(new_sid, {}).get("bot") == "Scribe")
check("…and now sfctl bots and /link/bots call it awake in that tab",
      state_in_sfctl()["state"] == "awake" and state_in_sfctl()["sid"] == new_sid and state_in_link()["state"] == "awake" and state_in_link()["sid"] == new_sid)
again = json.loads(api.bots_open("Scribe"))
check("waking it again: already awake, nothing more opened", again["details"].get("existed") is True and again["details"]["sid"] == new_sid and len(api.opened) == 1, again)
check("bot-dead is one of the reasons the memory flow leaves alone", "bot-dead" in api_bots.SELF_REASONS)

print("a process that runs in a tmux session that is gone")
reset(Tab("s2", "sf-codex", "Scribe", alive=True, tmux="sf_s2"))
gone_answer["v"] = True
res = json.loads(api.bots_open("Scribe"))
check("tmux says no such session: closed and reopened", res["success"] and not res["details"].get("existed") and api.closed == [("s2", "bot-dead")], (res, api.closed))
print("tmux that does not say so (slow, missing) counts as alive")
reset(Tab("s3", "sf-codex", "Scribe", alive=True, tmux="sf_s3"))
gone_answer["v"] = False
res = json.loads(api.bots_open("Scribe"))
check("already awake, nothing closed or opened", res["details"].get("existed") is True and not api.closed and not api.opened, (res, api.closed))

print("tmux_sessions.session_gone itself")
import importlib  # noqa: E402
importlib.reload(tmux_sessions)
real_run = tmux_sessions.subprocess.run


class R:
    def __init__(self, code):
        self.returncode = code


tmux_sessions.subprocess.run = lambda *a, **k: R(1)
check("tmux answered, no such session: gone", tmux_sessions.session_gone("/usr/bin/tmux", "sf_s9") is True)
tmux_sessions.subprocess.run = lambda *a, **k: R(0)
check("tmux answered, it exists: not gone", tmux_sessions.session_gone("/usr/bin/tmux", "sf_s9") is False)


def slow(*a, **k):
    raise tmux_sessions.subprocess.TimeoutExpired("tmux", 3)


tmux_sessions.subprocess.run = slow
check("tmux timed out: not gone", tmux_sessions.session_gone("/usr/bin/tmux", "sf_s9") is False)
tmux_sessions.subprocess.run = real_run
check("no tmux, or no name: not gone", tmux_sessions.session_gone(None, "sf_s9") is False and tmux_sessions.session_gone("/usr/bin/tmux", "") is False)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
