#!/usr/bin/env python3
"""Push wired into the app (api_push): the phone's routes, a phone renaming itself, and
the cues the status monitor turns into pushes and withdrawals.

  - register / status / unregister / test reach sf_push with the caller's peer id
  - /link/peer/name renames only the caller, at most 60 characters
  - a turn that ends on [[SF:RED]] is pushed once as a decision with the id /link/bots
    shows, hooks or not; a new turn withdraws it; an answer from one phone withdraws it
    from the others
  - the same question caught twice (Stop hook and monitor) keeps its id
  - a dialog is `stuck`; [[SF:YELLOW:…]] is `stuck` with its reason; a finished turn is `done`
  - nothing at all happens while no phone has registered
  - a delegated task's result is `done`; sfctl notify reaches the phones first
  - Telegram's sendMessage goes quietly while push works for a phone that asked for it

Run: .venv/bin/python tests_push_app.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-push-app-"))
os.environ["SHELLFRAME_PUSH_FILE"] = str(TMP / "push.json")
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import api_push  # noqa: E402
import bridge_home  # noqa: E402
import main  # noqa: E402
import sf_push  # noqa: E402
from main import Api  # noqa: E402
main.CONFIG_FILE = TMP / "config.json"
main.save_config(main.load_config())

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class Link:
    def __init__(self):
        self.block = {"frame_id": "f" * 32, "frame_name": "公司 Mac",
                      "peers": {"phone": {"name": "iPhone"}, "ipad": {"name": "iPad"}}}

    def frame_id(self):
        return self.block["frame_id"]

    def frame_name(self):
        return self.block["frame_name"]

    def peers(self):
        return dict(self.block["peers"])

    def _mutate_cfg(self, fn):
        fn(self.block)


class Recorder:
    def __init__(self):
        self.sent, self.withdrawn = [], []

    def send(self, kind, **kw):
        self.sent.append(dict(kw, kind=kind))
        return 1

    def withdraw(self, ids, **kw):
        self.withdrawn.append((list(ids), set(kw.get("exclude") or ())))
        return 1

    def test(self, peer, **kw):
        return True, f"test to {peer}"


class Tab:
    def __init__(self, label):
        self._custom_label = label
        self.cmd = "claude"
        self.written = []

    def write(self, data):
        self.written.append(data)


STATUS = {}
api = Api.__new__(Api)
api.sessions = {"s1": Tab("小幫手"), "s2": Tab("寫程式")}
api.bridge = api.line_bridge = None
api.frame_link = Link()
api._hook_events = {}
api._agent_status_snapshot = lambda sid: (dict(STATUS.get(sid, {}), prompt_at=api._hook_events.get(sid, {}).get("prompt_at", 0.0)), 0)
api._agent_state_for_list = lambda sid: STATUS.get(sid, {}).get("state", "")
rec = Recorder()
api._sf_pusher = rec
EVS = {}


def turn_end_sync(sid, res):     # the thread body, run in place
    api_push.PushApiMixin._push_turn_end(api, sid, res)


api._push_turn_end = turn_end_sync


class SyncThread:                # the turn-end reader runs in place, so a check right after sees it
    def __init__(self, target=None, args=(), kwargs=None, **_):
        self._run = lambda: target(*args, **(kwargs or {}))

    def start(self):
        self._run()


import types  # noqa: E402
api_push.threading = types.SimpleNamespace(Thread=SyncThread)   # only api_push's view of threading
import agent_status  # noqa: E402
agent_status.resolve_transcript = lambda worker: "t.jsonl"
agent_status._read_tail_events = lambda path, **kw: ("claude", EVS.get("now", []), None)


def run(states):
    for sid, st in states.items():
        STATUS[sid] = dict(STATUS.get(sid, {}), state=st)
    api._push_observe({sid: dict(STATUS[sid]) for sid in STATUS})


print("nothing without a registered phone")
STATUS.update(s1={"state": "working"}, s2={"state": "idle"})
run({})
run({"s1": "done"})
check("no phone registered: the monitor hook does nothing", not rec.sent and not hasattr(api, "_push_tracker"))

print("the phone's routes")
r = api._push_route("push_register", {"peer_id": "phone", "token": "ab" * 32, "env": "sandbox"})
check("register reaches sf_push with the caller's id", r.get("success") and "phone" in sf_push.devices(), r)
check("status answers for that phone", api._push_route("push_status", {"peer_id": "phone"})["registered"] is True)
check("test goes to the pusher for that phone", api._push_route("push_test", {"peer_id": "phone"})["message"] == "test to phone")
r = api._push_route("peer_name", {"peer_id": "ipad", "name": "  iPad   mini (A17 Pro) " + "x" * 80})
check("a phone renames itself, tidied and cut to 60",
      r["success"] and api.frame_link.block["peers"]["ipad"]["name"].startswith("iPad mini (A17 Pro) x")
      and len(api.frame_link.block["peers"]["ipad"]["name"]) == 60 and api.frame_link.block["peers"]["phone"]["name"] == "iPhone")
check("an unknown peer cannot rename anything", not api._push_route("peer_name", {"peer_id": "nobody", "name": "x"})["success"])
check("a blank name is refused", not api._push_route("peer_name", {"peer_id": "ipad", "name": "  "})["success"])
api._push_route("push_register", {"peer_id": "ipad", "token": "cd" * 32, "env": "sandbox"})

print("the status monitor")
STATUS.clear()
STATUS.update(s1={"state": "working"}, s2={"state": "idle"})
run({})                                          # baseline
EVS["now"] = [{"kind": "user_msg", "text": "下一步？"},
              {"kind": "assistant_text", "text": "要先做哪一件？\n[[SF:RED]]\n1. 先排卡片\n2. 先整理規格"}]
run({"s1": "done"})                              # DONE → the turn end reads the RED
check("a turn that ends on [[SF:RED]] is caught without any hook", bool(bridge_home.red_pending("s1")))
run({})
decisions = [s for s in rec.sent if s["kind"] == "decision"]
want = api._link_decision("s1", "", api._agent_status_snapshot("s1")[0])
check("…and pushed as a decision with the id /link/bots shows",
      len(decisions) == 1 and decisions[0]["qid"] == want["id"]
      and decisions[0]["options"] == [(1, "先排卡片"), (2, "先整理規格")], decisions)
check("…with the bot's name and this computer", decisions[0]["bot"] == "小幫手" and decisions[0]["frame_name"] == "公司 Mac")
check("no 'done' for a turn that ended on a question", not [s for s in rec.sent if s["kind"] == "done"])
run({})
check("the same question is not pushed again on the next pass", len([s for s in rec.sent if s["kind"] == "decision"]) == 1)
qid = decisions[0]["qid"]
bridge_home.on_turn_end(api, "s1", read_events=lambda sid: EVS["now"])
check("caught a second time (the Stop hook too), the question keeps its id",
      api._link_decision("s1", "", api._agent_status_snapshot("s1")[0])["id"] == qid)
api._hook_events["s1"] = {"prompt_at": time.time() + 1}
time.sleep(0.01)
bridge_home.on_turn_end(api, "s1", read_events=lambda sid: EVS["now"])
check("asked again after a new prompt, it is a new question",
      bridge_home.red_pending("s1")["at"] >= api._hook_events["s1"]["prompt_at"] - 1)
api._hook_events.clear()
api.__dict__["_push_asked"]["s1"] = qid
rec.withdrawn.clear()
run({"s1": "working"})                           # the user answered on the computer
check("a new turn forgets the question and withdraws it from the phones",
      not bridge_home.red_pending("s1") and rec.withdrawn and rec.withdrawn[0][0] == [f"q.{qid}"], rec.withdrawn)

print("answered from one phone")
api._push_withdrawn = {}
api._push_answered("s1", "abc123", "phone")
check("the other phones drop it at once", rec.withdrawn[-1] == (["q.abc123"], {"phone"}), rec.withdrawn[-1])
api._push_withdraw(["q.abc123"])
check("the same withdrawal is not sent twice within a minute", rec.withdrawn[-1] == (["q.abc123"], {"phone"}))

print("stuck and done")
rec.sent.clear()
STATUS["s2"] = {"state": "idle"}
run({"s2": "decision"})
STATUS["s2"]["blocked"] = "Bash: rm -rf build"
api._push_observe({"s2": {"state": "decision", "blocked": "Bash: rm -rf build"}, "s1": dict(STATUS["s1"])})
stuck = [s for s in rec.sent if s["kind"] == "stuck"]
check("a tab stopping on a dialog is stuck, once", len(stuck) == 1, rec.sent)
rec.sent.clear()
EVS["now"] = [{"kind": "user_msg", "text": "部署"}, {"kind": "assistant_text", "text": "我在等法務回覆 [[SF:YELLOW:等法務簽核]]"}]
run({"s1": "working"})
run({"s1": "done"})
check("[[SF:YELLOW:…]] is stuck with its reason", [s.get("text") for s in rec.sent if s["kind"] == "stuck"] == ["等法務簽核"], rec.sent)
rec.sent.clear()
EVS["now"] = [{"kind": "user_msg", "text": "整理"}, {"kind": "assistant_text", "text": "做好了。\n報告在 reports/weekly.md"}]
run({"s1": "working"})
run({"s1": "done"})
done = [s for s in rec.sent if s["kind"] == "done"]
check("a finished turn is done, with its last words (the phone decides whose)",
      len(done) == 1 and done[0]["text"] == "報告在 reports/weekly.md" and done[0]["primary"] is False, done)

print("a delegated result, sfctl notify")
rec.sent.clear()
api._push_a2a_result("s2", "寫程式", "PR 開好了")
check("a delegated task's result is done, as if from the primary bot",
      rec.sent and rec.sent[-1]["kind"] == "done" and rec.sent[-1]["primary"] is True and "寫程式" in rec.sent[-1]["title"])
r = api._push_notify({"sid": "s1", "status": "QUESTION", "message": "要上線嗎？"})
check("sfctl notify is info, the status in the title", rec.sent[-1]["kind"] == "info"
      and rec.sent[-1]["title"] == "小幫手｜有問題" and rec.sent[-1]["text"] == "要上線嗎？", rec.sent[-1])
src = (HERE / "sfctl.py").read_text(encoding="utf-8")
body = src[src.index("def _cmd_notify"):]
check("sfctl notify hands it to the app first, webhook configured or not, without waiting",
      body.index('_rpc_nowait("push_notify"') < body.index("sn.load_config("))
a2a = (HERE / "a2a_host.py").read_text(encoding="utf-8")
check("a2a-result calls the push hook only when the result went through",
      'if (res or {}).get("success") and hasattr(api, "_push_a2a_result")' in a2a)

print("Telegram")
sent_payloads = []
bridge_home.SEND = lambda method, payload: sent_payloads.append((method, payload))
sf_push.telegram_quiet = lambda now=None: True
bridge_home._tg(None, "sendMessage", {"chat_id": 1, "text": "x"})
bridge_home._tg(None, "editMessageText", {"chat_id": 1, "message_id": 2, "text": "y"})
check("sendMessage is quiet while the phones ring for it", sent_payloads[0][1].get("disable_notification") is True)
check("…an edit is left alone", "disable_notification" not in sent_payloads[1][1])
sf_push.telegram_quiet = lambda now=None: False
bridge_home._tg(None, "sendMessage", {"chat_id": 1, "text": "z"})
check("…and it rings again when push is not working", "disable_notification" not in sent_payloads[2][1])

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
