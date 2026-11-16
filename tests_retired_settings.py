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
  - Settings → Telegram's System Prompt (bridge.initial_prompt): Connect types nothing into a
    tab, whatever the page sends in that argument's place; a saved one stays in the file, unread
  - the glasses (glasses_enabled, the per-tab grant, `sfctl glasses`, the HTTP routes): no tab
    is written down as open to them; an old allow list and audit stay in the file, unread
  - the Plugins tab (the marketplace): gone, while a plugin enabled in config.plugins still
    loads and gives its settings panel and sidebar badge

Run: .venv/bin/python tests_retired_settings.py
"""
import importlib.util
import json
import subprocess
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

print("Connect types nothing into a tab (the retired System Prompt)")


class FakeBridge:
    def __init__(self, bridge_id, config, **callbacks):
        self.bridge_id, self.config, self.slots = bridge_id, config, {}
        self.active = self.connected = False

    def register_session(self, sid, label, write_fn, **kw):
        self.slots[sid] = types.SimpleNamespace(sent_texts=[])

    def start(self):
        self.active = self.connected = True

    def stop(self):
        self.active = self.connected = False

    def get_status(self):
        return {"bridge_id": self.bridge_id, "active": self.active}


built = []
main.TelegramBridge = FakeBridge
main.TelegramBridgeConfig = lambda **kw: built.append(kw) or types.SimpleNamespace(**kw)
for name, prev in (("a saved System Prompt from before", {"initial_prompt": "OLD SAVED PROMPT"}), ("none saved", {})):
    main.CONFIG_FILE.write_text(json.dumps({"settings": {}, "bridge": dict({"bot_token": "000:demo"}, **prev)}), encoding="utf-8")
    api = Api.__new__(Api)
    api.bridge = api.line_bridge = None
    api._persist_session_manifest = lambda *a, **k: None
    api._prepare_pane_for_input = lambda s: None
    first = Tab()
    first._custom_label = "第一個分頁"
    api.sessions = {"s1": first}
    r = json.loads(api.start_bridge("000:demo", "[]", True, "You are running inside ShellBot with a Telegram bridge", True, False))
    time.sleep(3.6)                                   # the old one typed it 3 s after Connect
    saved = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))["bridge"]
    check(f"{name}: connected, and the first tab got nothing typed", r.get("success") and first.writes == [], first.writes)
    check(f"{name}: the bridge's config carries no prompt", "initial_prompt" not in built[-1], built[-1])
    check(f"{name}: config.json keeps what was there and adds nothing",
          saved.get("initial_prompt") == prev.get("initial_prompt") and ("initial_prompt" in saved) == bool(prev), saved)

print("the glasses: nothing grants them a tab or writes them down")
check("DEFAULT_CONFIG: no glasses_enabled", "glasses_enabled" not in main.DEFAULT_CONFIG["settings"])
check("Api: no glasses calls", not any(hasattr(Api, n) for n in (
    "set_session_glasses", "get_glasses_status", "_glasses_report", "_glasses_transcript")))
OLD_GLASSES = {"settings": {"glasses_enabled": True}, "glasses_allowed_sessions": ["s1"],
               "glasses_audit": [{"ts": 1, "sid": "s1", "enabled": True, "source": "ui"}]}
main.CONFIG_FILE.write_text(json.dumps(OLD_GLASSES), encoding="utf-8")
api = Api.__new__(Api)
api.bridge = api.line_bridge = None
api.sessions = {"s1": Tab()}
api.sessions["s1"]._glasses_enabled = True              # what an old build left on a Session
api._persist_session_manifest()
cfg = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))
check("a tab's manifest entry has no glasses field",
      cfg.get("session_manifest") and all("glasses_enabled" not in e for e in cfg["session_manifest"]), cfg.get("session_manifest"))
check("the old allow list and audit stay in the file as they were",
      cfg.get("glasses_allowed_sessions") == ["s1"] and cfg.get("glasses_audit") == OLD_GLASSES["glasses_audit"], cfg)
rows = json.loads(api.list_sessions())
check("the page's tab list has no glasses field", rows and all("glasses_enabled" not in r for r in rows), rows)
r = subprocess.run([sys.executable, str(HERE / "sfctl.py"), "glasses"], capture_output=True, text=True, timeout=30)
check("`sfctl glasses` is not a command any more", r.returncode == 2 and "invalid choice" in r.stderr, r.stderr[-200:])
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import api_server  # noqa: E402
asked = []
httpd, _ = api_server.start(lambda cmd, args: asked.append(cmd) or {"success": True, "details": {"sessions": []}},
                            port=0, token="demo-token", allowed_ips=["127.0.0.1"])


def http(method, path):
    req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}", method=method,
                                 data=b"{}" if method == "POST" else None,
                                 headers={"Authorization": "Bearer demo-token", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


codes = {p: http(m, p) for m, p in (("GET", "/glasses"), ("POST", "/sessions/s1/glasses-allow"),
                                    ("POST", "/sessions/s1/glasses-deny"), ("GET", "/sessions"))}
check("HTTP API: /glasses and the per-tab glasses routes are 404, /sessions still answers",
      codes == {"/glasses": 404, "/sessions/s1/glasses-allow": 404, "/sessions/s1/glasses-deny": 404, "/sessions": 200}
      and asked == ["list"], (codes, asked))
check("…and the API docs (Swagger) list no glasses route",
      not [p for p in api_server._openapi_spec("0")["paths"] if "glasses" in p])
httpd.shutdown()

print("plugins: the marketplace is gone; an enabled plugin still loads")
check("Api: no marketplace calls", not any(hasattr(Api, n) for n in (
    "marketplace_list", "marketplace_install", "marketplace_enable", "marketplace_uninstall")))
check("no bundled plugins or marketplace list in the repo", not (HERE / "shellframe_plugins").exists())
app = TMP / "app"
plug = app / "shellframe_plugins" / "demo-plugin"
plug.mkdir(parents=True)
(plug / "manifest.json").write_text(json.dumps({"name": "demo-plugin", "version": "1.0.0", "description": "a demo"}), encoding="utf-8")
(plug / "settings.html").write_text("<p>demo settings</p>", encoding="utf-8")
(plug / "plugin.py").write_text("from plugin_sdk import SFPlugin\n\n\nclass Plugin(SFPlugin):\n"
                                "    def sidebar_badge(self, sid):\n        return '<span>demo</span>'\n", encoding="utf-8")
main.CONFIG_FILE.write_text(json.dumps({"settings": {}, "plugins": {"installed": ["demo-plugin"], "enabled": ["demo-plugin"]}}),
                            encoding="utf-8")
with mock.patch.object(main, "APP_DIR", app):
    api._plugins_reload()
panels = json.loads(api.list_plugin_panels())
check("an enabled plugin gets its settings panel", [x["name"] for x in panels] == ["demo-plugin"]
      and panels[0]["html"] == "<p>demo settings</p>", panels)
check("…and its sidebar badge", api.plugin_sidebar_badges("s1") == "<span>demo</span>", api.plugin_sidebar_badges("s1"))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
