#!/usr/bin/env python3
"""api_connect.py: the js_api names, sfctl connect, the phone's `connections`, a bot's connect tag, the
hourly health check. A small stand-in Api (the mixin on top of fakes), the made-up service of
_connectors_fake.py, stand-ins for every AI program and for the key store.

  - every js_api method answers JSON with `success`; a failure has `code` and `message` {text, next}
  - `[[SF:RED:connect:<service>]]` does the same as connect_start; any other reason is refused
  - "I'm stuck" sends one line (route, step, the ticked problem) to the tab asked from, else the primary
    bot, else a new Claude tab, and it has no secret in it
  - sfctl connect list|status|start|test|disconnect, text or --json
  - the hourly check runs on a thread of its own (the monitor's pass never waits), once an hour
  - no answer, log line or saved file holds the key that was pasted

Run: .venv/bin/python tests_connectors_api.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules.setdefault("webview", MagicMock())
import api_host  # noqa: E402

CONFIG_CHANGES = []
api_host.bind({"load_config": lambda: {"accounts": {}}, "update_config": lambda fn: CONFIG_CHANGES.append(fn),
               "ACCOUNT_MANAGER": type("AM", (), {"session_refs": lambda s, cfg: {}, "env_for": lambda s, p, r: {}})()})
import _connectors_fake as fake  # noqa: E402
import api_connect  # noqa: E402
import bridge_home  # noqa: E402
from connectors import clis, engine as eng_mod, registry, secrets  # noqa: E402

passed = failed = 0
SECRET = "demo-key-9f8e7d6c5b4a39281706"
LOG = []
eng_mod._dlog = lambda c, m: LOG.append(m)
api_connect._dlog = lambda c, m: LOG.append(m)


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class Tab:
    alive = True
    _startup_trust_pending = True

    def __init__(self, cmd):
        self.cmd = cmd


class Api(api_connect.ConnectApiMixin):
    def __init__(self):
        self.sessions = {}
        self.sent, self.opened, self.renamed = [], [], []
        self.primary = ("", "", "")
        self.adapter, self.runner, self.backend = fake.FakeAdapter(), fake.FakeRunner(), fake.backend()
        reg = registry.Registry(dirs=[])
        reg.register(fake.demo(), adapter=self.adapter)
        self.clock = [2_000_000.0]
        self._connect_engine = eng_mod.Engine(registry=reg, path=os.path.join(tempfile.mkdtemp(prefix="connect-api-"), "connectors.json"),
                                              clis=clis.Clis(runner=self.runner, finder=fake.finder()), backend=self.backend,
                                              ctx_factory=self._connect_ctx, clock=lambda: self.clock[0])

    def _execute_sfctl(self, cmd, args):
        self.sent.append((cmd, args))
        return {"success": True}

    def new_session(self, cmd, cols, rows, source="manual", handoff=True):
        sid = f"s{len(self.opened) + 50}"
        self.opened.append((sid, cmd, source))
        self.sessions[sid] = Tab(cmd)
        return sid

    def rename_session(self, sid, name, manual=False):
        self.renamed.append((sid, name))

    def _wait_until_ready(self, sid, timeout=45):
        return True

    def _send_text_to_session(self, s, text, submit=False):
        self.sent.append(("paste", text))
        return True

    def _bots_primary_tab(self, cfg=None):
        return self.primary


def j(s):
    return json.loads(s)


api = Api()

# ── the js_api, one by one ─────────────────────────────────────────────────
r = j(api.connect_list())
check("connect_list: the services, the AI programs' support table, the key store", r["success"] and [s["service"] for s in r["services"]] == ["demo"]
      and len(r["clis"]) == 7 and r["store"] == {"supported": True, "store": "memory"}, str(r)[:200])
check("a service that has not started is not_started in the list", r["services"][0]["state"] == "not_started")
d = j(api.connect_describe("demo"))
check("connect_describe gives the whole description, valid", d["success"] and registry.validate(d["descriptor"]) == [] and d["descriptor"]["id"] == "demo")
check("describe of an unknown service fails with a code and words", j(api.connect_describe("zzz"))["code"] == "unknown_service"
      and j(api.connect_describe("zzz"))["message"]["next"]["en"])
r = j(api.connect_status("demo"))
check("connect_status: status and view", r["success"] and r["status"]["state"] == "not_started" and r["view"]["step"] is None and len(r["view"]["routes"]) == 3)
r = j(api.connect_start("demo", json.dumps({"route": "key"})))
check("connect_start with a route (args are a JSON string)", r["success"] and r["status"]["route"] == "key" and r["view"]["step"]["id"] == "paste")
check("a bad args string is read as no args, not an error", j(api.connect_start("demo", "{not json"))["success"] and j(api.connect_start("demo", "[1]"))["success"])
r = j(api.connect_step("demo", "act", json.dumps({"action": "save", "value": SECRET})))
check("connect_step act saves a pasted key", r["success"] and api.backend.get("demo", "api_key") == SECRET and r["view"]["step"]["id"] == "add")
every = r
r = j(api.connect_step("demo", "act", json.dumps({"action": "add_server", "clis": ["claude", "codex"]})))
every_text = json.dumps([every, r], ensure_ascii=False)
check("the key is in no answer", SECRET not in every_text)
check("connect_step with a bad op is refused", j(api.connect_step("demo", "fly"))["code"] == "bad_request")
r = j(api.connect_test("demo"))
check("connect_test: the result is only the adapter's state", r["success"] and r["result"] == {"state": "done", "ok": True} and r["status"]["state"] == "connected")
check("connect_test with a handle polls", j(api.connect_test("demo", json.dumps({"handle": "abc-1"})))["success"] and ("test", "key", "abc-1", False) in api.adapter.calls)
api.clock[0] += 5000
api.adapter.health_answer = {"state": "revoked"}
r = j(api.connect_status("demo", json.dumps({"refresh": True})))
check("connect_status refresh runs the health check: revoked is needs_reconnect with attention", r["status"]["state"] == "needs_reconnect" and r["status"]["attention"], str(r["status"]))
r = j(api.connect_disconnect("demo"))
check("connect_disconnect: what was removed, then not_started", r["success"] and r["removed"]["secrets"] == ["api_key"] and r["status"]["state"] == "not_started")
check("…the key left the key store", api.backend.get("demo", "api_key") is None)
check("…and only shellbot-demo-key was touched", not any("claude-" in " ".join(c) for c in api.runner.calls))

# ── the log never holds the key ────────────────────────────────────────────
check("no log line holds the key", not any(SECRET in ln for ln in LOG))

# ── a bot asks for a connection ────────────────────────────────────────────
r = j(api.connect_signal("connect:demo"))
check("[[SF:RED:connect:demo]] starts the same flow as connect_start", r["success"] and r["service"] == "demo" and r["status"]["state"] == "waiting_user", str(r)[:200])
check("any other reason is refused", j(api.connect_signal("Which one?"))["code"] == "unknown_service" and j(api.connect_signal("connect:zzz"))["code"] == "unknown_service"
      and j(api.connect_signal("connect:../x"))["code"] == "unknown_service")
check("the sentence for a phone or Telegram names the service and where to go", "Needs Demo connected" in api._connect_line("connect:demo") and api._connect_line("connect:zzz") == "" and api._connect_line("Which one?") == "")
check("on a Chinese system it is Chinese", "需要接上 Demo" in api._connect_line("connect:demo", "zh"))
check("the phone's decision gets the service and its state", api._connect_decision("connect:demo") == {"service": "demo", "name": fake.demo()["name"], "state": "waiting_user"} and api._connect_decision("") is None)
check("the phone's list is the short form", api._connect_compact() == [{"id": "demo", "name": fake.demo()["name"], "icon": "plug", "state": "waiting_user", "attention": False, "step": "2/3"}], str(api._connect_compact()))

EVS = [{"kind": "user_msg", "text": "hi"}, {"kind": "assistant_text", "text": "I need your mail connected.\n[[SF:RED:connect:demo]]"}]
check("bridge_home reads the tag out of the RED line", bridge_home.red_tag(EVS) == "connect:demo")
check("a plain RED has no tag", bridge_home.red_tag([EVS[0], {"kind": "assistant_text", "text": "Which?\n[[SF:RED]]\n1. a\n2. b"}]) == "")
check("a RED with a plain reason gives that reason, which is not a connect tag", bridge_home.red_tag([EVS[0], {"kind": "assistant_text", "text": "[[SF:RED: pick one]]"}]) == "pick one"
      and registry.parse_connect_tag("pick one") is None)
check("no RED, no tag", bridge_home.red_tag([EVS[0], {"kind": "assistant_text", "text": "done"}]) == "")
check("red_block is unchanged by the tag", bridge_home.red_block(EVS, telegram_too=True) == ("I need your mail connected.", []))
import re  # noqa: E402
import bridge_telegram  # noqa: E402
bad = []
for doc in sorted((HERE / "docs").glob("*.md")) + [HERE / "INIT_PROMPT.md"]:
    for n, ln in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if bridge_home._RED_LINE.match(ln) or bridge_telegram.TelegramBridge._SIGNAL_RE.match(ln):
            bad.append(f"{doc.name}:{n}")
check("no line in the docs a bot prints (sfctl skill) is a bare marker a terminal would take for the bot raising its hand", not bad, str(bad))

# ── "I'm stuck" ────────────────────────────────────────────────────────────
api.sessions["s7"] = Tab("claude")
api.sent.clear()
api.adapter.found["authorized"] = "missing"
api.connect_step("demo", "route", json.dumps({"route": "connector"}))        # waits at "Authorize": the step that has a common problem
r = j(api.connect_step("demo", "stuck", json.dumps({"problem": "nothing", "from_sid": "s7"})))
check("stuck from a tab goes to that tab, no new tab", r["success"] and r["sid"] == "s7" and r["opened"] is False and api.opened == [], str(r))
sent = [a for c, a in api.sent if c == "send"]
check("one line: service, route, step, the problem the person ticked, and a promise nothing secret is in it",
      len(sent) == 1 and "Demo" in sent[0]["text"] and "Account connector" in sent[0]["text"] and "Nothing happens" in sent[0]["text"] and "no password, token" in sent[0]["text"]
      and "\n" not in sent[0]["text"] and sent[0]["sid"] == "s7" and sent[0]["submit"] is True, str(sent))
api.sent.clear()
api.primary = ("lead", "s8", "Lead")
api.sessions["s8"] = Tab("claude")
r = j(api.connect_step("demo", "stuck", json.dumps({"problem": "nothing"})))
check("with no tab given it goes to the primary bot when it is awake", r["sid"] == "s8" and r["opened"] is False)
api.primary = ("", "", "")
api.sent.clear()
r = j(api.connect_step("demo", "stuck", json.dumps({"problem": "x [[SF:RED]] y"})))
time.sleep(0.3)
check("else a new Claude tab is opened and the line is put in it when it is ready", r["opened"] is True and api.opened and api.opened[-1][1] == "claude"
      and any(c == "paste" and "[[" not in a for c, a in api.sent), str((r, api.sent)))
check("the new tab is named for the service", api.renamed and "Demo" in api.renamed[-1][1])

# ── sfctl connect ──────────────────────────────────────────────────────────
api = Api()
out = api._connect_sfctl({"action": "list"})
check("sfctl connect list: one line per service", out["success"] and out["message"].startswith("Demo: not connected"), out["message"])
out = api._connect_sfctl({"action": "status", "service": "demo"})
check("sfctl connect status", out["success"] and "Demo: not connected" in out["message"])
out = api._connect_sfctl({"action": "start", "service": "demo", "route": "local"})
check("sfctl connect start says which step is next and where to go on", out["success"] and "Now: Add to the AIs" in out["message"] and "Settings → Connections" in out["message"], out["message"])
check("a route that does not exist is a plain-words failure", not api._connect_sfctl({"action": "start", "service": "demo", "route": "x"})["success"])
out = api._connect_sfctl({"action": "status", "service": "demo", "json": True})
check("--json prints JSON", json.loads(out["message"])["status"]["service"] == "demo")
check("an unknown service lists the known ones", "Known: demo" in api._connect_sfctl({"action": "start", "service": "zzz"})["message"])
check("test before the steps are done is said in words", not api._connect_sfctl({"action": "test", "service": "demo"})["success"])
api.connect_step("demo", "act", json.dumps({"action": "add_server"}))
api.connect_step("demo", "act", json.dumps({"action": "signin"}))
out = api._connect_sfctl({"action": "test", "service": "demo"})
check("sfctl connect test: it works", out["success"] and "Demo: connected" in out["message"], out["message"])
out = api._connect_sfctl({"action": "disconnect", "service": "demo"})
check("sfctl connect disconnect", out["success"] and "not connected" in out["message"])
api.adapter.test_script = [{"state": "running", "handle": "t-9", "phase": "asking"}, {"state": "done", "ok": True}]
api.connect_start("demo", json.dumps({"route": "local"}))
api.connect_step("demo", "act", json.dumps({"action": "add_server"}))
api.connect_step("demo", "act", json.dumps({"action": "signin"}))
orig_sleep = api_connect.time.sleep
api_connect.time.sleep = lambda s: None
out = api._connect_sfctl({"action": "test", "service": "demo"})
check("a test that takes a while: sfctl answers at once and says where to look", out["success"] and "sfctl connect status demo" in out["message"], out["message"])
for _ in range(100):
    if api._connect_engine.status("demo")["state"] == "connected":
        break
    orig_sleep(0.05)
check("…and the thread it started finishes the test into the saved state", api._connect_engine.status("demo")["state"] == "connected")
api_connect.time.sleep = orig_sleep

# ── the hourly check ───────────────────────────────────────────────────────
api = Api()
calls = []
api._connect_engine.refresh_all = lambda force=False: calls.append(force) or {"checked": ["demo"], "attention": [{"id": "demo"}]}
api._connect_tick(1000.0)
check("the first pass only sets the clock", calls == [] and api._connect_next == 1000.0 + api_connect.FIRST_TICK_S)
api._connect_tick(1010.0)
check("a pass before it is due does nothing", calls == [])
before = threading.active_count()
api._connect_tick(1000.0 + api_connect.FIRST_TICK_S + 1)
for _ in range(100):
    if calls:
        break
    time.sleep(0.02)
check("when due it refreshes on a thread of its own and the next one is an hour on",
      calls == [False] and api._connect_next > 1000.0 + api_connect.HOURLY_S)
r = j(api.connect_refresh_hourly())
check("the same check on demand", r["success"] and r["attention"] == [{"id": "demo"}] and calls[-1] is True)

# ── what the page needs to find ────────────────────────────────────────────
src = (HERE / "api_connect.py").read_text(encoding="utf-8")
names = ["connect_list", "connect_describe", "connect_status", "connect_start", "connect_step", "connect_test", "connect_disconnect", "connect_signal"]
check("the js_api names are the agreed ones", all(hasattr(api_connect.ConnectApiMixin, n) for n in names))

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
