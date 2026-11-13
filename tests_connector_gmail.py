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

if __name__ == "__main__":
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
