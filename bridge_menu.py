"""Telegram's command menu follows the tabs (/1, /2 … one per tab), and is sent off the thread that
changed them.

Registering it is up to three Telegram calls (the menu for every chat, one for each allowed person,
the menu button), each allowed 35 s. They ran on whichever thread had just opened, renamed,
reordered or closed a tab. For `sfctl new`, `rename`, `close` and `bots-keep` that is the thread that
runs every sfctl command: with a slow or unreachable Telegram the command timed out after its work
was done (measured: 3 × 35 s), and every other sfctl, Telegram and phone command waited behind it.

`request(bridge)` returns at once. One thread per bridge sends the menu. It reads the tabs when it
sends, so what goes out is the newest, and requests that come while it is sending are one more send,
not one each: a burst of renames ends with the last menu. A bridge that has stopped sends nothing.

Leaf module: stdlib and sf_log, no `import main`.
"""

import threading
import time

import sf_log

STUCK_SEC = 600.0        # a send this long is given up on; the next request starts another thread

_ATTR = "_menu_sync"
_LOCK = threading.Lock()


class MenuSync:
    """One bridge's menu sender: a flag that says "send again" and the thread that works it off."""

    def __init__(self, bridge):
        self._bridge = bridge
        self._lock = threading.Lock()
        self._again = False            # a request came that no send has started for yet
        self._busy = False             # a thread is sending, or is about to look for more
        self._gen = 0                  # which thread owns the work; a stuck one is replaced by a newer
        self._since = 0.0
        self._idle = threading.Event()
        self._idle.set()

    def request(self):
        with self._lock:
            self._again = True
            if self._busy and time.monotonic() - self._since < STUCK_SEC:
                return                                  # the thread at work comes back for it
            self._busy, self._gen = True, self._gen + 1
            self._since, gen = time.monotonic(), self._gen
            self._idle.clear()
        threading.Thread(target=self._run, args=(gen,), daemon=True, name="tg-menu").start()

    def _run(self, gen):
        while True:
            with self._lock:
                if gen != self._gen:                    # given up on while it was stuck: a newer thread has the work
                    return
                if not self._again:
                    self._busy = False
                    self._idle.set()
                    return
                self._again, self._since = False, time.monotonic()
            try:
                if getattr(self._bridge, "active", False):
                    self._bridge._set_bot_commands()    # reads the tabs now, whatever they were at the request
            except Exception:
                sf_log._swallow("bridge_menu: sending the menu")


def _of(bridge) -> MenuSync:
    with _LOCK:
        sync = vars(bridge).get(_ATTR)
        if not isinstance(sync, MenuSync):
            sync = MenuSync(bridge)
            setattr(bridge, _ATTR, sync)
        return sync


def request(bridge):
    """Send `bridge`'s menu soon, on a thread of its own. Returns at once; a stopped bridge has no menu."""
    if bridge is not None and getattr(bridge, "active", False):
        _of(bridge).request()


def wait_idle(bridge, timeout: float = 10.0) -> bool:
    """True once nothing is being sent or waiting to be (a test, or a caller that must know)."""
    return _of(bridge)._idle.wait(timeout)
