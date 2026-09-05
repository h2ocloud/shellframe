#!/usr/bin/env python3
"""Frame Link（跨機配對）回歸測試。

兩個 FrameLink instance 在同一個 process 裡各開一個 127.0.0.1 listener，
端到端驗證：

  - 配對握手：配對碼不走明文、雙向互證、兩邊導出相同 secret
  - 錯誤碼：proof 驗不過；連錯 PAIR_MAX_ATTEMPTS 次配對窗口作廢
  - 簽章請求：未知 peer / 壞簽章 / 過期 timestamp / nonce 重放 全部 403
  - 遠端操作：list / peek / send 到達對方的 execute stub
  - 訊息與檔案：直連路徑 + 離線 outbox（排隊 → 對方輪詢拉走 → 確認後清掉暫存）

跑法：.venv/bin/python tests_frame_link.py
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import time
import urllib.request
import urllib.error
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frame_link  # noqa: E402

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
    """One in-process ShellFrame stand-in: config dict + execute stub."""

    def __init__(self, name):
        self.tmp = tempfile.mkdtemp(prefix=f"sflink-{name}-")
        self.downloads = os.path.join(self.tmp, "downloads")
        self.port = free_port()
        self.config = {"frame_link": {
            "enabled": True,
            "listen_host": "127.0.0.1",
            "listen_port": self.port,
            "frame_name": name,
            "frame_id": uuid.uuid4().hex,
            "peers": {},
        }}
        self.sent = []          # (sid, text, submit) received via /link/send
        self.events = []        # notify() pushes

        def execute(cmd, args):
            if cmd == "list":
                return {"success": True, "message": "2 sessions",
                        "details": {"sessions": [
                            {"sid": "s1", "label": f"{name}-tab1", "alive": True},
                            {"sid": "s2", "label": f"{name}-tab2", "alive": True},
                        ]}}
            if cmd == "peek":
                return {"success": True,
                        "details": {"text": f"screen-of-{args.get('sid')}@{name}"}}
            if cmd == "send":
                self.sent.append((args.get("sid"), args.get("text"),
                                  args.get("submit")))
                return {"success": True, "message": "sent"}
            return {"success": False, "message": f"unknown {cmd}"}

        def update_config(mutator):
            mutator(self.config)
            return self.config

        self.link = frame_link.FrameLink(
            get_config=lambda: self.config,
            update_config=update_config,
            execute_fn=execute,
            notify=self.events.append,
            log=lambda m: None,
            version="test",
            state_dir=os.path.join(self.tmp, "state"),
            downloads_dir=self.downloads,
        )

    @property
    def fid(self):
        return self.config["frame_link"]["frame_id"]

    def cleanup(self):
        self.link.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def post_json(url, obj):
    req = urllib.request.Request(
        url, data=json.dumps(obj).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def signed_get(node_from, peer, path, ts=None, nonce=None, sig=None):
    """Raw signed GET so tests can tamper with individual fields."""
    ts = ts if ts is not None else str(time.time())
    nonce = nonce or os.urandom(16).hex()
    if sig is None:
        sig = frame_link._hmac_hex(
            bytes.fromhex(peer["secret"]),
            frame_link.FrameLink._string_to_sign(
                "GET", path, ts, nonce, frame_link._sha256_hex(b"")))
    url = f"http://{peer['host']}:{peer['port']}{path}"
    req = urllib.request.Request(url, headers={
        "X-SF-Peer": node_from.fid, "X-SF-Ts": ts,
        "X-SF-Nonce": nonce, "X-SF-Sign": sig})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main():
    a = Node("frameA")
    b = Node("frameB")
    try:
        check("A listener starts", a.link.start())
        check("B listener starts", b.link.start())

        # ── 配對握手 ──
        pair = a.link.pairing_begin()
        check("pairing_begin returns a code", pair.get("success")
              and len(frame_link.normalize_code(pair["code"])) == frame_link._CODE_LEN)
        res = b.link.join("127.0.0.1", a.port, pair["code"])
        check("join succeeds", res.get("success") is True)
        check("A stored B as peer", b.fid in a.link.peers())
        check("B stored A as peer", a.fid in b.link.peers())
        sa = (a.link.peers().get(b.fid) or {}).get("secret")
        sb = (b.link.peers().get(a.fid) or {}).get("secret")
        check("both sides derived the same secret", sa and sa == sb)
        check("pairing window is single-use",
              a.link._pairing_active() is None)
        check("code never appears in stored secret",
              frame_link.normalize_code(pair["code"]) not in (sa or ""))

        # ── 錯誤配對碼：proof 驗不過、attempts 打死窗口 ──
        pair2 = a.link.pairing_begin()
        check("second pairing window opens", pair2.get("success"))
        bad = b.link.join("127.0.0.1", a.port, "AAAAA-AAAAA")
        check("wrong code rejected", bad.get("success") is False)
        for _ in range(frame_link.PAIR_MAX_ATTEMPTS):
            b.link.join("127.0.0.1", a.port, "BBBBB-BBBBB")
        good_after = b.link.join("127.0.0.1", a.port, pair2["code"])
        check("window dies after max bad attempts",
              good_after.get("success") is False)

        # ── 簽章請求防護 ──
        peer_a_from_b = b.link.peers()[a.fid]
        st, body = signed_get(b, peer_a_from_b, "/link/ping")
        check("signed ping ok", st == 200 and body.get("success"))
        # replay：同一組 header 打第二次
        ts = str(time.time())
        nonce = os.urandom(16).hex()
        st1, _ = signed_get(b, peer_a_from_b, "/link/ping", ts=ts, nonce=nonce)
        st2, body2 = signed_get(b, peer_a_from_b, "/link/ping", ts=ts, nonce=nonce)
        check("replayed nonce rejected", st1 == 200 and st2 == 403
              and "replay" in (body2.get("message") or ""))
        st, body = signed_get(b, peer_a_from_b, "/link/ping",
                              sig="0" * 64)
        check("bad signature rejected", st == 403)
        st, body = signed_get(b, peer_a_from_b, "/link/ping",
                              ts=str(time.time() - 3600))
        check("stale timestamp rejected", st == 403)
        fake = dict(peer_a_from_b)
        fake["secret"] = "ab" * 32
        st, body = signed_get(b, fake, "/link/ping")
        check("wrong secret rejected", st == 403)
        st, body = post_json(f"http://127.0.0.1:{a.port}/link/message",
                             {"text": "anon"})
        check("unsigned request rejected", st == 403)

        # ── 遠端操作 ──
        info = b.link.remote_info(a.fid)
        check("remote_info lists A's tabs",
              info.get("success") and
              [s["sid"] for s in info["details"]["sessions"]] == ["s1", "s2"])
        peek = b.link.remote_peek(a.fid, "s1")
        check("remote_peek returns A's screen",
              peek.get("success") and peek["details"]["text"] == "screen-of-s1@frameA")
        sent = b.link.remote_send(a.fid, "s2", "hello from B")
        check("remote_send lands in A's tab",
              sent.get("success") and a.sent == [("s2", "hello from B", True)])

        # ── 訊息（直連）──
        msg = b.link.send_message(a.fid, "哈囉 A")
        inbox_a = a.link.recent_events()
        check("direct message reaches A's inbox",
              msg.get("success") and any(
                  e.get("kind") == "message" and e.get("text") == "哈囉 A"
                  and e.get("dir") == "in" for e in inbox_a))

        # ── 檔案（直連）──
        src = os.path.join(b.tmp, "hello.txt")
        with open(src, "wb") as f:
            f.write(b"frame-link file payload \xe4\xb8\xad\xe6\x96\x87")
        fres = b.link.send_file(a.fid, src)
        check("direct file send ok", fres.get("success") is True)
        saved = fres.get("saved", "")
        check("file saved under A downloads dir",
              saved.startswith(a.downloads) and os.path.isfile(saved))
        if saved and os.path.isfile(saved):
            with open(saved, "rb") as f:
                check("file content intact",
                      f.read() == b"frame-link file payload \xe4\xb8\xad\xe6\x96\x87")
        else:
            check("file content intact", False)

        # ── outbox：A 這邊把 B 標成不可直連 → 排隊 → B 輪詢拉走 ──
        a.link.update_peer(b.fid, "", 0)
        q = a.link.send_message(b.fid, "排隊訊息")
        check("message to unreachable peer queues", q.get("queued") is True)
        src2 = os.path.join(a.tmp, "queued.bin")
        with open(src2, "wb") as f:
            f.write(os.urandom(2048))
        q2 = a.link.send_file(b.fid, src2)
        check("file to unreachable peer queues", q2.get("queued") is True)
        staged = list((a.link._files_dir / b.fid).glob("*"))
        check("queued file staged on A", len(staged) == 1)
        qs = a.link.remote_send(b.fid, "s1", "queued command")
        check("remote_send to unreachable peer queues", qs.get("queued") is True)

        cursors = {}
        b.link.poll_once(cursors)
        inbox_b = b.link.recent_events()
        check("B pulled the queued message", any(
            e.get("kind") == "message" and e.get("text") == "排隊訊息"
            for e in inbox_b))
        pulled = [p for p in
                  (os.path.join(dp, fn)
                   for dp, _, fns in os.walk(b.downloads) for fn in fns)
                  if os.path.basename(p) == "queued.bin"]
        check("B pulled the queued file", len(pulled) == 1)
        if pulled:
            with open(pulled[0], "rb") as f1, open(src2, "rb") as f2:
                check("pulled file content intact", f1.read() == f2.read())
        else:
            check("pulled file content intact", False)
        check("B executed the queued remote command",
              ("s1", "queued command", True) in b.sent)
        # 第二次 poll 帶 cursor → A 端 outbox 清掉、暫存檔刪除
        b.link.poll_once(cursors)
        staged_after = list((a.link._files_dir / b.fid).glob("*"))
        out_after = a.link._outbox.load().get(b.fid, {}).get("events", [])
        check("confirmed events pruned from A outbox", out_after == [])
        check("staged file removed after confirmation", staged_after == [])

        # ── unpair ──
        a.link.unpair(b.fid)
        check("unpair removes peer", b.fid not in a.link.peers())
        st, _ = signed_get(b, peer_a_from_b, "/link/ping")
        check("unpaired peer can no longer talk", st == 403)

        # ── helpers ──
        check("normalize_code strips separators",
              frame_link.normalize_code(" k7qx2-mrd34 ") == "K7QX2MRD34")
        check("generated code uses safe alphabet", all(
            c in frame_link._CODE_ALPHABET
            for c in frame_link.normalize_code(frame_link.generate_code())))
    finally:
        a.cleanup()
        b.cleanup()

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
