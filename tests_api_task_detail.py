#!/usr/bin/env python3
"""`task_detail` (api_tasks.py): one task and what happened to it, for the office's task board.

  - the task with its events, newest first, at most 20 of them (the newest 20)
  - the owner, waiting-on, next step and due date come back as the store has them
  - a number with a leading # works; no such task, a word instead of a number, and a store that
    cannot be opened each answer in plain words with a code, never raise
  - it only reads: asking changes nothing
Demo data only. Run: .venv/bin/python tests_api_task_detail.py
"""
import json
import sys
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


api_tasks._ = lambda en, zh: zh


class App(api_tasks.TasksApiMixin):
    pass


app = App()
T0 = 1_800_000_000.0
t = ss.add("整理示範週報", actor="示範助理", owner="示範助理", requester=ss.USER, next_step="先列出標題",
           due_at=T0 + 86400, origin="sfctl", now=T0)
tid = t["id"]
for i in range(30):
    ss.note(tid, f"第 {i} 則記錄", actor="示範助理", now=T0 + 10 + i)
ss.update(tid, actor="示範助理", status="waiting", waiting_on="user", now=T0 + 100)

print("one task")
r = json.loads(app.task_detail(tid))
task = r.get("task") or {}
check("the task, with its owner, waiting-on, next step and due date",
      r["success"] and task["title"] == "整理示範週報" and task["owner"] == "示範助理" and task["waiting_on"] == "user"
      and task["next_step"] == "先列出標題" and task["due_at"] == T0 + 86400 and task["status"] == "waiting", r)
ev = task.get("events") or []
check("at most 20 events", len(ev) == 20, len(ev))
check("newest first: the two changes of the update, then the last note",
      [e["ts"] for e in ev] == sorted((e["ts"] for e in ev), reverse=True) and ev[0]["kind"] != "note"
      and "第 29 則" in ev[2]["text"], [e["kind"] for e in ev[:3]])
check("…and the 20 are the newest ones (the oldest note shown is #12)", "第 12 則" in ev[-1]["text"], ev[-1])
check("a leading # is fine", json.loads(app.task_detail(f"#{tid}"))["success"])
n = len(json.loads(app.task_detail(tid))["task"]["events"])
check("asking changes nothing (still the same events)", len(ss.get(tid, limit=100)["events"]) == 33 and n == 20)

print("refusals")
r = json.loads(app.task_detail(999))
check("no such task: a code and a sentence", not r["success"] and r["code"] == "not_found" and "999" in r["message"], r)
r = json.loads(app.task_detail("abc"))
check("a word instead of a number", not r["success"] and r["code"] == "invalid" and "任務編號" in r["message"], r)
r = json.loads(app.task_detail(None))
check("nothing at all", not r["success"] and r["code"] == "invalid", r)
real = ss.get


def broken(*a, **k):
    raise ss.Unavailable("open_failed", "The task database could not be opened (x).", "任務資料庫打不開（x）。")


ss.get = broken
r = json.loads(app.task_detail(tid))
check("a store that cannot be opened: its own words and code",
      not r["success"] and r["code"] == "open_failed" and r["message"] == "任務資料庫打不開（x）。", r)
ss.get = real

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
