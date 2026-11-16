#!/usr/bin/env python3
"""Hand-offs tied to a task (`--for-task N` on a2a-send / a2a-handoff; a2a_host → api_tasks), and a
waking bot's brief listing its open tasks (bots.compose_brief).

Asked for: follow one piece of work across bots: who got it, whether they answered, who it waits on.
  - a hand-off for task N: the message says which task (and how to write on it); the task records
    the hand-off with its id and waits on whoever got it
  - to another computer: the task stays here; the message says to answer with a2a-result
  - a closed task or one that does not exist: refused before anything is sent
  - the result (from this computer or another): written on the task, which goes back to doing
  - a message that did not go: nothing recorded; a plain send records, and does not wait
  - a waking bot reads its open tasks and what others wait on it; without tasks its brief is as before
Demo data only; A2A itself is a stand-in. Run: .venv/bin/python tests_tasks_a2a.py
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.modules.setdefault("webview", MagicMock())
import state_store as ss  # noqa: E402
import api_tasks  # noqa: E402
import a2a_host  # noqa: E402
import bots  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


api_tasks._ = lambda en, zh: zh


class App(api_tasks.TasksApiMixin):
    def __init__(self):
        self.sessions = {"s1": types.SimpleNamespace(_custom_label="示範助理"),
                         "s2": types.SimpleNamespace(_custom_label="示範秘書")}

    def _bots_role_of(self, sid):
        return {"s1": "示範助理", "s2": "示範秘書"}.get(sid, "")

    def _resolve_name(self, name, prefix=True):
        role = {"示範秘書": "示範秘書", "示範助理": "示範助理"}.get(name)
        return {"ok": bool(role), "role": role or ""}


app = App()
sent = []
outcome = {"ok": True}
a2a_host._enabled = lambda api: True
a2a_host._readings = lambda: {}
a2a_host.agent_route.hint = lambda *a, **k: {}


def fake_dispatch(ctx, *, target, text, kind, **kw):
    sent.append({"target": target, "text": text, "kind": kind})
    n = len(sent)
    return {"success": outcome["ok"], "message": "ok" if outcome["ok"] else "no", "details": {"id": f"msg{n}"}}


a2a_host.agent_link.dispatch = fake_dispatch
a2a_host.agent_link.report_result = lambda ctx, hid, text, **kw: {"success": hid.startswith("msg"), "details": {"id": hid}}
a2a_host.agent_link.accept_remote = lambda ctx, args: {"success": True, "details": {"id": args.get("id")}}

t = ss.add("整理這週的筆記", actor="示範助理", owner="示範助理")
N = t["id"]

print("a hand-off for a task")
r = a2a_host.handle(app, "a2a_handoff", {"target": "示範秘書", "text": "幫我寄出筆記", "from_sid": "s1", "for_task": str(N)})
t = ss.get(N)
check("it goes, and the message says which task and how to write on it",
      r["success"] and f"任務 #{N}" in sent[-1]["text"] and f"sfctl task note {N}" in sent[-1]["text"]
      and sent[-1]["text"].startswith("幫我寄出筆記"), sent[-1]["text"])
check("the task records the hand-off with the message's id, by whom",
      any(e["kind"] == "handoff" and e["ref"] == "msg1" and e["actor"] == "示範助理" and "示範秘書" in e["text"] for e in t["events"]),
      t["events"])
check("…and waits on whoever got it", t["status"] == "waiting" and t["waiting_on"] == "示範秘書", (t["status"], t["waiting_on"]))

print("the result")
r = a2a_host.handle(app, "a2a_result", {"id": "msg1", "text": "已寄出，收件人三位", "from_sid": "s2"})
t = ss.get(N)
check("written on the task, by the one who answered", any(e["kind"] == "result" and e["ref"] == "msg1" and e["actor"] == "示範秘書"
                                                          and e["text"] == "已寄出，收件人三位" for e in t["events"]), t["events"])
check("…and the task goes back to its owner, doing", t["status"] == "doing" and t["waiting_on"] == "")
before = len(ss.get(N)["events"])
a2a_host.handle(app, "a2a_result", {"id": "other-id", "text": "x", "from_sid": "s2"})
a2a_host.handle(app, "a2a_result", {"id": "msg-unknown", "text": "x", "from_sid": "s2"})
check("a result for a hand-off no task asked for changes nothing", len(ss.get(N)["events"]) == before)

print("another computer")
r = a2a_host.handle(app, "a2a_handoff", {"target": "示範電腦/示範工程師", "text": "跑一次測試", "from_sid": "s1", "for_task": f"#{N}"})
t = ss.get(N)
check("the message says the task is on the sender's computer, answer with a2a-result",
      "對方電腦上任務" in sent[-1]["text"] and "a2a-result" in sent[-1]["text"] and "sfctl task note" not in sent[-1]["text"])
check("the task waits on that machine's bot by its full address", t["waiting_on"] == "示範電腦/示範工程師")
hid = f"msg{len(sent)}"
a2a_host.handle(app, "a2a_deliver", {"kind": "result", "id": hid, "text": "測試全過", "from_label": "示範電腦/示範工程師"})
t = ss.get(N)
check("its result, arriving from that computer, is written on the task too",
      any(e["kind"] == "result" and e["ref"] == hid and e["actor"] == "示範電腦/示範工程師" for e in t["events"])
      and t["status"] == "doing", t["events"][-2:])

print("refused before sending")
n_sent = len(sent)
r = a2a_host.handle(app, "a2a_handoff", {"target": "示範秘書", "text": "x", "from_sid": "s1", "for_task": "999"})
check("no such task: refused, nothing sent", not r["success"] and "沒有 #999" in r["message"] and len(sent) == n_sent)
done = ss.add("早就做完的", owner="示範助理")
ss.update(done["id"], status="done")
r = a2a_host.handle(app, "a2a_send", {"target": "示範秘書", "text": "x", "from_sid": "s1", "for_task": str(done["id"])})
check("a closed task: refused, nothing sent", not r["success"] and "已經結束" in r["message"] and len(sent) == n_sent)
r = a2a_host.handle(app, "a2a_send", {"target": "示範秘書", "text": "x", "from_sid": "s1", "for_task": "abc"})
check("not a number: refused", not r["success"] and "不是任務編號" in r["message"])
r = a2a_host.handle(app, "a2a_send", {"target": "示範秘書", "text": "沒有掛任務", "from_sid": "s1"})
check("without --for-task the message is as it was", r["success"] and sent[-1]["text"] == "沒有掛任務")

print("a send, and one that did not go")
before = len(ss.get(N)["events"])
a2a_host.handle(app, "a2a_send", {"target": "示範秘書", "text": "順便說一聲", "from_sid": "s1", "for_task": str(N)})
t = ss.get(N)
check("a plain send is recorded and does not make the task wait",
      t["events"][-1]["kind"] == "send" and t["status"] == "doing" and len(t["events"]) == before + 1)
outcome["ok"] = False
before = len(ss.get(N)["events"])
a2a_host.handle(app, "a2a_handoff", {"target": "示範秘書", "text": "x", "from_sid": "s1", "for_task": str(N)})
check("a message that did not go: nothing recorded", len(ss.get(N)["events"]) == before and ss.get(N)["status"] == "doing")
outcome["ok"] = True

print("a waking bot's brief")
ss.update(N, waiting_on="示範秘書")
own = app._tasks_brief("示範助理")
theirs = app._tasks_brief("示範秘書")
check("its own open tasks", f"#{N}" in own and "整理這週的筆記" in own, own)
check("…and, for the other bot, what waits on it", "在等你的：" in theirs and f"#{N}" in theirs, theirs)
entry = {"label": "示範助理", "bot": {"memory": True}}
plain = bots.compose_brief("示範助理", entry, {"示範助理": entry}, lang="zh")
with_t = bots.compose_brief("示範助理", entry, {"示範助理": entry}, lang="zh", tasks=own)
check("the brief lists them under their own heading", "你未完成的任務" in with_t and "整理這週的筆記" in with_t)
check("without tasks the brief is exactly as before", plain == bots.compose_brief("示範助理", entry, {"示範助理": entry}, lang="zh", tasks="")
      and "你未完成的任務" not in plain)
real = ss.listing
ss.listing = lambda *a, **k: (_ for _ in ()).throw(ss.Unavailable("open_failed", "x", "x"))
check("a store that cannot be used: no tasks in the brief, no error", app._tasks_brief("示範助理") == "")
ss.listing = real

print("the wiring")
src = (HERE / "api_bots.py").read_text(encoding="utf-8")
check("the brief asks the tasks mixin", '"_tasks_brief"' in src)
import sfctl  # noqa: E402
got = {}
sfctl._rpc = lambda cmd, args=None, timeout=15.0: got.update(cmd=cmd, args=args) or {"success": True, "message": "ok"}
sfctl._print_result = lambda *a, **k: None
sys.argv = ["sfctl", "a2a-handoff", "示範秘書", "寄出筆記", "--for-task", "7"]
sfctl.main()
check("sfctl a2a-handoff … --for-task 7 passes it on", got["cmd"] == "a2a_handoff" and got["args"]["for_task"] == "7")
sys.argv = ["sfctl", "a2a-send", "示範秘書", "說一聲", "--for-task", "7"]
sfctl.main()
check("…and a2a-send", got["cmd"] == "a2a_send" and got["args"]["for_task"] == "7")

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
