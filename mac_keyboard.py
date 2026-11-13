"""The page's keyboard on macOS: who holds it, and handing it back to the web view.

Seen on a release candidate: mouse moves, a click and the wheel reached a tab's terminal, not one key,
while other apps took keys fine. ShellBot's window was the key window, but keys reach the page only
when the window's first responder is the WKWebView (or a view inside it); JavaScript's focus() cannot
change that. When the page finds itself without focus right after a click in a terminal
(document.hasFocus() false), check_and_take() logs whether the app is active, whether its window is
key and which class holds the first responder, then makes the web view the first responder.

Leaf module: AppKit through pyobjc, on the main thread; nothing here imports main.
"""
import sys
import time

from sf_log import _dlog, _swallow

LOG_EVERY_S = 60.0
_last_log = [0.0]


def _webview_in(view, depth: int = 0):
    """The first WKWebView under `view`, depth first; None when there is none."""
    if view is None or depth > 12:
        return None
    try:
        if "WKWebView" in str(view.className()):
            return view
        for sub in (view.subviews() or []):
            found = _webview_in(sub, depth + 1)
            if found is not None:
                return found
    except Exception:
        _swallow("mac_keyboard._webview_in")
    return None


def _inside(responder, webview) -> bool:
    """True when the responder is the web view or a view inside it (its content views hold keys too)."""
    if responder is None or webview is None:
        return False
    if responder is webview:
        return True
    try:
        return bool(hasattr(responder, "isDescendantOf_") and responder.isDescendantOf_(webview))
    except Exception:
        return False


def check_and_take(reason: str = "", take: bool = True) -> None:
    """Queue on the main thread: log who holds the keyboard (at most once a minute) and, when `take`, make
    the app window's web view the first responder unless it (or a view inside it) already is."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApp, NSOperationQueue
    except Exception:
        return

    def _do():
        try:
            if NSApp is None:
                return
            wins = [w for w in (NSApp.windows() or []) if w.isVisible()]
            win = NSApp.keyWindow() or NSApp.mainWindow() or (wins[0] if wins else None)
            if win is None:
                return
            responder = win.firstResponder()
            webview = _webview_in(win.contentView())
            took = False
            if take and webview is not None and not _inside(responder, webview):
                took = bool(win.makeFirstResponder_(webview))
            now = time.time()
            if now - _last_log[0] >= LOG_EVERY_S or took:
                _last_log[0] = now
                _dlog("keyboard", f"{reason}: app_active={bool(NSApp.isActive())} key_window={bool(win.isKeyWindow())} "
                                  f"first_responder={responder.className() if responder is not None else None} "
                                  f"webview={'yes' if webview is not None else 'no'} took={took}")
        except Exception:
            _swallow("mac_keyboard.check_and_take")
    try:
        NSOperationQueue.mainQueue().addOperationWithBlock_(_do)
    except Exception:
        _swallow("mac_keyboard.queue")
