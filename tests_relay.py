#!/usr/bin/env python3
"""Frame Link relay（公網／NAT）回歸測試。

同一個 process 起：relay_server、frameA（電腦，設定 relay → 出站長輪詢）、
frameB（手機的替身：沒有可直連位址，只有 relay）。驗證：

  - 配對可以整段走 relay（pair/start、pair/finish 都是 envelope）
  - 配對後 B 經 relay 呼叫 A 的 list / stream / input 都到、回應簽章驗得過
  - relay token 錯 → 401；沒註冊的 frame_id → 404；A 不回 → 504（client 拿到錯誤而不是掛住）
  - 未簽章 / 壞簽章的 envelope 經 relay 一樣被 A 以 403 拒絕（relay 無法繞過簽章）
  - 只放行 /link/*（relay 端與電腦端都擋）
  - pair_url 編碼／解碼 round-trip、join_url 走 hosts→relay 順序

跑法：.venv/bin/python tests_relay.py
"""

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frame_link  # noqa: E402
import link_relay  # noqa: E402
import relay_server  # noqa: E402

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Node:
    def __init__(self, name, relay=None, listen=True):
        self.tmp = tempfile.mkdtemp(prefix=f"sfrelay-{name}-")
        self.port = free_port()
        self.config = {"frame_link": {
            "enabled": bool(listen),
            "listen_host": "127.0.0.1",
            "listen_port": self.port,
            "frame_name": name,
            "frame_id": uuid.uuid4().hex,
            "peers": {},
            "relay": relay or {"url": "", "token": ""},
            "public_host": "",
        }}
        self.raw = []
        self.voices = []

        def execute(cmd, args):
            if cmd == "list":
                return {"success": True, "message": "1 session",
                        "details": {"sessions": [{"sid": "s1", "label": f"{name}-tab",
                                                  "alive": True, "cols": 80, "rows": 24}]}}
            if cmd == "raw_input":
                self.raw.append((args.get("sid"), args.get("data")))
                return {"success": True}
            if cmd == "snapshot":
                return {"success": True, "details": {"ansi": "\x1b[31mhi\x1b[0m", "cols": 80, "rows": 24}}
            if cmd == "voice_inject":
                size = os.path.getsize(args["path"])
                self.voices.append((args.get("sid"), size))
                return {"success": True, "details": {"text": f"heard {size} bytes", "injected": True}}
            return {"success": False, "message": f"unknown {cmd}"}

        def update_config(mutator):
            mutator(self.config)
            return self.config

        self.link = frame_link.FrameLink(
            get_config=lambda: self.config, update_config=update_config,
            execute_fn=execute, notify=lambda ev: None, log=lambda m: None,
            version="test", state_dir=os.path.join(self.tmp, "state"),
            downloads_dir=os.path.join(self.tmp, "dl"))

    @property
    def fid(self):
        return self.config["frame_link"]["frame_id"]

    def cleanup(self):
        self.link.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def main():
    token = "relay-test-token-" + uuid.uuid4().hex[:8]
    rport = free_port()
    httpd, state = relay_server.create_server("127.0.0.1", rport, token)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    relay_url = f"http://127.0.0.1:{rport}"
    relay_cfg = {"url": relay_url, "token": token}

    a = Node("computer", relay=relay_cfg)          # the Mac: listener + relay poller
    b = Node("phone", listen=False)                 # never reachable, no listener
    try:
        # ── pair_url round-trip ──
        check("computer listener + relay start", a.link.start())
        check("relay poller running", a.link._relay is not None and a.link._relay.running())
        pair = a.link.pairing_begin(mode="host_controls")
        payload = frame_link.parse_pair_url(pair.get("pair_url", ""))
        check("pair_url carries fid/hosts/port/code/mode/relay",
              payload and payload["fid"] == a.fid and payload["port"] == a.port
              and frame_link.normalize_code(payload["code"]) == frame_link.normalize_code(pair["code"])
              and payload["mode"] == "host_controls" and payload["relay"]["url"] == relay_url
              and payload["relay"]["token"] == token)
        check("parse_pair_url rejects junk",
              frame_link.parse_pair_url("shellframe://pair?d=@@@") is None
              and frame_link.parse_pair_url("https://x/") is None)
        st = a.link.status()
        check("status exposes relay + public_host", "relay" in st and st["relay"]["configured"] is True
              and "public_host" in st)

        # ── computer must be online at the relay before a call is accepted ──
        check("relay sees computer online", wait_until(lambda: state.online(a.fid), 5))

        # ── pairing entirely through the relay (phone has no address to A) ──
        # force relay-only: drop the hosts from the payload like a phone on cellular would
        relay_only = dict(payload)
        relay_only["hosts"] = []
        res = b.link.join_url(frame_link.build_pair_url(relay_only))
        check("join through relay succeeds", res.get("success") is True)
        check("phone stored A with relay and no host",
              (b.link.peers().get(a.fid) or {}).get("relay", {}).get("url") == relay_url
              and not (b.link.peers().get(a.fid) or {}).get("host"))
        check("A stored phone with kind=shellframe and no host",
              (a.link.peers().get(b.fid) or {}).get("kind") == "shellframe"
              and not (a.link.peers().get(b.fid) or {}).get("host"))
        # A generated host_controls → A is master; B (joiner) is slave
        check("directional mode survives relay pairing",
              (a.link.peers()[b.fid].get("mode")) == "master"
              and (b.link.peers()[a.fid].get("mode")) == "slave")
        a.link.unpair(b.fid)
        b.link.unpair(a.fid)

        # duplex pairing via relay for the operational checks
        pair2 = a.link.pairing_begin(mode="duplex")
        p2 = frame_link.parse_pair_url(pair2["pair_url"])
        p2["hosts"] = []
        res2 = b.link.join_url(frame_link.build_pair_url(p2))
        check("duplex join through relay", res2.get("success") is True)

        # ── signed operations through the relay ──
        info = b.link.remote_info(a.fid)
        check("remote_info via relay", info.get("success") and
              [s["sid"] for s in info["details"]["sessions"]] == ["s1"])
        att = b.link.remote_stream(a.fid, "s1", -1)
        check("stream attach via relay", att.get("success") and "seq" in att)
        a.link.feed_output("s1", "\x1b[32mgreen\x1b[0m")
        got = b.link.remote_stream(a.fid, "s1", att["seq"])
        check("stream data via relay is byte-exact", got.get("data") == "\x1b[32mgreen\x1b[0m")
        ri = b.link.remote_input(a.fid, "s1", "ls\r")
        check("raw input via relay", ri.get("success") is True and ("s1", "ls\r") in a.raw)
        ping = b.link.ping_peer(a.fid)
        check("ping via relay marks reachable", ping.get("success") and
              b.link.status()["peers"][0]["reachable"] is True)
        # snapshot + signals routes (new in this version)
        peer_a = b.link._peer(a.fid)
        snap = b.link._signed_request(peer_a, "GET", "/link/snapshot?sid=s1")
        check("/link/snapshot via relay returns ansi", snap.get("success") and
              snap["details"]["ansi"].startswith("\x1b[31m"))
        try:
            import api_server
            api_server.EVENT_BUS.push(sid="s1", label="computer-tab", state="RED", reason="pick one")
            sig = b.link._signed_request(peer_a, "GET", "/link/signals?since=0")
            check("/link/signals returns agent events",
                  sig.get("success") and any(e.get("state") == "RED" for e in sig.get("events", [])))
        except ImportError:
            check("/link/signals returns agent events (api_server missing)", False)

        # ── voice through relay: signature over sha256, body verified, execute called ──
        import hashlib
        audio = os.urandom(4096)
        digest = hashlib.sha256(audio).hexdigest()
        vres = b.link._signed_request(
            peer_a, "POST", "/link/voice?sid=s1", body=audio,
            sign_payload=digest.encode(),
            extra_headers={"X-SF-Filename": "clip.m4a", "X-SF-Body-Sha256": digest,
                           "Content-Type": "application/octet-stream"}, timeout=20)
        check("voice via relay transcribed + injected",
              vres.get("success") and vres["details"]["text"] == "heard 4096 bytes"
              and ("s1", 4096) in a.voices)
        bad = os.urandom(1024)
        try:
            b.link._signed_request(
                peer_a, "POST", "/link/voice?sid=s1", body=bad,
                sign_payload=digest.encode(),            # claims the OLD hash
                extra_headers={"X-SF-Filename": "clip.m4a", "X-SF-Body-Sha256": digest,
                               "Content-Type": "application/octet-stream"}, timeout=20)
            check("voice hash mismatch rejected", False)
        except Exception as e:
            check("voice hash mismatch rejected", "400" in str(e))

        # ── the relay cannot bypass signatures ──
        st_, hdrs, raw = link_relay.relay_call(relay_url, token, a.fid, "POST", "/link/message",
                                               {"Content-Type": "application/json"},
                                               json.dumps({"text": "anon"}).encode(), timeout=10)
        check("unsigned envelope via relay → 403 from computer", st_ == 403)
        fake = dict(peer_a)
        fake["secret"] = "ab" * 32
        try:
            b.link._signed_request(fake, "GET", "/link/ping")
            check("wrong secret via relay rejected", False)
        except Exception as e:
            check("wrong secret via relay rejected", "403" in str(e))

        # ── relay-level guards ──
        try:
            link_relay.relay_call(relay_url, "wrong-token", a.fid, "GET", "/link/ping", {}, b"", timeout=5)
            check("bad relay token → 401", False)
        except RuntimeError as e:
            check("bad relay token → 401", "401" in str(e))
        try:
            link_relay.relay_call(relay_url, token, "deadbeef" * 4, "GET", "/link/ping", {}, b"", timeout=5)
            check("unknown frame → 404 offline", False)
        except RuntimeError as e:
            check("unknown frame → 404 offline", "404" in str(e))
        try:
            link_relay.relay_call(relay_url, token, a.fid, "GET", "/sessions", {}, b"", timeout=5)
            check("non-/link path refused by relay", False)
        except RuntimeError as e:
            check("non-/link path refused by relay", "400" in str(e))
        # computer stops pulling → the relay times out instead of hanging the client
        a.link._relay.stop()
        time.sleep(0.2)
        # a submit with a short wait: the poller is gone, nobody answers
        t0 = time.time()
        try:
            link_relay.relay_call(relay_url, token, a.fid, "GET", "/link/ping", {}, b"", timeout=1)
            check("no answer → 504 (not a hang)", False)
        except RuntimeError as e:
            check("no answer → 504 (not a hang)", "504" in str(e) and time.time() - t0 < 12)

        # ── join_url order: hosts first, relay fallback ──
        # (a fresh window; hosts contains a dead address then relay should win)
        a.link._start_relay()
        check("relay poller restarts", wait_until(lambda: a.link._relay.running(), 3))
        pair3 = a.link.pairing_begin(mode="duplex")
        p3 = frame_link.parse_pair_url(pair3["pair_url"])
        dead_port = free_port()
        p3["hosts"] = ["127.0.0.1"]
        p3["port"] = dead_port                      # nothing listens here → falls back to relay
        b.link.unpair(a.fid)
        res3 = b.link.join_url(frame_link.build_pair_url(p3))
        check("join_url falls back to relay when hosts are dead", res3.get("success") is True)
        check("fallback peer keeps relay config",
              (b.link.peers().get(a.fid) or {}).get("relay", {}).get("url") == relay_url)
    finally:
        a.cleanup()
        b.cleanup()
        httpd.shutdown()
        httpd.server_close()

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
