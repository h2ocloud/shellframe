"""win_instance — one ShellBot per Windows session, without blocking its own restart.

A named mutex marks the running instance. The first version (v0.37.7) let a restart
fail silently on a computer with many tabs, for two reasons:

* CreateMutexW returns a handle even when the mutex already exists, and that handle
  was never closed. A named kernel object lives while any handle to it is open, so
  after the first collision the new process itself kept the mutex alive and every
  retry saw it again. The retry could only succeed if the very first try did.
* The retry lasted 1.8 s, while the old process first closes every tab
  (restart_app → cleanup_all). Measured on a machine with 25 tabs: the new process
  gave up and the old one exited, leaving none running and no line in any log.

Now a failed try closes its handle, and a process started by a restart waits until
the old one has let go. A second launch from the shortcut still gives up after
about two seconds and brings the running window forward.

A restart is recognised by RESTART_ENV, which restart_app sets on the process it
spawns, or by the PATH this process started with: a running ShellBot puts its own
bin folder first (main.py), so a process spawned by an older version, which does not
set RESTART_ENV, still inherits that PATH.

Leaf module: no `import main`. The Windows calls sit behind a small kernel32 shim so
the logic is tested with a fake that keeps handles the way Windows does.
"""

import os
import sys
import time

RESTART_ENV = "SHELLFRAME_RESTART_FROM_PID"
MUTEX_NAME = "Local\\shellframe-single-instance"
ERROR_ALREADY_EXISTS = 183
QUICK_ATTEMPTS = 10          # a second launch: about 1.8 s, as before
QUICK_DELAY = 0.2
RESTART_WAIT_S = 90.0        # a restart: until the old process has closed its tabs
RESTART_POLL_S = 0.25
WINDOW_TITLES = ("ShellBot", "ShellFrame")   # the window's title, and its title before v0.41.0


def is_restart(path_at_start: str, app_dir, environ=None, sep: str = os.pathsep) -> bool:
    """Was this process started by a running ShellBot restarting itself?"""
    env = os.environ if environ is None else environ
    if str(env.get(RESTART_ENV) or "").strip():
        return True
    first = (path_at_start or "").split(sep, 1)[0].strip()
    if not first:
        return False
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    return norm(first) == norm(os.path.join(str(app_dir), "bin"))


def try_acquire(k32, name: str = MUTEX_NAME):
    """The mutex handle if this process now holds the name, else None.

    A handle to a mutex that already existed is closed at once: kept open, it keeps
    that mutex alive after its owner exits, and this process would never get it."""
    h = k32.create_mutex(name)
    if not h:
        return None
    if k32.last_error() == ERROR_ALREADY_EXISTS:
        k32.close(h)
        return None
    return h


def acquire(try_once, *, restart: bool, now=time.monotonic, sleep=time.sleep,
            attempts: int = QUICK_ATTEMPTS, delay: float = QUICK_DELAY,
            wait_s: float = RESTART_WAIT_S, poll_s: float = RESTART_POLL_S):
    """(handle or None, seconds waited, tries). A restart polls for up to wait_s;
    any other launch makes `attempts` tries `delay` apart."""
    start = now()
    tries = 0
    while True:
        tries += 1
        h = try_once()
        if h:
            return h, now() - start, tries
        if restart:
            if now() - start >= wait_s:
                return None, now() - start, tries
            sleep(poll_s)
        else:
            if tries >= attempts:
                return None, now() - start, tries
            sleep(delay)


class Kernel32:
    """The kernel32 calls this needs, with handle-sized types: the ctypes default
    return type (c_int) would cut a 64-bit HANDLE."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._k = k

    def create_mutex(self, name):
        return self._k.CreateMutexW(None, False, name)

    def last_error(self):
        # use_last_error + get_last_error: windll.GetLastError() is documented as
        # unreliable (ctypes' own calls can overwrite it).
        return self._ctypes.get_last_error()

    def close(self, h):
        self._k.CloseHandle(h)


def bring_forward(titles=WINDOW_TITLES) -> str:
    """Restore and focus the running instance's window; the title found, or ''."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        for title in titles:
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return title
    except Exception:
        pass
    return ""


def ensure_single_instance(*, app_dir, path_at_start: str, log, k32=None,
                           now=time.monotonic, sleep=time.sleep,
                           bring=bring_forward, exit=os._exit):
    """Hold the mutex for the life of this process (keep the returned handle), or
    exit. If the Windows calls themselves fail, start unguarded rather than not at all."""
    restart = is_restart(path_at_start, app_dir)
    os.environ.pop(RESTART_ENV, None)          # not for the tabs this process opens
    how = "restart" if restart else "launch"
    try:
        k32 = k32 or Kernel32()
        h, waited, tries = acquire(lambda: try_acquire(k32), restart=restart, now=now, sleep=sleep)
    except Exception as e:
        log("instance", f"no single-instance guard ({e}); starting unguarded")
        return None
    if h:
        if tries > 1:
            log("instance", f"got the single-instance mutex after {waited:.1f}s and {tries} tries ({how})")
        return h
    shown = bring()
    log("instance", f"another instance still holds the mutex after {waited:.1f}s and {tries} tries "
                    f"({how}); exiting" + (f", brought '{shown}' forward" if shown else ""))
    print("[shellbot] another instance is running — bringing it forward and exiting this one",
          file=sys.stderr)
    exit(0)
    return None
