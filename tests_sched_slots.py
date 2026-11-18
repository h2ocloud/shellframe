#!/usr/bin/env python3
"""What the office calendar needs from a schedule row: `slots` (when, as numbers) and `owner`.

  - slots: {"interval": seconds | None, "calendar": [{Month, Day, Weekday, Hour, Minute}]} with
    launchd's meaning (a missing key is a wildcard; Weekday 7 is folded to 0); a one-off carries its
    month and day; a KeepAlive job has none; a calendar too long to be useful is sent empty with
    "partial": true; a group of one-offs carries its members' entries
  - owners in schedules.json: validated and bounded, a row's `owner` is "" without one, a group takes
    its own, else its first member's
  - schedules_list carries both through (fixture LaunchAgents folder, launchctl stubbed; the real
    folder and the real launchctl are never touched), and the config file is only read

Plists are plain dicts; names are demo names; the clock is pinned.
Run: .venv/bin/python tests_sched_slots.py
"""
import atexit
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import api_schedules  # noqa: E402
import sched_view as sv  # noqa: E402
import sf_config  # noqa: E402

passed = failed = 0
NOW = datetime(2026, 11, 12, 14, 20)
UP = (None, 0)


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def one(label, plist, loaded=UP, cfg=None):
    return sv.describe(label, plist, loaded=loaded, now=NOW, cfg=cfg)


def cal(*entries):
    return {"ProgramArguments": ["/bin/bash", "/opt/demo/job.sh"],
            "StartCalendarInterval": list(entries) if len(entries) != 1 else entries[0]}


print("slots")
r = one("demo.daily", cal({"Hour": 9, "Minute": 5}))
check("a daily job: one entry with an hour and a minute, no interval",
      r["slots"] == {"interval": None, "calendar": [{"Hour": 9, "Minute": 5}]}, r["slots"])
r = one("demo.week", cal(*[{"Weekday": d, "Hour": 9, "Minute": 30} for d in (1, 2, 3, 4, 5)]))
check("weekdays stay one entry each", [c["Weekday"] for c in r["slots"]["calendar"]] == [1, 2, 3, 4, 5])
r = one("demo.sun", cal({"Weekday": 7, "Hour": 23, "Minute": 0}, {"Weekday": 0, "Hour": 22, "Minute": 0}))
check("Weekday 7 and 0 are both Sunday (0)", [c["Weekday"] for c in r["slots"]["calendar"]] == [0, 0], r["slots"])
r = one("demo.hourly", cal({"Minute": 15}))
check("a missing key is a wildcard: nothing is invented for it", r["slots"]["calendar"] == [{"Minute": 15}])
r = one("demo.everymin", {"ProgramArguments": ["/bin/true"], "StartCalendarInterval": [{}]})
check("an entry with nothing in it is every minute, still one entry", r["slots"]["calendar"] == [{}])
r = one("demo.poll", {"ProgramArguments": ["/bin/true"], "StartInterval": 600})
check("StartInterval is seconds, and there is no calendar", r["slots"] == {"interval": 600, "calendar": []}, r["slots"])
r = one("demo.both", {"ProgramArguments": ["/bin/true"], "StartInterval": 3600,
                      "StartCalendarInterval": {"Hour": 3, "Minute": 0}})
check("an interval and a calendar together keep both", r["slots"] == {"interval": 3600, "calendar": [{"Hour": 3, "Minute": 0}]})
r = one("demo.zero", {"ProgramArguments": ["/bin/true"], "StartInterval": 0, "StartCalendarInterval": {"Hour": 3, "Minute": 0}})
check("an interval of 0 (or junk) is none", r["slots"]["interval"] is None)
r = one("demo.once", cal({"Month": 11, "Day": 20, "Hour": 9, "Minute": 5}))
check("a one-off keeps its month and day", r["kind"] == "once" and r["slots"]["calendar"] == [{"Month": 11, "Day": 20, "Hour": 9, "Minute": 5}], r["slots"])
r = one("demo.bot", {"ProgramArguments": ["/bin/true"], "KeepAlive": True})
check("a KeepAlive job has no times at all", r["kind"] == "daemon" and r["slots"] == {"interval": None, "calendar": []})
r = one("demo.big", cal(*[{"Hour": h % 24, "Minute": h % 60, "Weekday": h % 7} for h in range(sv.SLOTS_MAX + 1)]))
check("a calendar too long to be useful is sent empty and says so",
      r["slots"]["calendar"] == [] and r["slots"]["partial"] is True and r["slots"]["interval"] is None)
r = one("demo.max", cal(*[{"Hour": h % 24, "Minute": h % 60, "Weekday": h % 7} for h in range(sv.SLOTS_MAX)]))
check("…exactly the limit is still sent whole", len(r["slots"]["calendar"]) == sv.SLOTS_MAX and "partial" not in r["slots"])
check("a row survives json.dumps with slots and owner", json.loads(json.dumps(r))["slots"]["calendar"][0].keys() >= {"Hour"})

print("a group of one-offs")
items = [("demo.p-in", cal({"Month": 11, "Day": 13, "Hour": 9, "Minute": 5})), ("demo.p-out", cal({"Month": 11, "Day": 13, "Hour": 18, "Minute": 25}))]
for _label, pl in items:
    pl["ProgramArguments"] = ["/bin/zsh", "/opt/demo/punch.sh"]
rows = sv.build_rows(items, {lbl: UP for lbl, _ in items}, {"owners": {"demo.p-out": "clerk"}}, NOW)
check("two one-offs on one script and day are one row", len(rows) == 1 and rows[0]["group"] is True, [r["id"] for r in rows])
g = rows[0]
check("the group carries every member's entry", [c["Hour"] for c in g["slots"]["calendar"]] == [9, 18] and g["slots"]["interval"] is None, g["slots"])
check("…and, with no owner of its own, the first member that has one", g["owner"] == "clerk", g["owner"])
rows = sv.build_rows(items, {lbl: UP for lbl, _ in items}, {}, NOW)
gid = rows[0]["id"]
rows = sv.build_rows(items, {lbl: UP for lbl, _ in items}, {"owners": {gid: "boss", "demo.p-out": "clerk"}}, NOW)
check("a group's own owner (its row id) wins over a member's", rows[0]["owner"] == "boss", (gid, rows[0]["owner"]))
rows = sv.build_rows(items, {lbl: UP for lbl, _ in items},
                     {"groups": {"punch": ["demo.p-in", "demo.p-out"]}, "owners": {"punch": "boss"}}, NOW)
check("…a named group takes the owner written under its group id", rows[0]["owner"] == "boss" and rows[0]["id"] == "group:punch", rows[0]["id"])

print("owners in schedules.json")
c = sv.parse_config({"prefixes": ["demo."], "owners": {"demo.a": " ops ", "demo.b": "", "demo.c": 7, "": "x", "demo.d": "x" * 200, 5: "y", "g": "boss"}})
check("only a non-empty string owner for a non-empty key is kept, trimmed and cut",
      c["owners"] == {"demo.a": "ops", "demo.d": "x" * sv.OWNER_LEN, "g": "boss"}, c["owners"])
check("junk is no owners", sv.parse_config({"owners": ["a"]})["owners"] == {} and sv.parse_config({"owners": "a"})["owners"] == {}
      and sv.parse_config(None)["owners"] == {})
big = sv.parse_config({"owners": {f"demo.{i}": "ops" for i in range(sv.OWNERS_MAX + 50)}})
check("the number of owners read is bounded", len(big["owners"]) == sv.OWNERS_MAX)
check("the key is only a name, not an access rule: a label with a path is kept as text but never grants anything",
      "prefixes" in c and c["prefixes"] == ["demo."])
r = one("demo.a", cal({"Hour": 9, "Minute": 5}), cfg=c)
check("a row gets the owner written for its label", r["owner"] == "ops")
check("…and '' without one", one("demo.zzz", cal({"Hour": 9, "Minute": 5}), cfg=c)["owner"] == "" and one("demo.q", cal({"Hour": 1, "Minute": 1}))["owner"] == "")
check("a daemon and a one-off rows get it too",
      one("demo.a", {"ProgramArguments": ["/bin/true"], "KeepAlive": True}, cfg=c)["owner"] == "ops"
      and one("demo.a", cal({"Month": 11, "Day": 20, "Hour": 9, "Minute": 5}), cfg=c)["owner"] == "ops")

print("schedules_list carries them (fixture folder, launchctl stubbed)")
AGENTS = Path(tempfile.mkdtemp(prefix="sched-slots-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))
atexit.register(shutil.rmtree, AGENTS, ignore_errors=True)
CONFIG = sf_config.config_dir() / "schedules.json"
CALLS = []


def fake_run(cmd, *a, **kw):
    CALLS.append(list(cmd))
    if cmd[:2] == ["launchctl", "list"]:
        return subprocess.CompletedProcess(cmd, 0, "PID\tStatus\tLabel\n-\t0\tdemo.daily\n", "")
    raise AssertionError(f"unexpected process: {cmd}")


subprocess.run = fake_run


class Api(api_schedules.SchedulesApiMixin):
    @staticmethod
    def _launch_agents_dir():
        return AGENTS

    @staticmethod
    def _sched_now():
        return NOW

    def _sched_supported(self):
        return True


def put(label, **plist):
    plist.setdefault("Label", label)
    (AGENTS / f"{label}.plist").write_bytes(plistlib.dumps(plist))


put("demo.daily", ProgramArguments=["/bin/bash", "/opt/demo/a.sh"], StartCalendarInterval={"Hour": 9, "Minute": 5})
put("demo.poll", ProgramArguments=["/bin/bash", "/opt/demo/b.sh"], StartInterval=600)
put("other.vendor", ProgramArguments=["/opt/vendor/u"], StartInterval=3600)
before = None
CONFIG.write_text(json.dumps({"prefixes": ["demo."], "owners": {"demo.daily": "ops", "other.vendor": "x"}}), encoding="utf-8")
before = CONFIG.read_text(encoding="utf-8")
out = json.loads(Api().schedules_list())
by = {x["id"]: x for x in out["schedules"]}
check("the listing has slots and owner on every row", set(by) == {"demo.daily", "demo.poll"}
      and by["demo.daily"]["slots"]["calendar"] == [{"Hour": 9, "Minute": 5}] and by["demo.daily"]["owner"] == "ops"
      and by["demo.poll"]["slots"]["interval"] == 600 and by["demo.poll"]["owner"] == "", out)
check("…the owner of a label outside the prefixes lists nothing (an owner is not an access rule)", "other.vendor" not in by)
check("…the config file was only read", CONFIG.read_text(encoding="utf-8") == before)
check("…only `launchctl list` was asked of launchd", all(c[:2] == ["launchctl", "list"] for c in CALLS), CALLS)
CONFIG.unlink(missing_ok=True)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
