#!/usr/bin/env python3
"""A read of the config that migrates it never writes back a stale copy (main.load_config).

Found by a test that failed about one run in eight (tests_bot_model.py): a bot saved through
update_config was gone a moment later. Traced: load_config saves when a migration changes the file
(a preset to offer, a default to add), and it saved the copy it had read without the lock. A bot's
wake thread read the file, the main thread saved a new bot (update_config, under the lock), then
the wake thread's migration saved its older copy over it, and the bot was gone.
  - a reader that read before a write and migrates after it: the write survives, and so does the
    migration (it is saved from a fresh read, under the lock)
  - an ordinary read, with nothing to migrate, writes nothing
Run: .venv/bin/python tests_config_migration_race.py
"""
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")

import main  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="sf-cfg-race-"))
main.CONFIG_FILE = TMP / "config.json"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def on_disk():
    return json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))


print("1. a reader that migrates, around a write")
main.CONFIG_FILE.write_text(json.dumps({"settings": {}, "agent_roster": {}}), encoding="utf-8")  # presets not offered yet
real_read = main._read_config_json
go, first = threading.Event(), {}


def paused_read():
    cfg = real_read()
    me = threading.current_thread().name
    if me == "reader" and not first.get(me):
        first[me] = True
        go.wait(5)                  # read, then held: the write below happens in between
    return cfg


main._read_config_json = paused_read
reader = threading.Thread(target=main.load_config, name="reader")
reader.start()
while not first.get("reader"):
    pass
main.update_config(lambda c: c.setdefault("agent_roster", {}).update({"示範": {"label": "示範"}}))
go.set()
reader.join(5)
main._read_config_json = real_read
cfg = on_disk()
check("the bot saved in between is still there", "示範" in (cfg.get("agent_roster") or {}), cfg.get("agent_roster"))
check("…and the migration was saved too", bool(cfg.get("_default_ai_presets_offered")),
      cfg.get("_default_ai_presets_offered"))

print("2. an ordinary read")
saves = []
real_save = main.save_config
main.save_config = lambda c: saves.append(1) or real_save(c)
before = main.CONFIG_FILE.read_text(encoding="utf-8")
main.load_config()
main.save_config = real_save
check("nothing to migrate: nothing is written",
      not saves and main.CONFIG_FILE.read_text(encoding="utf-8") == before, len(saves))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
