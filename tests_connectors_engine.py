#!/usr/bin/env python3
"""The connector core from the first step to the last, on a made-up service (one description, one fake
adapter, see _connectors_fake.py) and stand-ins for every AI program and for the key store. No real
program is run, no real settings or key store is touched.

Paths proven: not started → waiting → connected → expired → needs reconnect → reconnected (only what is
no longer true is redone) → broken → disconnected; a restart in the middle; a key that never leaves the
key store; only `shellbot-*` entries are ever removed.

Run: .venv/bin/python tests_connectors_engine.py
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import _connectors_fake as fake  # noqa: E402
from connectors import clis, engine as eng_mod, health, registry, secrets  # noqa: E402

passed = failed = 0
SECRET = "demo-key-9f8e7d6c5b4a39281706"       # a made-up key
LOG = []
eng_mod._dlog = lambda cat, msg: LOG.append(f"{cat}: {msg}")


ALL_AUTO = ["claude", "codex", "agy", "grok"]   # the programs ShellBot sets up itself; asked for by name (the default is Claude only)

def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class Rig:
    """One engine over a temp state file, with a fake adapter, a recording runner, an in-memory key store."""

    def __init__(self, path=None, installed=None, rig=None):
        self.tmp = tempfile.mkdtemp(prefix="connect-test-")
        self.path = path or os.path.join(self.tmp, "connectors.json")
        self.clock = [1_000_000.0]
        self.adapter = rig.adapter if rig else fake.FakeAdapter()
        self.runner = rig.runner if rig else fake.FakeRunner()
        self.backend = rig.backend if rig else fake.backend()
        self.installed = installed
        self.boot()

    def boot(self):
        """(Re)start the engine over the same state file: what a restart does."""
        reg = registry.Registry(dirs=[])
        reg.register(fake.demo(), adapter=self.adapter)
        cl = clis.Clis(runner=self.runner, finder=fake.finder(self.installed) if self.installed else fake.finder())
        self.engine = eng_mod.Engine(registry=reg, path=self.path, clis=cl, backend=self.backend, clock=lambda: self.clock[0])
        return self.engine

    def st(self):
        return self.engine.status("demo")

    def saved(self):
        return json.loads(Path(self.path).read_text(encoding="utf-8")) if os.path.exists(self.path) else {}


def connect_local(r):
    """The local route from start to connected."""
    r.engine.start("demo", route="local")
    r.engine.step("demo", "act", {"action": "add_server", "clis": ALL_AUTO})
    r.engine.step("demo", "act", {"action": "signin"})
    return r.engine.test("demo")


# ── A. the connector route: nothing to install, one check gates the step ────
r = Rig()
check("before anything the status is not_started", r.st()["state"] == "not_started" and not r.st()["attention"])
check("not_started shows no step", r.st()["step"] is None and r.st()["route"] == "")
res = r.engine.start("demo")
check("start picks the recommended route", res["success"] and res["status"]["route"] == "connector", str(res)[:200])
check("the first step's check passed on entering, so it went on by itself",
      res["status"]["step"]["id"] == "authorize" and res["view"]["steps"][0]["state"] == "done", str(res["status"]))
check("the status says waiting_user with step 2 of 3", res["status"]["state"] == "waiting_user" and res["status"]["step"] == {"id": "authorize", "index": 2, "total": 3})
r.adapter.found["authorized"] = "missing"
res = r.engine.step("demo", "done")
check("a gated step that does not pass stays, with its words", res["success"] and res["status"]["step"]["id"] == "authorize"
      and res["view"]["step"]["check"]["code"] == "missing" and res["view"]["step"]["check"]["tone"] == "warn")
check("test before the steps are done is refused", r.engine.test("demo")["code"] == "test_not_ready")
r.adapter.found["authorized"] = "ok"
res = r.engine.step("demo", "done")
check("when it passes the step is done and the test step is next", res["status"]["step"]["id"] == "test")
res = r.engine.test("demo")
check("the test passes: connected, with the time and the route's least permissions",
      res["status"]["state"] == "connected" and res["status"]["connected_at"] == 1_000_000 and res["status"]["scopes"] == ["read"], str(res["status"]))
check("claude is connected through the connector, codex cannot be", res["status"]["clis"]["claude"] == "connected" and res["status"]["clis"]["codex"] == "unsupported")
check("no AI program was run for a connector route", r.runner.calls == [])
check("start on a connected service changes nothing", r.engine.start("demo")["status"]["state"] == "connected")

# ── B. the local route, every AI program a stand-in ────────────────────────
r = Rig()
res = r.engine.start("demo", route="local")
check("a chosen route is used", res["status"]["route"] == "local" and res["status"]["kind"] == "local_mcp")
check("the tool check on entering passed, so 'add' is the open step", res["status"]["step"]["id"] == "add")
v = res["view"]["step"]
check("the step shows what to enter by hand for the programs ShellBot cannot set up",
      sorted(m["cli"] for m in v["manual"]) == ["cursor", "opencode", "pi"], str(v.get("manual")))
check("a manual entry has the command and the plain env, never a value from the key store",
      all(m["command"] == ["demo-mcp", "--serve"] and m["env"] == {"DEMO_PROFILE": "shellbot"} and m["env_secret_names"] == [] for m in v["manual"]))
res = r.engine.step("demo", "act", {"action": "add_server", "clis": ALL_AUTO})
by_cli = {c[0].rsplit("/", 1)[-1]: [x[2] for x in r.runner.calls if x[0] == c[0]] for c in r.runner.calls}
check("add ran 'remove ours' then 'add ours' for each auto program",
      all(by_cli[c] == ["remove", "add"] for c in ("claude", "codex", "agy", "grok")), str(by_cli))
check("nothing was run for the programs that are set up by hand", not any(c[0].endswith(("opencode", "cursor", "pi")) for c in r.runner.calls))
check("the add reached every auto program once and only under its own name",
      all(r.runner.entries[c] == {"shellbot-demo"} for c in ("claude", "codex", "agy", "grok")), str(r.runner.entries))
add_claude = next(c for c in r.runner.calls if c[0].endswith("claude") and c[2] == "add")
check("claude was told: user scope, stdio, env, then -- and the server's command",
      add_claude[3:] == ["-e", "DEMO_PROFILE=shellbot", "--transport", "stdio", "--scope", "user", "shellbot-demo", "--", "demo-mcp", "--serve"], str(add_claude))
add_codex = next(c for c in r.runner.calls if c[0].endswith("codex") and c[2] == "add")
check("codex takes --env and then --", add_codex[3:] == ["shellbot-demo", "--env", "DEMO_PROFILE=shellbot", "--", "demo-mcp", "--serve"], str(add_codex))
check("the action's result is recorded and the step went on", res["view"]["step"]["id"] == "signin" and res["status"]["clis"]["claude"] == "connected")
check("by-hand programs are said to be manual in the status", res["status"]["clis"]["cursor"] == "manual" and res["status"]["clis"]["pi"] == "manual")
r.adapter.found["signed_in"] = "missing"
res = r.engine.step("demo", "act", {"action": "signin"})
# (the fake adapter's sign-in sets signed_in ok, like a person finishing it in the browser)
check("an adapter action runs, then its check ('after'); it passed, so the step is done and the test step is next",
      res["status"]["step"]["id"] == "test" and res["view"]["steps"][2]["state"] == "done" and ("action", "signin") in r.adapter.calls, str(res["view"]["steps"]))
r.adapter.found["signed_in"] = "missing"
r.engine.step("demo", "back")
r.adapter.actions["signin"] = lambda ctx, args: {"state": "opened"}
res = r.engine.step("demo", "act", {"action": "signin"})
check("when the check after the action does not pass the step stays, with the action's result and the check's words",
      res["status"]["step"]["id"] == "signin" and res["view"]["step"]["result"]["code"] == "opened" and res["view"]["step"]["result"]["tone"] == "good"
      and res["view"]["step"]["check"]["passed"] is False and res["view"]["step"]["check"]["tone"] == "warn", str(res["view"]["step"]))

# a restart in the middle: the person gets back to the same place
r2 = Rig()
r2.engine.start("demo", route="local")
r2.engine.step("demo", "act", {"action": "add_server", "clis": ALL_AUTO})
check("test while a step is still open is refused", r2.engine.test("demo")["code"] == "test_not_ready")
mid = r2.st()
r2.engine = r2.boot()
check("after a restart the same service is at the same step", r2.st()["step"] == mid["step"] and r2.st()["route"] == "local" and r2.st()["state"] == "waiting_user")
r2.adapter.test_script = [{"state": "running", "handle": "t1", "phase": "asking"}]
r2.engine.step("demo", "act", {"action": "signin"})
res = r2.engine.test("demo")
check("a running test is verifying", res["status"]["state"] == "verifying" and res["result"] == {"state": "running", "handle": "t1", "phase": "asking"}, str(res))
r2.engine = r2.boot()
check("a restart turns 'verifying' back into 'waiting for you' and forgets the old test", r2.st()["state"] == "waiting_user" and "test_handle" not in r2.saved()["services"]["demo"])

# ── the test comes in three kinds ──────────────────────────────────────────
r = Rig()
r.engine.start("demo", route="local")
r.engine.step("demo", "act", {"action": "add_server", "clis": ALL_AUTO})
r.engine.step("demo", "act", {"action": "signin"})
r.adapter.test_script = [{"state": "running", "handle": "h-1", "phase": "asking"}, {"state": "needs_user", "handle": "h-1", "code": "allow"},
                         {"state": "done", "ok": True}]
a = r.engine.test("demo")
b = r.engine.test("demo", handle="h-1")
check("polling with the handle asks the adapter again with it", ("test", "local", "h-1", False) in r.adapter.calls)
check("needs_user is 'waiting for you' with the test step open", b["status"]["state"] == "waiting_user" and b["status"]["problem"]["code"] == "needs_user" and b["status"]["step"]["id"] == "test", str(b["status"]))
c = r.engine.test("demo", handle="h-1")
check("done ok is connected", c["status"]["state"] == "connected" and c["status"]["problem"] is None)
r.adapter.test_script = [{"state": "running", "handle": "h-2", "phase": "reading"}]
r.engine.test("demo")
r.adapter.test_script = [{"state": "done", "ok": True}]
cres = r.engine.test("demo", handle="h-2", cancel=True)
check("a stopped test goes back to what it was, not to failed", cres["status"]["state"] == "connected", str(cres["status"]))

# a test that fails names the step that did not go through
r.adapter.test_script = [{"state": "done", "ok": False, "code": "nothing_back", "failed_step": "signin"}]
res = r.engine.test("demo")
check("a failed test is 'failed' and needs attention", res["status"]["state"] == "failed" and res["status"]["attention"] is True)
check("it points at the step that did not go through, and says it in plain words with a next step",
      res["status"]["step"]["id"] == "signin" and res["status"]["problem"]["text"]["en"] == "The demo gave nothing back"
      and res["status"]["problem"]["next"]["en"] == "Sign in again", str(res["status"]["problem"]))
check("the view's step carries the same message", res["view"]["step"]["message"]["code"] == "nothing_back")
res = r.engine.start("demo")
check("starting again on a failed service goes on from that step, nothing is lost", res["status"]["state"] == "waiting_user" and res["status"]["step"]["id"] == "signin"
      and res["view"]["steps"][1]["state"] == "done")
r.adapter.test_script = [{"state": "done", "ok": False, "code": "unknown_thing", "failed_step": ""}]
r.engine.step("demo", "done")
res = r.engine.test("demo")
check("an unknown failure code still gets a sentence and a next step", res["status"]["problem"]["text"]["en"] and res["status"]["problem"]["next"]["en"])

# ── expired → needs reconnect → reconnected ────────────────────────────────
r = Rig()
connect_local(r)
check("setup for expiry: connected", r.st()["state"] == "connected")
r.runner.calls.clear()
r.clock[0] += 60
check("within the ttl a refresh only answers", r.engine.refresh("demo")["status"]["state"] == "connected" and not any(c[0] == "health" for c in r.adapter.calls[-1:]))
r.adapter.health_answer = {"state": "unreachable"}
res = r.engine.refresh("demo", force=True)
check("unreachable keeps it connected, with a problem and no attention", res["status"]["state"] == "connected" and res["status"]["problem"]["code"] == "unreachable" and not res["status"]["attention"])
r.adapter.health_answer = {"state": "unknown"}
check("unknown changes nothing", r.engine.refresh("demo", force=True)["status"]["state"] == "connected")
r.adapter.health_answer = {"state": "expired"}
res = r.engine.refresh("demo", force=True)
check("expired means needs_reconnect, with attention", res["status"]["state"] == "needs_reconnect" and res["status"]["attention"] and res["status"]["problem"]["code"] == "expired", str(res["status"]))
check("the problem has words and a next step", res["status"]["problem"]["text"]["zh"] and res["status"]["problem"]["next"]["zh"])
check("health words say it needs to be connected again", "again" in health.words(res["status"], "en") and "重新接上" in health.words(res["status"], "zh"))
check("the compact form for the phone", health.compact(res["status"]) == {"id": "demo", "name": fake.demo()["name"], "icon": "plug", "state": "needs_reconnect", "attention": True, "step": "3/4"} or health.compact(res["status"])["attention"] is True)
r.adapter.found["signed_in"] = "missing"
r.runner.calls.clear()
res = r.engine.start("demo", reconnect=True)
check("reconnect redoes only the steps marked redo_on_reconnect (and the test)",
      res["status"]["state"] == "waiting_user" and res["status"]["step"]["id"] == "signin" and
      [s["state"] for s in res["view"]["steps"]] == ["done", "done", "waiting_user", "pending"], str(res["view"]["steps"]))
check("reconnect did not add to the AI programs again", not any(c[2] == "add" for c in r.runner.calls if len(c) > 2), str(r.runner.calls))
r.adapter.found["signed_in"] = "ok"
r.engine.step("demo", "act", {"action": "signin"})
r.adapter.health_answer = {"state": "ok"}
res = r.engine.test("demo")
check("after the test it is connected again", res["status"]["state"] == "connected" and not res["status"]["attention"])

# the service's side takes the permission back
r.adapter.health_answer = {"state": "revoked"}
check("revoked is needs_reconnect too", r.engine.refresh("demo", force=True)["status"]["problem"]["code"] == "revoked")

# an AI program's entry is gone: redo the add step, not the others
r = Rig()
connect_local(r)
r.runner.entries["claude"].discard("shellbot-demo")
res = r.engine.refresh("demo", force=True)
check("an entry gone from an AI program is needs_reconnect (cli_entry_missing)", res["status"]["state"] == "needs_reconnect" and res["status"]["problem"]["code"] == "cli_entry_missing", str(res["status"]))
r.runner.calls.clear()
res = r.engine.start("demo")
check("reconnect then redoes the add step", res["status"]["step"]["id"] == "add" and res["view"]["steps"][1]["state"] == "waiting_user")
r.engine.step("demo", "act", {"action": "add_server", "clis": ALL_AUTO})
check("and the entry is back", "shellbot-demo" in r.runner.entries["claude"])
r.engine.step("demo", "act", {"action": "signin"})
r.runner.timeout_get.add("claude")
r.adapter.test_script = [{"state": "done", "ok": True}]
check("setup: connected again after the add step was redone", r.engine.test("demo")["status"]["state"] == "connected")
r.clock[0] += 1000
check("an AI program that does not answer in time is 'unknown', not 'gone': still connected", r.engine.refresh("demo")["status"]["state"] == "connected")

# ── a key stays in the key store ───────────────────────────────────────────
r = Rig()
LOG.clear()
res = r.engine.start("demo", route="key")
check("the key route starts at 'paste'", res["status"]["step"]["id"] == "paste" and res["view"]["step"]["secrets_saved"] == {"api_key": False})
res = r.engine.step("demo", "act", {"action": "save", "value": "   "})
check("an empty key is refused in plain words", res["view"]["step"]["result"]["code"] == "empty" and res["view"]["step"]["result"]["tone"] == "bad")
res = r.engine.step("demo", "act", {"action": "save", "value": SECRET})
check("the key is saved and the step is done", res["view"]["step"]["id"] == "add" and r.backend.get("demo", "api_key") == SECRET)
blob = json.dumps(res, ensure_ascii=False)
check("the reply never has the key", SECRET not in blob)
check("connectors.json never has the key", SECRET not in Path(r.path).read_text(encoding="utf-8"))
check("the step says the key lands in the AI programs' own settings", any("settings file" in n["en"] for n in res["view"]["step"]["notes"]), str(res["view"]["step"]["notes"]))
res = r.engine.step("demo", "act", {"action": "add_server", "clis": ["claude"]})
add = next(c for c in r.runner.calls if c[0].endswith("claude") and c[2] == "add")
check("the runner is given the key (the AI program's own store is where it lives)", f"DEMO_API_KEY={SECRET}" in add)
check("what ShellBot shows of the command has the key masked", SECRET not in json.dumps(res) and secrets.BULLETS in json.dumps(res["detail"], ensure_ascii=False), json.dumps(res["detail"]))
check("no log line has the key", not any(SECRET in ln for ln in LOG), str(LOG[-3:]))
check("connectors.json knows a key is stored, as yes/no only", r.saved()["services"]["demo"]["secrets"] == {"api_key": True})
r.backend.delete("demo", "api_key")
r.adapter.test_script = [{"state": "done", "ok": True}]
r.engine.test("demo")
r.clock[0] += 1000
res = r.engine.refresh("demo")
check("a key that left the key store is needs_reconnect (secret_missing)", res["status"]["problem"]["code"] == "secret_missing", str(res["status"]))

# the store has no key: the add step says so
r = Rig()
r.engine.start("demo", route="key")
r.engine.step("demo", "act", {"action": "save", "value": SECRET})
r.backend.delete("demo", "api_key")
res = r.engine.step("demo", "act", {"action": "add_server", "clis": ALL_AUTO})
check("add without its key says the key is missing", res["view"]["step"]["result"]["code"] == "secret_missing" and not r.runner.calls)

# ── disconnect: only what ShellBot added ───────────────────────────────────
r = Rig()
r.runner.entries["claude"] = {"claude-own-other", "shellbot-other-service"}      # someone else's, and another service's
connect_local(r)
r.runner.calls.clear()
res = r.engine.disconnect("demo")
removed = [c for c in r.runner.calls if c[2] == "remove"]
check("disconnect removes only shellbot-demo", all("shellbot-demo" in c for c in removed) and not any("claude-own-other" in c or "shellbot-other-service" in c for c in removed), str(removed))
check("the others are still there", r.runner.entries["claude"] == {"claude-own-other", "shellbot-other-service"}, str(r.runner.entries))
check("nothing is left of it in the saved state", "demo" not in r.saved()["services"] and res["status"]["state"] == "not_started")
check("the adapter was told", ("disconnect", "local") in r.adapter.calls)
r = Rig()
r.engine.start("demo", route="key")
r.engine.step("demo", "act", {"action": "save", "value": SECRET})
res = r.engine.disconnect("demo")
check("disconnect deletes the key it stored", r.backend.get("demo", "api_key") is None and res["removed"]["secrets"] == ["api_key"])

# ── switching route, busy, errors ──────────────────────────────────────────
r = Rig()
r.engine.start("demo", route="local")
r.engine.step("demo", "act", {"action": "add_server", "clis": ALL_AUTO})
res = r.engine.step("demo", "route", {"route": "connector"})
check("changing the way removes what the old way added and starts the new one",
      res["status"]["route"] == "connector" and r.runner.entries["claude"] == set() and res["status"]["step"]["id"] in ("open", "authorize"), str(res["status"]))
lock = r.engine._op("demo")
lock.acquire()
check("a second operation on the same service gets 'busy' at once", r.engine.start("demo")["code"] == "busy")
lock.release()
r.adapter.boom = True
res = r.engine.step("demo", "check")
blob = json.dumps(res)
check("an adapter that raises is 'adapter error' with no exception text or token", res["status"]["state"] != "connected" and "sk-abcdefghijklmnop" not in blob and "leaked" not in blob, blob[:300])
r.adapter.boom = False
check("unknown service / route / step / action / op are refused with a code",
      r.engine.start("nope")["code"] == "unknown_service" and r.engine.start("demo", route="x")["code"] == "unknown_route"
      and r.engine.step("demo", "act", {"action": "zzz"})["code"] in ("unknown_action", "bad_request", "unknown_step")
      and r.engine.step("demo", "fly")["code"] == "bad_request" and r.engine.step("nope", "done")["code"] == "unknown_service")
check("every failure has plain words and a next step", all(r.engine.start("nope")["message"][k]["en"] for k in ("text", "next")))
r3 = Rig()
check("a step on a service that was never started is refused", r3.engine.step("demo", "done")["code"] == "not_started")
msg = r.engine.step("demo", "stuck", {"problem": "nothing"})["message"]
check("'stuck' builds the line for a bot from the step and the ticked problem, nothing secret", "Nothing happens" in msg or "Nothing happens" in msg.replace("(zh)", ""), msg)
check("the stuck line is one line without markers", "\n" not in msg and "[[" not in msg)

# ── refresh_all (the hourly hook) ──────────────────────────────────────────
r = Rig()
r.engine.start("demo", route="connector")
r.adapter.found["authorized"] = "ok"
r.engine.step("demo", "done")
r.engine.test("demo")
r.clock[0] += 4000
r.adapter.health_answer = {"state": "revoked"}
out = r.engine.refresh_all()
check("the hourly hook says which services need attention", out["checked"] == ["demo"] and [a["id"] for a in out["attention"]] == ["demo"], str(out))
check("the hourly hook starts no thread of its own", threading.active_count() <= 2)

# a description that changed under a saved record
r = Rig()
r.engine.start("demo", route="local")
r.engine.store.update("demo", lambda x: x.update(step="a-step-that-no-longer-exists"))
res = r.engine.start("demo")
check("a saved step that no longer exists starts the route again instead of sticking", res["success"] and res["status"]["step"]["id"] == "add", str(res["status"]))
r.engine.store.update("demo", lambda x: x.update(route="a-route-that-no-longer-exists"))
res = r.engine.start("demo")
check("a saved route that no longer exists goes to the default", res["success"] and res["status"]["route"] == "connector", str(res["status"]))

# ── the state file ─────────────────────────────────────────────────────────
r = Rig()
r.engine.start("demo", route="key")
raw = Path(r.path).read_text(encoding="utf-8")
check("the state file is plain JSON with a schema", json.loads(raw)["schema"] == 1)
try:
    r.engine.store.update("demo", lambda x: x.update(api_token="abc12345"))
    leaked = True
except secrets.SecretLeak:
    leaked = False
check("a field that looks like a secret is refused at save", not leaked)
check("and the refused write left the file as it was", Path(r.path).read_text(encoding="utf-8") == raw)
Path(r.path).write_text("{ broken", encoding="utf-8")
check("a damaged state file reads as nothing started (no crash)", r.engine.status("demo")["state"] == "not_started" and len(r.engine.list()) == 1)

_route = {"clis": {c: {"via": "mcp"} for c in ALL_AUTO}}
check("unless more are asked for, an add goes to Claude only; asked, to those", r.engine._targets(_route, {}) == ["claude"]
      and r.engine._targets(_route, {"clis": ["codex", "grok"]}) == ["codex", "grok"])

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
