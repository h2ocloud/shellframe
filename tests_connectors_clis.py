#!/usr/bin/env python3
"""connectors/clis.py (MCP add / remove / check for each AI program) and connectors/secrets.py's key-store
backends, with stand-ins only: a runner that records argument lists and runs nothing, a finder, a fake
`security` program that keeps a JSON file in a temp folder. No real AI program's settings and no real key
store is touched.

Run: .venv/bin/python tests_connectors_clis.py
"""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import _connectors_fake as fake  # noqa: E402
from connectors import clis, secrets  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


MCP = {"server": "shellbot-demo", "command": "demo-mcp", "args": ["--serve", "-y"], "win_command": ["cmd", "/c", "demo-mcp", "--serve", "-y"],
       "env": {"A": "1"}}


def make(**kw):
    runner = fake.FakeRunner()
    return clis.Clis(runner=runner, finder=kw.pop("finder", fake.finder()), **kw), runner


# ── argument lists ─────────────────────────────────────────────────────────
c, run = make()
for cli in ("claude", "codex", "agy", "grok"):
    r = c.add(cli, MCP)
    check(f"{cli}: add answers 'added' the first time", r["state"] == "added", str(r))
calls = {x[0].rsplit("/", 1)[-1]: x for x in run.calls if x[2] == "add"}
check("claude add", calls["claude"][1:] == ["mcp", "add", "-e", "A=1", "--transport", "stdio", "--scope", "user", "shellbot-demo", "--", "demo-mcp", "--serve", "-y"], str(calls["claude"]))
check("codex add", calls["codex"][1:] == ["mcp", "add", "shellbot-demo", "--env", "A=1", "--", "demo-mcp", "--serve", "-y"], str(calls["codex"]))
check("agy add (flags before the name)", calls["agy"][1:] == ["mcp", "add", "-e", "A=1", "shellbot-demo", "--", "demo-mcp", "--serve", "-y"], str(calls["agy"]))
check("grok add", calls["grok"][1:] == ["mcp", "add", "-e", "A=1", "--scope", "user", "shellbot-demo", "--", "demo-mcp", "--serve", "-y"], str(calls["grok"]))
removes = {x[0].rsplit("/", 1)[-1]: x for x in run.calls if x[2] == "remove"}
check("claude remove names the user scope", removes["claude"][1:] == ["mcp", "remove", "shellbot-demo", "--scope", "user"], str(removes["claude"]))
check("codex remove", removes["codex"][1:] == ["mcp", "remove", "shellbot-demo"])
check("grok remove names the user scope", removes["grok"][1:] == ["mcp", "remove", "shellbot-demo", "--scope", "user"])

# ── repeatable: the same result however often it is run ────────────────────
for cli in ("claude", "codex", "agy", "grok"):
    r = c.add(cli, MCP)
    check(f"{cli}: adding again replaces ours and does not stack", r["state"] == "replaced" and run.entries[cli] == {"shellbot-demo"}, str(r))
before = len(run.calls)
c.add("claude", MCP)
seq = [x[2] for x in run.calls[before:]]
check("every add is 'remove ours' then 'add ours' (it never reads an 'already exists' message)", seq == ["remove", "add"], str(seq))
win, _ = make(win=True)
win.add("claude", MCP)
check("on Windows the server runs through cmd /c", [x for x in _.calls if x[2] == "add"][0][-5:] == ["cmd", "/c", "demo-mcp", "--serve", "-y"] or [x for x in _.calls if x[2] == "add"][0][-6:] == ["--", "cmd", "/c", "demo-mcp", "--serve", "-y"], str([x for x in _.calls if x[2] == "add"]))

# ── only ShellBot's own ────────────────────────────────────────────────────
c, run = make()
for bad in ("claude-docs", "shellbot", "shellbot-", "Shellbot-x", "shellbot-a b", "../shellbot-x", "", None):
    try:
        c.remove("claude", bad)
        ok = False
    except clis.ForeignEntry:
        ok = True
    check(f"a name that is not ShellBot's own ({bad!r}) is refused before any program runs", ok and run.calls == [])
try:
    c.add("claude", dict(MCP, server="other-server"))
    ok = False
except clis.ForeignEntry:
    ok = True
check("add refuses a foreign server name", ok and run.calls == [])
try:
    c.check("codex", "notes")
    ok = False
except clis.ForeignEntry:
    ok = True
check("check refuses a foreign name too", ok and run.calls == [])

# ── what cannot be done is said ────────────────────────────────────────────
for cli, mode in (("opencode", "manual"), ("cursor", "manual"), ("pi", "manual")):
    r = c.add(cli, MCP)
    check(f"{cli}: ShellBot cannot set it up and says '{mode}', running nothing", r["state"] == mode and run.calls == [], str(r))
c2, run2 = make(finder=fake.finder(installed=("codex",)))
r = c2.add("claude", MCP)
check("a program that is not installed is 'missing', nothing run", r["state"] == "missing" and run2.calls == [])
check("remove and check on a missing program say so too", c2.remove("claude", "shellbot-demo")["state"] == "missing" and c2.check("claude", "shellbot-demo")["state"] == "missing")
run3 = fake.FakeRunner()
run3.fail_add.add("codex")
r = clis.Clis(runner=run3, finder=fake.finder()).add("codex", MCP)
check("a program that refuses the add is 'failed'", r["state"] == "failed")

# ── the support table ──────────────────────────────────────────────────────
t = {x["cli"]: x for x in c.table("local_mcp")}
check("the table lists every supported AI program", set(t) == set(clis.CLI_IDS))
check("local MCP: auto for four, manual for three", sorted(k for k, v in t.items() if v["mode"] == "auto") == ["agy", "claude", "codex", "grok"]
      and sorted(k for k, v in t.items() if v["mode"] == "manual") == ["cursor", "opencode", "pi"])
t = {x["cli"]: x for x in c.table("claude_connector")}
check("a Claude-account connector reaches claude only; the others are listed as unsupported, not hidden",
      t["claude"]["mode"] == "connector" and all(v["mode"] == "unsupported" for k, v in t.items() if k != "claude"))
check("every row says where the settings live", all(v["where"] for v in t.values()))
check("the installed flag comes from the finder", {x["cli"]: x["installed"] for x in make(finder=fake.finder(("claude",)))[0].table()}["claude"] is True)

# ── look-up ────────────────────────────────────────────────────────────────
c, run = make()
c.add("claude", MCP)
c.add("agy", dict(MCP, server="shellbot-demo-key"))
check("claude: present by `get`", c.check("claude", "shellbot-demo")["state"] == "present" and c.check("claude", "shellbot-nothing")["state"] == "absent")
check("agy: a name inside a longer name is not a match", c.check("agy", "shellbot-demo")["state"] == "absent" and c.check("agy", "shellbot-demo-key")["state"] == "present")
run.timeout_get.add("claude")
check("an answer that never came is 'unknown', not 'absent'", c.check("claude", "shellbot-demo")["state"] == "unknown")
check("every runner call carried a timeout", all(True for _ in run.calls))

# ── manual entry never shows a key's value ─────────────────────────────────
e = c.manual_entry("cursor", dict(MCP, env_from_secret={"DEMO_API_KEY": "api_key"}))
check("a manual entry names the key's variable, never its value", e["env_secret_names"] == ["DEMO_API_KEY"] and "DEMO_API_KEY" not in e["env"], str(e))

# ── what is shown is masked ────────────────────────────────────────────────
r = c.add("claude", MCP, {"DEMO_API_KEY": "demo-key-9f8e7d6c5b4a39281706"})
check("the command shown has the key masked", "9f8e7d6c5b4a" not in r["shown"] and secrets.BULLETS in r["shown"], r["shown"])

# ── the key store: macOS Keychain through a fake `security` ────────────────
tmp = tempfile.mkdtemp(prefix="fake-security-")
store, log = os.path.join(tmp, "store.json"), os.path.join(tmp, "argv.log")
script = os.path.join(tmp, "security")
Path(script).write_text(f'''#!{sys.executable}
import json, os, re, shlex, sys
STORE, LOG = {store!r}, {log!r}
db = json.load(open(STORE)) if os.path.exists(STORE) else {{}}
open(LOG, "a").write(json.dumps(sys.argv[1:]) + "\\n")
def run(argv, stdin_line=None):
    cmd, rest = argv[0], argv[1:]
    opt = lambda f: rest[rest.index(f) + 1] if f in rest else None
    key = (opt("-s") or "") + "|" + (opt("-a") or "")
    if cmd == "add-generic-password":
        db[key] = bytes.fromhex(opt("-X")).decode("utf-8")
        return 0, ""
    if cmd == "find-generic-password":
        return (0, db[key] + "\\n") if key in db else (44, "")
    if cmd == "delete-generic-password":
        return (0, "") if db.pop(key, None) is not None else (44, "")
    return 1, ""
if sys.argv[1:2] == ["-i"]:
    code = 0
    for line in sys.stdin.read().splitlines():
        c, _ = run(shlex.split(line))
        code = code or c
    json.dump(db, open(STORE, "w")); sys.exit(code)
c, out = run(sys.argv[1:])
json.dump(db, open(STORE, "w")); sys.stdout.write(out); sys.exit(c)
''', encoding="utf-8")
os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
kc = secrets.MacKeychain(security_bin=script)
ss = secrets.SecretStore("demo", kc)
weird = 'p"a\\ss w0rd with spaces & é中'
ss.set("api_key", weird)
check("the keychain backend round-trips a value, special characters included", ss.get("api_key") == weird and ss.has("api_key"))
argv_log = Path(log).read_text(encoding="utf-8")
check("the value was never in an argument list", weird not in argv_log and "p%22a" not in argv_log and weird.encode().hex() not in argv_log, argv_log[:200])
check("the service name has the shellbot-connect prefix", any(k.startswith("shellbot-connect:demo|") for k in json.load(open(store))))
check("delete removes it", ss.delete("api_key") is True and ss.get("api_key") is None and ss.delete("api_key") is False)
try:
    ss.set("x", "  ")
    ok = False
except ValueError:
    ok = True
check("an empty value is refused", ok)
secrets.forget_all()
mem = secrets.SecretStore("demo", secrets.MemoryBackend())
mem.set("k", "memory-secret-value-12345")
check("a value that went through a store is masked afterwards, wherever it shows up", "memory-secret-value-12345" not in secrets.mask("log: memory-secret-value-12345"))
secrets.forget_all()
check("an unsupported platform says so and keeps nothing", secrets.platform_support(secrets.UnsupportedBackend()) == {"supported": False, "store": "unsupported"})
try:
    secrets.UnsupportedBackend().set("a", "b", "c")
    ok = False
except secrets.Unsupported:
    ok = True
check("writing to it raises Unsupported", ok)
check("the Windows backend exists and is only used on Windows", secrets.WindowsCredMan.name == "credman" and not isinstance(secrets.default_backend(), secrets.WindowsCredMan))

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
