#!/usr/bin/env python3
"""The Loops panel's schedule rows (sched_view.py), over fake plists and a fixed clock.

What the panel used to get wrong, pinned here so it cannot come back:

  - a month-and-day plist (a one-off) was shown as "every day 09:05, on"
  - twenty weekday x time entries were shown as one run-on line starting "every day"
  - a job that is not loaded in launchd looked the same as one that is
  - a KeepAlive job that keeps dying was not listed at all

Plists are plain dicts here, no file on disk and no launchctl; the module under test
does not import subprocess. Names are demo names. The clock is passed in, so a test
that says "ran 11/10" does not change meaning on 11/10 of the next year.

Run: .venv/bin/python tests_sched_freq.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# A test never reads or writes the app's real config folder (sf_config.config_dir): say so
# even when this file is started some way run_tests.sh does not.
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import sched_view as sv  # noqa: E402

passed = failed = 0
NOW = datetime(2026, 11, 12, 14, 20)          # a Thursday afternoon
UP = (None, 0)                                 # loaded, not running, last exit 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def one(label, plist, loaded=UP, now=NOW, cfg=None):
    return sv.describe(label, plist, loaded=loaded, now=now, cfg=cfg)


def rows(items, loaded=None, cfg=None, now=NOW):
    loaded = {lbl: UP for lbl, _ in items} if loaded is None else loaded
    return sv.build_rows(items, loaded, cfg or {}, now)


def cal(*entries, script="/opt/demo/job.sh"):
    return {"ProgramArguments": ["/bin/bash", script],
            "StartCalendarInterval": list(entries) if len(entries) != 1 else entries[0]}


def at(h, m, **kw):
    return dict(Hour=h, Minute=m, **kw)


def punch(label, h, m, arg, month=11, day=10, script="/opt/demo/punch.sh"):
    return (label, {"ProgramArguments": ["/bin/zsh", script, arg],
                    "StartCalendarInterval": {"Month": month, "Day": day, "Hour": h, "Minute": m}})


print("interval")
r = one("demo.poll", {"ProgramArguments": ["/bin/bash", "/opt/demo/poll.sh"], "StartInterval": 600})
check("600 s is every 10 minutes", r["when"] == {"en": "Every 10 min", "zh": "每 10 分鐘"} and r["kind"] == "interval", r["when"])
check("…and says launchd does not overlap runs (the real gap can be longer)", "still going" in r["tip"]["en"] and r["tip"]["zh"])
check("7200 s is every 2 hours", one("a.b", {"StartInterval": 7200})["when"]["en"] == "Every 2 h")
check("5400 s is 90 minutes, not '1 h'", one("a.b", {"StartInterval": 5400})["when"]["zh"] == "每 90 分鐘")
check("45 s stays seconds", one("a.b", {"StartInterval": 45})["when"]["en"] == "Every 45 s")

print("daily")
r = one("demo.backup", cal(at(2, 0), at(21, 0)))
check("two times a day", r["when"] == {"en": "Daily 02:00 / 21:00", "zh": "每天 02:00／21:00"} and r["kind"] == "daily", r["when"])
check("an hour and minute only is daily, with no toggle taken away", one("a.b", cal(at(9, 15)))["when"]["en"] == "Daily 09:15"
      and r["toggle"] is True and r["enabled"] and r["active"])
every_day = [dict(Weekday=d, Hour=7, Minute=0) for d in range(7)]
check("seven weekday entries at one time are daily", one("a.b", cal(*every_day))["when"]["en"] == "Daily 07:00"
      and one("a.b", cal(*every_day))["kind"] == "daily")

print("weekly")
workweek = [dict(Weekday=d, Hour=h, Minute=m) for d in range(1, 6) for h, m in ((9, 50), (12, 30), (15, 30), (17, 50))]
r = one("demo.weekdays", cal(*workweek))
check("20 entries merge into one phrase", r["when"] == {"en": "Mon–Fri 09:50 / 12:30 / 15:30 / 17:50",
                                                        "zh": "週一～五 09:50／12:30／15:30／17:50"}, r["when"])
check("…and it never starts with 'every day'", r["kind"] == "weekly" and "Daily" not in r["when"]["en"] and "每天" not in r["when"]["zh"])
check("non-adjacent days are listed", one("a.b", cal(*[dict(Weekday=d, Hour=9, Minute=0) for d in (1, 3, 5)]))["when"]
      == {"en": "Mon, Wed, Fri 09:00", "zh": "週一、週三、週五 09:00"})
check("Sunday as 0 or as 7 is Sunday", one("a.b", cal(dict(Weekday=0, Hour=8, Minute=0)))["when"]["en"] == "Sun 08:00"
      and one("a.b", cal(dict(Weekday=7, Hour=8, Minute=0)))["when"]["en"] == "Sun 08:00")
check("Saturday and Sunday are a range", one("a.b", cal(dict(Weekday=6, Hour=10, Minute=0), dict(Weekday=0, Hour=10, Minute=0)))["when"]["zh"]
      == "週六～日 10:00")
mixed = [dict(Weekday=d, Hour=9, Minute=0) for d in range(1, 6)] + [dict(Weekday=6, Hour=10, Minute=30)]
check("different days at different times stay apart", one("a.b", cal(*mixed))["when"]["en"] == "Mon–Fri 09:00 · Sat 10:30"
      and one("a.b", cal(*mixed))["kind"] == "weekly")

print("monthly and odd shapes")
check("a day of the month", one("a.b", cal(dict(Day=1, Hour=9, Minute=0)))["when"] == {"en": "Monthly on day 1 09:00", "zh": "每月 1 日 09:00"})
check("two days of the month", one("a.b", cal(dict(Day=1, Hour=9, Minute=0), dict(Day=15, Hour=9, Minute=0)))["when"]["zh"] == "每月 1、15 日 09:00")
r = one("a.b", cal(dict(Hour=9)))
check("a wildcard minute is not shown as a clock time", r["kind"] == "calendar" and "09:" not in r["when"]["en"] and "Hour=9" in r["when"]["en"], r["when"])
r = one("a.b", {"StartInterval": 600, "StartCalendarInterval": at(9, 0)})
check("an interval and a calendar together are both said", r["kind"] == "calendar" and r["when"]["en"] == "Every 10 min · Daily 09:00", r["when"])
check("a plist with no timer and no KeepAlive is not a schedule", one("a.b", {"RunAtLoad": True, "ProgramArguments": ["/bin/true"]}) is None)

print("one-off")
once_past = dict(ProgramArguments=["/bin/zsh", "/opt/demo/punch.sh", "in"],
                 StartCalendarInterval={"Month": 11, "Day": 10, "Hour": 9, "Minute": 5})
r = one("demo.punch-in", once_past)
check("a month-and-day plist is a one-off, never 'daily'", r["kind"] == "once" and "Daily" not in r["when"]["en"]
      and "每天" not in r["when"]["zh"] and "every day" not in r["when"]["en"].lower(), r["when"])
check("11/10 has passed and it is loaded: 'ran 11/10'", r["state"] == "ran" and r["when"] == {
    "en": "One-off · ran 11/10", "zh": "單次・11/10 已執行"}, r["when"])
check("…and it warns that it fires again the same day next year", [n["code"] for n in r["notes"]] == ["yearly_repeat"]
      and r["notes"][0]["text"] == {"en": "Fires again on 11/10 next year", "zh": "隔年 11/10 會再觸發"}
      and r["notes"][0]["level"] == "warn", r["notes"])
check("a one-off has no on/off switch and does not count as on", r["toggle"] is False and r["active"] is False)
check("'ran' says what it can and cannot know", "log" in r["tip"]["en"] and "紀錄" in r["tip"]["zh"])
r = one("demo.punch-in", once_past, loaded=None)
check("not loaded: 'not armed (waiting for you)'", r["state"] == "unarmed" and r["when"] == {
    "en": "One-off · not armed (waiting for you)", "zh": "單次・未排（等你說要排）"}, r["when"])
check("…no yearly warning, because it will not fire at all, and the date it was set for is said",
      r["notes"] == [] and "11/10 09:05" in r["detail"]["en"] and "not" in r["detail"]["en"] and "不會觸發" in r["detail"]["zh"])
future = dict(once_past, StartCalendarInterval={"Month": 11, "Day": 13, "Hour": 9, "Minute": 5})
r = one("demo.punch-in", future)
check("a date still ahead and loaded is armed", r["state"] == "armed" and r["when"] == {
    "en": "One-off · armed 11/13 09:05", "zh": "單次・已排 11/13 09:05"} and r["active"] is True, r["when"])
check("…it also repeats next year unless it is unloaded", r["notes"][0]["text"]["en"] == "Fires again on 11/13 next year")
later_today = dict(once_past, StartCalendarInterval={"Month": 11, "Day": 12, "Hour": 18, "Minute": 25})
check("later today is said as today", one("a.b", later_today)["when"] == {"en": "One-off · armed today 18:25", "zh": "單次・已排 今天 18:25"})
earlier_today = dict(once_past, StartCalendarInterval={"Month": 11, "Day": 12, "Hour": 9, "Minute": 5})
check("earlier today it has already run", one("a.b", earlier_today)["when"]["en"] == "One-off · ran 11/12")
two_times = dict(once_past, StartCalendarInterval=[{"Month": 11, "Day": 12, "Hour": 9, "Minute": 0},
                                                    {"Month": 11, "Day": 12, "Hour": 17, "Minute": 0}])
r = one("a.b", two_times)
check("one plist with two times is armed until the last has passed", r["state"] == "armed"
      and r["when"]["en"] == "One-off · armed today 17:00" and r["times"]["en"] == "09:00 / 17:00", r["when"])
check("the same plist a year on says the same thing about its date: it only depends on the clock passed in",
      one("a.b", once_past, now=datetime(2027, 11, 9, 8, 0))["state"] == "armed"
      and one("a.b", once_past, now=datetime(2027, 11, 10, 8, 0))["state"] == "armed"
      and one("a.b", once_past, now=datetime(2027, 11, 10, 9, 5))["state"] == "ran")
r = one("a.b", dict(once_past, StartCalendarInterval={"Month": 2, "Day": 29, "Hour": 9, "Minute": 0}))
check("Feb 29 in a common year does not crash and is not called 'ran'", r["state"] == "armed" and "2/29" in r["when"]["en"], r["when"])

print("groups of one-offs")
trio = [punch("demo.punch-in", 9, 5, "in"), punch("demo.punch-out", 18, 25, "out"), punch("demo.punch-out-early", 18, 10, "out")]
rs = rows(trio + [("demo.zz", cal(at(1, 0)))])
check("three one-offs on one script and one day are one row (plus the unrelated one)", [r["id"] for r in rs] == [
    "group:/opt/demo/punch.sh@11/10", "demo.zz"], [r["id"] for r in rs])
g = rs[0]
check("the group is named for the shared script by default", g["title"] == "punch" and g["group"] and g["kind"] == "once")
check("members are listed in the order they fire, not in label order", [m["id"] for m in g["members"]] == [
    "demo.punch-in", "demo.punch-out-early", "demo.punch-out"] and [m["times"]["en"] for m in g["members"]] == ["09:05", "18:10", "18:25"],
      [m["id"] for m in g["members"]])
check("all loaded and past: the group ran 11/10, with one yearly warning", g["state"] == "ran" and g["when"] == {
    "en": "One-off · ran 11/10", "zh": "單次・11/10 已執行"} and [n["code"] for n in g["notes"]] == ["yearly_repeat"], g["when"])
check("a group has no switch and is not counted as on", g["toggle"] is False and g["active"] is False and g["enabled"] is True)
loaded = {"demo.punch-in": UP, "demo.punch-out": UP}
g = rows(trio, loaded=loaded)[0]
check("one member not loaded: the group says it is only partly armed, and which one will not fire",
      g["state"] == "partial" and g["when"]["en"] == "One-off · only 2 of 3 armed" and g["when"]["zh"] == "單次・3 個只排了 2 個"
      and any(n["code"] == "partial_group" and "punch-out-early" in n["text"]["en"] for n in g["notes"]), g["notes"])
g = rows(trio, loaded={})[0]
check("none loaded: not armed, waiting for you, and no yearly warning", g["state"] == "unarmed" and g["when"]["zh"] == "單次・未排（等你說要排）"
      and g["notes"] == [] and g["enabled"] is False)
fut = [punch("demo.punch-in", 9, 5, "in", day=13), punch("demo.punch-out-early", 18, 10, "out", day=13), punch("demo.punch-out", 18, 25, "out", day=13)]
g = rows(fut)[0]
check("all loaded and 11/13 still ahead: armed, date only on the header", g["state"] == "armed" and g["when"] == {
    "en": "One-off · armed 11/13", "zh": "單次・已排 11/13"} and g["active"] is True, g["when"])
today = [punch("demo.punch-in", 9, 5, "in", day=12), punch("demo.punch-out-early", 18, 10, "out", day=12), punch("demo.punch-out", 18, 25, "out", day=12)]
g = rows(today)[0]
check("mid-day on the day: morning ran, evening still armed, so the group is armed today",
      g["state"] == "armed" and g["when"]["en"] == "One-off · armed today" and [m["state"] for m in g["members"]] == ["ran", "armed", "armed"], g["when"])
split = [punch("demo.punch-in", 9, 5, "in", day=10), punch("demo.punch-out", 18, 25, "out", day=11)]
check("different dates are not grouped: the odd one out stays visible", [r["id"] for r in rows(split)] == ["demo.punch-in", "demo.punch-out"])
other = [punch("demo.a", 9, 5, "in", script="/opt/demo/one.sh"), punch("demo.b", 18, 5, "out", script="/opt/demo/two.sh")]
check("different scripts are not grouped", [r["id"] for r in rows(other)] == ["demo.a", "demo.b"])
daily_same = [("demo.a", cal(at(9, 5), script="/opt/demo/x.sh")), ("demo.b", cal(at(18, 5), script="/opt/demo/x.sh"))]
check("only one-offs are grouped", [r["id"] for r in rows(daily_same)] == ["demo.a", "demo.b"])
cfg = {"groups": {"clock": ["demo.a", "demo.b"]}, "titles": {"clock": "Clock"}}
g = rows(other, cfg=cfg)
check("a group named in the config groups by name, whatever the scripts, and takes its title", len(g) == 1
      and g[0]["id"] == "group:clock" and g[0]["title"] == "Clock" and [m["id"] for m in g[0]["members"]] == ["demo.a", "demo.b"], g)
check("a group the user named is titled with that name when no title is given, not with the script's",
      rows(other, cfg={"groups": {"clock": ["demo.a", "demo.b"]}})[0]["title"] == "clock")
check("a title keyed by the script's name retitles an automatic group",
      rows(trio, cfg={"titles": {"punch": "Punch cards"}})[0]["title"] == "Punch cards")
check("a title keyed by label retitles a single row", rows([("demo.backup", cal(at(2, 0)))], cfg={"titles": {"demo.backup": "Nightly backup"}})[0]["title"] == "Nightly backup"
      and rows([("demo.backup", cal(at(2, 0)))])[0]["title"] == "backup")
mid = rows([("demo.a", cal(at(1, 0))), *trio, ("demo.z", cal(at(2, 0)))])
check("the group sits where its first member sat", [r["id"] for r in mid] == [
    "demo.a", "group:/opt/demo/punch.sh@11/10", "demo.z"], [r["id"] for r in mid])

print("resident (KeepAlive)")
bot = {"ProgramArguments": ["/usr/bin/node", "/opt/demo/bot.js"], "KeepAlive": True}
r = one("demo.bot", bot, loaded=(4242, 0))
check("a running daemon is listed, with no warning", r["kind"] == "daemon" and r["state"] == "running" and r["running"] and r["notes"] == []
      and r["when"]["zh"] == "由 launchd 保持常駐" and r["toggle"] is True)
r = one("demo.bot", bot, loaded=(None, 78))
check("not running with a non-zero last exit is flagged (the 'keeps restarting' case)", r["state"] == "failing"
      and r["notes"][0]["text"] == {"en": "Not running · last exit 78", "zh": "沒有在跑・上次 exit 78"} and r["notes"][0]["level"] == "warn", r["notes"])
check("loaded, stopped, exit 0: stopped, not an alarm", one("demo.bot", bot, loaded=(None, 0))["state"] == "stopped" and one("demo.bot", bot, loaded=(None, 0))["notes"] == [])
check("not loaded: off", one("demo.bot", bot, loaded=None)["state"] == "off" and one("demo.bot", bot, loaded=None)["enabled"] is False)
check("a KeepAlive dict (restart only when it failed) is a daemon too", one("demo.bot", dict(bot, KeepAlive={"SuccessfulExit": False}))["kind"] == "daemon")

print("rows are plain data")
big = rows(trio + [("demo.backup", cal(at(2, 0))), ("demo.bot", bot), ("demo.weekdays", cal(*workweek)),
                   ("demo.poll", {"StartInterval": 600}), ("demo.one", once_past)])
check("json.dumps works on every row", bool(json.dumps(big)))


def keys(d, out):
    for k, v in d.items():
        out.append(k)
        if isinstance(v, dict):
            keys(v, out)
        for x in (v if isinstance(v, list) else []):
            if isinstance(x, dict):
                keys(x, out)
    return out


check("no internal key leaks out", not [k for r in big for k in keys(r, []) if k.startswith("_")])
check("every row says what to show in both languages", all(set(r["when"]) == {"en", "zh"} and r["when"]["en"] and r["when"]["zh"] for r in big))
check("an unreadable plist does not blank the others", [r["id"] for r in rows([("demo.bad", {"StartCalendarInterval": "nope"}), ("demo.ok", cal(at(1, 0)))])] == ["demo.ok"])
check("the module never starts a process (no launchctl in the data layer)", not hasattr(sv, "subprocess") and "subprocess" not in open(sv.__file__, encoding="utf-8").read().replace("`launchctl list`", ""))

print("what the user configured")
check("a prefix needs a few characters of its own", [sv.valid_prefix(p) for p in ("demo.", "mine.tools.", "co.", ".", "", "ab", "a/b/cdef.", None, 5)]
      == [True, True, False, False, False, False, False, False, False])
check("a label is a file name, not a path", [sv.valid_label(x) for x in ("demo.backup", "demo.punch-in", "../x", "a/b", ".hidden", "", "a b", "a..b")]
      == [True, True, False, False, False, False, False, False])
c = sv.parse_config({"prefixes": ["demo.", "co.", 7], "titles": {"demo.a": " A ", "demo.b": 3, "demo.c": ""},
                     "groups": {"g": ["demo.a", "demo.b"], "solo": ["demo.a"], "bad": "x", "paths": ["../x", "demo.a"]}})
check("only well-formed parts are kept", c == {"prefixes": ["demo."], "titles": {"demo.a": "A"}, "groups": {"g": ["demo.a", "demo.b"]}}, c)
check("junk is an empty config", sv.parse_config(["x"]) == {"prefixes": [], "titles": {}, "groups": {}} and sv.parse_config(None)["prefixes"] == [])
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "schedules.json"
    check("no file: nothing configured, not an error", sv.read_config(p) == (sv.parse_config({}), ""))
    p.write_text('{"prefixes": ["demo."]}', encoding="utf-8")
    check("a file is read", sv.read_config(p) == (sv.parse_config({"prefixes": ["demo."]}), ""))
    p.write_text("{not json", encoding="utf-8")
    cfg, err = sv.read_config(p)
    check("broken JSON is reported, and nothing is listed", cfg["prefixes"] == [] and "not valid JSON" in err and "schedules.json" in err, err)
    p.write_text("[1, 2]", encoding="utf-8")
    check("JSON of another shape is an empty config", sv.read_config(p) == (sv.parse_config({}), ""))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
