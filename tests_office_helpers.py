#!/usr/bin/env python3
"""The office's Python side: a tab's subagents (office_helpers.py) and what the office keeps (api_office.py).

Asked for: a Claude Code tab's subagents show in the office as part-timers beside the tab that
launched them, so a stuck one or a pile of them is noticed; seats and accessories survive a
restart of the app.
  - `<dir>/<S>.jsonl` → `<dir>/<S>/subagents`
  - a fake subagents folder (demo meta files): running and written lately = working; quiet for
    a few minutes = idle; silent 10 minutes = stuck; finished = gone; silent for hours = a
    leftover, not shown; a broken meta file is skipped; a meta file is read once until it changes
  - office_helpers(): only while the switch is on, only Claude tabs, the transcript path the status
    monitor already resolved (no lookup of its own), sid → list
  - office_prefs / office_prefs_save: a file in the state folder (known keys only, size-capped,
    an object only), never config.json
Demo data only. Run: .venv/bin/python tests_office_helpers.py
"""
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import api_host  # noqa: E402
import api_office  # noqa: E402
import office_helpers  # noqa: E402
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


NOW = time.time()
ROOT = Path(tempfile.mkdtemp(prefix="office-helpers-"))
PROJ = ROOT / "projects" / "-demo-project"
PROJ.mkdir(parents=True)
TRANSCRIPT = PROJ / "11111111-2222-3333-4444-555555555555.jsonl"
TRANSCRIPT.write_text("{}\n", encoding="utf-8")
SUB = PROJ / TRANSCRIPT.stem / "subagents"
SUB.mkdir(parents=True)


def agent(aid, desc, quiet_s, finished=False, meta_text=None, jsonl=True):
    meta = SUB / f"agent-{aid}.meta.json"
    body = {"agentType": "general-purpose", "description": desc, "model": "demo-model", "spawnDepth": 1,
            "requestShape": "background"}
    if finished:
        body["finished"] = True
    meta.write_text(meta_text if meta_text is not None else json.dumps(body, ensure_ascii=False), encoding="utf-8")
    t = NOW - quiet_s
    os.utime(meta, (t, t))
    if jsonl:
        j = SUB / f"agent-{aid}.jsonl"
        j.write_text('{"demo": true}\n', encoding="utf-8")
        os.utime(j, (t, t))


agent("run", "示範：跑一次完整測試", 20)
agent("quiet", "示範：整理三份文件", 300)
agent("stale", "示範：查一個很慢的網站", 900)
agent("done", "示範：已經做完的事", 30, finished=True)
agent("old", "示範：好幾天前沒收尾的", 9 * 3600)
agent("broken", "", 10, meta_text="{not json")
agent("nojsonl", "示範：剛派出去", 5, jsonl=False)
(SUB / "notes.txt").write_text("not an agent", encoding="utf-8")

print("the folder")
check("a transcript's subagents folder", office_helpers.folder_of(str(TRANSCRIPT)) == str(SUB))
check("…nothing for anything else", office_helpers.folder_of("") == "" and office_helpers.folder_of("/x/y.txt") == "")
rows = office_helpers.scan(str(SUB), NOW)
by = {r["id"]: r for r in rows}
check("the running ones only: finished, leftover and broken ones are not shown",
      sorted(by) == ["nojsonl", "quiet", "run", "stale"], sorted(by))
check("written in the last 2 minutes: working", by["run"]["state"] == "working" and by["nojsonl"]["state"] == "working")
check("quiet for 5 minutes: idle", by["quiet"]["state"] == "idle")
check("silent 15 minutes and not finished: stuck", by["stale"]["state"] == "stuck")
check("description, model and type from the meta file",
      by["run"]["description"] == "示範：跑一次完整測試" and by["run"]["model"] == "demo-model"
      and by["run"]["agent_type"] == "general-purpose", by["run"])
check("last_at is the transcript's mtime", abs(by["stale"]["last_at"] - (NOW - 900)) < 2, by["stale"])
check("oldest first", rows == sorted(rows, key=lambda r: (r["started_at"], r["id"])), [(r["id"], r["started_at"]) for r in rows])
check("a missing folder is no part-timers", office_helpers.scan(str(ROOT / "nope"), NOW) == [])

reads = []
real_open = open


def counting_open(path, *a, **k):
    if str(path).endswith(".meta.json"):
        reads.append(str(path))
    return real_open(path, *a, **k)


office_helpers.open = counting_open
office_helpers.scan(str(SUB), NOW)
check("a meta file that has not changed is not read again", reads == [], reads)
agent("run", "示範：跑一次完整測試", 20, finished=True)
os.utime(SUB / "agent-run.meta.json", (NOW + 1, NOW + 1))
rows2 = {r["id"] for r in office_helpers.scan(str(SUB), NOW + 2)}
check("…one that changed is read again: it finished, it is gone", "run" not in rows2 and len(reads) == 1, (rows2, reads))
del office_helpers.open

print("office_helpers()")
CFG = {"settings": {"experimental_office": False}}
api_host._ns = {"__name__": "demo", "load_config": lambda: CFG}


class Tracker:
    def __init__(self):
        self.asked = []

    def transcript_path(self, sid):
        self.asked.append(sid)
        return str(TRANSCRIPT) if sid == "s1" else None


class App(api_office.OfficeApiMixin):
    def __init__(self):
        self.sessions = {"s1": types.SimpleNamespace(cmd="claude --demo", alive=True),
                         "s2": types.SimpleNamespace(cmd="codex", alive=True),
                         "s3": types.SimpleNamespace(cmd="claude", alive=False),
                         "s4": types.SimpleNamespace(cmd="claude", alive=True)}
        self._status_tracker = Tracker()


app = App()
off = json.loads(app.office_helpers())
check("refused while the switch is off, without looking", off["success"] is False and app._status_tracker.asked == [], off)
CFG["settings"]["experimental_office"] = True
got = json.loads(app.office_helpers())
check("on: sid → its running subagents", got["success"] and sorted(got["helpers"]) == ["s1"]
      and {r["id"] for r in got["helpers"]["s1"]} == {"quiet", "stale", "nojsonl"}, got)
check("…only the open Claude tabs are looked at, by the path the monitor already has",
      sorted(app._status_tracker.asked) == ["s1", "s4"], app._status_tracker.asked)

print("the monitor's path, no lookup of its own")
import agent_status  # noqa: E402
tr = agent_status.StatusTracker()
check("nothing resolved yet: no path (and no lookup)", tr.transcript_path("s1") is None)
tr._path_cache["s1"] = (str(TRANSCRIPT), 0.0)
tr._path_cache["s2"] = (str(ROOT / "gone.jsonl"), 0.0)
check("the path the monitor resolved, however old", tr.transcript_path("s1") == str(TRANSCRIPT))
check("…not one whose file is gone", tr.transcript_path("s2") is None)

print("what the office keeps")
cfg_file = sf_config.config_file()
before = cfg_file.read_text(encoding="utf-8") if cfg_file.exists() else None
check("nothing kept yet", json.loads(app.office_prefs()) == {"success": True, "prefs": {}})
res = json.loads(app.office_prefs_save(json.dumps({"seats": {"eng": {"zone": "辦公區#1", "slot": 3}}, "items": {"eng": ["plant"]},
                                                     "mode": "flat", "legendMin": True, "panelTab": "info", "other": 1}, ensure_ascii=False)))
back = json.loads(app.office_prefs())["prefs"]
check("saved and read back (the panel's tab too), unknown keys dropped", res["success"] and back == {
    "seats": {"eng": {"zone": "辦公區#1", "slot": 3}}, "items": {"eng": ["plant"]}, "mode": "flat", "legendMin": True,
    "panelTab": "info"}, back)
check("…in the state folder, as office.json", api_office.prefs_file() == sf_config.state_dir() / "office.json"
      and api_office.prefs_file().exists())
check("…config.json untouched", (cfg_file.read_text(encoding="utf-8") if cfg_file.exists() else None) == before)
check("too large is refused", json.loads(app.office_prefs_save("x" * (api_office.PREFS_MAX + 1)))["success"] is False)
check("not an object is refused", json.loads(app.office_prefs_save("[1, 2]"))["success"] is False
      and json.loads(app.office_prefs_save("{oops"))["success"] is False)
check("…and a refusal keeps what was there", json.loads(app.office_prefs())["prefs"]["mode"] == "flat")
api_office.prefs_file().write_text("{broken", encoding="utf-8")
check("a broken file reads as nothing kept", json.loads(app.office_prefs())["prefs"] == {})

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
