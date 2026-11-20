#!/usr/bin/env python3
"""The ops TV's rule state machine (ops_rules.py): pure logic, the time of each sample is passed in.

  - a condition must hold for its minutes before it is an event; `since` is the FIRST sample over the line
  - the same event is counted once (one start, however many samples follow)
  - recovery needs 3 samples in a row under the line; a single dip does not end or restart anything
  - a field that could not be read (None) changes nothing; a long gap restarts what was building up
  - mute hides the level and the notification, not the tracking; rules switch off and change within min..max
  - saved settings are validated; the defaults are the design's table
Run: .venv/bin/python tests_ops_rules.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops_rules as R  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


T0 = 1_800_000_000.0
MIN = 60.0


def smp(**kw):
    base = {"cpu": 10.0, "pressure": "normal", "swap_used": 1 << 30, "disk_free_pct": 50.0, "online": True}
    base.update(kw)
    return base


print("the defaults are the design's table")
d = {i: R.spec(i) for i in R.IDS}
check("six rules, in the design's order", list(R.IDS) == ["mem_pressure_high", "mem_pressure_warn", "cpu_high",
                                                          "swap_growth", "disk_low", "net_down"])
check("memory pressure high: 10 min, alert", (d["mem_pressure_high"]["value"], d["mem_pressure_high"]["level"]) == (10, "alert"))
check("memory pressure warning: 30 min, warn", (d["mem_pressure_warn"]["value"], d["mem_pressure_warn"]["level"]) == (30, "warn"))
check("CPU: 5 min, alert", (d["cpu_high"]["value"], d["cpu_high"]["level"]) == (5, "alert"))
check("swap: 2 GB, warn", (d["swap_growth"]["value"], d["swap_growth"]["level"]) == (2, "warn"))
check("disk: under 10 %, alert", (d["disk_low"]["value"], d["disk_low"]["level"]) == (10, "alert"))
check("network: 2 min, warn, off by default", (d["net_down"]["value"], d["net_down"]["level"], d["net_down"]["on"]) == (2, "warn", False))
check("every other rule is on by default", all(d[i]["on"] for i in R.IDS if i != "net_down"))

print("a condition must hold for its minutes")
e = R.Engine()
evs = []
for n in range(0, 10):                                   # 0..9 minutes, one sample a minute
    evs += e.feed(smp(pressure="critical"), T0 + n * MIN)
check("9 minutes of critical pressure is not yet an event", not evs and e.status(T0 + 9 * MIN)["level"] == "ok")
check("but the engine is already watching closely (pending)", e.pending())
evs = e.feed(smp(pressure="critical"), T0 + 10 * MIN)
check("the 10th minute starts one alert event", len(evs) == 1 and evs[0].rule == "mem_pressure_high" and evs[0].level == "alert")
check("its `since` is the first sample over the line, not the minute it counted 10", evs[0].since == T0)
st = e.status(T0 + 12 * MIN, "zh")
check("status: alert, the reason says how long, in the page's language",
      st["level"] == "alert" and st["since"] == T0 and st["reason"] == "記憶體壓力高 12 分鐘" and st["rule"] == "mem_pressure_high", st)
check("English words too", e.status(T0 + 12 * MIN, "en")["reason"] == "Memory pressure high for 12 min")

print("the same event is counted once")
more = []
for n in range(11, 40):
    more += e.feed(smp(pressure="critical"), T0 + n * MIN)
check("no second start while it goes on", more == [] or all(x.rule != "mem_pressure_high" for x in more))
check("the warning rule (30 min, warn or worse) started on its own, once",
      sum(1 for x in more if x.rule == "mem_pressure_warn") == 1)
check("status shows the alert first, then the earlier one", e.status(T0 + 40 * MIN)["rule"] == "mem_pressure_high"
      and [a["rule"] for a in e.status(T0 + 40 * MIN)["active"]] == ["mem_pressure_high", "mem_pressure_warn"])

print("recovery needs 3 samples in a row under the line")
e2 = R.Engine()
for n in range(0, 11):
    e2.feed(smp(pressure="critical"), T0 + n * MIN)
check("alert on", e2.status(T0 + 10 * MIN)["level"] == "alert")
e2.feed(smp(), T0 + 11 * MIN)
e2.feed(smp(), T0 + 12 * MIN)
check("two low samples are not recovery", e2.status(T0 + 12 * MIN)["level"] == "alert")
e2.feed(smp(pressure="critical"), T0 + 13 * MIN)
e2.feed(smp(), T0 + 14 * MIN)
e2.feed(smp(), T0 + 15 * MIN)
check("a high sample resets the count", e2.status(T0 + 15 * MIN)["level"] == "alert")
e2.feed(smp(), T0 + 16 * MIN)
check("three in a row: recovered", e2.status(T0 + 16 * MIN)["level"] == "ok" and not e2.pending())
again = []
for n in range(17, 28):
    again += e2.feed(smp(pressure="critical"), T0 + n * MIN)
check("a new rise is a new event with its own `since`", len(again) == 1 and again[0].since == T0 + 17 * MIN, [(a.rule, a.since) for a in again])

print("a short dip does not end or restart an event")
e3 = R.Engine()
starts = []
for n in range(0, 6):
    starts += e3.feed(smp(cpu=95.0), T0 + n * MIN)
check("CPU over 90 % for 5 minutes: one alert, since the first sample", [(x.rule, x.since) for x in starts] == [("cpu_high", T0)], starts)
starts = e3.feed(smp(cpu=20.0), T0 + 6 * MIN) + e3.feed(smp(cpu=95.0), T0 + 7 * MIN) + e3.feed(smp(cpu=95.0), T0 + 8 * MIN)
check("one low sample between is not a recovery and not a second event", starts == [] and e3.status(T0 + 8 * MIN)["since"] == T0)
e3b = R.Engine()
for n in range(0, 3):
    e3b.feed(smp(cpu=95.0), T0 + n * MIN)
e3b.feed(smp(cpu=20.0), T0 + 3 * MIN)
got = []
for n in range(4, 6):
    got += e3b.feed(smp(cpu=95.0), T0 + n * MIN)
check("a dip while it is still building up bridges it: the 5 minutes count from the first sample", [x.since for x in got] == [T0], got)

print("a field that could not be read changes nothing; a gap starts over")
e4 = R.Engine()
for n in range(0, 4):
    e4.feed(smp(pressure="critical"), T0 + n * MIN)
e4.feed(smp(pressure=None), T0 + 4 * MIN)
e4.feed(smp(pressure="critical"), T0 + 10 * MIN)
check("an unreadable pressure is skipped, not counted as low or high", e4.status(T0 + 10 * MIN)["since"] == T0)
e5 = R.Engine(max_gap=900)
e5.feed(smp(pressure="critical"), T0)
got = e5.feed(smp(pressure="critical"), T0 + 8 * 3600)           # the computer slept 8 hours
check("8 hours asleep is not '10 minutes of high pressure'", got == [] and e5.status(T0 + 8 * 3600)["level"] == "ok")
check("it counts again from the sample after the gap", e5.feed(smp(pressure="critical"), T0 + 8 * 3600 + 10 * MIN)[0].since == T0 + 8 * 3600)

print("disk and swap start at once")
e6 = R.Engine()
got = e6.feed(smp(disk_free_pct=6.0), T0)
check("disk under 10 %: an alert at the first sample", [(x.rule, x.level) for x in got] == [("disk_low", "alert")])
check("reason names the number", e6.status(T0, "zh")["reason"] == "磁碟只剩 6%" and e6.status(T0, "en")["reason"] == "Only 6% of the disk is free")
e7 = R.Engine()
base = 5 << 30
e7.feed(smp(swap_used=base), T0)
check("swap steady: nothing", e7.feed(smp(swap_used=base + (1 << 30)), T0 + 10 * MIN) == [])
got = e7.feed(smp(swap_used=base + (3 << 30)), T0 + 20 * MIN)
check("swap grew more than 2 GB within 30 minutes: a warn event", [(x.rule, x.level) for x in got] == [("swap_growth", "warn")], got)
check("the reason says how much", "3.0 GB" in e7.status(T0 + 20 * MIN, "en")["reason"], e7.status(T0 + 20 * MIN, "en"))
e9 = R.Engine(max_gap=10 ** 9)
e9.feed(smp(swap_used=base), T0)
check("growth older than 30 minutes does not count (window)", e9.feed(smp(swap_used=base + (3 << 30)), T0 + 40 * MIN) == [])

print("network is off by default; on, it needs its minutes")
e10 = R.Engine()
check("off: an offline computer is not an event", e10.feed(smp(online=False), T0) == [] and not e10.pending())
e10.set_rule("net_down", on=True)
for n in range(0, 2):
    e10.feed(smp(online=False), T0 + n * MIN)
got = e10.feed(smp(online=False), T0 + 2 * MIN)
check("on: 2 minutes offline is a warn", [(x.rule, x.level) for x in got] == [("net_down", "warn")])
check("a warn does not notify (only alerts do)", got[0].notify is False)

print("mute")
e11 = R.Engine()
e11.mute(60, T0)
got = e11.feed(smp(disk_free_pct=5.0), T0 + MIN)
check("an alert that starts while muted is an event but asks for no notification", len(got) == 1 and got[0].notify is False)
st = e11.status(T0 + 2 * MIN)
check("level is muted, the real level is kept under it", st["level"] == "muted" and st["under"] == "alert" and st["muted_until"] == T0 + 3600)
check("after the mute the level is back, and the mute time is gone", e11.status(T0 + 3601)["level"] == "alert" and e11.status(T0 + 3601)["muted_until"] is None)
e11.unmute()
check("unmute", e11.status(T0 + 2 * MIN)["level"] == "alert")
e12 = R.Engine()
check("an unmuted alert asks for a notification", e12.feed(smp(disk_free_pct=5.0), T0)[0].notify is True)
check("mute minutes are bounded to a day", R.Engine().mute(10 ** 6, T0) == T0 + 24 * 3600)

print("rules switch off and change within their range")
e13 = R.Engine()
check("a number is clamped to min..max", e13.set_rule("cpu_high", value=0) and e13.cfg["cpu_high"]["value"] == 1
      and e13.set_rule("cpu_high", value=10 ** 6) and e13.cfg["cpu_high"]["value"] == 120
      and e13.set_rule("disk_low", value="x") and e13.cfg["disk_low"]["value"] == 10)
check("an unknown rule id is refused", e13.set_rule("nope", on=True) is False)
e14 = R.Engine()
e14.feed(smp(disk_free_pct=5.0), T0)
check("switching a rule off ends its event at once", e14.set_rule("disk_low", on=False) and e14.status(T0)["level"] == "ok" and not e14.pending())
e15 = R.Engine()
e15.set_rule("mem_pressure_high", value=2)
for n in range(0, 2):
    e15.feed(smp(pressure="critical"), T0 + n * MIN)
check("a smaller number makes it fire sooner", [x.rule for x in e15.feed(smp(pressure="critical"), T0 + 2 * MIN)] == ["mem_pressure_high"])
e16 = R.Engine()
e16.feed(smp(disk_free_pct=5.0), T0)
e16.set_rule("disk_low", value=8)
check("changing a running rule's number keeps its event (it ends on its readings, so the TV does not blink)",
      e16.status(T0)["level"] == "alert" and e16.status(T0)["since"] == T0)
e16.reset_rules()
check("reset keeps a running event of a rule that is on by default", e16.status(T0)["level"] == "alert")
e15.reset_rules()
check("reset puts the defaults back", e15.cfg["mem_pressure_high"]["value"] == 10 and e15.cfg["net_down"]["on"] is False)

print("what is saved")
saved = R.Engine()
saved.set_rule("cpu_high", value=7)
saved.set_rule("net_down", on=True)
saved.mute(30, T0)
blob = saved.export()
back = R.Engine(blob)
check("rules and mute survive an export and load", back.cfg["cpu_high"]["value"] == 7 and back.cfg["net_down"]["on"] is True
      and back.muted_until == T0 + 1800)
junk = R.Engine({"rules": {"cpu_high": {"on": "yes", "value": 99999}, "evil": {"on": True}, "disk_low": "x"}, "muted_until": "tomorrow"})
check("junk is dropped or clamped", junk.cfg["cpu_high"] == {"on": True, "value": 120} and "evil" not in junk.cfg
      and junk.cfg["disk_low"]["value"] == 10 and junk.muted_until is None, junk.cfg)
check("not a dict: defaults", R.Engine("x").cfg["cpu_high"]["value"] == 5 and R.Engine([1]).muted_until is None)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
