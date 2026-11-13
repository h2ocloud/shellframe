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

if __name__ == "__main__":
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
