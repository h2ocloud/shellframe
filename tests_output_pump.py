#!/usr/bin/env python3
"""A tab's output reaches the page, the bridges and Frame Link in loops that cannot stop one
another, and a stuck loop is reported and replaced (output_pump.py).

Measured 2026-11-14: the one output loop stopped inside a tmux call right after a restart and
stayed there 21 hours. The terminal froze on that minute and the Telegram bridge saw no output
from any tab; nothing was logged.
  - a push into the page that never comes back: the bridges still get every tab's output
  - the watchdog names the line the push loop is stuck on (debug log and fault log), starts a
    new loop, redraws the visible tab; the new loop pushes what waited
  - the stuck call, when it comes back, ends its thread without pushing again
  - a collector stuck reading one tab: replaced, the other tabs flow, the stale one hands nothing on
  - a startup trust prompt is read on a thread of its own: a slow tmux call delays no output,
    and output that came in during a check gets one more
  - a bridge that raises does not stop the feed; a loop whose code raises is replaced
  - replacements are capped per hour, and the cap is said once
  - what waits for a page that takes nothing is capped, cut at a line break

Run: .venv/bin/python tests_output_pump.py
"""
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from queue import SimpleQueue

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import output_pump  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class Tab:
    def __init__(self):
        self.buf, self.lock = [], threading.Lock()
        self.block_next = None          # an Event: the next read waits on it

    def put(self, text, api):
        with self.lock:
            self.buf.append(text)
        api._output_event.set()

    def read(self):
        with self.lock:
            out, self.buf = "".join(self.buf), []
        ev = self.block_next
        if ev is not None and out:            # stuck with the text in hand, as in a call after the read
            self.block_next = None
            ev.wait()
        return out


class Bridge:
    def __init__(self):
        self.got, self.raise_once = [], False

    def feed_output(self, sid, data):
        if self.raise_once:
            self.raise_once = False
            raise RuntimeError("bridge broke on this chunk")
        self.got.append((sid, data))

    def text(self, sid):
        return "".join(d for s, d in self.got if s == sid)


class Window:
    def __init__(self):
        self.pushed, self.stuck = [], None

    def evaluate_js(self, script):          # the name the stack must show
        ev = self.stuck
        if ev is not None:
            self.stuck = None
            ev.wait()
        self.pushed.append(script)

    def text(self):
        return "".join(self.pushed)


def make(tabs=("s1", "s2")):
    api = types.SimpleNamespace()
    api.sessions = {sid: Tab() for sid in tabs}
    api._output_event = threading.Event()
    api._bridge_queue = SimpleQueue()
    api.bridge, api.line_bridge, api.frame_link = Bridge(), None, None
    api._window, api._active_sid = Window(), tabs[0]
    api._auto_accept_startup_trust_prompt = lambda sid, s: None
    logs, swallowed, repaints = [], [], []
    fault = tempfile.TemporaryFile("w+", encoding="utf-8")
    pump = output_pump.OutputPump(api, lambda c, m: logs.append(f"[{c}] {m}"),
                                  lambda ctx: swallowed.append(ctx),
                                  repaint=lambda: repaints.append(1), fault_file=lambda: fault)
    return api, pump, logs, swallowed, repaints, fault


output_pump.WATCH_EVERY = 3600          # the tests call check() themselves
output_pump.IDLE_S = 0.05
output_pump.STALL_S = 0.6


def fault_text(f):
    f.flush()
    f.seek(0)
    return f.read()

print("a push into the page that never comes back")
api, pump, logs, swallowed, repaints, fault = make()
pump.start()
stuck = threading.Event()
api._window.stuck = stuck
api.sessions["s1"].put("first ", api)
check("the push loop is inside evaluate_js", wait_for(lambda: api._window.stuck is None))
api.sessions["s1"].put("second", api)
api.sessions["s2"].put("other tab", api)
check("the bridge still gets every tab's output",
      wait_for(lambda: api.bridge.text("s1") == "first second" and api.bridge.text("s2") == "other tab"),
      api.bridge.got)
old_push = pump._thread["push"]
time.sleep(output_pump.STALL_S + 0.2)
replaced = pump.check()
check("the watchdog replaces the push loop, only it", replaced == ["push"], replaced)
report = "\n".join(l for l in logs if l.startswith("[pump]"))
check("the debug log names the stuck line", "push loop silent" in report and "evaluate_js" in report
      and "ev.wait()" in report, report[-400:])
ft = fault_text(fault)
check("the fault log has every thread's stack", "output push loop silent" in ft
      and "evaluate_js" in ft and ft.count("Thread 0x") >= 3, ft[:300])
check("the visible tab is redrawn", repaints == [1])
check("the new loop pushes what waited", wait_for(lambda: "second" in api._window.text()
                                                  and "other tab" in api._window.text()), api._window.pushed)
n = len(api._window.pushed)
stuck.set()
old_push.join(2)
check("the stuck call comes back and its thread ends", not old_push.is_alive())
time.sleep(0.2)
check("…having pushed only the call it was in", len(api._window.pushed) == n + 1, api._window.pushed)
api.sessions["s1"].put("after", api)
check("output keeps flowing", wait_for(lambda: api._window.text().endswith('"after")')), api._window.pushed[-1:])

print("a collector stuck reading one tab")
api, pump, logs, swallowed, repaints, fault = make()
pump.start()
held = threading.Event()
api.sessions["s1"].block_next = held
api.sessions["s1"].put("held text", api)
check("it is stuck with s1's text in hand", wait_for(lambda: api.sessions["s1"].block_next is None))
api.sessions["s2"].put("waits", api)
time.sleep(output_pump.STALL_S + 0.2)
old = pump._thread["collect"]
check("the watchdog replaces the collector", pump.check() == ["collect"])
check("the other tab flows again", wait_for(lambda: api.bridge.text("s2") == "waits"
                                            and "waits" in api._window.text()), api.bridge.got)
check("the stack shows the read", "in read" in "\n".join(logs))
held.set()
old.join(2)
check("the stale collector ends", not old.is_alive())
check("…handing nothing on (the redraw replaces it)", "held text" not in api.bridge.text("s1")
      and "held text" not in api._window.text())

print("a startup trust prompt read with a slow tmux call")
api, pump, logs, swallowed, repaints, fault = make()
calls, running, most = [], [0], [0]


def slow_trust(sid, s):
    running[0] += 1
    most[0] = max(most[0], running[0])
    calls.append(sid)
    time.sleep(1.0)
    running[0] -= 1


api._auto_accept_startup_trust_prompt = slow_trust
api.sessions["s1"]._startup_trust_pending = True
pump.start()
api.sessions["s1"].put("Do you trust this folder?", api)
check("the trust check started", wait_for(lambda: calls == ["s1"]))
t0 = time.time()
api.sessions["s2"].put("meanwhile", api)
api.sessions["s1"].put("more from s1", api)
check("other output is not held up by it", wait_for(lambda: "meanwhile" in api._window.text(), 0.5)
      and time.time() - t0 < 0.5)
check("s1's own output is not held up either", wait_for(lambda: "more from s1" in api._window.text(), 0.5))
time.sleep(2.4)
check("one check per tab at a time", most[0] == 1, most[0])
check("output during a check gets one more check after it (the prompt may be in it)",
      calls == ["s1", "s1"], calls)
api.sessions["s1"]._startup_trust_pending = False
api.sessions["s1"].put("trusted, working", api)
time.sleep(0.3)
check("no check once the prompt is answered", calls == ["s1", "s1"], calls)

print("a bridge that raises, a loop whose code raises")
api, pump, logs, swallowed, repaints, fault = make()
api.bridge.raise_once = True
pump.start()
api.sessions["s1"].put("lost to the error", api)
check("the error is logged", wait_for(lambda: "output_pump.feed" in swallowed), swallowed)
api.sessions["s1"].put("next", api)
check("the feed goes on", wait_for(lambda: api.bridge.text("s1") == "next"), api.bridge.got)
class BreaksOnce:
    def __init__(self, q):
        self.q, self.broken = q, False

    def put_nowait(self, item):
        self.q.put_nowait(item)

    def get(self, *a, **k):
        if not self.broken:
            self.broken = True
            raise RuntimeError("feed loop bug")
        return self.q.get(*a, **k)


api._bridge_queue = BreaksOnce(api._bridge_queue)
check("the loop's thread ends, said with its traceback",
      wait_for(lambda: any("feed loop ended on" in l and "feed loop bug" in l for l in logs)))
time.sleep(output_pump.STALL_S + 0.2)
check("the watchdog replaces it", pump.check() == ["feed"]
      and any("(its thread has ended)" in l for l in logs))
check("…without a redraw (the screen was not involved)", repaints == [])
api.sessions["s2"].put("fed again", api)
check("the bridge is fed again", wait_for(lambda: api.bridge.text("s2") == "fed again"))

print("replacements are capped")
api, pump, logs, swallowed, repaints, fault = make()
pump._beat = {name: 0.0 for name in output_pump.LOOPS}
pump._spawn = lambda name: pump._beat.__setitem__(name, 0.0)     # replacements that stay stuck
now = 1000.0
counts = [len(pump.check(now + i)) for i in range(output_pump.MAX_REPLACED + 3)]
check(f"{output_pump.MAX_REPLACED} replacements per loop an hour",
      counts == [3] * output_pump.MAX_REPLACED + [0, 0, 0], counts)
check("the cap is said once per loop", sum("not again" in l for l in logs) == 3)
check("an hour later it may replace again", len(pump.check(now + 3700)) == 3)

print("what waits for a page that takes nothing")
api, pump, logs, swallowed, repaints, fault = make()
api._window = None
pump.start()
for i in range(30):
    api.sessions["s1"].put(("line %05d " % i) * 2000 + "\r\n", api)
    time.sleep(0.01)
time.sleep(0.3)
pend = pump._pending.get("s1", "")
check("it is capped", len(pend) <= output_pump.MAX_PENDING_CHARS + 80, len(pend))
check("cut at a line break, the cut said", pend.startswith("\x1b[2m…[已略過") and "\r\nline" in pend[:120],
      repr(pend[:80]))
check("the newest text is kept", pend.rstrip().endswith("line 00029"))
check("the bridge still had all of it", len(api.bridge.text("s1")) == 30 * (len("line 00000 ") * 2000 + 2))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
