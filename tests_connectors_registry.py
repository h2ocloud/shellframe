#!/usr/bin/env python3
"""connectors/registry.py, problems.py, secrets.py (mask) and the CLI table, with no program run.

Run: .venv/bin/python tests_connectors_registry.py
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import _connectors_fake as fake  # noqa: E402
import usage_probe  # noqa: E402
from connectors import clis, problems, registry, secrets  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def bad(mutate, needle):
    d = fake.demo()
    mutate(d)
    errs = registry.validate(d)
    return any(needle in e for e in errs), errs


# ── a good description ─────────────────────────────────────────────────────
check("the made-up service is valid", registry.validate(fake.demo()) == [], str(registry.validate(fake.demo())))
check("the CLI ids are the repo's providers", set(clis.CLI_IDS) == set(usage_probe.PROVIDERS), str(set(usage_probe.PROVIDERS) ^ set(clis.CLI_IDS)))

# ── what a description may not say ─────────────────────────────────────────
ok, e = bad(lambda d: d["name"].pop("zh"), "name: needs both en and zh")
check("a text needs both languages", ok, e)
ok, e = bad(lambda d: d["routes"][1]["mcp"].update(server="claude-demo"), "must be shellbot-")
check("an MCP server name must be ShellBot's own (shellbot-…)", ok, e)
ok, e = bad(lambda d: d["routes"][1]["mcp"]["env"].update(DEMO_KEY="sk-abcdefghijklmnopqrstuv"), "looks like a secret")
check("a secret cannot sit in the description's env", ok, e)
ok, e = bad(lambda d: d["routes"][0].update(scopes=[]), "must include the required")
check("a route must ask for the required permissions", ok, e)
ok, e = bad(lambda d: d["routes"][0].update(scopes=["read", "admin"]), "not in the service's scopes")
check("a route cannot ask for a permission the service does not list", ok, e)
ok, e = bad(lambda d: d["routes"][0]["steps"].pop(), "ends with a step of kind 'test'")
check("a route must end with a test step", ok, e)
ok, e = bad(lambda d: d["routes"][0]["steps"][0].update(url="http://example.com"), "https link")
check("a link must be https", ok, e)
ok, e = bad(lambda d: d["routes"][0]["clis"].update(codex={"via": "connector"}), "claude only")
check("'connector' is for claude only", ok, e)
ok, e = bad(lambda d: d["routes"][0]["clis"].update(vim={"via": "none"}), "not an AI program ShellBot supports")
check("an unknown AI program is refused", ok, e)
ok, e = bad(lambda d: d["routes"][1]["steps"][1]["actions"][0].update(builtin="rm_rf"), "builtin")
check("a built-in action must be one the core has", ok, e)
ok, e = bad(lambda d: d["routes"][0].pop("mcp", None) or d["routes"][0]["clis"].update(claude={"via": "mcp"}), "needs the route's mcp")
check("via mcp needs the route's mcp", ok, e)
ok, e = bad(lambda d: d["routes"][1]["steps"][0]["check"].update(on="sometimes"), "check.on")
check("a check runs on enter or done", ok, e)
ok, e = bad(lambda d: d["routes"][0]["steps"][1]["stuck"][0]["links"][0].update(step="nowhere"), "a url, or a step")
check("a stuck link must point at a step of the route", ok, e)
ok, e = bad(lambda d: d["routes"][2]["mcp"]["env_from_secret"].update(X="nope"), "no secret 'nope'")
check("env_from_secret must name a secret of the route", ok, e)
ok, e = bad(lambda d: d["name"].update(en="Hello [[SF:RED]]"), "no [[ ]]")
check("no ShellBot marker inside a text", ok, e)
check("a description with no id is refused", registry.validate({"schema": 1}) != [])

# ── the registry ───────────────────────────────────────────────────────────
reg = registry.Registry(dirs=[])
check("an empty registry has no service", reg.ids() == [])
check("a valid description registers", reg.register(fake.demo(), adapter=fake.FakeAdapter()))
broken = fake.demo()
broken["id"] = "broken"
broken["routes"] = []
check("a broken one is skipped and the reason kept", not reg.register(broken) and "broken" in reg.errors)
check("the first route is the recommended one", reg.route("demo")["id"] == "connector")
check("a route by id", reg.route("demo", "local")["kind"] == "local_mcp")
check("an unknown route is None", reg.route("demo", "nope") is None and reg.route("nope") is None)
check("the injected adapter is used", reg.adapter("demo").id == "demo")
with tempfile.TemporaryDirectory() as d:
    (Path(d) / "demo.json").write_text(json.dumps(fake.demo()), encoding="utf-8")
    (Path(d) / "junk.json").write_text("{not json", encoding="utf-8")
    r2 = registry.Registry(dirs=[d])
    check("a folder of descriptions loads; junk is skipped", r2.ids() == ["demo"] and "junk.json" in r2.errors)
    check("a description with no adapter module has no adapter", r2.adapter("demo") is None)

# ── the catalog's fields (all optional: an older description still reads) ──
def soon():
    """A made-up service listed before it can be connected: no routes, and why."""
    d = fake.demo()
    d.update(id="later", routes=[], platforms=["mac"], local_app=True,
             unavailable={"text": fake.T("Not yet: no server to recommend"), "next": fake.T("It comes later")})
    return d


old = fake.demo()
check("a description without the catalog's fields is valid, and its entry says so plainly",
      registry.validate(old) == [] and registry.catalog_entry(old, "mac") == {
          "id": "demo", "summary": old["summary"], "category": None, "recommended": False, "local_app": False,
          "platforms": list(registry.PLATFORMS), "available": True, "why": None, "beta": False})
full = fake.demo()
full.update(category={"id": "productivity", "name": fake.T("Productivity")}, recommended=True, platforms=["mac", "windows"])
e = registry.catalog_entry(full, "mac")
check("category, recommended and platforms are read", registry.validate(full) == [] and e["category"]["id"] == "productivity"
      and e["recommended"] is True and e["platforms"] == ["mac", "windows"] and e["available"] is True, (registry.validate(full), e))
check("…on a computer its platforms leave out: not available, and why", registry.catalog_entry(full, "linux")["available"] is False
      and registry.catalog_entry(full, "linux")["why"] == "not_on_platform" and registry.available(full, "linux") == "not_on_platform")
check("a service listed before it can be connected is valid with no routes", registry.validate(soon()) == [], registry.validate(soon()))
check("…and not available on the computer it is for", registry.available(soon(), "mac") == "not_available"
      and registry.catalog_entry(soon(), "mac")["why"] == "not_available" and registry.catalog_entry(soon(), "mac")["local_app"] is True)
check("…and on another kind of computer it is that computer's reason first (a Mac app is never listed as this PC's)",
      registry.available(soon(), "windows") == "not_on_platform" and registry.catalog_entry(soon(), "linux")["why"] == "not_on_platform")
ok, e = bad(lambda d: d.update(routes=[]), "routes: at least one")
check("no routes without `unavailable` saying why is refused", ok, e)
ok, e = bad(lambda d: d.update(unavailable={"text": fake.T("x"), "next": fake.T("y")}), "has \"routes\": []")
check("`unavailable` with routes is refused (nothing to walk, or nothing unavailable)", ok, e)
ok, e = bad(lambda d: d.update(unavailable={"next": fake.T("y")}, routes=[]), "unavailable.text")
check("`unavailable` needs its words", ok, e)
ok, e = bad(lambda d: d.update(category={"id": "Bad Id", "name": fake.T("x")}), "category")
check("a category id is kebab-case", ok, e)
ok, e = bad(lambda d: d.update(category={"id": "files"}), "category.name")
check("a category has a name in both languages", ok, e)
ok, e = bad(lambda d: d.update(recommended="yes"), "recommended: true or false")
check("recommended is true or false", ok, e)
ok, e = bad(lambda d: d.update(local_app=1), "local_app: true or false")
check("local_app is true or false", ok, e)
ok, e = bad(lambda d: d.update(platforms=["mac", "amiga"]), "platforms")
check("platforms are mac, windows, linux", ok, e)
ok, e = bad(lambda d: d.update(platforms=[]), "platforms")
check("…and at least one (leave it out for everywhere)", ok, e)
a, b = fake.demo(), fake.demo()
a["category"] = {"id": "files", "name": fake.T("Files")}
b.update(id="other", category={"id": "files", "name": fake.T("Documents")})
check("one category id named two ways is found", registry.category_conflicts([a, b]) != [] and registry.category_conflicts([a, a]) == [])
listing = "https://example.com/directory/0f1e2d3c-4b5a-4968-8778-695a4b3c2d1e"
ok = fake.demo()
ok["routes"][0]["steps"][0].update(url=listing, in_browser=True, done_label=fake.T("Yes"))
ok["routes"][0]["beta"] = True
check("a step done in the browser on a listing's own page, its button's words, a beta way: all valid",
      registry.validate(ok) == [] and registry.catalog_entry(ok, "mac")["beta"] is True, registry.validate(ok))
ok, e = bad(lambda d: d["routes"][0]["steps"][0].update(url=listing + "?k=0f1e2d3c-4b5a-4968-8778-695a4b3c2d1e"), "https link")
check("…but an id in a query string is still refused (it may be a key)", ok, e)
ok, e = bad(lambda d: d["routes"][0]["steps"][0].update(url="https://example.com/" + "a1b2c3d4" * 6), "https link")
check("…and so is a long token-like run in a path", ok, e)
ok, e = bad(lambda d: d["routes"][0]["steps"][1].update(in_browser=True), "needs its url")
check("in_browser needs the step's url", ok, e)
ok, e = bad(lambda d: d["routes"][0]["steps"][0].update(in_browser="yes"), "in_browser: true or false")
check("in_browser is true or false", ok, e)
ok, e = bad(lambda d: d["routes"][0]["steps"][0].update(done_label={"en": "Yes"}), "done_label")
check("done_label is a text in both languages", ok, e)
ok, e = bad(lambda d: d["routes"][0].update(beta=1), "beta: true or false")
check("beta is true or false", ok, e)
reg3 = registry.Registry(dirs=[])
reg3.register(soon())
reg3.register(full)
check("the registry lists a catalog entry per service, in id order", [x["id"] for x in reg3.catalog("mac")] == ["demo", "later"])
check("a service with no routes has no route (and nothing raises)", reg3.route("later") is None and reg3.route("later", "x") is None)
check("this computer is one of the platforms", registry.this_platform() in registry.PLATFORMS)
d = soon()
check("its `unavailable` words are what not_available says", problems.explain("not_available", d)["text"] == d["unavailable"]["text"]
      and problems.explain("not_available")["text"] == problems.CORE["not_available"]["text"])

# ── the words ──────────────────────────────────────────────────────────────
t = {"en": "Hello", "zh": "你好"}
check("pick follows the language", registry.pick(t, "zh") == "你好" and registry.pick(t, "en") == "Hello")
check("pick falls back to the other language", registry.pick({"en": "Only"}, "zh") == "Only")
check("clean_line drops markers and control characters", registry.clean_line("a [[SF:RED]]\nb\x07  c") == "a SF:RED b c")
check("a connect tag gives the service", registry.parse_connect_tag("connect:demo") == "demo" and registry.parse_connect_tag(" Connect : Demo ") == "demo")
check("a tag for an unknown service is None when the registry is given", registry.parse_connect_tag("connect:nope", known=["demo"]) is None)
check("an ordinary reason is not a tag", registry.parse_connect_tag("Which one?") is None and registry.parse_connect_tag("connect:../x") is None and registry.parse_connect_tag("") is None)

# ── problems ───────────────────────────────────────────────────────────────
e = problems.explain("cli_missing")
check("a core problem has words and a next step in both languages", registry.is_text(e["text"]) and registry.is_text(e["next"]))
check("every core problem is complete", all(registry.is_text(v["text"]) and registry.is_text(v["next"]) for v in problems.CORE.values()))
d = fake.demo()
check("a description's problem is used", problems.explain("demo_down", d)["text"]["en"] == "The demo service is down")
check("a route's test verdict wins", problems.explain("nothing_back", d, d["routes"][1])["next"]["en"] == "Sign in again")
check("an unknown code gets the generic sentence", problems.explain("zzz")["text"] == problems.GENERIC["text"])

# ── masks ──────────────────────────────────────────────────────────────────
secrets.forget_all()
check("a token shape is masked", "sk-abcdefghijklmnop12" not in secrets.mask("x sk-abcdefghijklmnop12 y") and secrets.BULLETS in secrets.mask("x sk-abcdefghijklmnop12"))
check("KEY=VALUE is masked", secrets.mask("DEMO_API_KEY=hunter22") == f"DEMO_API_KEY={secrets.BULLETS}")
check("a bearer header is masked", "abcdefghijklmnopq" not in secrets.mask("Authorization: Bearer abcdefghijklmnopq"))
check("a long unbroken run is masked", "a" * 40 not in secrets.mask("id " + "a" * 40))
check("a plain sentence is left alone", secrets.mask("Press the Connect button, then wait.") == "Press the Connect button, then wait.")
check("a known value is masked wherever it is", secrets.mask("pw is hunter2xyz!", known=["hunter2xyz"]) == f"pw is {secrets.BULLETS}!")
secrets.remember("remembered-value")
check("a remembered value is masked", "remembered-value" not in secrets.mask("see remembered-value now"))
secrets.forget_all()
try:
    secrets.assert_clean({"a": {"api_token": "x"}})
    leak = False
except secrets.SecretLeak:
    leak = True
check("a key named like a secret may not be saved", leak)
try:
    secrets.assert_clean({"note": "sk-abcdefghijklmnopqrstuv"})
    leak = False
except secrets.SecretLeak:
    leak = True
check("a value shaped like a token may not be saved", leak)
secrets.assert_clean({"state": "connected", "has_secret": False, "steps": [{"id": "a", "state": "done"}]})
check("an ordinary state is clean", True)
check("under test the default key store refuses", isinstance(secrets.default_backend(), secrets.RefusingBackend))
try:
    secrets.default_backend().get("a", "b")
    refused = False
except secrets.Unsupported:
    refused = True
check("the refusing backend raises", refused)

try:
    clis.default_runner(["true"])
    refused = False
except RuntimeError:
    refused = True
check("under test the default runner refuses", refused)

# a result may say "ask again with this action" (`poll`): it has to name an action of the same step
good = fake.demo()
sig = [st for st in good["routes"][1]["steps"] if st["id"] == "signin"][0]
sig["actions"][0]["results"]["opened"]["poll"] = "signin"
check("a result's poll naming an action of its step is valid", registry.validate(good) == [], registry.validate(good))
sig["actions"][0]["results"]["opened"]["poll"] = "nope"
check("…and one naming no action of the step is refused", any("poll" in e for e in registry.validate(good)), registry.validate(good))

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
