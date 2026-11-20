#!/usr/bin/env python3
"""The ops TV object (office_ops.py), its desktop notification (desktop_notify.py) and its js_api mixin (api_ops.py).

Over a scripted sampler and a clock the test moves itself, in a folder of its own (nothing is written to the
real state or config folder, and no notification is sent):
  - the pace: 300 s, 30 s while a rule is over its line, 2 s while the detail screen is live, back to 300 s
    60 s after the last heartbeat
  - an alert event notifies once (not while muted, not on recovery, not a warn); the page is told when the level
    changes, not every sample
  - the 24-hour record: one point per 5 minutes however often it samples, 288 at most, written every 30 minutes
    and when it stops, read back, stale points dropped; rules and mute in ops.json
  - summary / clean_summary / view / clean_view: the eight widget kinds only, numbers clamped, strings in the page's language
  - the flag: off = not in the registry, every call `code: "off"`, no thread
  - the thread starts, samples and stops
Run: .venv/bin/python tests_ops_object.py
"""
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="sf-ops-object-"))
os.environ["SHELLFRAME_TESTS"] = "1"
os.environ["SHELLFRAME_CONFIG_DIR"] = str(TMP / "config")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules["webview"] = MagicMock()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import api_office_objects as aoo  # noqa: E402
import api_ops  # noqa: E402
import desktop_notify  # noqa: E402
import office_objects as oo  # noqa: E402
import office_ops as O  # noqa: E402
import ops_sampler  # noqa: E402
import sf_config  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


REAL_STATE = Path.home() / ".local" / "state" / "shellframe"
before = {p.name: p.stat().st_mtime for p in REAL_STATE.glob("ops*.json")} if REAL_STATE.exists() else {}
print("isolation")
check("the state folder of this run is inside the test's own folder", str(sf_config.state_dir()).startswith(str(TMP)), sf_config.state_dir())
check("the ops files are there", str(O.state_file()).startswith(str(TMP)) and str(O.history_file()).startswith(str(TMP)))

T0 = 1_800_000_000.0
CJK = re.compile(r"[一-鿿]")


class Clock:
    t = T0

    def __call__(self):
        return self.t


class Fake:
    """A sampler that answers from a script: `values` are merged into a normal reading each time."""
    kind = "mac"

    def __init__(self, clock):
        self.clock, self.values, self.calls, self.tops = clock, {}, [], 0

    def sample(self, full=True, prime=True):
        self.calls.append(full)
        out = dict.fromkeys(ops_sampler.SAMPLE_KEYS)
        out.update(t=self.clock(), cpu=12.0, cores=[10.0] * 6 + [20.0] * 4, cores_p=4, cores_e=6, pressure="normal",
                   swap_used=1 << 30, swap_total=4 << 30, disk_free_pct=50.0, disk_free=250 << 30, disk_total=500 << 30,
                   online=True)
        if full:
            out.update(mem_total=16 << 30, mem_used=10 << 30, mem_pct=62.5, mem_app=6 << 30, mem_wired=2 << 30,
                       mem_compressed=1 << 30, mem_cached=3 << 30, net_in=120000.0, net_out=30000.0, iface="en0")
        out.update(self.values)
        return out

    def top_both(self, n=5):
        self.tops += 1
        a, b = {"name": "Safari", "cpu": 12.0, "rss": 1 << 30, "count": 3}, {"name": "demo-tool", "cpu": 4.0, "rss": 200 << 20, "count": 1}
        big = {"name": "demo-agent", "cpu": 1.0, "rss": 6 << 30, "count": 23}
        return {"cpu": [a, b, big], "mem": [big, a, b]}


def make(keep=False, **kw):
    if not keep:                                    # every case starts with no files of an earlier one
        for f in (O.state_file(), O.history_file()):
            f.unlink(missing_ok=True)
    clock = Clock()
    pushed, sent = [], []
    c = O.Collector(sampler=Fake(clock), clock=clock, notify=lambda title, text: sent.append((title, text)),
                    on_level=pushed.append, **kw)
    c.lang = "en"
    return c, clock, pushed, sent


print("the pace")
c, clock, pushed, sent = make()
check("normally every 300 s", c.interval(clock()) == 300.0)
c.step()
check("a first reading is a full one", c.sampler.calls == [True])
clock.t += 100
c.step()
check("a reading soon after is a light one (only what the rules need)", c.sampler.calls == [True, False])
clock.t += 200
c.step()
check("every 5 minutes a full one", c.sampler.calls[-1] is True, c.sampler.calls)
c.sampler.values = {"disk_free_pct": 5.0}
c.step()
check("a rule over its line: every 30 s", c.interval(clock()) == 30.0 and c.engine.pending())
c.live(True, clock())
check("the detail screen open: every 2 s", c.interval(clock()) == 2.0)
clock.t += 59
check("still live 59 s after the last word", c.is_live(clock()) and c.interval(clock()) == 2.0)
c.live(True, clock())
clock.t += 59
check("a heartbeat keeps it going", c.is_live(clock()))
clock.t += 2
check("60 s with no word: back to the slower pace on its own", not c.is_live(clock()) and c.interval(clock()) == 30.0)
c.live(True, clock())
c.live(False, clock())
check("closing says so at once", not c.is_live(clock()))
c.sampler.values = {}
c2, clock2, *_ = make()
check("with nothing wrong and the screen closed it is 300 s again", c2.interval(clock2()) == 300.0)

print("the top five is read only while the screen is live, every 10 s")
c, clock, pushed, sent = make()
c.step()
check("not live: no top, and nothing kept", c.sampler.tops == 0 and not c.top)
c.live(True, clock())
c.step()
n1 = c.sampler.tops
clock.t += 2
c.step()
clock.t += 2
c.step()
check("live: read once, not again for 10 s", n1 == 1 and c.sampler.tops == 1 and len(c.top["cpu"]) == 3 and len(c.top["mem"]) == 3)
clock.t += 10
c.step()
check("and again after 10 s", c.sampler.tops == 2)
c.live(False, clock())
clock.t += 400
c.step()
check("closed: the list is dropped, not kept", not c.top)

print("an alert event: one notification, and the page is told on a change")
c, clock, pushed, sent = make()
c.step()
check("a normal first reading tells the page nothing", pushed == [] and sent == [])
c.sampler.values = {"disk_free_pct": 6.0}
clock.t += 300
c.step()
check("disk low: alert, one notification, the page told once", len(sent) == 1 and sent[0][0] == "ShellBot" and "6%" in sent[0][1]
      and len(pushed) == 1 and pushed[0]["level"] == "alert", (sent, pushed))
for _ in range(5):
    clock.t += 30
    c.step()
check("the same event does not notify or push again", len(sent) == 1 and len(pushed) == 1)
st = c.state(clock())
check("state(): level, reason, since", st["level"] == "alert" and "6%" in st["reason"] and st["since"] == T0 + 300, st)
c.sampler.values = {}
for _ in range(3):
    clock.t += 30
    c.step()
check("recovery after three low readings: the page is told, no notification", c.state(clock())["level"] == "ok"
      and pushed[-1]["level"] == "ok" and len(sent) == 1, (pushed, sent))
c.act("mute", {"minutes": 60}, "en")
c.sampler.values = {"cpu": 99.0}
for _ in range(12):
    clock.t += 30
    c.step()
check("muted: a CPU alert starts but sends nothing, and the level is muted", len(sent) == 1 and c.state(clock())["level"] == "muted"
      and c.state(clock())["under"] == "alert", c.state(clock()))
c3, clock3, pushed3, sent3 = make()
c3.sampler.values = {"online": False}
c3.act("set_rule", {"id": "net_down", "on": True}, "en")
for _ in range(6):
    c3.step()
    clock3.t += 30
check("a warn event does not notify", c3.state(clock3())["level"] == "warn" and sent3 == [])

print("desktop_notify never sends in a test")
desktop_notify.SENT.clear()
c4, clock4, _p, _s = make()
c4.notify = desktop_notify.send
c4.sampler.values = {"disk_free_pct": 3.0}
c4.step()
check("it is recorded and not sent", desktop_notify.SENT and desktop_notify.SENT[0][1].startswith("Only 3%") and desktop_notify.send("a", "b") is False,
      desktop_notify.SENT)
check("escape: quotes, backslashes, line breaks and control characters", desktop_notify.escape('a "b" \\ c\nd\x07e') == 'a \\"b\\" \\\\ c d e'
      and len(desktop_notify.escape("x" * 1000)) == 240)
plat = sys.platform
try:
    sys.platform = "darwin"
    cmd = desktop_notify.command('He said "hi"', 'line\nbreak "q"')
    check("macOS: osascript display notification, the text escaped", cmd[:2] == ["osascript", "-e"]
          and 'display notification "line break \\"q\\""' in cmd[2] and 'with title "He said \\"hi\\""' in cmd[2], cmd)
    sys.platform = "win32"
    check("Windows: nothing yet", desktop_notify.command("a", "b") is None)
    sys.platform = "linux"
    real_which = desktop_notify.shutil.which
    desktop_notify.shutil.which = lambda name: None
    check("Linux without notify-send: nothing", desktop_notify.command("a", "b") is None)
    desktop_notify.shutil.which = lambda name: "/usr/bin/notify-send"
    check("Linux with notify-send: its arguments, after `--`", desktop_notify.command("T", "-x text") == ["notify-send", "--", "T", "-x text"])
    desktop_notify.shutil.which = real_which
finally:
    sys.platform = plat

print("the 24-hour record")
c, clock, pushed, sent = make()
for _ in range(300):                                     # 10 minutes at a 2 s pace
    c.live(True, clock())
    c.step()
    clock.t += 2
check("sampling every 2 s still keeps one point per 5 minutes (not one per reading)",
      2 <= len(c.history) <= 3 and all(b[0] - a[0] >= 298 for a, b in zip(c.history, c.history[1:])), [p[0] - T0 for p in c.history])
check("a point: time, CPU, memory %, pressure code, swap, free disk %, speeds, route", len(c.history[0]) == 9 and c.history[0][2] == 62.5
      and c.history[0][3] == 0 and c.history[0][8] is True, c.history[0])
c, clock, pushed, sent = make()
c.live(True, clock())
for _ in range(300):                                     # 25 hours, one reading every 5 minutes
    c.step()
    clock.t += 300
    c.live(True, clock())
check("never more than 288 points (24 hours)", len(c.history) == 288, len(c.history))
check("written every 30 minutes while it runs", O.history_file().exists())
c.history = c.history[-3:]
c.flush()
c.live(False)
c.stop()
saved = json.loads(O.history_file().read_text())
check("the file is the points, nothing else (no per-core numbers, no program names)", set(saved) == {"v", "points"}
      and all(len(p) == 9 for p in saved["points"]) and "Safari" not in O.history_file().read_text(), saved.keys())
clock5 = Clock()
clock5.t = c.clock() + 10
c5 = O.Collector(sampler=Fake(clock5), clock=clock5)
check("loaded back after a restart", len(c5.history) == 3, len(c5.history))
clock5.t = c.clock() + 2 * 86400
c6 = O.Collector(sampler=Fake(clock5), clock=clock5)
check("points older than 24 hours are dropped on load", c6.history == [])
O.history_file().write_text("{broken")
check("a broken file is ignored", O.Collector(sampler=Fake(clock5), clock=clock5).history == [])
mt = O.history_file().stat().st_mtime
c7, clock7, *_ = make(keep=True)
c7.step()
time.sleep(0.01)
check("a normal step does not write the file (only every 30 minutes and on stop)", O.history_file().stat().st_mtime == mt)
c7.stop()
check("stop writes the record", O.history_file().stat().st_mtime > mt and json.loads(O.history_file().read_text())["points"])

print("rules and mute are kept in ops.json, not office.json")
c, clock, *_ = make()
r = c.act("set_rule", {"id": "cpu_high", "on": True, "value": 7}, "en")
check("set_rule: ok, refresh", r["ok"] and r["refresh"] and c.engine.cfg["cpu_high"]["value"] == 7, r)
check("a number outside its range is clamped", c.act("set_rule", {"id": "cpu_high", "value": 5000}, "en")["ok"] and c.engine.cfg["cpu_high"]["value"] == 120)
check("a rule that does not exist is refused", c.act("set_rule", {"id": "nope", "on": True}, "en")["ok"] is False)
c.act("mute", {"minutes": 30}, "en")
disk = json.loads(O.state_file().read_text())
check("saved: rules and the mute time", disk["rules"]["cpu_high"]["value"] == 120 and disk["muted_until"] == T0 + 1800, disk)
check("not in office.json", not (sf_config.state_dir() / "office.json").exists())
c8, *_ = make(keep=True)
check("read back by a new collector", c8.engine.cfg["cpu_high"]["value"] == 120 and c8.engine.muted_until == T0 + 1800)
c8.act("unmute", {}, "zh")
c8.act("reset_rules", {}, "en")
check("unmute and reset are saved", json.loads(O.state_file().read_text())["muted_until"] is None
      and json.loads(O.state_file().read_text())["rules"]["cpu_high"]["value"] == 5)
check("an unknown action is refused", c8.act("explode", {}, "en")["ok"] is False)
check("the mute message is in the page's language", "已靜音" in c8.act("mute", {"minutes": "x"}, "zh")["message"]
      and "Muted until" in c8.act("mute", {}, "en")["message"])

print("the top five can be ordered by CPU or by memory (top_sort)")
c, clock, *_ = make()
check("default: auto", c.top_sort == "auto" and O.TOP_SORT_DEFAULT == "auto" and O.TOP_SORTS == ("auto", "cpu", "mem"))


def top_table(coll, lang="en"):
    sec = [x for x in coll.view(lang)["sections"] if x["id"] == "top"][0]
    return sec, sec["widgets"][0]


def live_view(coll, clk, **vals):
    coll.sampler.values = vals
    for _ in range(3):
        coll.live(True, clk())
        coll.step()
        clk.t += 2


live_view(c, clock)
sec, tab = top_table(c)
check("auto, nothing short: ranked by CPU, the switch says so", [r[0] for r in tab["rows"]] == ["Safari ×3", "demo-tool", "demo-agent ×23"]
      and tab["switch"]["value"] == "auto" and tab["switch"]["note"] == "Now ranked by CPU" and sec["title"] == "Top 5 by CPU", tab)
check("the switch: the action, three options", tab["switch"]["action"] == "top_sort"
      and [o["id"] for o in tab["switch"]["options"]] == ["auto", "cpu", "mem"] and [o["label"] for o in tab["switch"]["options"]] == ["Auto", "CPU", "Memory"], tab["switch"])
check("a memory-heavy program (23 processes, 6 GB) shows its summed memory", tab["rows"][2] == ["demo-agent ×23", "1%", "6.0 GB"], tab["rows"])
check("auto: memory pressure not normal", O.Collector.top_order("auto", {"pressure": "warn"}) == ("mem", "pressure")
      and O.Collector.top_order("auto", {"pressure": "critical"}) == ("mem", "pressure"))
check("auto: memory used at 80% or more (79.9 is not)", O.Collector.top_order("auto", {"mem_pct": 80.0}) == ("mem", "mem")
      and O.Collector.top_order("auto", {"mem_pct": 79.9}) == ("cpu", ""))
check("auto: swap at 4 GB or more (just under is not)", O.Collector.top_order("auto", {"swap_used": 4 << 30}) == ("mem", "swap")
      and O.Collector.top_order("auto", {"swap_used": (4 << 30) - 1}) == ("cpu", ""))
check("auto with nothing known is CPU; a fixed choice ignores the readings", O.Collector.top_order("auto", {}) == ("cpu", "")
      and O.Collector.top_order("cpu", {"pressure": "critical"}) == ("cpu", "") and O.Collector.top_order("mem", {}) == ("mem", ""))
live_view(c, clock, mem_pct=81.0)
sec, tab = top_table(c)
check("auto, memory 81%: ranked by memory, the note says why", [r[0] for r in tab["rows"]] == ["demo-agent ×23", "Safari ×3", "demo-tool"]
      and tab["switch"]["note"] == "Now ranked by memory (memory 81% used)" and sec["title"] == "Top 5 by memory", tab["switch"])
live_view(c, clock, pressure="warn")
check("auto, pressure warn: memory, the note names the pressure", "memory pressure is up" in top_table(c)[1]["switch"]["note"])
live_view(c, clock, swap_used=5 << 30)
check("auto, swap 5 GB: memory, the note names the swap", top_table(c)[1]["switch"]["note"] == "Now ranked by memory (swap 5.0 GB)")
live_view(c, clock)
r = c.act("top_sort", {"by": "mem"}, "en")
check("top_sort mem: ok, refresh, ranked by memory with nothing short, no note", r["ok"] and r["refresh"] and c.top_sort == "mem"
      and top_table(c)[1]["rows"][0][0] == "demo-agent ×23" and top_table(c)[1]["switch"]["value"] == "mem" and top_table(c)[1]["switch"]["note"] == "")
check("top_sort cpu: back to CPU", c.act("top_sort", {"by": "cpu"}, "en")["ok"] and top_table(c)[1]["rows"][0][0] == "Safari ×3")
check("an unknown order or no args is refused and changes nothing", c.act("top_sort", {"by": "disk"}, "en")["ok"] is False
      and c.act("top_sort", {}, "en")["ok"] is False and c.act("top_sort", None, "en")["ok"] is False and c.top_sort == "cpu")
check("the Chinese words", "沒有這種排序" in c.act("top_sort", {"by": "x"}, "zh")["message"] and "已存" in c.act("top_sort", {"by": "auto"}, "zh")["message"])
vz = top_table(c, "zh")
check("Chinese: labels, note and title", [o["label"] for o in vz[1]["switch"]["options"]] == ["自動", "CPU", "記憶體"]
      and vz[0]["title"] == "吃資源前 5 名・照 CPU" and vz[1]["switch"]["note"] == "現在照 CPU 排", vz)
c.act("top_sort", {"by": "mem"}, "en")
check("saved in ops.json next to the rules and the mute", json.loads(O.state_file().read_text())["top_sort"] == "mem"
      and "rules" in json.loads(O.state_file().read_text()))
c9, *_ = make(keep=True)
check("a new collector reads it back", c9.top_sort == "mem")
O.state_file().write_text(json.dumps({"v": 1, "top_sort": "bogus"}))
check("an unknown saved value is ignored", make(keep=True)[0].top_sort == "auto")
c.act("mute", {"minutes": 5}, "en")
c.act("reset_rules", {}, "en")
check("muting and resetting the rules keep the order", json.loads(O.state_file().read_text())["top_sort"] == "mem")
pre = {"id": "top", "widgets": [{"kind": "table", "columns": ["a"], "rows": [["x"]], "switch": {
    "action": "top_sort", "value": "zzz", "note": "n " * 100, "options": [{"id": "auto", "label": "A"}, {"id": "Bad Id", "label": "x"}, {"id": "cpu", "label": "C"}]}}]}
w = oo.clean_view({"sections": [pre]})["sections"][0]["widgets"][0]
check("clean_view keeps the switch: an unknown value falls back to the first option, a bad option id is dropped, the note is cut",
      w["switch"]["value"] == "auto" and [o["id"] for o in w["switch"]["options"]] == ["auto", "cpu"] and len(w["switch"]["note"]) <= 120
      and w["switch"]["action"] == "top_sort", w)
for bad in (None, "x", {"action": "Not Ok", "options": [{"id": "a"}, {"id": "b"}]}, {"action": "go", "options": [{"id": "a"}]}, {"action": "go"}):
    pre["widgets"][0]["switch"] = bad
    check(f"clean_view drops a switch that is not usable ({str(bad)[:30]})", "switch" not in oo.clean_view({"sections": [pre]})["sections"][0]["widgets"][0])
pre["widgets"][0].pop("switch")
check("a table without a switch has none", "switch" not in oo.clean_view({"sections": [pre]})["sections"][0]["widgets"][0])

print("summary and what the host keeps of it")
c, clock, *_ = make()
check("no reading yet: level none", c.summary("en")["level"] == "none" and c.summary("zh")["hint"] == "還沒有資料")
c.step()
s = c.summary("zh")
check("summary: the contract's keys", {"badge", "hint", "level", "reason", "since", "muted_until", "updated", "face"} <= set(s)
      and s["level"] == "ok" and s["hint"] == "一切正常" and s["badge"] == 0 and s["updated"] == T0, s)
check("face: the contract's keys", set(s["face"]) == {"cpu", "mem", "pressure", "net_in", "net_out", "online", "disk_free_pct", "spark"}
      and s["face"]["mem"] == 62.5 and s["face"]["pressure"] == "normal" and s["face"]["spark"] == [62.5], s["face"])
clean = oo.clean_summary(s)
check("clean_summary keeps level, reason, since, muted_until, updated and the face", clean["level"] == "ok" and clean["face"] == s["face"]
      and clean["updated"] == T0 and clean["since"] is None and clean["badge"] == 0, clean)
weird = oo.clean_summary({"badge": 1, "hint": "x", "level": "on fire", "reason": "token = abcdefgh12345 " + "y" * 300, "since": "soon",
                          "muted_until": 1e30, "updated": float("nan"),
                          "face": {"cpu": 250, "mem": -4, "pressure": "bad", "net_in": "9", "online": "yes", "extra": 1,
                                   "spark": list(range(40)) + ["x", None, float("inf"), 500], "disk_free_pct": True}})
check("an unknown level becomes none, a long reason is cut and masked, numbers are numbers or null",
      weird["level"] == "none" and len(weird["reason"]) <= 120 and "abcdefgh12345" not in weird["reason"]
      and weird["since"] is None and weird["muted_until"] == 4_000_000_000 and weird["updated"] is None, weird)
check("face: numbers clamped, an odd word dropped, only the contract's keys, spark at most 12 numbers",
      weird["face"]["cpu"] == 100 and weird["face"]["mem"] == 0 and weird["face"]["pressure"] is None and weird["face"]["net_in"] is None
      and weird["face"]["online"] is None and weird["face"]["disk_free_pct"] is None and "extra" not in weird["face"]
      and len(weird["face"]["spark"]) <= 12 and all(isinstance(v, (int, float)) and 0 <= v <= 100 for v in weird["face"]["spark"]), weird["face"])
check("an object with no level adds nothing to its summary", set(oo.clean_summary({"badge": 2})) == {"badge", "hint"})
c.sampler.values = {"pressure": "critical"}
for _ in range(25):
    clock.t += 30
    c.step()
s = c.summary("en")
check("a running alert: badge 1, the reason, since", s["badge"] == 1 and s["level"] == "alert" and s["reason"].startswith("Memory pressure high for")
      and s["since"] == T0 + 30, s)
check("the face carries the latest values", s["face"]["pressure"] == "critical")
c.act("mute", {"minutes": 60}, "en")
s = c.summary("en")
check("muted: level muted, no badge, the hint says until when", s["level"] == "muted" and s["badge"] == 0 and s["hint"].startswith("Muted until") and s["reason"] == "", s)

print("the detail screen")
c, clock, *_ = make()
check("before any reading: a banner of none and the rules to change, no gauges",
      c.view("en")["banner"]["level"] == "none" and [x["id"] for x in c.view("en")["sections"]] == ["rules"])
for _ in range(40):
    c.live(True, clock())
    c.step()
    clock.t += 2
v = c.view("en")
kinds = {w["kind"] for sec in v["sections"] for w in sec["widgets"]}
check("sections in the contract's order", [x["id"] for x in v["sections"]] == ["cpu", "mem", "net", "top", "day", "rules"])
check("only the eight kinds", kinds <= set(oo.WIDGET_KINDS) and kinds == set(oo.WIDGET_KINDS), kinds)
check("refresh 2000 ms, a title", v["refresh_ms"] == 2000 and v["title"] == "Ops board")
cpu = v["sections"][0]["widgets"]
check("cpu: a gauge with text, bars (performance first, then efficiency), a 60 s spark",
      cpu[0]["kind"] == "gauge" and cpu[0]["text"] == "12%" and cpu[0]["tone"] == "ok" and cpu[1]["kind"] == "bars"
      and [i["label"] for i in cpu[1]["items"]] == ["P1", "P2", "P3", "P4", "E1", "E2", "E3", "E4", "E5", "E6"]
      and cpu[1]["items"][0]["group"] == "p" and cpu[1]["items"][0]["value"] == 20.0 and cpu[1]["items"][9]["group"] == "e"
      and cpu[2]["kind"] == "spark" and len(cpu[2]["series"][0]["values"]) == 30 and cpu[2]["max"] == 100, cpu)
mem = v["sections"][1]["widgets"]
check("memory: stack, 30-minute pressure strip, swap stat", [w["kind"] for w in mem] == ["stack", "strip", "stat"]
      and mem[0]["label"] == "Used 62%" and mem[0]["total"] == 16 << 30 and [p["label"] for p in mem[0]["parts"]] == ["App", "Wired", "Compressed", "Cached"]
      and mem[1]["step_s"] == 30 and len(mem[1]["cells"]) == 60 and mem[1]["cells"][-1] == "ok" and mem[2]["value"] == "1.0 GB", mem)
net = v["sections"][2]["widgets"]
check("network and disk: spark of two series, the connection, the disk", [w["kind"] for w in net] == ["spark", "stat", "stat"]
      and len(net[0]["series"]) == 2 and net[1]["value"] == "Connected" and net[2]["value"] == "50%" and net[2]["sub"] == "250.0 GB" and net[2]["tone"] == "ok", net)
top = v["sections"][3]["widgets"][0]
check("top: columns and rows, a program as its name (and how many processes)", top["columns"] == ["Program", "CPU", "Memory"]
      and top["rows"][0] == ["Safari ×3", "12%", "1.0 GB"], top)
day = v["sections"][4]["widgets"][0]
check("24 hours: 288 cells of 5 minutes", day["kind"] == "strip" and day["step_s"] == 300 and len(day["cells"]) == 288
      and day["cells"][-1] == "ok" and day["start"] % 300 == 0, (day["step_s"], len(day["cells"])))
rules = v["sections"][5]["widgets"][0]
check("rules: id, label, on, value, unit, level, min, max, step", rules["kind"] == "rules" and len(rules["items"]) == 6
      and rules["items"][0] == {"id": "mem_pressure_high", "label": "Memory pressure is high for", "on": True, "value": 10, "unit": "min",
                                "level": "alert", "min": 1, "max": 120, "step": 1} and rules["items"][5]["on"] is False, rules["items"][0])
check("the banner when all is well", v["banner"] == {"level": "ok", "text": "All normal", "since": None, "rule": ""}, v["banner"])
check("the whole English screen has no Chinese in it", not CJK.search(json.dumps(v, ensure_ascii=False)), CJK.findall(json.dumps(v, ensure_ascii=False))[:5])
vz = c.view("zh")
text = json.dumps(vz, ensure_ascii=False)
check("the Chinese screen: titles, labels, units and the banner are Chinese", vz["title"] == "維運戰情" and vz["banner"]["text"] == "一切正常"
      and vz["sections"][0]["widgets"][0]["label"] == "全部核心" and vz["sections"][1]["widgets"][0]["label"].startswith("已用")
      and vz["sections"][5]["widgets"][0]["items"][0]["unit"] == "分鐘")
check("and no English label of the English screen is left in it", all(w not in text for w in ("All cores", "Each core", "Free disk", "Connected", "Program")))
check("every widget survives the host's cleaning unchanged", oo.clean_view(v)["sections"] == v["sections"], "")
c.sampler.values = {"pressure": "critical", "cpu": 97.0}
for _ in range(400):
    c.step()
    clock.t += 2
    c.live(True, clock())
v = c.view("en")
check("a running alert: the banner says what, since when, which rule", v["banner"]["level"] == "alert" and v["banner"]["rule"] == "mem_pressure_high"
      and v["banner"]["since"] and v["banner"]["text"].startswith("Memory pressure high for"), v["banner"])
check("the cpu gauge is bad and the strip shows the alert", v["sections"][0]["widgets"][0]["tone"] == "bad" and "alert" in v["sections"][1]["widgets"][1]["cells"])
cleaned = oo.clean_view({"title": "T", "banner": {"level": "weird", "text": "token = abcdefgh12345"},
                         "sections": [{"id": "a", "title": "A", "widgets": [
                             {"kind": "sparkle"}, {"kind": "gauge", "value": 500, "text": "x", "tone": "rainbow"},
                             {"kind": "strip", "cells": ["ok", "purple"], "start": 1, "step_s": 30}, "junk",
                             {"kind": "spark", "series": [{"label": "s", "values": [1, "x", None, float("nan")]}]}]},
                                      {"id": "Bad Id", "widgets": []}]})
w = cleaned["sections"][0]["widgets"]
check("clean_view: an unknown kind is dropped, a tone and a cell word outside the set are emptied, numbers clamped, a bad section dropped",
      len(cleaned["sections"]) == 1 and [x["kind"] for x in w] == ["gauge", "strip", "spark"] and w[0]["value"] == 100 and w[0]["tone"] == ""
      and w[1]["cells"] == ["ok", "none"] and w[2]["series"][0]["values"] == [1, None, None, None]
      and cleaned["banner"]["level"] == "none" and "abcdefgh12345" not in cleaned["banner"]["text"], cleaned)

print("the pressure strip holds a slow reading")
c, clock, *_ = make()
for _ in range(4):
    c.step()
    clock.t += 300
check("readings 5 minutes apart still colour the strip (until 10 minutes after the last)",
      c.view("en")["sections"][1]["widgets"][1]["cells"].count("ok") >= 20, c.view("en")["sections"][1]["widgets"][1]["cells"])

print("an unsupported system")
class NoSampler(Fake):
    kind = "none"

    def sample(self, full=True, prime=True):
        return dict.fromkeys(ops_sampler.SAMPLE_KEYS, None) | {"t": self.clock()}


clockn = Clock()
cn = O.Collector(sampler=NoSampler(clockn), clock=clockn)
cn.step()
real_supported = ops_sampler.supported
ops_sampler.supported = lambda: False
check("level none, a banner that says so, nothing else but the rules", cn.state(clockn())["level"] == "none"
      and cn.view("en")["banner"] == {"level": "none", "text": "This system is not supported yet", "since": None, "rule": ""}
      and [x["id"] for x in cn.view("en")["sections"]] == ["rules"] and cn.summary("zh")["hint"] == "這個系統還不支援"
      and cn.history == [] and cn.engine.last == {}, cn.view("en")["banner"])
ops_sampler.supported = real_supported

print("the object in the office")
check("declared: id ops, furniture tv (a known kind), the lobby wall", oo.normalize_declaration(O.DECLARATION, "en")["furniture"] == "tv"
      and oo.normalize_declaration(O.DECLARATION, "en")["place"] == "lobby:wall" and "tv" in oo.FURNITURE)
decl = oo.normalize_declaration(O.DECLARATION, "zh")
check("title Ops / 維運, the five actions", decl["title"] == "維運" and [a["id"] for a in decl["actions"]] == ["mute", "unmute", "set_rule", "reset_rules", "top_sort"])
check("a built-in list has it, and its registry hides it while the switch is off", any(isinstance(o, O.Ops) for o in aoo.builtin_objects()))
O._enabled = False
reg = oo.Registry([O.Ops()], lambda: [])
check("flag off: not in the registry", reg.objects() == {})
O._enabled = True
fakec, fclock, *_ = make()
O.set_collector(fakec)
fakec.step()
reg = oo.Registry([O.Ops()], lambda: [])
check("flag on: in the registry, with its summary", list(reg.objects()) == ["ops"] and reg.describe(oo.Ctx("en"), "en")[0]["level"] == "ok")
res = reg.action("ops", oo.Ctx("en"), "en", "", "mute", {"minutes": 15})
check("an action goes through the office's own action path", res["success"] and res["ok"] and res["refresh"] and fakec.engine.muted_until == fclock() + 900, res)
check("an undeclared action is refused as for any object", reg.action("ops", oo.Ctx("en"), "en", "", "explode", {})["code"] == "bad_action")
vw = reg.view("ops", oo.Ctx("en"), "en")
check("Registry.view: success and cleaned", vw["success"] and vw["banner"]["level"] == "ok", vw.get("code"))
check("an object without a detail screen: no_view", oo.Registry([oo.OfficeObject()], lambda: []).view("", oo.Ctx("en"), "en")["code"] == "unknown")


class Bare(oo.OfficeObject):
    declaration = {"id": "bare"}


check("a built-in with no view: no_view", oo.Registry([Bare()], lambda: []).view("bare", oo.Ctx("en"), "en")["code"] == "no_view")


class FakePlugin:
    manifest = type("M", (), {"raw": {"office_objects": [{"id": "plug"}]}})()

    def office_view(self, obj_id):
        return {"sections": []}


check("a plugin's object gets no detail screen in this version", oo.Registry([], lambda: [FakePlugin()]).view("plug", oo.Ctx("en"), "en")["code"] == "no_view")
check("an unknown object", reg.view("nope", oo.Ctx("en"), "en")["code"] == "unknown")

print("the js_api mixin")


class Host(aoo.OfficeObjectsApiMixin, api_ops.OpsApiMixin):
    office = True
    flag = False

    def __init__(self):
        self._window = MagicMock()

    def _office_on(self):
        return self.office

    def _ops_flag(self):
        return self.flag

    def _oo_ctx(self, lang):
        return oo.Ctx(lang)


class Counting(O.Collector):
    started = 0
    stopped = 0

    def start(self):
        Counting.started += 1

    def stop(self):
        Counting.stopped += 1


O.configure(False)
cclock = Clock()
for f in (O.state_file(), O.history_file()):
    f.unlink(missing_ok=True)
counting = Counting(sampler=Fake(cclock), clock=cclock)
O.set_collector(counting)
h = Host()
for name, call in (("ops_state", lambda: h.ops_state()), ("office_object_view", lambda: h.office_object_view("ops", "en")),
                   ("office_object_live", lambda: h.office_object_live("ops", True))):
    got = json.loads(call())
    if name == "ops_state":
        check("ops_state with the flag off: on false, level none", got == {"on": False, "level": "none", "reason": "", "since": None}, got)
    else:
        check(f"{name} with the flag off: code off", got["success"] is False and got["code"] == "off", got)
check("the flag off: no collector thread was started", Counting.started == 0 and not O.enabled())
check("office_objects does not list it", all(o["id"] != "ops" for o in json.loads(h.office_objects("en"))["objects"]))
h.office = False
check("the office off too: the office's own refusal", json.loads(h.office_object_view("ops", "en"))["code"] == "off")
h.office = True
h.flag = True
got = json.loads(h.ops_state())
check("the flag on: the collector is started once and ops_state answers", Counting.started == 1 and O.enabled()
      and got == {"on": True, "level": "none", "reason": "", "since": None}, (Counting.started, got))
h.ops_state()
check("calling again does not start it again", Counting.started == 1)
counting.step()
check("ops_state after a reading", json.loads(h.ops_state())["level"] == "ok")
check("office_objects lists the object", any(o["id"] == "ops" and o["furniture"] == "tv" and o["level"] == "ok" for o in json.loads(h.office_objects("en"))["objects"]))
got = json.loads(h.office_object_view("ops", "zh"))
check("office_object_view: success, the contract's top-level keys, in Chinese",
      got["success"] and {"refresh_ms", "title", "banner", "sections"} <= set(got) and got["title"] == "維運戰情" and counting.lang == "zh", got.get("code"))
check("a view for another object: unknown", json.loads(h.office_object_view("bookshelf", "en"))["code"] in ("unknown", "no_view"))
check("office_object_live on: ok, and the collector is live", json.loads(h.office_object_live("ops", True)) == {"success": True} and counting.is_live())
h.office_object_live("ops", False)
check("off again", not counting.is_live())
check("live for another object: unknown", json.loads(h.office_object_live("bookshelf", True))["code"] == "unknown")
act = json.loads(h.office_object_action("ops", "", "mute", json.dumps({"minutes": 60}), "en"))
check("mute through office_object_action", act["success"] and act["ok"] and act["refresh"] and json.loads(h.ops_state())["level"] == "muted", act)
act = json.loads(h.office_object_action("ops", "", "set_rule", json.dumps({"id": "disk_low", "on": True, "value": 99}), "en"))
check("set_rule through it, clamped", act["ok"] and counting.engine.cfg["disk_low"]["value"] == 50, act)
check("reset_rules", json.loads(h.office_object_action("ops", "", "reset_rules", "{}", "en"))["ok"] and counting.engine.cfg["disk_low"]["value"] == 10)
h._ops_push({"level": "alert", "reason": "Disk 'x'", "since": 5})
js = h._window.evaluate_js.call_args[0][0]
check("the page is told through window.__sfOpsLevel with level, reason, since", js.startswith("window.__sfOpsLevel && window.__sfOpsLevel(")
      and '"level": "alert"' in js and '"since": 5' in js, js)
counting.engine.reset_rules()
counting.engine.unmute()
counting.sampler.values = {"disk_free_pct": 3.0}
cclock.t += 300
counting.step()
got = json.loads(h.ops_state())
check("ops_state: an alert, since is a whole number", got["level"] == "alert" and isinstance(got["since"], int) and "3%" in got["reason"], got)
sm = counting.summary("en")
check("summary: since and muted_until are whole numbers too", isinstance(sm["since"], int) and sm["muted_until"] is None, sm)
h._ops_push({"level": "alert", "reason": "x", "since": 1800000300.7})
check("the page gets since as a whole number", '"since": 1800000300}' in h._window.evaluate_js.call_args[0][0], h._window.evaluate_js.call_args[0][0])
told = []
counting.on_level = told.append
counting.act("set_rule", {"id": "disk_low", "value": 8}, "en")
check("changing the number of a running rule does not blink the TV to ok (it recovers on its readings)",
      counting.state(cclock())["level"] == "alert" and told == [], (counting.state(cclock()), told))
counting.act("reset_rules", {}, "en")
check("nor does reset", counting.state(cclock())["level"] == "alert" and told == [])
counting.sampler.values = {}
counting.on_level = h._ops_push
h.flag = False
check("the flag turned off: the collector is stopped and the calls say off", json.loads(h.ops_state())["on"] is False and Counting.stopped == 1
      and json.loads(h.office_object_view("ops", "en"))["code"] == "off")
check("turning the flag off tells the page the level is gone (the red dot)", '"level": "none"' in h._window.evaluate_js.call_args[0][0], h._window.evaluate_js.call_args[0][0])
h.flag = True
Counting.started = 0
O.configure(False, tell=False)
threads = [threading.Thread(target=h._ops_sync) for _ in range(8)]
[t.start() for t in threads]
[t.join() for t in threads]
check("eight calls at once start the collector once", Counting.started == 1, Counting.started)
h._window.evaluate_js.reset_mock()
h._ops_shutdown()
check("the app closing stops it without a call into the page", not O.enabled() and h._window.evaluate_js.call_count == 0)

print("the thread")
tclock_real = time.time
real = O.Collector(sampler=Fake(tclock_real), clock=tclock_real)
real.START_DELAY_S = 0.0
O.set_collector(real)
real.start()
deadline = time.time() + 3
while time.time() < deadline and real.updated is None:
    time.sleep(0.02)
check("started: it takes a first reading by itself", real.updated is not None and real.running)
real.live(True)
n = len(real.sampler.calls)
time.sleep(2.3)
check("live wakes it at once and it reads about every 2 s", len(real.sampler.calls) >= n + 1, (n, len(real.sampler.calls)))
t0 = time.time()
real.stop()
check("stop returns quickly and ends the thread", time.time() - t0 < 2.5 and not real.running and not any(t.name == "ops-collector" and t.is_alive() for t in threading.enumerate()))
real.start()
real.stop()
check("it can be started again and stopped again", not real.running)
O.configure(False)

after = {p.name: p.stat().st_mtime for p in REAL_STATE.glob("ops*.json")} if REAL_STATE.exists() else {}
check("nothing was written to the real state folder", before == after, (before, after))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
