#!/usr/bin/env python3
"""`sfctl tg-push` (tg_push.py): a script's message to Telegram, registered so a reply to it finds its owner.

  - ShellBot up: the app does it (an RPC with the text, the schedule and the chat; never a token);
    it could not and sent nothing: this process sends it; it sent part of it: no second send
  - ShellBot down: a direct Bot API call (a local stand-in server here), every part registered
  - the token only from the caller's environment (the variable --token-var names) or the
    --env-file's lines, never exported, printed, logged or stored
  - exit 0 when sent, 1 when not, with a short error that does not hold the token
  - long text is split like the bridge does; "-" reads stdin

Run: .venv/bin/python tests_tg_push.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHELLFRAME_TESTS"] = "1"

import sf_config  # noqa: E402
import tg_origins as to  # noqa: E402
import tg_push as tp  # noqa: E402

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
    """A stand-in Bot API: remembers what it was sent, answers with the next message id."""
    log = []
    mode = {"status": 200, "next": 100}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Fake.log.append((self.path, body))
        if Fake.mode["status"] != 200:
            payload = json.dumps({"ok": False, "description": "Unauthorized"}).encode()
            self.send_response(Fake.mode["status"])
        else:
            Fake.mode["next"] += 1
            payload = json.dumps({"ok": True, "result": {"message_id": Fake.mode["next"], "chat": {"id": body.get("chat_id")}}}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"
os.environ["SHELLFRAME_TG_API_BASE"] = BASE


def reset():
    Fake.log.clear()
    Fake.mode.update(status=200, next=100)
    for n in (to.FILE_NAME, to.LOCK_NAME):
        try:
            (sf_config.config_dir() / n).unlink()
        except OSError:
            pass


def registry_text():
    try:
        return to.path().read_text(encoding="utf-8")
    except OSError:
        return ""


def ns(text="報表完成", sched="demo.report", env_file="", token_var="BOT_TOKEN", chat=""):
    return SimpleNamespace(text=text, sched=sched, env_file=env_file, token_var=token_var, chat=chat)


def run(args, up=False, rpc=None, env=None, stdin=""):
    calls = []

    def fake_rpc(cmd, a, timeout=0):
        calls.append((cmd, a))
        return (rpc or (lambda c, a: {"success": False, "message": "no app"}))(cmd, a)

    out, err = io.StringIO(), io.StringIO()
    code = tp.cli(args, fake_rpc, lambda: up, environ=env if env is not None else {}, stdin=io.StringIO(stdin), out=out, err=err)
    return code, out.getvalue(), err.getvalue(), calls


def clean(*streams):
    return all(TOKEN not in s and "AAFakeToken" not in s for s in streams)


ENV = {"BOT_TOKEN": TOKEN, "CHAT_ID": "1100"}

print("ShellBot is running")
reset()
code, out, err, calls = run(ns(), up=True, rpc=lambda c, a: {"success": True, "message": "已送到 1 個對話"}, env=ENV)
check("the app sends it: OK, exit 0", code == 0 and out.startswith("OK"), (code, out, err))
check("…one RPC, tg_push, with the text, the schedule and the chat", calls == [("tg_push", {"text": "報表完成", "schedule": "demo.report", "chat": "1100", "preview": False})], calls)
check("…the token is nowhere in the RPC and nothing was sent directly", TOKEN not in json.dumps(calls) and not Fake.log)
code, out, err, calls = run(ns(), up=True, rpc=lambda c, a: {"success": False, "message": "Telegram 沒有連線"}, env=ENV)
check("the app could not (nothing sent): this process sends it", code == 0 and len(Fake.log) == 1 and "directly" in out, (code, out, err))
reset()
code, out, err, calls = run(ns(), up=True, rpc=lambda c, a: {"success": False, "message": "partial", "details": {"sent": 1}}, env=ENV)
check("the app sent part of it: exit 1 and no second send (it would repeat the part)", code == 1 and not Fake.log and "ERR" in err)
code, out, err, calls = run(ns(), up=True, rpc=lambda c, a: {"success": False, "message": "Telegram 沒有連線"}, env={})
check("the app could not and there is no token: exit 1, and it says why", code == 1 and "Telegram 沒有連線" in err and "BOT_TOKEN" in err, err)

print("ShellBot is not running: a direct send")
reset()
env_before = dict(os.environ)
code, out, err, calls = run(ns(), env=ENV)
check("sent: OK, exit 0, no RPC", code == 0 and out.startswith("OK sent directly") and not calls, (code, out, err))
path, body = Fake.log[0]
check("the Bot API got the token in its path, the chat and the text", path == f"/bot{TOKEN}/sendMessage" and body == {"chat_id": 1100, "text": "報表完成", "disable_web_page_preview": True}, (path, body))
e = to.lookup(1100, 101)
check("it is registered as that schedule's push", e and e["kind"] == "schedule" and e["id"] == "demo.report" and e["head"] == "報表完成", e)
check("the token is not printed or stored", clean(out, err, registry_text()))
check("the registry holds the text's head only", set(json.loads(registry_text())["items"]["1100:101"]) == {"kind", "id", "at", "head"})

print("the token from an env file or another variable")
reset()
envf = Path(tempfile.mkdtemp(prefix="sf-tgp-")) / "job.env"
envf.write_text(f"# a job's settings\nexport MY_BOT='{TOKEN}'\nCHAT_ID=\"1100\"\nOTHER=1\n", encoding="utf-8")
code, out, err, calls = run(ns(env_file=str(envf), token_var="MY_BOT"), env={})
check("--env-file and --token-var: sent, the chat from the file's CHAT_ID", code == 0 and Fake.log and Fake.log[0][1]["chat_id"] == 1100, (code, out, err))
check("…not exported, not printed, not stored", "MY_BOT" not in os.environ and dict(os.environ) == env_before and clean(out, err, registry_text()))
reset()
code, out, err, calls = run(ns(token_var="MY_BOT", chat="1100"), env={"MY_BOT": TOKEN})
check("--token-var names an environment variable; --chat beats CHAT_ID", code == 0 and Fake.log[0][1]["chat_id"] == 1100)
code, out, err, calls = run(ns(chat="-100777"), env=ENV)
check("a group's negative chat id is fine", code == 0 and Fake.log[-1][1]["chat_id"] == -100777)
code, out, err, calls = run(ns(env_file=str(envf.with_name("missing.env"))), env=ENV)
check("an env file that cannot be read: exit 1", code == 1 and "env file" in err and clean(out, err))

print("it did not go: exit 1, and nothing about the token")
reset()
code, out, err, calls = run(ns(), env={"CHAT_ID": "1100"})
check("no token: exit 1", code == 1 and not Fake.log and "BOT_TOKEN" in err and clean(out, err))
code, out, err, calls = run(ns(), env={"BOT_TOKEN": "abc\r\nInjected: x", "CHAT_ID": "1100"})
check("a token with a line break in it is refused, not put in a URL", code == 1 and not Fake.log and "Injected" not in err + out)
code, out, err, calls = run(ns(), env={"BOT_TOKEN": TOKEN})
check("no chat: exit 1", code == 1 and not Fake.log and "chat" in err and clean(out, err))
code, out, err, calls = run(ns(chat="abc"), env=ENV)
check("a chat id that is not a number: exit 1", code == 1 and not Fake.log)
code, out, err, calls = run(ns(text="   "), env=ENV)
check("nothing to send: exit 1", code == 1 and not Fake.log)
code, out, err, calls = run(ns(sched=""), env=ENV)
check("no --from: exit 1", code == 1 and not Fake.log)
Fake.mode["status"] = 401
code, out, err, calls = run(ns(), env=ENV)
check("Telegram refuses (401): exit 1 with Telegram's reason", code == 1 and "Unauthorized" in err and "401" in err, err)
check("…the token is in no message and nothing was registered", clean(out, err) and not registry_text())
server2 = HTTPServer(("127.0.0.1", 0), Fake)
dead = f"http://127.0.0.1:{server2.server_port}"
server2.server_close()
os.environ["SHELLFRAME_TG_API_BASE"] = dead
code, out, err, calls = run(ns(), env=ENV)
check("Telegram unreachable: exit 1 and a short error without the token", code == 1 and "ERR" in err and clean(out, err), err)
del os.environ["SHELLFRAME_TG_API_BASE"]
code, out, err, calls = run(ns(), env=ENV)
check("a test run never reaches the real Telegram", code == 1 and clean(out, err) and not Fake.log[1:], (code, err))
os.environ["SHELLFRAME_TG_API_BASE"] = BASE

print("long text and stdin")
reset()
text = "\n".join(f"line {i} " + "z" * 80 for i in range(120))
code, out, err, calls = run(ns(text=text), env=ENV)
parts = [b["text"] for _, b in Fake.log]
check("a long text is split like the bridge does, nothing lost", code == 0 and len(parts) > 1 and "\n".join(parts) == text
      and f"({len(parts)} messages)" in out, (code, out))
items = json.loads(registry_text())["items"]
check("…every part is registered", len(items) == len(parts) and all(v["id"] == "demo.report" for v in items.values()), items)
reset()
Fake.mode["next"] = 100
state = {"n": 0}
orig = Fake.do_POST


def second_fails(self):
    state["n"] += 1
    if state["n"] == 2:
        Fake.mode["status"] = 500
    orig(self)


Fake.do_POST = second_fails
code, out, err, calls = run(ns(text=text), env=ENV)
Fake.do_POST = orig
check("a part that fails stops it: exit 1, and says how many went", code == 1 and "1 part(s) went" in err, err)
check("…the part that went is registered", len(json.loads(registry_text())["items"]) == 1)
reset()
code, out, err, calls = run(ns(text="-"), env=ENV, stdin="from stdin\n")
check('"-" reads stdin', code == 0 and Fake.log[0][1]["text"] == "from stdin")
code, out, err, calls = run(ns(text=None), env=ENV, stdin="no argument\n")
check("no argument reads stdin too", code == 0 and Fake.log[-1][1]["text"] == "no argument")

print("a registry that cannot be written, and an error that escapes")
reset()
real_record = to.record
to.record = lambda *a, **k: 1 / 0
code, out, err, calls = run(ns(), env=ENV)
to.record = real_record
check("it went but the registry could not be written: still exit 0 (a failure would make the script send it twice)", code == 0 and len(Fake.log) == 1, (code, err))
code, out, err, calls = run(ns(), up=True, rpc=lambda c, a: 1 / 0, env=ENV)
check("anything that escapes ends as a short error and exit 1, never a traceback", code == 1 and "Traceback" not in err and clean(out, err) and "ZeroDivisionError" in err, err)
long_id = "demo." + "x" * 100
reset()
code, out, err, calls = run(ns(sched=long_id), env=ENV)
check("a long schedule id (up to 120 characters) is stored whole", code == 0 and (to.lookup(1100, 101) or {}).get("id") == long_id)

print("is the app up?")
import sfctl  # noqa: E402
pidf = Path(tempfile.mkdtemp(prefix="sf-tgp-pid-")) / "shellframe.pid"
sfctl.PID_FILE = str(pidf)
real_tests = sfctl._tests_running
sfctl._tests_running = lambda: False
pidf.write_text(str(os.getpid()))
check("a live process in the pid file: up", sfctl._app_running() is True)
gone = subprocess.Popen([sys.executable, "-c", "pass"])
gone.wait()
pidf.write_text(str(gone.pid))
check("a pid that is gone: not up (no 45 s wait for an app that is not there)", sfctl._app_running() is False)
pidf.write_text("junk")
check("a junk pid file: not up", sfctl._app_running() is False)
pidf.unlink()
check("no pid file: not up", sfctl._app_running() is False)
real_platform = sfctl.sys.platform
pidf.write_text(str(os.getpid()))
try:
    sfctl.sys.platform = "win32"          # os.kill(pid, 0) would be CTRL_C_EVENT there: it must not be used
    killed = []
    real_kill = os.kill
    os.kill = lambda *a: killed.append(a)
    sfctl._app_running()
    check("on Windows it never signals the process", not killed, killed)
finally:
    os.kill = real_kill
    sfctl.sys.platform = real_platform
check("a test run never reaches the real app", (lambda: (setattr(sfctl, "_tests_running", real_tests), sfctl._app_running())[1])() is False)

print("through sfctl itself")
reset()
cfg = sf_config.config_dir()
sub_env = {k: v for k, v in os.environ.items() if k not in ("BOT_TOKEN", "CHAT_ID")}
sub_env.update(SHELLFRAME_TESTS="1", SHELLFRAME_CONFIG_DIR=str(cfg), SHELLFRAME_TG_API_BASE=BASE)
envf.write_text(f"BOT_TOKEN={TOKEN}\nCHAT_ID=1100\n", encoding="utf-8")
r = subprocess.run([sys.executable, str(HERE / "sfctl.py"), "tg-push", "--from", "demo.report", "--env-file", str(envf), "排程回報"],
                   capture_output=True, text=True, env=sub_env, timeout=60)
check("sfctl tg-push --from … --env-file …: exit 0", r.returncode == 0 and r.stdout.startswith("OK"), (r.returncode, r.stdout, r.stderr))
check("…the Bot API got it and the registry has it", Fake.log and Fake.log[0][1]["text"] == "排程回報"
      and (to.lookup(1100, 101) or {}).get("id") == "demo.report")
check("…the token is in neither stream nor the registry", clean(r.stdout, r.stderr, registry_text()))
r = subprocess.run([sys.executable, str(HERE / "sfctl.py"), "tg-push", "--from", "demo.report", "--env-file", str(envf.with_name("none.env")), "x"],
                   capture_output=True, text=True, env=sub_env, timeout=60)
check("sfctl exits 1 when it could not send (a script falls back to its old line)", r.returncode == 1 and r.stdout == "" and clean(r.stdout, r.stderr), (r.returncode, r.stderr))
r = subprocess.run([sys.executable, str(HERE / "sfctl.py"), "tg-push", "x"], capture_output=True, text=True, env=sub_env, timeout=60)
check("--from is required", r.returncode == 2 and "--from" in r.stderr)
r = subprocess.run([sys.executable, str(HERE / "sfctl.py"), "tg-push", "--from", "demo.report", "-"], input="piped text\n",
                   capture_output=True, text=True, env=dict(sub_env, BOT_TOKEN=TOKEN, CHAT_ID="1100"), timeout=60)
check("the token and chat from the environment, the text from a pipe", r.returncode == 0 and Fake.log[-1][1]["text"] == "piped text" and clean(r.stdout, r.stderr),
      (r.returncode, r.stderr))
src = (HERE / "tg_push.py").read_text(encoding="utf-8") + (HERE / "tg_origins.py").read_text(encoding="utf-8")
check("the code writes the token nowhere: no os.environ write, no log call", "os.environ[" not in src.replace("os.environ.get", "") and "logging" not in src and "_dlog" not in src)

reset()
server.shutdown()
print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
