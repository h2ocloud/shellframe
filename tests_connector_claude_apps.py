#!/usr/bin/env python3
"""The catalog's first services: four through the Claude account's connector (google_drive, google_calendar,
notion, slack: one shared adapter, connectors/services/_claude_connector.py, with each service's names) and
four apps on a Mac listed before they can be connected (mac_messages, mac_notes, mac_reminders, mac_calendar).

Every program and tab is a stand-in: nothing here runs claude, opens a tab, touches a real settings file, a
claude.ai account or the running app.

  - each description passes `registry.validate`, its adapter has what the steps name (`validate_adapter`), and
    its catalog fields say where it sits (a category whose name is the same in every description, recommended)
  - the route is the connector one: Claude through the connector, every other program `none` with a note
  - the test's question names its marker, asks for a count and forbids every change, in both languages
  - the judge: pass, not authorized yet, no tools, said OK without a call, failed, unclear, timeout, waiting for
    the person's OK; another service's tool does not count
  - the whole route through the engine to "connected", with no AI program's settings changed, and a result of
    codes and numbers only; one service's test tab is never closed by another's
  - the Mac apps: no routes, the reason in both languages, this computer's section, not startable

Run: .venv/bin/python tests_connector_claude_apps.py
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
import _connectors_fake as fake  # noqa: E402
from connectors import clis, engine as eng_mod, health, registry  # noqa: E402
from connectors.adapter import Ctx  # noqa: E402
from connectors.services import _claude_connector as cc  # noqa: E402

SERVICES_DIR = HERE / "connectors" / "services"
CONNECTOR = {"google_drive": ("Google_Drive", "DRIVE-TEST"), "google_calendar": ("Google_Calendar", "GCAL-TEST"),
             "notion": ("Notion", "NOTION-TEST"), "slack": ("Slack", "SLACK-TEST")}
MAC = ("mac_messages", "mac_notes", "mac_reminders", "mac_calendar")
CONTENT = "Demo item title 0000"          # what a tool could return: must never come back in a result

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


def load(sid):
    return json.loads((SERVICES_DIR / f"{sid}.json").read_text(encoding="utf-8"))


class Kit:
    """connect_tabs.TabKit's shape: open / command / ask / phase / read / close. One for the whole app."""

    def __init__(self):
        self.opened, self.asked, self.closed, self.alive, self.phases, self.snaps = [], [], [], set(), {}, {}

    def command(self, cli="claude"):
        return cli

    def open(self, cmd, title):
        sid = f"s{len(self.opened) + 1}"
        self.opened.append((cmd, title))
        self.alive.add(sid)
        self.phases[sid] = "waiting_ready"
        return sid

    def ask(self, sid, text, ready_timeout=90.0):
        self.asked.append((sid, text))
        self.phases[sid] = "asking"

    def phase(self, sid):
        return self.phases.get(sid, "closed") if sid in self.alive else "closed"

    def read(self, sid):
        return dict(self.snaps.get(sid) or {"alive": True, "state": "", "events": []}) if sid in self.alive else {"alive": False}

    def close(self, sid):
        self.closed.append(sid)
        self.alive.discard(sid)


CFG = {"accounts": {"global": {"claude": "p1"}, "profiles": {"claude": [{"id": "p1", "plan": "max"}]}}}


def ctx(kit, cancel=False, now=1000.0):
    return Ctx(service="x", route="connector", cfg=CFG, tabs=kit, cancel=cancel, now=lambda: now)


def turn(prompt, tools, reply, ended=True):
    ev = [{"kind": "user_msg", "text": prompt}] + [{"kind": "tool_call", "tool": t} for t in tools]
    ev.append({"kind": "assistant_text", "text": reply})
    return ev + ([{"kind": "turn_end"}] if ended else [])


# ── the descriptions and their adapters ────────────────────────────────────
shipped = registry.Registry()
check("every shipped description is valid (the catalog's first services among them)",
      not shipped.errors and set(CONNECTOR) | set(MAC) <= set(shipped.ids()), shipped.errors)
check("one category id has one name in every shipped description",
      registry.category_conflicts([shipped.get(i) for i in shipped.ids()]) == [])
WORDS = ("starting", "waiting_ready", "asking", "reading", "needs_allow", "pass", "said_ok", "timeout", "unclear", "not_ready",
         "closed", "start_failed", "needs_auth", "no_tools", "failed")
for sid, (tool, mark) in CONNECTOR.items():
    d = load(sid)
    mod = importlib.import_module(f"connectors.services.{sid}")
    ad = mod.ADAPTER
    print(f"{sid}")
    check("the description is valid and its adapter has every check and action it names",
          registry.validate(d) == [] and registry.validate_adapter(d, ad) == [], registry.validate(d) + registry.validate_adapter(d, ad))
    check("the adapter is this service's, with the connector's tool names and its own marker",
          ad.id == sid and ad.prefix == f"mcp__claude_ai_{tool}__" and ad.mark == mark and shipped.adapter(sid) is ad)
    check("the catalog: a category, recommended, every kind of computer", (d.get("category") or {}).get("id") in ("productivity", "communication")
          and d.get("recommended") is True and "platforms" not in d and registry.catalog_entry(d, "linux")["available"] is True)
    route = d["routes"][0]
    check("one route: the Claude account's connector, steps a1 a2 a3 (the test last)",
          len(d["routes"]) == 1 and route["kind"] == "claude_connector" and [s["id"] for s in route["steps"]] == ["a1", "a2", "a3"]
          and route["steps"][-1]["kind"] == "test")
    others = {c: v for c, v in route["clis"].items() if c != "claude"}
    check("Claude through the connector; every other program none, each with a note in both languages",
          route["clis"]["claude"]["via"] == "connector" and set(others) == set(clis.CLI_IDS) - {"claude"}
          and all(v["via"] == "none" and registry.is_text(v.get("note")) for v in others.values()))
    a2 = route["steps"][1]
    check("step 2 opens the connectors page on claude.ai and has the usual ways to get stuck",
          a2["url"] == "https://claude.ai/customize/connectors" and len(a2["stuck"]) >= 3 and a2.get("redo_on_reconnect") is True)
    check("the step a verdict sends the person back to is a step of the route", set(cc.FAILED_STEP.values()) <= {s["id"] for s in route["steps"]})
    check("there are words for every phase and verdict the adapter can come to", set(WORDS) <= set(route["test"]["verdicts"]),
          set(WORDS) - set(route["test"]["verdicts"]))
    check("no text promises read-only use (the connector decides what it may do)",
          "read-only" not in json.dumps(d).lower() and "只讀" not in json.dumps(d, ensure_ascii=False))
    os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
    zh = ad.prompt()
    os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
    en = ad.prompt()
    check("the test's question: one line, its marker, a count only, and no change of any kind (both languages)",
          "\n" not in en and mark in en and "count" in en and "do not create, send, change, share or delete" in en
          and "\n" not in zh and mark in zh and "不要新增、寄出、修改、分享或刪除" in zh, (en, zh))

# ── the judge ───────────────────────────────────────────────────────────────
print("the judge")
ad = importlib.import_module("connectors.services.google_drive").ADAPTER
q = ad.prompt()
P = ad.prefix
J = ad.judge
check("a call and the OK line: pass", J(turn(q, [P + "search"], f"3\n{ad.mark}: OK")) == {"state": "done", "verdict": "pass", "calls": 1})
check("only authenticate / complete_authentication: not authorized yet",
      J(turn(q, [P + "authenticate", P + "complete_authentication"], f"{ad.mark}: FAIL needs auth"))["verdict"] == "needs_auth")
check("no tool at all: no_tools; an OK without a call does not count", J(turn(q, [], f"{ad.mark}: FAIL none"))["verdict"] == "no_tools"
      and J(turn(q, [], f"{ad.mark}: OK"))["verdict"] == "said_ok")
check("another connector's tool does not count", J(turn(q, ["mcp__claude_ai_Notion__search"], f"{ad.mark}: OK"))["verdict"] == "said_ok")
check("a call and the FAIL line: failed; no marker: unclear", J(turn(q, [P + "search"], f"{ad.mark}: FAIL error"))["verdict"] == "failed"
      and J(turn(q, [P + "search"], "here you go"))["verdict"] == "unclear")
check("the marker is read in markdown, from the last two lines only", J(turn(q, [P + "x"], f"3\n**{ad.mark}: OK**"))["verdict"] == "pass"
      and J(turn(q, [P + "x"], f"{ad.mark}: OK\na\nb"))["verdict"] == "unclear")
check("still working: running; a menu on screen: waiting for the person", J(turn(q, [P + "x"], "", ended=False))["state"] == "running"
      and J(turn(q, [P + "x"], "", ended=False), "decision")["state"] == "needs_allow")
check("too long: timeout; the question not in yet: running, until too long",
      J(turn(q, [P + "x"], "", ended=False), "", cc.TEST_TIMEOUT_S + 1)["verdict"] == "timeout"
      and J([], "", 1)["state"] == "running" and J([], "", cc.TEST_TIMEOUT_S + 1)["verdict"] == "timeout")

# ── the whole route through the engine ──────────────────────────────────────
print("the route through the engine")
kit = Kit()
runner = fake.FakeRunner()
eng = eng_mod.Engine(path=os.path.join(tempfile.mkdtemp(prefix="connect-apps-"), "connectors.json"),
                     clis=clis.Clis(runner=runner, finder=fake.finder()), backend=fake.backend(),
                     ctx_factory=lambda s, r: Ctx(service=s, route=r, cfg=CFG, tabs=kit), clock=lambda: 1000.0, platform="mac")
for sid in CONNECTOR:
    res = eng.start(sid)
    check(f"{sid}: start checks how Claude is signed in (a claude.ai plan) and goes on to step 2 by itself",
          res["success"] and res["view"]["step"]["id"] == "a2", res.get("view", {}).get("step", {}).get("id"))
    res = eng.step(sid, "done")
    check(f"{sid}: 'I did it' on claude.ai opens the test", res["success"] and res["view"]["step"]["id"] == "a3")
    res = eng.test(sid)
    sid_tab = res["result"].get("sid")
    check(f"{sid}: the test opens a Claude tab and asks its one question", res["result"]["state"] == "running" and kit.asked[-1][0] == sid_tab
          and CONNECTOR[sid][1] in kit.asked[-1][1])
    a = shipped.adapter(sid)
    kit.snaps[sid_tab] = {"alive": True, "state": "", "events": turn(kit.asked[-1][1], [a.prefix + "list"], f"{CONTENT}\n{a.mark}: OK")}
    res = eng.test(sid, handle=res["result"]["handle"])
    check(f"{sid}: it passes and the service is connected", res["result"] == {"state": "done", "ok": True, "calls": 1, "sid": sid_tab}
          and res["status"]["state"] == "connected" and res["status"]["clis"]["claude"] == "connected"
          and res["status"]["clis"]["codex"] == "unsupported", res)
    check(f"{sid}: nothing of what came back is in the answer", CONTENT not in json.dumps(res))
check("no AI program's settings were touched on the way", runner.calls == [], runner.calls)

kit2 = Kit()
drive, notion = shipped.adapter("google_drive"), shipped.adapter("notion")
r1 = drive.test(ctx(kit2), "connector")
r2 = notion.test(ctx(kit2), "connector")
del kit2.closed[:]                          # a new test closes that service's tab left from before (the walk above): not counted here
drive.disconnect(ctx(kit2), "connector")
check("one service's clean-up closes its own test tab and never another's", kit2.closed == [r1["sid"]] and r2["sid"] in kit2.alive, kit2.closed)
res = notion.test(ctx(kit2, cancel=True), "connector", r2["handle"])
check("a cancelled test answers test_cancelled and closes its tab", res["code"] == "test_cancelled" and r2["sid"] in kit2.closed)
check("a route that is not the connector's cannot be tested", drive.test(ctx(kit2), "local")["code"] == "start_failed")
check("health is unknown (the connector lives on claude.ai)", drive.health(ctx(kit2), "connector") == {"state": "unknown"})

# ── the Mac apps ─────────────────────────────────────────────────────────────
for sid in MAC:
    d = load(sid)
    print(sid)
    check("valid, with no routes and the reason in both languages", registry.validate(d) == [] and d["routes"] == []
          and registry.is_text(d["unavailable"]["text"]) and registry.is_text(d["unavailable"]["next"]), registry.validate(d))
    check("an app on this computer, for a Mac, in a category", d["local_app"] is True and d["platforms"] == ["mac"]
          and (d.get("category") or {}).get("id") in ("productivity", "communication"))
    check("no adapter: there is nothing to run", not (SERVICES_DIR / f"{sid}.py").exists() and shipped.adapter(sid) is None)
    e = registry.catalog_entry(d, "mac")
    check("the catalog says it cannot be connected yet, and why", e["available"] is False and e["why"] == "not_available" and e["local_app"] is True)
    res = eng.start(sid)
    check("starting it says so in its own words and starts nothing", res["code"] == "not_available"
          and res["message"]["text"] == d["unavailable"]["text"] and eng.store.get(sid) == {})
    check("sfctl's line says it cannot be connected here yet", "cannot be connected here yet" in health.words(eng.status(sid), "en"))

# ── the core's other paths over every shipped service (the hourly check must not stop on one with no routes) ──
print("the health check and disconnect over the shipped services")
try:
    out = eng.refresh_all(force=True)
    raised = ""
except Exception as exc:                 # the hourly thread swallows this: it would stop the check for every service
    out, raised = {}, repr(exc)
check("refresh_all over every shipped service raises nothing, and none of these needs attention",
      not raised and not [a for a in out.get("attention", []) if a["id"] in set(CONNECTOR) | set(MAC)], raised or out)
res = eng.refresh("mac_notes", force=True)
check("a health check of a Mac app answers, starts nothing and keeps it not connected",
      res["success"] and res["status"]["state"] == "not_started" and eng.store.get("mac_notes") == {}, res)
res = eng.disconnect("mac_notes")
check("disconnecting a Mac app answers and changes nothing", res["success"] and eng.status("mac_notes")["state"] == "not_started", res)
res = eng.disconnect("notion")
check("disconnecting a connector service forgets it and touches no AI program's settings",
      res["success"] and eng.status("notion")["state"] == "not_started" and runner.calls == [], (res, runner.calls))

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
