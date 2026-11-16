#!/usr/bin/env python3
"""The task store (state_store.py): one SQLite file for tasks and what happened to them.

Asked for: schedules, tasks a bot keeps on with across sessions, and hand-offs between bots,
kept as structured state instead of JSON files and conversations.
  - a task's columns are its state; every real change is one event (history for free)
  - "stuck" is computed from the last event, never a status somebody must reset
  - nothing secret is kept: titles, notes and names are masked like every other outgoing text
  - the store degrades: a file it cannot read, or one a newer version wrote, makes it
    unavailable with a plain sentence, and nothing else breaks
  - an older file is copied (backup API) before it is migrated; two writers at once lose nothing
  - once a day: a backup (7 kept), and tasks closed over a year ago go
Demo data only. Run: .venv/bin/python tests_state_store.py
"""
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import state_store as ss  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def raises(fn, exc):
    try:
        fn()
    except exc as e:
        return e
    return None


TMP = Path(tempfile.mkdtemp(prefix="sf-state-"))
DB = TMP / "state.db"
T0 = 1_800_000_000.0

print("opening")
check("importing the module opens nothing", not DB.exists() and not ss.db_path().exists())
t = ss.add("整理這週的筆記", actor="示範助理", owner="示範助理", requester=ss.USER, origin="sfctl", now=T0, path=DB)
check("the first write makes the file, at the current schema, in WAL",
      DB.exists() and sqlite3.connect(DB).execute("PRAGMA user_version").fetchone()[0] == ss.SCHEMA
      and sqlite3.connect(DB).execute("PRAGMA journal_mode").fetchone()[0] == "wal")
check("a new task: its id, open (todo), created by whom, one 'created' event",
      t["id"] == 1 and t["status"] == "todo" and t["owner"] == "示範助理" and t["requester"] == ss.USER
      and [e["kind"] for e in t["events"]] == ["created"] and t["events"][0]["actor"] == "示範助理", t)

print("changing a task")
t = ss.update(1, actor="示範助理", status="doing", next_step="先列出標題", now=T0 + 60, path=DB)
check("one event per column that changed, saying before → after",
      [(e["kind"], e["text"]) for e in t["events"][1:]] == [("status", "todo → doing"), ("next_step", "— → 先列出標題")], t["events"])
t2 = ss.update(1, actor="示範助理", status="doing", now=T0 + 70, path=DB)
check("…and none when nothing changed", len(t2["events"]) == 3 and t2["updated_at"] == T0 + 60)
t = ss.update(1, actor="示範助理", status="done", now=T0 + 120, path=DB)
check("closing sets closed_at", t["closed_at"] == T0 + 120)
t = ss.update(1, actor="示範助理", status="doing", now=T0 + 180, path=DB)
check("reopening clears it", t["closed_at"] is None)
check("an unknown column is refused", raises(lambda: ss.update(1, color="red", path=DB), ValueError))
check("so is a status that does not exist", raises(lambda: ss.update(1, status="stuck", path=DB), ValueError))
check("a task cannot be its own parent", raises(lambda: ss.update(1, parent_id=1, path=DB), ValueError))
check("no such task: NotFound", raises(lambda: ss.update(99, status="done", path=DB), ss.NotFound)
      and raises(lambda: ss.get(99, path=DB), ss.NotFound) and raises(lambda: ss.note(99, "x", path=DB), ss.NotFound))
check("a refused change leaves the task as it was", ss.get(1, path=DB)["status"] == "doing")

print("parents, dependencies, notes")
c = ss.add("寄出整理好的筆記", actor="示範助理", owner="示範秘書", parent_id=1, depends_on=[1], now=T0 + 200, path=DB)
p = ss.get(1, path=DB)
check("a child knows its parent and what it waits for; the parent lists it",
      c["parent_id"] == 1 and c["depends_on"] == [1] and p["children"] == [c["id"]])
check("a parent or dependency that does not exist is refused, and nothing is written",
      raises(lambda: ss.add("x", parent_id=42, path=DB), ss.NotFound)
      and raises(lambda: ss.add("x", depends_on=[42], path=DB), ss.NotFound)
      and len(ss.listing(status="all", path=DB)) == 2)
t = ss.note(1, "標題列好了，等確認", actor="示範助理", now=T0 + 300, path=DB)
check("a note is an event and counts as activity", t["events"][-1]["kind"] == "note" and t["updated_at"] == T0 + 300)
check("an empty note is refused", raises(lambda: ss.note(1, "  ", path=DB), ValueError))

print("nothing secret is kept")
s = ss.add("deploy with sk-ant-demo0123456789abcdef", actor="示範助理", now=T0 + 400, path=DB)
ss.note(s["id"], "push to https://demo:hunter2@example.com/repo.git\nline two", actor="示範助理", now=T0 + 401, path=DB)
s = ss.get(s["id"], path=DB)
raw = DB.read_bytes() + (TMP / "state.db-wal").read_bytes() if (TMP / "state.db-wal").exists() else DB.read_bytes()
check("a key in a title or note is masked before it is written",
      "sk-ant-demo" not in s["title"] and "hunter2" not in s["events"][-1]["text"] and b"hunter2" not in raw
      and b"sk-ant-demo0123" not in raw, (s["title"], s["events"][-1]["text"]))
check("…and a note is one line", "\n" not in s["events"][-1]["text"])
check("a title is cut to its limit", len(ss.add("長" * 500, now=T0 + 402, path=DB)["title"]) == ss.TITLE_MAX)

print("listing and stuck")
ss.add("要不要換新的排程方式", actor="示範秘書", owner=ss.USER, kind="decision", due_at=T0 + 86400, now=T0 + 500, path=DB)
ss.add("回覆示範客戶", actor="示範秘書", owner="示範秘書", due_at=T0 + 3600, now=T0 + 501, path=DB)
mine = ss.listing(owner="示範秘書", path=DB)
check("one owner's open tasks, the soonest due first", [x["title"] for x in mine][:1] == ["回覆示範客戶"], [x["title"] for x in mine])
check("the decisions waiting for the person", [x["title"] for x in ss.listing(owner=ss.USER, kind="decision", path=DB)] == ["要不要換新的排程方式"])
ss.update(1, actor="示範助理", status="waiting", waiting_on="示範秘書", now=T0 + 600, path=DB)
check("waiting less than its limit: not stuck", 1 not in [x["id"] for x in ss.stuck(T0 + 600 + 3600, path=DB)])
st = ss.stuck(T0 + 600 + ss.STUCK_S["waiting"] + 1, path=DB)
check("waiting past it: stuck, with how long nothing happened",
      1 in [x["id"] for x in st] and next(x for x in st if x["id"] == 1)["idle_s"] == ss.STUCK_S["waiting"] + 1)
ss.note(1, "催了一下", actor="示範助理", now=T0 + 600 + ss.STUCK_S["waiting"] + 2, path=DB)
check("a note resets it", 1 not in [x["id"] for x in ss.stuck(T0 + 600 + ss.STUCK_S["waiting"] + 3, path=DB)])
check("a closed task is never stuck", c["id"] not in [x["id"] for x in ss.stuck(T0 + 10 ** 8, path=DB)]
      or ss.get(c["id"], path=DB)["status"] in ss.OPEN)

print("two writers at once")
errors = []


def writer(who):
    try:
        for i in range(40):
            ss.add(f"{who} 第 {i} 件", actor=who, path=DB)
    except Exception as e:                     # noqa: BLE001
        errors.append(repr(e))


before = len(ss.listing(status="all", limit=10_000, path=DB))
ths = [threading.Thread(target=writer, args=(n,)) for n in ("甲", "乙", "丙")]
[x.start() for x in ths]
[x.join() for x in ths]
check("three threads, 120 tasks, none lost, no error",
      not errors and len(ss.listing(status="all", limit=10_000, path=DB)) == before + 120, errors[:2])

print("it degrades")
bad = TMP / "broken.db"
bad.write_bytes(b"this is not a database at all" * 100)
e = raises(lambda: ss.listing(path=bad), ss.Unavailable)
check("a file it cannot read: Unavailable, with words in both languages",
      e is not None and e.en and e.zh and not ss.available(bad), e)
newer = TMP / "newer.db"
x = sqlite3.connect(newer)
x.execute(f"PRAGMA user_version={ss.SCHEMA + 1}")
x.close()
e = raises(lambda: ss.listing(path=newer), ss.Unavailable)
check("a file a newer ShellBot wrote: Unavailable (newer_schema), left as it was",
      e is not None and e.code == "newer_schema"
      and sqlite3.connect(newer).execute("PRAGMA user_version").fetchone()[0] == ss.SCHEMA + 1)

print("migrating an older file")
old = TMP / "old.db"
ss.add("舊版的一件事", now=T0, path=old)
ss._MIGRATIONS[2] = "ALTER TABLE tasks ADD COLUMN machine TEXT NOT NULL DEFAULT ''"
ss.SCHEMA = 2
ss._ready.discard(str(old))
try:
    t = ss.get(1, path=old)
    bak = TMP / "old.db.bak-v1"
    check("an older file is copied first, then brought up; what it held is kept",
          bak.exists() and sqlite3.connect(bak).execute("PRAGMA user_version").fetchone()[0] == 1
          and t["title"] == "舊版的一件事" and "machine" in t
          and sqlite3.connect(old).execute("PRAGMA user_version").fetchone()[0] == 2)
finally:
    ss.SCHEMA = 1
    del ss._MIGRATIONS[2]

print("upkeep")
up = TMP / "upkeep.db"
gone = ss.add("一年多前做完的", now=T0, path=up)
ss.update(gone["id"], status="done", now=T0 + 10, path=up)
kept = ss.add("還在做的", now=T0, path=up)
day = T0 + ss.KEEP_CLOSED_S + 100
r = ss.maintain(day, path=up)
check("once a day: a backup, and tasks closed over a year ago go with their events",
      r["ran"] and r["removed"] == 1 and raises(lambda: ss.get(gone["id"], path=up), ss.NotFound)
      and ss.get(kept["id"], path=up)["title"] == "還在做的"
      and sqlite3.connect(up).execute("SELECT COUNT(*) FROM events WHERE task_id=?", (gone["id"],)).fetchone()[0] == 0)
check("…and not twice the same day", ss.maintain(day + 3600, path=up) == {"ran": False})
for i in range(1, 10):
    ss.maintain(day + i * 86400 + 1, path=up)
check(f"{ss.BACKUP_KEEP} backups kept", len(list(TMP.glob("upkeep.db.bak-2*"))) == ss.BACKUP_KEEP,
      sorted(p.name for p in TMP.glob("upkeep.db.bak-*")))

print("due dates")
now = 1_800_000_000.0
check("+3h, +2d, +1w", ss.parse_due("+3h", now) == now + 3 * 3600 and ss.parse_due("+2d", now) == now + 2 * 86400
      and ss.parse_due("+1w", now) == now + 7 * 86400)
from datetime import datetime  # noqa: E402
check("a date alone is the end of that day; a time is that time",
      datetime.fromtimestamp(ss.parse_due("2026-11-20")).strftime("%Y-%m-%d %H:%M") == "2026-11-20 23:59"
      and datetime.fromtimestamp(ss.parse_due("2026-11-20 09:30")).strftime("%H:%M") == "09:30")
check("empty or 'none' clears it; nonsense is refused",
      ss.parse_due("") is None and ss.parse_due("none") is None and raises(lambda: ss.parse_due("next tuesday"), ValueError))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
