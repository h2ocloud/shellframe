#!/usr/bin/env python3
"""Telegram's command menu is sent off the thread that changed the tabs (#51).

Measured on a computer where Telegram was slow: `sfctl new` and `sfctl rename` each made the app
register the bot's command menu on the thread that runs every sfctl command, up to three Telegram
calls of 35 s each. The command timed out after its work was done, and every other sfctl, Telegram
and phone command waited behind it. `close` and `bots-keep` did the same.

  - bridge_menu: request() returns at once while a send is stuck; requests that come meanwhile are
    one more send that sees the newest tabs; a send that raises does not end the sender; a stopped
    bridge sends nothing; a send that never returns is replaced by a new one
  - the commands themselves, against the app's own dispatcher with a real Telegram bridge and a
    stand-in Telegram that answers only after 5 s (or when the test lets it): new_session, rename,
    bots_keep and close_session each come back in well under a second, and what is sent in the
    end is the menu of the tabs as they are then, in one more send, not one per command

Demo tabs and names. Run: .venv/bin/python tests_bridge_menu.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-menu-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"             # no look-up of the system's language (a slow first call)
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt                    # main's own import; the real bridge is loaded below
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")   # fake tabs stay out of the real log

import bridge_menu  # noqa: E402
import main  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ── 1. the sender ────────────────────────────────────────────────────────────
class FakeBridge:
    """What the sender needs of a bridge: `active` and `_set_bot_commands`, which reads the tabs when it runs."""

    def __init__(self):
        self.active = True
        self.tabs = 0
        self.sends = []                       # the tabs each send read
        self.block = False
        self.gate = threading.Event()
        self.fail_next = False

    def _set_bot_commands(self):
        self.sends.append(self.tabs)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("telegram said no")
        if self.block:
            self.gate.wait(5)


print("the sender")
b = FakeBridge()
b.block = True
t0 = time.time()
bridge_menu.request(b)
check("request() returns at once while the send is stuck", time.time() - t0 < 1.0, time.time() - t0)
check("…the send has started", wait_for(lambda: b.sends == [0]), b.sends)       # read the tabs as they were
for n in (2, 3, 4):
    b.tabs = n
    bridge_menu.request(b)
check("requests during the send do not start sends of their own", b.sends == [0], b.sends)
b.gate.set()
check("…they become one more send", bridge_menu.wait_idle(b, 5) and len(b.sends) == 2, b.sends)
check("…which reads the tabs as they are by then, not as they were at the first request", b.sends[-1] == 4, b.sends)

b = FakeBridge()
b.fail_next = True
bridge_menu.request(b)
check("a send that raises ends only itself", bridge_menu.wait_idle(b, 5) and b.sends == [0], b.sends)
b.tabs = 7
bridge_menu.request(b)
check("…the next request is sent", bridge_menu.wait_idle(b, 5) and b.sends == [0, 7], b.sends)

b = FakeBridge()
b.active = False
bridge_menu.request(b)
check("a stopped bridge sends nothing", bridge_menu.wait_idle(b, 1) and b.sends == [], b.sends)
b = FakeBridge()
b.block = True
bridge_menu.request(b)
wait_for(lambda: b.sends == [0])
bridge_menu.request(b)
b.active = False                                        # the bridge stops while the send is stuck
b.gate.set()
check("…nor does one that stops while a request waits", bridge_menu.wait_idle(b, 5) and b.sends == [0], b.sends)

real_stuck = bridge_menu.STUCK_SEC
bridge_menu.STUCK_SEC = 0.3
b = FakeBridge()
b.block = True
bridge_menu.request(b)
wait_for(lambda: b.sends == [0])
time.sleep(0.5)                                         # the first send never comes back
b.block, b.tabs = False, 9
bridge_menu.request(b)
check("a send stuck for good does not stop the menu from being kept", wait_for(lambda: b.sends == [0, 9]), b.sends)
b.gate.set()                                            # …and when it does come back it ends without sending again
time.sleep(0.3)
check("…the old thread leaves the work to the new one", bridge_menu.wait_idle(b, 5) and b.sends == [0, 9], b.sends)
bridge_menu.STUCK_SEC = real_stuck

# ── 2. the commands, with a real Telegram bridge and a Telegram that does not answer ─────────
spec = importlib.util.spec_from_file_location("bt_real", HERE / "bridge_telegram.py")
bt_real = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt_real)
bt_real._blog = lambda msg: None

STUB = {"gate": threading.Event(), "slow": True}        # the stand-in Telegram answers when the gate opens
CALLS = []                                              # (method, payload) in the order they were made


def fake_tg(token, method, payload=None, **kw):
    """A Telegram that does not answer: 5 s for whoever asks on the test's own thread (where a command
    runs, so a command that waits for it shows), as long as the test wants for the menu's own thread."""
    CALLS.append((method, payload))
    if STUB["slow"] and method == "setMyCommands":
        STUB["gate"].wait(5 if threading.current_thread() is threading.main_thread() else 30)
    return {"ok": True}


bt_real.tg_api = fake_tg


def make_bridge():
    br = object.__new__(bt_real.TelegramBridge)
    br.slots, br._slot_order = {}, []
    br._slots_lock = threading.Lock()
    br._user_active, br._user_chat, br._default_active_sid = {}, {111: 1110}, ""
    br._last_prune_ts = time.time() + 3600              # do not go and ask the app for its tabs
    br.paused, br.active, br.bot_info = False, True, {}
    br.config = types.SimpleNamespace(bot_token="TEST", allowed_users=[111])
    return br


class FakeTab:
    """What Session gives the app, without a process behind it."""

    def __init__(self, sid, cmd, cols, rows, on_data=None, **kw):
        self.sid, self.cmd, self.cols, self.rows = sid, cmd, cols, rows
        self.alive, self.lock = True, threading.Lock()
        self._recent, self._tmux_name = bytearray(), None
        self._custom_label = None
        self._slug_pending = True
        self._startup_trust_pending = False
        self._init_pending = False

    def write(self, text):
        pass

    def kill(self, kill_tmux=True):
        self.alive = False


main.Session = FakeTab
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_bots": True}}), encoding="utf-8")
api = Api()
api.sessions = {}
api.line_bridge = None
api._window = None
api._persist_session_manifest = lambda *a, **k: None
api._start_startup_trust_watcher = lambda *a, **k: None
api._prepare_pane_for_input = lambda *a, **k: None
api.bridge = br = make_bridge()


def timed(cmd, args):
    t0 = time.time()
    res = api._execute_sfctl(cmd, args)
    return res, time.time() - t0


def global_menus():
    """The menus sent to every chat (not the per-person copies), each as {command: description}."""
    return [{c["command"]: c["description"] for c in p["commands"]}
            for m, p in CALLS if m == "setMyCommands" and "scope" not in p]


print("sfctl new, rename, bots-keep and close while Telegram does not answer")
res, dt = timed("new_session", {"cmd": "claude"})
sid1 = (res.get("details") or {}).get("sid", "")
check("sfctl new comes back at once", res.get("success") and dt < 1.0, (res, dt))
check("…the tab is in the bridge", sid1 in br.slots and br.slots[sid1].label == "claude", list(br.slots))
check("…and Telegram has been asked (the first call is stuck)", wait_for(lambda: any(m == "setMyCommands" for m, _ in CALLS)))
res, dt = timed("rename", {"sid": sid1, "name": "research-1"})
check("sfctl rename comes back at once", res.get("success") and dt < 1.0, (res, dt))
res, dt = timed("new_session", {"cmd": "codex"})
sid2 = (res.get("details") or {}).get("sid", "")
check("a second sfctl new does too", res.get("success") and dt < 1.0, (res, dt))
res, dt = timed("bots_keep", {"sid": sid2, "name": "Scribe"})
check("sfctl bots-keep comes back at once", res.get("success") and dt < 1.0, (res, dt))
check("…the bridge has the bot's name for the tab", br.slots[sid2].label == "Scribe", br.slots[sid2].label)
res, dt = timed("new_session", {"cmd": "zsh"})
sid3 = (res.get("details") or {}).get("sid", "")
res, dt = timed("close_session", {"sid": sid3})
check("sfctl close comes back at once", res.get("success") and dt < 1.0, (res, dt))
check("…the tab is gone from the bridge", sid3 not in br.slots, list(br.slots))
check("Telegram has been asked once so far: the other changes wait for it", len(global_menus()) == 1,
      f"{len(global_menus())} menus sent")

STUB["gate"].set()                                      # Telegram answers
check("the menu is sent in the end", bridge_menu.wait_idle(br, 10))
menus = global_menus()
check("…in one more send, however many commands came meanwhile (5 changes, 2 sends)", len(menus) == 2,
      f"{len(menus)} menus sent")
last = menus[-1]
check("the last menu is the tabs as they are: the renamed one first, then the bot",
      last.get("1") == "Switch to research-1" and last.get("2") == "Switch to Scribe" and "3" not in last,
      {k: v for k, v in last.items() if k.isdigit()})
check("…and the same menu went to the allowed person", (lambda m: m and m[-1] == last)(
    [{c["command"]: c["description"] for c in p["commands"]} for mth, p in CALLS
     if mth == "setMyCommands" and (p.get("scope") or {}).get("chat_id") == 111]), CALLS[-4:])

print("the menu follows a rename when Telegram is quick")
STUB["slow"] = False
CALLS.clear()
res, dt = timed("rename", {"sid": sid1, "name": "research-2"})
check("sfctl rename comes back at once", res.get("success") and dt < 1.0, (res, dt))
check("…and the menu has the new name", wait_for(lambda: global_menus() and global_menus()[-1].get("1") == "Switch to research-2"),
      global_menus())

print("the way it is built")
import _testsrc  # noqa: E402
check("no Api code sends the menu itself: it asks the bridge, which asks bridge_menu",
      "_set_bot_commands" not in _testsrc.app_source())
tg_src = (HERE / "bridge_telegram.py").read_text(encoding="utf-8")
body = tg_src.split("    def refresh_commands(self):", 1)[1].split("\n    # ──", 1)[0]
check("TelegramBridge.refresh_commands only asks bridge_menu to send",
      "bridge_menu.request(self)" in body and "_set_bot_commands" not in body and "tg_api" not in body, body)

print(f"\n{passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
