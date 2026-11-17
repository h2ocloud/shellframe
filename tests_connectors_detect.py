#!/usr/bin/env python3
"""Connections that already work, recognized per route (connectors/detect.py), and what the status says.

Stand-ins only: every AI program is `_connectors_fake.FakeRunner`, whose `claude mcp list` / `codex mcp list --json`
answers are made up here in the shape the real programs print (2026-11: Claude Code 2.1, Codex 0.142). No real
program, account folder or settings file is read.

  1. reading: Claude's lines (a connector ✔ / needs authentication, a local server ✔ / failed / pending, names with
     colons, the header), Codex's JSON (on, off, not signed in, stderr around it); lowercased output reads the same
  2. matching comes from the description: a connector by its whole listed name (the description's `detect.connector`,
     else the service's English name; never a name that only starts the same), ShellBot's own entry by its name, a
     person's own entry by the package its command runs (version and path left off), only in a program the route reaches
  3. the status: a checked connector is connected `via` it; needs authentication is ⚠ and asks nothing of anyone;
     ShellBot's own entry is checked only once its steps are done; an adopted entry is connected and says it is the
     person's; Codex's entry is only found; a failed one is broken; a half-done wizard does not hide a working route
  4. disconnect forgets ShellBot's record and never removes the person's entry; the phone's short form keeps its keys
  5. nothing secret is kept: Codex's env values and a key on a command line never reach the snapshot or a status
  6. a program that does not answer keeps the last look; a program that is not there is "missing"
  7. api_connect: the first connect_list starts one look on a thread of its own and does not wait; the page's
     `detect` asks again at most every few minutes; connect_detect looks again now (not twice at once); the hourly
     check does not look (a look starts every MCP server Claude has), it counts what the last look found
  8. the shipped descriptions: each connector's listed name agrees with its adapter's tool names; each local route's
     package is the one its server runs; and a typical shape (a Claude account folder with Gmail, Calendar, Drive
     connected and Notion needing authentication) comes out as it should

Run: .venv/bin/python tests_connectors_detect.py
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
import _connectors_fake as fake  # noqa: E402
from connectors import clis, detect, engine as eng_mod, health, registry  # noqa: E402

passed = failed = 0
LOG = []
eng_mod._dlog = lambda cat, msg: LOG.append(f"{cat}: {msg}")
ENV_SECRET = "demo-env-value-7c6b5a4f3e2d1c0b9a8f"           # a made-up value in Codex's env: must never be kept
ARG_SECRET = "sk-demo-0123456789abcdefghijklmnop"             # a made-up key on a command line: must never be kept


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


def claude_list(*lines):
    return "Checking MCP server health…\n\n" + "\n".join(lines) + "\n"


def codex_json(*servers, noise=""):
    """What `codex mcp list` prints (its plain tables; the name is kept from when it was JSON): local servers in
    one table, remote ones in another, env values masked as Codex masks them."""
    loc = [x for x in servers if x["kind"] == "local"]
    rem = [x for x in servers if x["kind"] == "remote"]
    out = noise
    if loc:
        rows = [("Name", "Command", "Args", "Env", "Cwd", "Status", "Auth")] + [
            (x["name"], x["command"], " ".join(x["args"]) or "-", "DEMO_SECRET=*****", "-", x["status"], x["auth"]) for x in loc]
        w = [max(len(r[i]) for r in rows) + 2 for i in range(7)]
        out += "\n".join("".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in rows) + "\n"
    if rem:
        rows = [("Name", "Url", "Bearer Token Env Var", "Status", "Auth")] + [(x["name"], x["url"], "-", "enabled", x["auth"]) for x in rem]
        w = [max(len(r[i]) for r in rows) + 2 for i in range(5)]
        out += ("\n" if loc else "") + "\n".join("".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in rows) + "\n"
    return out if servers else (noise + "No MCP servers configured yet. Try `codex mcp add my-tool -- my-command`.\n")


def stdio(name, command, args, enabled=True, auth="Unsupported"):
    return {"kind": "local", "name": name, "command": command, "args": args, "status": "enabled" if enabled else "disabled",
            "auth": "Not logged in" if auth == "not_logged_in" else auth}


def remote(name, url, auth="OAuth"):
    return {"kind": "remote", "name": name, "url": url, "auth": "Not logged in" if auth == "not_logged_in" else auth}


class Rig:
    """The engine over a temp state file, the made-up service, a recording runner whose lists we script."""

    def __init__(self, desc=None, installed=None):
        self.runner, self.adapter, self.clock = fake.FakeRunner(), fake.FakeAdapter(), [1_000_000.0]
        self.reg = registry.Registry(dirs=[])
        assert self.reg.register(desc or fake.demo(), adapter=self.adapter), self.reg.errors
        self.engine = eng_mod.Engine(registry=self.reg, path=os.path.join(tempfile.mkdtemp(prefix="connect-detect-"), "connectors.json"),
                                     clis=clis.Clis(runner=self.runner, finder=fake.finder(installed) if installed else fake.finder()),
                                     backend=fake.backend(), clock=lambda: self.clock[0])

    def look(self, claude=None, codex=None):
        if claude is not None:
            self.runner.listing["claude"] = claude
        if codex is not None:
            self.runner.listing["codex"] = codex
        return self.engine.detect()

    def st(self, sid="demo"):
        return self.engine.status(sid)


def proto(st, rid):
    return next(p for p in st["protocols"] if p["id"] == rid)


# ── 1. reading ────────────────────────────────────────────────────────────
print("== 1. what each program says ==")
OUT = claude_list(
    "claude.ai Demo: https://demo.example/mcp - ✔ Connected",
    "claude.ai Demo Two: https://demo.example/two - ! Needs authentication",
    "plugin:demo:demo: bun run --cwd /tmp/demo --shell=bun --silent start - ✘ Failed to connect — CONNECTION_CLOSED: Connection closed",
    "my-demo: npx -y demo-mcp@2.0.1 --serve - ✔ Connected",
    "web-demo: https://demo.example/sse (HTTP) - ✔ Connected",
    "waiting-demo: demo-mcp - ⏸ Pending approval",
)
ents = detect.parse_claude(OUT)
by = {e["name"]: e for e in ents}
check("the header and blank lines are not servers; every server line is", sorted(by) == sorted(
    ["claude.ai Demo", "claude.ai Demo Two", "plugin:demo:demo", "my-demo", "web-demo", "waiting-demo"]), sorted(by))
check("a connector: its name after 'claude.ai ', ✔ Connected is checked", by["claude.ai Demo"]["kind"] == "connector"
      and by["claude.ai Demo"]["label"] == "Demo" and by["claude.ai Demo"]["state"] == "checked")
check("Needs authentication is broken (auth)", (by["claude.ai Demo Two"]["state"], by["claude.ai Demo Two"]["why"]) == ("broken", "auth"))
check("a name with colons (a plugin's server) reads whole; 'Failed to connect' is broken, never connected",
      (by["plugin:demo:demo"]["state"], by["plugin:demo:demo"]["why"], by["plugin:demo:demo"]["kind"]) == ("broken", "failed", "local"))
check("a local server keeps its command words for matching", by["my-demo"]["kind"] == "local" and by["my-demo"]["words"][:3] == ["npx", "-y", "demo-mcp@2.0.1"])
check("a URL server is remote; pending approval is only found", by["web-demo"]["kind"] == "remote" and (by["waiting-demo"]["state"], by["waiting-demo"]["why"]) == ("found", "pending"))
low = detect.parse_claude(OUT.lower())
check("lowercased output (the default runner lowers it) reads the same states", [(e["kind"], e["state"], e["why"]) for e in low] == [(e["kind"], e["state"], e["why"]) for e in ents])
alt = detect.parse_claude(claude_list("a-demo: demo-mcp - √ Connected", "b-demo: demo-mcp - Connected", "c-demo: demo-mcp - x Not connected",
                                      "d-demo: demo-mcp - Disconnected"))
check("another mark (or none) before 'Connected' is checked; 'Not connected' and 'Disconnected' are broken",
      [e["state"] for e in alt] == ["checked", "checked", "broken", "broken"], [(e["name"], e["state"]) for e in alt])
check("nothing to read is no servers", detect.parse_claude("") == [] and detect.parse_claude("No MCP servers configured. Use `claude mcp add`.") == [])

CX = codex_json(stdio("demo-tool", "npx", ["-y", "demo-mcp"]), stdio("off-demo", "demo-mcp", [], enabled=False),
                remote("cloud-demo", "https://demo.example/mcp", auth="not_logged_in"), stdio("in-demo", "/opt/demo/bin/demo-mcp", ["--serve"], auth="not_logged_in"),
                noise="WARNING: a demo warning [not json]\n")
cents = {e["name"]: e for e in detect.parse_codex(CX)}
check("Codex: a warning before the tables is skipped; every server in both tables is read", sorted(cents) == ["cloud-demo", "demo-tool", "in-demo", "off-demo"], sorted(cents))
check("Codex: an entry that is on is found (Codex does not check)", (cents["demo-tool"]["state"], cents["demo-tool"]["kind"]) == ("found", "local"))
check("Codex: turned off is broken (off); not signed in is broken (auth)", (cents["off-demo"]["state"], cents["off-demo"]["why"]) == ("broken", "off")
      and (cents["in-demo"]["state"], cents["in-demo"]["why"]) == ("broken", "auth") and cents["cloud-demo"]["kind"] == "remote")
check("Codex: its env values are never read into an entry", ENV_SECRET not in json.dumps(list(cents.values())))
check("Codex: output with no table is 'could not read' (None), not 'nothing there'; its empty-list sentence is nothing there",
      detect.parse_codex("No MCP servers configured yet. Try `codex mcp add my-tool -- my-command`.") == []
      and detect.parse_codex("error: no config") is None
      and detect.parse_codex("Name  Command  Args  Env  Cwd  Status  Auth\n") == [])
check("Codex: the listing used is the plain one (`--json` would carry env values)", detect.PARSERS["codex"][0] == ["mcp", "list"])

# ── 2. matching from the description ──────────────────────────────────────
print("== 2. a route is recognized by what its description says ==")
reg = registry.Registry(dirs=[])
reg.register(fake.demo(), adapter=fake.FakeAdapter())
hits = detect.match(ents, "claude", reg)
hm = {(h["route"], h["entry"]): h for h in hits}
check("the connector 'Demo' matches 'claude.ai Demo' and not 'claude.ai Demo Two' (whole names only)",
      ("connector", "") in hm and hm[("connector", "")]["state"] == "checked" and len([h for h in hits if h["route"] == "connector"]) == 1, hits)
check("an entry the person made that runs the route's command (version left off) is adopted",
      hm.get(("local", "my-demo"), {}).get("adopted") is True and hm[("local", "my-demo")]["own"] is False)
check("…a pending one running the same program is adopted too, as found", hm.get(("local", "waiting-demo"), {}).get("state") == "found")
check("the API-key route runs the same program: it is recognized for it as well", ("key", "my-demo") in hm)
nodet = fake.demo()
del nodet["routes"][0]["detect"]
reg2 = registry.Registry(dirs=[])
reg2.register(nodet, adapter=fake.FakeAdapter())
check("without detect.connector the service's English name is used", detect.connector_name(nodet, nodet["routes"][0]) == "Demo"
      and any(h["route"] == "connector" for h in detect.match(ents, "claude", reg2)))
check("the default packages: the route's command when it is not a launcher, and pinned packages in its args",
      detect.packages(nodet["routes"][1]) == ["demo-mcp"]
      and detect.packages({"mcp": {"command": "npx", "args": ["-y", "@demo/pkg@1.2.3", "--flag", "run"]}}) == ["@demo/pkg"]
      and detect.packages({"mcp": {"command": "uvx", "args": ["run", "server.py"]}}) == [])
own_ents = detect.parse_claude(claude_list("shellbot-demo: demo-mcp --serve - ✔ Connected", "shellbot-other: demo-mcp - ✔ Connected",
                                           "path-demo: node /opt/x/node_modules/demo-mcp/dist/index.js - ✔ Connected",
                                           "nearly-demo: npx -y demo-mcp-extra - ✔ Connected"))
oh = {(h["route"], h["entry"]): h for h in detect.match(own_ents, "claude", reg)}
check("ShellBot's own entry is recognized by its exact name (own, not adopted)", oh.get(("local", "shellbot-demo"), {}).get("own") is True
      and oh[("local", "shellbot-demo")]["adopted"] is False)
check("another shellbot-… entry is not the person's: not adopted, not own", ("local", "shellbot-other") not in oh)
check("a command that runs the package from its folder (a path) is recognized", ("local", "path-demo") in oh)
check("a package that only starts the same is not the same package", ("local", "nearly-demo") not in oh)
check("a connector route is looked for only in Claude (Codex lists no connectors)",
      not [h for h in detect.match(detect.parse_codex(CX), "codex", reg) if h["route"] == "connector"])
cxh = detect.match(detect.parse_codex(CX), "codex", reg)
check("Codex: a server running the route's program is found there (adopted)", any(h["route"] == "local" and h["entry"] == "demo-tool" and h["state"] == "found" and h["adopted"] for h in cxh), cxh)
check("a remote server is never taken for a local route", not any(h["entry"] == "cloud-demo" for h in cxh))

# ── 3. the status ─────────────────────────────────────────────────────────
print("== 3. the status counts what works ==")
r = Rig()
st = r.st()
check("before any look: as before (not started, no via), every route listed with nothing found, never looked",
      st["state"] == "not_started" and st["via"] is None and [p["state"] for p in st["protocols"]] == ["none", "none", "none"] and st["detected_at"] == 0, st)
ans = r.look(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ✔ Connected"), codex=codex_json())
st = r.st()
check("a look answers when it was and what each program said", ans["at"] == 1_000_000 and ans["read"] == {"claude": "ok", "codex": "ok"}, ans)
check("a checked connector: connected through it, by a look (not set up here), not the person's own entry",
      st["state"] == "connected" and st["via"] == {"route": "connector", "kind": "claude_connector", "adopted": False, "detected": True, "cli": "claude", "entry": ""}
      and not st["attention"] and st["step"] is None and st["detected_at"] == 1_000_000, st)
check("…its route is ✅ checked; the others are not set up", [(p["id"], p["state"]) for p in st["protocols"]] == [("connector", "checked"), ("local", "none"), ("key", "none")])
check("…the wizard's own fields are untouched (no route, no step, no AIs, no scopes)", st["route"] == "" and st["kind"] == "" and st["clis"] == {} and st["scopes"] == [])
check("…the sentence says which way, and that it is for Claude tabs only", health.words(st, "en") == "Demo: connected (the Claude account's connector: Claude tabs only)"
      and "：已連結（Claude 帳號連接器：只有 Claude 分頁能用）" in health.words(st, "zh"), health.words(st, "zh"))
check("…the list and the snapshot agree", r.engine.list()[0]["via"]["route"] == "connector" and r.engine.snapshot("demo")["status"]["state"] == "connected")
compact = health.compact(st)
check("the phone's short form keeps exactly its keys: connected, no step", set(compact) == {"id", "name", "icon", "state", "attention", "step"}
      and compact["state"] == "connected" and compact["step"] == "" and compact["attention"] is False, compact)

r = Rig()
r.look(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ! Needs authentication"), codex=codex_json())
st = r.st()
check("Needs authentication: ⚠ found but not working, and the service is still 'not connected' (no attention, no card)",
      st["state"] == "not_started" and not st["attention"] and st["via"] is None and (proto(st, "connector")["state"], proto(st, "connector")["why"]) == ("broken", "auth"), st)

r = Rig()
r.look(claude=claude_list("shellbot-demo: demo-mcp --serve - ✔ Connected"), codex=codex_json())
st = r.st()
check("ShellBot's own entry ✔ while its steps never finished: only found (its sign-in is not known), not connected",
      st["state"] == "not_started" and (proto(st, "local")["state"], proto(st, "local")["why"], proto(st, "local")["own"]) == ("found", "unfinished", True), proto(st, "local"))
r.engine.start("demo", route="local")
r.engine.step("demo", "act", {"action": "add_server"})
r.engine.step("demo", "act", {"action": "signin"})
res = r.engine.test("demo")
st = r.st()
check("…once ShellBot's steps and test are done on it: ✅ checked, connected via it (set up here)",
      st["state"] == "connected" and proto(st, "local")["state"] == "checked" and st["via"] == {"route": "local", "kind": "local_mcp", "adopted": False, "detected": False, "cli": "", "entry": ""}, st["via"])
check("…and the sentence names the way", health.words(st, "en") == "Demo: connected (a local MCP server)", health.words(st, "en"))

r = Rig()
r.look(claude=claude_list(f"my-demo: npx -y demo-mcp --api-key={ARG_SECRET} - ✔ Connected"), codex=codex_json())
st = r.st()
check("an entry the person made (✔): connected through the local route, adopted, with its name and program",
      st["state"] == "connected" and st["via"] == {"route": "local", "kind": "local_mcp", "adopted": True, "detected": True, "cli": "claude", "entry": "my-demo"}, st["via"])
check("…the sentence says it is the person's own setting", health.words(st, "en") == 'Demo: connected (a local MCP server "my-demo", your own setting, in Claude Code)'
      and "你自己的設定" in health.words(st, "zh"), health.words(st, "en"))
check("…the key on its command line is nowhere: snapshot, status, log", ARG_SECRET not in json.dumps(r.engine.detected) and ARG_SECRET not in json.dumps(st)
      and not any(ARG_SECRET in ln for ln in LOG))

r = Rig()
r.look(claude=claude_list(), codex=codex_json(stdio("demo-tool", "npx", ["-y", "demo-mcp"])))
st = r.st()
check("only in Codex's list: 🟡 found (not tested), not connected; the place says Codex and the entry",
      st["state"] == "not_started" and proto(st, "local")["state"] == "found" and proto(st, "local")["places"] == [
          {"cli": "codex", "entry": "demo-tool", "state": "found", "why": "", "own": False, "adopted": True}], proto(st, "local"))
check("…Codex's env values are nowhere in what was kept", ENV_SECRET not in json.dumps(r.engine.detected) and ENV_SECRET not in json.dumps(st))

r = Rig()
r.look(claude=claude_list("my-demo: demo-mcp - ✘ Failed to connect — demo error"), codex=codex_json(stdio("my-demo", "demo-mcp", [])))
st = r.st()
check("failed in Claude, listed in Codex: ⚠ broken (a failed check says more than an untested entry), both places shown",
      st["state"] == "not_started" and (proto(st, "local")["state"], proto(st, "local")["why"]) == ("broken", "failed")
      and sorted((p["cli"], p["state"]) for p in proto(st, "local")["places"]) == [("claude", "broken"), ("codex", "found")], proto(st, "local"))

print("== 3b. a half-done wizard does not hide a working route ==")
r = Rig()
r.engine.start("demo", route="local")
st = r.st()
check("the wizard on the local route at step 2 of 4: waiting for you, step 2/4", st["state"] == "waiting_user" and st["step"]["index"] == 2 and health.compact(st)["step"] == "2/4", st["step"])
r.look(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ✔ Connected"), codex=codex_json())
st = r.st()
check("…with the connector ✔: connected via the connector; no step, no attention, no problem",
      st["state"] == "connected" and st["via"]["route"] == "connector" and st["step"] is None and not st["attention"] and st["problem"] is None, st)
check("…the unfinished local setup is just another way: its route says where ShellBot's steps are",
      proto(st, "local")["state"] == "none" and proto(st, "local")["setup"] == {"state": "waiting_user", "step": {"id": "add", "index": 2, "total": 4}}
      and st["route"] == "local", proto(st, "local"))
check("…the phone gets connected, no step (its keys unchanged)", health.compact(st) == {"id": "demo", "name": fake.demo()["name"], "icon": "plug",
                                                                                        "state": "connected", "attention": False, "step": ""}, health.compact(st))
res = r.engine.start("demo")
check("…'Continue' on the local route goes on where it was left (the wizard reads its own record)", res["success"] and res["view"]["step"]["id"] == "add"
      and res["view"]["route"]["id"] == "local" and res["status"]["state"] == "connected")
r2 = Rig()
r2.engine.start("demo", route="local")
r2.engine.step("demo", "act", {"action": "add_server"})
r2.engine.step("demo", "act", {"action": "signin"})
r2.adapter.test_script = [{"state": "done", "ok": False, "code": "nothing_back", "failed_step": ""}]
r2.engine.test("demo")
check("a failed wizard alone needs attention (as before)", r2.st()["state"] == "failed" and r2.st()["attention"])
r2.look(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ✔ Connected"), codex=codex_json())
check("…with another route checked it does not: connected via that one, the failure stays on its own route",
      r2.st()["state"] == "connected" and not r2.st()["attention"] and r2.st()["via"]["route"] == "connector"
      and proto(r2.st(), "local")["setup"]["state"] == "failed", r2.st())
r3 = Rig()
r3.look(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ✔ Connected", "my-demo: demo-mcp - ✔ Connected"), codex=codex_json())
check("two routes checked: the recommended one is the way it is connected", r3.st()["via"]["route"] == "connector"
      and [p["state"] for p in r3.st()["protocols"]] == ["checked", "checked", "checked"])
r3.engine.start("demo", route="local")
check("…ShellBot's own route comes first when it works too", r3.st()["via"]["route"] == "local" and r3.st()["via"]["adopted"] is True)
plat = Rig()
plat.engine.platform = "linux"
pdesc = fake.demo()
pdesc.update(platforms=["mac"])
plat.reg.register(pdesc, adapter=plat.adapter)
plat.look(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ✔ Connected"), codex=codex_json())
check("a service that cannot be connected on this computer is never made connected by a look", plat.st()["state"] == "not_started" and plat.st()["via"] is None)

# ── 4. disconnect never removes the person's entry ─────────────────────────
print("== 4. disconnect forgets ShellBot's record, keeps the person's entry ==")
r = Rig()
r.runner.entries["claude"] = {"my-demo", "my-other-server"}
r.engine.start("demo", route="local")
r.engine.step("demo", "act", {"action": "add_server"})            # ShellBot's own entry, half way
r.look(claude=claude_list("my-demo: demo-mcp - ✔ Connected", "shellbot-demo: demo-mcp --serve - ✔ Connected", "my-other-server: other-mcp - ✔ Connected"), codex=codex_json())
check("before: connected through the person's entry; ShellBot's own half-done one is only found",
      r.st()["via"]["entry"] == "my-demo" and r.st()["via"]["adopted"] and proto(r.st(), "local")["places"][0]["entry"] in ("my-demo", "shellbot-demo"))
n0 = len(r.runner.calls)
res = r.engine.disconnect("demo")
removes = [c for c in r.runner.calls[n0:] if c[1:3] == ["mcp", "remove"]]
check("disconnect removed only ShellBot's own entry", res["success"] and all("shellbot-demo" in c for c in removes) and removes
      and not any("my-demo" in c or "my-other-server" in c for c in r.runner.calls), removes)
check("…the person's entries are still there", {"my-demo", "my-other-server"} <= r.runner.entries["claude"], r.runner.entries)
check("…the answer names the entry it kept (the person's own)", res["removed"].get("yours") == [{"route": "local", "cli": "claude", "entry": "my-demo"}]
      or res["removed"].get("yours") == [{"route": "key", "cli": "claude", "entry": "my-demo"}, {"route": "local", "cli": "claude", "entry": "my-demo"}], res["removed"])
check("…ShellBot's record is gone, and the status still says it works through the person's own setting",
      r.engine.store.get("demo") == {} and res["status"]["state"] == "connected" and res["status"]["via"]["adopted"] and res["status"]["route"] == "")
r.engine.disconnect("demo")
check("disconnecting again (nothing of ShellBot's left) still touches nothing of the person's", not any("my-demo" in c for c in r.runner.calls))
try:
    clis.own("my-demo")
    check("clis.own refuses the person's entry name before any program runs", False)
except clis.ForeignEntry:
    check("clis.own refuses the person's entry name before any program runs", True)

# ── 6. what a look cannot read ─────────────────────────────────────────────
print("== 6. a program that does not answer keeps the last look ==")
r = Rig()
r.look(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ✔ Connected"), codex=codex_json(stdio("demo-tool", "demo-mcp", [])))
r.clock[0] += 600
r.runner.timeout_list = {"claude"}
r.runner.listing["codex"] = "error: cannot read config"
ans = r.engine.detect()
check("a timeout and unreadable output: 'unknown', and what was found before is kept",
      ans["read"] == {"claude": "unknown", "codex": "unknown"} and r.st()["state"] == "connected" and proto(r.st(), "local")["state"] == "found", (ans, r.st()["protocols"]))
check("…the look's own time moves on", r.st()["detected_at"] == 1_000_600)
r.runner.timeout_list = set()
r.runner.listing["claude"] = claude_list()
r.look()
check("…and once it answers, gone is gone", r.st()["state"] == "not_started" and proto(r.st(), "connector")["state"] == "none")
r = Rig(installed=("codex",))
ans = r.look(claude=claude_list("claude.ai Demo: x - ✔ Connected"), codex=codex_json())
check("a program that is not installed is 'missing' and not asked", ans["read"]["claude"] == "missing" and not any(c[0].endswith("/claude") for c in r.runner.calls))
r = Rig()
r.look(claude=claude_list(), codex=codex_json())
check("the programs are asked read-only: `claude mcp list` and `codex mcp list` (not --json), nothing else",
      [c[1:] for c in r.runner.calls] == [["mcp", "list"], ["mcp", "list"]], r.runner.calls)
check("no state file was written by a look", not os.path.exists(r.engine.store.path))

# ── 7. api_connect: in the background, never waited for ────────────────────
print("== 7. api_connect looks on a thread of its own ==")
import api_host  # noqa: E402

api_host.bind({"load_config": lambda: {"accounts": {}}, "update_config": lambda fn: None,
               "ACCOUNT_MANAGER": type("AM", (), {"session_refs": lambda s, cfg: {}, "env_for": lambda s, p, r: {}})()})
import api_connect  # noqa: E402

api_connect._dlog = lambda c, m: LOG.append(m)


class Api(api_connect.ConnectApiMixin):
    def __init__(self):
        self.rig = Rig()
        self._connect_engine = self.rig.engine
        self.gate = threading.Event()
        self.gate.set()
        real = self.rig.runner

        def slow(argv, timeout=40, env=None):          # a look takes a while: the gate holds it
            self.gate.wait(5)
            return real(argv, timeout, env)
        self.rig.engine.clis.runner = slow


def wait_for(cond, limit=3.0):
    end = time.time() + limit
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


api = Api()
api.rig.runner.listing.update(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ✔ Connected"), codex=codex_json())
api.gate.clear()
t0 = time.time()
first = json.loads(api.connect_list())
check("the first connect_list answers at once, says a look is running, and shows what it knew (nothing yet)",
      time.time() - t0 < 1.0 and first["detect"] == {"running": True, "at": 0} and first["services"][0]["state"] == "not_started", first["detect"])
again = json.loads(api.connect_list(json.dumps({"detect": True})))
check("…a second call while it runs starts no second look", again["detect"]["running"] and len(api.rig.runner.calls) == 0)
api.gate.set()
check("…when it is done the next connect_list has it: connected via the connector", wait_for(lambda: not json.loads(api.connect_list())["detect"]["running"])
      and json.loads(api.connect_list())["services"][0]["via"]["route"] == "connector" and json.loads(api.connect_list())["detect"]["at"] == 1_000_000)
n = len(api.rig.runner.calls)
json.loads(api.connect_list(json.dumps({"detect": True})))
time.sleep(0.1)
check("the page's own ask looks again only after a few minutes", len(api.rig.runner.calls) == n)
api._connect_detect_last -= api_connect.DETECT_EVERY_S + 1
json.loads(api.connect_list(json.dumps({"detect": True})))
check("…and then does", wait_for(lambda: len(api.rig.runner.calls) > n and not api._connect_detecting))
n = len(api.rig.runner.calls)
json.loads(api.connect_list())
time.sleep(0.1)
check("a plain connect_list (the sidebar's poll) never looks again once a look was made", len(api.rig.runner.calls) == n)
d = json.loads(api.connect_detect())
time.sleep(0.1)
check("'Look again' right after a look: not more often than every few seconds", d["success"] and len(api.rig.runner.calls) == n)
api._connect_detect_last -= api_connect.DETECT_FLOOR_S + 1
api.rig.runner.listing["claude"] = claude_list("claude.ai Demo: https://demo.example/mcp - ! Needs authentication")
d = json.loads(api.connect_detect())
check("'Look again' later looks now, in the background", d["success"] and wait_for(lambda: not api._connect_detecting)
      and json.loads(api.connect_status("demo"))["status"]["state"] == "not_started")
api2 = Api()
api2.rig.runner.listing.update(claude=claude_list("claude.ai Demo: https://demo.example/mcp - ✔ Connected"), codex=codex_json())
told = []
import bridge_home  # noqa: E402
bridge_home.tell_all = lambda a, text: told.append(text) or 1
calls_before = len(api2.rig.runner.calls)
api2._connect_hourly_run()
check("the hourly check does not look (no `mcp list`): a look starts every MCP server Claude has",
      not [c for c in api2.rig.runner.calls[calls_before:] if c[1:3] == ["mcp", "list"]], api2.rig.runner.calls[calls_before:])
api2._connect_detect_kick(force=True)
for _ in range(100):
    if not getattr(api2, "_connect_detecting", False) and api2._connect_engine.detected is not None:
        break
    time.sleep(0.02)
api2._connect_hourly_run()
check("…it counts what the last look found, so the phone's list says connected", api2._connect_compact()[0]["state"] == "connected" and told == [],
      api2._connect_compact())
out = api2._connect_sfctl({"action": "list"})
check("sfctl connect list says connected and which way", out["success"] and "Demo: connected (the Claude account's connector" in out["message"], out["message"])
js = json.loads(api2._connect_sfctl({"action": "status", "service": "demo", "json": True})["message"])
api2._connect_engine.start("demo", route="local")
out = api2._connect_sfctl({"action": "start", "service": "demo"})
check("sfctl connect start on a service connected another way: it says so, and which of ShellBot's own steps is next",
      out["success"] and "connected (the Claude account's connector" in out["message"] and "Now: Check the tool" not in out["message"]
      and "Now: Add to the AIs" in out["message"] and "Settings → Connect" in out["message"], out["message"])
api2._connect_engine.disconnect("demo")
check("sfctl connect status --json carries via and protocols", js["status"]["via"]["route"] == "connector" and len(js["status"]["protocols"]) == 3)
check("connect_detect is a js_api name of the mixin", hasattr(api_connect.ConnectApiMixin, "connect_detect"))

# ── 8. the shipped descriptions ────────────────────────────────────────────
print("== 8. the shipped descriptions ==")
SERVICES_DIR = HERE / "connectors" / "services"
ship = registry.Registry(dirs=[SERVICES_DIR])
check("every shipped description (with its detect fields) is valid", not ship.errors, ship.errors)
for sid in ship.ids():
    d = ship.get(sid)
    for rt in d["routes"]:
        if rt["kind"] == "claude_connector":
            ad = ship.adapter(sid)
            prefix = getattr(ad, "prefix", "") or getattr(sys.modules.get(type(ad).__module__), "PREFIX", {}).get(rt["id"], "")
            want = "mcp__claude_ai_" + detect.connector_name(d, rt).replace(" ", "_") + "__"
            check(f"{sid}: the listed connector name agrees with the adapter's tool names", prefix == want and "connector" in (rt.get("detect") or {}), (prefix, want))
        if rt["kind"] == "local_mcp":
            pk = detect.packages(rt)
            check(f"{sid}: the local route's package is the one its server runs", pk and all(detect._runs(clis.server_command(rt["mcp"]), set([p])) for p in pk), pk)
bad = fake.demo()
bad["routes"][1]["detect"] = {"connector": "Demo"}
check("detect.connector only on a connector route; packages only with a server; nothing else", any("detect.connector" in e for e in registry.validate(bad))
      and any("detect" in e for e in registry.validate(dict(fake.demo(), routes=[dict(fake.demo()["routes"][0], detect={"pkgs": ["x"]})] + fake.demo()["routes"][1:])))
      and any("detect.packages" in e for e in registry.validate(dict(fake.demo(), routes=[dict(fake.demo()["routes"][0], detect={"packages": ["x"]})] + fake.demo()["routes"][1:]))))

# a typical shape: the Claude tabs' account folder, its connectors, and no local server for mail there
TYPICAL = claude_list(
    "claude.ai Google Drive: https://drive.example/mcp - ✔ Connected",
    "claude.ai Slack: https://chat.example/mcp - ! Needs authentication",
    "claude.ai Google Calendar: https://cal.example/mcp - ✔ Connected",
    "claude.ai Gmail: https://mail.example/mcp - ✔ Connected",
    "claude.ai Notion: https://pages.example/mcp - ! Needs authentication",
    "claude.ai Google Docs: https://docs.example/mcp - ✔ Connected",
    "demo-local: npx -y demo-local-mcp - ✔ Connected",
)
sr = fake.FakeRunner()
sr.listing.update(claude=TYPICAL, codex=codex_json(stdio("my-mail", "npx", ["-y", "@dguido/google-workspace-mcp@3.4.4"]),
                                                     remote("notion", "https://pages.example/mcp", auth="not_logged_in")))
seng = eng_mod.Engine(registry=ship, path=os.path.join(tempfile.mkdtemp(prefix="connect-ship-"), "connectors.json"),
                      clis=clis.Clis(runner=sr, finder=fake.finder()), backend=fake.backend(), clock=lambda: 2_000_000.0, platform="mac")
seng.start("gmail", route="local")
seng.step("gmail", "done")                           # Gmail's own steps left at step 2 or so, as a person once did
before = seng.status("gmail")
seng.detect()
by = {s["service"]: s for s in seng.list()}
check("a typical shape: Gmail, Calendar, Drive connected through the Claude account's connector",
      all(by[s]["state"] == "connected" and by[s]["via"]["route"] == "connector" for s in ("gmail", "google_calendar", "google_drive")),
      {s: (by[s]["state"], by[s]["via"]) for s in by})
check("…Gmail was waiting at a step of its local setup; it is now connected, and the setup is listed as another way",
      before["state"] == "waiting_user" and proto(by["gmail"], "local")["setup"]["state"] == "waiting_user" and by["gmail"]["step"] is None)
check("…Gmail's local route: the person's own server in Codex is 🟡 found (adopted)", (proto(by["gmail"], "local")["state"], proto(by["gmail"], "local")["adopted"],
                                                                                    proto(by["gmail"], "local")["cli"]) == ("found", True, "codex"), proto(by["gmail"], "local"))
check("…Notion and Slack need authorizing: ⚠, still 'not connected', nobody is asked to reconnect",
      all(by[s]["state"] == "not_started" and proto(by[s], "connector")["state"] == "broken" and not by[s]["attention"] for s in ("notion", "slack")))
check("…a Codex entry for a service that has no such route is not taken for anything", proto(by["notion"], "connector")["places"][0]["cli"] == "claude"
      and len(proto(by["notion"], "connector")["places"]) == 1)
check("…the Mac apps (another kind of route) found nothing", all(by[s]["via"] is None for s in ("mac_reminders", "mac_calendar")))
check("…the phone's list: three connected, the same keys as before", sorted(c["id"] for c in map(health.compact, by.values()) if c["state"] == "connected")
      == ["gmail", "google_calendar", "google_drive"] and all(set(health.compact(s)) == {"id", "name", "icon", "state", "attention", "step"} for s in by.values()))

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
