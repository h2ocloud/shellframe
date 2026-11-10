"""A trail for when the app goes: an exit that leaves no word should not happen again.

A Windows run once vanished with nothing in the debug log, no crash log and no system
event. Ways it can go like that, and what this module writes for each:

- It runs attached to a console window (started from a terminal through shellframe.bat;
  before v0.41.2 an in-app restart did that too): closing that window, or Ctrl+C /
  Ctrl+Break in it, ends the process. The start line (win_instance.start_line) says which
  exe and whether a console is attached, and the console's control events are logged as
  they come.
- The window's loop ends (pywebview's own Ctrl+C handler, or the window closing), and
  main() goes straight to os._exit, which skips atexit: main() logs it (exiting()), and
  the window's closing / closed events are logged here.
- A native crash: faulthandler writes the Python stacks of every thread into
  shellframe_fault.log beside the debug log; the runs before keep theirs as .1 and .2.
- The page's WebView2 process failing (pywebview does not handle it; the page goes blank).
- Which call from the page was in flight: every js_api call, in and out, goes to
  shellframe_jsapi.log (rotated), the method's name and tab id only, never its arguments.

Lines go to the debug log under "lifecycle". Nothing here changes what the app does.
"""

import atexit
import faulthandler
import functools
import os
import re
import sys
import threading
import time
import traceback

import win_instance

FAULT_MAX = 8 * 1024 * 1024     # faulthandler is switched off past this (see _fault_guard)
TRAIL_MAX = 2 * 1024 * 1024     # the js_api trail rotates to .1 past this
CONSOLE_EVENTS = {0: "Ctrl+C", 1: "Ctrl+Break", 2: "its console window was closed",
                  5: "the user is logging off", 6: "Windows is shutting down"}

_log = None
_dir = None
_fault_file = None
_console_ref = None             # the ctypes callback must outlive the call that registers it
_trail_lock = threading.Lock()
_trail_n = 0


def _say(msg):
    try:
        if _log:
            _log("lifecycle", msg)
    except Exception:
        pass


def _stamp():
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


# ── start ─────────────────────────────────────────────────────────────────
def start(log, log_dir):
    """Once, first thing in main(): the start line, uncaught exceptions, console events."""
    global _log, _dir
    _log, _dir = log, str(log_dir)
    _say(start_line())
    atexit.register(lambda: _say("the interpreter is exiting normally (atexit)"))
    _hook_exceptions()
    if os.name == "nt":
        _console_handler()


def keep_faults():
    """faulthandler's file, once this is the instance that stays (after the single-instance
    guard): a second one started meanwhile must not empty the running one's, and on Windows
    a file still open in the instance before cannot be renamed."""
    if _dir:
        _faulthandler()


def start_line(environ=None, executable=None, console=None) -> str:
    """Which program this is and how it was started: what to read when it vanishes."""
    return win_instance.start_line(environ, executable, console)


def _faulthandler():
    """faulthandler into a file of its own; the last two runs' stacks kept."""
    global _fault_file
    try:
        path = os.path.join(_dir, "shellframe_fault.log")
        rotate_kept(path, keep=2)
        _fault_file = open(path, "w", encoding="utf-8")
        _fault_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}\n")
        _fault_file.flush()
        faulthandler.enable(file=_fault_file, all_threads=True)
        threading.Thread(target=_fault_guard, args=(path,), name="sf-fault-guard", daemon=True).start()
    except Exception as e:
        _say(f"faulthandler not on: {e}")


def rotate_kept(path, keep=2):
    """The file from the run before, kept when it holds more than its first line (a run
    that crashed wrote its stacks there): path -> .1 -> .2 … up to `keep`."""
    try:
        with open(path, "rb") as f:
            f.readline()
            if not f.read(1):
                return False
    except OSError:
        return False
    for i in range(keep, 0, -1):
        src = path if i == 1 else f"{path}.{i - 1}"
        if os.path.exists(src):
            try:
                os.replace(src, f"{path}.{i}")
            except OSError:
                return False
    return True


def _fault_guard(path, every=60.0, limit=None):
    """On Windows faulthandler also reports exceptions the program catches itself (a .NET
    first-chance exception is one), so a busy run could fill the disk: past FAULT_MAX it is
    switched off, and said so."""
    limit = limit or FAULT_MAX
    while True:
        time.sleep(every)
        try:
            if os.path.getsize(path) > limit:
                faulthandler.disable()
                _say(f"faulthandler switched off: {os.path.basename(path)} passed {limit // (1024 * 1024)} MB")
                return
        except OSError:
            return


def _hook_exceptions():
    prev_sys, prev_thread = sys.excepthook, threading.excepthook

    def sys_hook(etype, value, tb):
        _say("uncaught exception in the main thread: " + _brief_exc(etype, value, tb))
        prev_sys(etype, value, tb)

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            name = getattr(args.thread, "name", "?")
            _say(f"uncaught exception in thread {name}: " + _brief_exc(args.exc_type, args.exc_value, args.exc_traceback))
        prev_thread(args)

    sys.excepthook, threading.excepthook = sys_hook, thread_hook


def _brief_exc(etype, value, tb):
    frames = traceback.extract_tb(tb)[-3:] if tb else []
    where = " < ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in reversed(frames))
    return f"{getattr(etype, '__name__', etype)}: {str(value)[:200]}" + (f" at {where}" if where else "")


def console_event(ctrl_type) -> bool:
    """A console control event, logged before Windows (or Python) acts on it. False: the
    next handler and then the default still run."""
    _say(f"console event: {CONSOLE_EVENTS.get(ctrl_type, ctrl_type)} (type {ctrl_type})"
         + (" → Windows ends the app now" if ctrl_type in (1, 2, 5, 6) else ""))
    return False


def _console_handler():
    global _console_ref
    try:
        import ctypes
        from ctypes import wintypes
        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        _console_ref = handler_type(console_event)
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_ref, True):
            _say("console events not watched: SetConsoleCtrlHandler failed")
    except Exception as e:
        _say(f"console events not watched: {e}")


def exiting(reason):
    """The line before an os._exit (which skips atexit)."""
    _say(f"exiting: {reason} pid={os.getpid()}")


# ── the window ────────────────────────────────────────────────────────────
def watch_window(window):
    """Log the window's closing / closed / shown / loaded events (each handler returns
    None: a False from a closing handler would cancel the close). On Windows, the page's
    WebView2 process failing too, once the page has loaded."""
    try:
        ev = window.events
        ev.closing += lambda: _say("window closing")
        ev.closed += lambda: _say("window closed")
        ev.shown += lambda: _say("window shown")
        state = {"hooked": False}

        def loaded():
            _say("page loaded")
            if os.name == "nt" and not state["hooked"]:
                state["hooked"] = _watch_webview2(window)

        ev.loaded += loaded
    except Exception as e:
        _say(f"window events not watched: {e}")


def _watch_webview2(window) -> bool:
    """CoreWebView2.ProcessFailed → a line (pywebview leaves it unhandled: the page goes
    blank and the app keeps running). CoreWebView2 is read on the window's own thread
    only: read from the thread the loaded event runs on, it raised "CoreWebView2 can only
    be accessed from the UI thread" (measured on Windows). Nothing raised here may reach
    .NET."""
    try:
        from System import Action           # pythonnet, under pywebview's WinForms backend
        form = window.native

        def failed(_sender, args):
            try:
                _say("WebView2 process failed: kind={} reason={} exit={}".format(
                    getattr(args, "ProcessFailedKind", "?"), getattr(args, "Reason", "?"),
                    getattr(args, "ExitCode", "?")))
            except Exception:
                pass

        def add():
            try:
                form.browser.webview.CoreWebView2.ProcessFailed += failed
                _say("WebView2 failures watched")
            except Exception as e:
                _say(f"WebView2 failures not watched: {e}")

        form.Invoke(Action(add))
        return True
    except Exception as e:
        _say(f"WebView2 failures not watched: {e}")
        return False


# ── calls from the page ───────────────────────────────────────────────────
def trail_js_api(window, util=None, modules=None, path=None):
    """Every call the page makes into Python (pywebview's js_bridge_call), in and out,
    to shellframe_jsapi.log: the one in flight when the app vanished is the last line
    without its "<". Each method is wrapped the first time it is called and kept in
    window._functions, which pywebview looks in first. False when this pywebview has no
    such hook (another version): then only that is said."""
    try:
        if util is None:
            import webview.util as util
        orig = getattr(util, "js_bridge_call", None)
        if not callable(orig) or not isinstance(getattr(window, "_functions", None), dict):
            _say("js_api trail off: this pywebview has no hook for it")
            return False
        if getattr(orig, "_sf_trail", False):
            return True
        trail_path = path or os.path.join(_dir or ".", "shellframe_jsapi.log")

        def bridge(win, func_name, param, value_id):
            try:
                if (win is window and isinstance(func_name, str) and func_name.isidentifier()
                        and not func_name.startswith("pywebview") and func_name not in win._functions):
                    real = getattr(win._js_api, func_name, None)
                    if callable(real):
                        win._functions[func_name] = _traced(func_name, real, trail_path)
            except Exception:
                pass
            return orig(win, func_name, param, value_id)

        bridge._sf_trail = True
        if modules is None:
            modules = [m for n, m in list(sys.modules.items())
                       if n.startswith("webview.platforms.") and getattr(m, "js_bridge_call", None) is orig]
        for m in [util] + list(modules):
            m.js_bridge_call = bridge
        _say(f"js_api trail on: {os.path.basename(trail_path)}")
        return True
    except Exception as e:
        _say(f"js_api trail off: {e}")
        return False


_SID = re.compile(r"^s\d+$")


def _brief_args(args):
    sid = next((a for a in args if isinstance(a, str) and _SID.match(a)), "")
    return f"({sid + ', ' if sid else ''}{len(args)} arg{'' if len(args) == 1 else 's'})"


def _traced(name, func, path):
    @functools.wraps(func)
    def traced(*args):
        global _trail_n
        with _trail_lock:
            _trail_n += 1
            n = _trail_n
        t0 = time.monotonic()
        _trail(path, f"> {name}{_brief_args(args)} #{n}")
        try:
            out = func(*args)
        except BaseException as e:
            _trail(path, f"< {name} #{n} {int((time.monotonic() - t0) * 1000)}ms raised {type(e).__name__}")
            raise
        _trail(path, f"< {name} #{n} {int((time.monotonic() - t0) * 1000)}ms")
        return out
    return traced


def _trail(path, line):
    try:
        with _trail_lock:
            try:
                if os.path.getsize(path) > TRAIL_MAX:
                    os.replace(path, path + ".1")
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{_stamp()} {line}\n")
    except Exception:
        pass
