"""Another machine's bots, for this desktop: the Frame Link zone's faces and the chat view's # list.

A paired machine already answers GET /link/bots, the phone's bot view (docs/mobile-link.md): its
bots with their faces and states, which one is primary, and every tab's face. This module asks
each paired desktop this one may control for it and keeps the part the page draws, so that

- a tab over there shows the face that machine gives it, not one made up here from its name;
- that machine's primary bot is marked, as the local one is;
- `#` can name a bot over there as `<machine>/<bot>`, the target A2A already takes
  (agent_link.parse_target: the first `/` splits; agent_link._peer_id takes a name or an id).

Asking never holds up the page: `view()` hands back what is kept and asks again on a thread of
its own when that is older than FRESH seconds. A machine that is offline, refuses (403: one-way
pairing, it may not be controlled from here) or is older than the route (404) simply has no bots
here; it is asked again after a while, never in a loop. A phone is never asked.
"""

import re
import threading
import time

FRESH = 15.0           # an answer this young is used as it is (the Frame Link poll loop's cadence)
REFUSED_RETRY = 300.0  # after a 403 / 404: the answer will not change soon
KEEP = 60.0            # a failed ask (a timeout) keeps the last good answer this long; offline drops it at once

_ADDR_OK = re.compile(r"[^\s/#＃]+")     # a name that survives `#name/bot` in a message as one token


def _avatar(av) -> dict:
    """{shape, color} as strings; bots.js cleanAvatar checks them against its own lists."""
    av = av if isinstance(av, dict) else {}
    return {"shape": str(av.get("shape") or "")[:24], "color": str(av.get("color") or "")[:24]}


def compact(res: dict) -> dict:
    """The part of a /link/bots answer the page draws: no decisions, choices or latest lines."""
    bots = []
    for b in res.get("bots") or []:
        if not isinstance(b, dict) or not b.get("role"):
            continue
        role = str(b["role"])
        bots.append({"role": role, "label": str(b.get("label") or role),
                     "former_names": [str(x) for x in (b.get("former_names") or []) if x][:8],
                     "avatar": _avatar(b.get("avatar")), "primary": bool(b.get("primary")),
                     "state": str(b.get("state") or ""), "sid": str(b.get("sid") or "")})
    tabs = {}
    for sid, t in (res.get("tabs") or {}).items():
        if isinstance(t, dict):
            tabs[str(sid)] = {"bot": str(t.get("bot") or ""), "avatar": _avatar(t.get("avatar")),
                              "kind": str(t.get("kind") or "")}
    return {"enabled": bool(res.get("enabled")), "primary": str(res.get("primary") or ""),
            "bots": bots, "tabs": tabs}


def targets(peers: dict) -> dict:
    """The machines asked: paired desktops this one may control (mode duplex or master, as
    FrameLink.status's can_control). A phone (kind ios, or no address of its own) is not one."""
    out = {}
    for pid, p in (peers or {}).items():
        p = p or {}
        if p.get("kind") == "ios" or p.get("mode", "duplex") not in ("duplex", "master"):
            continue
        if not ((p.get("host") and p.get("port")) or (p.get("relay") or {}).get("url")):
            continue
        out[pid] = p
    return out


def addresses(peers: dict, own: str = "") -> dict:
    """How a message names each machine: its name when that is one token and no other paired
    device (phones too) has it, nor this machine (`own`; A2A refuses that name as itself), else
    its id. Either way agent_link._peer_id finds the same one."""
    names = [str((p or {}).get("name") or "") for p in (peers or {}).values()] + ([own] if own else [])
    out = {}
    for pid, p in (peers or {}).items():
        name = str((p or {}).get("name") or "")
        out[pid] = name if name and _ADDR_OK.fullmatch(name) and names.count(name) == 1 else pid
    return out


class PeerBots:
    """What each paired machine said its bots are, kept for the page.

    `link` is a callable returning the FrameLink (or None while Frame Link is not up); `spawn`
    runs a function in the background (tests pass one that runs it at once); `clock` is
    monotonic seconds."""

    def __init__(self, link, spawn=None, clock=time.monotonic):
        self._link = link
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True, name="link-bots").start())
        self._clock = clock
        self._lock = threading.Lock()
        self._kept = {}       # pid -> {"rev", "data", "ok_at", "asked_at", "next_at", "busy"}

    def view(self) -> dict:
        """{"peers": {pid: {name, addr, enabled, primary, bots, tabs}}, "pending": n}: machines with a
        good answer only. Stale ones are asked again in the background; `pending` says how many."""
        fl = self._link()
        if fl is None:
            return {"peers": {}, "pending": 0}
        peers = fl.peers()
        want = targets(peers)
        addr = addresses(peers, fl.frame_name())   # unique among all: agent_link._peer_id takes the first match
        now = self._clock()
        out, ask = {}, []
        with self._lock:
            for gone in [pid for pid in self._kept if pid not in want]:
                del self._kept[gone]                      # unpaired, or no longer ours to control
            for pid, p in want.items():
                k = self._kept.setdefault(pid, {"rev": "", "data": None, "ok_at": 0.0, "asked_at": 0.0,
                                                "next_at": 0.0, "busy": False})
                if not k["busy"] and now >= k["next_at"]:
                    k["busy"] = True
                    ask.append(pid)
                if k["data"] is not None:
                    out[pid] = dict(k["data"], name=str(p.get("name") or pid[:8]), addr=addr.get(pid, pid))
            pending = sum(1 for k in self._kept.values() if k["busy"])
        for pid in ask:
            self._spawn(lambda pid=pid: self.refresh(pid))
        return {"peers": out, "pending": pending}

    def refresh(self, pid: str):
        """Ask one machine now (on the caller's thread) and keep what it says."""
        try:
            fl = self._link()
            with self._lock:
                k = self._kept.get(pid)
                rev = (k or {}).get("rev", "") if (k or {}).get("data") is not None else ""
            try:
                res = fl.remote_bots(pid, rev) if fl is not None else {"success": False, "code": "offline"}
            except Exception as e:                     # never let one machine break the others
                res = {"success": False, "message": str(e)}
            res = res if isinstance(res, dict) else {}
            now = self._clock()
            with self._lock:
                k = self._kept.get(pid)
                if k is None:                          # unpaired while it was being asked
                    return
                k["asked_at"] = now
                code = res.get("code")
                if res.get("success") and res.get("unchanged") and k["data"] is not None:
                    k["ok_at"], k["next_at"] = now, now + FRESH
                elif res.get("success") and not res.get("unchanged"):
                    k.update(rev=str(res.get("rev") or ""), data=compact(res), ok_at=now, next_at=now + FRESH)
                elif code in (403, 404):               # may not look, or too old to know the route
                    k.update(rev="", data=None, next_at=now + REFUSED_RETRY)
                else:                                  # offline, a timeout, a bad answer
                    if code == "offline" or now - k["ok_at"] > KEEP:
                        k.update(rev="", data=None)
                    k["next_at"] = now + FRESH
        finally:
            with self._lock:
                if pid in self._kept:
                    self._kept[pid]["busy"] = False
