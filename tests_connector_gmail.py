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
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
import types
from collections import namedtuple
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
_TMP = Path(tempfile.mkdtemp(prefix="sf-gmail-adapter-"))
os.environ.setdefault("SHELLFRAME_CONFIG_DIR", str(_TMP / "shellbot"))

# The core is written by another branch. Until it is merged, the adapter only needs its base class.
CORE = True
try:
    import connectors.adapter  # noqa: F401
except ModuleNotFoundError:
    CORE = False

    class _Adapter:
        id = ""
        checks = {}
        actions = {}

        def test(self, ctx, route, handle=None):
            raise NotImplementedError

        def health(self, ctx, route):
            return {"state": "unknown"}

        def disconnect(self, ctx, route):
            return {"state": "done"}

    _pkg = types.ModuleType("connectors")
    _pkg.__path__ = [str(HERE / "connectors")]
    _mod = types.ModuleType("connectors.adapter")
    _mod.Adapter = _Adapter
    sys.modules.update({"connectors": _pkg, "connectors.adapter": _mod})

SERVICES_DIR = HERE / "connectors" / "services"
_spec = importlib.util.spec_from_file_location("sf_test_gmail_adapter", SERVICES_DIR / "gmail.py")
gmail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gmail)
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
Result = namedtuple("Result", "rc out")
SENDER, SUBJECT = "demo-sender@example.invalid", "Demo subject line 0000"      # what a mail would say: must never come back
CLIENT_SECRET, TOKEN = "demo-client-secret-not-real", "demo-token-not-real"      # what the login files would hold: never opened


class FakeCtx:
    """What the core hands an adapter (SA9's Ctx) plus the tab calls the adapter asks of it: tab_ready, send,
    tab_events, close_tab. Every program the adapter asks for is answered from `answers`; none is run."""

    def __init__(self, home, xdg=None, bins=None, answers=None, cfg=None, win=False):
        self.service, self.route, self.lang, self.win, self.cancel = "gmail", "", "en", win, False
        self.cfg = cfg if cfg is not None else {}
        self.env = {"HOME": str(home), "PATH": "/nowhere"}
        if xdg:
            self.env["XDG_CONFIG_HOME"] = str(xdg)
        self.bins = dict(bins or {})
        self.answers = dict(answers or {})
        self.runs, self.tabs, self.paths, self.closed, self.sent = [], [], [], [], []
        self.t = 1000.0
        self.snap = {"alive": True, "state": "", "events": []}
        self.ready = True

    def run(self, argv, timeout, env=None):
        self.runs.append((list(argv), timeout))
        return self.answers.get(tuple(argv), Result(1, ""))

    def find(self, binary):
        return self.bins.get(binary)

    def open_tab(self, cmd, title):
        self.tabs.append((cmd, title))
        return f"s{len(self.tabs)}"

    def open_path(self, path):
        self.paths.append(path)

    def now(self):
        return self.t

    def tab_ready(self, sid, timeout):
        return self.ready

    def send(self, sid, text):
        self.sent.append((sid, text))
        return True

    def tab_events(self, sid):
        return dict(self.snap)

    def close_tab(self, sid):
        self.closed.append(sid)


class SyncThread:
    """threading.Thread that runs its target at start(), so a test is not about timing."""

    def __init__(self, target=None, args=(), **kw):
        self._t, self._a = target, args

    def start(self):
        self._t(*self._a)


gmail.threading.Thread = SyncThread

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
    cmd = c3.tabs[0][0]
    ok = (r3 == {"state": "opened", "sid": "s1"} and cmd == gmail.auth_command(want_ro, win_)
          and ("READ_ONLY" in cmd) == want_ro and "GOOGLE_WORKSPACE_SERVICES" in cmd)
    check(f"auth_tab ({label}): opens a tab with the {'read-only ' if want_ro else ''}sign-in", ok, (r3, cmd))
check("the sign-in tab is named for what it does", c3.tabs[0][1] == "Gmail sign-in", c3.tabs)

print("== the adapter is a plain module: no login file, no program of its own ==")
src = (SERVICES_DIR / "gmail.py").read_text(encoding="utf-8")
code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
check("it does not run programs itself (everything goes through ctx.run)", "subprocess" not in code and "os.system" not in code and "Popen" not in code)
check("it never opens or reads a file", not re.search(r"\bopen\(|read_text|read_bytes|json\.load", code))
check("it does not read the environment of the app (ctx.env only)", "os.environ" not in code and "getenv" not in code)
check("no key or token value is written in it", not re.search(r"sk-ant|Bearer ", code))

# ── 2. the description ────────────────────────────────────────────────────────
print("== the description (gmail.json) ==")
TEXT_KEYS = {"name", "label", "summary", "blurb", "title", "intro", "see", "note", "text", "hint", "q", "a", "next", "help"}
ROUTE_KINDS = {"claude_connector", "local_mcp", "api_key", "oauth"}
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
    check("the structure holds together (steps, checks, actions, scopes, links, verdicts)", not structure_problems(DESC, gmail.ADAPTER),
          "\n".join(structure_problems(DESC, gmail.ADAPTER)[:8]))
    if CORE:
        from connectors import registry
        check("registry.validate passes", registry.validate(DESC) == [], registry.validate(DESC))
    else:
        print("  [note] the core is not merged here: registry.validate not run (the structure check above stands in)")
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
          and routes["local"]["clis"]["pi"]["via"] == "none")
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
    check("nothing in it is a secret, a person, a company or an address of anyone's",
          not re.search(r"sk-ant|Bearer|@gmail\.com|@[a-z0-9-]+\.(com|tw)\b|Howard|neux", blob, re.I))

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
check("start: one temporary Claude tab in a folder of ShellBot's own", len(ctx.tabs) == 1 and ctx.tabs[0][0].startswith("cd ")
      and ctx.tabs[0][0].endswith(" && claude") and "connect-test-gmail" in ctx.tabs[0][0] and ctx.tabs[0][1] == "Gmail test", ctx.tabs)
check("the one question went in, once: names GMAIL-TEST, asks sender and subject only, no body, no sending",
      len(ctx.sent) == 1 and ctx.sent[0][0] == "s1" and "GMAIL-TEST: OK" in ctx.sent[0][1] and "do not open the body" in ctx.sent[0][1].lower()
      and "\n" not in ctx.sent[0][1], ctx.sent)
wctx = FakeCtx(*new_pc("tw"), win=True)
gmail.ADAPTER.test(wctx, "local")
check("Windows: the test tab runs a plain claude", wctx.tabs[0][0] == "claude", wctx.tabs)

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
ctx.t += 181
check("no answer after 3 minutes -> timeout", poll(ctx, "connector", r0, [user])["code"] == "timeout")
ctx, r0 = started("connector")
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
ctx.snap = {"alive": False, "state": "", "events": []}
out = gmail.ADAPTER.test(ctx, "local", r0["handle"])
check("the person closed the test tab -> closed", out["code"] == "closed" and out["ok"] is False, out)
ctx, r0 = started("connector")
ctx.cancel = True
out = gmail.ADAPTER.test(ctx, "connector", r0["handle"])
check("cancel closes the test tab and ends the test", ctx.closed == ["s1"] and out["code"] == "closed", (ctx.closed, out))
check("an unknown handle (after a restart) -> closed, not an error", gmail.ADAPTER.test(ctx, "connector", "gone")["code"] == "closed")
check("an unknown route -> start_failed", gmail.ADAPTER.test(FakeCtx(*new_pc("ur")), "nope")["code"] == "start_failed")


class _NoTab(FakeCtx):
    def open_tab(self, cmd, title):
        raise OSError("no pty")


check("a tab that cannot be opened -> start_failed, not an exception", gmail.ADAPTER.test(_NoTab(*new_pc("nt")), "connector")["code"] == "start_failed")
ctx, r0 = started("connector")
ctx2 = FakeCtx(*new_pc("again"))
gmail.ADAPTER.test(ctx2, "local")
check("a new test closes the test tab left from before", ctx2.closed == ["s1"], ctx2.closed)
gmail.ADAPTER.disconnect(ctx2, "local")
check("disconnect closes a test tab that is still open", ctx2.closed == ["s1", "s1"], ctx2.closed)
check("disconnect answers done and leaves Google's side alone (no program run)", gmail.ADAPTER.disconnect(ctx2, "local") == {"state": "done"} and ctx2.runs == [])

phase_ctx = FakeCtx(*new_pc("wr"))
_real_thread = gmail.threading.Thread
gmail.threading.Thread = type("NoRun", (), {"__init__": lambda self, **kw: None, "start": lambda self: None})
try:
    r0 = gmail.ADAPTER.test(phase_ctx, "connector")
finally:
    gmail.threading.Thread = _real_thread
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

# ── 4. each route, from its first step to connected ───────────────────────────
print("== every route, from the first step to connected ==")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def person_does(step_id, prof):
    """What the person does in a browser or a folder before pressing 'I did it' (a stand-in file each)."""
    if step_id == "b5":
        (prof / "credentials.json").write_text(CLIENT_SECRET)
    if step_id == "b7":
        (prof / "tokens.json").write_text(TOKEN)


def walk(route_id):
    route = next(r for r in DESC["routes"] if r["id"] == route_id)
    home, xdg = new_pc(f"walk-{route_id}")
    ctx = FakeCtx(home, xdg, bins={"node": NODE, "claude": "/fake/bin/claude"}, answers={(NODE, "--version"): Result(0, "v22.17.1")}, cfg=cfg_plan)
    prof = Path(gmail.profile_dir(ctx.env))
    problems, core_calls, seen = [], [], []

    def said(where, res, states):
        code = res.get("state")
        if code not in states:
            problems.append(f"{where}: state {code!r} has no words")
            return
        for lang in ("en", "zh"):
            for ph in PLACEHOLDER.findall(states[code]["text"][lang]):
                if ph not in res:
                    problems.append(f"{where}: {{{ph}}} is in the words for {code} but not in the result")

    for s in route["steps"]:
        if s.get("kind") == "test":
            break
        ck = s.get("check")
        if ck and ck["on"] == "enter":
            res = gmail.ADAPTER.checks[ck["id"]](ctx, {})
            said(f"{s['id']} check", res, ck["states"])
            seen.append((s["id"], "enter", res["state"]))
        for a in s.get("actions", []):
            if a["builtin"]:
                core_calls.append((s["id"], a["builtin"], route["mcp"]["server"], route["mcp"]["env"]))      # the core's own: stood in for
                continue
            res = gmail.ADAPTER.actions[a["id"]](ctx, {"options": {"readonly": True}})
            said(f"{s['id']}/{a['id']}", res, a["results"])
            seen.append((s["id"], a["id"], res["state"]))
            if a.get("after") == "check":
                res = gmail.ADAPTER.checks[ck["id"]](ctx, {})
                seen.append((s["id"], "after", res["state"]))
        if ck and ck["on"] == "done":
            before = gmail.ADAPTER.checks[ck["id"]](ctx, {})
            if ck.get("gate") and before["state"] in ck["pass"]:
                problems.append(f"{s['id']}: the gate is already open before the person did anything")
            person_does(s["id"], prof)
            res = gmail.ADAPTER.checks[ck["id"]](ctx, {})
            said(f"{s['id']} check", before, ck["states"])
            said(f"{s['id']} check", res, ck["states"])
            seen.append((s["id"], "done", before["state"] + ">" + res["state"]))
            if ck.get("gate") and res["state"] not in ck["pass"]:
                problems.append(f"{s['id']}: the gate stays shut after the person did it: {res}")
        elif ck and ck.get("gate") and gmail.ADAPTER.checks[ck["id"]](ctx, {})["state"] not in ck["pass"]:
            problems.append(f"{s['id']}: the gate stays shut: {ck['id']}")
    events = [user, tool(CONNECTOR_TOOL if route_id == "connector" else LOCAL_TOOL), mail, say("GMAIL-TEST: OK"), end]
    r0 = gmail.ADAPTER.test(ctx, route_id)
    ctx.snap = {"alive": True, "state": "", "events": events}
    fin = gmail.ADAPTER.test(ctx, route_id, r0["handle"])
    return ctx, problems, core_calls, seen, fin


if DESC is not None:
    ctx, problems, core_calls, seen, fin = walk("connector")
    check("connector: the account check, the connect step, the test -> connected", not problems and fin == {"state": "done", "ok": True, "calls": 1, "sid": "s1"}
          and seen == [("a1", "enter", "claude_ai")], (problems, fin, seen))
    check("connector: health has nothing local to look at -> unknown", gmail.ADAPTER.health(ctx, "connector") == {"state": "unknown"})
    ctx, problems, core_calls, seen, fin = walk("local")
    check("local: Node, folder, file, the server added, sign-in, the test -> connected", not problems and fin["ok"] is True, (problems, fin))
    check("local: what the person met on the way (Node ok; the file missing then in place; the sign-in unfinished then done)",
          seen == [("b0", "enter", "ok"), ("b5", "open_folder", "opened"), ("b5", "after", "missing"), ("b5", "done", "missing>ok"),
                   ("b7", "auth_tab", "opened"), ("b7", "done", "missing>ok")], seen)
    check("local: the server goes in through the core's cli_add, with the read-only Gmail-only settings",
          core_calls == [("b6", "cli_add", "shellbot-gmail", {"GOOGLE_WORKSPACE_MCP_PROFILE": "shellbot", "GOOGLE_WORKSPACE_SERVICES": "gmail",
                                                           "GOOGLE_WORKSPACE_READ_ONLY": "true"})], core_calls)
    check("local: the sign-in tab ran the Gmail-only, read-only command", ctx.tabs[0][0] == gmail.auth_command(True, False) and ctx.tabs[0][1] == "Gmail sign-in", ctx.tabs[:1])
    check("local: connected, health says ok", gmail.ADAPTER.health(ctx, "local") == {"state": "ok"})
    (Path(gmail.profile_dir(ctx.env)) / "tokens.json").unlink()
    check("local: Google takes the permission back -> health says revoked (the core asks to reconnect)", gmail.ADAPTER.health(ctx, "local") == {"state": "revoked"})
    all_cmds = json.dumps([ctx.tabs, ctx.runs, ctx.sent, ctx.paths])
    check("no client secret or token in any command, tab, prompt or path the walk produced", CLIENT_SECRET not in all_cmds and TOKEN not in all_cmds and "sk-" not in all_cmds)

if __name__ == "__main__":
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
