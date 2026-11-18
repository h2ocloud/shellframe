#!/usr/bin/env python3
"""`task_user_act` (api_tasks.py): the person acts on a task from the office's board.

Asked for: from a task's card the person can say 「我做好了」 or leave a note, as themselves.
  - done on the person's own task (a decision, or work they own) closes it, the result written down
  - done on a task that waits on the person is their part done: a result note, the owner carries on
    (waiting cleared, status doing), the task stays open
  - done on a task that is neither theirs nor waiting on them is refused in words
  - a note needs a text; an unknown task or action answers in words
  - the answer says who should hear about it (`tell`): the requester for a closed decision, the owner
    when the person's part is done or for a note on someone else's task, "" when that is the person
  - every event the person causes carries the actor "user"
Demo data only. Run: .venv/bin/python tests_api_task_user_act.py
"""
import json
import sys
import tempfile
import types
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
TMP = tempfile.mkdtemp(prefix="sf-user-act-")
ss.db_path = lambda: Path(TMP) / "state.db"


class App(api_tasks.TasksApiMixin):
    pass


app = App()


def act(tid, action, text=""):
    return json.loads(app.task_user_act(tid, action, text))


def actors(tid):
    return {e["actor"] for e in ss.get(tid)["events"] if e["kind"] != "created"}


dec = ss.add("示範：選方案 A 或 B", actor="示範秘書", owner=ss.USER, requester="示範秘書", kind="decision")["id"]
own = ss.add("示範：自己寫一段說明", actor="示範秘書", owner=ss.USER, requester=ss.USER)["id"]
wait = ss.add("示範：整理週報", actor=ss.USER, owner="示範助理", requester=ss.USER)["id"]
ss.update(wait, actor="示範助理", waiting_on=ss.USER, status="waiting")
other = ss.add("示範：別人的事", actor="示範秘書", owner="示範助理", requester="示範秘書")["id"]

print("done on the person's own task")
r = act(dec, "done", "選 A")
t = ss.get(dec)
check("a decision is closed, the result noted, and the requester is the one to tell",
      r["success"] and t["status"] == "done" and r["task"]["status"] == "done" and r["tell"] == "示範秘書"
      and any(e["kind"] == "result" and e["text"] == "選 A" for e in t["events"]), (r, t))
check("…every event after the creation is the person's", actors(dec) == {ss.USER}, actors(dec))
r = act(own, "done")
check("work the person owns: closed without a text, and nobody to tell (they asked for it themselves)",
      r["success"] and ss.get(own)["status"] == "done" and r["tell"] == "", r)
check("a closed task is not closed twice", act(own, "done")["code"] == "closed")

print("done on a task waiting on the person")
r = act(wait, "done", "資料我補好了")
t = ss.get(wait)
check("their part is done: a result note, waiting cleared, back to doing, still open, the owner is told",
      r["success"] and t["status"] == "doing" and t["waiting_on"] == "" and t["owner"] == "示範助理" and r["tell"] == "示範助理"
      and any(e["kind"] == "result" and e["text"] == "資料我補好了" and e["actor"] == ss.USER for e in t["events"]), (r, t))
check("…the status change is theirs too", actors(wait) == {ss.USER, "示範助理"} or ss.USER in actors(wait), actors(wait))
w2 = ss.add("示範：再一件", actor=ss.USER, owner="示範助理", requester=ss.USER)["id"]
ss.update(w2, actor="示範助理", waiting_on=ss.USER, status="waiting")
r = act(w2, "done")
check("no text: the result note says it in words", r["success"] and any(e["kind"] == "result" and e["text"] == "做好了" for e in ss.get(w2)["events"]), r)

print("done on someone else's task")
r = act(other, "done", "我來收尾")
check("refused: 這件不是在等你; nothing changed", not r["success"] and r["message"] == "這件不是在等你" and ss.get(other)["status"] == "todo"
      and len(ss.get(other)["events"]) == 1, (r, ss.get(other)))

print("notes")
r = act(other, "note", "記得先跟客戶確認")
check("a note on someone else's task: written as the person; the owner hears", r["success"] and r["tell"] == "示範助理"
      and ss.get(other)["events"][-1]["actor"] == ss.USER and ss.get(other)["events"][-1]["text"] == "記得先跟客戶確認", r)
r = act(dec, "note", "補充一點")
check("a note on a task the person owns: the requester hears", r["success"] and r["tell"] == "示範秘書", r)
r = act(own, "note", "給自己的備忘")
check("…on the person's own task with nobody else involved: nobody to tell", r["success"] and r["tell"] == "", r)
n0 = len(ss.get(other)["events"])
r = act(other, "note", "   ")
check("an empty note is refused, nothing written", not r["success"] and r["code"] == "empty" and len(ss.get(other)["events"]) == n0, r)

print("what it refuses in words")
r = act(9999, "note", "x")
check("an unknown task", not r["success"] and r["code"] == "not_found" and "9999" in r["message"], r)
r = act(other, "drop", "x")
check("an unknown action", not r["success"] and r["code"] == "invalid" and "drop" in r["message"], r)
r = act("abc", "done")
check("a task number that is not one", not r["success"] and r["code"] == "invalid", r)
real = ss.get


def broken(*a, **k):
    raise ss.Unavailable("open_failed", "The task database could not be opened (x).", "任務資料庫打不開（x）。")


ss.get = broken
r = act(other, "done")
ss.get = real
check("an unusable store answers in plain words", not r["success"] and r["code"] == "open_failed" and "打不開" in r["message"], r)
check("accepts '#12' and text numbers", act(f"#{other}", "note", "用井字號")["success"])

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
