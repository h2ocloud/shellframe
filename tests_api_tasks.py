#!/usr/bin/env python3
"""`sfctl task` (api_tasks.py over state_store.py), with a stand-in app.

Asked for: bots keep their work as tasks that outlive a session, hand them to each other,
and leave the person a list of what waits for their decision.
  - who acts is the calling tab: its bot's roster name, else its title, else its sid; an
    argument cannot claim to be another bot
  - an owner given by any of a bot's names is that bot; `user` is the person; `me` the caller
  - add / list / mine / show / update / note / done / drop / stuck answer in one line per task
  - `mine` also lists what other bots wait on this one
  - a store that cannot be used answers in plain words, and the monitor's tick never raises
  - the wiring: the dispatcher's "task", the monitor's tick, sfctl's parser
Demo data only. Run: .venv/bin/python tests_api_tasks.py
"""
import io
import json
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.modules.setdefault("webview", MagicMock())
import state_store as ss  # noqa: E402
import api_tasks  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


api_tasks._ = lambda en, zh: zh                 # the answers in Chinese, as on this computer


class App(api_tasks.TasksApiMixin):
    def __init__(self):
        self.sessions = {"s1": types.SimpleNamespace(_custom_label="示範助理-CLD"),
                         "s2": types.SimpleNamespace(_custom_label="示範秘書"),
                         "s9": types.SimpleNamespace(_custom_label="臨時分頁")}
        self.roles = {"s1": "示範助理", "s2": "示範秘書"}

    def _bots_role_of(self, sid):
        return self.roles.get(sid, "")

    def _resolve_name(self, name, prefix=True):
        names = {"示範助理": "示範助理", "示範助理-cld": "示範助理", "示範秘書": "示範秘書"}
        role = names.get(str(name).lower())
        return {"ok": bool(role), "role": role or "", "sid": ""}


app = App()


def task(sid, action, **kw):
    return app._tasks_sfctl({"action": action, "from_sid": sid, **kw})


print("who acts")
r = task("s1", "add", title="整理這週的筆記", next="先列出標題")
t = r["details"]["task"]
check("a bot's tab acts as that bot, and owns what it adds by default",
      r["success"] and t["owner"] == "示範助理" and t["requester"] == "示範助理" and t["events"][0]["actor"] == "示範助理", r)
check("…and the answer is the task on one line, with its next step",
      r["message"].startswith("已新增 #1 [待辦] 整理這週的筆記") and "下一步：先列出標題" in r["message"], r["message"])
r = task("s9", "add", title="臨時分頁記一件事")
check("a tab that is no bot acts under its title", r["details"]["task"]["owner"] == "臨時分頁")
r = task("", "add", title="外面記的")
check("outside a tab: sfctl", r["details"]["task"]["requester"] == "sfctl")

print("owners by name")
r = task("s1", "add", title="回覆示範客戶", owner="示範秘書", due="+2d")
check("an owner by a bot's name", r["details"]["task"]["owner"] == "示範秘書" and "期限" in r["message"])
r = task("s1", "add", title="示範助理-CLD 的事", owner="示範助理-CLD")
check("…or by another of its names: its roster name is kept", r["details"]["task"]["owner"] == "示範助理")
r = task("s2", "add", title="要不要換新的排程方式", decision=True)
d = r["details"]["task"]
check("--decision: a decision, owned by the person", d["kind"] == "decision" and d["owner"] == ss.USER
      and "決定：" in r["message"] and "負責 使用者" in r["message"], r["message"])
check("a due date it cannot read is refused in words",
      not task("s1", "add", title="x", due="next tuesday")["success"]
      and "期限" in task("s1", "add", title="x", due="next tuesday")["message"])

print("lists")
r = task("s1", "list", decisions=True)
check("--decisions: what waits for the person", [x["title"] for x in r["details"]["tasks"]] == ["要不要換新的排程方式"], r["message"])
r = task("s1", "list", owner="示範秘書")
check("--owner NAME: that bot's open tasks", {x["title"] for x in r["details"]["tasks"]} == {"回覆示範客戶"}, r["message"])
r = task("s1", "update", id="1", waiting_on="示範秘書")
check("waiting on someone: the task says so, and becomes 'waiting'",
      r["details"]["task"]["status"] == "waiting" and "等 示範秘書" in r["message"], r["message"])
r = task("s2", "mine")
check("mine: this bot's open tasks, and what others wait on it",
      {x["title"] for x in r["details"]["tasks"]} == {"回覆示範客戶"}
      and [x["id"] for x in r["details"]["waiting_on_me"]] == [1] and "在等你的：" in r["message"], r["message"])

print("show, note, done, drop")
task("s1", "note", id="#1", text="標題列好了")
r = task("s1", "show", id="1")
check("show: the task, then what happened, who and when",
      r["message"].splitlines()[0].startswith("#1 [等別人] 整理這週的筆記")
      and any("示範助理  note  標題列好了" in x for x in r["message"].splitlines()), r["message"])
r = task("s1", "done", id="1", text="已寄給示範秘書")
t = ss.get(1)
check("done with a result: the result is kept, the task closed, no longer waiting on anyone",
      r["success"] and t["status"] == "done" and t["waiting_on"] == ""
      and any(e["kind"] == "result" and e["text"] == "已寄給示範秘書" for e in t["events"]), t["events"])
r = task("s2", "drop", id="2", text="客戶改用電話")
check("drop with why", ss.get(2)["status"] == "dropped" and any(e["kind"] == "why" for e in ss.get(2)["events"]))
check("closed ones leave the open list, and come back with --all",
      1 not in [x["id"] for x in task("s1", "list")["details"]["tasks"]]
      and 1 in [x["id"] for x in task("s1", "list", all=True)["details"]["tasks"]])

print("refusals")
check("no such task", task("s1", "show", id="99")["message"] == "沒有 #99 這件任務。")
check("no number", "任務編號" in task("s1", "note", id="", text="x")["message"])
check("an update with nothing to change", "沒有要改的欄位" in task("s1", "update", id="3")["message"])
check("an unknown action", not task("s1", "fly")["success"])

print("when the store cannot be used")
real_listing = ss.listing


def broken(*a, **k):
    raise ss.Unavailable("open_failed", "The task database could not be opened (x).", "任務資料庫打不開（x）。")


ss.listing = broken
r = task("s1", "list")
check("sfctl answers in plain words, with a code", not r["success"] and r["message"] == "任務資料庫打不開（x）。"
      and r["details"]["code"] == "open_failed")
check("the page's overview says the same, as JSON", json.loads(app.tasks_overview())["code"] == "open_failed")
ss.listing = real_listing
ss.maintain = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
app._tasks_tick(1000.0)
app._tasks_tick(1000.0 + api_tasks.FIRST_TICK_S + 1)
import time as _t  # noqa: E402
for _i in range(50):
    if not getattr(app, "_tasks_busy", False):
        break
    _t.sleep(0.02)
check("the monitor's tick never raises, and the upkeep thread lets go after a failure",
      getattr(app, "_tasks_busy", None) is False and app._tasks_next > 1000.0)

print("stuck")
api_tasks.ss.stuck = lambda *a, **k: [dict(ss.get(3, events=False), idle_s=3 * 3600)]
r = task("s1", "stuck")
check("stuck: each with how long nothing happened", "3 小時沒動靜" in r["message"], r["message"])

print("the wiring")
remote = (HERE / "api_remote.py").read_text(encoding="utf-8")
check("the sfctl dispatcher sends 'task' here", 'elif cmd == "task":' in remote and "self._tasks_sfctl(args)" in remote)
status = (HERE / "api_status.py").read_text(encoding="utf-8")
check("the status monitor calls the tick", '"_tasks_tick"' in status)
main_src = (HERE / "main.py").read_text(encoding="utf-8")
check("the Api has the mixin", "TasksApiMixin):" in main_src and "from api_tasks import TasksApiMixin" in main_src)
import sfctl  # noqa: E402
sent = {}
sfctl._rpc = lambda cmd, args=None, timeout=15.0: sent.update(cmd=cmd, args=args) or {"success": True, "message": "ok"}
sys.argv = ["sfctl", "task", "note", "12", "標題", "列好了", "--due", "+1d"]
with redirect_stdout(io.StringIO()):
    rc = sfctl.main()
check("sfctl task note 12 words…: the number, the text, from this tab",
      rc == 0 and sent["cmd"] == "task" and sent["args"]["id"] == "12" and sent["args"]["text"] == "標題 列好了"
      and sent["args"]["due"] == "+1d" and "from_sid" in sent["args"], sent)
sys.argv = ["sfctl", "task", "add", "寫", "週報", "--owner", "user", "--decision"]
with redirect_stdout(io.StringIO()):
    sfctl.main()
check("sfctl task add words…: the title", sent["args"]["title"] == "寫 週報" and sent["args"]["decision"] is True)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
