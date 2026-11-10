#!/usr/bin/env python3
"""Tests never read or write the running app's ~/.config/shellframe/config.json.

Measured before: about 30 test files load main without moving CONFIG_FILE, and once
load_config gained a migration, the first of them wrote into the config of the ShellBot
running on that computer. Now sf_config.config_dir() decides the folder for everyone:
SHELLFRAME_CONFIG_DIR when set (run_tests.sh sets one per run), and for a test without it
(a tests_*.py started by hand) a folder of its own, which its subprocesses inherit.

Run: .venv/bin/python tests_config_isolation.py
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
REAL = Path.home() / ".config" / "shellframe"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


import sf_config  # noqa: E402
here = sf_config.config_dir()
check("a test does not get the real folder", here.resolve() != REAL.resolve(), here)
check("…its folder is a temporary one", str(here.resolve()).startswith(str(Path(tempfile.gettempdir()).resolve()))
      or "shellframe-test-config" in str(here) or os.environ.get("SHELLFRAME_CONFIG_DIR"), here)
check("…and stays the same for the whole run", sf_config.config_dir() == here and sf_config.config_file() == here / "config.json")
child = subprocess.run([sys.executable, "-c", "import sf_config; print(sf_config.config_dir())"],
                       cwd=str(HERE), capture_output=True, text=True, env=os.environ.copy())
check("a process the test starts gets the same folder", Path(child.stdout.strip()) == here, child.stdout + child.stderr)
lone = subprocess.run([sys.executable, "-c", "import sf_config; print(sf_config.config_dir())"], cwd=str(HERE),
                      capture_output=True, text=True,
                      env={k: v for k, v in os.environ.items() if k not in ("SHELLFRAME_CONFIG_DIR", "SHELLFRAME_TESTS")})
check("(the app itself, not a test, still uses ~/.config/shellframe)", Path(lone.stdout.strip()) == REAL, lone.stdout)
import main  # noqa: E402
check("main's config file is in it", main.CONFIG_FILE == here / "config.json" and main.CONFIG_DIR == here, main.CONFIG_FILE)
main.load_config()
main.save_config({"settings": {"ui_style": "chat"}})
check("…so a save lands there", (here / "config.json").exists())
import bridge_telegram  # noqa: E402
check("Telegram's state files are in it", bridge_telegram.TelegramBridge._OFFSET_FILE.parent == here
      and bridge_telegram.TelegramBridge._PENDING_FILE.parent == here)
check("…and its config reader reads it", bridge_telegram._read_config().get("settings", {}).get("ui_style") == "chat")
import bridge_line  # noqa: E402
check("the LINE bridge reads it", bridge_line._read_settings().get("ui_style") == "chat")
# Nobody builds the real config.json path by hand any more (sf_config is the one place).
offenders = []
for p in sorted(HERE.glob("*.py")):
    if p.name.startswith(("tests_", "test_")) or p.name == "sf_config.py":
        continue
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(r'"\.config"\s*/\s*"shellframe"\s*/\s*"(config\.json|tg_offset\.json|tg_pending\.json)"', code):
            offenders.append(f"{p.name}:{n}")
check("no module spells out config.json's path itself", not offenders, offenders)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
