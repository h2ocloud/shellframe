#!/usr/bin/env python3
"""js_guard: evaluate_js from a background thread gives up after its time limit, a pile of
stuck calls stops new ones, the main thread calls straight through, and main() installs it.
A stand-in window; nothing here starts pywebview."""

import sys
import threading
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import js_guard  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def in_thread(fn):
    """fn() on a background thread, the way the app's pushes run; its result."""
    out = {}
    t = threading.Thread(target=lambda: out.update(r=fn()))
    t.start()
    t.join(10)
    return out.get("r", "<still running>")


LOG = []
release = threading.Event()
calls = []


def evaluate_js(script):
    calls.append((script, threading.current_thread().name))
    if script.startswith("hang"):
        release.wait(20)
        return "late"
    if script.startswith("boom"):
        raise ValueError("bad script")
    return "ok:" + script


win = types.SimpleNamespace(evaluate_js=evaluate_js)
print("1. a background call")
check("installs once", js_guard.install(win, lambda c, m: LOG.append(m), limit=0.3, max_stuck=2)
      and not js_guard.install(win, lambda c, m: None))
check("an answer comes back as before", in_thread(lambda: win.evaluate_js("x")) == "ok:x")
check("…on a helper thread, not the caller's", calls[-1][1] == "evaluate_js", calls[-1])


def raised():
    try:
        win.evaluate_js("boom")
    except ValueError as e:
        return str(e)
    return "no error"


check("an error in the page call is raised to the caller", in_thread(raised) == "bad script")

print("2. a call that never comes back")
t0 = time.monotonic()
check("the caller stops waiting after the limit", in_thread(lambda: win.evaluate_js("hang 1")) is None
      and time.monotonic() - t0 < 2, time.monotonic() - t0)
check("…and the debug log says so, with the script's start", any("gave no answer in 0.3s" in m and "hang 1" in m for m in LOG), LOG)
in_thread(lambda: win.evaluate_js("hang 2"))
n_calls = len(calls)
LOG.clear()
check("two stuck: a new call is dropped at once, without a thread",
      in_thread(lambda: win.evaluate_js("x")) is None and len(calls) == n_calls)
in_thread(lambda: win.evaluate_js("y"))
check("…said once, not per call", sum("new ones are dropped" in m for m in LOG) == 1, LOG)
caller = {}


def push_output():
    caller["name"] = threading.current_thread().name
    return win.evaluate_js('_pushOutput("s1","text")')


check("terminal output is never dropped: it goes straight through, on the caller's thread, even now",
      in_thread(push_output) == 'ok:_pushOutput("s1","text")' and calls[-1][1] == caller["name"], calls[-1])
release.set()
time.sleep(0.3)
check("once the stuck ones come back, calls go through again", in_thread(lambda: win.evaluate_js("z")) == "ok:z")

print("3. the window's own thread")
check("the main thread calls straight through", win.evaluate_js("m") == "ok:m"
      and calls[-1][1] == threading.main_thread().name)
check("nothing to guard: no evaluate_js", js_guard.install(types.SimpleNamespace()) is False)

print("4. main() installs it")
src = (HERE / "main.py").read_text(encoding="utf-8")
check("right after the window is made and watched",
      "    sf_trail.trail_js_api(window)\n    js_guard.install(window, _dlog)" in src
      and src.index("js_guard.install(window, _dlog)") < src.index("    api._window = window"))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
