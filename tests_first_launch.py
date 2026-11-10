#!/usr/bin/env python3
"""What a brand-new user's first launch offers.

Measured before this test: with no config file yet, load_config() returned
DEFAULT_CONFIG, whose "+" menu has the shell and three AI CLIs. Pi, OpenCode, Grok
Build and Cursor Agent appeared only after something had saved the config once.

Runs main.load_config() under a temporary HOME, in a child process: CONFIG_FILE is
fixed when main is imported.

Run: .venv/bin/python tests_first_launch.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


CHILD = r"""
import json, main
before = [p["name"] for p in main.DEFAULT_CONFIG["presets"]]
first = main.load_config()
main.save_config(first)
again = main.load_config()
print(json.dumps({
    "exists_before": False,
    "first": [p["name"] for p in first["presets"]],
    "offered": first.get("_default_ai_presets_offered"),
    "again": [p["name"] for p in again["presets"]],
    "default_untouched": [p["name"] for p in main.DEFAULT_CONFIG["presets"]] == before,
    "ai": [p["name"] for p in main._DEFAULT_AI_PRESETS],
}))
"""

with tempfile.TemporaryDirectory(prefix="sf-first-launch-") as home:
    env = dict(os.environ, HOME=home, USERPROFILE=home)
    out = subprocess.run([sys.executable, "-c", CHILD], cwd=str(HERE), env=env,
                         capture_output=True, text=True, timeout=120)
    try:
        got = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        got = {}
        print(out.stdout[-800:], out.stderr[-800:])

ai = got.get("ai") or []
first = got.get("first") or []
check("the first launch's \"+\" menu offers every supported AI CLI",
      bool(ai) and all(n in first for n in ai), str(first))
check("…and a shell first", first[:1] in (["Bash"], ["PowerShell"]), str(first[:1]))
check("no preset twice", len(first) == len(set(first)), str(first))
check("the offer is recorded, so it is not made again", sorted(got.get("offered") or []) == sorted(ai))
check("after the first save the menu is the same", got.get("again") == first, str(got.get("again")))
check("DEFAULT_CONFIG's own list is not changed (copy() is shallow)", got.get("default_untouched") is True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
