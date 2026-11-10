#!/usr/bin/env python3
"""Push to the paired phones (sf_push): what goes to Apple, once, and what Apple's answers do.

No network and no real key: the key is made here and thrown away, and the transport is a
fake that answers like APNs. The JWT is checked by verifying its signature with openssl.

Run: .venv/bin/python tests_push.py
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = Path(tempfile.mkdtemp(prefix="sf-push-test-"))
os.environ["SHELLFRAME_PUSH_FILE"] = str(TMP / "push.json")

import sf_push as P  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


OPENSSL = P.tools()[0]
KEY = TMP / "AuthKey_TEST000001.p8"
PUB = TMP / "pub.pem"
subprocess.run(f'"{OPENSSL}" ecparam -name prime256v1 -genkey -noout | "{OPENSSL}" pkcs8 -topk8 -nocrypt -out "{KEY}"',
               shell=True, check=True, capture_output=True)
subprocess.run([OPENSSL, "ec", "-in", str(KEY), "-pubout", "-out", str(PUB)], check=True, capture_output=True)
TOKEN_A = "a1" * 32
TOKEN_B = "b2" * 32
FRAME = "f" * 32


def unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def raw_to_der(raw):
    def integer(b):
        b = b.lstrip(b"\x00") or b"\x00"
        if b[0] & 0x80:
            b = b"\x00" + b
        return b"\x02" + bytes([len(b)]) + b
    body = integer(raw[:32]) + integer(raw[32:])
    return b"\x30" + bytes([len(body)]) + body


print("JWT")
jwt = P.make_jwt(str(KEY), "TEST000001", "TEAM000001", now=1_800_000_000)
h, c, s = jwt.split(".")
check("header says ES256 and the key id", json.loads(unb64(h)) == {"alg": "ES256", "kid": "TEST000001"})
check("claims carry the team id and the time", json.loads(unb64(c)) == {"iss": "TEAM000001", "iat": 1_800_000_000})
sig = unb64(s)
(TMP / "sig.der").write_bytes(raw_to_der(sig))
(TMP / "input").write_bytes(f"{h}.{c}".encode())
v = subprocess.run([OPENSSL, "dgst", "-sha256", "-verify", str(PUB), "-signature", str(TMP / "sig.der"),
                    str(TMP / "input")], capture_output=True, text=True)
check("the signature is 64 bytes r||s and openssl verifies it", len(sig) == 64 and "Verified OK" in v.stdout,
      v.stdout + v.stderr)
padded = b"\x30\x45\x02\x21\x00" + b"\x81" * 32 + b"\x02\x20" + b"\x01" * 32
short = b"\x30\x26\x02\x01\x05\x02\x21\x00" + b"\x90" * 32
check("DER → r||s strips a leading zero", P.der_to_raw(padded) == b"\x81" * 32 + b"\x01" * 32)
check("…and pads a short integer to 32 bytes", P.der_to_raw(short) == b"\x00" * 31 + b"\x05" + b"\x90" * 32)
calls = []
P.drop_jwt()
cfg = {"key_path": str(KEY), "key_id": "TEST000001", "team_id": "TEAM000001"}
sign = lambda k, d: calls.append(1) or P.openssl_sign(k, d)
t1 = P.jwt_for(cfg, now=1000.0, sign=sign)
t2 = P.jwt_for(cfg, now=1000.0 + 30 * 60, sign=sign)
t3 = P.jwt_for(cfg, now=1000.0 + 41 * 60, sign=sign)
check("the provider token is reused for 40 minutes, then renewed (Apple: 20–60)",
      t1 == t2 and t3 != t1 and len(calls) == 2, str(len(calls)))

print("setup and availability")
check("no key yet: says how to set it up", "push-setup" in P.availability())
check("setup refuses a key id that is not 10 characters", not P.setup(str(KEY), "short", "TEAM000001")["success"])
os.chmod(KEY, 0o644)
check("setup saves", P.setup(str(KEY), "TEST000001", "TEAM000001", "com.example.app")["success"])
check("…and makes a key readable by others private", (os.stat(KEY).st_mode & 0o777) == 0o600,
      oct(os.stat(KEY).st_mode & 0o777))
check("with a key, this computer can send", P.availability() == "", P.availability())
check("push.json is 0600", (os.stat(P.config_path()).st_mode & 0o777) == 0o600)

print("registration")
bad = P.register("peerA", {"token": "nothex", "env": "sandbox"})
check("a token that is not hex is refused", not bad["success"])
check("an unknown env is refused", not P.register("peerA", {"token": TOKEN_A, "env": "beta"})["success"])
r = P.register("peerA", {"token": TOKEN_A, "env": "sandbox", "topic": "com.example.app",
                         "device_name": "iPad   mini", "prefs": {"kinds": ["decision", "stuck", "bogus"],
                                                                 "telegram": "both", "x": 1}})
check("register answers registered and enabled", r == {"success": True, "details": {"registered": True, "enabled": True}}, str(r))
dev = P.devices()["peerA"]
check("prefs are cleaned (unknown kinds and keys dropped, defaults filled)",
      dev["prefs"] == {"kinds": ["decision", "stuck"], "hide_content": False, "show_computer": False,
                       "telegram": "both", "done": "primary"}, str(dev["prefs"]))
check("the device name is tidied", dev["device_name"] == "iPad mini")
P.register("peerA", {"token": TOKEN_B, "env": "production"})
check("one registration per phone: a new one replaces it", P.devices()["peerA"]["token"] == TOKEN_B
      and P.devices()["peerA"]["env"] == "production" and len(P.devices()) == 1)
st = P.status("peerA")
check("status reports enabled, registered, env and prefs",
      st["enabled"] and st["registered"] and st["env"] == "production" and st["prefs"]["kinds"] == list(P.KINDS), str(st))
check("unregister removes it", P.unregister("peerA")["success"] and "peerA" not in P.devices())
check("status after that: not registered", P.status("peerA")["registered"] is False)

print("payloads")
prefs = P.clean_prefs({})
hd, pl = P.build("decision", frame_id=FRAME, frame_name="Mac", sid="s101", bot="小幫手", text="先做哪一件？",
                 qid="4e8a9c7d0fe5", options=[(1, "先排卡片"), (2, "先整理規格")], prefs=prefs,
                 topic="com.example.app", now=1000)
check("decision: title, body with options, category for two buttons",
      pl["aps"]["alert"]["title"].startswith("小幫手｜") and pl["aps"]["alert"]["body"] == "先做哪一件？\n1. 先排卡片\n2. 先整理規格"
      and pl["aps"]["category"] == "SF_DECISION_2", json.dumps(pl, ensure_ascii=False))
check("decision: sf carries qid and options for /link/answer",
      pl["sf"]["qid"] == "4e8a9c7d0fe5" and pl["sf"]["options"][1] == {"n": 2, "text": "先整理規格"})
check("decision headers: alert, priority 10, collapse q.<qid>, topic, expiry +6 h",
      hd == {"apns-push-type": "alert", "apns-topic": "com.example.app", "apns-priority": "10",
             "apns-collapse-id": "q.4e8a9c7d0fe5", "apns-expiration": str(1000 + 6 * 3600)}, str(hd))
check("one thread per bot on each computer", pl["aps"]["thread-id"] == f"{FRAME}:s101")
for n, want in ((1, "SF_OPEN"), (3, "SF_DECISION_3"), (4, "SF_DECISION_4"), (6, "SF_OPEN")):
    _, p = P.build("decision", frame_id=FRAME, sid="s1", bot="b", qid="q", options=[(i, f"o{i}") for i in range(1, n + 1)])
    check(f"{n} option(s) → {want}", p["aps"]["category"] == want, p["aps"]["category"])
_, p = P.build("decision", frame_id=FRAME, sid="s1", bot="b", qid="q", options=[(i, f"o{i}") for i in range(1, 7)])
check("more than four options: four in the body", len(p["aps"]["alert"]["body"].splitlines()) == 4, p["aps"]["alert"]["body"])
_, hid = P.build("decision", frame_id=FRAME, sid="s1", bot="小幫手", text="公司的機密題目", qid="q",
                 options=[(1, "甲"), (2, "乙")], prefs={"hide_content": True})
check("hide_content: no question, no options, only Open",
      "機密" not in json.dumps(hid, ensure_ascii=False) and "options" not in hid["sf"]
      and hid["aps"]["category"] == "SF_OPEN", json.dumps(hid, ensure_ascii=False))
_, sub = P.build("stuck", frame_id=FRAME, frame_name="公司 Mac", sid="s2", bot="寫程式", text="等權限",
                 prefs={"show_computer": True})
check("show_computer puts the computer under the title", sub["aps"]["alert"].get("subtitle") == "公司 Mac")
hs, ps = P.build("stuck", frame_id=FRAME, sid="s2", bot="寫程式", text="x")
check("stuck: priority 10, collapse stuck.<sid>, category SF_STUCK",
      hs["apns-priority"] == "10" and hs["apns-collapse-id"] == "stuck.s2" and ps["aps"]["category"] == "SF_STUCK")
hdn, pdn = P.build("done", frame_id=FRAME, sid="s2", bot="寫程式", text="好了", silent=True)
check("done: priority 5, quiet replacement has no sound",
      hdn["apns-priority"] == "5" and "sound" not in pdn["aps"] and pdn["aps"]["category"] == "SF_DONE")
_, pin = P.build("info", frame_id=FRAME, sid="s3", bot="研究員", text="報告寫好了")
check("info: the title is the bot", pin["aps"]["alert"]["title"] == "研究員" and pin["aps"]["category"] == "SF_INFO")
_, long_ = P.build("stuck", frame_id=FRAME, sid="s2", bot="b", text="字" * 500)
check("the body stays within 180 characters", len(long_["aps"]["alert"]["body"]) <= 180)
_, big = P.build("decision", frame_id=FRAME, sid="s1", bot="b", qid="q",
                 options=[(i, "長" * 400) for i in range(1, 10)])
check("the whole payload stays within 4 KB", len(json.dumps(big, ensure_ascii=False).encode()) <= 4096)
hw, pw = P.build_withdraw(frame_id=FRAME, ids=["q.abc"], topic="com.example.app")
check("withdraw is a background push without a collapse id",
      hw == {"apns-push-type": "background", "apns-topic": "com.example.app", "apns-priority": "5"}
      and pw == {"aps": {"content-available": 1}, "sf": {"v": 1, "kind": "withdraw", "frame": FRAME, "ids": ["q.abc"]}})

print("curl")
conf = P.curl_config("https://api.sandbox.push.apple.com/3/device/x", {"authorization": 'bearer a"b\\c', "apns-topic": ""})
check("the config quotes values and leaves out empty headers",
      'header = "authorization: bearer a\\"b\\\\c"' in conf and "apns-topic" not in conf, conf)
seen = {}
real_run = subprocess.run


def fake_run(args, **kw):
    seen["argv"], seen["stdin"] = args, kw.get("input")
    return subprocess.CompletedProcess(args, 0, stdout=b'{"reason":"BadDeviceToken"}\n400', stderr=b"")


P.subprocess.run = fake_run
code, reason = P.curl_transport("https://h/3/device/t", {"authorization": "bearer SECRETJWT"}, b"{}")
P.subprocess.run = real_run
check("the bearer goes through stdin, never argv",
      "SECRETJWT" not in " ".join(seen["argv"]) and b"SECRETJWT" in seen["stdin"] and "--http2" in seen["argv"])
check("Apple's status and reason are read back", (code, reason) == (400, "BadDeviceToken"), str((code, reason)))
check("an empty 200 reply parses", P.parse_reply("\n200") == (200, ""))

print("sending")


class Fake:
    def __init__(self):
        self.calls, self.replies = [], []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, json.loads(body)))
        return self.replies.pop(0) if self.replies else (200, "")


now = [10_000.0]
fake = Fake()
logs = []
push = P.Pusher(log=lambda c, m: logs.append(m), redact=lambda t: str(t).replace("sk-live-123", "[redacted]"),
                transport=fake, sign=lambda k, d: P.openssl_sign(k, d), spawn=lambda fn: fn(), clock=lambda: now[0])
P.register("phone", {"token": TOKEN_A, "env": "sandbox", "topic": "com.example.app"})
P.register("ipad", {"token": TOKEN_B, "env": "sandbox", "topic": "com.example.app",
                    "prefs": {"kinds": ["decision"], "show_computer": True}})
n = push.send("decision", sid="s1", bot="小幫手", frame_id=FRAME, frame_name="Mac", text="選哪個？ key sk-live-123",
              qid="q1", options=[(1, "甲"), (2, "乙")])
check("a decision goes to both devices", n == 2 and len(fake.calls) == 2, str(n))
check("…to the sandbox host for the device's token, with a bearer",
      fake.calls[0][0] == f"https://api.sandbox.push.apple.com/3/device/{TOKEN_A}"
      and fake.calls[0][1]["authorization"].startswith("bearer "))
check("credentials in the text are redacted before they leave",
      all("sk-live-123" not in json.dumps(c[2], ensure_ascii=False) for c in fake.calls))
check("the log masks the token and has no content",
      any("a1a1a1…a1a1" in m for m in logs) and not any("選哪個" in m for m in logs), str(logs))
check("the same decision is not pushed twice", push.send("decision", sid="s1", bot="b", frame_id=FRAME, qid="q1",
                                                         options=[(1, "甲"), (2, "乙")]) == 0)
again = P.Pusher(transport=fake, spawn=lambda fn: fn(), clock=lambda: now[0])
check("…not even by a fresh process after a restart",
      again.send("decision", sid="s1", bot="b", frame_id=FRAME, qid="q1", options=[(1, "甲")]) == 0)
fake.calls.clear()
check("stuck reaches only the devices that want it", push.send("stuck", sid="s2", bot="b", frame_id=FRAME, text="x") == 1)
now[0] += 20
push.send("stuck", sid="s2", bot="b", frame_id=FRAME, text="y")
check("a second stuck within a minute replaces it quietly",
      "sound" in fake.calls[0][2]["aps"] and "sound" not in fake.calls[1][2]["aps"]
      and fake.calls[1][1]["apns-collapse-id"] == "stuck.s2")
fake.calls.clear()
check("done is only the primary bot's by default", push.send("done", sid="s3", bot="b", frame_id=FRAME) == 0
      and push.send("done", sid="s3", bot="b", frame_id=FRAME, primary=True) == 1)
P.register("phone", {"token": TOKEN_A, "env": "sandbox", "prefs": {"done": "all"}})
check("…or every bot when the phone asks for it", push.send("done", sid="s4", bot="b", frame_id=FRAME) == 1)
check("a phone that is no longer paired loses its registration",
      push.send("info", sid="s5", bot="b", frame_id=FRAME, paired={"phone"}) == 1 and "ipad" not in P.devices())

print("Apple's answers")
P.register("ipad", {"token": TOKEN_B, "env": "sandbox"})
fake.replies = [(200, "")]
push.send("info", sid="s6", bot="b", frame_id=FRAME, exclude={"ipad"})
check("a success records when, clears the error", P.devices()["phone"]["ok_at"] == now[0]
      and P.devices()["phone"]["last_error"] == "")
check("…so a quiet Telegram is allowed", P.telegram_quiet(now=now[0]))
fake.replies = [(410, "Unregistered")]
push.send("info", sid="s7", bot="b", frame_id=FRAME, exclude={"phone"})
check("410: the registration is removed", "ipad" not in P.devices())
P.register("ipad", {"token": TOKEN_B, "env": "sandbox"})
fake.replies = [(400, "BadDeviceToken")]
push.send("info", sid="s8", bot="b", frame_id=FRAME, exclude={"phone"})
check("400 BadDeviceToken: removed too", "ipad" not in P.devices())
P.drop_jwt()
P.jwt_for(P.load(), now=now[0])
fake.replies = [(403, "ExpiredProviderToken")]
push.send("info", sid="s9", bot="b", frame_id=FRAME)
check("403 on the provider token: it is renewed next time, the error is kept",
      P._jwt["token"] == "" and "ExpiredProviderToken" in P.devices()["phone"]["last_error"])
check("an error since the last success makes Telegram ring again", not P.telegram_quiet(now=now[0]))
fake.replies = [(429, "TooManyRequests")]
push.send("info", sid="s10", bot="b", frame_id=FRAME)
fake.calls.clear()
check("429: the device is left alone for a while",
      push.send("info", sid="s11", bot="b", frame_id=FRAME) == 0 and not fake.calls)
now[0] += 3700
fake.replies = []
sent = sum(push.send("info", sid=f"t{i}", bot="b", frame_id=FRAME) for i in range(40))
check(f"at most {P.DEVICE_HOUR_MAX} an hour per device", sent == P.DEVICE_HOUR_MAX, str(sent))
check("withdraw reaches every device but the one that answered",
      push.withdraw(["q.q1"], frame_id=FRAME, exclude={"phone"}) == 0)
P.unregister("phone")
P.update(lambda c: c.pop("key_id"))
check("without a key nothing is sent", push.send("info", sid="s1", bot="b", frame_id=FRAME) == 0)

print("what a key check can and cannot tell")
v, m = P.key_check(400, "BadDeviceToken", "sandbox")
check("BadDeviceToken for a made-up device: the key itself is accepted…", v == "ok", m)
check("…and the output says a topic restriction is invisible here, and how to be sure",
      "another App ID" in m and "/link/push/test" in m, m)
v, m = P.key_check(400, "TopicDisallowed", "sandbox")
check("TopicDisallowed is a failure: this key may not push the app", v == "fail" and "may not push" in m, m)
v, m = P.key_check(403, "BadEnvironmentKeyInToken", "production")
check("BadEnvironmentKeyInToken: the key lacks that environment", v == "fail" and "environment" in m, m)
v, m = P.key_check(403, "InvalidProviderToken", "sandbox")
check("InvalidProviderToken: check the Key ID, team id and key file", v == "fail" and "Key ID" in m, m)
check("no answer at all is reported as unreadable, not as a pass", P.key_check(0, "", "sandbox")[0] == "unknown")
P.update(lambda c: c.update(key_id="TEST000001"))
P.register("phone", {"token": TOKEN_A, "env": "sandbox", "topic": "com.example.app"})
fake.replies = [(400, "TopicDisallowed")]
now[0] += 7200
push.send("info", sid="s20", bot="b", frame_id=FRAME)
dev = P.devices().get("phone") or {}
check("a real send refused for its topic keeps the phone's registration (the key is wrong, not the phone)",
      bool(dev), str(P.devices()))
check("…and the error says why, for the app's settings page", "may not push" in dev.get("last_error", ""),
      dev.get("last_error"))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
