#!/usr/bin/env python3
"""The trail for when the app goes (sf_trail.py): the start line, faulthandler's file and
its rotation, uncaught exceptions, console events, the window's events, and every call
from the page in and out. pywebview and the window are stand-ins; nothing here starts a
window.

Run: .venv/bin/python tests_sf_trail.py
"""
import os
import re
import sys
import tempfile
import threading
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sf_trail  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


TMP = Path(tempfile.mkdtemp(prefix="sf-trail-"))
LOG = []


def log(tag, msg):
    LOG.append(f"[{tag}] {msg}")


def logged(fragment):
    return [ln for ln in LOG if fragment in ln]


print("1. the start line: which program, and whether a console can end it")
line = sf_trail.start_line({"SHELLFRAME_RESTART_FROM_PID": "4242"}, r"C:\app\.venv\Scripts\python.exe", True)
check("python.exe on a console, started by a restart: said, with what ends it",
      "exe=python.exe" in line and "console=yes" in line and "restarted_from=4242" in line
      and "closing it, or Ctrl+C / Ctrl+Break in it, ends the app at once" in line, line)
line = sf_trail.start_line({}, r"C:\app\.venv\Scripts\pythonw.exe", False)
check("pythonw.exe without one: no warning", "exe=pythonw.exe" in line and "console=no" in line
      and "restarted_from=-" in line and "ends the app" not in line, line)
check("a Mac: console unknown", "console=?" in sf_trail.start_line({}, "/usr/bin/python3", None))

print("2. faulthandler's file: the runs before that wrote something are kept")
fault = TMP / "fault.log"
fault.write_text("2026-11-11 00:00:00 pid=1\n")
check("a run that wrote nothing past its first line is not kept", sf_trail.rotate_kept(str(fault)) is False
      and not Path(str(fault) + ".1").exists())
fault.write_text("2026-11-11 00:00:00 pid=1\nWindows fatal exception: access violation\n")
Path(str(fault) + ".1").write_text("older\nstack\n")
check("one that did is kept as .1, and the one before as .2",
      sf_trail.rotate_kept(str(fault)) and "access violation" in Path(str(fault) + ".1").read_text()
      and Path(str(fault) + ".2").read_text() == "older\nstack\n" and not fault.exists())

print("3. start(): the line, faulthandler on, uncaught exceptions logged")
prev_sys, prev_thread = sys.excepthook, threading.excepthook
seen = []
sys.excepthook = lambda *a: seen.append("sys")
threading.excepthook = lambda a: seen.append("thread")
sf_trail.start(log, TMP)
check("the start line is in the debug log", logged("[lifecycle] started pid="), LOG)
f = TMP / "shellframe_fault.log"
check("faulthandler's file waits for the instance that stays", not f.exists())
sf_trail.keep_faults()
check("…then faulthandler writes beside the debug log", f.exists() and "pid=" in f.read_text()
      and sf_trail.faulthandler.is_enabled())
try:
    raise ValueError("boom")
except ValueError:
    sys.excepthook(*sys.exc_info())
check("an uncaught exception in the main thread: logged, then the usual handling",
      logged("uncaught exception in the main thread: ValueError: boom") and seen == ["sys"], (LOG[-1:], seen))


def worker():
    raise KeyError("in a thread")


t = threading.Thread(target=worker, name="sf-test-worker")
t.start()
t.join()
check("…and in another thread, by its name", logged("uncaught exception in thread sf-test-worker: KeyError")
      and seen == ["sys", "thread"], (LOG[-1:], seen))
sys.excepthook, threading.excepthook = prev_sys, prev_thread
sf_trail.faulthandler.disable()

print("4. console events, as Windows sends them")
check("its window closed: logged, and the default handling still runs (False)",
      sf_trail.console_event(2) is False and logged("console event: its console window was closed (type 2) → Windows ends the app now"))
sf_trail.console_event(0)
check("Ctrl+C: logged (pywebview turns it into the window loop ending)", logged("console event: Ctrl+C (type 0)")
      and not logged("Ctrl+C (type 0) →"))
sf_trail.exiting("webview.start() returned: the window's loop ended")
check("the line before an os._exit", logged("exiting: webview.start() returned: the window's loop ended pid="))


print("5. the window's events")


class Ev:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, f):
        self.handlers.append(f)
        return self

    def fire(self):
        return [h() for h in self.handlers]


win = types.SimpleNamespace(events=types.SimpleNamespace(closing=Ev(), closed=Ev(), shown=Ev(), loaded=Ev()))
sf_trail.watch_window(win)
check("closing: logged, and the handler returns None (False would cancel the close)",
      win.events.closing.fire() == [None] and logged("[lifecycle] window closing"))
win.events.closed.fire()
win.events.shown.fire()
win.events.loaded.fire()
check("closed, shown, page loaded", logged("window closed") and logged("window shown") and logged("page loaded"))
LOG.clear()
sf_trail.watch_window(object())
check("a window without events: said, nothing raised", logged("window events not watched"))

print("6. every call from the page, in and out")
trail = TMP / "jsapi.log"


class Api:
    def chat_view(self, sid, limit, rev):
        return {"sid": sid}

    def boom(self):
        raise ValueError("no")

    def pywebviewEventHandler(self):
        return "pywebview's own"


def orig_bridge(win, func_name, param, value_id):      # pywebview's: _functions first, then the api
    func = win._functions.get(func_name) or getattr(win._js_api, func_name, None)
    return func(*param)


util = types.SimpleNamespace(js_bridge_call=orig_bridge)
plat = types.SimpleNamespace(js_bridge_call=orig_bridge)
pw = types.SimpleNamespace(_functions={}, _js_api=Api())
check("installed on pywebview and on the platform module that imported it by name",
      sf_trail.trail_js_api(pw, util=util, modules=[plat], path=str(trail))
      and util.js_bridge_call is plat.js_bridge_call and util.js_bridge_call is not orig_bridge)
r = util.js_bridge_call(pw, "chat_view", ["s3", 120, "a-secret-rev-value"], "v1")
lines = trail.read_text().splitlines()
check("in and out, with the tab's id and the number of arguments; the result unchanged",
      r == {"sid": "s3"} and len(lines) == 2 and re.search(r"> chat_view\(s3, 3 args\) #\d+$", lines[0])
      and re.search(r"< chat_view #\d+ \d+ms$", lines[1]), lines)
check("…never an argument's content", "a-secret-rev-value" not in trail.read_text())
try:
    plat.js_bridge_call(pw, "boom", [], "v2")
    raised = False
except ValueError:
    raised = True
check("a call that raises: the exception goes on to pywebview as before, and the trail says so",
      raised and re.search(r"< boom #\d+ \d+ms raised ValueError$", trail.read_text().splitlines()[-1]))
util.js_bridge_call(pw, "chat_view", ["s3", 120, ""], "v3")
check("wrapped once, then kept in window._functions", sorted(pw._functions) == ["boom", "chat_view"]
      and len(trail.read_text().splitlines()) == 6)
check("pywebview's own calls are not touched", util.js_bridge_call(pw, "pywebviewEventHandler", [], "v4") == "pywebview's own"
      and "pywebviewEventHandler" not in pw._functions and len(trail.read_text().splitlines()) == 6)
check("installed twice: no second layer", sf_trail.trail_js_api(pw, util=util, modules=[plat], path=str(trail))
      and util.js_bridge_call._sf_trail)
LOG.clear()
check("a pywebview without window._functions: off, and said",
      sf_trail.trail_js_api(types.SimpleNamespace(), util=types.SimpleNamespace(js_bridge_call=orig_bridge)) is False
      and logged("js_api trail off"))
saved_max = sf_trail.TRAIL_MAX
sf_trail.TRAIL_MAX = 300
for _ in range(20):
    util.js_bridge_call(pw, "chat_view", ["s3", 1, ""], "v")
sf_trail.TRAIL_MAX = saved_max
check("the trail rotates to .1 past its size", Path(str(trail) + ".1").exists() and trail.stat().st_size < 600)

print("7. main.py: first thing in main(), and a line before every quiet os._exit")
src = (HERE / "main.py").read_text(encoding="utf-8")
body = src[src.index("def main():\n"):]
check("sf_trail.start is main()'s first call", body.split("\n")[1].strip().startswith("sf_trail.start(_dlog, TMP_DIR)"))
check("the window's events and the page's calls are watched",
      "window = webview.create_window(**create_kwargs)\n    sf_trail.watch_window(window)\n    sf_trail.trail_js_api(window)" in src)
check("the loop ending is said before cleanup and os._exit",
      "sf_trail.exiting(\"webview.start() returned: the window's loop ended\")\n    api.cleanup_all()\n    os._exit(0)" in src)
check("faulthandler's file only after the single-instance guard",
      "    _ensure_single_instance()\n    sf_trail.keep_faults()" in src)
check("the second instance that steps aside says so",
      'sf_trail.exiting(f"another instance (pid={old_pid}) is running")\n    os._exit(0)' in src)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
