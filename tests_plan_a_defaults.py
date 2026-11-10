#!/usr/bin/env python3
"""Plan A's defaults: a new install starts with no roles and with bots on; a computer that
already ran ShellBot keeps bots as they were (off when never set), and a role it deleted
stays deleted (it used to come back on every load); the defaults handed out are copies.

Run: .venv/bin/python tests_plan_a_defaults.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-plan-a-defaults-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import main  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


main.CONFIG_FILE = TMP / "config.json"

print("1. a new install")
cfg = main.load_config()
check("no roles: its roles are the bots it keeps", cfg.get("agent_roster") == {}, list(cfg.get("agent_roster") or {}))
check("bots on", cfg["settings"].get("experimental_bots") is True)
check("A2A off", cfg["settings"].get("experimental_a2a") is False)
cfg["settings"]["experimental_bots"] = False
cfg["agent_roster"]["x"] = {"label": "x"}
check("what it hands out is a copy: the defaults are untouched",
      main.DEFAULT_CONFIG["settings"]["experimental_bots"] is True and main.DEFAULT_CONFIG["agent_roster"] == {})

print("2. a computer that already ran ShellBot")
roster = {k: dict(v) for k, v in main.DEFAULT_AGENT_ROSTER.items()}
gone = next(iter(roster))
del roster[gone]
kept = next(iter(roster))
roster[kept].pop("handoff", None)
main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": "cli"}, "agent_roster": roster}, ensure_ascii=False),
                            encoding="utf-8")
cfg = main.load_config()
check("a role it deleted stays deleted", gone not in cfg["agent_roster"], list(cfg["agent_roster"]))
check("a role it has gets the fields roles gained since", cfg["agent_roster"][kept].get("handoff") is True,
      cfg["agent_roster"][kept])
check("bots never set there: off, and written so",
      cfg["settings"].get("experimental_bots") is False
      and json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))["settings"].get("experimental_bots") is False)
main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": "cli", "experimental_bots": True}, "agent_roster": {}}),
                            encoding="utf-8")
check("bots someone turned on stay on", main.load_config()["settings"]["experimental_bots"] is True)
main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": "cli", "experimental_bots": False}, "agent_roster": {}}),
                            encoding="utf-8")
check("…and off stays off", main.load_config()["settings"]["experimental_bots"] is False)

print("3. the settings page")
js = (HERE / "web" / "bots.js").read_text(encoding="utf-8")
check("an A2A switch beside the Bots one, saved as settings.experimental_a2a",
      "setting-experimental-a2a" in js and "cfg.settings.experimental_a2a = !a2a.classList.contains('on')" in js)
check("the Bots switch says it is on for a new install", "新安裝預設打開" in js and "預設關閉。')}</span>\n      </div>\n      <label" not in js)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
