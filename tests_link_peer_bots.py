#!/usr/bin/env python3
"""Another machine's bots on this desktop (link_bots.py, FrameLink.remote_bots).

Asked for in daily use: with two computers paired, the chat view's # should find the other
computer's bots, and the sidebar's Frame Link zone should show that computer's own faces with its
primary bot marked, not faces guessed here from the tab names.

  1. remote_bots: two in-process Frame Link instances. A paired machine's GET /link/bots comes
     back; `rev` is passed through (unchanged answers stay small); a machine the poll loop found
     offline is not asked; one-way pairing (may not control) is a 403 and an older version a 404,
     both kept as `code`.
  2. PeerBots: what the page gets back at once, and what is asked in the background: only paired
     desktops this one may control (no phone, not the other side of a one-way pairing); a fresh
     answer is reused; an unchanged one keeps what was kept; 403 / 404 / offline leave the machine
     with no bots (asked again later, never in a loop); a timeout keeps the last good answer for a
     while; an unpaired machine is forgotten; a machine being asked is not asked twice.
  3. The # address: `<machine>/<bot>` reaches the same machine through A2A's own parser and lookup
     (agent_link.parse_target, _peer_id); a shared or spaced name, or this machine's own name, falls
     back to the id. A #<machine>/<bot> in a delegate's task is not read as a local tab's tag.

Demo data only. Run: .venv/bin/python tests_link_peer_bots.py
"""
import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-peerbots-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules.setdefault("webview", MagicMock())
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import agent_link  # noqa: E402
import frame_link  # noqa: E402
import link_bots  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


BOTS_LINK = {"success": True, "v": 1, "rev": 41, "now": 1.0, "enabled": True, "primary": "總管",
             "bots": [{"role": "總管", "label": "總管", "avatar": {"shape": "star", "color": "red"}, "primary": True,
                       "external": False, "state": "awake", "sid": "s1", "former_names": ["主控"],
                       "latest": "not for the page", "decision": None},
                      {"role": "研究", "label": "研究-CLD", "avatar": {"shape": "hex", "color": "blue"}, "primary": False,
                       "external": False, "state": "asleep", "sid": ""},
                      {"role": "信箱", "label": "信箱", "avatar": {"shape": "round", "color": "ink"}, "primary": False,
                       "external": True, "state": "external", "sid": ""}],
             "tabs": {"s1": {"bot": "總管", "avatar": {"shape": "star", "color": "red"}, "kind": "bot", "latest": "x"},
                      "s2": {"bot": "", "avatar": {"shape": "pill", "color": "green"}, "kind": "temp"},
                      "s3": {"bot": "", "avatar": {"shape": "round", "color": "sky"}, "kind": "terminal"}},
             "choices": {"shapes": ["round"]}, "connections": []}


class Node:
    """One in-process ShellBot stand-in: config dict + an execute stub that answers bots_link."""

    def __init__(self, name):
        self.tmp = tempfile.mkdtemp(prefix=f"sf-peerbots-{name}-", dir=TMP)
        self.port = free_port()
        self.asked = []
        self.config = {"frame_link": {"enabled": True, "listen_host": "127.0.0.1", "listen_port": self.port,
                                      "frame_name": name, "frame_id": uuid.uuid4().hex, "peers": {}}}

        def execute(cmd, args):
            if cmd == "bots_link":
                self.asked.append(dict(args or {}))
                if str((args or {}).get("rev") or "") == str(BOTS_LINK["rev"]):
                    return {"success": True, "unchanged": True, "rev": BOTS_LINK["rev"]}
                return json.loads(json.dumps(BOTS_LINK))
            return {"success": False, "message": f"unknown {cmd}"}

        def update_config(mutator):
            mutator(self.config)
            return self.config

        self.link = frame_link.FrameLink(get_config=lambda: self.config, update_config=update_config,
                                         execute_fn=execute, log=lambda m: None, version="test",
                                         state_dir=os.path.join(self.tmp, "state"),
                                         downloads_dir=os.path.join(self.tmp, "dl"))

    @property
    def fid(self):
        return self.config["frame_link"]["frame_id"]


class Old(http.server.BaseHTTPRequestHandler):
    """A machine on a version before GET /link/bots: every route it does not know is a 404."""

    def do_GET(self):
        body = b'{"success": false, "message": "no such route"}'
        self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def part1():
    print("1. FrameLink.remote_bots between two machines")
    a, b, c = Node("desk-a"), Node("desk-b"), Node("desk-c")
    old = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Old)
    threading.Thread(target=old.serve_forever, daemon=True).start()
    try:
        for n in (a, b, c):
            n.link.start()
        pair = a.link.pairing_begin()
        check("A and B pair", b.link.join("127.0.0.1", a.port, pair["code"]).get("success") is True)
        b.link._mark_status(a.fid, False, "no answer")             # what the poll loop records for a machine that is off
        res = b.link.remote_bots(a.fid)
        check("a machine the poll loop found offline is not asked", res.get("code") == "offline"
              and not a.asked, res)
        check("…ping finds it", b.link.ping_peer(a.fid).get("success") is True)
        res = b.link.remote_bots(a.fid)
        check("then its /link/bots comes back", res.get("success") and res.get("primary") == "總管"
              and len(res.get("bots") or []) == 3 and "s1" in (res.get("tabs") or {}), res)
        res2 = b.link.remote_bots(a.fid, str(res.get("rev")))
        check("…and with its rev, only {unchanged}", res2.get("unchanged") is True and "bots" not in res2, res2)
        check("…the rev reached the other side", a.asked[-1].get("rev") == str(res.get("rev")), a.asked[-1])
        check("a machine that is not paired is not asked", b.link.remote_bots("nobody").get("success") is False)

        pc = c.link.pairing_begin(mode="host_controls")            # C controls B; B may not control C
        check("one-way pairing joins", b.link.join("127.0.0.1", c.port, pc["code"]).get("success") is True)
        b.link.ping_peer(c.fid)
        res = b.link.remote_bots(c.fid)
        check("the controlled side asking the controller: 403, nothing read", res.get("code") == 403
              and not c.asked, res)

        oid = uuid.uuid4().hex
        b.config["frame_link"]["peers"][oid] = {"name": "desk-old", "host": "127.0.0.1",
                                                 "port": old.server_address[1], "secret": "00" * 32, "mode": "duplex"}
        b.link._peer_status[oid] = {"reachable": True}
        res = b.link.remote_bots(oid)
        check("an older machine without the route: 404", res.get("code") == 404, res)

        pb = link_bots.PeerBots(lambda: b.link, spawn=lambda fn: fn())
        pb.view()
        v = pb.view()
        check("PeerBots over the real link: A's bots, not C's (one-way) nor the old one's",
              list(v["peers"]) == [a.fid] or set(v["peers"]) == {a.fid}, list(v["peers"]))
        pa = v["peers"].get(a.fid) or {}
        check("…its name and address", pa.get("name") == "desk-a" and pa.get("addr") == "desk-a", pa)
        check("…its primary, the tab faces and kinds", pa.get("primary") == "總管"
              and pa["tabs"]["s1"] == {"bot": "總管", "avatar": {"shape": "star", "color": "red"}, "kind": "bot"}
              and pa["tabs"]["s3"]["kind"] == "terminal", pa.get("tabs"))
        check("…without what the page does not draw", "latest" not in json.dumps(pa) and "choices" not in pa)
    finally:
        old.shutdown()
        for n in (a, b, c):
            n.link.stop()


class FakeLink:
    """FrameLink as PeerBots uses it: peers(), frame_name(), remote_bots()."""

    def __init__(self, peers, own="desk-here"):
        self._peers, self.own, self.answers, self.calls = peers, own, {}, []

    def peers(self):
        return dict(self._peers)

    def frame_name(self):
        return self.own

    def remote_bots(self, pid, rev=""):
        self.calls.append((pid, rev))
        ans = self.answers.get(pid, {"success": False, "code": "offline"})
        return ans(rev) if callable(ans) else ans


def peer(name, mode="duplex", kind="", host="10.0.0.2"):
    return {"name": name, "mode": mode, "kind": kind, "host": host, "port": 8767 if host else 0}


def part2():
    print("2. PeerBots: kept, asked in the background, never blocking")
    peers = {"p-desk": peer("desk-b"), "p-ctl": peer("desk-c", mode="slave"), "p-phone": peer("phone", kind="ios"),
             "p-oldphone": peer("tablet", host=""), "p-mine": peer("desk-d", mode="master")}
    check("targets: paired desktops this one may control (duplex, master), no phone, no address-less device",
          sorted(link_bots.targets(peers)) == ["p-desk", "p-mine"], sorted(link_bots.targets(peers)))
    fl = FakeLink(peers)
    fl.answers["p-desk"] = lambda rev: ({"success": True, "unchanged": True, "rev": 41} if rev == "41"
                                        else json.loads(json.dumps(BOTS_LINK)))
    fl.answers["p-mine"] = {"success": False, "code": 404, "message": "HTTP 404"}
    clock = [1000.0]
    queued = []
    pb = link_bots.PeerBots(lambda: fl, spawn=queued.append, clock=lambda: clock[0])
    v = pb.view()
    check("the first look returns at once, empty, with the asks pending", v == {"peers": {}, "pending": 2}, v)
    check("…nothing was asked on the caller's thread", fl.calls == [])
    v2 = pb.view()
    check("a machine being asked is not asked twice", len(queued) == 2 and v2["pending"] == 2, (len(queued), v2))
    for fn in queued:
        fn()
    queued.clear()
    v = pb.view()
    check("the answer is there on the next look", list(v["peers"]) == ["p-desk"], v)
    check("…only the machines PeerBots may ask were asked", sorted(p for p, _ in fl.calls) == ["p-desk", "p-mine"], fl.calls)
    d = v["peers"]["p-desk"]
    check("…compact: bots with face, primary, state; tabs with face, bot, kind",
          [b["role"] for b in d["bots"]] == ["總管", "研究", "信箱"] and d["bots"][0]["former_names"] == ["主控"]
          and d["tabs"]["s2"] == {"bot": "", "avatar": {"shape": "pill", "color": "green"}, "kind": "temp"}, d)
    check("an older machine (404) has no bots here", "p-mine" not in v["peers"])
    check("…and nothing is asked while the answer is fresh", not queued and v["pending"] == 0, queued)
    clock[0] += link_bots.FRESH + 1
    pb.view()
    check("past FRESH the desktop is asked again, with its rev; the 404 one is not",
          len(queued) == 1, queued)
    for fn in queued:
        fn()
    queued.clear()
    check("…the rev went along", fl.calls[-1] == ("p-desk", "41"), fl.calls[-1])
    check("…unchanged keeps what was kept", pb.view()["peers"]["p-desk"]["primary"] == "總管")
    clock[0] += link_bots.REFUSED_RETRY + 1
    pb.view()
    check("a refused machine is asked again only after REFUSED_RETRY", len(queued) == 2, len(queued))
    for fn in queued:
        fn()
    queued.clear()

    print("   failures")
    fl.answers["p-desk"] = {"success": False, "code": 0, "message": "timed out"}
    clock[0] += link_bots.FRESH + 1
    pb.view()
    for fn in queued:
        fn()
    queued.clear()
    check("one timeout keeps the last good answer (no flicker back to guessed faces)",
          "p-desk" in pb.view()["peers"])
    clock[0] += link_bots.KEEP + 1
    pb.view()
    for fn in queued:
        fn()
    queued.clear()
    check("…not past KEEP", "p-desk" not in pb.view()["peers"])
    fl.answers["p-desk"] = json.loads(json.dumps(BOTS_LINK))
    clock[0] += link_bots.FRESH + 1
    pb.view()
    for fn in queued:
        fn()
    queued.clear()
    check("…back when it answers again", "p-desk" in pb.view()["peers"])
    fl.answers["p-desk"] = {"success": False, "code": "offline"}
    clock[0] += link_bots.FRESH + 1
    pb.view()
    for fn in queued:
        fn()
    queued.clear()
    check("offline (the poll loop says so) drops it at once", "p-desk" not in pb.view()["peers"])
    fl.answers["p-desk"] = {"success": False, "code": 403}
    clock[0] += link_bots.FRESH + 1
    pb.view()
    for fn in queued:
        fn()
    queued.clear()
    check("403 (one-way pairing turned around) leaves it with no bots", "p-desk" not in pb.view()["peers"])
    fl.answers["p-desk"] = lambda rev: (_ for _ in ()).throw(RuntimeError("boom"))
    clock[0] += link_bots.REFUSED_RETRY + 1
    pb.view()
    for fn in queued:
        fn()
    queued.clear()
    check("an exception from one machine is contained, and it is not left 'being asked'",
          pb.view()["pending"] == 0 and "p-desk" not in pb.view()["peers"])
    fl.answers["p-desk"] = json.loads(json.dumps(BOTS_LINK))
    clock[0] += link_bots.FRESH + 1
    pb.view()
    for fn in queued:
        fn()
    queued.clear()
    del fl._peers["p-desk"]
    check("an unpaired machine is forgotten at once", pb.view()["peers"] == {} and "p-desk" not in pb._kept)
    fl._peers["p-desk"] = peer("desk-b", mode="slave")
    check("…and one this machine may no longer control is not listed", pb.view()["peers"] == {})

    print("   real threads")
    fl2 = FakeLink({"p1": peer("desk-b")})
    gate = threading.Event()

    def slow(rev):
        gate.wait(3)
        return json.loads(json.dumps(BOTS_LINK))
    fl2.answers["p1"] = slow
    pb2 = link_bots.PeerBots(lambda: fl2)
    import time as _t
    t0 = _t.monotonic()
    v = pb2.view()
    check("a slow machine does not hold up the look", _t.monotonic() - t0 < 0.5 and v["pending"] == 1, v)
    gate.set()
    for _ in range(50):
        if pb2.view()["peers"]:
            break
        _t.sleep(0.05)
    check("…its answer arrives from the background", "p1" in pb2.view()["peers"])
    check("no Frame Link: nothing", link_bots.PeerBots(lambda: None).view() == {"peers": {}, "pending": 0})


class Ctx:
    def __init__(self, peers, own_name="desk-here", own_id="me"):
        self._p, self._n, self._i = peers, own_name, own_id

    def peers(self):
        return self._p

    def frame_name(self):
        return self._n

    def frame_id(self):
        return self._i


def part3():
    print("3. #<machine>/<bot> reaches the same machine through A2A")
    peers = {"aa11": {"name": "desk-b"}, "bb22": {"name": "iPad"}, "cc33": {"name": "iPad"},
             "dd44": {"name": "Home Mac"}, "ee55": {"name": "desk-here"}, "ff66": {"name": "lab/2"}, "ab12": {"name": ""}}
    addr = link_bots.addresses(peers, own="desk-here")
    check("a unique one-word name is the address", addr["aa11"] == "desk-b", addr)
    check("a shared name falls back to the id", addr["bb22"] == "bb22" and addr["cc33"] == "cc33", addr)
    check("a name with a space, a '/', or no name: the id", addr["dd44"] == "dd44" and addr["ff66"] == "ff66"
          and addr["ab12"] == "ab12", addr)
    check("this machine's own name: the id (A2A refuses it as itself)", addr["ee55"] == "ee55", addr)
    ctx = Ctx(peers)
    for pid in ("aa11", "bb22", "dd44", "ee55"):
        t = agent_link.parse_target(f"{addr[pid]}/研究")
        check(f"{addr[pid]}/研究 → that machine and that bot", t["peer"] == addr[pid] and t["role"] == "研究"
              and agent_link._peer_id(ctx, t["peer"]) == pid, (t, agent_link._peer_id(ctx, t["peer"])))
    t = agent_link.parse_target("desk-b/s7")
    check("a sid after the machine is a tab there", t == {"peer": "desk-b", "role": "", "sid": "s7"}, t)

    import main                                  # noqa: E402  (webview is stubbed above)
    from main import Api                         # noqa: E402

    class Tab:
        def __init__(self, sid, label):
            self.sid, self._custom_label, self.alive = sid, label, True
    api = Api.__new__(Api)
    api.sessions = {"s1": Tab("s1", "desk-b"), "s2": Tab("s2", "研究")}
    tags = api._extract_tab_tags("請 #desk-b/研究 看一下，再跟 #研究 對一下")
    check("a delegate's task: #desk-b/研究 is not the local tab named desk-b", [x["sid"] for x in tags] == ["s2"], tags)
    tags = api._extract_tab_tags("#desk-b 和 #研究")
    check("…a plain #desk-b still is", [x["sid"] for x in tags] == ["s1", "s2"], tags)


try:
    part1()
    part2()
    part3()
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
