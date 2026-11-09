#!/usr/bin/env python3
"""The phone's bot view: GET /link/bots and POST /link/bots/wake (docs/mobile-link.md).

Asked for by the iOS app (its M3 waits on it): every bot with its face, where it is and
what it is doing, every tab's face, state, last answer and latest line, by the desktop's
own rules, and the question a tab waits on with its options.

  - a face nobody picked is the page's defaultAvatar to the bit (the real web/bots.js
    runs in node beside the Python one)
  - bots: awake / asleep / external, the tab, its place, what it does, the model; an
    external bot's webhook never leaves the computer
  - tabs: face, binding, state (decision and stuck too), last answer ("now" while it
    runs), the latest line as the page's previewOf picks it, redacted
  - a menu on screen and a question a turn ended on with [[SF:RED]] both come with
    their options, with no Telegram bridge at all; a later prompt answers the RED one
  - rev: the same while nothing changes (a running tab's clock does not count)
  - bots off: nothing; wake opens a sleeping bot, refuses an external one
  - /link/signals: a RED event says which bot asks and lists the options

Run: .venv/bin/python tests_link_bots.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-link-bots-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")  # fake tabs stay out of the real log
import bridge_telegram as bt  # noqa: E402
bt._LOG_FILE = str(TMP / "shellframe_bridge.log")     # …and out of the real bridge log

import api_bots  # noqa: E402
import api_server  # noqa: E402
import bots  # noqa: E402
import bridge_home  # noqa: E402
import main  # noqa: E402
import tab_delivery  # noqa: E402
from main import Api  # noqa: E402

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


print("a face nobody picked")
SEEDS = ["主控", "Coding", "s259", "s1", "", "peer/行政", "😀x", "研究-CLD", "a", "ShellBot", "知庫", "s1000",
         "Mac mini/主控", "x" * 40, "tab 2", "🤖🤖", "規格站", "z", "s99", "Grok Bot"]
node = shutil.which("node")
if node:
    js = ("const fs = require('fs'), vm = require('vm');"
          "const ctx = {window: {}, document: {readyState: 'loading', addEventListener() {}, documentElement: {lang: 'en'}},"
          " localStorage: {getItem() { return null; }, setItem() {}}, console, navigator: {platform: 'MacIntel'}};"
          "ctx.window.document = ctx.document; vm.createContext(ctx);"
          "vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);"
          "const seeds = JSON.parse(fs.readFileSync(0, 'utf8'));"
          "console.log(JSON.stringify(seeds.map(s => ctx.window.SFBots.defaultAvatar(s))));")
    r = subprocess.run([node, "-e", js, str(HERE / "web" / "bots.js")], input=json.dumps(SEEDS),
                       capture_output=True, text=True, timeout=30)
    page = json.loads(r.stdout or "[]")
    mine = [bots.default_avatar(s) for s in SEEDS]
    check(f"the same face as the page's defaultAvatar for {len(SEEDS)} seeds (CJK, emoji, empty)",
          page == mine, [(s, p, m) for s, p, m in zip(SEEDS, page, mine) if p != m][:3] or r.stderr[-300:])
else:
    print("  SKIP the page comparison (no node)")
check("a picked face is kept; what is not valid falls back to the seed's default",
      bots.resolved_avatar({"shape": "cloud", "color": "sky"}, "x") == {"shape": "cloud", "color": "sky"}
      and bots.resolved_avatar({"shape": "nope", "color": "#12AB9f"}, "x")
      == {"shape": bots.default_avatar("x")["shape"], "color": "#12AB9f"}
      and bots.resolved_avatar(None, "Coding") == bots.default_avatar("Coding"))

# ── a fake app ─────────────────────────────────────────────────────────────
HOOK_URL = "https://hooks.example/routines/secret-path-123/trigger"
HOOK_AUTH = "Bearer sk-test-very-secret"


def fresh_config(on=True):
    cfg = {"settings": {"experimental_bots": on},
           "agent_roster": {
               "Coding": {"label": "Coding-CLD", "cmd": "claude", "responsibility": "程式與測試",
                          "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True}},
               "研究": {"label": "研究-CLD", "cmd": "codex", "responsibility": "資料調研"},
               "寫手": {"label": "寫手", "cmd": "claude --permission-mode x", "model": "sonnet",
                        "responsibility": "寫文件", "bot": {"memory": True}},
               "總管": {"label": "總管-外部", "cmd": "zsh", "responsibility": "只派工與驗收",
                        "bot": {"avatar": {"shape": "star", "color": "purple"}, "primary": True,
                                "external": {"kind": "webhook", "url": HOOK_URL, "auth": HOOK_AUTH}}}},
           "bots_sessions": {"s1": {"bot": "Coding"}, "s2": {"avatar": {"shape": "egg", "color": "teal"}},
                             "s4": {"plain": True}}}
    main.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


class Tab:
    def __init__(self, sid, label):
        self.sid, self._custom_label, self.cmd, self.alive = sid, label, "claude", True
        self._tmux_name, self._recent, self.lock = None, bytearray(), threading.Lock()
        self._last_output_activity_time = 0.0


STATUS = {
    "s1": {"state": "working", "action": "Running curl https://bob:hunter2@example.com/x", "narration": "先試一次",
           "model": {"name": "Opus 5.5", "effort": "high"}},
    "s2": {"state": "done", "narration": "**週報**整理好了，放在 `reports/weekly.md`", "task": "整理週報"},
    "s3": {"state": "decision", "summary": "等待權限決策"},
    "s4": {"state": "idle", "summary": "idle"},
}
LAST = {"s2": 1_700_000_000.0, "s3": 1_700_000_100.0, "s4": 0.0}
# Measured on Claude Code 2.1.319 (a throwaway session asked to run a command): no side
# borders, a rule on top, the command between dashed rules, then the question and options.
SCREENS = {"s3": "\n".join(["⏺ Bash(touch notes.txt)", "  ⎿ \xa0Waiting…", "", "─" * 60, " Bash command",
                            " Create empty file notes.txt", "╌" * 60, " touch notes.txt", "╌" * 60,
                            " Do you want to proceed?", " ❯ 1. Yes",
                            "   2. Yes, and always allow access to /repo from this project", "   3. No", "",
                            " Esc to cancel · Tab to amend"])}
PROMPT_AT = {}


def make_api():
    api = Api.__new__(Api)
    api.sessions = {sid: Tab(sid, label) for sid, label in
                    (("s1", "Coding-CLD"), ("s2", "週報整理"), ("s3", "部署檢查"), ("s4", "Coding-CLD"))}
    api.bridge = api.line_bridge = None
    api._window = None
    api._agent_status_snapshot = lambda sid: (dict(STATUS.get(sid, {}), prompt_at=PROMPT_AT.get(sid, 0.0)), 0)
    api._agent_state_for_list = lambda sid: STATUS.get(sid, {}).get("state", "")
    api._agent_blocked_for_list = lambda sid: "trust dialog" if sid == "s4" else ""
    api._last_activity_at = lambda sid, s, out_ts=0.0: LAST.get(sid, 0.0)
    api._ordered_sids = lambda cfg=None, preferred_order=None: ["s2", "s1", "s3", "s4"]
    return api


tab_delivery.screen_text = lambda api, sid, lines=60: SCREENS.get(sid, "")
fresh_config()
api = make_api()


def link(**args):
    return api._execute_sfctl("bots_link", args)


print("bots")
t0 = time.time()
res = link()
by = {b["role"]: b for b in res.get("bots") or []}
check("the answer: version, rev, the clock, bots on, the primary",
      res.get("success") and res.get("v") == 1 and isinstance(res.get("rev"), int) and res.get("enabled") is True
      and res.get("primary") == "總管" and abs(res.get("now", 0) - t0) < 5, {k: res.get(k) for k in ("v", "rev", "primary")})
check("only bots are listed (a plain roster role is not)", sorted(by) == sorted(["Coding", "寫手", "總管"]), sorted(by))
c = by.get("Coding") or {}
check("an awake bot: its tab, its place in the tab bar, working, the model it runs",
      c.get("state") == "awake" and c.get("sid") == "s1" and c.get("position") == 2 and c.get("activity") == "working"
      and c.get("runs_on") == "Opus 5.5 high" and c.get("avatar") == {"shape": "pill", "color": "red"}, c)
check("…what it is doing, with the credential in it masked",
      c.get("doing", "").startswith("Running curl https://") and "hunter2" not in json.dumps(res, ensure_ascii=False), c)
check("…and while it runs, it last answered now", abs(c.get("last_at", 0) - res["now"]) < 1, c.get("last_at"))
w = by.get("寫手") or {}
check("a sleeping bot: no tab, no state, the page's default face, the model its roster command starts",
      w.get("state") == "asleep" and w.get("sid") == "" and w.get("activity") == "" and w.get("position") == 0
      and w.get("avatar") == bots.default_avatar("寫手") and w.get("runs_on") == "claude／sonnet", w)
x = by.get("總管") or {}
check("an external bot: marked so, no tab, no model", x.get("state") == "external" and x.get("external") is True
      and x.get("sid") == "" and x.get("runs_on") == "" and x.get("primary") is True, x)
blob = json.dumps(res, ensure_ascii=False)
check("its webhook never leaves the computer", "secret-path-123" not in blob and "sk-test" not in blob)

print("tabs")
tabs = res.get("tabs") or {}
check("every tab is there, bot or not", sorted(tabs) == ["s1", "s2", "s3", "s4"], sorted(tabs))
check("a bot's tab wears the bot's face", tabs["s1"]["bot"] == "Coding" and tabs["s1"]["avatar"] == {"shape": "pill", "color": "red"})
check("a plain tab with a face of its own keeps it", tabs["s2"]["bot"] == "" and tabs["s2"]["avatar"] == {"shape": "egg", "color": "teal"})
check("a plain tab without one: the page's default for its id", tabs["s3"]["avatar"] == bots.default_avatar("s3"))
check("a tab marked plain is not the bot it is named after", tabs["s4"]["plain"] is True and tabs["s4"]["bot"] == "")
check("state, decision and stuck included (the desktop's dots)", [tabs[s]["activity"] for s in ("s1", "s2", "s3", "s4")]
      == ["working", "done", "decision", "idle"])
check("last answer: the transcript's time when it is not running, 0 when unknown",
      tabs["s2"]["last_at"] == LAST["s2"] and tabs["s4"]["last_at"] == 0)
check("latest, as the page's previewOf picks it: what it said, markdown gone",
      tabs["s2"]["latest"] == "週報整理好了，放在 reports/weekly.md", tabs["s2"]["latest"])
check("…what it runs while it runs", tabs["s1"]["latest"].startswith("Running curl"), tabs["s1"]["latest"])
check("…an idle summary is no line", tabs["s4"]["latest"] == "", tabs["s4"]["latest"])
STATUS["s1"] = {"state": "done", "task": "改好了"}
check("…a bot's tab with nothing to say: its job", link()["tabs"]["s1"]["latest"] == "改好了"
      and (STATUS.__setitem__("s1", {"state": "idle"}) or link()["tabs"]["s1"]["latest"] == "程式與測試"))
STATUS["s1"] = {"state": "working", "action": "x" * 400}
check("…at most 140 characters", len(link()["tabs"]["s1"]["latest"]) <= 140)
check("blocked on a dialog says so", tabs["s4"]["blocked"] == "trust dialog")
api_bots._CLOSING["s1"] = {"role": "Coding", "phase": "writing", "since": time.time()}
r2 = link()
check("closing: writing its memory, on the tab and on the bot",
      r2["tabs"]["s1"]["closing"] == "writing" and [b for b in r2["bots"] if b["role"] == "Coding"][0]["closing"] == "writing")
api_bots._CLOSING.pop("s1", None)

print("waiting on you")
d = tabs["s3"].get("decision") or {}
check("a menu on screen: the question and its options, read off the screen",
      d.get("reason") == "Do you want to proceed?"
      and d.get("options") == [{"n": 1, "text": "Yes"}, {"n": 2, "text": "Yes, and always allow access to /repo from this project"},
                               {"n": 3, "text": "No"}], d)
check("nothing waiting: no decision", tabs["s2"].get("decision") is None and tabs["s1"].get("decision") is None)
RED = [{"kind": "user_msg", "text": "[[A2A：來自「研究」的訊息]] 做完了"},
       {"kind": "assistant_text", "text": "兩個方案都可以。\n要先修哪個？\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知"}]
bridge_home.on_turn_end(api, "s2", read_events=lambda sid: RED)          # no Telegram bridge on this computer
d2 = link()["tabs"]["s2"].get("decision") or {}
check("a question a turn ended on with [[SF:RED]] comes with its options, with no Telegram bridge",
      d2.get("reason") == "要先修哪個？" and d2.get("options") == [{"n": 1, "text": "先修按鈕"}, {"n": 2, "text": "先修通知"}], d2)
TG_RED = [{"kind": "user_msg", "text": "請放在 [[TG_REPLY_ab12cd34]] 之間"}, RED[1]]
bridge_home.on_turn_end(api, "s2", read_events=lambda sid: TG_RED)
check("…also when the turn started from Telegram", (link()["tabs"]["s2"].get("decision") or {}).get("options"))
PROMPT_AT["s2"] = time.time() + 1
check("…and an answer sent to the tab after it retires it", link()["tabs"]["s2"].get("decision") is None)
PROMPT_AT.pop("s2", None)
bridge_home.on_turn_end(api, "s2", read_events=lambda sid: [RED[0], {"kind": "assistant_text", "text": "好了"}])
check("…as does a later turn that asks nothing", link()["tabs"]["s2"].get("decision") is None)
bridge_home.on_turn_end(api, "s2", read_events=lambda sid: RED)
api.close_session = Api.close_session.__get__(api)
api._status_tracker = None
api._persist_session_manifest = lambda *a, **k: None
api._plugin_dispatch_session_close = lambda sid: None
api._bots_divert_close = lambda sid, reason: False
try:
    api.close_session("s2", reason="test")
except Exception:
    pass
check("closing the tab forgets its question (tab ids are reused)", bridge_home.red_pending("s2") is None)
api.sessions["s2"] = Tab("s2", "週報整理")

print("rev")
STATUS["s1"] = {"state": "working", "action": "Running npm test"}
r1 = link()
time.sleep(1.1)
r2 = link(rev=str(r1["rev"]))
check("nothing changed, the clock moved: unchanged, in a few bytes", r2 == {"success": True, "unchanged": True, "rev": r1["rev"]}, r2)
STATUS["s2"] = dict(STATUS["s2"], narration="新的一句")
r3 = link(rev=str(r1["rev"]))
check("a new line on a tab: a new rev and the whole answer", not r3.get("unchanged") and r3["rev"] != r1["rev"]
      and r3["tabs"]["s2"]["latest"] == "新的一句")

print("bots off, wake")
fresh_config(on=False)
off = link()
check("bots off: no bots, no tabs", off.get("enabled") is False and off.get("bots") == [] and off.get("tabs") == {}, off)
check("…and no waking", not api._execute_sfctl("bots_wake", {"role": "寫手"}).get("success"))
fresh_config()
opened = []
api.bots_open = lambda role: (opened.append(role) or json.dumps({"success": True, "details": {"sid": "s9", "existed": False}}))
r = api._execute_sfctl("bots_wake", {"role": "寫手"})
check("wake opens the bot through the page's own bots_open", r.get("success") and r["details"]["sid"] == "s9"
      and opened == ["寫手"], r)
del api.bots_open
r = api._execute_sfctl("bots_wake", {"role": "總管"})
check("an external bot is not woken here, and says why", r.get("success") is False and r.get("message"), r)

print("/link/signals: who asks, and the options")
bt.tg_api = lambda *a, **k: {"ok": True, "result": {"message_id": 1}}
b = bt.TelegramBridge("tg", bt.TelegramBridgeConfig(bot_token="x", allowed_users=[]))
b._prune_stale_slots = lambda force=False: None
b._signal_desktop_notify = lambda *a, **k: None
b.register_session("s1", "Coding-CLD", lambda text: None, cmd="claude")
b._slot_display = lambda slot: ["要先排哪一張？", "1. 先排 10/15 的卡片", "2. 先處理合約盤點", ""]
b.home_api = api
cursor = api_server.EVENT_BUS.since(0)[1]
b._detect_and_fire_signal(b.slots["s1"], ["[[SF:RED: 要先排哪一張]]"])
evs = api_server.EVENT_BUS.since(cursor)[0]
ev = evs[-1] if evs else {}
check("a RED event names the bot and lists the options; old fields as they were",
      ev.get("state") == "RED" and ev.get("role") == "Coding" and ev.get("sid") == "s1" and ev.get("reason")
      and ev.get("options") == [{"n": 1, "text": "先排 10/15 的卡片"}, {"n": 2, "text": "先處理合約盤點"}], ev)
b._detect_and_fire_signal(b.slots["s1"], ["[[SF:GREEN]]"])
ev = api_server.EVENT_BUS.since(cursor)[0][-1]
check("other states are unchanged (no new fields)", ev.get("state") == "GREEN" and "options" not in ev and "role" not in ev, ev)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
