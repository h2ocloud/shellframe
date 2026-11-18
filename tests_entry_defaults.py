#!/usr/bin/env python3
"""The office's default (the entry row's 辦公室 button needs something to open): a new install starts with it on, as with
bots; a computer that already ran ShellBot keeps it as it was (off when never set, written so), and what someone
turned on or off stays.

Run: .venv/bin/python tests_entry_defaults.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-entry-defaults-"))
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
check("the office is on", cfg["settings"].get("experimental_office") is True)
check("bots are on, as before", cfg["settings"].get("experimental_bots") is True)
check("the defaults handed out are copies", (cfg["settings"].__setitem__("experimental_office", False) or True)
      and main.DEFAULT_CONFIG["settings"]["experimental_office"] is True)

print("2. a computer that already ran ShellBot")
main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": "cli"}, "agent_roster": {}}), encoding="utf-8")
cfg = main.load_config()
check("an office never set there: off, and written so", cfg["settings"].get("experimental_office") is False
      and json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))["settings"].get("experimental_office") is False)
main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": "cli", "experimental_office": True}, "agent_roster": {}}), encoding="utf-8")
check("an office someone turned on stays on", main.load_config()["settings"]["experimental_office"] is True)
main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": "cli", "experimental_office": False}, "agent_roster": {}}), encoding="utf-8")
check("…and off stays off", main.load_config()["settings"]["experimental_office"] is False)
once = main.bots.keep_old_install_office_setting({"settings": {}})
check("the rule writes once (a second pass changes nothing)", once is True and main.bots.keep_old_install_office_setting({"settings": {"experimental_office": False}}) is False)

print("3. the page")
js = (HERE / "web" / "office.js").read_text(encoding="utf-8")
check("the Settings switch says it is on for a new install", "新安裝預設打開" in js and "On for a new install" in js)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
