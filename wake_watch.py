"""macOS display sleep/wake notifications -> callbacks on a worker thread.

中文摘要：螢幕睡醒之後 ShellFrame 原本什麼都不做，WebView 能不能自己重畫全看
運氣；記憶體吃緊時畫面常常就停在空白。這裡訂 NSWorkspace 的螢幕睡／醒通知，
醒來就叫後端強制重畫。回呼一律丟到背景執行緒：通知在主執行緒送達，而
evaluate_js 要等主執行緒，在主執行緒上呼叫等於自己卡死自己。
"""
import subprocess
import sys
import threading

from sf_log import _dlog, _swallow

# NSNotificationCenter does not retain its observers; keep ours alive here.
_observer = None
_callbacks = {"wake": None, "sleep": None}

_NOTIFICATIONS = (
    ("screensDidWake:", "NSWorkspaceScreensDidWakeNotification"),
    ("systemDidWake:", "NSWorkspaceDidWakeNotification"),
    ("screensDidSleep:", "NSWorkspaceScreensDidSleepNotification"),
)


def memory_pressure() -> str:
    """Kernel memory pressure level as a word (normal / warn / critical)."""
    try:
        out = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return "?"
    return {"1": "normal", "2": "warn", "4": "critical"}.get(out, out or "?")


def _spawn(kind: str, reason: str):
    fn = _callbacks.get(kind)
    if fn:
        threading.Thread(target=fn, args=(reason,), daemon=True,
                         name=f"display-{kind}-{reason}").start()


def install(on_wake, on_sleep=None) -> bool:
    """Subscribe once per process. Returns True when the observer is active."""
    global _observer
    _callbacks["wake"], _callbacks["sleep"] = on_wake, on_sleep
    if _observer is not None:
        return True
    if sys.platform != "darwin":
        return False
    try:
        import AppKit
        import objc
        from Foundation import NSObject
    except Exception:
        _swallow("wake_watch.install:import")
        return False
    try:
        try:
            cls = objc.lookUpClass("SFDisplayWakeObserver")
        except objc.nosuchclass_error:
            class SFDisplayWakeObserver(NSObject):
                def screensDidWake_(self, _note):
                    _spawn("wake", "screens")

                def systemDidWake_(self, _note):
                    _spawn("wake", "system")

                def screensDidSleep_(self, _note):
                    _spawn("sleep", "screens")
            cls = SFDisplayWakeObserver
        center = AppKit.NSWorkspace.sharedWorkspace().notificationCenter()
        obs = cls.alloc().init()
        for selector, name in _NOTIFICATIONS:
            center.addObserver_selector_name_object_(obs, selector, getattr(AppKit, name, name), None)
        _observer = obs
        _dlog("wake", "display sleep/wake observer installed")
        return True
    except Exception:
        _swallow("wake_watch.install")
        return False
