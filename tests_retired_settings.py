#!/usr/bin/env python3
"""Settings that were retired keep the behaviour their "off" had, and an old config.json that
still has them changes nothing.

Retired in the settings clean-up (the keys stay in a person's config.json, unread; nothing
rewrites the file):
  - the master turn reminder, its custom text, auto-delegate, and the note to the main tab
    when a tab closes (master_turn_preamble_enabled, master_turn_preamble,
    auto_delegate_enabled, idle_reaper.handoff_to_main / handoff_on_start):
      * Telegram's per-message prompt carries the "work in this tab, another one only when
        asked" part, whatever auto_delegate_enabled says
      * a tab named like a master tab gets what was typed, nothing in front of it
      * closing a tab (a scheduled one, or with --handoff) writes nothing into another tab
        and sends nothing to Telegram
  - the first-message init prompt and its text (inject_init_prompt, ui_prompt): a new AI
    tab's first message, typed or from Telegram, goes in as it is

Run: .venv/bin/python tests_retired_settings.py
"""
import importlib.util
import json
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-retired-"))
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")  # fake tabs stay out of the real log

import main  # noqa: E402
from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# What an install that had every retired switch on still carries in its config.json.
OLD_SETTINGS = {
    "master_turn_preamble_enabled": True,
    "master_turn_preamble": "MY DELEGATION PROTOCOL",
    "auto_delegate_enabled": True,
    "inject_init_prompt": True,
    "ui_prompt": "MY INIT PROMPT",
}
OLD_IDLE = {"enabled": False, "idle_sec": 1800, "summary_grace_sec": 120,
            "handoff_to_main": True, "handoff_on_start": True}
OLD_CFG = {"settings": dict(OLD_SETTINGS), "idle_reaper": dict(OLD_IDLE), "presets": []}

# The real bridge_telegram, beside the stand-in main.py was given above.
_spec = importlib.util.spec_from_file_location("bt_real", HERE / "bridge_telegram.py")
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


def bt_settings(d):
    """Put settings into bridge_telegram's one-second cache, so _read_settings returns them."""
    bt._SETTINGS_CACHE["ts"] = time.monotonic()
    bt._SETTINGS_CACHE["val"] = d


print("DEFAULT_CONFIG no longer has the retired keys")
check("settings: no master_turn_preamble_enabled",
      "master_turn_preamble_enabled" not in main.DEFAULT_CONFIG["settings"], sorted(main.DEFAULT_CONFIG["settings"]))
check("idle_reaper: no handoff_to_main, no handoff_on_start",
      not {"handoff_to_main", "handoff_on_start"} & set(main.DEFAULT_CONFIG["idle_reaper"]),
      sorted(main.DEFAULT_CONFIG["idle_reaper"]))

print("Telegram's per-message prompt: work stays in this tab")
for name, settings in (("no settings", {}), ("an old config with auto-delegate on", dict(OLD_SETTINGS))):
    bt_settings(settings)
    p = bt.get_tg_prompt()
    check(f"{name}: the base part is there", p.startswith("[TG] Replying to Telegram mobile"), p[:60])
    check(f"{name}: another tab only when the person asks",
          "only when the user explicitly asks" in p and "prefer `sfctl delegate" not in p, p[-300:])
    check(f"{name}: no word about a switch that no longer exists", "auto-delegation" not in p.lower(), p[-300:])
bt_settings(dict(OLD_SETTINGS, tg_prompt="MY TG PROMPT"))
check("a custom tg_prompt is used word for word", bt.get_tg_prompt() == "MY TG PROMPT", bt.get_tg_prompt())
check("the per-turn master reminder is gone from the bridge",
      not any(hasattr(bt, n) for n in ("get_master_turn_preamble", "master_turn_preamble_enabled",
                                       "auto_delegate_enabled", "wrap_master_turn_input", "is_master_label")))


print("a tab named like a master tab gets what was typed")


def api_with_tab(label, cmd="claude"):
    api = Api()
    written = []
    s = types.SimpleNamespace(
        sid="s1", cmd=cmd, cwd="~", lock=MagicMock(), write=lambda d: written.append(d),
        _recent=bytearray(b"\xe2\x9d\xaf "), _startup_trust_pending=False,
        _last_activity_time=0.0, _last_user_activity_time=0.0, _idle_reap_state="",
        _slug_pending=False, _tmux_name=None, _custom_label=label, _init_pending=False,
    )
    api.sessions = {"s1": s}
    api.bridge = None
    api._arm_awaiting_response = lambda *a, **k: None
    return api, written


with mock.patch.object(main, "load_config", return_value=json.loads(json.dumps(OLD_CFG))):
    for label in ("總控-助理", "master", "user-facing desk"):
        api, written = api_with_tab(label)
        api.write_input("s1", "看一下今天的信\r")
        check(f"「{label}」: exactly the typed text reaches the CLI", written == ["看一下今天的信\r"], written)


print("a new AI tab's first message goes in as it is")
with mock.patch.object(main, "load_config", return_value=json.loads(json.dumps(OLD_CFG))):
    api, written = api_with_tab("研究")
    api.sessions["s1"]._init_pending = True          # what the old new_session armed, on a tab already ready
    api.write_input("s1", "先")
    api.write_input("s1", "整理一下\r")
    check("typed: exactly the keys reach the CLI, no init prompt in front", written == ["先", "整理一下\r"], written)
check("the init prompt's helpers are gone from Api",
      not any(hasattr(Api, n) for n in ("consume_init_prompt_if_ready", "_get_init_prompt", "_inject_init_prompt_enabled",
                                        "_init_inject_decision", "_is_user_content")))
check("…and from the bridge (no INIT_PROMPT.md, no ui_prompt)",
      not any(hasattr(bt, n) for n in ("get_ui_prompt", "load_init_prompt", "DEFAULT_UI_PROMPT", "_INIT_PROMPT_FILE"))
      and not (HERE / "INIT_PROMPT.md").exists())
bt_settings(dict(OLD_SETTINGS))
tgb = bt.TelegramBridge("tg", bt.TelegramBridgeConfig(bot_token="x", allowed_users=[]), on_consume_init=lambda sid: "INIT")
check("Telegram: a running app's on_consume_init is still accepted (sfctl reload before a restart) and kept nowhere",
      not hasattr(tgb, "_on_consume_init"))

print("closing a tab writes no note to the main tab")


class Tab:
    def __init__(self, cmd="claude"):
        self.alive, self.lock, self._recent, self.cmd = True, threading.Lock(), bytearray(), cmd
        self._tmux_name = None
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def kill(self):
        self.alive = False


main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps(OLD_CFG), encoding="utf-8")
api = Api.__new__(Api)
api.line_bridge = None
api._window = None
api._status_tracker = None
api._hook_events, api._status_cache = {}, {}
api._persist_session_manifest = lambda *a, **k: None
api._plugin_dispatch_session_close = lambda sid: None
api._bots_divert_close = lambda sid, reason: False
sent = []
api._send_text_to_session = lambda *a, **k: sent.append(a)
bridge = MagicMock()
bridge.active = True
bridge.config.bot_token = "000:demo"
bridge._user_chat = {1: 1}
api.bridge = bridge
main_tab, scheduled = Tab(), Tab()
scheduled._lifecycle_source = "scheduler"
api.sessions = {"s1": main_tab, "s2": scheduled, "s3": Tab()}
tg = sys.modules["bridge_telegram"]
tg.tg_api.reset_mock()
api.close_session("s2", reason="done", handoff=True, summary_path=str(TMP / "summary.md"), idle_seconds=1900)
api.close_session("s3", reason="idle", handoff=True)
check("both tabs are closed", "s2" not in api.sessions and "s3" not in api.sessions and not scheduled.alive)
check("the main tab got nothing typed into it", main_tab.writes == [] and sent == [], (main_tab.writes, sent))
check("nothing went to Telegram", not tg.tg_api.called, tg.tg_api.call_args_list)
check("no lifecycle note helpers remain on Api",
      not any(hasattr(Api, n) for n in ("_write_lifecycle_handoff", "_handoff_target_sid", "_bridge_send_handoff")))

print("Save in idle tab cleanup leaves an old handoff key alone (no rewrite of the person's file)")
api.save_idle_reaper(json.dumps({"enabled": True, "idle_sec": 3600, "summary_grace_sec": 300, "handoff_to_main": False}))
idle = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))["idle_reaper"]
check("the three cleanup values are saved", (idle["enabled"], idle["idle_sec"], idle["summary_grace_sec"]) == (True, 3600, 300), idle)
check("handoff_to_main is neither written from the page nor removed from the file", idle.get("handoff_to_main") is True, idle)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
