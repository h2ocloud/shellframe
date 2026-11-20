#!/usr/bin/env python3
"""A Codex bot's model list comes from Codex itself (agent_model.codex_models).

Reported: the bot settings offered only gpt-5.5 for Codex, and the bot then failed on its first message
("The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account"). The list was ShellBot's
own, measured once on Codex 0.142.1. Codex keeps the menu it shows in `$CODEX_HOME/models_cache.json`
(fetched for the signed-in account), so:
  - the picker lists the models Codex's own menu lists (visibility "list"), in Codex's order, by their
    names; one it keeps off its menu (gpt-5.5 there) is not offered
  - without that file, or with one that cannot be read, the old table is listed as before
  - the file lists, it never refuses: a model it does not name (a launcher's newer one) is still taken
    by its shape, and one it hides is taken with `hidden`, which the editor turns into a warning
  - only slug, display_name, visibility and priority are read; nothing else from the file is kept
  - a changed file is read again; an unchanged one is not
Demo data only (made-up model names). Run: .venv/bin/python tests_codex_models.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
HOME = Path(tempfile.mkdtemp(prefix="sf-codex-models-"))
os.environ["HOME"] = str(HOME)
os.environ["CODEX_HOME"] = str(HOME)
os.environ["SHELLFRAME_CONFIG_DIR"] = str(HOME / "shellframe-config")
os.environ["PI_CODING_AGENT_DIR"] = tempfile.mkdtemp()

import agent_model  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


CMD = "sf-codex --search -m gpt-9-demo"
CACHE = HOME / "models_cache.json"


def write(models, bump=0):
    CACHE.write_text(json.dumps({"fetched_at": "2026-11-01T00:00:00Z", "etag": "W/\"demo\"", "client_version": "0.0.0",
                                 "models": models}), encoding="utf-8")
    t = time.time() + bump
    os.utime(CACHE, (t, t))


def keys(provider="codex"):
    return [c["key"] for c in agent_model.choices(provider)]


print("without Codex's file")
table = [k for k, _ in agent_model._TABLE["codex"]["models"]]
check("the old table is listed, between CLI default and Other", keys() == [""] + table + [agent_model.CUSTOM], keys())
check("…and a table model is listed by the check", agent_model.check(CMD, table[0])["kind"] == "listed")

print("with it")
write([
    {"slug": "demo-reserve", "display_name": "Demo Reserve", "visibility": "hide", "priority": 4, "secret_field": "x"},
    {"slug": "demo-6-luna", "display_name": "Demo-6-Luna", "visibility": "list", "priority": 7},
    {"slug": "demo-6-sol", "display_name": "Demo-6-Sol", "visibility": "list", "priority": 5, "base_instructions": "long text"},
    {"slug": "demo-6-terra", "display_name": "Demo-6-Terra", "visibility": "list", "priority": 6},
    {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "hide", "priority": 8},
    {"slug": "bad slug with spaces", "display_name": "x", "visibility": "list", "priority": 1},
    {"slug": "demo-6-sol", "display_name": "dup", "visibility": "list", "priority": 9},
    "not a dict",
    {"display_name": "no slug", "visibility": "list"},
])
cs = agent_model.choices("codex", zh=True)
check("Codex's own menu, in its order, by its names; hidden and malformed ones left out",
      [(c["key"], c["label"]) for c in cs[1:-1]] == [("demo-6-sol", "Demo-6-Sol"), ("demo-6-terra", "Demo-6-Terra"), ("demo-6-luna", "Demo-6-Luna")], cs)
check("…still CLI default first and Other last", cs[0]["key"] == "" and cs[-1]["key"] == agent_model.CUSTOM and cs[-1].get("custom"))
check("…the table's gpt-5.5 is no longer offered", "gpt-5.5" not in keys())
rows = agent_model.codex_models()
check("only key, label and listed are kept from the file", all(set(r) == {"key", "label", "listed"} for r in rows), rows)
check("a listed one is listed", agent_model.check(CMD, "demo-6-luna") == {"ok": True, "code": "", "kind": "listed", "provider": "codex", "suggestion": ""},
      agent_model.check(CMD, "demo-6-luna"))
r = agent_model.check(CMD, "gpt-5.5")
check("a hidden one is still taken, flagged hidden (the editor warns)", r["ok"] and r["kind"] == "known" and r.get("hidden") is True, r)
r = agent_model.check(CMD, "gpt-9-demo")
check("one the file does not name is taken by its shape (the file never refuses)", r["ok"] and r["kind"] == "known" and not r.get("hidden"), r)
check("…nonsense is still refused", not agent_model.check(CMD, "not-a-codex-model")["ok"])
r = agent_model.check(CMD, "demo-reserve")
check("a name Codex's file has, of any shape, is known (hidden here)", r["ok"] and r.get("hidden") is True, r)
check("a model's name comes from Codex's file", agent_model.label_of("codex", "demo-6-terra") == "Demo-6-Terra")
check("other CLIs are untouched", "demo-6-sol" not in keys("claude"))

print("when the file changes")
write([{"slug": "demo-7-nova", "display_name": "Demo-7-Nova", "visibility": "list", "priority": 1}], bump=5)
check("a changed file is read again", keys()[1:-1] == ["demo-7-nova"], keys())
seen = {"n": 0}
real_open = open


def counting_open(*a, **k):
    if str(a[0]).endswith("models_cache.json"):
        seen["n"] += 1
    return real_open(*a, **k)


agent_model.open = counting_open
agent_model.choices("codex"); agent_model.choices("codex"); agent_model.check(CMD, "demo-7-nova")
del agent_model.open
check("an unchanged file is not read again", seen["n"] == 0, seen)
CACHE.write_text("{not json", encoding="utf-8")
os.utime(CACHE, (time.time() + 10, time.time() + 10))
check("a file that cannot be read: the table again", keys()[1:-1] == table, keys())
write({"models": "wrong"} and [], bump=15)
check("an empty menu: the table", keys()[1:-1] == table, keys())

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
