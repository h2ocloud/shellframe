#!/usr/bin/env python3
"""Two starts of the Telegram bridge at once leave one poller, not two.

At launch the autostart thread (api_bridges._autostart_bridge) and the page's restore
(restoreSavedBridge) both start the bridge. On a Windows cold start the autostart took 25 s,
and the page's start came in while TelegramBridge.start() was still running: it stopped a
bridge that had not started yet and built its own. The first one then cleared its stop flag
and polled on with nobody to stop it, and every poll got 409 Conflict until the app quit.

A stand-in bridge here starts slowly (the real one calls getMe and setMyCommands first) and, like
the real one, polls once its start ends even if it was stopped meanwhile. A test holds the start open
on a gate it releases itself, and waits for "the start is under way" instead of sleeping and hoping
the thread got that far: on a slow shared CPU a fixed sleep let the second call win the race.
  - the autostart and the page's restore at once: one bridge polls, the one api.bridge names,
    and the page keeps it (no second bridge, no restart)
  - two plain starts at once (two reconnects): the second replaces the first; one polls
  - the page's restore once the bridge runs: kept
  - a reload or a stop waits for a start that is under way
Run: .venv/bin/python tests_bridge_start_race.py
"""
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

import main as _main  # noqa: E402
_main._dlog = lambda *a, **k: None

START_S = 0.05              # a start nobody holds open
SETTLE_S = 0.3              # lets a call that must block reach the lock before the start is released
TOKEN = "123456:demo-token-not-real"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeBridge:
    made = []                   # every bridge built, in order
    gate = None                 # set: start() holds here until the test sets it
    entered = threading.Event()  # a start() is under way

    def __init__(self, bridge_id, config, **callbacks):
        self.bridge_id, self.config, self.slots = bridge_id, config, {}
        self.active = self.connected = self.polling = False
        self.starts = 0
        FakeBridge.made.append(self)

    def register_session(self, sid, label, write_fn, **kw):
        self.slots[sid] = label

    def refresh_commands(self):
        pass

    def start(self):
        self.starts += 1
        if FakeBridge.gate is not None:
            FakeBridge.entered.set()
            FakeBridge.gate.wait(30)        # getMe, setMyCommands: the network, as long as the test wants
        else:
            time.sleep(START_S)
        self.active = self.connected = True
        self.polling = True                 # TelegramBridge.start clears the stop flag here

    def stop(self):
        self.active = self.connected = self.polling = False

    def get_status(self):
        return {"bridge_id": self.bridge_id, "active": self.active, "sessions": len(self.slots)}


_main.TelegramBridge = FakeBridge
_main.TelegramBridgeConfig = lambda **kw: SimpleNamespace(**kw)
_main.load_config = lambda: {"bridge": {"bot_token": TOKEN, "allowed_users": [], "prefix_enabled": True}}
_main.save_config = lambda cfg: None


def fresh_api(held=False):
    FakeBridge.made = []
    FakeBridge.entered = threading.Event()
    FakeBridge.gate = threading.Event() if held else None
    api = object.__new__(_main.Api)
    api.sessions, api.bridge, api.line_bridge = {}, None, None
    api._autoinstall_ai_skill = lambda: None
    api._persist_session_manifest = lambda: None
    api._prepare_pane_for_input = lambda s: None
    return api


def page_restore(api):
    """restoreSavedBridge's call, with the arguments the page passes."""
    return json.loads(api.start_bridge(TOKEN, "[]", True, "", True, True))


def polling():
    return [b for b in FakeBridge.made if b.polling]


def under_way(api_start, *args):
    """Start a call that stays inside bridge.start() until released; return its thread."""
    t = threading.Thread(target=api_start, args=args)
    t.start()
    assert FakeBridge.entered.wait(30), "the start never began"
    return t


print("1. the autostart and the page's restore at once (a cold start)")
api = fresh_api(held=True)
auto = under_way(api._autostart_bridge)     # the autostart is inside TelegramBridge.start()
box = {}
page_t = threading.Thread(target=lambda: box.update(page=page_restore(api)))
page_t.start()
time.sleep(SETTLE_S)                        # the page's call is at the lock
FakeBridge.gate.set()
auto.join(30)
page_t.join(30)
page = box.get("page", {})
check("one bridge polls", len(polling()) == 1, [(b.starts, b.polling) for b in FakeBridge.made])
check("…the one api.bridge names: no poller that nothing can stop", polling() == [api.bridge])
check("…the page waited and kept it: one bridge built, started once", len(FakeBridge.made) == 1
      and api.bridge.starts == 1 and page.get("success") is True and page.get("kept") is True, page)

print("2. two plain starts at once (two reconnects)")
api = fresh_api(held=True)
first = under_way(api.start_bridge, TOKEN, "[]", True, "")
second = threading.Thread(target=api.start_bridge, args=(TOKEN, "[]", True, ""))
second.start()
time.sleep(SETTLE_S)
FakeBridge.gate.set()
first.join(30)
second.join(30)
check("the second replaces the first: one polls, the one api.bridge names",
      len(polling()) == 1 and polling() == [api.bridge], [(b.starts, b.polling) for b in FakeBridge.made])

print("3. the page's restore once the bridge runs")
api = fresh_api()
api._autostart_bridge()
page = page_restore(api)
check("kept: still one bridge, started once", len(FakeBridge.made) == 1 and api.bridge.starts == 1
      and page.get("kept") is True and len(polling()) == 1, page)

print("4. a reload or a stop waits for a start that is under way")
for name, call in (("a reload", lambda a: a.hot_reload_bridge()), ("a stop", lambda a: a.stop_bridge())):
    api = fresh_api(held=True)
    api._hot_reload_bridge_locked = lambda: "reloaded"
    done = threading.Event()
    starting = under_way(api.start_bridge, TOKEN, "[]", True, "")
    threading.Thread(target=lambda: (call(api), done.set())).start()
    early = done.wait(SETTLE_S * 2)         # held open: it must still be waiting
    FakeBridge.gate.set()
    starting.join(30)
    check(f"{name} waits for it", not early and done.wait(30))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
