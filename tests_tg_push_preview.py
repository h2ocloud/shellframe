#!/usr/bin/env python3
"""`sfctl tg-push` sends without a link preview unless `--preview` is given (the scripts it replaces did).

Both ways it can send:
  - the app's side (tg_reply_route.tg_push, reached by the RPC): the sendMessage it makes carries
    disable_web_page_preview, and does not with preview=True; the sfctl flag travels in the RPC
  - the direct Bot API path (tg_push.send_direct, ShellBot not running): the request body to a
    stand-in server carries it, and does not with --preview
  - sfctl's parser knows --preview and defaults it off

Run: .venv/bin/python tests_tg_push_preview.py
"""
import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHELLFRAME_TESTS"] = "1"

import bridge_home as bh  # noqa: E402
import tg_push as tp  # noqa: E402
import tg_reply_route as rr  # noqa: E402

TOKEN = "123456789:AAFakeTokenValue_abcdefghijk-XYZ"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class Fake(BaseHTTPRequestHandler):
    log = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Fake.log.append(body)
        payload = json.dumps({"ok": True, "result": {"message_id": 500 + len(Fake.log), "chat": {"id": body.get("chat_id")}}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["SHELLFRAME_TG_API_BASE"] = f"http://127.0.0.1:{server.server_port}"

print("the app's side")
sent = []
bh._chats = lambda bridge: {1100}
bh._tg = lambda bridge, method, payload=None, **k: (sent.append((method, dict(payload or {}))) or
                                                    {"ok": True, "result": {"message_id": 700 + len(sent), "chat": {"id": 1100}}})
bridge = SimpleNamespace(active=True)
app = SimpleNamespace(bridge=bridge)
res = rr.tg_push(app, "報表 https://example.test/a", "demo.report")
check("default: sendMessage carries disable_web_page_preview", res["success"] and sent[-1][1].get("disable_web_page_preview") is True, sent)
sent.clear()
res = rr.tg_push(app, "報表 https://example.test/a", "demo.report", preview=True)
check("preview=True: the flag is not sent", res["success"] and "disable_web_page_preview" not in sent[-1][1], sent)
sent.clear()
rr.tg_push(app, "x" * 9000, "demo.report")
check("every part of a long text carries it", len(sent) == 3 and all(p.get("disable_web_page_preview") is True for _, p in sent), sent)

print("sfctl to the app (RPC)")


def run(args, up, env=None):
    calls = []
    out, err = io.StringIO(), io.StringIO()
    code = tp.cli(args, lambda c, a, timeout=0: (calls.append((c, a)) or {"success": True, "message": "sent"}),
                  lambda: up, environ=env or {}, stdin=io.StringIO(""), out=out, err=err)
    return code, calls


def ns(**k):
    base = dict(text="hi https://example.test", sched="demo.report", env_file="", token_var="BOT_TOKEN", chat="")
    base.update(k)
    return SimpleNamespace(**base)


code, calls = run(ns(), True)
check("default: the RPC says preview False", code == 0 and calls[0][1]["preview"] is False, calls)
code, calls = run(ns(preview=True), True)
check("--preview: the RPC says preview True", code == 0 and calls[0][1]["preview"] is True, calls)

print("the direct Bot API path")
env = {"BOT_TOKEN": TOKEN, "CHAT_ID": "1100"}
Fake.log.clear()
code, _ = run(ns(), False, env)
check("default: the request body carries disable_web_page_preview", code == 0 and Fake.log and Fake.log[0].get("disable_web_page_preview") is True, Fake.log)
Fake.log.clear()
code, _ = run(ns(preview=True), False, env)
check("--preview: the request body does not", code == 0 and Fake.log and "disable_web_page_preview" not in Fake.log[0], Fake.log)

print("sfctl's parser")
import sfctl  # noqa: E402
seen = []
real_cli = tp.cli
tp.cli = lambda args, rpc, up, **k: (seen.append(args) or 0)
for argv in (["tg-push", "--from", "x", "hi"], ["tg-push", "--from", "x", "--preview", "hi"]):
    sys.argv = ["sfctl"] + argv
    try:
        sfctl.main()
    except SystemExit:
        pass
tp.cli = real_cli
check("parsed: off by default, on with the flag", len(seen) == 2 and seen[0].preview is False and seen[1].preview is True, seen)

server.shutdown()
print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
