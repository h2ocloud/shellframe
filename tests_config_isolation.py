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

# Push and the debug log: measured before, every run of the suite sent a dozen notices to
# Apple with this computer's key and phone (sf_push read the real push.json), and wrote the
# tests' lines (an "unreadable config.json", the paths on the real drag pasteboard) into the
# log of the ShellBot running on it.
import sf_log  # noqa: E402
import sf_push  # noqa: E402
os.environ.pop(sf_push.PATH_ENV, None)
check("push.json is the test's own, not this computer's", Path(sf_push.config_path()).parent == here
      and not Path(sf_push.config_path()).is_relative_to(REAL), sf_push.config_path())
check("…so nothing is configured and nothing is pushed", sf_push.load().get("key_path") in (None, ""))
check("the debug log is the test's own, not the running app's",
      Path(sf_log.DEBUG_LOG) != sf_log.TMP_DIR / "shellframe_debug.log", sf_log.DEBUG_LOG)
import sfctl  # noqa: E402
os.environ["SHELLFRAME_TESTS"] = "1"
before = set(os.listdir(sfctl.CMD_DIR)) if os.path.isdir(sfctl.CMD_DIR) else set()
sfctl._rpc_nowait("push_notify", {"message": "from a test"})
after = set(os.listdir(sfctl.CMD_DIR)) if os.path.isdir(sfctl.CMD_DIR) else set()
check("a test's sfctl leaves no command for the running app", after - before == set(), after - before)
check("…and asks it nothing", sfctl._rpc("version", timeout=1).get("success") is False)
drop = (HERE / "tests_drop_paths.py").read_text(encoding="utf-8")
check("tests_drop_paths.py reads a stand-in drag pasteboard, not this computer's",
      drop.count('_patch.dict(sys.modules, {"AppKit": FAKE_APPKIT})') == 2)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
