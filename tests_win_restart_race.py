#!/usr/bin/env python3
"""Windows self-restart is not blocked by its own single-instance guard (win_instance).

v0.37.7 retried the mutex for 1.8 s, and its test passed, but a restart on a machine
with 25 tabs still left nothing running. The test's fake said "free" on the fourth
try; real Windows never would: CreateMutexW hands back a handle to the existing mutex,
the guard never closed it, and a named kernel object lives while any handle to it is
open. After the first collision the new process kept the mutex alive by itself.

The fake kernel32 here keeps handles the way Windows does, so that bug is reproducible
and the fix is tested against the same rule.

Run: .venv/bin/python tests_win_restart_race.py
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import win_instance as W  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeKernel:
    """Named mutexes as Windows keeps them: the object exists while any handle is open."""

    def __init__(self):
        self.open = {}          # name -> set of open handles
        self.owner = {}         # handle -> name
        self.next = 100
        self.err = 0

    def create_mutex(self, name):
        existed = bool(self.open.get(name))
        h = self.next
        self.next += 4
        self.open.setdefault(name, set()).add(h)
        self.owner[h] = name
        self.err = W.ERROR_ALREADY_EXISTS if existed else 0
        return h

    def last_error(self):
        return self.err

    def close(self, h):
        name = self.owner.pop(h)
        self.open[name].discard(h)

    def handles(self, name=W.MUTEX_NAME):
        return len(self.open.get(name, ()))


class Clock:
    def __init__(self):
        self.t = 0.0
        self.events = []        # (time, fn): run once the clock passes time

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        for at, fn in list(self.events):
            if self.t >= at:
                fn()
                self.events.remove((at, fn))


# 1. The v0.37.7 guard against this model: a holder that leaves after 1 s, and it never gets in.
k = FakeKernel()
old_instance = k.create_mutex(W.MUTEX_NAME)
clock = Clock()
clock.events.append((1.0, lambda: k.close(old_instance)))


def v0377_try():                       # what main.py did: keep the handle whatever happened
    h = k.create_mutex(W.MUTEX_NAME)
    return h if k.last_error() != W.ERROR_ALREADY_EXISTS else None


h, waited, tries = W.acquire(v0377_try, restart=False, now=clock.now, sleep=clock.sleep)
check("the v0.37.7 guard never recovers after a collision, even once the holder is gone (the bug)",
      h is None and k.handles() >= tries, f"h={h} tries={tries} open handles={k.handles()}")

# 2. Closing the handle of a failed try: gets the mutex once the holder leaves.
k = FakeKernel()
old_instance = k.create_mutex(W.MUTEX_NAME)
clock = Clock()
clock.events.append((1.0, lambda: k.close(old_instance)))
h, waited, tries = W.acquire(lambda: W.try_acquire(k), restart=False, now=clock.now, sleep=clock.sleep)
check("a failed try closes its handle, so the mutex is free once the holder leaves",
      h is not None and k.handles() == 1, f"h={h} handles={k.handles()}")
check("…after about the holder's exit time", 0.9 <= waited <= 1.3, f"waited={waited}")

# 3. A restart whose old process needs 40 s to close its tabs: the new one waits it out.
k = FakeKernel()
old_instance = k.create_mutex(W.MUTEX_NAME)
clock = Clock()
clock.events.append((40.0, lambda: k.close(old_instance)))
h, waited, tries = W.acquire(lambda: W.try_acquire(k), restart=True, now=clock.now, sleep=clock.sleep)
check("a restart waits for the old process however long its tabs take (40 s)",
      h is not None and 40.0 <= waited <= 40.5, f"h={h} waited={waited}")
check("no handles left behind while waiting", k.handles() == 1, f"handles={k.handles()}")

# 4. A second launch while the app runs: gives up after about two seconds, as before.
k = FakeKernel()
k.create_mutex(W.MUTEX_NAME)
clock = Clock()
h, waited, tries = W.acquire(lambda: W.try_acquire(k), restart=False, now=clock.now, sleep=clock.sleep)
check("a second launch gives up after its ten tries (~1.8 s)",
      h is None and tries == W.QUICK_ATTEMPTS and waited < 2.5, f"tries={tries} waited={waited}")
check("…and leaves only the running instance's handle", k.handles() == 1, f"handles={k.handles()}")

# 5. A restart whose old process never exits: stops waiting at the limit.
k = FakeKernel()
k.create_mutex(W.MUTEX_NAME)
clock = Clock()
h, waited, tries = W.acquire(lambda: W.try_acquire(k), restart=True, now=clock.now, sleep=clock.sleep)
check("a restart gives up at the wait limit when the old one never leaves",
      h is None and W.RESTART_WAIT_S <= waited <= W.RESTART_WAIT_S + 1, f"waited={waited}")
check("the wait limit leaves room for a slow shutdown of many tabs (>= 60 s)", W.RESTART_WAIT_S >= 60)

# 6. Telling a restart from a launch.
app = os.path.join(os.sep, "apps", "shellframe")
sep = os.pathsep
spawned = sep.join([os.path.join(app, "bin"), "/opt/homebrew/bin", "/usr/bin"])
check("RESTART_ENV marks a restart", W.is_restart("/usr/bin", app, {W.RESTART_ENV: "4242"}))
check("an inherited PATH with ShellBot's bin folder first marks a restart (older versions)",
      W.is_restart(spawned, app, {}))
check("an ordinary PATH is a launch", not W.is_restart(sep.join(["/usr/bin", "/bin"]), app, {}))
check("ShellBot's bin folder further down the PATH is not enough",
      not W.is_restart(sep.join(["/usr/bin", os.path.join(app, "bin")]), app, {}))
check("an empty PATH is a launch", not W.is_restart("", app, {}))

# 7. The whole guard: restart → waits, logs, keeps the handle, and the flag does not reach tabs.
logs = []
k = FakeKernel()
old_instance = k.create_mutex(W.MUTEX_NAME)
clock = Clock()
clock.events.append((12.0, lambda: k.close(old_instance)))
os.environ[W.RESTART_ENV] = "4242"
exits = []
h = W.ensure_single_instance(app_dir=app, path_at_start="/usr/bin", log=lambda c, m: logs.append(m),
                             k32=k, now=clock.now, sleep=clock.sleep,
                             bring=lambda: "ShellBot", exit=exits.append)
check("restart: the guard returns the handle once the old instance is gone", h is not None and not exits)
check("restart: the flag is removed so tabs do not inherit it", W.RESTART_ENV not in os.environ)
check("restart: the log says how long it waited",
      any(re.search(r"after 12\.\ds .*\(restart\)", m) for m in logs), str(logs))

logs, exits = [], []
k = FakeKernel()
k.create_mutex(W.MUTEX_NAME)
clock = Clock()
brought = []
W.ensure_single_instance(app_dir=app, path_at_start="/usr/bin", log=lambda c, m: logs.append(m),
                         k32=k, now=clock.now, sleep=clock.sleep,
                         bring=lambda: brought.append(1) or "ShellBot", exit=exits.append)
check("second launch: brings the running window forward and exits", brought and exits == [0], f"{exits}")
check("second launch: the log says so, instead of nothing at all",
      any("exiting" in m and "(launch)" in m and "ShellBot" in m for m in logs), str(logs))


class Broken:
    def create_mutex(self, name):
        raise OSError("no kernel32")


logs, exits = [], []
h = W.ensure_single_instance(app_dir=app, path_at_start="", log=lambda c, m: logs.append(m),
                             k32=Broken(), exit=exits.append)
check("if the Windows calls fail, start unguarded rather than not at all",
      h is None and not exits and any("unguarded" in m for m in logs), str(logs))

# 8. Where the pieces live.
here = Path(__file__).parent
main_src = (here / "main.py").read_text(encoding="utf-8")
cap = main_src.find('_PATH_AT_START = os.environ.get("PATH"')
rewrite = main_src.find('os.environ["PATH"] = _session_env()["PATH"]')
check("main.py reads the starting PATH before it rewrites it", 0 <= cap < rewrite, f"{cap} {rewrite}")
check("the bring-forward looks for today's window title and the old one",
      W.WINDOW_TITLES[0] == "ShellBot" and "ShellFrame" in W.WINDOW_TITLES)
upd = (here / "api_update.py").read_text(encoding="utf-8")
win = upd[upd.find("if main.IS_WIN:"):upd.find("if not spawned:\n                return")]
check("both Windows relaunch paths pass the restart flag", win.count("env=restart_env") == 2,
      str(win.count("env=restart_env")))
check("the old instance logs how long closing its tabs took", "closed {n} tab(s) in" in upd)
import ast  # noqa: E402
_imports = {a.name.split(".")[0] for n in ast.walk(ast.parse((here / "win_instance.py").read_text(encoding="utf-8")))
            if isinstance(n, ast.Import) for a in n.names}
_imports |= {(n.module or "").split(".")[0] for n in ast.walk(ast.parse((here / "win_instance.py").read_text(encoding="utf-8")))
             if isinstance(n, ast.ImportFrom)}
check("win_instance stays a leaf module (no main, no api_host)", not _imports & {"main", "api_host"}, str(_imports))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
