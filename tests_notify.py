#!/usr/bin/env python3
"""可選的外送 webhook 通知（sf_notify.py、`sfctl notify`、api_status 的 auto-notify）。

守住的事：
  1. 沒設定＝完全靜默：不連網、不印字、不寫 log、退出碼 0。
  2. 秘密不外漏：URL 的 path/query 與 Authorization 值不會出現在任何印出或
     記錄的字串裡（測試用 SECRET* 字樣的假值逐一比對）。
  3. 不擋呼叫端：逾時、被拒、5xx、轉址一律回 (False, 簡短原因)，不丟例外；
     轉址不跟（Authorization 不能被帶去別處）。
  4. auto-notify 只在「真的轉換」時送：重啟不洗版、done→idle→done 不重送、
     沒跑過的 idle→done 不送。

所有碰設定檔的測試都在暫存 HOME 底下；mock endpoint 只綁 127.0.0.1 的暫時埠；
CLI 測試一律帶 --label（或不帶 sid），不走 sfctl 的 _rpc，不碰執行中的 app。

跑法：.venv/bin/python tests_notify.py
"""
import argparse
import ast
import contextlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 整支測試的 HOME 指到暫存目錄：就算哪個呼叫忘了傳 path，也讀不到真的設定檔。
TMP = Path(tempfile.mkdtemp(prefix="sfnotify-"))
HOME = TMP / "home"
HOME.mkdir()
os.environ["HOME"] = os.environ["USERPROFILE"] = str(HOME)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

import sf_notify as N  # noqa: E402

for _k in (N.ENV_URL, N.ENV_AUTH, "SF_SID"):
    os.environ.pop(_k, None)

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def section(title):
    print(f"\n{title}")


SECRET_PATH = "/hook/SECRETPATH123"
SECRET_Q = "key=SECRETQ456"
SECRET_AUTH = "Bearer SECRETTOKEN789"


def clean(*texts):
    """No SECRET* fragment survives in any of the strings."""
    return all("SECRET" not in t for t in texts)


# ── mock endpoint ──────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        srv = self.server
        srv.hits.append({"path": self.path, "body": body,
                         "headers": {k.lower(): v for k, v in self.headers.items()}})
        if srv.delay:
            time.sleep(srv.delay)
        try:
            if srv.redirect_to:
                self.send_response(302)
                self.send_header("Location", srv.redirect_to)
            else:
                self.send_response(srv.code)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except OSError:
            pass                      # the client already gave up (timeout test)


class Mock:
    def __init__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.srv.daemon_threads = True
        self.srv.handle_error = lambda *a: None
        self.srv.hits, self.srv.code, self.srv.delay, self.srv.redirect_to = [], 200, 0, ""
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def url(self, path=SECRET_PATH, query=SECRET_Q):
        return f"http://127.0.0.1:{self.port}{path}" + (f"?{query}" if query else "")

    @property
    def hits(self):
        return self.srv.hits

    def reset(self, code=200, delay=0, redirect_to=""):
        self.srv.hits.clear()
        self.srv.code, self.srv.delay, self.srv.redirect_to = code, delay, redirect_to

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


_n = [0]


def write_cfg(obj, mode=0o600, name=None):
    """A config file in its own temp dir. -> path"""
    _n[0] += 1
    d = TMP / f"cfg{_n[0]}"
    d.mkdir()
    p = d / (name or "notify.json")
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    os.chmod(p, mode)
    return str(p)


def load(path, env=None):
    warnings = []
    cfg = N.load_config(environ=env or {}, path=path, warn=warnings.append)
    return cfg, warnings


mock = Mock()
URL = mock.url()

# ── load_config ────────────────────────────────────────────────────────────
section("load_config")
cfg, w = load(str(TMP / "does-not-exist.json"))
check("沒有檔案也沒有 env → None，且沒有任何警告", cfg is None and w == [], str((cfg, w)))

p = write_cfg({"url": URL, "auth": SECRET_AUTH})
cfg, w = load(p)
check("只有檔案 → url/auth 讀得到", cfg and cfg["url"] == URL and cfg["auth"] == SECRET_AUTH, str(cfg))
check("auto 預設 False（要明確開）", cfg["auto"] is False)
check("tabs/labels 預設空", cfg["tabs"] == [] and cfg["labels"] == [])
check("0600 的檔案沒有警告", w == [], str(w))

p = write_cfg({"url": URL, "auth": SECRET_AUTH, "auto": True, "tabs": ["s3"], "labels": ["build-*"]})
cfg, _ = load(p)
check("auto/tabs/labels 照檔案走",
      cfg["auto"] is True and cfg["tabs"] == ["s3"] and cfg["labels"] == ["build-*"], str(cfg))

cfg, _ = load(p, {N.ENV_URL: "https://example.com/hook", N.ENV_AUTH: "Bearer <token>"})
check("env 同時蓋掉 url 與 auth",
      cfg["url"] == "https://example.com/hook" and cfg["auth"] == "Bearer <token>", str(cfg))
check("env 只蓋 url 時，auth 仍來自檔案",
      load(p, {N.ENV_URL: "https://example.com/hook"})[0]["auth"] == SECRET_AUTH)
check("env 只蓋 auth 時，url 仍來自檔案",
      load(p, {N.ENV_AUTH: "Bearer other"})[0]["url"] == URL)
check("env 蓋掉 url 但檔案的 auto 仍有效",
      load(p, {N.ENV_URL: "https://example.com/hook"})[0]["auto"] is True)

cfg, w = load(str(TMP / "none.json"), {N.ENV_URL: "https://example.com/hook"})
check("只有 env → 可用，auth 空、auto False",
      cfg == {"url": "https://example.com/hook", "auth": "", "auto": False, "tabs": [], "labels": []}, str(cfg))
cfg, _ = load(p, {N.ENV_URL: "", N.ENV_AUTH: ""})
check("空字串 env 視為沒設（不會蓋掉檔案）", cfg["url"] == URL and cfg["auth"] == SECRET_AUTH, str(cfg))
check("只有空字串 env、沒有檔案 → None",
      load(str(TMP / "none.json"), {N.ENV_URL: "", N.ENV_AUTH: "Bearer x"})[0] is None)

bad = write_cfg('{"url": "https://example.com/hook", SECRETJUNK')
warnings = []
try:
    cfg = N.load_config(environ={}, path=bad, warn=warnings.append)
    raised = False
except Exception:
    cfg, raised = 1, True
N.load_config(environ={}, path=bad, warn=warnings.append)
check("壞 JSON → None、不丟例外", cfg is None and not raised)
check("壞 JSON 只警告一次，且不含檔案內容", len(warnings) == 1 and clean(*warnings), str(warnings))
check("壞 JSON 時 env 仍可單獨啟用",
      load(bad, {N.ENV_URL: "https://example.com/hook"})[0]["url"] == "https://example.com/hook")

for label, obj in [("url 不是字串", {"url": 123}), ("auto 不是布林", {"url": URL, "auto": "yes"}),
                   ("tabs 不是字串清單", {"url": URL, "tabs": [1, 2]}), ("最外層不是物件", ["x"]),
                   ("auth 不是字串", {"url": URL, "auth": ["Bearer x"]})]:
    cfg, w = load(write_cfg(obj))
    check(f"型別錯誤（{label}）→ 整個檔案當作不存在，警告一次",
          cfg is None and len(w) == 1 and clean(*w), str((cfg, w)))

for scheme_url in ("ftp://example.com/x", "file:///etc/hosts", "example.com/hook", "http:///nohost"):
    cfg, w = load(write_cfg({"url": scheme_url}))
    check(f"只收 http/https：{scheme_url!r} → None＋警告，警告不含網址",
          cfg is None and len(w) == 1 and scheme_url not in w[0], str((cfg, w)))
check("https 可用", load(write_cfg({"url": "https://example.com/hook"}))[0] is not None)

if os.name == "posix":
    loose = write_cfg({"url": URL}, mode=0o644)
    warnings = []
    c1 = N.load_config(environ={}, path=loose, warn=warnings.append)
    N.load_config(environ={}, path=loose, warn=warnings.append)
    check("0644 → 警告（不拒絕）：設定照樣載入", c1 is not None and c1["url"] == URL)
    check("警告提到 chmod 600、同一個檔案版本只講一次",
          len(warnings) == 1 and "chmod 600" in warnings[0], str(warnings))
    os.utime(loose, (time.time() + 5, time.time() + 5))
    N.load_config(environ={}, path=loose, warn=warnings.append)
    check("檔案改過（mtime 變）才會再警告一次", len(warnings) == 2, str(warnings))
    check("0640 也算（group 讀得到）", len(load(write_cfg({"url": URL}, mode=0o640))[1]) == 1)
    check("0600 沒有警告", load(write_cfg({"url": URL}, mode=0o600))[1] == [])
check("沒給 warn callback 也不出事（靜默）",
      N.load_config(environ={}, path=write_cfg({"url": URL}, mode=0o644)) is not None)

check("config_path() 跟著 HOME 走（呼叫時才展開）",
      N.config_path() == str(HOME / ".config" / "shellframe" / "notify.json"), N.config_path())
check("不給 path 時讀的是暫存 HOME 底下的檔案（目前不存在 → None）", N.load_config(environ={}) is None)

# ── 遮蔽 ───────────────────────────────────────────────────────────────────
section("mask_url / mask_auth / scrub")
full = f"https://user1:pass1234@example.com:8443{SECRET_PATH}?{SECRET_Q}#frag9"
check("mask_url 留 scheme://host:port，其餘變 /***",
      N.mask_url(full) == "https://example.com:8443/***", N.mask_url(full))
check("mask_url 丟掉 userinfo", "user1" not in N.mask_url(full) and "pass1234" not in N.mask_url(full))
check("mask_url 沒有 path/query 就只有 host", N.mask_url("https://example.com") == "https://example.com")
check("mask_url 根路徑 / 視為沒有 path", N.mask_url("http://127.0.0.1:9/") == "http://127.0.0.1:9")
check("mask_url 只有 query 也要遮", N.mask_url("https://example.com?k=SECRETQ456") == "https://example.com/***")
check("mask_url 不含任何 SECRET", clean(N.mask_url(URL), N.mask_url(full)))
check("mask_url 壞輸入不丟例外", isinstance(N.mask_url(None), str) and isinstance(N.mask_url("http://[::1"), str))
check("mask_auth 空 → (none)", N.mask_auth("") == "(none)" and N.mask_auth(None) == "(none)")
check("mask_auth 只留字數", N.mask_auth(SECRET_AUTH) == f"*** ({len(SECRET_AUTH)} chars)" and clean(N.mask_auth(SECRET_AUTH)))

sc = {"url": full, "auth": SECRET_AUTH}
dirty = (f"POST {full} failed; header {SECRET_AUTH}; token SECRETTOKEN789; path {SECRET_PATH}; "
         f"bare {SECRET_PATH.lstrip('/')}; q {SECRET_Q}; who user1:pass1234 frag9")
out = N.scrub(dirty, sc)
check("scrub 去掉網址、path、query、token、userinfo",
      clean(out) and "pass1234" not in out and "user1" not in out and "frag9" not in out, out)
check("scrub 留下無關文字", "failed" in out and "***" in out)
check("scrub 沒有設定時原樣回傳", N.scrub("HTTP 500", None) == "HTTP 500" and N.scrub("x", {}) == "x")
check("scrub 不會因為根路徑 / 把整串打爛",
      N.scrub("a/b", {"url": "https://example.com/"}) == "a/b")
check("scrub 認得沒有 scheme 字的裸 token", clean(N.scrub("tok SECRETBARE1", {"url": URL, "auth": "SECRETBARE1"})))

# ── payload ────────────────────────────────────────────────────────────────
section("build_payload")
pl = N.build_payload("done", "hello 世界", sid="s3", label="build-1")
check("欄位剛好這七個",
      set(pl) == {"machine", "sid", "label", "status", "message", "ts", "shellframe_version"}, str(sorted(pl)))
check("status 轉大寫", pl["status"] == "DONE" and N.build_payload("Blocked", "x")["status"] == "BLOCKED")
check("sid/label/message 原樣", (pl["sid"], pl["label"], pl["message"]) == ("s3", "build-1", "hello 世界"))
check("ts 是帶時區偏移的 ISO-8601",
      re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", pl["ts"]) is not None, pl["ts"])
check("給定 now 就用它", N.build_payload("INFO", "x", now=datetime(2026, 1, 2, 3, 4, 5))["ts"].startswith("2026-01-02T03:04:05"))
check("machine 是本機 hostname", pl["machine"] == socket.gethostname())
ver = json.loads((HERE / "version.json").read_text(encoding="utf-8"))["version"]
check("shellframe_version 來自 version.json", pl["shellframe_version"] == ver, pl["shellframe_version"])
check("訊息超過 4000 字會截斷", len(N.build_payload("INFO", "x" * 5000)["message"]) == 4000)
check("sid/label 預設空字串", N.build_payload("INFO", "x")["sid"] == "" and N.build_payload("INFO", "x")["label"] == "")
check("payload 可序列化", bool(json.dumps(pl)))
check("STATUSES 固定四個", N.STATUSES == ("DONE", "BLOCKED", "QUESTION", "INFO"))

# ── send ───────────────────────────────────────────────────────────────────
section("send")
cfg = {"url": URL, "auth": SECRET_AUTH}
body = N.build_payload("done", "完成 ✓ ünïcode", sid="s1", label="L")
mock.reset()
ok, info = N.send(body, cfg)
h = mock.hits[0] if mock.hits else {"path": "", "headers": {}, "body": b""}
check("200 → ok", ok is True and info == "HTTP 200", str((ok, info)))
check("path 與 query 原樣送到", h["path"] == f"{SECRET_PATH}?{SECRET_Q}", h["path"])
check("body 是 UTF-8 JSON 且等於 payload", json.loads(h["body"].decode("utf-8")) == body)
check("Authorization 與設定值一字不差", h["headers"].get("authorization") == SECRET_AUTH)
check("Content-Type 是 application/json", h["headers"].get("content-type") == "application/json")
check("User-Agent 帶版本", h["headers"].get("user-agent") == f"ShellFrame/{ver}", h["headers"].get("user-agent"))
mock.reset(code=204)
check("204 也算成功", N.send(body, cfg)[0] is True)
mock.reset()
ok, _ = N.send(body, {"url": URL, "auth": ""})
check("auth 空 → 不送 Authorization 標頭", ok and "authorization" not in mock.hits[0]["headers"])
mock.reset(code=500)
ok, info = N.send(body, cfg)
check("500 → not ok，原因只有狀態碼", ok is False and info == "HTTP 500" and clean(info), str((ok, info)))
mock.reset(code=401)
check("4xx → not ok", N.send(body, cfg)[0] is False)

mock.reset(delay=2.5)
t0 = time.time()
ok, info = N.send(body, cfg, timeout=0.5)
dt = time.time() - t0
check("慢伺服器：timeout=0.5 在 2 秒內放棄、不丟例外", ok is False and dt < 2.0, f"{dt:.2f}s {info}")
check("逾時原因是 timeout 且不含秘密", info == "timeout" and clean(info), info)


# 每 0.2 秒吐一個位元組：每次 socket 讀取都不會超過 0.5 秒，只有整體期限擋得住。
def _trickle_server():
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)

    def serve():
        try:
            c, _ = ls.accept()
            c.recv(65536)
            for b in b"HTTP/1.1 200 OK\r\nX-Pad: " + b"a" * 200:
                c.sendall(bytes([b]))
                time.sleep(0.2)
        except OSError:
            pass
    threading.Thread(target=serve, daemon=True).start()
    return ls


trickle = _trickle_server()
t0 = time.time()
ok, info = N.send(body, {"url": f"http://127.0.0.1:{trickle.getsockname()[1]}{SECRET_PATH}",
                         "auth": SECRET_AUTH}, timeout=0.5)
dt = time.time() - t0
check("一點一點吐的伺服器：整體期限內放棄（timeout=0.5 → 1.5 秒內）",
      ok is False and dt < 1.5 and info == "timeout", f"{dt:.2f}s {info}")
trickle.close()

dead = {"url": f"http://127.0.0.1:{closed_port()}{SECRET_PATH}?{SECRET_Q}", "auth": SECRET_AUTH}
ok, info = N.send(body, dead, timeout=2.0)
check("連線被拒 → not ok，原因簡短且無秘密",
      ok is False and info == "connection refused" and clean(info), str((ok, info)))

target = Mock()
mock.reset(redirect_to=f"http://127.0.0.1:{target.port}/landed")
ok, info = N.send(body, cfg)
time.sleep(0.2)
check("302 → not ok（不跟轉址）", ok is False and "302" in info and clean(info), info)
check("轉址目標什麼都沒收到（Authorization 沒被轉送）", target.hits == [], str(target.hits))
check("原 endpoint 只被打一次", len(mock.hits) == 1)
target.close()

ok, info = N.send(body, {"url": URL, "auth": "Bearer a\nX-Evil: 1"})
check("標頭裡塞換行 → not ok、不丟例外", ok is False and clean(info), info)
ok, info = N.send(body, {})
check("設定殘缺 → not ok、不丟例外", ok is False)

# ── classify / TransitionTracker ───────────────────────────────────────────
section("classify")
check("done → DONE", N.classify({"state": "done", "blocked": ""}) == "DONE")
check("done 但有 blocked 原因 → BLOCKED", N.classify({"state": "done", "blocked": "trust this folder?"}) == "BLOCKED")
check("idle 但有 blocked 原因 → BLOCKED", N.classify({"state": "idle", "blocked": "Overwrite?"}) == "BLOCKED")
check("decision → BLOCKED", N.classify({"state": "decision"}) == "BLOCKED")
check("其他一律 None",
      all(N.classify({"state": s}) is None for s in ("working", "idle", "stuck", "unknown", "", None)))
check("壞輸入 → None", N.classify(None) is None and N.classify("x") is None and N.classify({}) is None)

section("TransitionTracker")


def R(state, blocked="", **kw):
    return {"state": state, "blocked": blocked, **kw}


def feed(tr, sid, *states):
    """觀察一串結果，回傳每一步的事件。state 可以是字串或 (state, blocked)。"""
    out = []
    for st in states:
        res = R(*st) if isinstance(st, tuple) else R(st)
        out.append(tr.observe(sid, res))
    return out


tr = N.TransitionTracker()
check("第一次看到 done → 只記基準、不送（重啟不洗版）", feed(tr, "a", "done") == [None])
check("基準是 done 之後再 done → 不送", feed(tr, "a", "done", "done") == [None, None])
tr = N.TransitionTracker()
check("第一次看到就在等人 → 不送", feed(tr, "a", ("idle", "trust this folder?")) == [None])
check("基準是 blocked，持續 blocked → 不送", feed(tr, "a", ("idle", "trust this folder?")) == [None])
tr = N.TransitionTracker()
check("working→done 送一次 DONE", feed(tr, "a", "idle", "working", "done", "done", "done") == [None, None, "DONE", None, None])
tr = N.TransitionTracker()
check("done→idle→done 不重送（吸收抖動）",
      feed(tr, "a", "working", "done", "idle", "done", "idle", "idle", "done") == [None, "DONE", None, None, None, None, None])
tr = N.TransitionTracker()
check("兩個回合送兩次", feed(tr, "a", "idle", "working", "done", "working", "done") == [None, None, "DONE", None, "DONE"])
tr = N.TransitionTracker()
check("全新分頁 idle→done（沒跑過）不送", feed(tr, "a", "idle", "done", "done") == [None, None, None])
tr = N.TransitionTracker()
check("開機對話框被關掉直接變 done 不送", feed(tr, "a", ("idle", "trust this folder?"), "done") == [None, None])
tr = N.TransitionTracker()
check("基準就在 working，之後 done 會送（app 重啟當下正在跑的回合）", feed(tr, "a", "working", "done") == [None, "DONE"])
tr = N.TransitionTracker()
check("working 中夾 stuck/unknown 不影響之後的 DONE",
      feed(tr, "a", "working", "stuck", "unknown", "", "done") == [None, None, None, None, "DONE"])
tr = N.TransitionTracker()
check("BLOCKED 只送一次", feed(tr, "a", "working", ("idle", "Overwrite?"), ("idle", "Overwrite?"), ("done", "Overwrite?")) == [None, "BLOCKED", None, None])
tr = N.TransitionTracker()
check("blocked→idle→blocked 不重送", feed(tr, "a", "working", ("idle", "Q?"), "idle", ("idle", "Q?")) == [None, "BLOCKED", None, None])
tr = N.TransitionTracker()
check("blocked→working→blocked 送兩次",
      feed(tr, "a", "working", ("idle", "Q?"), "working", ("idle", "Q?")) == [None, "BLOCKED", None, "BLOCKED"])
tr = N.TransitionTracker()
check("decision 算 BLOCKED，且只送一次", feed(tr, "a", "working", "decision", "decision") == [None, "BLOCKED", None])
tr = N.TransitionTracker()
check("基準 blocked→done（沒 armed）不送 DONE", feed(tr, "a", ("idle", "Q?"), "done") == [None, None])
tr = N.TransitionTracker()
check("跑過的回合：working→blocked→done 兩個都送", feed(tr, "a", "working", ("idle", "Q?"), "done") == [None, "BLOCKED", "DONE"])
tr = N.TransitionTracker()
check("各 sid 各自獨立",
      feed(tr, "a", "working") == [None] and feed(tr, "b", "done") == [None]
      and feed(tr, "a", "done") == ["DONE"] and feed(tr, "b", "done") == [None])
tr = N.TransitionTracker()
feed(tr, "a", "working")
tr.prune(["b"])
check("prune 後那個 sid 重新當成第一次看到（armed 沒有殘留）", feed(tr, "a", "done") == [None])
tr = N.TransitionTracker()
feed(tr, "a", "working")
tr.prune(["a", "b"])
check("prune 只清不在 live 的 sid", feed(tr, "a", "done") == ["DONE"])
tr = N.TransitionTracker()
check("壞輸入不丟例外", feed(tr, "a", "idle") == [None] and tr.observe("a", None) is None and tr.observe("z", None) is None)

# ── matches_filter ─────────────────────────────────────────────────────────
section("matches_filter")
check("沒有 tabs 也沒有 labels → 全部通過", N.matches_filter({}, "s1", "x") and N.matches_filter({"tabs": [], "labels": []}, "s1", ""))
check("tabs：在清單裡通過、不在被擋",
      N.matches_filter({"tabs": ["s3"]}, "s3", "x") and not N.matches_filter({"tabs": ["s3"]}, "s4", "x"))
check("labels 萬用字元", N.matches_filter({"labels": ["build-*"]}, "s9", "build-7")
      and not N.matches_filter({"labels": ["build-*"]}, "s9", "deploy"))
check("labels 不分大小寫（雙向）", N.matches_filter({"labels": ["BUILD-*"]}, "s9", "build-7")
      and N.matches_filter({"labels": ["build-*"]}, "s9", "Build-7"))
check("tabs 與 labels 是『或』", N.matches_filter({"tabs": ["s3"], "labels": ["build-*"]}, "s3", "zzz")
      and N.matches_filter({"tabs": ["s3"], "labels": ["build-*"]}, "s8", "build-2")
      and not N.matches_filter({"tabs": ["s3"], "labels": ["build-*"]}, "s8", "zzz"))
check("label 為空/None 不會爆", not N.matches_filter({"labels": ["a*"]}, "s1", None))
check("? 與 [] 也走 fnmatch", N.matches_filter({"labels": ["w?rker-[12]"]}, "s1", "worker-2"))

# ── AutoNotifier ───────────────────────────────────────────────────────────
section("AutoNotifier")
ACFG = {"url": URL, "auth": SECRET_AUTH, "auto": True, "tabs": [], "labels": []}


def make(cfg, sender=None, labels=None, **kw):
    calls, logs, loads = [], [], []

    def default_sender(payload, c):
        calls.append((payload, c))
        return True, "HTTP 200"

    def loader():
        loads.append(1)
        return cfg

    kw.setdefault("spawn", lambda fn: fn())
    n = N.AutoNotifier(loader=loader, sender=sender or default_sender,
                       log=lambda cat, msg: logs.append((cat, msg)), **kw)
    lab = labels or {}
    return n, calls, logs, loads, (lambda sid: lab.get(sid, sid))


def turn(n, label_of, sid="s1", *, task="", summary="", end="done", blocked=""):
    """working → end，各一個 pass。"""
    n.process({sid: R("working")}, label_of)
    n.process({sid: R(end, blocked, task=task, summary=summary)}, label_of)


n, calls, logs, loads, lab = make(None)
n.process({"s1": R("working")}, lab)
check("沒有轉換時根本不讀設定", loads == [])
n.process({"s1": R("done")}, lab)
check("沒設定 → sender 沒被呼叫、log 沒寫（完全靜默）", calls == [] and logs == [], str((calls, logs)))
check("轉換發生才讀一次設定", len(loads) == 1)

n, calls, logs, loads, lab = make(dict(ACFG, auto=False))
turn(n, lab)
check("auto=false → 不送、不寫 log", calls == [] and logs == [])

n, calls, logs, loads, lab = make(ACFG, labels={"s1": "build-1"})
turn(n, lab, task="Fix the flaky test", summary="ignored")
check("auto=true → 一次轉換呼叫一次 sender", len(calls) == 1, str(calls))
pl, c = calls[0] if calls else ({}, {})
check("payload 欄位正確",
      (pl.get("status"), pl.get("sid"), pl.get("label"), pl.get("message"))
      == ("DONE", "s1", "build-1", "Turn finished: Fix the flaky test"), str(pl))
check("sender 收到的是載入的設定", c is ACFG)
n.process({"s1": R("done")}, lab)
n.process({"s1": R("idle")}, lab)
n.process({"s1": R("done")}, lab)
check("同一個 done 反覆出現不重送", len(calls) == 1)
check("log 一行：分類 notify、含遮過的網址、沒有秘密",
      len(logs) == 1 and logs[0][0] == "notify" and "127.0.0.1" in logs[0][1]
      and "/***" in logs[0][1] and clean(logs[0][1]), str(logs))

n, calls, logs, loads, lab = make(ACFG)
turn(n, lab, summary="Wrapped up")
check("沒有 task 時用 summary", calls[0][0]["message"] == "Turn finished: Wrapped up", str(calls))
n, calls, logs, loads, lab = make(ACFG)
turn(n, lab)
check("task/summary 都沒有 → 只有 Turn finished", calls[0][0]["message"] == "Turn finished")
n, calls, logs, loads, lab = make(ACFG)
turn(n, lab, end="idle", blocked="Overwrite config.json?")
check("BLOCKED 訊息＝阻塞原因", (calls[0][0]["status"], calls[0][0]["message"]) == ("BLOCKED", "Overwrite config.json?"), str(calls))
n, calls, logs, loads, lab = make(ACFG)
turn(n, lab, end="decision")
check("decision 沒原因 → 預設訊息", calls[0][0]["message"] == "Waiting for a permission decision", str(calls))

n, calls, logs, loads, lab = make(ACFG, redact=lambda t: t.replace("hunter2", "<redacted>"))
turn(n, lab, task="login with hunter2 now")
check("redact 會先套用再送", "hunter2" not in calls[0][0]["message"] and "<redacted>" in calls[0][0]["message"], str(calls))
n, calls, logs, loads, lab = make(ACFG)
turn(n, lab, task="x" * 500)
check("訊息細節截到 200 字", len(calls[0][0]["message"]) == len("Turn finished: ") + 200, str(len(calls[0][0]["message"])))


def boom(_t):
    raise RuntimeError("redactor broke")


n, calls, logs, loads, lab = make(ACFG, redact=boom)
turn(n, lab, task="sk-live-secret-text")
check("redact 壞掉 → 不送原文（寧可少一段）",
      calls and calls[0][0]["message"] == "Turn finished" and "sk-live" not in json.dumps(calls[0][0]))

n, calls, logs, loads, lab = make(dict(ACFG, tabs=["s9"]))
turn(n, lab, sid="s1")
check("tabs 過濾：不在清單的分頁不送", calls == [] and logs == [])
turn(n, lab, sid="s9")
check("tabs 過濾：在清單的分頁會送", len(calls) == 1 and calls[0][0]["sid"] == "s9")
n, calls, logs, loads, lab = make(dict(ACFG, labels=["build-*"]), labels={"s1": "build-1", "s2": "other"})
turn(n, lab, sid="s1")
turn(n, lab, sid="s2")
check("labels 過濾：只送符合的標籤", [c[0]["sid"] for c in calls] == ["s1"], str(calls))

n, calls, logs, loads, lab = make(ACFG, sender=lambda p, c: (_ for _ in ()).throw(RuntimeError(c["url"] + c["auth"])))
try:
    turn(n, lab)
    raised = False
except Exception:
    raised = True
check("sender 丟例外不會傳出 process", raised is False)
check("丟例外時 log 只記型別、不含秘密", len(logs) == 1 and "failed" in logs[0][1] and clean(logs[0][1]), str(logs))
n, calls, logs, loads, lab = make(ACFG, sender=lambda p, c: (False, "HTTP 500"))
turn(n, lab)
check("送失敗 → log 記遮過的網址與原因", len(logs) == 1 and "HTTP 500" in logs[0][1] and "failed" in logs[0][1] and clean(logs[0][1]), str(logs))
n, calls, logs, loads, lab = make(ACFG, sender=lambda p, c: (False, f"oops {c['url']} {c['auth']}"))
turn(n, lab)
check("sender 回傳的原因含秘密也會被擦掉", clean(logs[0][1]), logs[0][1])

n, calls, logs, loads, _ = make(ACFG)
turn(n, lambda sid: 1 / 0)
check("label_of 壞掉 → label 空字串、照常送", len(calls) == 1 and calls[0][0]["label"] == "")
n, calls, logs, loads, lab = make(None)
try:
    n.process(None, None)
    n.process({"s1": None, "s2": "weird"}, lab)
    n.process({"s1": None}, None)
    raised = False
except Exception:
    raised = True
check("亂輸入不會讓 process 丟例外", raised is False)

# 同時送出的上限
pending = []
n, calls, logs, loads, lab = make(ACFG, spawn=pending.append)
sids = [f"s{i}" for i in range(1, 7)]
n.process({s: R("working") for s in sids}, lab)
n.process({s: R("done") for s in sids}, lab)
check("同時 4 個送出在飛，第 5、6 個丟掉", len(pending) == N.MAX_INFLIGHT == 4, str(len(pending)))
check("丟掉時各留一行遮過的 log", sum("dropped" in m for _, m in logs) == 2 and clean(*[m for _, m in logs]), str(logs))
for fn in list(pending):
    fn()
check("在飛的送完會釋放名額", len(calls) == 4)
pending.clear()
n.process({"s1": R("working")}, lab)
n.process({"s1": R("done")}, lab)
check("釋放後可再送", len(pending) == 1)

# 真的背景執行緒：process 不能被慢 sender 卡住
done_ev = threading.Event()


def slow_sender(payload, c):
    time.sleep(1.0)
    done_ev.set()
    return True, "HTTP 200"


n, calls, logs, loads, lab = make(ACFG, sender=slow_sender, spawn=None)
n.process({"s1": R("working")}, lab)
t0 = time.time()
n.process({"s1": R("done")}, lab)
dt = time.time() - t0
check("慢 sender 不會卡住 monitor（process 在 0.5 秒內返回）", dt < 0.5, f"{dt:.2f}s")
check("背景執行緒最後有送完", done_ev.wait(5.0))
for _ in range(50):
    if logs:
        break
    time.sleep(0.05)
check("背景送完後留下一行 log", len(logs) == 1 and clean(logs[0][1]), str(logs))

# 預設 loader：讀暫存 HOME 下的設定；壞檔的警告走 log、只一次
cfg_dir = HOME / ".config" / "shellframe"
cfg_dir.mkdir(parents=True)
(cfg_dir / "notify.json").write_text("{not json SECRETJUNK", encoding="utf-8")
logs, calls = [], []
n = N.AutoNotifier(sender=lambda p, c: calls.append(p) or (True, "ok"),
                   log=lambda cat, msg: logs.append(msg), spawn=lambda fn: fn())
lab = lambda sid: sid  # noqa: E731
turn(n, lab)
turn(n, lab)
check("預設 loader：壞設定 → 不送", calls == [])
check("預設 loader：警告進 log、同一版本只一次、不含檔案內容",
      len([m for m in logs if m.startswith("WARN")]) == 1 and clean(*logs), str(logs))
# 之後把檔案改好：不必重啟就生效
(cfg_dir / "notify.json").write_text(json.dumps({"url": URL, "auto": True}), encoding="utf-8")
os.chmod(cfg_dir / "notify.json", 0o600)
mock.reset()
real_calls = []
n2 = N.AutoNotifier(log=lambda cat, msg: real_calls.append(msg), spawn=lambda fn: fn())
turn(n2, lab, task="end to end")
check("預設 loader + 預設 sender：改檔後下一次轉換就送出（不必重啟）",
      len(mock.hits) == 1 and json.loads(mock.hits[0]["body"])["message"] == "Turn finished: end to end", str(mock.hits))
check("端到端 log 沒有秘密", clean(*real_calls), str(real_calls))
shutil.rmtree(cfg_dir.parent)

# ── 接進 api_status ────────────────────────────────────────────────────────
section("api_status wiring")
import api_status  # noqa: E402
import sf_log  # noqa: E402


class _Sess:
    def __init__(self, cmd="", label=None):
        self.cmd = cmd
        if label:
            self._custom_label = label


class _Mon(api_status.StatusApiMixin):
    def __init__(self):
        self.sessions = {"s1": _Sess("claude --model x", label="build-1"),
                         "s2": _Sess("codex exec"), "s3": _Sess("")}


mon = _Mon()
rec = []
mon._auto_notifier = N.AutoNotifier(
    loader=lambda: ACFG, sender=lambda p, c: rec.append(p) or (True, "ok"), spawn=lambda fn: fn())
for sid in ("s1", "s2", "s3", "gone"):
    mon._notify_transitions({sid: R("working")})
    mon._notify_transitions({sid: R("done")})
labels = {p["sid"]: p["label"] for p in rec}
check("label_of：自訂標籤優先", labels.get("s1") == "build-1", str(labels))
check("label_of：沒有標籤就用指令第一個字", labels.get("s2") == "codex", str(labels))
check("label_of：沒指令就用 sid；分頁已不存在也不爆", labels.get("s3") == "s3" and labels.get("gone") == "gone", str(labels))

mon2 = _Mon()
saved = sf_log._dlog
captured = []
sf_log._dlog = lambda cat, msg: captured.append((cat, msg))
try:
    mon2._notify_transitions({})
    created = getattr(mon2, "_auto_notifier", None)
    mon2._notify_transitions({"s1": R("working")})
    mon2._notify_transitions({"s1": R("done")})
finally:
    sf_log._dlog = saved
check("第一次呼叫才建立 notifier，且重用同一個",
      isinstance(created, N.AutoNotifier) and mon2._auto_notifier is created)
check("沒設定時整條路徑靜默（沒 log）", captured == [], str(captured))
broken = _Mon()
broken._auto_notifier = type("Boom", (), {"process": lambda self, o, f: 1 / 0})()
try:
    broken._notify_transitions({"s1": R("done")})
    raised = False
except Exception:
    raised = True
check("notifier 炸掉不會傳出 _notify_transitions", raised is False)

from _testsrc import app_source  # noqa: E402

src = app_source()
mon_src = src[src.index("def _start_status_monitor"):]
hook_at = mon_src.find("self._notify_transitions(out)")
push_at = mon_src.find("if out and self._window:")
check("status monitor 迴圈每個 pass 都呼叫 notify hook，且在推送區塊之前",
      0 < hook_at < push_at, f"hook={hook_at} push={push_at}")
api_text = (HERE / "api_status.py").read_text(encoding="utf-8")
fn_src = next((ast.get_source_segment(api_text, n) for n in ast.walk(ast.parse(api_text))
               if isinstance(n, ast.FunctionDef) and n.name == "_notify_transitions"), "")
check("notify hook 直接 import sf_log/agent_status，不增加 main. 的縫隙",
      "sf_notify.AutoNotifier" in fn_src and "main." not in fn_src, fn_src)

# 葉節點模組：只用標準庫、不 import main
tree = ast.parse((HERE / "sf_notify.py").read_text(encoding="utf-8"))
mods = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        mods |= {a.name.split(".")[0] for a in node.names}
    elif isinstance(node, ast.ImportFrom) and node.module:
        mods.add(node.module.split(".")[0])
std = getattr(sys, "stdlib_module_names", None)
check("sf_notify 不 import main", "main" not in mods)
check("sf_notify 只用標準庫", std is None or mods <= set(std), str(sorted(mods - set(std or ()))))

# ── sfctl notify（行程層，端到端）──────────────────────────────────────────
section("sfctl notify (subprocess)")
CLI_SECRET_ENV_AUTH = "Bearer SECRETENV000"


def make_home(cfg=None, mode=0o600):
    _n[0] += 1
    home = TMP / f"clihome{_n[0]}"
    home.mkdir()
    if cfg is not None:
        d = home / ".config" / "shellframe"
        d.mkdir(parents=True)
        (d / "notify.json").write_text(json.dumps(cfg), encoding="utf-8")
        os.chmod(d / "notify.json", mode)
    return str(home)


def sfctl(args, home, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("SF_SID", N.ENV_URL, N.ENV_AUTH)}
    env.update({"HOME": home, "USERPROFILE": home, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, str(HERE / "sfctl.py"), "notify", *args],
                       capture_output=True, text=True, env=env, cwd=str(HERE), timeout=60)
    return p.returncode, p.stdout, p.stderr


ARGS = ["--status", "done", "--message", "build ok", "--sid", "s7", "--label", "build-1"]

mock.reset()
rc, so, se = sfctl(ARGS, make_home())
check("沒設定 → 退出碼 0、stdout/stderr 全空、沒連網", rc == 0 and so == "" and se == "" and mock.hits == [], str((rc, so, se)))
rc, so, se = sfctl(["--status", "info", "--message", "x"], make_home())
check("沒設定、沒 sid 也一樣靜默", rc == 0 and so == "" and se == "")
rc, so, se = sfctl(ARGS + ["--dry-run"], make_home())
check("沒設定 + --dry-run → 印 not configured、退出碼 0", rc == 0 and so.strip() == "notify: not configured" and se == "", str((so, se)))

home = make_home({"url": URL, "auth": SECRET_AUTH})
mock.reset()
rc, so, se = sfctl(ARGS, home)
sent = json.loads(mock.hits[0]["body"]) if mock.hits else {}
check("設定好 → 退出碼 0、印 OK notify sent (DONE)", rc == 0 and so.strip() == "OK notify sent (DONE)" and se == "", str((rc, so, se)))
check("server 收到指定的 sid/label/status/message",
      (sent.get("sid"), sent.get("label"), sent.get("status"), sent.get("message")) == ("s7", "build-1", "DONE", "build ok"), str(sent))
check("Authorization 與 path 正確送出",
      mock.hits[0]["headers"].get("authorization") == SECRET_AUTH and mock.hits[0]["path"] == f"{SECRET_PATH}?{SECRET_Q}")
check("輸出沒有秘密", clean(so, se))

mock.reset()
rc, so, se = sfctl(["--status", "BlOcKeD", "--message", "need input", "--label", "w"], home, {"SF_SID": "s8"})
sent = json.loads(mock.hits[0]["body"]) if mock.hits else {}
check("--status 不分大小寫；--sid 預設取 SF_SID",
      rc == 0 and sent.get("status") == "BLOCKED" and sent.get("sid") == "s8", str((rc, so, se, sent)))
mock.reset()
rc, so, se = sfctl(["--status", "question", "--message", "?"], home)
sent = json.loads(mock.hits[0]["body"]) if mock.hits else {}
check("沒有 sid 時不查標籤（label 空）、QUESTION 可用", rc == 0 and sent.get("label") == "" and sent.get("status") == "QUESTION", str((rc, se, sent)))

mock.reset()
rc, so, se = sfctl(["--status", "nope", "--message", "x", "--label", "l"], home)
check("不認得的 status → 退出碼 2、有說明、沒送", rc == 2 and se.startswith("ERR") and mock.hits == [], str((rc, se)))
rc, so, se = sfctl(["--status", "done"], home)
check("缺 --message → argparse 的用法錯誤（退出碼 2）", rc == 2)

mock.reset(code=500)
rc, so, se = sfctl(ARGS, home)
check("server 500 → 退出碼 0（不擋呼叫端）", rc == 0, str(rc))
check("500 → stderr 以 WARN 開頭、有原因、stdout 空",
      se.startswith("WARN") and "HTTP 500" in se and so == "", str((so, se)))
check("500 的輸出沒有 SECRET*", clean(so, se))

dead_home = make_home({"url": f"http://127.0.0.1:{closed_port()}{SECRET_PATH}?{SECRET_Q}", "auth": SECRET_AUTH})
rc, so, se = sfctl(ARGS, dead_home)
check("連線被拒 → 退出碼 0、WARN、沒秘密", rc == 0 and se.startswith("WARN") and "connection refused" in se and clean(so, se), str((rc, se)))

mock.reset()
rc, so, se = sfctl(ARGS + ["--dry-run"], home)
check("--dry-run：什麼都沒送、退出碼 0", rc == 0 and mock.hits == [], str((rc, se)))
check("--dry-run：輸出沒有 SECRET*", clean(so, se), so + se)
check("--dry-run：印遮過的網址與 auth 字數",
      f"http://127.0.0.1:{mock.port}/***" in so and f"*** ({len(SECRET_AUTH)} chars)" in so, so)
try:
    shown = json.loads(so[so.index("{"):])
except Exception:
    shown = {}
check("--dry-run：印出完整 payload",
      (shown.get("sid"), shown.get("label"), shown.get("status"), shown.get("message")) == ("s7", "build-1", "DONE", "build ok")
      and "ts" in shown and "machine" in shown, str(shown))
rc, so, se = sfctl(ARGS + ["--dry-run"], make_home({"url": URL}))
check("--dry-run：沒有 auth 顯示 (none)", "(none)" in so and clean(so, se))

# env 蓋過檔案
mock.reset()
rc, so, se = sfctl(ARGS, home, {N.ENV_URL: mock.url("/env-path", ""), N.ENV_AUTH: CLI_SECRET_ENV_AUTH})
check("env 的 url/auth 蓋過設定檔",
      rc == 0 and len(mock.hits) == 1 and mock.hits[0]["path"] == "/env-path"
      and mock.hits[0]["headers"].get("authorization") == CLI_SECRET_ENV_AUTH, str(mock.hits))
mock.reset()
rc, so, se = sfctl(ARGS, make_home(), {N.ENV_URL: mock.url("/only-env", "")})
check("只有 env（沒有檔案）也能用，且不帶 Authorization",
      rc == 0 and mock.hits and mock.hits[0]["path"] == "/only-env" and "authorization" not in mock.hits[0]["headers"], str(mock.hits))
mock.reset()
rc, so, se = sfctl(ARGS, home, {N.ENV_URL: "", N.ENV_AUTH: ""})
check("空字串 env 不蓋掉檔案", rc == 0 and len(mock.hits) == 1 and mock.hits[0]["path"].startswith(SECRET_PATH))

if os.name == "posix":
    mock.reset()
    rc, so, se = sfctl(ARGS, make_home({"url": URL, "auth": SECRET_AUTH}, mode=0o644))
    check("設定檔權限太鬆 → stderr 警告 chmod 600，但照送、退出碼 0",
          rc == 0 and "chmod 600" in se and len(mock.hits) == 1 and clean(so, se), str((rc, se)))
rc, so, se = sfctl(ARGS, make_home(), {N.ENV_URL: "ftp://example.com/x"})
check("env 給了非 http(s) 網址 → 當作沒設定（0、不連網），只警告原因", rc == 0 and so == "" and "http" in se and "ftp" not in se, str((so, se)))

# ── sfctl notify（行程內：標籤查詢，_rpc 以假的取代，絕不碰執行中的 app）────────
section("sfctl notify (in-process, _rpc faked)")
import sfctl as S  # noqa: E402

rpc_calls = []
SESSIONS = {"details": {"sessions": [{"sid": "s7", "label": "from-app"}, {"sid": "s8", "label": ""}]}}
real_rpc = S._rpc


def fake_rpc(cmd, args=None, timeout=15.0):
    rpc_calls.append((cmd, timeout))
    return SESSIONS


S._rpc = fake_rpc
try:
    check("_tab_label：找到就回標籤", S._tab_label("s7") == "from-app")
    check("_tab_label：找不到 / 標籤空 → 空字串", S._tab_label("s99") == "" and S._tab_label("s8") == "")
    S._rpc = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no app"))
    check("_tab_label：_rpc 失敗 → 空字串、不丟例外", S._tab_label("s7") == "")
    S._rpc = lambda *a, **k: {"success": False, "message": "Timeout"}
    check("_tab_label：app 沒回應 → 空字串", S._tab_label("s7") == "")
    S._rpc = fake_rpc

    def run_cmd(**kw):
        ns = argparse.Namespace(**{"status": "done", "message": "m", "sid": None, "label": None, "dry_run": False, **kw})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = S._cmd_notify(ns)
        return rc, out.getvalue(), err.getvalue()

    saved_env = {k: os.environ.get(k) for k in (N.ENV_URL, N.ENV_AUTH, "SF_SID")}
    try:
        rpc_calls.clear()
        rc, so, se = run_cmd(sid="s7")
        check("沒設定：就算有 sid 也不查標籤（_rpc 不被呼叫）、靜默", rc == 0 and rpc_calls == [] and so == "" and se == "")

        os.environ[N.ENV_URL] = URL
        os.environ[N.ENV_AUTH] = SECRET_AUTH
        mock.reset()
        rc, so, se = run_cmd(sid="s7")
        sent = json.loads(mock.hits[0]["body"]) if mock.hits else {}
        check("設定好、有 sid、沒給 --label → 用 list 查標籤（timeout 2 秒）",
              rpc_calls == [("list", 2.0)] and sent.get("label") == "from-app" and rc == 0, str((rpc_calls, sent)))
        rpc_calls.clear()
        run_cmd(sid="s7", label="given")
        check("給了 --label 就不查", rpc_calls == [])
        rpc_calls.clear()
        run_cmd(sid="")
        check("沒有 sid 就不查", rpc_calls == [])
        os.environ["SF_SID"] = "s8"
        mock.reset()
        run_cmd()
        check("--sid 預設取 SF_SID", json.loads(mock.hits[0]["body"])["sid"] == "s8" and rpc_calls == [("list", 2.0)])
        rpc_calls.clear()
        mock.reset()
        rc, so, se = run_cmd(sid="s7", dry_run=True)
        check("--dry-run 會查標籤但不送", mock.hits == [] and "from-app" in so and clean(so, se), so)
        S._rpc = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no app"))
        mock.reset()
        rc, so, se = run_cmd(sid="s7")
        check("查標籤失敗仍照送（label 空）", rc == 0 and json.loads(mock.hits[0]["body"])["label"] == "")
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
finally:
    S._rpc = real_rpc

mock.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
