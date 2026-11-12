#!/usr/bin/env python3
"""A bot that is writing its memory is not deleted, and a bot deleted anyway gets no folder back (#46).

Found on the v0.42.0 release candidate: `sfctl close` on a bot tab ("it is writing its memory and
closes when done"), then `sfctl bots-delete` on that bot, then the memory folder moved away. When
the bot finished, it wrote the summary into the old path: the folder was there again, with a
MEMORY.md and a summary in it, for a bot the roster no longer had.

  - bots_delete (the desktop's Delete, sfctl bots-delete, a phone) is refused while the bot's tab is
    closing: the roster, the binding and the memory are as they were, the message says why (English
    and Chinese), the code is "busy" like the rename's and the model switch's refusals
  - once the tab has closed it can be deleted, and its memory folder stays, as it always did
  - a bot whose roster entry goes by another way while its tab waits (a hand edit of config.json)
    is asked for nothing, no folder is made for it, and the tab is still closed
  - and one that goes while the tab is not yet ready for the prompt is not asked either
  - a bot that is there is written as before (tests_bots.py has the whole flow)

Demo roster and tabs. Run: .venv/bin/python tests_bots_delete_closing.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-bots-del-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")   # fake tabs stay out of the real log

import api_bots  # noqa: E402
import bots  # noqa: E402
import main  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
api_bots.SUMMARY_WAIT_SEC = 3
api_bots.BUSY_WAIT_SEC = 20

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


class FakeSession:
    def __init__(self, sid, cmd, label=""):
        self.sid, self.cmd, self._custom_label = sid, cmd, label
        self.alive = True
        self.lock = threading.Lock()
        self._recent = bytearray(b"last screen line\n")
        self._tmux_name = None
        self._startup_trust_pending = True
        self._init_pending = True
        self.state = ""


def write_config(roster, bound):
    cfg = {"settings": {"experimental_bots": True}, "agent_roster": roster,
           "bots_sessions": {sid: {"bot": role} for sid, role in bound.items()}}
    main.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def demo_roster():
    return {
        # a role the bots feature made (a delete takes all of it) and one that was in the roster before
        "Scribe": {"label": "Scribe", "cmd": "claude", "agent_code": "BOT", "handoff": True,
                   "bot": {"memory": True, "created_by_bots": True}},
        "Reviewer": {"label": "Reviewer", "cmd": "claude", "handoff": True, "bot": {"memory": True}},
    }


def cfg():
    return json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))


def make_api(writes_memory=True):
    api = Api.__new__(Api)
    api.sessions = {}
    api.bridge = api.line_bridge = None
    api._window = None
    api.sent, api.closed = [], []                      # (sid, text), (sid, reason)

    def send(s, text, submit=False):
        api.sent.append((s.sid, text))
        import re
        m = re.search(r"寫成新檔 (\S+\.md)", text)
        if m and writes_memory:                        # the bot does what it is asked
            p = Path(m.group(1))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# 摘要\n- 做了 X\n", encoding="utf-8")
        return True

    def close_session(sid, reason="manual", handoff=False, summary_path="", idle_seconds=None):
        api.closed.append((sid, reason))
        api.sessions.pop(sid, None)

    api._send_text_to_session = send
    api.close_session = close_session
    api._wait_until_ready = lambda sid, timeout=45.0: sid in api.sessions
    api._agent_state_for_list = lambda sid: getattr(api.sessions.get(sid), "state", "")
    return api


def wait_for(cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


print("a bot that is writing its memory is not deleted")
shutil.rmtree(TMP / "bots", ignore_errors=True)
write_config(demo_roster(), {"s1": "Scribe"})
api = make_api()
api.sessions["s1"] = FakeSession("s1", "claude", "Scribe")
api.sessions["s1"].state = "working"                   # the flow waits for the turn to end first
check("closing the tab starts the memory flow", json.loads(api.bots_close("s1", "save"))["success"])
res = json.loads(api.bots_delete("Scribe"))
check("Delete is refused, with the code the other busy refusals have",
      res.get("success") is False and res.get("code") == "busy", res)
check("…and the Chinese message says it is writing its memory", "正在寫記憶，寫完再刪" in res.get("message", ""), res)
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
res_en = json.loads(api.bots_delete("Scribe"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
check("…and so does the English one", "writing its memory" in res_en.get("message", "")
      and "delete it when that is done" in res_en["message"], res_en)
res = api._execute_sfctl("bots_delete", {"role": "Scribe"})
check("sfctl bots-delete is refused the same way", res.get("success") is False and res.get("code") == "busy"
      and "正在寫記憶" in res.get("message", ""), res)
c = cfg()
check("the roster, the binding and the tab are as they were",
      "Scribe" in c["agent_roster"] and c["bots_sessions"].get("s1") == {"bot": "Scribe"} and "s1" in api.sessions,
      (c["agent_roster"], c["bots_sessions"]))
check("another bot can still be deleted meanwhile", json.loads(api.bots_delete("Reviewer")).get("success") is True)

api.sessions["s1"].state = "done"                      # the turn ends: the bot is asked and writes
check("the tab closes once the memory is written", wait_for(lambda: ("s1", "bot-memory") in api.closed))
folder = bots.bot_dir("Scribe")
check("…its summary is in its folder", any(folder.joinpath("sessions").glob("*.md")))
check("…and nothing is left marked as closing", not api_bots._CLOSING)
res = json.loads(api.bots_delete("Scribe"))
c = cfg()
check("now it can be deleted: role and binding go, the memory folder stays",
      res.get("success") is True and "Scribe" not in c["agent_roster"] and "s1" not in c["bots_sessions"]
      and folder.is_dir() and any(folder.joinpath("sessions").glob("*.md")), res)

print("a bot removed another way while its tab waits gets no folder back")
shutil.rmtree(TMP / "bots", ignore_errors=True)
write_config(demo_roster(), {"s2": "Scribe"})
api = make_api()
api.sessions["s2"] = FakeSession("s2", "claude", "Scribe")
api.sessions["s2"].state = "working"
api.bots_close("s2", "save")
time.sleep(0.3)
c = cfg()                                              # what the incident did: the entry goes, the folder is moved away
c["agent_roster"].pop("Scribe")
c["bots_sessions"].pop("s2")
main.CONFIG_FILE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
shutil.rmtree(bots.bot_dir("Scribe"), ignore_errors=True)
api.sessions["s2"].state = "done"
check("the tab is still closed", wait_for(lambda: ("s2", "bot-memory") in api.closed), api.closed)
check("the bot is not asked to write anything", not any("寫成新檔" in t for _, t in api.sent), api.sent)
check("no memory folder is made for a bot the roster no longer has", not bots.bot_dir("Scribe").exists(),
      list(bots.memory_root().glob("*")))
check("nothing is left marked as closing", not api_bots._CLOSING)

print("a bot removed while the tab is not ready for the prompt")
shutil.rmtree(TMP / "bots", ignore_errors=True)
write_config(demo_roster(), {"s3": "Scribe"})
api = make_api()
api.sessions["s3"] = FakeSession("s3", "claude", "Scribe")
asked = []


def ready_after_removal(sid, timeout=45.0):
    """The tab takes its time to come back to the prompt; meanwhile the bot is removed."""
    if not asked:
        asked.append(sid)
        c = cfg()
        c["agent_roster"].pop("Scribe")
        c["bots_sessions"].pop("s3")
        main.CONFIG_FILE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    return sid in api.sessions


api._wait_until_ready = ready_after_removal
api.bots_close("s3", "save")
check("the tab is closed", wait_for(lambda: ("s3", "bot-memory") in api.closed), api.closed)
check("it was not asked, and no folder was made", not api.sent and not bots.bot_dir("Scribe").exists(),
      (api.sent, list(bots.memory_root().glob("*"))))

print("a bot that is there is written as before")
shutil.rmtree(TMP / "bots", ignore_errors=True)
write_config(demo_roster(), {"s4": "Scribe"})
api = make_api()
api.sessions["s4"] = FakeSession("s4", "claude", "Scribe")
api.bots_close("s4", "save")
check("closed with its summary written", wait_for(lambda: ("s4", "bot-memory") in api.closed)
      and any(bots.bot_dir("Scribe").joinpath("sessions").glob("*.md")))
write_config(demo_roster(), {"s5": "Scribe"})
api = make_api(writes_memory=False)                    # a bot that never answers still leaves the screen
api.sessions["s5"] = FakeSession("s5", "claude", "Scribe")
before = set(bots.sessions_dir("Scribe").glob("*.md"))
api.bots_close("s5", "save")
check("…a bot that does not answer still leaves a file", wait_for(lambda: ("s5", "bot-memory") in api.closed, 20)
      and len(set(bots.sessions_dir("Scribe").glob("*.md")) - before) == 1)

print(f"\n{passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
