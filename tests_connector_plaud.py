#!/usr/bin/env python3
"""Plaud on the connector core: the description (connectors/services/plaud.json), the adapter
(connectors/services/plaud.py), the shared stdio runner (_local_mcp.py) and `sfctl connect step`, against a FAKE stdio
MCP server (a small Python script in a temp folder) and stand-ins for every AI program and tab. No network, no real
Plaud, no real token, no browser, no npx; the home folder is a temp folder.

  - the description loads and validates; the pinned package, the server name and the telemetry switches agree with the adapter
  - `cli_add` writes the pinned command with PLAUD_TELEMETRY_DISABLED=1 and DO_NOT_TRACK=1, for Claude and for Codex
  - the sign-in action answers at once ("waiting") and the check reaches ok / timeout / denied / port_busy / failed
  - the server and a grandchild it started are gone after the cap, after success and after a disconnect
  - what the tool said (a made-up address) never appears in a result, the state file or the log
  - the token check only asks whether the file exists (the file is never opened)
  - the test prompt never asks for a title; every verdict of the judge; Claude and Codex spellings of the tool names
  - an entry the person made with the same package is recognized
  - `sfctl connect step` end to end on the real engine (and the sfctl parser)

Run: .venv/bin/python tests_connector_plaud.py
"""
import builtins
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
os.environ.setdefault("SHELLFRAME_TESTS", "1")
_TMP = Path(tempfile.mkdtemp(prefix="sf-plaud-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))
os.environ.setdefault("SHELLFRAME_CONFIG_DIR", str(_TMP / "shellbot"))

sys.modules.setdefault("webview", MagicMock())
import api_host  # noqa: E402

api_host.bind({"load_config": lambda: {}, "update_config": lambda fn: None,
               "ACCOUNT_MANAGER": type("AM", (), {"session_refs": lambda s, cfg: {}, "env_for": lambda s, p, r: {}})()})
import _connectors_fake as fake  # noqa: E402
import api_connect  # noqa: E402
from connectors import clis as clis_mod, engine as eng_mod, registry, health  # noqa: E402
from connectors.adapter import Ctx  # noqa: E402
from connectors.clis import Result  # noqa: E402
from connectors.services import _local_mcp, plaud  # noqa: E402

SERVICES = HERE / "connectors" / "services"
DESC = json.loads((SERVICES / "plaud.json").read_text(encoding="utf-8"))
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")
        for line in str(detail).splitlines()[:10]:
            print(f"         {line[:300]}")


LOG = []
eng_mod._dlog = lambda cat, msg: LOG.append(msg)

# ── the fake stdio MCP server (_plaud_fake.py) ──────────────────────────────────
from _plaud_fake import IDENTITY, Spawner, alive, wait_for  # noqa: E402


def reset_login():
    plaud.LOGIN.stop()
    plaud.LOGIN.state, plaud.LOGIN.code = "idle", ""
    plaud.SPAWN, plaud.LOGIN_CAP_S = None, 150.0


# ── stand-ins ────────────────────────────────────────────────────────────────
NODE, NPX = "/fake/bin/node", "/fake/bin/npx"
cfg_claude = {}
cfg_codex = {"agent_roster": {"chief": {"label": "Demo chief", "cmd": "sf-codex --search", "bot": {"primary": True}}}}


class Tabs:
    """connect_tabs.TabKit as the adapter uses it: what was opened and asked, and the transcript a test sets."""

    def __init__(self):
        self.opened, self.asked, self.alive, self.snap = [], [], set(), {"alive": True, "state": "", "events": []}

    def command(self, cli="claude"):
        return cli

    def open(self, cmd, title):
        self.opened.append((cmd, title))
        sid = f"s{len(self.opened)}"
        self.alive.add(sid)
        return sid

    def ask(self, sid, text, ready_timeout=90.0):
        self.asked.append((sid, text))

    def phase(self, sid):
        return "asking" if sid in self.alive else "closed"

    def read(self, sid):
        return dict(self.snap) if sid in self.alive else {"alive": False, "state": "", "events": []}

    def close(self, sid):
        self.alive.discard(sid)


class Rig:
    """The real engine over a temp state file, plaud.json and the adapter as shipped; every AI program a recording runner."""

    def __init__(self, name, cfg=None, node=True):
        self.home = _TMP / name / "home"
        self.home.mkdir(parents=True)
        self.tabs, self.runner, self.cfg, self.node = Tabs(), fake.FakeRunner(), cfg or cfg_claude, node
        self.runner.entries["claude"] = {"my-other-server"}
        self.engine = eng_mod.Engine(registry=registry.Registry(dirs=[SERVICES]), path=str(_TMP / name / "connectors.json"),
                                     clis=clis_mod.Clis(runner=self.runner, finder=fake.finder()), backend=fake.backend(),
                                     ctx_factory=self.ctx, clock=time.time, platform="mac")

    def ctx(self, service, route):
        c = Ctx(service=service, cfg=self.cfg, env={"HOME": str(self.home), "PATH": "/nowhere"})
        c.find = lambda b: {"node": NODE, "npx": NPX}.get(b) if self.node else None
        c.run = lambda argv, timeout=40, env=None: Result(0, "v20.11.1") if list(argv) == [NODE, "--version"] else Result(1, "")
        c.tabs = self.tabs
        return c

    @property
    def token(self):
        return self.home / ".plaud" / "tokens-mcp.json"

    def step(self, op, **a):
        return self.engine.step("plaud", op, a)


def sid_of(res):
    return res["status"]["step"]["id"]


def to_signin(r):
    res = r.engine.start("plaud")
    assert sid_of(res) == "p1", res["status"]
    return r.step("act", action="cli_add")


# ── 1. the description ───────────────────────────────────────────────────────
print("== the description ==")
reg = registry.Registry(dirs=[SERVICES])
check("it loads with no error and validates", "plaud" in reg.ids() and not reg.errors and registry.validate(DESC) == [], reg.errors or registry.validate(DESC))
check("the adapter has every check and action it names", registry.validate_adapter(DESC, plaud.ADAPTER) == [] and reg.adapter("plaud") is plaud.ADAPTER)
route = DESC["routes"][0]
check("one route: local_mcp, recommended, for the primary bot's AI, detected by the package (version left off)",
      len(DESC["routes"]) == 1 and route["id"] == "local" and route["kind"] == "local_mcp" and route["recommended"] is True
      and route.get("target") == "primary" and route["detect"] == {"packages": ["@plaud-ai/mcp"]} and "recommended" not in DESC)
check("steps p0 p1 p2 p3, the test last; the checks are the adapter's", [s["id"] for s in route["steps"]] == ["p0", "p1", "p2", "p3"]
      and route["steps"][-1]["kind"] == "test" and route["steps"][0]["check"]["id"] == "node" and route["steps"][2]["check"]["id"] == "tokens"
      and route["steps"][0]["check"]["on"] == "enter")
check("p1 is the core's cli_add; p2 has login (ShellBot starts it) and login_wait (a look), both marked to be followed while waiting",
      route["steps"][1]["actions"][0]["builtin"] == "cli_add" and [a["id"] for a in route["steps"][2]["actions"]] == ["login", "login_wait"]
      and route["steps"][2]["actions"][0]["results"]["waiting"]["poll"] == "login_wait" and route["steps"][2]["actions"][1]["results"]["waiting"]["poll"] == "login_wait")
mcp = route["mcp"]
check("the server's settings: the pinned package, shellbot-plaud, the Windows command, telemetry off; the adapter says the same",
      mcp["server"] == plaud.SERVER == "shellbot-plaud" and mcp["args"] == ["-y", plaud.PACKAGE] and plaud.PACKAGE == "@plaud-ai/mcp@0.3.14"
      and mcp["command"] == "npx" and mcp["win_command"] == ["cmd", "/c", "npx", "-y", plaud.PACKAGE]
      and mcp["env"] == {"PLAUD_TELEMETRY_DISABLED": "1", "DO_NOT_TRACK": "1"} == plaud.ENV and plaud.server_command(True) == mcp["win_command"]
      and plaud.server_command(False) == ["npx", "-y", plaud.PACKAGE])
check("every AI program is reached by MCP; the scope is reading only", all(v["via"] == "mcp" for v in route["clis"].values())
      and set(route["clis"]) == {"claude", "codex", "opencode", "agy", "grok", "cursor", "pi"} and DESC["scopes"] == [dict(DESC["scopes"][0], id="read", required=True)])
other = json.loads((SERVICES / "google_drive.json").read_text(encoding="utf-8"))
check("it sits in the category the others of its kind use, named the same way", registry.category_conflicts([DESC, other]) == [] and DESC["category"]["id"] == "productivity")
ids = {s["id"] for s in route["steps"][2]["stuck"]}
check("the sign-in step answers the questions people have (no browser, port busy, wrong account, SSO, not at the computer)",
      {"no-browser", "port-busy", "wrong-account", "sso", "away"} <= ids)
text = json.dumps(DESC, ensure_ascii=False)
check("the words say the browser opens on this computer, the login stays in ~/.plaud, telemetry is off, how to disconnect",
      "ON THIS COMPUTER" in text and "~/.plaud" in text and "PLAUD_TELEMETRY_DISABLED=1" in text and {"what-it-does", "where-login", "telemetry", "disconnect"} <= {f["id"] for f in DESC["faq"]})
check("the server is never asked to install itself or sign out (no plaud-mcp install, no logout call)", "plaud-mcp install" not in text
      and "tools/call" not in (HERE / "connectors/services/plaud.py").read_text() and "logout" not in plaud.server_command())
need = {c for c in plaud.FAILURES} | {"waiting", "ok", "missing"}
check("every sign-in outcome has words (the check's and the actions')", need <= set(route["steps"][2]["check"]["states"])
      and all(need - {"no_node"} <= set(a["results"]) for a in route["steps"][2]["actions"]))
check("every test verdict the adapter can give has words", {"starting", "waiting_ready", "asking", "reading", "needs_allow", "pass", "said_ok", "timeout", "unclear",
                                                           "not_ready", "closed", "start_failed", "no_tools", "needs_auth", "failed"} <= set(route["test"]["verdicts"]))

# ── 2. cli_add ───────────────────────────────────────────────────────────────
print("== adding the server ==")
r = Rig("add-claude")
res = to_signin(r)
adds = [c for c in r.runner.calls if c[1:3] == ["mcp", "add"] and c[0].endswith("/claude")]
a = adds[0] if adds else []
envs = sorted(a[i + 1] for i, x in enumerate(a) if x == "-e")
check("claude: `claude mcp add` once, user scope, shellbot-plaud, npx and the pinned package, telemetry off (no other setting)",
      len(adds) == 1 and "shellbot-plaud" in a and a[a.index("--scope") + 1] == "user" and a[-3:] == ["npx", "-y", "@plaud-ai/mcp@0.3.14"]
      and envs == ["DO_NOT_TRACK=1", "PLAUD_TELEMETRY_DISABLED=1"], adds)
check("…Node.js 20 passed on entering, so the person started at the adding step; after it the sign-in is next",
      sid_of(res) == "p2" and res["status"]["clis"].get("claude") == "connected" and "my-other-server" in r.runner.entries["claude"])
rc = Rig("add-codex", cfg_codex)
to_signin(rc)
adds = [c for c in rc.runner.calls if c[1:3] == ["mcp", "add"] and c[0].endswith("/codex")]
a = adds[0] if adds else []
check("codex (the primary bot runs it): `codex mcp add` with --env pairs, the pinned package; nothing added to Claude",
      len(adds) == 1 and a[-3:] == ["npx", "-y", plaud.PACKAGE] and sorted(a[i + 1] for i, x in enumerate(a) if x == "--env") == ["DO_NOT_TRACK=1", "PLAUD_TELEMETRY_DISABLED=1"]
      and not [c for c in rc.runner.calls if c[1:3] == ["mcp", "add"] and c[0].endswith("/claude")], rc.runner.calls)
check("Node.js 18 is too old for it (20 or newer)", plaud.check_node(Ctx(service="plaud", find=lambda b: NODE, run=lambda a, t=40, env=None: Result(0, "v18.19.0")), {}) == {"state": "old", "version": "18.19.0"}
      and plaud.check_node(Ctx(service="plaud", find=lambda b: None), {}) == {"state": "missing"})

# ── 3. the sign-in ───────────────────────────────────────────────────────────
print("== the sign-in: returns at once, ends in a code, leaves nothing running ==")
OPENED = []
_real_open = builtins.open


def _guarded(file, *a, **kw):
    OPENED.append(str(file))
    return _real_open(file, *a, **kw)


builtins.open = _guarded
RETURNS = []


def drive(name, mode, cap=20.0, delay=0.0):
    """A fresh rig at the sign-in step; start the sign-in against the fake in `mode`; follow it with login_wait until it stops waiting."""
    reset_login()
    r = Rig(name)
    to_signin(r)
    plaud.SPAWN, plaud.LOGIN_CAP_S = Spawner(mode, delay, _TMP / f"{name}.pids"), cap
    t0 = time.monotonic()
    res = r.step("act", action="login")
    took = time.monotonic() - t0
    RETURNS.append(res)
    return r, res, took


r, res, took = drive("ok", "ok", delay=1.2)
check("login answers at once with `waiting` while the server is still asking (it takes 1.2 s)", took < 0.8 and res["view"]["step"]["result"]["code"] == "waiting"
      and res["view"]["step"]["id"] == "p2", (took, res["view"]["step"]["result"]))
res2 = r.step("act", action="login")
check("a second press while one runs does not start another (the port is taken by the first)", len(plaud.SPAWN.calls) == 1 and res2["view"]["step"]["result"]["code"] == "waiting")
res3 = r.step("check")
check("the check says `waiting` meanwhile, and the step stays", res3["view"]["step"]["check"]["code"] == "waiting" and sid_of(res3) == "p2", res3["view"]["step"]["check"])
argv, env = plaud.SPAWN.calls[0]
check("the server was started with telemetry off, npx at the path ShellBot found, node's folder first on PATH",
      env["PLAUD_TELEMETRY_DISABLED"] == "1" and env["DO_NOT_TRACK"] == "1" and argv == [NPX, "-y", plaud.PACKAGE] and env["PATH"].startswith("/fake/bin" + os.pathsep), (argv, env.get("PATH")))
final = wait_for(lambda: plaud.LOGIN.look()[0] == "done", 20)
res4 = r.step("act", action="login_wait")
RETURNS.append(res4)
check("the sign-in ends ok: login_wait says ok, the check passes and the step moves on to the test", final and sid_of(res4) == "p3"
      and r.engine.status("plaud")["step"]["id"] == "p3", res4["status"])
check("the fake wrote the token file in the temp home; ShellBot never opened it", r.token.exists() and not [o for o in OPENED if "tokens-mcp" in o], OPENED[-3:])

for mode, code in (("timeout", "timeout"), ("denied", "denied"), ("port_busy", "port_busy"), ("failed", "failed")):
    r, res, took = drive("m-" + mode, mode)
    wait_for(lambda: plaud.LOGIN.look()[0] == "done", 20)
    res = r.step("act", action="login_wait")
    RETURNS.append(res)
    chk = r.step("check")
    check(f"a tool error '{mode}' becomes the code `{code}`: login_wait and the check say it; the step stays; the file is not there",
          res["view"]["step"]["result"]["code"] == code and chk["view"]["step"]["check"]["code"] == code and sid_of(chk) == "p2" and not r.token.exists(),
          (res["view"]["step"]["result"], chk["view"]["step"]["check"]))
    again = r.step("act", action="login")
    check(f"…and a retry after `{code}` starts a new sign-in (the port is free)", again["view"]["step"]["result"]["code"] == "waiting" and len(plaud.SPAWN.calls) == 2)
    wait_for(lambda: plaud.LOGIN.look()[0] == "done", 20)

check("the tool's text never reaches a result, the state file or the log (the made-up address)",
      IDENTITY not in json.dumps(RETURNS) and all(IDENTITY not in p.read_text() for p in _TMP.rglob("connectors.json")) and LOG and IDENTITY not in " ".join(LOG), "")

reset_login()
r = Rig("no-node", node=False)
r.engine.start("plaud")
st = r.engine.status("plaud")
check("without Node.js the first step holds the person; the sign-in action itself answers no_node (and starts nothing)",
      sid_of({"status": st}) == "p0" and plaud.action_login(r.ctx("plaud", "local"), {}) == {"state": "no_node"} and plaud.LOGIN.look() == ("idle", ""))

print("== kill: the cap, success, disconnect ==")
reset_login()
pidfile = _TMP / "cap.pids"
r = Rig("cap")
to_signin(r)
plaud.SPAWN, plaud.LOGIN_CAP_S = Spawner("never", 0.0, pidfile), 1.5
t0 = time.monotonic()
r.step("act", action="login")
wait_for(pidfile.exists, 10)
pids = [int(x) for x in pidfile.read_text().split()]
check("the server and a grandchild it started are running while it waits", all(alive(p) for p in pids), pids)
check("the cap (made short here) ends the sign-in as `timeout`", wait_for(lambda: plaud.LOGIN.look() == ("done", "timeout"), 12) and time.monotonic() - t0 < 10, plaud.LOGIN.look())
check("both processes are gone afterwards, so the port is free for a retry", wait_for(lambda: not any(alive(p) for p in pids), 5), [alive(p) for p in pids])
reset_login()
check("the shipped default for the cap is 150 s", plaud.LOGIN_CAP_S == 150.0)

pidfile = _TMP / "ok.pids"
reset_login()
r = Rig("ok-kill")
to_signin(r)
plaud.SPAWN = Spawner("ok", 0.0, pidfile)
r.step("act", action="login")
wait_for(lambda: plaud.LOGIN.look()[0] == "done", 15)
pids = [int(x) for x in pidfile.read_text().split()]
check("after a successful sign-in the server (and its child) is killed too", wait_for(lambda: not any(alive(p) for p in pids), 5), [alive(p) for p in pids])

pidfile = _TMP / "dis.pids"
reset_login()
r = Rig("disconnect")
to_signin(r)
plaud.SPAWN, plaud.LOGIN_CAP_S = Spawner("never", 0.0, pidfile), 60
r.step("act", action="login")
wait_for(pidfile.exists, 10)
pids = [int(x) for x in pidfile.read_text().split()]
t0 = time.monotonic()
out = r.engine.disconnect("plaud")
check("disconnect stops a sign-in that is still waiting and kills its processes (within seconds, not the 60 s cap)",
      out["success"] and time.monotonic() - t0 < 15 and plaud.LOGIN.look() == ("idle", "") and wait_for(lambda: not any(alive(p) for p in pids), 5),
      (out.get("success"), plaud.LOGIN.look()))
check("…it removed only its own entry; the person's stayed; the login file (there or not) is not touched",
      "shellbot-plaud" not in r.runner.entries["claude"] and "my-other-server" in r.runner.entries["claude"])

for mode in ("silent", "rpc_error", "exit"):
    reset_login()
    r = Rig("x-" + mode)
    to_signin(r)
    plaud.SPAWN, plaud.LOGIN_CAP_S = Spawner(mode), 2.0
    r.step("act", action="login")
    wait_for(lambda: plaud.LOGIN.look()[0] == "done", 10)
    want = "timeout" if mode == "silent" else "failed"
    check(f"a server that {mode.replace('_', ' ')}s ends as `{want}`, never hangs", plaud.LOGIN.look() == ("done", want), plaud.LOGIN.look())
reset_login()
os.environ["SHELLFRAME_TESTS"] = "1"
r = Rig("guard")
to_signin(r)
r.step("act", action="login")                      # no stand-in given: under test the default starter refuses to run a real server
wait_for(lambda: plaud.LOGIN.look()[0] == "done", 5)
check("under test the default starter refuses to start a real server (ends as `failed`)", plaud.LOGIN.look() == ("done", "failed"), plaud.LOGIN.look())
check("classify: the tool's flag and a few words only, nothing else comes out",
      plaud.classify({"code": "answered", "is_error": False, "text": "anything"}) == "ok" and plaud.classify({"code": "answered", "is_error": True, "text": "Authentication timed out"}) == "timeout"
      and plaud.classify({"code": "answered", "is_error": True, "text": "User denied access"}) == "denied" and plaud.classify({"code": "cancelled"}) == ""
      and plaud.classify({"code": "answered", "is_error": True, "text": "Failed to start callback server: EADDRINUSE"}) == "port_busy"
      and plaud.classify({"code": "answered", "is_error": True, "text": "Request timed out on port 443"}) == "timeout"
      and plaud.classify({"code": "answered", "is_error": True, "text": "boom"}) == "failed" and plaud.classify({"code": "no_start"}) == "failed")
reset_login()

print("== the token check asks whether the file exists, nothing else ==")
r = Rig("tokens")
ctx = r.ctx("plaud", "local")
check("no file: missing", plaud.check_tokens(ctx, {}) == {"state": "missing"})
r.token.parent.mkdir()
r.token.write_text("{}")
OPENED.clear()
check("the file there: ok; health is ok too; and ShellBot opened nothing", plaud.check_tokens(ctx, {}) == {"state": "ok"} and plaud.ADAPTER.health(ctx, "local") == {"state": "ok"} and not OPENED, OPENED)
r.token.unlink()
check("the file gone: health says revoked (Plaud took the permission back)", plaud.ADAPTER.health(ctx, "local") == {"state": "revoked"})
src = (HERE / "connectors/services/plaud.py").read_text()
check("the adapter's code opens no file at all (only os.path.exists on the path)", "open(" not in src.replace("os.path.exists(", "").replace("_open", "").replace("open the browser", ""), "")
check("the token path is the server's own file, under the home folder (not the command-line tool's)",
      plaud.tokens_path({"HOME": "/h"}).replace("\\", "/") == "/h/.plaud/tokens-mcp.json")
builtins.open = _real_open

# ── 4. the test ──────────────────────────────────────────────────────────────
print("== the connection test ==")
for cli in ("claude", "codex"):
    p = plaud.ADAPTER.prompt(cli)
    check(f"{cli}: one line; it asks for list_files, at most 1, a verdict line; forbids login/logout and repeating any title; asks for no title",
          "\n" not in p and "list_files" in p and "at most 1" in p and "PLAUD-TEST: OK" in p and "PLAUD-TEST: FAIL" in p and "do not call login or logout" in p
          and "do not repeat any title" in p and "sender" not in p.lower() and "show the title" not in p.lower() and "list the titles" not in p.lower(), p)
check("Claude's sentence loads the tools with ToolSearch; Codex's names the server instead",
      "ToolSearch" in plaud.ADAPTER.prompt("claude") and "ToolSearch" not in plaud.ADAPTER.prompt("codex") and "shellbot-plaud" in plaud.ADAPTER.prompt("codex"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
check("in Chinese too: one line, the same marker, no title asked", "\n" not in plaud.ADAPTER.prompt("claude") and "PLAUD-TEST: OK" in plaud.ADAPTER.prompt("claude") and "list_files" in plaud.ADAPTER.prompt("claude"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"

PRE = "mcp__shellbot-plaud__"
call = lambda n: {"kind": "tool_call", "tool": n}                                                   # noqa: E731
ask = {"kind": "user_msg", "text": plaud.ADAPTER.prompt("claude")}
say = lambda t: {"kind": "assistant_text", "text": t}                                              # noqa: E731
end = {"kind": "turn_end"}
J = plaud.ADAPTER.judge
check("judge: a list_files call and `PLAUD-TEST: OK` is a pass", J([ask, call(PRE + "list_files"), say("1\nPLAUD-TEST: OK"), end]) == {"state": "done", "verdict": "pass", "calls": 1})
check("judge: Codex's `_` spelling of the server counts", J([ask, call("mcp__shellbot_plaud__list_files"), say("1\nPLAUD-TEST: OK"), end])["verdict"] == "pass")
check("judge: a verdict with no Plaud call is said_ok (proves nothing); no call and no OK is no_tools",
      J([ask, say("PLAUD-TEST: OK"), end])["verdict"] == "said_ok" and J([ask, say("I could not find it"), end])["verdict"] == "no_tools")
check("judge: FAIL after a call is failed; a call without the marker is unclear", J([ask, call(PRE + "list_files"), say("PLAUD-TEST: FAIL 401"), end])["verdict"] == "failed"
      and J([ask, call(PRE + "list_files"), say("1"), end])["verdict"] == "unclear")
check("judge: a call to another server does not count", J([ask, call("mcp__other__list_files"), say("PLAUD-TEST: OK"), end])["verdict"] == "said_ok")
check("judge: only login (or logout) called is needs_auth, never a pass", J([ask, call(PRE + "login"), say("PLAUD-TEST: OK"), end])["verdict"] == "needs_auth"
      and J([ask, call(PRE + "login"), call(PRE + "list_files"), say("PLAUD-TEST: OK"), end])["verdict"] == "pass")
check("judge: running, waiting for the person's OK, and the time-out",
      J([ask, call(PRE + "list_files")])["state"] == "running" and J([ask], "decision")["state"] == "needs_allow"
      and J([ask, call(PRE + "list_files")], "decision")["state"] == "needs_allow" and J([ask], "", 1000)["verdict"] == "timeout" and J([], "", 0)["state"] == "running")

# the engine's test, in the CLI the primary bot runs on
for name, cfg, prog in (("t-claude", cfg_claude, "claude"), ("t-codex", cfg_codex, "codex")):
    r = Rig(name, cfg)
    to_signin(r)
    r.token.parent.mkdir()
    r.token.write_text("{}")
    res = r.step("done")
    check(f"{name}: with the file in place the sign-in step passes and the test is next", sid_of(res) == "p3", res["status"])
    t = r.engine.test("plaud")
    q = r.tabs.asked[-1][1] if r.tabs.asked else ""
    r.tabs.snap = {"alive": True, "state": "", "events": [{"kind": "user_msg", "text": q}, call(PRE + "list_files"), say("1\nPLAUD-TEST: OK"), end]}
    t2 = r.engine.test("plaud", t["result"]["handle"])
    check(f"{name}: the test opens a tab of {prog}, asks the fixed question, and connects on a pass",
          r.tabs.opened and r.tabs.opened[0][0].endswith(prog) and t2["result"].get("ok") and t2["status"]["state"] == "connected" and "title" not in q.lower().replace("do not repeat any title", ""), (r.tabs.opened, t2))
    RETURNS.append(t2)
r = Rig("t-fail")
to_signin(r)
r.token.parent.mkdir()
r.token.write_text("{}")
r.step("done")
t = r.engine.test("plaud")
r.tabs.snap = {"alive": True, "state": "", "events": [{"kind": "user_msg", "text": r.tabs.asked[-1][1]}, say("nothing"), end]}
t2 = r.engine.test("plaud", t["result"]["handle"])
check("no Plaud tools in the test tab sends the person back to the add step (p1); a failed read to the sign-in (p2)",
      t2["result"]["code"] == "no_tools" and t2["result"]["failed_step"] == "p1" and sid_of(t2) == "p1" and plaud.FAILED_STEP["failed"] == "p2" and plaud.FAILED_STEP["needs_auth"] == "p2", t2["result"])
check("a result holds codes and numbers only", all(isinstance(v, (str, int, float, bool)) for x in [t2["result"]] for v in x.values()))

# ── 5. already set up ────────────────────────────────────────────────────────
print("== what already works ==")
r = Rig("detect")
r.runner.listing["claude"] = "Checking MCP server health…\n\nmy-plaud: npx -y @plaud-ai/mcp@0.3.14 - ✔ Connected\nother: npx -y something-else - ✔ Connected\n"
r.engine.detect()
st = r.engine.status("plaud")
via = st.get("via") or {}
check("a person's own `@plaud-ai/mcp` entry (any name, any version) is recognized: connected through the local route, adopted",
      st["state"] == "connected" and via.get("route") == "local" and via.get("adopted") is True and via.get("cli") == "claude" and via.get("entry") == "my-plaud", st)
r2 = Rig("detect2")
r2.runner.listing["claude"] = "Checking MCP server health…\n\nsomething: npx -y @plaud-ai/cli - ✔ Connected\n"
r2.engine.detect()
check("the separate command-line package is not the server: nothing is recognized", r2.engine.status("plaud")["state"] == "not_started")

# ── 6. sfctl connect step ────────────────────────────────────────────────────
print("== sfctl connect step ==")


class Api(api_connect.ConnectApiMixin):
    def __init__(self, engine):
        self._connect_engine = engine
        self._connect_detecting = True        # no look in the background

    def _connect_detect_kick(self, force=False):
        return False


reset_login()
r = Rig("sfctl")
api = Api(r.engine)
out = api._connect_sfctl({"action": "step", "service": "plaud", "op": "check"})
check("step before start: says to start it", not out["success"] and "sfctl connect start plaud" in out["message"], out)
out = api._connect_sfctl({"action": "start", "service": "plaud"})
check("start prints the step's id and title, the actions, and the old lines (Now: …, Settings → Connect)",
      out["success"] and "Now: Add the server to your primary bot's AI" in out["message"] and "Step id: p1" in out["message"] and "cli_add (Add it for me)" in out["message"]
      and "Settings → Connect" in out["message"] and "sfctl connect step plaud act --action <id>" in out["message"], out["message"])
check("step with a bad op or an act with no action id fails in plain words", not api._connect_sfctl({"action": "step", "service": "plaud", "op": "dance"})["success"]
      and not api._connect_sfctl({"action": "step", "service": "plaud", "op": "act"})["success"] and not api._connect_sfctl({"action": "step", "service": "zzz", "op": "check"})["success"])
out = api._connect_sfctl({"action": "step", "service": "plaud", "op": "act", "act": "cli_add"})
check("step act --action cli_add adds the server and shows the sign-in step", out["success"] and "Step id: p2" in out["message"] and "login (Sign in to Plaud)" in out["message"]
      and "login_wait" in out["message"] and "Did: cli_add → Added shellbot-plaud to your AIs" in out["message"], out["message"])
plaud.SPAWN, plaud.LOGIN_CAP_S = Spawner("ok", 0.8), 20
out = api._connect_sfctl({"action": "step", "service": "plaud", "op": "act", "act": "login"})
check("step act --action login answers at once: waiting in the browser on this computer",
      out["success"] and "Did: login" in out["message"] and "browser" in out["message"] and "computer" in out["message"], out["message"])
out = api._connect_sfctl({"action": "status", "service": "plaud"})
check("status shows the step in front of the person too", "Step id: p2 — Sign in to Plaud" in out["message"] and "Last action: login" in out["message"], out["message"])
wait_for(lambda: plaud.LOGIN.look()[0] == "done", 15)
out = api._connect_sfctl({"action": "step", "service": "plaud", "op": "check"})
check("step check, after the browser part: ok, and the step moved on to the test (which says to use sfctl connect test)",
      out["success"] and "Step id: p3" in out["message"] and "sfctl connect test plaud" in out["message"], out["message"])
out = api._connect_sfctl({"action": "step", "service": "plaud", "op": "done"})
check("on the test step `step` is refused and points at `sfctl connect test`", not out["success"] and "sfctl connect test plaud" in out["message"], out)
out = api._connect_sfctl({"action": "step", "service": "plaud", "op": "back"})
check("…but `back` goes through: from the test the bot can return to the sign-in step (with its title)",
      out["success"] and "Step id: p2 — Sign in to Plaud" in out["message"], out)
api._connect_sfctl({"action": "step", "service": "plaud", "op": "check"})          # the file is still there: on to the test again
j = api._connect_sfctl({"action": "status", "service": "plaud", "json": True})
check("--json gives the whole answer", json.loads(j["message"])["status"]["service"] == "plaud")
out = api._connect_sfctl({"action": "test", "service": "plaud"})
check("`sfctl connect test` still starts the test", out["success"] and ("Testing" in out["message"] or "connected" in out["message"]), out["message"])
reset_login()

import sfctl  # noqa: E402
sent = []
sfctl._rpc = lambda cmd, args, timeout=0: sent.append((cmd, args)) or {"success": True, "message": "ok"}
sfctl._print_result = lambda res: None
for argv in (["connect", "step", "plaud", "act", "--action", "login"], ["connect", "step", "plaud", "check"], ["connect", "status", "plaud"]):
    sent.clear()
    sys.argv = ["sfctl"] + argv
    try:
        sfctl.main()
    except SystemExit:
        pass
    check("sfctl parses `" + " ".join(argv) + "`", sent and sent[0][0] == "connect" and sent[0][1]["action"] == argv[1] and sent[0][1]["service"] == "plaud"
          and sent[0][1]["op"] == (argv[3] if len(argv) > 3 else "") and sent[0][1]["act"] == ("login" if "--action" in argv else ""), sent)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
