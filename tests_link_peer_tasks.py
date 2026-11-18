#!/usr/bin/env python3
"""Another machine's tasks on this desktop (GET /link/tasks, FrameLink.remote_tasks, link_bots.PeerTasks,
Api.link_peer_tasks).

Asked for: in the office, each paired machine's room gets its own read-only whiteboard of that
machine's tasks. This is the data path under it.

  1. The serving side (api_tasks._tasks_for_peer over the real store) behind a real signed link: the asking
     machine gets decisions / stuck / open; the same rev answers {unchanged}; a changed list changes the
     rev, a stuck row's growing idle_s does not; a store that cannot be opened answers {ok: false, code}.
  2. The permission is the one /link/bots has: a signed peer that this machine may not be controlled by
     (one-way pairing) gets 403 and nothing is read; an unsigned request, a bad signature and an unknown
     peer get 403; a machine on a version before the route is a 404 (kept as `code`); an offline one is not asked.
  3. PeerTasks, kept for the page: returns at once from what is kept, empty and `pending` the first time;
     asks in the background only when the page calls and the answer is older than TASKS_FRESH; unchanged
     keeps the lists; 404 -> old, 403 -> denied (both asked again only after REFUSED_RETRY); a timeout keeps
     the last good lists for KEEP seconds, then marks them error (rows and `at` kept, the page says how old);
     offline is error at once; one machine's exception is
     contained; an unpaired machine is forgotten; a phone is not listed.
  4. Over the real link and with real threads, through Api.link_peer_tasks' JSON.

Demo data only. Run: .venv/bin/python tests_link_peer_tasks.py
"""
import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-peertasks-"))
sys.modules.setdefault("webview", MagicMock())
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import api_tasks  # noqa: E402
import frame_link  # noqa: E402
import link_bots  # noqa: E402
import state_store as ss  # noqa: E402

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


api_tasks._ = lambda en, zh: en


class App(api_tasks.TasksApiMixin):
    """The part of the Api the route reaches: _tasks_for_peer over the real (test-isolated) store."""


class Node:
    """One in-process ShellBot stand-in: config + an execute stub that answers tasks_link like the Api does."""

    def __init__(self, name):
        self.tmp = tempfile.mkdtemp(prefix=f"sf-peertasks-{name}-", dir=TMP)
        self.port = free_port()
        self.asked = []
        self.app = App()
        self.config = {"frame_link": {"enabled": True, "listen_host": "127.0.0.1", "listen_port": self.port,
                                      "frame_name": name, "frame_id": uuid.uuid4().hex, "peers": {}}}

        def execute(cmd, args):
            if cmd == "tasks_link":
                self.asked.append(dict(args or {}))
                return self.app._tasks_for_peer(args or {})
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
    """A machine on a version before GET /link/tasks: every route it does not know is a 404."""

    def do_GET(self):
        body = b'{"success": false, "message": "no such route"}'
        self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def signed_get(node_from, peer, path, ts=None, nonce=None, sig=None, who=None):
    ts = ts if ts is not None else str(time.time())
    nonce = nonce or os.urandom(16).hex()
    if sig is None:
        sig = frame_link._hmac_hex(bytes.fromhex(peer["secret"]), frame_link.FrameLink._string_to_sign(
            "GET", path, ts, nonce, frame_link._sha256_hex(b"")))
    headers = {} if who == "none" else {"X-SF-Peer": who or node_from.fid, "X-SF-Ts": ts, "X-SF-Nonce": nonce, "X-SF-Sign": sig}
    req = urllib.request.Request(f"http://{peer['host']}:{peer['port']}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def part1_2():
    print("1. GET /link/tasks between two machines, over the real store")
    a, b, c = Node("desk-a"), Node("desk-b"), Node("desk-c")
    old = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Old)
    threading.Thread(target=old.serve_forever, daemon=True).start()
    try:
        for n in (a, b, c):
            n.link.start()
        pair = a.link.pairing_begin()
        check("A and B pair", b.link.join("127.0.0.1", a.port, pair["code"]).get("success") is True)
        b.link._mark_status(a.fid, False, "no answer")
        res = b.link.remote_tasks(a.fid)
        check("a machine the poll loop found offline is not asked", res.get("code") == "offline" and not a.asked, res)
        check("…ping finds it", b.link.ping_peer(a.fid).get("success") is True)

        res = b.link.remote_tasks(a.fid)
        check("an empty store answers three empty lists and a rev", res.get("ok") is True and res.get("success") is True
              and res.get("decisions") == [] and res.get("stuck") == [] and res.get("open") == [] and res.get("rev"), res)
        ss.add("示範：整理週報", actor="示範助理", owner="示範助理")
        ss.add("示範：要你選方案", actor="示範助理", owner=ss.USER, kind="decision")
        old_one = ss.add("示範：放很久的事", actor="示範助理", owner="示範助理", now=time.time() - 30 * 86400)
        res = b.link.remote_tasks(a.fid)
        titles = lambda k: {t["title"] for t in res.get(k) or []}          # noqa: E731
        check("the asking side gets the other side's three lists", titles("open") ==
              {"示範：整理週報", "示範：放很久的事", "示範：要你選方案"} and titles("decisions") == {"示範：要你選方案"}
              and [t["title"] for t in res["stuck"]] == ["示範：放很久的事"], res)
        row = res["open"][0]
        check("…rows are the store's own (same keys as tasks_overview)", {"id", "title", "status", "owner", "kind"} <= set(row)
              and set(json.loads(App().tasks_overview())["open"][0]) == set(row), sorted(row))
        rev = res["rev"]
        check("…the rev reached the other side on the next ask", b.link.remote_tasks(a.fid, rev).get("unchanged") is True
              and a.asked[-1].get("rev") == rev)
        res2 = b.link.remote_tasks(a.fid, rev)
        check("…with its rev, only {ok, rev, unchanged}", res2 == {"ok": True, "success": True, "rev": rev, "unchanged": True}, res2)
        stuck_row = res["stuck"][0]
        check("a stuck row carries idle_s", stuck_row.get("idle_s", 0) > 86400, stuck_row)
        ss.add("示範：新的一件", actor="示範助理", owner="示範助理")
        res3 = b.link.remote_tasks(a.fid, rev)
        check("a new task changes the rev and brings the lists", res3.get("unchanged") is None and res3["rev"] != rev
              and len(res3["open"]) == 4, res3.get("rev"))

        real_stuck = ss.stuck
        r_a = a.app._tasks_for_peer({})
        ss.stuck = lambda *x, **k: [dict(r, idle_s=r["idle_s"] + 500) for r in real_stuck()]
        r_b = a.app._tasks_for_peer({})
        ss.stuck = real_stuck
        check("a stuck row's growing idle_s alone does not change the rev", r_a["rev"] == r_b["rev"], (r_a["rev"], r_b["rev"]))

        real_listing = ss.listing

        def broken(*x, **k):
            raise ss.Unavailable("open_failed", "The task database could not be opened (x).", "任務資料庫打不開（x）。")
        ss.listing = broken
        res = b.link.remote_tasks(a.fid)
        ss.listing = real_listing
        check("a store that cannot be opened answers {ok: false, code}", res.get("ok") is False and res.get("success") is False
              and res.get("code") == "open_failed" and "decisions" not in res, res)

        print("2. who may ask")
        before = len(a.asked)
        peer_a = b.link.peers()[a.fid]
        st, body = signed_get(b, peer_a, "/link/tasks")
        check("a signed duplex peer: 200 with the lists", st == 200 and body.get("ok") is True and len(body["open"]) == 4, (st, body))
        st, _ = signed_get(b, peer_a, "/link/tasks", who="none")
        check("an unsigned request: 403, nothing read", st == 403 and len(a.asked) == before + 1)
        st, _ = signed_get(b, peer_a, "/link/tasks", sig="0" * 64)
        check("a bad signature: 403, nothing read", st == 403 and len(a.asked) == before + 1)
        st, _ = signed_get(b, peer_a, "/link/tasks", who="f" * 32)
        check("an unknown peer: 403, nothing read", st == 403 and len(a.asked) == before + 1)
        st, _ = signed_get(b, peer_a, "/link/tasks?rev=%s" % body["rev"])
        check("…with its rev: {unchanged}", st == 200 and _.get("unchanged") is True)

        a.config["frame_link"]["peers"][b.fid]["mode"] = "master"       # A turns the pairing one-way: A controls B, not the reverse
        before = len(a.asked)
        st, body = signed_get(b, peer_a, "/link/tasks")
        check("a peer this machine may not be controlled by (one-way pairing): 403, nothing read",
              st == 403 and "decisions" not in body and len(a.asked) == before, (st, body))
        res = b.link.remote_tasks(a.fid)
        check("…remote_tasks keeps the code", res.get("code") == 403 and res.get("success") is False, res)
        st, _ = signed_get(b, peer_a, "/link/bots")
        check("…the same rule as /link/bots", st == 403)
        a.config["frame_link"]["peers"][b.fid]["mode"] = "duplex"

        pc = c.link.pairing_begin(mode="host_controls")                 # C controls B; B may not control C
        check("a one-way pairing joins", b.link.join("127.0.0.1", c.port, pc["code"]).get("success") is True)
        b.link.ping_peer(c.fid)
        res = b.link.remote_tasks(c.fid)
        check("the controlled side asking the controller: 403, nothing read", res.get("code") == 403 and not c.asked, res)

        oid = uuid.uuid4().hex
        b.config["frame_link"]["peers"][oid] = {"name": "desk-old", "host": "127.0.0.1", "port": old.server_address[1],
                                                 "secret": "00" * 32, "mode": "duplex"}
        b.link._peer_status[oid] = {"reachable": True}
        res = b.link.remote_tasks(oid)
        check("an older machine without the route: 404 kept as `code`", res.get("code") == 404, res)
        check("a machine that is not paired is not asked", b.link.remote_tasks("nobody").get("success") is False)

        print("3b. link_peer_tasks over the real link")
        gate = threading.Event()
        real_exec = a.link._execute

        def slow(cmd, args):
            gate.wait(5)
            return real_exec(cmd, args)
        a.link._execute = slow
        import api_link

        class Page(api_link.LinkApiMixin):
            def __init__(self, link):
                self._l = link

            def _link(self):
                return self._l
        page = Page(b.link)
        t0 = time.monotonic()
        v = json.loads(page.link_peer_tasks())
        check("the page method returns at once while the machine is slow, with it pending",
              time.monotonic() - t0 < 1.0 and v["pending"] >= 1 and v["peers"][a.fid]["state"] == "pending"
              and v["peers"][a.fid]["at"] == 0, v)
        check("…and lists only what this machine may control (A and the old one; C is one-way)",
              set(v["peers"]) == {a.fid, oid}, sorted(v["peers"]))
        gate.set()
        got = {}
        for _ in range(100):
            got = json.loads(page.link_peer_tasks())
            if got["peers"][a.fid]["state"] == "ok" and got["peers"][oid]["state"] == "old":
                break
            time.sleep(0.05)
        pa = got["peers"][a.fid]
        check("…the background answer fills it in: A's lists, ok, with the time of the answer",
              pa["state"] == "ok" and abs(pa["at"] - time.time()) < 10 and len(pa["open"]) == 4
              and [t["title"] for t in pa["decisions"]] == ["示範：要你選方案"] and len(pa["stuck"]) == 1, pa)
        check("…the old machine is `old` with empty lists", got["peers"][oid] == {"state": "old", "at": 0, "decisions": [], "stuck": [], "open": []},
              got["peers"][oid])
        a.link._execute = real_exec
        a.config["frame_link"]["peers"][b.fid]["mode"] = "master"       # A turns one-way after B paired as duplex
        page._peer_tasks._kept[a.fid]["next_at"] = 0
        got = {}
        for _ in range(100):
            got = json.loads(page.link_peer_tasks())
            if got["peers"][a.fid]["state"] == "denied":
                break
            time.sleep(0.05)
        check("a machine that turned the pairing one-way is `denied`, no lists", got["peers"][a.fid]["state"] == "denied"
              and got["peers"][a.fid]["open"] == [], got["peers"][a.fid])
    finally:
        old.shutdown()
        for n in (a, b, c):
            n.link.stop()


class FakeLink:
    """FrameLink as PeerTasks uses it: peers(), remote_tasks()."""

    def __init__(self, peers):
        self._peers, self.answers, self.calls = peers, {}, []

    def peers(self):
        return dict(self._peers)

    def frame_name(self):
        return "desk-here"

    def remote_tasks(self, pid, rev=""):
        self.calls.append((pid, rev))
        ans = self.answers.get(pid, {"success": False, "code": "offline"})
        return ans(rev) if callable(ans) else ans


def peer(name, mode="duplex", kind="", host="10.0.0.2"):
    return {"name": name, "mode": mode, "kind": kind, "host": host, "port": 8767 if host else 0}


ANSWER = {"ok": True, "success": True, "rev": "r1",
          "decisions": [{"id": 3, "title": "示範：選方案", "status": "todo", "owner": "user", "kind": "decision"}],
          "stuck": [{"id": 4, "title": "示範：卡住", "status": "doing", "idle_s": 9000}],
          "open": [{"id": i, "title": f"示範：第{i}件", "status": "todo"} for i in range(5, 8)] + ["junk", None]}


def part3():
    print("3a. PeerTasks: kept, asked in the background, never blocking")
    peers = {"p-desk": peer("desk-b"), "p-ctl": peer("desk-c", mode="slave"), "p-phone": peer("phone", kind="ios"),
             "p-old": peer("desk-d", mode="master"), "p-off": peer("desk-e")}
    fl = FakeLink(peers)
    fl.answers["p-desk"] = lambda rev: ({"ok": True, "success": True, "rev": "r1", "unchanged": True} if rev == "r1"
                                        else json.loads(json.dumps(ANSWER)))
    fl.answers["p-old"] = {"success": False, "code": 404, "message": "HTTP 404"}
    clock, wall, queued = [1000.0], [1_800_000_000.0], []
    pt = link_bots.PeerTasks(lambda: fl, spawn=queued.append, clock=lambda: clock[0], wall=lambda: wall[0])
    run = lambda: ([fn() for fn in list(queued)], queued.clear())                        # noqa: E731
    v = pt.view()
    empty = {"at": 0, "decisions": [], "stuck": [], "open": []}
    check("the first look returns at once: every machine this one may control, pending, no rows; no phone, no one-way",
          v == {"peers": {k: dict(empty, state="pending") for k in ("p-desk", "p-old", "p-off")}, "pending": 3}, v)
    check("…nothing was asked on the caller's thread", fl.calls == [])
    check("a machine being asked is not asked twice", (pt.view(), len(queued))[1] == 3)
    run()
    v = pt.view()
    d = v["peers"]["p-desk"]
    check("the answer is there on the next look: ok, the time, the lists as given (non-row junk dropped)",
          d["state"] == "ok" and d["at"] == 1_800_000_000 and d["decisions"][0]["title"] == "示範：選方案"
          and d["stuck"][0]["idle_s"] == 9000 and [t["id"] for t in d["open"]] == [5, 6, 7], d)
    check("…404 is old, offline (nothing known) is error", v["peers"]["p-old"]["state"] == "old"
          and v["peers"]["p-off"]["state"] == "error", v)
    check("…and nothing is asked while it is fresh", not queued and v["pending"] == 0)
    wall[0] += 25
    clock[0] += link_bots.TASKS_FRESH + 1
    pt.view()
    check("past TASKS_FRESH it is asked again (the old one and the offline one: the offline after TASKS_FRESH too)", len(queued) == 2, len(queued))
    run()
    check("…with its rev", ("p-desk", "r1") in fl.calls)
    d = pt.view()["peers"]["p-desk"]
    check("unchanged keeps the lists and moves `at`", d["state"] == "ok" and d["at"] == 1_800_000_025 and len(d["open"]) == 3, d)
    clock[0] += link_bots.REFUSED_RETRY + 1
    pt.view()
    check("the old one is asked again only after REFUSED_RETRY", len(queued) == 3, len(queued))
    run()

    print("   failures")
    fl.answers["p-desk"] = {"success": False, "code": 0, "message": "timed out"}
    clock[0] += link_bots.TASKS_FRESH + 1
    pt.view()
    run()
    d = pt.view()["peers"]["p-desk"]
    check("one timeout keeps the last good lists and their time", d["state"] == "ok" and len(d["open"]) == 3
          and d["at"] == 1_800_000_025, d)
    clock[0] += link_bots.KEEP + 1
    pt.view()
    run()
    d = pt.view()["peers"]["p-desk"]
    check("…past KEEP: error, the last good rows and their time kept (the page says how old)",
          d["state"] == "error" and len(d["open"]) == 3 and d["at"] == 1_800_000_025, d)
    fl.answers["p-desk"] = json.loads(json.dumps(ANSWER))
    clock[0] += link_bots.TASKS_FRESH + 1
    pt.view()
    run()
    check("…ok again when it answers", pt.view()["peers"]["p-desk"]["state"] == "ok")
    fl.answers["p-desk"] = {"success": False, "code": "offline"}
    clock[0] += link_bots.TASKS_FRESH + 1
    pt.view()
    run()
    check("offline (the poll loop says so) is error at once", pt.view()["peers"]["p-desk"]["state"] == "error")
    fl.answers["p-desk"] = {"success": False, "code": 403}
    clock[0] += link_bots.TASKS_FRESH + 1
    pt.view()
    run()
    check("403 is denied", pt.view()["peers"]["p-desk"]["state"] == "denied")
    clock[0] += link_bots.TASKS_FRESH + 1
    n_calls = len(fl.calls)
    pt.view()
    run()
    check("…and not asked again for REFUSED_RETRY", [c for c in fl.calls[n_calls:] if c[0] == "p-desk"] == [], fl.calls[n_calls:])
    fl.answers["p-desk"] = {"ok": False, "success": False, "code": "open_failed", "message": "x"}
    clock[0] += link_bots.REFUSED_RETRY + 1
    pt.view()
    run()
    check("its store unavailable (200, ok false) is error", pt.view()["peers"]["p-desk"]["state"] == "error")
    fl.answers["p-desk"] = lambda rev: (_ for _ in ()).throw(RuntimeError("boom"))
    clock[0] += link_bots.TASKS_FRESH + 1
    pt.view()
    run()
    check("an exception from one machine is contained and it is not left 'being asked'",
          pt.view()["pending"] == 0 and pt.view()["peers"]["p-desk"]["state"] == "error")
    fl.answers["p-desk"] = json.loads(json.dumps(ANSWER))
    clock[0] += link_bots.TASKS_FRESH + 1
    pt.view()
    run()
    del fl._peers["p-desk"]
    v = pt.view()
    check("an unpaired machine is forgotten at once", "p-desk" not in v["peers"] and "p-desk" not in pt._kept)
    fl._peers["p-desk"] = peer("desk-b", mode="slave")
    check("…and one this machine may no longer control is not listed", "p-desk" not in pt.view()["peers"])
    check("no Frame Link: nothing", link_bots.PeerTasks(lambda: None).view() == {"peers": {}, "pending": 0})
    check("a list that is not a list is empty, rows over the cap are cut",
          link_bots.compact_tasks({"decisions": "x", "open": [{"id": i} for i in range(300)]})
          == {"decisions": [], "stuck": [], "open": [{"id": i} for i in range(link_bots.TASK_ROWS)]})

    print("   real threads")
    fl2 = FakeLink({"p1": peer("desk-b")})
    gate = threading.Event()

    def slow(rev):
        gate.wait(3)
        return json.loads(json.dumps(ANSWER))
    fl2.answers["p1"] = slow
    pt2 = link_bots.PeerTasks(lambda: fl2)
    t0 = time.monotonic()
    v = pt2.view()
    check("a slow machine does not hold up the look", time.monotonic() - t0 < 0.5 and v["pending"] == 1
          and v["peers"]["p1"]["state"] == "pending", v)
    gate.set()
    for _ in range(50):
        if pt2.view()["peers"]["p1"]["state"] == "ok":
            break
        time.sleep(0.05)
    check("…its answer arrives from the background", pt2.view()["peers"]["p1"]["state"] == "ok")


try:
    part1_2()
    part3()
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
