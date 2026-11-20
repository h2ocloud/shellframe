#!/usr/bin/env python3
"""A paired computer's ops board over Frame Link (link_ops.py, GET /link/ops, GET /link/ops/view, POST /link/ops/live,
and the desktop's ops_peer / ops_peer_view / ops_peer_live in api_ops.py).

Two in-process computers over a real signed link, a scripted sampler and a clock the test moves (nothing is written
to the real state or config folder):
  1. /link/ops: the summary without the object's badge and hint; with the switch off `level: "off"` (a success);
     /link/ops/view: the rules widget is `readonly: true`, the top five's `sort` shows that order for this one answer
     and does not change the saved one, the page's language is not changed on the serving side; /link/ops/live with
     the switch off is `code: "off"`
  2. who may ask: unsigned, a bad signature and an unknown peer get 403, a peer under one-way pairing gets 403 (the
     asking side says `denied`); a computer on a version before the routes is 404 (`old`); an unreachable one is not
     asked (`offline`)
  3. live: a paired computer's open window gives 2 s readings, 60 s with no heartbeat and it ends, its close does not
     end this computer's own window (nor another peer's), and each of theirs is kept apart
  4. what comes back from a peer is cleaned as a stranger's: an unknown level, a number out of range, rules that
     claim to be writable, an unknown widget
  5. the desktop's js_api: refused (`code: "off"`) while this computer's own switch is off; the JSON shapes
Demo data only. Run: .venv/bin/python tests_link_ops.py
"""
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="sf-link-ops-"))
os.environ["SHELLFRAME_TESTS"] = "1"
os.environ["SHELLFRAME_CONFIG_DIR"] = str(TMP / "config")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules.setdefault("webview", MagicMock())
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import api_ops  # noqa: E402
import frame_link  # noqa: E402
import link_ops  # noqa: E402
import office_ops as O  # noqa: E402
import ops_sampler  # noqa: E402
import sf_log  # noqa: E402

sf_log.DEBUG_LOG = str(TMP / "debug.log")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


class Fake:
    kind = "mac"

    def __init__(self, clock):
        self.clock = clock

    def sample(self, full=True, prime=True):
        out = dict.fromkeys(ops_sampler.SAMPLE_KEYS)
        out.update(t=self.clock(), cpu=12.0, cores=[10.0] * 4, cores_p=4, cores_e=0, pressure="normal", swap_used=1 << 30,
                   swap_total=4 << 30, disk_free_pct=50.0, disk_free=250 << 30, disk_total=500 << 30, online=True)
        if full:
            out.update(mem_total=16 << 30, mem_used=10 << 30, mem_pct=62.5, mem_app=6 << 30, mem_wired=2 << 30,
                       mem_compressed=1 << 30, mem_cached=3 << 30, net_in=120000.0, net_out=30000.0, iface="en0")
        return out

    def top_both(self, n=5):
        a = {"name": "示範瀏覽器", "cpu": 12.0, "rss": 1 << 30, "count": 3}
        b = {"name": "示範工具", "cpu": 4.0, "rss": 200 << 20, "count": 1}
        big = {"name": "示範代理程式", "cpu": 1.0, "rss": 6 << 30, "count": 23}
        return {"cpu": [a, b, big], "mem": [big, a, b]}


clock = Clock()
coll = O.Collector(sampler=Fake(clock), clock=clock, notify=lambda *a: None, load=False)
coll.lang = "en"
O.set_collector(coll)
coll.step()


class Host(api_ops.OpsApiMixin):
    """The part of the Api that answers the routes and the page's calls (no thread, no window)."""
    flag = True
    link = None

    def _ops_sync(self):
        return self.flag

    def _oo_off(self, lang):
        return None

    def _link(self):
        return self.link


class Node:
    def __init__(self, name):
        self.tmp = tempfile.mkdtemp(prefix=f"sf-link-ops-{name}-", dir=TMP)
        self.port = free_port()
        self.host = Host()
        self.asked = []
        self.config = {"frame_link": {"enabled": True, "listen_host": "127.0.0.1", "listen_port": self.port,
                                      "frame_name": name, "frame_id": uuid.uuid4().hex, "peers": {}}}

        def execute(cmd, args):
            if cmd in ("ops", "ops_view", "ops_live"):
                self.asked.append((cmd, dict(args or {})))
                return self.host._link_ops(cmd, args or {})
            return {"success": False, "message": f"unknown {cmd}"}

        def update_config(mutator):
            mutator(self.config)
            return self.config

        self.link = frame_link.FrameLink(get_config=lambda: self.config, update_config=update_config, execute_fn=execute,
                                         log=lambda m: None, version="test", state_dir=os.path.join(self.tmp, "state"),
                                         downloads_dir=os.path.join(self.tmp, "dl"))
        self.host.link = self.link

    @property
    def fid(self):
        return self.config["frame_link"]["frame_id"]


class Old(http.server.BaseHTTPRequestHandler):
    """A computer on a version before the routes: every one it does not know is a 404."""

    def _no(self):
        body = b'{"success": false, "message": "no such route"}'
        self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _no

    def log_message(self, *a):
        pass


def signed_get(node_from, peer, path, sig=None, who=None):
    ts, nonce = str(time.time()), os.urandom(16).hex()
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


def widgets(v, kind):
    return [w for s in v.get("sections", []) for w in s.get("widgets", []) if w.get("kind") == kind]


a, b = Node("desk-a"), Node("desk-b")
old = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Old)
threading.Thread(target=old.serve_forever, daemon=True).start()
try:
    a.link.start()
    b.link.start()
    pair = a.link.pairing_begin()
    check("A and B pair", b.link.join("127.0.0.1", a.port, pair["code"]).get("success") is True)
    check("…ping finds it", b.link.ping_peer(a.fid).get("success") is True)

    print("1. the three routes, over the real signed link")
    got = link_ops.peer_summary(b.link, a.fid, "en")
    check("/link/ops: success with the TV's six fields only (no badge, no hint)", got["success"] is True
          and set(got) == {"success", "level", "reason", "since", "muted_until", "updated", "face"}, sorted(got))
    check("…the level and the face", got["level"] == "ok" and got["face"]["cpu"] == 12.0 and got["face"]["mem"] == 62.5
          and got["updated"] == clock.t, got)
    check("…the language arrived (a=asked with lang en)", a.asked[-1] == ("ops", {"rev": "", "lang": "en", "sort": "", "peer_id": b.fid}), a.asked[-1])
    v = link_ops.peer_view(b.link, a.fid, "zh")
    check("/link/ops/view: success, in Chinese", v["success"] is True and v["title"] == "維運戰情" and len(v["sections"]) >= 5, v.get("code"))
    check("…and the serving side's own language is not changed by the asker", coll.lang == "en")
    rules = widgets(v, "rules")
    check("…the rules widget is readonly: true", len(rules) == 1 and rules[0].get("readonly") is True and rules[0]["items"], rules)
    top = widgets(v, "table")
    check("…the top five came with its order switch", len(top) == 1 and top[0].get("switch", {}).get("action") == "top_sort", top)
    saved = coll.top_sort
    coll.top_t = 0
    coll.live(True, clock())
    coll.step()
    v_mem = link_ops.peer_view(b.link, a.fid, "en", "mem")
    v_cpu = link_ops.peer_view(b.link, a.fid, "en", "cpu")
    check("…with the program names once it is being watched", widgets(v_mem, "table")[0]["rows"][0][0].startswith("示範代理程式"), widgets(v_mem, "table"))
    first = lambda vv: widgets(vv, "table")[0]["rows"][0][0]       # noqa: E731
    check("sort=mem and sort=cpu show their own order", first(v_mem).startswith("示範代理程式") and first(v_cpu).startswith("示範瀏覽器"), (first(v_mem), first(v_cpu)))
    check("…and the switch says which is on", widgets(v_mem, "table")[0]["switch"]["value"] == "mem" and widgets(v_cpu, "table")[0]["switch"]["value"] == "cpu")
    check("…this computer's saved order did not change", coll.top_sort == saved, coll.top_sort)
    v_bad = link_ops.peer_view(b.link, a.fid, "en", "x; drop")
    check("…a nonsense sort is left out of the request and the saved order shows", v_bad["success"] and widgets(v_bad, "table")[0]["switch"]["value"] == saved)
    coll.live(False, clock())

    print("1b. the switch off on the serving computer")
    a.host.flag = False
    got = link_ops.peer_summary(b.link, a.fid, "en")
    check("/link/ops: a success with level off and nothing else", got == {"success": True, "level": "off", "reason": "", "since": None,
                                                                         "muted_until": None, "updated": None, "face": None}, got)
    check("/link/ops/view: code off", link_ops.peer_view(b.link, a.fid, "en").get("code") == "off")
    live = link_ops.peer_live(b.link, a.fid, True)
    check("/link/ops/live: code off, nothing made live", live.get("code") == "off" and not coll.is_live(), live)
    a.host.flag = True

    print("2. who may ask")
    peer_a = b.link.peers()[a.fid]
    n = len(a.asked)
    st, body = signed_get(b, peer_a, "/link/ops?lang=en")
    check("a signed duplex peer: 200", st == 200 and body["level"] == "ok", (st, body))
    st, _ = signed_get(b, peer_a, "/link/ops", who="none")
    check("unsigned: 403, nothing answered", st == 403 and len(a.asked) == n + 1)
    st, _ = signed_get(b, peer_a, "/link/ops/view", sig="0" * 64)
    check("a bad signature on the view: 403", st == 403 and len(a.asked) == n + 1)
    st, _ = signed_get(b, peer_a, "/link/ops", who="f" * 32)
    check("an unknown peer: 403", st == 403 and len(a.asked) == n + 1)
    a.config["frame_link"]["peers"][b.fid]["mode"] = "master"
    for fn in (lambda: link_ops.peer_summary(b.link, a.fid, "en"), lambda: link_ops.peer_view(b.link, a.fid, "en"),
               lambda: link_ops.peer_live(b.link, a.fid, True)):
        r = fn()
        check("one-way pairing: `denied`, nothing answered", r.get("code") == "denied" and len(a.asked) == n + 1, r)
    a.config["frame_link"]["peers"][b.fid]["mode"] = "duplex"
    oid = uuid.uuid4().hex
    b.config["frame_link"]["peers"][oid] = {"name": "desk-old", "host": "127.0.0.1", "port": old.server_address[1], "secret": "00" * 32, "mode": "duplex"}
    b.link._peer_status[oid] = {"reachable": True}
    for fn in (lambda: link_ops.peer_summary(b.link, oid, "en"), lambda: link_ops.peer_view(b.link, oid, "en"), lambda: link_ops.peer_live(b.link, oid, True)):
        r = fn()
        check("a computer before the routes: 404 is `old`", r.get("code") == "old" and r.get("success") is False, r)
    b.link._mark_status(a.fid, False, "no answer")
    n = len(a.asked)
    check("an unreachable computer is `offline` and not asked", link_ops.peer_summary(b.link, a.fid, "en").get("code") == "offline" and len(a.asked) == n)
    check("…so is one that is not paired", link_ops.peer_view(b.link, "nobody", "en").get("code") == "offline")
    check("…ping finds it again", b.link.ping_peer(a.fid).get("success") is True)

    print("3. live, kept apart per looker")
    coll.live(False, clock())
    check("nobody looking: not live", not coll.is_live() and coll.interval(clock()) == 300.0)
    check("a peer's window opens: live, 2 s", link_ops.peer_live(b.link, a.fid, True) == {"success": True} and coll.is_live() and coll.interval(clock()) == 2.0)
    clock.t += 59
    check("59 s on, still live", coll.is_live())
    link_ops.peer_live(b.link, a.fid, True)
    clock.t += 59
    check("a heartbeat lengthens it", coll.is_live())
    clock.t += 2
    check("60 s with no word and it ends by itself", not coll.is_live() and coll.interval(clock()) == 300.0)
    coll.live(True, clock())                                    # this computer's own window
    link_ops.peer_live(b.link, a.fid, True)
    link_ops.peer_live(b.link, a.fid, False)
    check("a peer closing does not end this computer's own window", coll.is_live())
    coll.live(False, clock())
    check("…and now nobody is looking", not coll.is_live())
    link_ops.peer_live(b.link, a.fid, True)
    coll.live(True, clock())
    coll.live(False, clock())
    check("this computer closing does not end the peer's", coll.is_live())
    link_ops.peer_live(b.link, a.fid, False)
    check("…the peer closing does", not coll.is_live())
    for i in range(30):
        coll.live(True, clock(), who=f"peer:{i}")
    check("windows of peers that never said goodbye are capped", len(coll._live) <= 16, len(coll._live))
    coll._live.clear()
finally:
    a.link.stop()
    b.link.stop()
    old.shutdown()

print("4. what comes back from a peer is cleaned")


def fake_link(answer):
    return SimpleNamespace(_peer_or_err=lambda pid: ({"secret": "00"}, None), _peer_status={"p": {"reachable": True}},
                           _signed_request=lambda peer, method, path_qs, body=b"", timeout=6: answer)


dirty = {"success": True, "level": "bogus", "reason": "x" * 500, "since": "soon", "muted_until": 1e30, "updated": 1_800_000_000,
         "badge": 7, "hint": "ignored", "face": {"cpu": 1e99, "mem": -5, "pressure": "weird", "spark": list(range(40)), "extra": 1}}
got = link_ops.peer_summary(fake_link(dirty), "p", "en")
check("an unknown level becomes none, the reason is cut, nonsense numbers are None or clamped to their range", got["level"] == "none" and len(got["reason"]) <= 120
      and got["since"] is None and got["muted_until"] == 4000000000 and got["face"]["cpu"] == 100 and got["face"]["mem"] == 0
      and len(got["face"]["spark"]) <= 12 and "extra" not in got["face"] and "badge" not in got and "hint" not in got, got)
check("a peer that says level off (even with a face) is just off", link_ops.peer_summary(fake_link({"success": True, "level": "off", "face": {"cpu": 5}}), "p", "en")["face"] is None)
dv = {"success": True, "title": "t", "banner": {"level": "ok", "text": "fine"}, "sections": [
    {"id": "rules", "title": "r", "widgets": [{"kind": "rules", "readonly": False, "items": [{"id": "a", "label": "A", "on": True, "value": 1}]},
                                              {"kind": "script", "src": "x"}]},
    {"id": "../bad", "title": "no", "widgets": []}]}
got = link_ops.peer_view(fake_link(dv), "p", "en")
check("a view: rules made readonly here whatever the peer said, an unknown widget and a bad section id dropped",
      got["success"] and [w["kind"] for w in got["sections"][0]["widgets"]] == ["rules"] and got["sections"][0]["widgets"][0]["readonly"] is True
      and len(got["sections"]) == 1, got)
check("a non-dict answer is an error, not an exception", link_ops.peer_summary(fake_link([1, 2]), "p", "en").get("code") == "error")
check("the peer's own refusal code off stays off on the view", link_ops.peer_view(fake_link({"success": False, "code": "off"}), "p", "en").get("code") == "off")


def boom(*a, **k):
    raise TimeoutError("timed out")


slow = fake_link(None)
slow._signed_request = boom
check("a timeout is offline", link_ops.peer_summary(slow, "p", "en").get("code") == "offline")

print("5. the desktop's js_api")
h = Host()
h.flag = False
h._ops_off = lambda lang: json.dumps({"success": False, "code": "off"})
for fn in (lambda: h.ops_peer("p", "en"), lambda: h.ops_peer_view("p", "en"), lambda: h.ops_peer_live("p", True)):
    check("own switch off: code off, nobody asked", json.loads(fn()) == {"success": False, "code": "off"})
h2 = Host()
h2._ops_off = lambda lang: None
h2.link = fake_link({"success": True, "level": "ok", "reason": "", "face": {"cpu": 3}})
r = json.loads(h2.ops_peer("p", "zh"))
check("own switch on: ops_peer is a JSON summary", r["success"] and r["level"] == "ok" and r["face"]["cpu"] == 3, r)
h2.link = fake_link(dv)
check("ops_peer_view is a JSON view with readonly rules", json.loads(h2.ops_peer_view("p", "zh", "cpu"))["sections"][0]["widgets"][0]["readonly"] is True)
h2.link = fake_link({"success": True})
check("ops_peer_live is {success}", json.loads(h2.ops_peer_live("p", "true")) == {"success": True})

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
