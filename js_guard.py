"""evaluate_js from a thread other than the window's own, with a time limit.

pywebview's WebView2 backend waits for a script's result on a semaphore without a
timeout, and a script whose task faults never releases it. On a Windows computer the
sfctl command loop then hung for good after a tab was closed (every sfctl call timed out
while the window itself kept working). ShellBot only pushes into the page and never reads
an answer back, so a call from any thread but the main one runs on a helper thread, and
the caller stops waiting after LIMIT_S. A call that is still stuck keeps its helper
thread; once MAX_STUCK of them are stuck, new calls are dropped (said once in the debug
log) until one comes back, instead of leaving a thread behind for every push.

Terminal output (`_pushOutput(...)`) is not guarded: a dropped push would be text missing
from the terminal, where a slow page only made it late, so it waits as it always did. The
calls dropped are the nudges (session list, bot view, status), which the page also polls.

Nor are the answers to the page's own calls. pywebview runs each `pywebview.api.<name>()` on a
thread of its own and hands the result back with evaluate_js
(`window.pywebview._returnValuesCallbacks[...]`). Dropped, that call never returns in the page,
and whatever awaits it waits for good: on a Windows computer the page's tab list stopped
following renames, new tabs and closes after one `list_sessions` answer was dropped, until the
app restarted. They wait as they did before this guard, on pywebview's thread for that call.
"""

import threading

LIMIT_S = 5.0
MAX_STUCK = 4
PASS_THROUGH = (
    "_pushOutput(",                               # terminal output: lost if dropped, so it waits as before
    "window.pywebview._returnValuesCallbacks[",   # the answer to a page call: dropped, the page waits for good
)


def install(window, log=lambda category, msg: None, limit: float = LIMIT_S, max_stuck: int = MAX_STUCK) -> bool:
    """Replace window.evaluate_js with the guarded one. False when there is nothing to
    guard, or it is guarded already."""
    orig = getattr(window, "evaluate_js", None)
    if not callable(orig) or getattr(orig, "_sf_guarded", False):
        return False
    lock = threading.Lock()
    state = {"stuck": 0, "said": False}

    def guarded(script, *args, **kwargs):
        if threading.current_thread() is threading.main_thread() or str(script).startswith(PASS_THROUGH):
            return orig(script, *args, **kwargs)
        with lock:
            if state["stuck"] >= max_stuck:
                if not state["said"]:
                    state["said"] = True
                    log("js", f"{max_stuck} calls into the page have not come back; new ones are dropped until one does")
                return None
        box, done = {}, threading.Event()

        def run():
            try:
                box["result"] = orig(script, *args, **kwargs)
            except Exception as e:
                box["error"] = e
            with lock:
                box["finished"] = True
                if box.get("late"):
                    state["stuck"] -= 1
                    state["said"] = False
            done.set()

        threading.Thread(target=run, daemon=True, name="evaluate_js").start()
        if not done.wait(limit):
            with lock:
                if not box.get("finished"):
                    box["late"] = True
                    state["stuck"] += 1
                    log("js", f"a call into the page gave no answer in {limit:g}s, not waited for: "
                              f"{str(script)[:60]!r}")
                    return None
        if "error" in box:
            raise box["error"]
        return box.get("result")

    guarded._sf_guarded = True
    window.evaluate_js = guarded
    return True
