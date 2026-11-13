#!/usr/bin/env python3
"""Gmail on the connector core: the description (connectors/services/gmail.json) and the adapter
(connectors/services/gmail.py), with every program and tab replaced by a stand-in. Nothing here runs claude,
node or npx, opens a tab, or touches a real settings file, token, keychain, Google account or the running app.

  - the description passes the core's `registry.validate` when the core is there; the same structure is
    checked here either way (every text has both languages, every route ends in a test, the scopes, the
    checks and actions the steps name exist in the adapter, the server's settings match the adapter's)
  - every route is walked from its first step to "connected" against stand-in ctx objects
  - the Google sign-in command carries GOOGLE_WORKSPACE_SERVICES=gmail (and READ_ONLY when read-only), so Google
    is asked for the mailbox only (B7)
  - the connection test: every verdict, "waiting for your OK", cancel; the result holds codes and numbers only,
    never the sender, the subject or a reply
  - no login file is opened (checks look at names and sizes), no secret reaches a command or a result

Run: .venv/bin/python tests_connector_gmail.py
"""
import builtins
import json
import os
import re
import stat
import sys
import shlex
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
_TMP = Path(tempfile.mkdtemp(prefix="sf-gmail-adapter-"))
os.environ.setdefault("SHELLFRAME_CONFIG_DIR", str(_TMP / "shellbot"))

import _connectors_fake as fake
from connectors import clis as clis_mod, engine as eng_mod, registry
from connectors.adapter import Ctx
from connectors.clis import Result
import connectors.services.gmail as gmail

SERVICES_DIR = HERE / "connectors" / "services"
DESC = json.loads((SERVICES_DIR / "gmail.json").read_text(encoding="utf-8")) if (SERVICES_DIR / "gmail.json").exists() else None

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")
        for line in str(detail).splitlines()[:12]:
            print(f"         {line}")


# ── stand-ins ─────────────────────────────────────────────────────────────────
SENDER, SUBJECT = "demo-sender@example.invalid", "Demo subject line 0000"      # what a mail would say: must never come back
CLIENT_SECRET, TOKEN = "demo-client-secret-not-real", "demo-token-not-real"      # what the login files would hold: never opened


class World:
    """The tabs of a stand-in app: what was opened, what was typed into them, what was closed, what a tab shows."""

    def __init__(self, win=False):
        self.opened, self.sent, self.closed, self.alive, self.phase = [], [], [], set(), {}
        self.snap = {"alive": True, "state": "", "events": []}
        self.ready, self.defer, self.fail_open, self.win = True, False, False, win
        self.folder = "/fake/config/connect-test"


class FakeTabKit:
    """connect_tabs.TabKit (open / folder / command / ask / phase / read / close). The app has one for the
    whole app, so each tab's phase lives in the world, not in the instance."""

    def __init__(self, world):
        self.w = world
        self._phase = world.phase

    def folder(self):
        return self.w.folder

    def command(self, cli="claude"):
        return cli if self.w.win else f"cd {shlex.quote(self.w.folder)} && {cli}"

    def open(self, cmd, title):
        if self.w.fail_open:
            raise OSError("no pty")
        self.w.opened.append((cmd, title))
        sid = f"s{len(self.w.opened)}"
        self.w.alive.add(sid)
        self._phase[sid] = "waiting_ready"
        return sid

    def ask(self, sid, text, ready_timeout=90.0):
        if self.w.defer:
            return
        if not self.w.ready:
            self._phase[sid] = "not_ready"
            return
        self.w.sent.append((sid, text))
        self._phase[sid] = "asking"

    def phase(self, sid):
        return "closed" if sid not in self.w.alive else self._phase.get(sid, "closed")

    def read(self, sid):
        return dict(self.w.snap) if sid in self.w.alive else {"alive": False, "state": "", "events": []}

    def close(self, sid):
        self.w.closed.append(sid)
        self.w.alive.discard(sid)
        self._phase.pop(sid, None)


class _WorldView:
    """What a test reads off a ctx or a rig: the world's lists, and the two things a test sets."""

    @property
    def opened(self):
        return self.world.opened

    @property
    def sent(self):
        return self.world.sent

    @property
    def closed(self):
        return self.world.closed

    @property
    def snap(self):
        return self.world.snap

    @snap.setter
    def snap(self, v):
        self.world.snap = v

    @property
    def ready(self):
        return self.world.ready

    @ready.setter
    def ready(self, v):
        self.world.ready = v


class FakeCtx(_WorldView):
    """What the core hands an adapter (connectors.adapter.Ctx): run, find, open_tab, open_path, tabs, now, cancel.
    Every program the adapter asks for is answered from `answers`; none is run."""

    def __init__(self, home, xdg=None, bins=None, answers=None, cfg=None, win=False, world=None):
        self.service, self.route, self.lang, self.win, self.cancel = "gmail", "", "en", win, False
        self.cfg = cfg if cfg is not None else {}
        self.env = {"HOME": str(home), "PATH": "/nowhere"}
        if xdg:
            self.env["XDG_CONFIG_HOME"] = str(xdg)
        self.bins = dict(bins or {})
        self.answers = dict(answers or {})
        self.runs, self.paths = [], []
        self.t = 1000.0
        self.world = world or World(win)
        self.tabs = FakeTabKit(self.world)

    def run(self, argv, timeout, env=None):
        self.runs.append((list(argv), timeout))
        return self.answers.get(tuple(argv), Result(1, ""))

    def find(self, binary):
        return self.bins.get(binary)

    def open_tab(self, cmd, title):
        return FakeTabKit(self.world).open(cmd, title)

    def open_path(self, path):
        self.paths.append(path)

    def now(self):
        return self.t


OPENED = []
_real_open = builtins.open


def _guarded_open(file, *a, **kw):
    OPENED.append(str(file))
    return _real_open(file, *a, **kw)


def new_pc(name, xdg=True):
    root = _TMP / name
    home = root / "home"
    home.mkdir(parents=True)
    cfg_home = root / "xdg" if xdg else None
    if cfg_home:
        cfg_home.mkdir()
    return home, cfg_home


# ── 1. the pure parts ─────────────────────────────────────────────────────────
print("== B7: the sign-in command asks Google for the mailbox only ==")
ro = gmail.auth_command(True, False)
rw = gmail.auth_command(False, False)
win = gmail.auth_command(True, True)
core = lambda d: {k: v for k, v in (d or {}).items() if k not in ("calls", "sid")}   # a test result past its count and its tab

check("read-only sign-in: services=gmail and READ_ONLY=true", "GOOGLE_WORKSPACE_SERVICES=gmail" in ro and "GOOGLE_WORKSPACE_READ_ONLY=true" in ro, ro)
check("read-write sign-in: services=gmail, no READ_ONLY", "GOOGLE_WORKSPACE_SERVICES=gmail" in rw and "READ_ONLY" not in rw, rw)
check("the sign-in names the shellbot profile on the command line", ro.endswith("npx -y @dguido/google-workspace-mcp auth --profile shellbot"), ro)
check("only Gmail: no other Google service named", not re.search(r"drive|docs|calendar|sheets|slides", ro, re.I), ro)
check("Windows sign-in: the same two settings in PowerShell form",
      "$env:GOOGLE_WORKSPACE_SERVICES='gmail'" in win and "$env:GOOGLE_WORKSPACE_READ_ONLY='true'" in win and win.startswith("powershell"), win)
check("no secret shape in the sign-in command", not re.search(r"sk-|token|secret|password", ro + rw + win, re.I))

print("== the profile folder ==")
h, x = new_pc("pf1")
check("XDG_CONFIG_HOME (absolute) wins", gmail.profile_dir({"XDG_CONFIG_HOME": str(x), "HOME": str(h)}) == str(x / "google-workspace-mcp" / "profiles" / "shellbot"))
check("no XDG: ~/.config", gmail.profile_dir({"HOME": str(h)}) == str(h / ".config" / "google-workspace-mcp" / "profiles" / "shellbot"))
check("a relative XDG_CONFIG_HOME is ignored (as the server does)", gmail.profile_dir({"XDG_CONFIG_HOME": "rel/path", "HOME": str(h)}).startswith(str(h / ".config")))
check("a folder under home is shown as ~/…", gmail.shown_path(str(h / ".config" / "x"), str(h)) == "~/.config/x")
check("a folder elsewhere is shown whole, forward slashes", gmail.shown_path("C:\\a\\b", "D:\\home") == "C:/a/b")

print("== how Claude is signed in (the account label, never a login file) ==")
cfg_plan = {"accounts": {"global": {"claude": "p1"}, "profiles": {"claude": [{"id": "p1", "plan": "max", "email": SENDER}]}}}
ctx = FakeCtx(*new_pc("acct"), cfg=cfg_plan)
res = gmail.ADAPTER.checks["claude_account"](ctx, {})
check("a plan on record: claude_ai with the plan", res == {"state": "claude_ai", "plan": "Max"}, res)
check("the e-mail label is never returned", SENDER not in json.dumps(res))
check("no plan: unknown", gmail.ADAPTER.checks["claude_account"](FakeCtx(*new_pc("acct2"), cfg={"accounts": {"global": {"claude": "p1"}, "profiles": {"claude": [{"id": "p1"}]}}}), {})["state"] == "unknown")
check("no accounts at all: unknown", gmail.ADAPTER.checks["claude_account"](FakeCtx(*new_pc("acct3")), {})["state"] == "unknown")

print("== Node.js ==")
home, xdg = new_pc("node")
NODE = "/fake/bin/node"
for label, bins, out, want in (
        ("missing", {}, None, {"state": "missing"}),
        ("too old", {"node": NODE}, Result(0, "v20.11.1\n"), {"state": "old", "version": "20.11.1"}),
        ("fine", {"node": NODE}, Result(0, "v22.17.1\n"), {"state": "ok", "version": "22.17.1"}),
        ("a newer one", {"node": NODE}, Result(0, "v24.0.0\n"), {"state": "ok", "version": "24.0.0"}),
        ("found but gives no version", {"node": NODE}, Result(0, "hello"), {"state": "missing"}),
        ("found but fails", {"node": NODE}, Result(1, "v22.17.1"), {"state": "missing"})):
    c = FakeCtx(home, xdg, bins=bins, answers={(NODE, "--version"): out} if out else {})
    got = gmail.ADAPTER.checks["node"](c, {})
    check(f"node {label}: {want['state']}", got == want, got)
c = FakeCtx(home, xdg, bins={"node": NODE}, answers={(NODE, "--version"): Result(0, "v22.0.0")})
gmail.ADAPTER.checks["node"](c, {})
check("the version command runs with a timeout, through ctx.run", c.runs == [([NODE, "--version"], 10)], c.runs)

print("== the OAuth client's file and the sign-in's file: names and sizes only ==")
home, xdg = new_pc("files")
c = FakeCtx(home, xdg)
prof = Path(gmail.profile_dir(c.env))
OPENED.clear()
builtins.open = _guarded_open
try:
    states = [gmail.ADAPTER.checks["credentials"](c, {})["state"]]            # no folder
    prof.mkdir(parents=True)
    states.append(gmail.ADAPTER.checks["credentials"](c, {})["state"])        # empty folder
    (prof / "credentials.json.json").write_text(CLIENT_SECRET)
    states.append(gmail.ADAPTER.checks["credentials"](c, {})["state"])
    (prof / "credentials.json.json").unlink()
    (prof / "client_secret_123.apps.googleusercontent.com.json").write_text(CLIENT_SECRET)
    states.append(gmail.ADAPTER.checks["credentials"](c, {})["state"])
    (prof / "credentials.json").write_text("")
    states.append(gmail.ADAPTER.checks["credentials"](c, {})["state"])
    (prof / "credentials.json").write_text(CLIENT_SECRET)
    states.append(gmail.ADAPTER.checks["credentials"](c, {})["state"])
    tok = [gmail.ADAPTER.checks["tokens"](c, {})["state"]]
    (prof / "tokens.json").write_text(TOKEN)
    tok.append(gmail.ADAPTER.checks["tokens"](c, {})["state"])
    health_ok = gmail.ADAPTER.health(c, "local")
    (prof / "tokens.json").unlink()
    health_gone = gmail.ADAPTER.health(c, "local")
    health_conn = gmail.ADAPTER.health(c, "connector")
finally:
    builtins.open = _real_open
# (the stand-in files were written through pathlib, which does not go through builtins.open)
check("credentials: no_folder, missing, double_ext, downloaded_name, empty, ok",
      states == ["no_folder", "missing", "double_ext", "downloaded_name", "empty", "ok"], states)
check("tokens: missing, then ok", tok == ["missing", "ok"], tok)
check("no login file was opened by a check or by health", not [p for p in OPENED if "google-workspace-mcp" in p], OPENED)
check("health, local: the sign-in file is there -> ok", health_ok == {"state": "ok"}, health_ok)
check("health, local: Google took it back (file gone) -> revoked", health_gone == {"state": "revoked"}, health_gone)
check("health, connector: unknown (lives on claude.ai)", health_conn == {"state": "unknown"}, health_conn)

print("== the actions ==")
home, xdg = new_pc("acts")
c = FakeCtx(home, xdg)
res = gmail.ADAPTER.actions["open_folder"](c, {})
d = Path(gmail.profile_dir(c.env))
check("open_folder makes the profile folder and opens it", d.is_dir() and c.paths == [str(d)], (d, c.paths))
check("the folder is private to the person", stat.S_IMODE(d.stat().st_mode) == 0o700, oct(d.stat().st_mode))
check("open_folder says opened and where (no user name)", res["state"] == "opened" and str(home) not in res["path"], res)
home2, _ = new_pc("acts2", xdg=False)
c2 = FakeCtx(home2)
res2 = gmail.ADAPTER.actions["open_folder"](c2, {})
check("under home it is shown as ~/.config/…", res2["path"] == "~/.config/google-workspace-mcp/profiles/shellbot", res2)
for label, args, win_, want_ro in (("default", {}, False, True), ("options ticked", {"options": {"readonly": True}}, False, True),
                                   ("options unticked", {"options": {"readonly": False}}, False, False),
                                   ("flat unticked", {"readonly": False}, False, False), ("Windows", {}, True, True)):
    c3 = FakeCtx(home, xdg, win=win_)
    r3 = gmail.ADAPTER.actions["auth_tab"](c3, args)
    cmd = c3.opened[0][0]
    ok = (r3 == {"state": "opened", "sid": "s1"} and cmd == gmail.auth_command(want_ro, win_)
          and ("READ_ONLY" in cmd) == want_ro and "GOOGLE_WORKSPACE_SERVICES" in cmd)
    check(f"auth_tab ({label}): opens a tab with the {'read-only ' if want_ro else ''}sign-in", ok, (r3, cmd))
check("the sign-in tab is named for what it does", c3.opened[0][1] == "Gmail sign-in", c3.opened)

print("== the adapter is a plain module: no login file, no program of its own ==")
src = (SERVICES_DIR / "gmail.py").read_text(encoding="utf-8")
code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
check("it does not run programs or threads itself (programs go through ctx.run, the tab kit waits in its own thread)",
      "subprocess" not in code and "os.system" not in code and "Popen" not in code and "threading.Thread" not in code)
check("it never opens or reads a file", not re.search(r"(?<![\w.])open\(|read_text|read_bytes|json\.load", code))
check("it does not read the environment of the app (ctx.env only)", "os.environ" not in code and "getenv" not in code)
check("no key or token value is written in it", not re.search(r"sk-ant|Bearer ", code))

# ── 2. the description ────────────────────────────────────────────────────────
print("== the description (gmail.json) ==")
TEXT_KEYS = {"name", "label", "summary", "blurb", "title", "intro", "see", "note", "text", "hint", "q", "a", "next", "help"}
ROUTE_KINDS = {"claude_connector", "local_mcp", "api_key", "oauth"}
TONES = {"good", "warn", "bad", "info"}
BUILTINS = {"cli_add", "cli_remove", "cli_check", "secret_set", None}


def is_text(v):
    return isinstance(v, dict) and set(v) == {"en", "zh"} and all(isinstance(x, str) and x.strip() for x in v.values())


def text_problems(node, path="$"):
    """Every slot that is a text must hold {en, zh}, both filled; `how` and `needs` are lists of them."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in TEXT_KEYS:
                if not is_text(v):
                    out.append(f"{path}.{k} is not a filled {{en, zh}}: {str(v)[:60]}")
                continue
            if k in ("how", "needs"):
                if not (isinstance(v, list) and v and all(is_text(i) for i in v)):
                    out.append(f"{path}.{k} is not a list of {{en, zh}}")
                continue
            out += text_problems(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += text_problems(v, f"{path}[{i}]")
    return out


def structure_problems(desc, adapter):
    out = []
    if desc.get("schema") != 1 or desc.get("id") != adapter.id:
        out.append("schema/id")
    scopes = {s["id"]: s for s in desc.get("scopes", [])}
    required = {i for i, s in scopes.items() if s.get("required")}
    clis_known = {"claude", "codex", "pi", "opencode", "agy", "grok", "cursor"}
    for r in desc["routes"]:
        rid = r["id"]
        steps = {s["id"]: s for s in r["steps"]}
        if len(steps) != len(r["steps"]):
            out.append(f"{rid}: duplicate step ids")
        if r["kind"] not in ROUTE_KINDS:
            out.append(f"{rid}: kind")
        if r["steps"][-1].get("kind") != "test":
            out.append(f"{rid}: the last step is not the test")
        if [s["id"] for s in r["steps"] if s.get("kind") == "test"] != [r["steps"][-1]["id"]]:
            out.append(f"{rid}: more than one test step")
        if not set(r["scopes"]) <= set(scopes) or not required <= set(r["scopes"]):
            out.append(f"{rid}: scopes {r['scopes']}")
        if not set(r.get("clis", {})) <= clis_known:
            out.append(f"{rid}: unknown cli {set(r['clis']) - clis_known}")
        if any(c.get("via") not in ("connector", "mcp", "none") for c in r.get("clis", {}).values()):
            out.append(f"{rid}: cli via")
        for s in r["steps"]:
            ck = s.get("check")
            if ck:
                if ck["id"] not in adapter.checks:
                    out.append(f"{rid}/{s['id']}: check {ck['id']} is not in the adapter")
                if ck["on"] not in ("enter", "done") or not set(ck["pass"]) <= set(ck["states"]):
                    out.append(f"{rid}/{s['id']}: check on/pass")
            for a in s.get("actions", []):
                if a["builtin"] not in BUILTINS:
                    out.append(f"{rid}/{s['id']}: builtin {a['builtin']}")
                if a["builtin"] is None and a["id"] not in adapter.actions:
                    out.append(f"{rid}/{s['id']}: action {a['id']} is not in the adapter")
                if a["builtin"] in ("cli_add", "cli_remove", "cli_check") and not r.get("mcp"):
                    out.append(f"{rid}/{s['id']}: {a['builtin']} needs route.mcp")
                if a.get("after") not in (None, "check"):
                    out.append(f"{rid}/{s['id']}: after")
            for q in s.get("stuck", []):
                for ln in q.get("links", []):
                    if ("url" in ln) == ("step" in ln) or ("step" in ln and ln["step"] not in steps):
                        out.append(f"{rid}/{s['id']}: link {ln}")
        for e in (r.get("mcp") or {}).get("env", {}).values():
            if not isinstance(e, str):
                out.append(f"{rid}: mcp env")
        verdicts = r["test"]["verdicts"]
        if not is_text(r["test"]["via"]):
            out.append(f"{rid}: test.via")
        for code in ("pass", "needs_allow", "starting", "waiting_ready", "asking", "reading", "said_ok", "timeout", "unclear",
                     "not_ready", "closed", "start_failed", "no_tools", "failed") + (("needs_auth",) if rid == "connector" else ()):
            if code not in verdicts:
                out.append(f"{rid}: no words for {code}")
        for code, step_id in gmail.FAILED_STEP.get(rid, {}).items():
            if step_id not in steps or code not in verdicts:
                out.append(f"{rid}: failed_step {code}->{step_id}")
    if not all(is_text(q["q"]) and is_text(q["a"]) for q in desc.get("faq", [])):
        out.append("faq")
    return out


check("gmail.json exists", DESC is not None)
if DESC is not None:
    check("every text has both languages, filled", not text_problems(DESC), "\n".join(text_problems(DESC)[:8]))
    tones = []

    def collect(o):
        if isinstance(o, dict):
            if "tone" in o:
                tones.append(o["tone"])
            for v in o.values():
                collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)
    collect(DESC)
    check("every tone is one the core knows (good, warn, bad, info)", tones and set(tones) <= TONES, set(tones) - TONES)
    check("the structure holds together (steps, checks, actions, scopes, links, verdicts)", not structure_problems(DESC, gmail.ADAPTER),
          "\n".join(structure_problems(DESC, gmail.ADAPTER)[:8]))
    check("registry.validate passes", registry.validate(DESC) == [], registry.validate(DESC))
    check("the adapter has every check and action the description names (registry.validate_adapter)",
          registry.validate_adapter(DESC, gmail.ADAPTER) == [], registry.validate_adapter(DESC, gmail.ADAPTER))
    check("the registry loads it with its adapter, nothing skipped", registry.Registry(dirs=[SERVICES_DIR]).adapter("gmail") is gmail.ADAPTER
          and "gmail" in registry.Registry(dirs=[SERVICES_DIR]).ids() and not registry.Registry(dirs=[SERVICES_DIR]).errors)
    routes = {r["id"]: r for r in DESC["routes"]}
    check("two routes: the connector first and recommended, then the local server",
          [r["id"] for r in DESC["routes"]] == ["connector", "local"] and routes["connector"]["recommended"] and not routes["local"]["recommended"]
          and routes["connector"]["kind"] == "claude_connector" and routes["local"]["kind"] == "local_mcp")
    check("connector: sign-in check, connect on claude.ai, test", [s["id"] for s in routes["connector"]["steps"]] == ["a1", "a2", "a3"])
    check("local: Node, Cloud project, API, consent, client, file, add, sign in, test",
          [s["id"] for s in routes["local"]["steps"]] == ["b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8"])
    check("the connector reaches Claude only; the local server reaches the others",
          [c for c, v in routes["connector"]["clis"].items() if v["via"] == "connector"] == ["claude"]
          and all(routes["local"]["clis"][c]["via"] == "mcp" for c in ("claude", "codex", "opencode", "agy", "grok", "cursor"))
          and routes["local"]["clis"]["pi"]["via"] == "mcp")
    check("each other AI has its settings file named in its note", all(f in routes["local"]["clis"][c]["note"]["en"] for c, f in (
        ("codex", "~/.codex/config.toml"), ("opencode", "opencode.json"), ("agy", "~/.gemini/config/mcp_config.json"),
        ("grok", "~/.grok/config.toml"), ("cursor", "~/.cursor/mcp.json"), ("pi", "~/.pi/agent/mcp.json"))))
    n_stuck = sum(len(s.get("stuck", [])) for r in DESC["routes"] for s in r["steps"])
    check("SA4's questions are all kept (35 'I am stuck' entries)", n_stuck == 35, n_stuck)
    mcp = routes["local"]["mcp"]
    check("the server's settings: shellbot- name, npx, the package, a Windows form",
          mcp["server"] == gmail.SERVER and mcp["server"].startswith("shellbot-") and mcp["command"] == "npx"
          and mcp["args"] == ["-y", gmail.PACKAGE] and mcp["win_command"] == ["cmd", "/c", "npx", "-y", gmail.PACKAGE], mcp)
    check("B7 for the server: services=gmail, READ_ONLY=true, the shellbot profile, nothing else",
          mcp["env"] == {"GOOGLE_WORKSPACE_MCP_PROFILE": gmail.PROFILE, "GOOGLE_WORKSPACE_SERVICES": "gmail", "GOOGLE_WORKSPACE_READ_ONLY": "true"}, mcp["env"])
    check("no secret field in the route: no secrets list, no env_from_secret", "secrets" not in routes["local"] and "env_from_secret" not in mcp)
    check("the test's tool prefix follows the server's name", gmail.PREFIX["local"] == f"mcp__{mcp['server']}__")
    check("the minimal scope is Gmail alone (read), and sending is optional", [s["id"] for s in DESC["scopes"] if s["required"]] == ["read"]
          and all(r["scopes"] == ["read"] for r in DESC["routes"]))
    check("the server is added with the core's cli_add, the sign-in with the adapter's",
          [(a["id"], a["builtin"]) for a in routes["local"]["steps"][6]["actions"]] == [("cli_add", "cli_add")]
          and [(a["id"], a["builtin"]) for a in routes["local"]["steps"][7]["actions"]] == [("auth_tab", None)])
    check("'Read mail only' is on by default at both steps",
          all(o["default"] for i in (6, 7) for o in routes["local"]["steps"][i]["options"]) and
          [o["id"] for i in (6, 7) for o in routes["local"]["steps"][i]["options"]] == ["readonly", "readonly"])
    blob = json.dumps(DESC, ensure_ascii=False)
    check("nothing in it is a secret or an e-mail address of anyone's",
          not re.search(r"sk-ant|Bearer|@gmail\.com|@[a-z0-9-]+\.(com|tw)\b", blob, re.I))

# ── 3. the connection test ────────────────────────────────────────────────────
print("== the connection test: a temporary tab, tool names and the last line only ==")
CONNECTOR_TOOL = "mcp__claude_ai_Gmail__search_threads"
LOCAL_TOOL = "mcp__shellbot-gmail__search_emails"
AUTH = "mcp__claude_ai_Gmail__authenticate"
user = {"kind": "user_msg", "text": gmail.test_prompt()}
tool = lambda n: {"kind": "tool_call", "tool": n}
mail = {"kind": "tool_result", "text": f"From: {SENDER} Subject: {SUBJECT}"}
say = lambda t: {"kind": "assistant_text", "text": t}
end = {"kind": "turn_end"}
ALLOWED_KEYS = {"state", "ok", "code", "failed_step", "handle", "phase", "calls", "sid"}
ALL_RETURNS = []


def started(route, **kw):
    home, xdg = new_pc(f"t{len(ALL_RETURNS)}")
    ctx = FakeCtx(home, xdg, **kw)
    r0 = gmail.ADAPTER.test(ctx, route)
    ALL_RETURNS.append(r0)
    return ctx, r0


def poll(ctx, route, r0, events, state=""):
    ctx.snap = {"alive": True, "state": state, "events": events}
    out = gmail.ADAPTER.test(ctx, route, r0["handle"])
    ALL_RETURNS.append(out)
    return out


ctx, r0 = started("connector")
check("start: running, with a handle and the test tab's id, nothing asked of ctx.run",
      r0["state"] == "running" and r0["handle"] and r0["sid"] == "s1" and ctx.runs == [], r0)
check("start: one temporary Claude tab, started the way the core's tab kit says (an empty folder of ShellBot's own)",
      len(ctx.opened) == 1 and ctx.opened[0] == (ctx.tabs.command("claude"), "Gmail test") and "connect-test" in ctx.opened[0][0], ctx.opened)
check("the one question went in, once: names GMAIL-TEST, asks sender and subject only, no body, no sending",
      len(ctx.sent) == 1 and ctx.sent[0][0] == "s1" and "GMAIL-TEST: OK" in ctx.sent[0][1] and "do not open the body" in ctx.sent[0][1].lower()
      and "\n" not in ctx.sent[0][1], ctx.sent)
wctx = FakeCtx(*new_pc("tw"), win=True)
gmail.ADAPTER.test(wctx, "local")
check("Windows: the test tab runs what the kit says (a plain claude)", wctx.opened[0][0] == "claude", wctx.opened)

CASES = [
    # (name, route, events, tab state, want)
    ("connector: a Gmail tool was called and it said OK -> works", "connector", [user, tool(CONNECTOR_TOOL), mail, say(f"{SENDER}: {SUBJECT}\n**GMAIL-TEST: OK**"), end],
     "", {"state": "done", "ok": True, "calls": 1}),
    ("local: the same, through the local server's tool -> works", "local", [user, tool("ToolSearch"), tool(LOCAL_TOOL), tool(LOCAL_TOOL), mail, say("`GMAIL-TEST: OK`"), end],
     "", {"state": "done", "ok": True, "calls": 2}),
    ("connector: only the authenticate tool -> not authorized (go back to the connect step)", "connector", [user, tool(AUTH), say("GMAIL-TEST: FAIL needs auth"), end],
     "", {"state": "done", "ok": False, "code": "needs_auth", "failed_step": "a2", "calls": 0}),
    ("connector: no Gmail tool at all -> not yet (connect step)", "connector", [user, say("GMAIL-TEST: FAIL I have no Gmail tool"), end],
     "", {"state": "done", "ok": False, "code": "no_tools", "failed_step": "a2", "calls": 0}),
    ("local: no Gmail tool -> back to 'add the server'", "local", [user, say("GMAIL-TEST: FAIL none"), end],
     "", {"state": "done", "ok": False, "code": "no_tools", "failed_step": "b6", "calls": 0}),
    ("local: the other route's tool does not count", "local", [user, tool(CONNECTOR_TOOL), say("GMAIL-TEST: OK"), end],
     "", {"state": "done", "ok": False, "code": "said_ok", "failed_step": "", "calls": 0}),
    ("said OK without calling any Gmail tool -> does not count", "connector", [user, say("GMAIL-TEST: OK"), end],
     "", {"state": "done", "ok": False, "code": "said_ok", "failed_step": "", "calls": 0}),
    ("connector: a Gmail tool, then FAIL -> an error answer (no step to blame)", "connector", [user, tool(CONNECTOR_TOOL), say("GMAIL-TEST: FAIL 500"), end],
     "", {"state": "done", "ok": False, "code": "failed", "failed_step": "", "calls": 1}),
    ("local: a Gmail tool, then FAIL -> back to the sign-in step", "local", [user, tool(LOCAL_TOOL), say("GMAIL-TEST: FAIL invalid_grant"), end],
     "", {"state": "done", "ok": False, "code": "failed", "failed_step": "b7", "calls": 1}),
    ("a Gmail tool but no result line -> unclear", "connector", [user, tool(CONNECTOR_TOOL), say("Here you go."), end],
     "", {"state": "done", "ok": False, "code": "unclear", "failed_step": "", "calls": 1}),
    ("a mail that says GMAIL-TEST: OK cannot make a pass (tool results are not read)", "connector",
     [user, tool(CONNECTOR_TOOL), {"kind": "tool_result", "text": "GMAIL-TEST: OK"}, end],
     "", {"state": "done", "ok": False, "code": "unclear", "failed_step": "", "calls": 1}),
    ("still asking: the prompt is not in the transcript yet", "connector", [], "", {"state": "running", "phase": "asking", "calls": 0}),
    ("still asking: the prompt is in, no tool yet", "connector", [user], "", {"state": "running", "phase": "asking", "calls": 0}),
    ("reading: a tool was called, the turn is not over", "local", [user, tool(LOCAL_TOOL)], "", {"state": "running", "phase": "reading", "calls": 1}),
    ("waiting for the person's OK (the tab asks to allow a tool) -> needs the user", "connector", [user, tool("ToolSearch")], "decision",
     {"state": "needs_user", "code": "needs_allow", "calls": 0}),
]
for name, route, events, tab_state, want in CASES:
    ctx, r0 = started(route)
    got = poll(ctx, route, r0, events, tab_state)
    shape = {k: v for k, v in got.items() if k not in ("handle", "sid")}
    check(name, shape == want and got.get("sid") == "s1" and (want["state"] in ("done",) or got.get("handle") == r0["handle"]), got)

print("== time: 3 minutes, and the wait for your OK does not count ==")
ctx, r0 = started("connector")
poll(ctx, "connector", r0, [user])                       # the first poll that sees the question in: the clock starts
ctx.t += 181
check("no answer after 3 minutes -> timeout", poll(ctx, "connector", r0, [user])["code"] == "timeout")
ctx, r0 = started("connector")
poll(ctx, "connector", r0, [user])
ctx.t += 170
a = poll(ctx, "connector", r0, [user, tool("ToolSearch")], "decision")
ctx.t += 100
b = poll(ctx, "connector", r0, [user, tool("ToolSearch")], "")
ctx.t += 100
c = poll(ctx, "connector", r0, [user, tool("ToolSearch")], "")
check("170 s of waiting for the person is not counted: still running at 270 s", a["state"] == "needs_user" and b["state"] == "running", (a, b))
check("...and the clock runs again after: 370 s with 170 s paused is over 180 -> timeout", c.get("code") == "timeout", c)

print("== a test that did not work out ==")
ctx = FakeCtx(*new_pc("nr"))
ctx.ready = False
r0 = gmail.ADAPTER.test(ctx, "connector")
out = poll(ctx, "connector", r0, [])
check("the tab never got to its prompt -> not_ready (back to the sign-in check), nothing was asked",
      out == {"state": "done", "ok": False, "code": "not_ready", "failed_step": "a1", "calls": 0, "sid": "s1"} and ctx.sent == [], out)
ctx, r0 = started("local")
ctx.world.alive.clear()
out = gmail.ADAPTER.test(ctx, "local", r0["handle"])
check("the person closed the test tab -> closed", out["code"] == "closed" and out["ok"] is False, out)
ctx, r0 = started("connector")
ctx.cancel = True
out = gmail.ADAPTER.test(ctx, "connector", r0["handle"])
check("cancel closes the test tab and answers test_cancelled (the core puts the service back as it was)",
      ctx.closed == ["s1"] and out == {"state": "done", "ok": False, "code": "test_cancelled", "failed_step": "", "calls": 0, "sid": "s1"}, (ctx.closed, out))
check("an unknown handle (after a restart) -> closed, not an error", gmail.ADAPTER.test(ctx, "connector", "gone")["code"] == "closed")
check("an unknown route -> start_failed", gmail.ADAPTER.test(FakeCtx(*new_pc("ur")), "nope")["code"] == "start_failed")


nt_ctx = FakeCtx(*new_pc("nt"))
nt_ctx.world.fail_open = True
check("a tab that cannot be opened -> start_failed, not an exception", gmail.ADAPTER.test(nt_ctx, "connector")["code"] == "start_failed")
ctx, r0 = started("connector")
ctx2 = FakeCtx(*new_pc("again"), world=ctx.world)         # the same app: one world of tabs
gmail.ADAPTER.test(ctx2, "local")
check("a new test closes the test tab left from before", ctx2.closed == ["s1"], ctx2.closed)
gmail.ADAPTER.disconnect(ctx2, "local")
check("disconnect closes a test tab that is still open", ctx2.closed == ["s1", "s2"], ctx2.closed)
check("disconnect answers done and leaves Google's side alone (no program run)", gmail.ADAPTER.disconnect(ctx2, "local") == {"state": "done"} and ctx2.runs == [])

phase_ctx = FakeCtx(*new_pc("wr"))
phase_ctx.world.defer = True                              # the tab is open but not at its prompt: nothing asked yet
r0 = gmail.ADAPTER.test(phase_ctx, "connector")
out = poll(phase_ctx, "connector", r0, [])
check("the tab is not at its prompt yet -> running, waiting_ready", out["state"] == "running" and out["phase"] == "waiting_ready" and phase_ctx.sent == [], out)

print("== what comes back: codes and numbers, never the mail ==")
blob = json.dumps(ALL_RETURNS)
check("no sender, no subject, no reply text anywhere in a result", SENDER not in blob and SUBJECT not in blob and "Here you go" not in blob and "invalid_grant" not in blob)
check("every result is a plain dict with only the agreed fields", all(set(r) <= ALLOWED_KEYS for r in ALL_RETURNS) and all(
    isinstance(v, (str, int, float, bool)) for r in ALL_RETURNS for v in r.values()), [r for r in ALL_RETURNS if set(r) - ALLOWED_KEYS])
check("a verdict is always one the description has words for", DESC is not None and all(
    r["code"] in DESC["routes"][0]["test"]["verdicts"] or r["code"] in DESC["routes"][1]["test"]["verdicts"] for r in ALL_RETURNS if "code" in r))
check("the test never ran a program", all(not c.runs for c in (ctx, ctx2, phase_ctx)))
check("verdict_line: only a marker line in the last two lines counts", gmail.verdict_line("x\nGMAIL-TEST: OK") == "OK" and gmail.verdict_line("GMAIL-TEST: OK\na\nb") == ""
      and gmail.verdict_line("a\n> GMAIL-TEST: FAIL why") == "FAIL" and gmail.verdict_line("") == "" and gmail.verdict_line(None) == "")

# ── 4. through the real engine ───────────────────────────────────────────────
print("== the real engine: each route from the first step to connected, expired, reconnected, disconnected ==")
eng_mod._dlog = lambda cat, msg: None


class Rig(_WorldView):
    """The core's engine over a temp state file, the description and the adapter as shipped, stand-ins for every
    AI program (a recording runner), the key store and the tabs. The person's own entry in Claude's settings
    ("my-other-server") must survive everything."""

    def __init__(self, name):
        gmail._RUNS.clear()                                # one process, one table: a rig starts with no test tab of an earlier one
        self.home, self.xdg = new_pc(name)
        self.runner = fake.FakeRunner()
        self.runner.entries["claude"] = {"my-other-server"}
        self.clock = [2_000_000.0]
        self.world, self.paths = World(), []
        self.state_file = self.home.parent / "connectors.json"
        cl = clis_mod.Clis(runner=self.runner, finder=fake.finder())
        self.engine = eng_mod.Engine(registry=registry.Registry(dirs=[SERVICES_DIR]), path=str(self.state_file), clis=cl,
                                     backend=fake.backend(), ctx_factory=self.make_ctx, clock=lambda: self.clock[0])
        self.prof = Path(gmail.profile_dir({"XDG_CONFIG_HOME": str(self.xdg), "HOME": str(self.home)}))

    def make_ctx(self, service, route_id):
        c = Ctx(service=service, cfg=cfg_plan, env={"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.xdg), "PATH": "/nowhere"})
        c.find = lambda b: NODE if b == "node" else None
        c.run = lambda argv, timeout=40, env=None: Result(0, "v22.17.1") if list(argv) == [NODE, "--version"] else Result(1, "")
        c.open_path = self.paths.append
        c.open_tab = FakeTabKit(self.world).open
        c.tabs = FakeTabKit(self.world)
        return c

    def step(self, op, **args):
        return self.engine.step("gmail", op, args)

    def status(self):
        return self.engine.status("gmail")

    def test_until_done(self, events, tab_state=""):
        res = self.engine.test("gmail")
        h = res["result"].get("handle", "")
        self.snap = {"alive": True, "state": tab_state, "events": events}
        return res, self.engine.test("gmail", h)


def sid_of(res):
    return res["status"]["step"]["id"]


reg = registry.Registry(dirs=[SERVICES_DIR])
check("the engine's registry loads gmail with no error", "gmail" in reg.ids() and not reg.errors, reg.errors)

# -- local: Node, the Google Cloud steps, the file, the server, the sign-in, the test ---------------------------
r = Rig("e2e-local")
res = r.engine.start("gmail", route="local")
check("local: starts at step 1; Node.js passes on entering so it goes on by itself",
      res["success"] and res["status"]["route"] == "local" and sid_of(res) == "b1" and res["view"]["steps"][0]["state"] == "done", str(res["status"]))
for _ in range(4):
    res = r.step("done")                                  # b1..b4: the Google Cloud pages, nothing ShellBot can check
check("local: the four Google Cloud steps are the person's; the file step is next", sid_of(res) == "b5", str(res["status"]))
res = r.step("act", action="open_folder")
check("local: 'Open the folder' makes the profile folder, opens it, then checks: not there yet",
      r.prof.is_dir() and r.paths == [str(r.prof)] and res["view"]["step"]["result"]["code"] == "opened"
      and res["view"]["step"]["check"]["code"] == "missing" and sid_of(res) == "b5", str(res["view"]["step"])[:300])
res = r.step("done")
check("local: the file step is gated: 'I did it' without the file stays", sid_of(res) == "b5" and res["view"]["step"]["check"]["code"] == "missing")
(r.prof / "credentials.json").write_text(CLIENT_SECRET)
res = r.step("done")
check("local: with credentials.json in place it moves on to adding the server", sid_of(res) == "b6", str(res["status"]))
man = {m["cli"]: m for m in res["view"]["step"].get("manual", [])}
check("local: the AIs that have to be set up by hand are listed with what to enter (Gmail only, no secret)",
      {"opencode", "cursor"} <= set(man) and all(m["env"].get("GOOGLE_WORKSPACE_SERVICES") == "gmail" and "env_secret_names" in m for m in man.values()), (list(man), list(res["view"]["step"])))
res = r.step("act", action="cli_add", options={"readonly": True})
adds = [c for c in r.runner.calls if c[1:3] == ["mcp", "add"] and c[0].endswith("/claude")]
args_ = adds[0] if adds else []
env_pairs = [args_[i + 1] for i, a in enumerate(args_) if a == "-e"]
check("local: the core's cli_add runs `claude mcp add` once, user scope, the shellbot-gmail name, npx and the package",
      len(adds) == 1 and "shellbot-gmail" in args_ and args_[args_.index("--scope") + 1] == "user" and args_[-3:] == ["npx", "-y", gmail.PACKAGE], adds)
check("local B7: the server's settings carry services=gmail and READ_ONLY=true (the mailbox only), nothing else of Google's",
      sorted(env_pairs) == ["GOOGLE_WORKSPACE_MCP_PROFILE=shellbot", "GOOGLE_WORKSPACE_READ_ONLY=true", "GOOGLE_WORKSPACE_SERVICES=gmail"], env_pairs)
check("local: it was added to Claude only (others when the person picks them), and the person's own entry was left alone",
      res["status"]["clis"].get("claude") == "connected" and res["status"]["clis"].get("codex") != "connected"
      and "shellbot-gmail" in r.runner.entries["claude"] and "my-other-server" in r.runner.entries["claude"], res["status"]["clis"])
check("local: after the add the sign-in step is open", sid_of(res) == "b7", str(res["status"]))
res = r.step("act", action="auth_tab", options={"readonly": True})
check("local B7: the sign-in tab runs the Gmail-only, read-only command", r.opened[-1] == (gmail.auth_command(True, False), "Gmail sign-in") and res["view"]["step"]["result"]["code"] == "opened", r.opened)
res = r.step("done")
check("local: the sign-in step is gated until the permission file is there", sid_of(res) == "b7" and res["view"]["step"]["check"]["code"] == "missing")
(r.prof / "tokens.json").write_text(TOKEN)
res = r.step("done")
check("local: sign-in done, the test is next", sid_of(res) == "b8", str(res["status"]))
test_events = [user, tool("ToolSearch"), tool(LOCAL_TOOL), mail, say(f"{SENDER}: {SUBJECT}\nGMAIL-TEST: OK"), end]
res = r.engine.test("gmail")
h = res["result"]["handle"]
check("local: the test opens one temporary tab and asks once; the status says verifying",
      res["result"]["state"] == "running" and res["status"]["state"] == "verifying" and len(r.sent) == 1 and r.opened[-1][1] == "Gmail test", str(res)[:300])
r.snap = {"alive": True, "state": "decision", "events": [user, tool("ToolSearch")]}
res = r.engine.test("gmail", h)
check("local: the test tab waits for the person's OK -> needs_user, the service waits for you (not a failure)",
      core(res["result"]) == {"state": "needs_user", "handle": h, "code": "needs_allow"} and res["status"]["state"] == "waiting_user" and not res["status"]["attention"], str(res)[:300])
r.snap = {"alive": True, "state": "", "events": test_events}
res = r.engine.test("gmail", h)
check("local: the test passes -> connected, with the time and the least permission (read)",
      core(res["result"]) == {"state": "done", "ok": True} and res["status"]["state"] == "connected" and res["status"]["scopes"] == ["read"]
      and res["status"]["connected_at"] == 2_000_000, str(res)[:300])
check("local: what the person sees of the test is codes (no sender, no subject)", SENDER not in json.dumps(res) and SUBJECT not in json.dumps(res))
r.clock[0] += 400
check("health: the sign-in file is there -> still connected", r.engine.refresh("gmail")["status"]["state"] == "connected")
(r.prof / "tokens.json").unlink()
res = r.engine.refresh("gmail", force=True)
st = res["status"]
check("health: Google took the permission back (file gone) -> needs reconnect, with Gmail's own words and the way out",
      st["state"] == "needs_reconnect" and st["attention"] and st["problem"]["code"] == "revoked"
      and st["problem"]["text"]["en"].startswith("Google took the Gmail permission back") and "Sign in again" in st["problem"]["next"]["en"], str(st)[:400])
res = r.engine.start("gmail", reconnect=True)
steps = {s["id"]: s["state"] for s in res["view"]["steps"]}
check("reconnect: the sign-in (b7) and the test are redone; Node, the Google Cloud steps and the server stay done",
      steps["b7"] != "done" and steps["b8"] == "pending" and all(steps[i] == "done" for i in ("b0", "b1", "b2", "b3", "b4", "b6")), steps)
if steps["b5"] != "done":
    print("  [note] the core's reconnect also redid the file step: that step's action is the adapter's (builtin None) "
          "and reconnect compares it with an unset redo code (None)")
for _ in range(5):                                         # whatever is open: the steps the core redid, then the sign-in
    cur = sid_of(res)
    if cur == "b5":
        res = r.step("done")
    elif cur == "b6":
        res = r.step("act", action="cli_add", options={"readonly": True})
    elif cur == "b7":
        r.step("act", action="auth_tab", options={"readonly": True})
        (r.prof / "tokens.json").write_text(TOKEN)
        res = r.step("done")
closed_before = len(r.closed)
res, res2 = r.test_until_done(test_events)
check("reconnect: signed in again, tested again -> connected; the earlier test tab was closed first",
      res2["status"]["state"] == "connected" and not res2["status"]["attention"] and closed_before == 0 and len(r.closed) == 1, (res2["status"]["state"], r.closed))
res = r.engine.disconnect("gmail")
check("disconnect: ShellBot's own shellbot-gmail entries are gone, the person's own entry is not",
      all("shellbot-gmail" not in v for v in r.runner.entries.values()) and "my-other-server" in r.runner.entries["claude"], r.runner.entries)
check("disconnect: the open test tab is closed; the progress is forgotten; the person's files stay where they are",
      len(r.closed) == 2 and r.status()["state"] == "not_started" and (r.prof / "credentials.json").exists() and (r.prof / "tokens.json").exists(), (r.closed, r.status()["state"]))
everything = json.dumps([r.runner.calls, r.opened, r.sent, r.paths]) + (r.state_file.read_text(encoding="utf-8") if r.state_file.exists() else "")
check("no client secret, token, sender or subject in any command, tab, prompt, path or in connectors.json",
      not any(x in everything for x in (CLIENT_SECRET, TOKEN, SENDER, SUBJECT)))

# -- the connector: nothing to install --------------------------------------------------------------------------
r = Rig("e2e-conn")
res = r.engine.start("gmail")
check("connector: the recommended route is picked; the account check passes on entering (a claude.ai plan), so connect-on-claude.ai is open",
      res["status"]["route"] == "connector" and res["status"]["kind"] == "claude_connector" and sid_of(res) == "a2", str(res["status"]))
res = r.step("done")
check("connector: 'I connected it' -> the test", sid_of(res) == "a3")
res, res2 = r.test_until_done([user, tool(CONNECTOR_TOOL), mail, say("GMAIL-TEST: OK"), end])
check("connector: the test passes -> connected; only Claude is reached, the others say they cannot be",
      res2["status"]["state"] == "connected" and res2["status"]["clis"]["claude"] == "connected" and res2["status"]["clis"]["codex"] == "unsupported", str(res2["status"]["clis"]))
check("connector: no AI program was run and no file was looked at", r.runner.calls == [] and not r.prof.exists())

r = Rig("e2e-fail")
r.engine.start("gmail")
r.step("done")
res, res2 = r.test_until_done([user, say("GMAIL-TEST: FAIL I have no Gmail tool"), end])
st = res2["status"]
check("a test that finds no Gmail tool: failed, back at step a2 with Gmail's words for it",
      st["state"] == "failed" and st["attention"] and sid_of(res2) == "a2" and st["problem"]["code"] == "no_tools"
      and st["problem"]["text"]["en"].startswith("Not yet"), str(st)[:400])
check("...and a test before redoing that step is refused", r.engine.test("gmail")["code"] == "test_not_ready")
r.engine.start("gmail")
r.step("done")
res, res2 = r.test_until_done([user, tool(CONNECTOR_TOOL), say("GMAIL-TEST: OK"), end])
check("...redo the step, test again -> connected", res2["status"]["state"] == "connected")

r = Rig("e2e-cancel")
r.engine.start("gmail")
r.step("done")
res = r.engine.test("gmail")
h = res["result"]["handle"]
res = r.engine.test("gmail", h, cancel=True)
check("cancel: the test tab is closed and the service goes back to waiting (not failed)",
      res["result"]["code"] == "test_cancelled" and r.closed == ["s1"] and res["status"]["state"] == "waiting_user" and not res["status"]["attention"], str(res)[:300])

if __name__ == "__main__":
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
