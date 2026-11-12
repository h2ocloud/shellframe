#!/usr/bin/env python3
"""The Loops panel's backend (api_schedules.py), over fake plists in a temp folder.

  - nothing is listed until the user's own label prefix is in schedules.json (the
    app's config folder); the code names no one's labels
  - only plists under those prefixes are read; the real ~/Library/LaunchAgents is
    never touched (the folder is replaced) and launchctl is never started (subprocess
    is replaced by a recorder that refuses anything it was not told to expect)
  - `launchctl list` is the only evidence of what is loaded; if it cannot be asked
    the payload says so instead of showing every job switched off
  - a platform without launchd says it is unsupported rather than showing an empty list
  - the toggle and the edit prompt refuse a label that is not the user's own, or that
    is a path; the prompt names every plist of a group, warns about a one-off's
    yearly repeat, and follows the language the OS prefers

Names are demo names. The clock is pinned.

Run: .venv/bin/python tests_sched_api.py
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
# A test never reads or writes the app's real config folder (sf_config.config_dir): say so
# even when this file is started some way run_tests.sh does not.
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import api_schedules  # noqa: E402
import sf_config  # noqa: E402

passed = failed = 0
NOW = datetime(2026, 11, 12, 14, 20)


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── the fakes ──────────────────────────────────────────────────────────────

AGENTS = Path(tempfile.mkdtemp(prefix="sched-agents-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))
atexit.register(shutil.rmtree, AGENTS, ignore_errors=True)
CONFIG = sf_config.config_dir() / "schedules.json"
CALLS = []                 # every subprocess call the code under test makes
LAUNCHCTL = {"list": "", "rc": 0, "boom": False}
DIR_READS = []             # every time the plist folder was asked for


def fake_run(cmd, *a, **kw):
    CALLS.append(list(cmd))
    if cmd[:2] == ["launchctl", "list"]:
        if LAUNCHCTL["boom"]:
            raise FileNotFoundError("launchctl")
        return subprocess.CompletedProcess(cmd, LAUNCHCTL["rc"], LAUNCHCTL["list"], "")
    if cmd[:1] == ["launchctl"] and cmd[1] in ("enable", "bootstrap", "bootout", "disable"):
        return subprocess.CompletedProcess(cmd, 0, "", "")
    raise AssertionError(f"unexpected process: {cmd}")


subprocess.run = fake_run


class Session:
    _startup_trust_pending = True


class Api(api_schedules.SchedulesApiMixin):
    SUPPORTED = True
    sessions = {"s1": Session()}

    def __init__(self):
        self.sent = []

    @staticmethod
    def _launch_agents_dir():
        DIR_READS.append(1)
        return AGENTS

    @staticmethod
    def _sched_now():
        return NOW

    def _sched_supported(self):
        return self.SUPPORTED

    def _send_text_to_session(self, session, text, submit=True):
        self.sent.append((text, submit))


def put(label, fname=None, **plist):
    plist.setdefault("Label", label)
    (AGENTS / f"{fname or label}.plist").write_bytes(plistlib.dumps(plist))


def configure(**cfg):
    CONFIG.write_text(json.dumps(cfg), encoding="utf-8")


def unconfigure():
    CONFIG.unlink(missing_ok=True)


def listing(api=None):
    return json.loads((api or Api()).schedules_list())


def loaded(*lines):
    LAUNCHCTL["list"] = "PID\tStatus\tLabel\n" + "".join(l + "\n" for l in lines)
    LAUNCHCTL["rc"] = 0
    LAUNCHCTL["boom"] = False


def punch(label, h, m, arg, day=10):
    put(label, ProgramArguments=["/bin/zsh", "/opt/demo/punch.sh", arg],
        StartCalendarInterval={"Month": 11, "Day": day, "Hour": h, "Minute": m})


put("demo.backup", ProgramArguments=["/bin/bash", "/opt/demo/backup.sh"],
    StartCalendarInterval=[{"Hour": 2, "Minute": 0}, {"Hour": 21, "Minute": 0}])
put("demo.poll", ProgramArguments=["/bin/bash", "/opt/demo/poll.sh"], StartInterval=600)
put("demo.bot", ProgramArguments=["/usr/bin/node", "/opt/demo/bot.js"], KeepAlive=True)
punch("demo.punch-in", 9, 5, "in")
punch("demo.punch-out", 18, 25, "out")
punch("demo.punch-out-early", 18, 10, "out")
put("other.vendor", ProgramArguments=["/opt/vendor/updater"], StartInterval=3600)      # not the user's
put("demo.noisy", ProgramArguments=["/bin/true"], RunAtLoad=True)                      # neither timer nor daemon
(AGENTS / "demo.broken.plist").write_bytes(b"not a plist")                              # unreadable: skipped

print("nothing configured")
unconfigure()
CALLS.clear()
DIR_READS.clear()
r = listing()
check("no schedules.json: an empty list that says it is not configured", r["schedules"] == [] and r["configured"] is False
      and r["unsupported"] is False and r["config_error"] == "", r)
check("…and nothing was asked of launchd, nor the plist folder read", CALLS == [] and DIR_READS == [])
configure(prefixes=["co.", "", 5])
check("prefixes too short to be one's own count as not configured", listing()["configured"] is False and CALLS == [] and DIR_READS == [])
CONFIG.write_text("{nope", encoding="utf-8")
r = listing()
check("a broken schedules.json is reported, and nothing is listed", r["schedules"] == [] and "not valid JSON" in r["config_error"], r)

print("listing")
configure(prefixes=["demo."], titles={"demo.backup": "Nightly backup"})
loaded("-\t0\tdemo.backup", "812\t0\tdemo.poll", "-\t78\tdemo.bot", "-\t0\tdemo.punch-in", "-\t0\tdemo.punch-out",
       "-\t0\tdemo.punch-out-early", "-\t0\tother.vendor")
CALLS.clear()
r = listing()
ids = [s["id"] for s in r["schedules"]]
check("only the user's own plists, in file-name order, a group as one row; no timer-less job; the unreadable one skipped",
      ids == ["demo.backup", "demo.bot", "demo.poll", "group:/opt/demo/punch.sh@11/10"], ids)
by = {s["id"]: s for s in r["schedules"]}
check("a configured title is used", by["demo.backup"]["title"] == "Nightly backup" and by["demo.poll"]["title"] == "poll")
check("cadence in both languages", by["demo.backup"]["when"] == {"en": "Daily 02:00 / 21:00", "zh": "每天 02:00／21:00"}
      and by["demo.poll"]["when"]["zh"] == "每 10 分鐘")
check("a running job says so, a loaded one is enabled", by["demo.poll"]["running"] and by["demo.backup"]["enabled"] and not by["demo.backup"]["running"])
check("the resident job that is not running and exited 78 is flagged", by["demo.bot"]["state"] == "failing"
      and by["demo.bot"]["last_exit"] == 78 and by["demo.bot"]["notes"][0]["code"] == "daemon_failing")
g = by["group:/opt/demo/punch.sh@11/10"]
check("three one-offs on one script are one group, ran 11/10, repeating next year", g["state"] == "ran" and g["when"]["en"] == "One-off · ran 11/10"
      and [m["id"] for m in g["members"]] == ["demo.punch-in", "demo.punch-out-early", "demo.punch-out"]
      and g["notes"][0]["code"] == "yearly_repeat", g)
check("the only process started was `launchctl list`", CALLS == [["launchctl", "list"]], CALLS)
check("the whole payload is JSON of plain values", json.loads(json.dumps(r)) == r and r["launchd_ok"] is True)
loaded("-\t0\tdemo.backup")
r = listing()
g = next(s for s in r["schedules"] if s["group"])
check("what launchd has not loaded is shown as not armed, never as 'every day … on'",
      g["state"] == "unarmed" and g["when"]["zh"] == "單次・未排（等你說要排）"
      and all("每天" not in s["when"]["zh"] for s in r["schedules"] if s["kind"] == "once"))
check("a one-off has no on/off switch", g["toggle"] is False)

print("launchd cannot be asked")
LAUNCHCTL["rc"] = 1
r = listing()
check("a failing `launchctl list` is said, not read as 'nothing is loaded'", r["launchd_ok"] is False and r["configured"] is True)
LAUNCHCTL["rc"], LAUNCHCTL["boom"] = 0, True
check("…also when launchctl cannot be started", listing()["launchd_ok"] is False)
loaded("-\t0\tdemo.backup")

print("another platform")
api = Api()
api.SUPPORTED = False
CALLS.clear()
r = listing(api)
check("no launchd: unsupported, not an empty list", r == {"schedules": [], "unsupported": True, "platform": sys.platform}, r)
check("…and nothing was run or read", CALLS == [])
check("…the toggle and the prompt refuse as well", json.loads(api.schedule_set_enabled("demo.backup", True))["success"] is False
      and json.loads(api.schedule_prompt("demo.backup", "s1"))["success"] is False and CALLS == [])

print("toggle")
api = Api()
loaded("-\t0\tdemo.backup")
CALLS.clear()
r = json.loads(api.schedule_set_enabled("other.vendor", True))
check("a label outside the prefixes is refused, with no process started", r["success"] is False and CALLS == [], CALLS)
for bad in ("demo.../../x", "demo.a/b", "../demo.backup", "demo.missing", ""):
    CALLS.clear()
    check(f"refused: {bad!r}", json.loads(api.schedule_set_enabled(bad, True))["success"] is False and CALLS == [], CALLS)
unconfigure()
check("with no prefix configured nothing can be toggled", json.loads(api.schedule_set_enabled("demo.backup", True))["success"] is False)
configure(prefixes=["demo."])
loaded("-\t0\tdemo.backup", "-\t0\tdemo.poll")
CALLS.clear()
r = json.loads(api.schedule_set_enabled("demo.poll", False))
check("off: bootout then disable", [c[1] for c in CALLS if c[0] == "launchctl" and c[1] != "list"] == ["bootout", "disable"], CALLS)
check("…and the answer is what launchd then reports (still loaded here: so not a success)", r == {"success": False, "enabled": True}, r)
loaded("-\t0\tdemo.backup")
CALLS.clear()
r = json.loads(api.schedule_set_enabled("demo.poll", True))
check("on: enable then bootstrap of that plist", [c[1] for c in CALLS if c[1] != "list"] == ["enable", "bootstrap"]
      and CALLS[1][-1] == str(AGENTS / "demo.poll.plist") and CALLS[1][-2].startswith("gui/"), CALLS)
check("…reported as not on, since launchd does not list it", r == {"success": False, "enabled": False}, r)

print("edit prompt")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
loaded("-\t0\tdemo.backup", "-\t0\tdemo.punch-in", "-\t0\tdemo.punch-out", "-\t0\tdemo.punch-out-early")
api = Api()
r = json.loads(api.schedule_prompt("demo.backup", "s1"))
text, submit = api.sent[-1]
check("one schedule: its plist, script, command and cadence are in the prompt, unsent", r["success"] and submit is False
      and str(AGENTS / "demo.backup.plist") in text and "/opt/demo/backup.sh" in text and "Daily 02:00 / 21:00" in text
      and "one-off" not in text and "launchctl bootout/bootstrap" in text, text)
check("the tab is let past its trust gate, as before", Api.sessions["s1"]._startup_trust_pending is False)
r = json.loads(api.schedule_prompt("group:/opt/demo/punch.sh@11/10", "s1"))
text, _ = api.sent[-1]
check("a group: every member's plist is in the prompt", all(str(AGENTS / f"{m}.plist") in text for m in
      ("demo.punch-in", "demo.punch-out", "demo.punch-out-early")) and "3 jobs" in text, text)
check("…and a one-off is flagged as repeating yearly, so unload it afterwards", "repeats a month-and-day every year" in text and "ran 11/10" in text)
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
api = Api()
api.schedule_prompt("demo.poll", "s1")
check("the prompt follows the OS language (Chinese here)", "我想調整這個排程" in api.sent[-1][0] and "每 10 分鐘" in api.sent[-1][0], api.sent[-1][0])
for lbl, sid in (("other.vendor", "s1"), ("demo.nothing", "s1"), ("demo.backup", "s9")):
    n = len(api.sent)
    check(f"refused: {lbl} in {sid}", json.loads(api.schedule_prompt(lbl, sid))["success"] is False and len(api.sent) == n)
unconfigure()
check("with no prefix configured nothing can be edited", json.loads(api.schedule_prompt("demo.backup", "s1"))["success"] is False)

print("a label that differs from its file name, and files with no Label")
configure(prefixes=["demo."])
put("demo.renamed", fname="demo.on-disk", ProgramArguments=["/bin/true", "/opt/demo/x.sh"], StartInterval=60)
put("demo.nolabel", ProgramArguments=["/bin/true", "/opt/demo/y.sh"], StartInterval=60)
p = plistlib.loads((AGENTS / "demo.nolabel.plist").read_bytes())
del p["Label"]
(AGENTS / "demo.nolabel.plist").write_bytes(plistlib.dumps(p))
ids = [s["id"] for s in listing()["schedules"]]
check("the plist's own Label names the row; with no Label the file name does", "demo.renamed" in ids and "demo.nolabel" in ids, ids)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
