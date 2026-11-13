#!/usr/bin/env python3
"""Handing the keyboard back to the web view on macOS (mac_keyboard), against stand-in AppKit objects.

Seen on a release candidate: a tab got mouse events and no keys while other apps took keys: the window
was key, but its first responder was not the WKWebView. When the page finds itself without focus right
after a click in a terminal, check_and_take logs who holds the keyboard and makes the web view the
first responder.
  - a first responder outside the web view: the web view is made first responder, and it is logged
  - one inside the web view (or the web view itself): left alone
Run: .venv/bin/python tests_mac_keyboard.py
"""
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")
import mac_keyboard as mk  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    print(("  [PASS] " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))
    passed, failed = passed + bool(ok), failed + (not ok)


class View:
    def __init__(self, cls, subs=(), parent=None):
        self.cls, self.subs, self.parent = cls, list(subs), parent
        for v in self.subs:
            v.parent = self

    def className(self):
        return self.cls

    def subviews(self):
        return self.subs

    def isDescendantOf_(self, other):
        p = self
        while p is not None:
            if p is other:
                return True
            p = p.parent
        return False


class Window:
    def __init__(self, content, responder):
        self.content, self.responder, self.made = content, responder, []

    def isVisible(self):
        return True

    def isKeyWindow(self):
        return True

    def contentView(self):
        return self.content

    def firstResponder(self):
        return self.responder

    def makeFirstResponder_(self, v):
        self.made.append(v)
        self.responder = v
        return True


def appkit(win):
    app = types.SimpleNamespace(windows=lambda: [win], keyWindow=lambda: win, mainWindow=lambda: win, isActive=lambda: True)
    queue = types.SimpleNamespace(addOperationWithBlock_=lambda block: block())
    return types.SimpleNamespace(NSApp=app, NSOperationQueue=types.SimpleNamespace(mainQueue=lambda: queue))


mk.sys = types.SimpleNamespace(platform="darwin")
logged = []
mk._dlog = lambda tag, text: logged.append(text)

inner = View("WKContentView")
web = View("WKWebView", [inner])
other = View("NSTextView")
content = View("NSView", [View("NSView", [web]), other])
win = Window(content, other)
sys.modules["AppKit"] = appkit(win)
mk._last_log[0] = 0.0
mk.check_and_take("click")
check("a first responder outside the web view: the web view is made first responder", win.made == [web])
check("…and the log says who held the keyboard", logged and "first_responder=NSTextView" in logged[-1]
      and "key_window=True" in logged[-1] and "took=True" in logged[-1], logged)

win2 = Window(content, inner)
sys.modules["AppKit"] = appkit(win2)
mk.check_and_take("click")
check("one inside the web view: left alone", win2.made == [])
win3 = Window(content, web)
sys.modules["AppKit"] = appkit(win3)
mk.check_and_take("click")
check("the web view itself: left alone", win3.made == [])

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
