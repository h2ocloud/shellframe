"""This machine's addresses as its own hostname resolves to them, without waiting on a resolver that hangs.

Frame Link (frame_link.local_addresses) uses it as a second source of LAN addresses for the pairing code and
status(). A module of its own so frame_link stays within its size budget (tests_architecture).
"""
import socket
import threading
import time

LOOKUP_S = 2.0          # how long the hostname lookup may take before it is given up
KEEP_S = 30.0           # an answer is reused this long (status() asks every few seconds)
_STATE = {"thread": None, "found": [], "at": -1e9}
_LOCK = threading.Lock()


def hostname_addresses() -> list:
    """What the resolver says this machine's hostname is: a second source of LAN addresses.

    gethostbyname_ex has no timeout of its own. On a Mac whose hostname does not resolve (a CI
    runner, a laptop on a new network) it waits out mDNS and DNS for a minute or more, and
    pairing_begin and status() both ask: the pairing screen froze for that long. So it runs in a
    thread the caller stops waiting for; the addresses found by then are used, the rest dropped.
    status() is asked every few seconds: one lookup at a time (a hung one is not started again
    while it hangs), and its answer is kept for KEEP_S."""
    with _LOCK:
        if time.monotonic() - _STATE["at"] < KEEP_S:
            return list(_STATE["found"])
        t = _STATE["thread"]
        if t is None or not t.is_alive():
            box = []

            def lookup():
                try:
                    box.extend(socket.gethostbyname_ex(socket.gethostname())[2])
                except OSError:
                    pass
                with _LOCK:
                    _STATE.update(found=list(box), at=time.monotonic())

            t = threading.Thread(target=lookup, daemon=True, name="sf-link-hostname")
            _STATE["thread"] = t
            t.start()
    t.join(LOOKUP_S)
    with _LOCK:
        return list(_STATE["found"])
